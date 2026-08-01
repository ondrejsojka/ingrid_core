#!/usr/bin/env python3
"""Pin long theme entries into a dense template, one at a time, solver as the oracle.

`ingrid_core`'s preferred maximisation reliably finds short theme entries (3-5 letters)
and essentially never a long one, because a long slot has few candidates and the odds
that one of them is in a 79-word tier are tiny. Long entries are also the recognisable
ones. So pin them explicitly and let the solver keep the rest.

Two things this gets right that a generic seeder does not:

* the oracle timeout is sized against a measured bare fill of the same template, so a
  rejection means "unfillable", not "slower than 12 seconds" — the failure mode that
  makes a seeder report "nothing placeable" on a grid that fills fine;
* accepted placements are kept as fixed letters and the next round searches on top,
  so the pins compose instead of being scored independently.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import subprocess
import sys
import tempfile
from pathlib import Path


def read_grid(path):
    return [list(l.rstrip("\n")) for l in open(path, encoding="utf-8") if l.strip()]


def dump(grid):
    return "\n".join("".join(r) for r in grid) + "\n"


def slots(grid, min_run=3):
    h, w = len(grid), len(grid[0])
    out = []
    for r in range(h):
        c = 0
        while c < w:
            if grid[r][c] != "#":
                c0 = c
                while c < w and grid[r][c] != "#":
                    c += 1
                if c - c0 >= min_run:
                    out.append(("A", r, c0, c - c0))
            else:
                c += 1
    for c in range(w):
        r = 0
        while r < h:
            if grid[r][c] != "#":
                r0 = r
                while r < h and grid[r][c] != "#":
                    r += 1
                if r - r0 >= min_run:
                    out.append(("D", r0, c, r - r0))
            else:
                r += 1
    return out


def cells_of(slot):
    d, r, c, n = slot
    return [(r, c + i) if d == "A" else (r + i, c) for i in range(n)]


def oracle(grid, args):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(dump(grid))
        path = fh.name
    try:
        proc = subprocess.run(
            [args.binary, "--wordlist", args.wordlist,
             "--preferred-wordlist", args.preferred,
             "--min-score", str(args.min_score),
             "--max-shared-substring", str(args.max_shared_substring),
             "--dupe-exempt-preferred",
             "--timeout", str(args.oracle_timeout),
             "--cores", str(args.oracle_cores), path],
            capture_output=True, text=True,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out or "Error" in out.splitlines()[0]:
        return None
    return out.splitlines()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--theme", required=True)
    ap.add_argument("--wordlist", required=True)
    ap.add_argument("--preferred", required=True)
    ap.add_argument("--binary", default="./target/release/ingrid_core")
    ap.add_argument("--min-score", type=int, default=33)
    ap.add_argument("--max-shared-substring", type=int, default=5)
    ap.add_argument("--oracle-timeout", type=int, default=90)
    ap.add_argument("--oracle-cores", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--min-len", type=int, default=6)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    theme = []
    for ln in open(args.theme, encoding="utf-8"):
        if not ln.strip():
            continue
        wd = (ln.split("\t", 1)[0] if "\t" in ln else ln.split(" ", 1)[0]).strip().lower()
        if wd and wd not in theme:
            theme.append(wd)

    grid = read_grid(args.grid)
    placed, best_fill = [], None

    for rnd in range(args.rounds):
        free = [s for s in slots(grid) if all(grid[r][c] == "." for r, c in cells_of(s))]
        trials = []
        for word in sorted(theme, key=len, reverse=True):
            if word in placed or len(word) < args.min_len:
                continue
            for slot in free:
                if slot[3] != len(word):
                    continue
                work = [row[:] for row in grid]
                for (r, c), ch in zip(cells_of(slot), word):
                    work[r][c] = ch
                trials.append((word, slot, work))
        if not trials:
            print(f"round {rnd}: no candidate placements left")
            break
        print(f"round {rnd}: {len(trials)} placements to test", flush=True)
        chosen = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(oracle, w, args): (word, slot, w)
                       for word, slot, w in trials}
            for fut in concurrent.futures.as_completed(futures):
                word, slot, work = futures[fut]
                try:
                    res = fut.result()
                except concurrent.futures.CancelledError:
                    continue
                print(f"  {word:<13} {slot} {'FILL' if res else 'reject'}", flush=True)
                if res:
                    chosen = (word, slot, work, res)
                    # first acceptance wins: stop the round rather than paying for the
                    # remaining oracle calls, and never read a cancelled future
                    for f in futures:
                        f.cancel()
                    break
        if chosen is None:
            print(f"round {rnd}: nothing placeable, stopping")
            break
        word, slot, work, res = chosen
        grid = work
        placed.append(word)
        best_fill = res
        print(f"round {rnd}: PLACED {word} at {slot}", flush=True)

    Path(args.out).write_text(dump(grid), encoding="utf-8")
    if best_fill:
        Path(args.out + ".filled").write_text("\n".join(best_fill) + "\n", encoding="utf-8")
    print(f"pinned {len(placed)}: {placed}")


if __name__ == "__main__":
    main()
