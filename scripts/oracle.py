#!/usr/bin/env python3
"""Client for `ingrid_core --serve`: ask the real solver whether a grid is still fillable.

The point is that the dictionary is loaded once. A one-shot `ingrid_core` call spends
seconds parsing word lists and microseconds deciding fillability, which makes the perfect
oracle unusable inside a constructor loop; an `Oracle` here pays that cost at construction
and then answers ~10-100 ms per 15x15 probe, depending on how constrained the grid is.

Three verdicts, never a boolean:

* `unfillable` -- a proof. Arc consistency wiped out a domain, or the search refuted the
  whole tree. It accounts for the dupe index and the shared-substring constraint, so it
  sees things a per-slot pattern screen cannot. Prune with confidence.
* `fillable`   -- a fill was found, and `verdict.fill` has it if you asked for it.
* `unknown`    -- the budget ran out. **Not** a rejection. With the default zero budget
  every grid that survives arc consistency is `unknown`, which is the cheapest useful
  setting: it prunes hard and never lies about the rest.

`Verdict.__bool__` raises on purpose. Collapsing `unknown` into `unfillable` is how a
constructor comes to report a perfectly good template as saturated.

Usage:

    from oracle import Oracle

    with Oracle(wordlist="local/cstenten.dict", min_score=33,
                max_shared_substring=5, probe_ms=0) as oracle:
        v = oracle.probe(rows)              # rows: list[str] or "\\n"-joined str
        if v.unfillable:
            ...
        v = oracle.probe(rows, ms=500, want_fill=True)

`OraclePool` runs several oracles in parallel processes, each with its own copy of the
dictionary, and is the way to use more than one core.
"""

from __future__ import annotations

import argparse
import queue
import select
import shlex
import subprocess
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BINARY = "./target/release/ingrid_core"

UNFILLABLE = "unfillable"
FILLABLE = "fillable"
UNKNOWN = "unknown"
STATES = (UNFILLABLE, FILLABLE, UNKNOWN)


class OracleError(RuntimeError):
    """The oracle process failed, or refused a template."""


@dataclass(frozen=True)
class Verdict:
    """One probe's answer. `state` is one of `unfillable`, `fillable`, `unknown`."""

    state: str
    slots: int
    min_domain: int
    setup_us: int
    ac_us: int
    us: int
    fill: tuple[str, ...] | None = None

    @property
    def unfillable(self) -> bool:
        """True only for a proof of unfillability."""
        return self.state == UNFILLABLE

    @property
    def fillable(self) -> bool:
        """True only when a complete fill was actually found."""
        return self.state == FILLABLE

    @property
    def unknown(self) -> bool:
        """True when the budget ran out without a fill or a proof."""
        return self.state == UNKNOWN

    def __bool__(self):
        raise TypeError(
            "a Verdict has three states; test .unfillable / .fillable / .unknown "
            "explicitly rather than collapsing 'unknown' into a rejection"
        )


def _as_rows(grid) -> list[str]:
    """Accept a string, a list of strings, or a list of lists of single characters.

    Surrounding blank rows are cosmetic and dropped, exactly as the Rust parser drops them. An
    *interior* blank row is not: it is a malformed grid, and it is passed through so the engine
    rejects it, rather than silently deleted so the caller gets a confident verdict about a
    different, smaller template.
    """
    if isinstance(grid, str):
        rows = grid.splitlines()
    else:
        rows = [row if isinstance(row, str) else "".join(row) for row in grid]
    rows = [row.strip() for row in rows]
    while rows and not rows[0]:
        rows.pop(0)
    while rows and not rows[-1]:
        rows.pop()
    if not rows:
        raise ValueError("grid has no rows")
    for index, row in enumerate(rows):
        # `/` frames the rows on the wire and whitespace ends the template token, so a row
        # containing either would silently reframe the request.
        if "/" in row or any(ch.isspace() for ch in row):
            raise ValueError(f"row {index} contains a separator character: {row!r}")
    return rows


def _parse_response(line: str) -> Verdict:
    head, *rest = line.split()
    if head == "error":
        raise OracleError(line[len("error ") :])
    if head not in STATES:
        raise OracleError(f"unrecognized oracle response: {line!r}")
    fields = {}
    fill = None
    for token in rest:
        key, _, value = token.partition("=")
        if key == "fill":
            fill = tuple(value.split("/"))
        else:
            fields[key] = int(value)
    return Verdict(
        state=head,
        slots=fields.get("slots", 0),
        min_domain=fields.get("min_domain", 0),
        setup_us=fields.get("setup_us", 0),
        ac_us=fields.get("ac_us", 0),
        us=fields.get("us", 0),
        fill=fill,
    )


class Oracle:
    """A single `ingrid_core --serve` process holding one loaded dictionary.

    Every policy argument is fixed for the process's lifetime, which is the point: word
    lists, `min_score`, `max_shared_substring` and the dupe rules are campaign-wide
    decisions. Run two oracles if you need two policies.

    Not thread-safe -- one probe at a time per process. Use `OraclePool` for parallelism.
    """

    def __init__(
        self,
        wordlist=None,
        preferred=None,
        blocklist=None,
        min_score=50,
        max_shared_substring=None,
        dupe_exempt_preferred=False,
        ignore_diacritics=False,
        max_length=None,
        probe_ms=0,
        seed=0,
        binary=DEFAULT_BINARY,
        startup_timeout=300.0,
    ):
        argv = [str(binary), "--serve", "--min-score", str(min_score),
                "--probe-time", str(probe_ms), "--seed", str(seed)]
        if wordlist:
            argv += ["--wordlist", str(wordlist)]
        if preferred:
            argv += ["--preferred-wordlist", str(preferred)]
        if blocklist:
            argv += ["--blocklist", str(blocklist)]
        if max_shared_substring is not None:
            argv += ["--max-shared-substring", str(max_shared_substring)]
        if dupe_exempt_preferred:
            argv += ["--dupe-exempt-preferred"]
        if ignore_diacritics:
            argv += ["--ignore-diacritics"]
        if max_length is not None:
            argv += ["--max-length", str(max_length)]

        self._stderr: list[str] = []

        try:
            self._proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        except OSError as error:
            raise OracleError(f"could not run {shlex.join(argv)}: {error}") from error

        # Drain stderr continuously: it carries the Rust side's diagnostics, and an unread pipe
        # would eventually block the child.
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

        banner = self._read_line(startup_timeout)
        if banner is None:
            self._terminate()
            raise OracleError(
                f"oracle produced no ready banner within {startup_timeout:g}s "
                f"({shlex.join(argv)}){self._diagnosis()}"
            )
        if not banner.startswith("ready "):
            self._terminate()
            raise OracleError(
                f"oracle failed to start ({shlex.join(argv)}){self._diagnosis(banner)}"
            )
        self.ready = {
            key: int(value)
            for key, _, value in (token.partition("=") for token in banner.split()[1:])
        }

    def _drain_stderr(self):
        """Owns `self._proc.stderr` for its whole life, reading it and then closing it.

        Closing it from another thread instead would block on the io lock for as long as this
        read does, which is until the last holder of the write end goes away -- so tearing down a
        child that has stopped talking would wait for exactly the thing we gave up waiting for.
        """
        try:
            for line in self._proc.stderr:
                line = line.rstrip("\n")
                if line:
                    self._stderr.append(line)
        finally:
            try:
                self._proc.stderr.close()
            except OSError:
                pass

    def _read_line(self, timeout):
        """Read one stdout line, or return None if the child neither answers nor exits in time.

        `select` is enough here because nothing has been read yet, so no partial line is sitting
        in the buffer, and the banner is one short write well under `PIPE_BUF`: readability means
        the whole line is there. POSIX only, which is all this repository targets.
        """
        if not select.select([self._proc.stdout], [], [], timeout)[0]:
            return None
        return self._proc.stdout.readline().strip()

    def _diagnosis(self, stdout_line=""):
        """Everything we know about a failure: the child's stderr, stdout and exit status."""
        parts = []
        self._stderr_reader.join(timeout=1)
        if self._stderr:
            parts.append("stderr: " + "; ".join(self._stderr))
        if stdout_line:
            parts.append(f"stdout: {stdout_line}")
        status = self._proc.poll()
        if status is not None:
            parts.append(f"exit status {status}")
        return ": " + ", ".join(parts) if parts else ": no output"

    def _terminate(self):
        """Kill the child and reap it, so no zombie survives a failed startup."""
        if self._proc.poll() is None:
            self._proc.kill()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self._close_streams()

    def _close_streams(self):
        # Not stderr: `_drain_stderr` owns that one.
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass

    def probe(self, grid, ms=None, want_fill=False) -> Verdict:
        """Ask about one template. `ms` overrides the campaign budget; 0 means AC only."""
        if self._proc.poll() is not None:
            raise OracleError(f"oracle already exited{self._diagnosis()}")
        request = "/".join(_as_rows(grid))
        if ms is not None:
            request += f" ms={int(ms)}"
        if want_fill:
            request += " fill=1"
        try:
            self._proc.stdin.write(request + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as error:
            raise OracleError(f"oracle stdin closed ({error}){self._diagnosis()}") from error
        line = self._proc.stdout.readline()
        if not line:
            raise OracleError(f"oracle died mid-probe{self._diagnosis()}")
        return _parse_response(line.strip())

    def close(self):
        if self._proc.poll() is None:
            try:
                self._proc.stdin.write("quit\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self._proc.kill()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self._stderr_reader.join(timeout=1)
        self._close_streams()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class OraclePool:
    """`jobs` independent oracle processes, each with its own copy of the dictionary.

    Loading is the expensive part, so a pool of N costs N times the memory and N times the
    startup, then answers N probes at once. Every process gets the same policy.
    """

    def __init__(self, jobs=3, **kwargs):
        if jobs < 1:
            raise ValueError("jobs must be at least 1")
        self._free: queue.Queue[Oracle] = queue.Queue()
        self._all: list[Oracle] = []
        errors = []

        def start():
            try:
                oracle = Oracle(**kwargs)
            except OracleError as error:  # keep the first failure, tear the rest down
                errors.append(error)
                return
            self._all.append(oracle)
            self._free.put(oracle)

        threads = [threading.Thread(target=start) for _ in range(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            self.close()
            raise errors[0]
        self.ready = self._all[0].ready

    def probe(self, grid, ms=None, want_fill=False) -> Verdict:
        oracle = self._free.get()
        try:
            return oracle.probe(grid, ms=ms, want_fill=want_fill)
        finally:
            self._free.put(oracle)

    def probe_many(self, items, ms=None, want_fill=False, stop_on=None):
        """Probe `(key, grid)` pairs lazily, yielding `(key, verdict)` as answers arrive.

        A rolling window of at most `jobs` probes: fill the window, wait for one answer, decide,
        then submit exactly one replacement. `items` is pulled one element at a time, so memory is
        `O(jobs)` rather than `O(items)` and an endless generator of candidate placements is a
        perfectly good argument.

        `stop_on` is a predicate on the verdict, and the guarantee is that **no probe is started
        after a matching result has been observed**. Observation is made as complete and as late
        as it can be: each turn takes the *whole* set of probes that have finished rather than one
        member of the batch `wait` reports, and the loop looks again for a landed match
        immediately before each submission. What is left is the instant between that look and a
        probe completing; a match that is merely still running has not been decided on, and
        letting the window refill in that case is the window working as intended, not a leak. The
        cost when it happens is one replacement probe that turns out to have been unnecessary.

        Answers that completed alongside a match are discarded along with the probes still
        running. There is deliberately no claim of cancellation: the protocol cannot abandon a
        probe the child has already begun, so on a match the at most `jobs - 1` probes still
        running are drained and only then does the generator return. Its runtime after a match is
        one probe, not the remaining work. A caller that needs a tighter bound should pass a
        smaller `ms`.

        Uses the whole pool for its duration; a concurrent `probe()` call will wait.
        """
        items = iter(items)

        with ThreadPoolExecutor(max_workers=len(self._all)) as pool:
            in_flight = {}

            def submit_next():
                for key, grid in items:
                    in_flight[pool.submit(self.probe, grid, ms=ms, want_fill=want_fill)] = key
                    return


            def completed():
                """Every finished probe, in submission order.

                `wait` reports the futures that tripped it, which need not be every one that has
                finished by the time it returns, and it reports them as a set whose iteration
                order is the hash of an address. Neither is a basis for deciding anything.
                """
                return [future for future in in_flight if future.done()]

            def match_in(answers):
                if stop_on is None:
                    return None
                return next(((key, verdict) for key, verdict in answers if stop_on(verdict)), None)

            def a_match_has_landed():
                """Has an answer arrived, unread, since we last decided? Peeks; consumes nothing."""
                return stop_on is not None and any(
                    future.done() and future.exception() is None and stop_on(future.result())
                    for future in in_flight
                )

            for _ in self._all:
                submit_next()

            while in_flight:
                wait(in_flight, return_when=FIRST_COMPLETED)
                # Decide on everything that has finished, together. Looking at one arbitrary
                # member would let a non-match start replacement work while the match sat unread
                # beside it.
                batch = [(in_flight.pop(future), future.result()) for future in completed()]
                matched = match_in(batch)
                if matched is not None:
                    # Its batch-mates finished alongside it and are discarded exactly like the
                    # probes still running, which is what a match means here.
                    yield matched
                    return
                for key, verdict in batch:
                    yield key, verdict
                    # Work starts only here, and only if nothing has matched in the meantime --
                    # a probe can finish while the caller holds us between answers, and a match
                    # that has already landed must stop the window even though its turn to be
                    # delivered has not come round yet.
                    if not a_match_has_landed():
                        submit_next()

    def close(self):
        for oracle in self._all:
            oracle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def add_oracle_arguments(parser: argparse.ArgumentParser):
    """Register the campaign-wide oracle policy flags on a script's own parser.

    A calling script may narrow the defaults with `parser.set_defaults(...)`; these are the
    engine's own, not any campaign's.
    """
    group = parser.add_argument_group("oracle")
    group.add_argument("--wordlist", help="standard-tier dict [default: embedded STWL]")
    group.add_argument("--preferred", help="preferred-tier dict")
    group.add_argument("--blocklist", help="words that may never appear in a fill")
    group.add_argument("--min-score", type=int, default=50, help="default: %(default)s")
    group.add_argument("--max-shared-substring", type=int,
                       help="largest overlap two entries may share (default: %(default)s, "
                            "where None means unlimited)")
    group.add_argument("--dupe-exempt-preferred", action="store_true",
                       help="exempt preferred/preferred pairs from the overlap rule "
                            "(default: %(default)s)")
    group.add_argument("--ignore-diacritics", action="store_true",
                       help="fold accents in grids, dicts and blocklist (default: %(default)s)")
    group.add_argument("--max-length", type=int,
                       help="longest slot the oracle will be asked about [default: 21]")
    group.add_argument("--seed", type=int, default=0, help="default: %(default)s")
    group.add_argument("--binary", default=DEFAULT_BINARY, help="default: %(default)s")


def oracle_kwargs(args, probe_ms=0) -> dict:
    """Turn parsed `add_oracle_arguments` values into `Oracle` keyword arguments."""
    return {
        "wordlist": args.wordlist,
        "preferred": args.preferred,
        "blocklist": args.blocklist,
        "min_score": args.min_score,
        "max_shared_substring": args.max_shared_substring,
        "dupe_exempt_preferred": args.dupe_exempt_preferred,
        "ignore_diacritics": args.ignore_diacritics,
        "max_length": args.max_length,
        "probe_ms": probe_ms,
        "seed": args.seed,
        "binary": args.binary,
    }


def main():
    ap = argparse.ArgumentParser(description="Probe grid files through one loaded oracle.")
    ap.add_argument("grids", nargs="+", help="grid files to probe, in order")
    ap.add_argument("--probe-ms", type=int, default=0,
                    help="search budget per probe after arc consistency (0 = AC only)")
    ap.add_argument("--fill", action="store_true", help="print the fill when one is found")
    add_oracle_arguments(ap)
    args = ap.parse_args()

    with Oracle(**oracle_kwargs(args, probe_ms=args.probe_ms)) as oracle:
        print(" ".join(f"{k}={v}" for k, v in oracle.ready.items()), file=sys.stderr)
        worst = 0
        for path in args.grids:
            rows = _as_rows(Path(path).read_text(encoding="utf-8"))
            try:
                verdict = oracle.probe(rows, want_fill=args.fill)
            except OracleError as error:
                print(f"{path}\terror\t{error}")
                worst = 2
                continue
            print(f"{path}\t{verdict.state}\tmin_domain={verdict.min_domain}"
                  f"\tslots={verdict.slots}\tms={verdict.us / 1000:.1f}")
            if verdict.fill:
                print("\n".join(verdict.fill))
            if verdict.unfillable:
                worst = max(worst, 1)
    sys.exit(worst)


if __name__ == "__main__":
    main()
