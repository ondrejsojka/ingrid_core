#!/usr/bin/env python3
"""Contract tests for `scripts/oracle.py`.

The things worth pinning here are the ones green Rust tests cannot see: the row handling that
decides *which* grid the engine is asked about, the pool's concurrency contract, and the startup
teardown. All three were wrong at some point in ways that produced a confident answer to the
wrong question, or a hang.

Everything runs against fakes and needs no `ingrid_core` binary. One test spawns a deliberately
silent child; it is the only one that costs more than milliseconds.

Run with `python3 scripts/test_oracle_client.py` (or under pytest; the assertions are plain).
"""

from __future__ import annotations

import queue
import stat
import sys
import tempfile
import threading
import time
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oracle  # noqa: E402
from oracle import Oracle, OracleError, OraclePool, Verdict, _as_rows  # noqa: E402


def test_a_silent_child_times_out_without_waiting_for_it():
    """Startup teardown must not block on the child it just gave up on.

    Closing the child's stderr from the constructor's thread parks on the io lock until the drain
    thread's read finishes -- which is when the child finally exits. That turned a one-second
    startup timeout into a thirty-second one. The margin below is 20x, so it is a statement about
    who waits for whom rather than about scheduling.
    """
    with tempfile.TemporaryDirectory() as directory:
        silent = Path(directory) / "silent.sh"
        silent.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        silent.chmod(silent.stat().st_mode | stat.S_IEXEC)

        started = time.monotonic()
        try:
            Oracle(binary=str(silent), startup_timeout=0.5)
        except OracleError as error:
            assert "no ready banner" in str(error), error
        else:
            raise AssertionError("a child that never speaks must not be accepted")
        elapsed = time.monotonic() - started
        assert elapsed < 10, f"teardown waited {elapsed:.1f}s for a child it had abandoned"


def test_surrounding_blank_rows_are_cosmetic():
    assert _as_rows("\n\n.....\n.....\n\n") == [".....", "....."]
    assert _as_rows("  ...  \n  .#.  ") == ["...", ".#."]
    assert _as_rows([[".", "#"], [".", "."]]) == [".#", ".."]


def test_interior_blank_rows_are_preserved_for_the_engine_to_reject():
    # Deleting the empty row would frame a 2x5 grid and answer a question nobody asked.
    assert _as_rows(".....\n\n.....") == [".....", "", "....."]
    assert "/".join(_as_rows(".....\n\n.....")) == ".....//....."


def test_rows_that_would_reframe_the_request_are_refused():
    for bad in ["..../..", ".. ..", "..\t.."]:
        try:
            _as_rows(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not be accepted")
    try:
        _as_rows("   \n  ")
    except ValueError:
        pass
    else:
        raise AssertionError("an all-blank grid should not be accepted")


class _Gate:
    """One probe's handshake.

    The test waits for `started`, decides when `release` happens, and can wait for `answered` to
    know the probe has produced its verdict.
    """

    def __init__(self, name, state="unknown"):
        self.name = name
        self.state = state
        self.started = threading.Event()
        self.release = threading.Event()
        self.answered = threading.Event()

    def item(self):
        return (self.name, self)


class _GatedOracle:
    def probe(self, gate, ms=None, want_fill=False):
        gate.started.set()
        assert gate.release.wait(timeout=5), f"{gate.name} was never released"
        gate.answered.set()
        return Verdict(gate.state, 1, 1, 0, 0, 0)

    def close(self):
        pass


class _FakePool(OraclePool):
    def __init__(self, jobs):
        self._free = queue.Queue()
        self._all = [_GatedOracle() for _ in range(jobs)]
        for oracle in self._all:
            self._free.put(oracle)


def _await(event, what):
    assert event.wait(timeout=5), f"timed out waiting for {what}"


def _settle():
    """Give any (incorrectly) eager worker a chance to start before we assert it did not."""
    time.sleep(0.05)


class _Pump:
    """Drives the generator on its own thread.

    `probe_many` is a generator, so nothing at all happens until someone asks for the first
    answer; a test that wants to watch the window fill has to be pulling. `hold`, when given, is
    waited on after each answer is handed over, which parks the generator mid-yield exactly where
    a slow caller would park it.
    """

    def __init__(self, answers, hold=None):
        self.answers: queue.Queue = queue.Queue()
        self.finished = threading.Event()
        self.error: BaseException | None = None
        self._hold = hold
        self._thread = threading.Thread(target=self._run, args=(answers,), daemon=True)
        self._thread.start()

    def _run(self, answers):
        try:
            for answer in answers:
                self.answers.put(answer)
                if self._hold is not None:
                    assert self._hold.wait(timeout=5), "hold was never released"
        except BaseException as error:  # noqa: BLE001 - surfaced by `finish`
            self.error = error
        finally:
            self.finished.set()

    def next(self):
        return self.answers.get(timeout=5)

    def finish(self):
        _await(self.finished, "the generator to finish")
        self._thread.join(timeout=5)
        if self.error is not None:
            raise self.error
        rest = []
        while True:
            try:
                rest.append(self.answers.get_nowait())
            except queue.Empty:
                return rest


_MATCH = lambda verdict: verdict.fillable  # noqa: E731


def test_the_window_holds_exactly_jobs_probes():
    gates = [_Gate(f"g{i}") for i in range(5)]
    pool = _FakePool(2)
    pump = _Pump(pool.probe_many([gate.item() for gate in gates]))
    _await(gates[0].started, "g0")
    _await(gates[1].started, "g1")
    _settle()
    assert not gates[2].started.is_set(), "a third probe started with only two workers"
    for gate in gates:
        gate.release.set()
    seen = [pump.next()[0]] + [key for key, _ in pump.finish()]
    assert sorted(seen) == sorted(gate.name for gate in gates)
    assert all(gate.started.is_set() for gate in gates), "not every item was probed"


def test_no_probe_starts_after_a_match():
    # Two workers: `hit` matches while `busy` is still running, so `never` must not be picked up.
    hit, busy, never = _Gate("hit", "fillable"), _Gate("busy"), _Gate("never")
    pool = _FakePool(2)
    pump = _Pump(pool.probe_many([g.item() for g in (hit, busy, never)], stop_on=_MATCH))
    _await(hit.started, "hit")
    _await(busy.started, "busy")
    _settle()
    assert not never.started.is_set(), "the window overflowed"

    hit.release.set()
    key, verdict = pump.next()
    assert (key, verdict.state) == ("hit", "fillable")
    _settle()
    assert not never.started.is_set(), "a probe was started after the match"

    # Draining the in-flight sibling ends the run; still nothing new begins.
    busy.release.set()
    assert pump.finish() == []
    assert not never.started.is_set(), "the drain started fresh work"


def test_a_match_completing_alongside_a_non_match_stops_replacement_work():
    """`wait` hands back batches, and a match anywhere in one has to stop the batch.

    Deciding one arbitrary member at a time lets a non-match start replacement work while the
    match sits unread beside it. The shim forces exactly that batch with no reliance on timing:
    the first `wait` does not return until every probe in the window has finished, so `done`
    necessarily holds both.
    """
    miss, hit, extra = _Gate("miss"), _Gate("hit", "fillable"), _Gate("extra")
    for gate in (miss, hit, extra):
        gate.release.set()
    pool = _FakePool(2)

    primed = threading.Event()
    real_wait = oracle.wait

    def batching_wait(futures, return_when=FIRST_COMPLETED, **kwargs):
        # `in_flight` is a dict, so this arrives in submission order: miss, then hit.
        ordered = list(futures)
        if not primed.is_set() and len(ordered) > 1:
            real_wait(ordered, return_when=ALL_COMPLETED)
            primed.set()
            # Hand the batch back whole and in submission order. Returning a real set would leave
            # "which member gets looked at first" to the hash of a future's address, and the
            # difference between deciding on the batch and deciding on one arbitrary member of it
            # is exactly what this test is for.
            return ordered, []
        return real_wait(ordered, return_when=return_when, **kwargs)

    oracle.wait = batching_wait
    try:
        seen = [
            key
            for key, _ in pool.probe_many(
                [gate.item() for gate in (miss, hit, extra)], stop_on=_MATCH
            )
        ]
    finally:
        oracle.wait = real_wait

    assert primed.is_set(), "the shim never saw a full window, so nothing was tested"
    assert seen == ["hit"], seen
    assert not extra.started.is_set(), "replacement work started despite a completed match"


def test_a_match_that_lands_between_answers_stops_replacement_work():
    """A probe can finish while the caller is away, before its turn to be delivered.

    `miss` is answered and handed over; the caller is then parked while `hit` finishes. Resuming
    must not start `extra`, even though `hit`'s answer has not been delivered yet.
    """
    miss, hit, extra = _Gate("miss"), _Gate("hit", "fillable"), _Gate("extra")
    hold = threading.Event()
    pool = _FakePool(2)
    pump = _Pump(
        pool.probe_many([g.item() for g in (miss, hit, extra)], stop_on=_MATCH), hold=hold
    )
    _await(miss.started, "miss")
    _await(hit.started, "hit")

    miss.release.set()
    assert pump.next()[0] == "miss"
    # The caller is parked on `hold`. Land the match behind its back.
    hit.release.set()
    _await(hit.answered, "hit's verdict")
    _settle()  # let the executor mark the future done; a precondition, not an assertion

    hold.set()
    rest = pump.finish()
    assert [key for key, _ in rest] == ["hit"], rest
    assert not extra.started.is_set(), "work started after a match had already landed"


def test_no_probe_starts_while_the_caller_holds_the_generator():
    # `hold` parks the generator mid-yield, exactly where a caller doing real work parks it.
    hit, busy, never = _Gate("hit", "fillable"), _Gate("busy"), _Gate("never")
    hold = threading.Event()
    pool = _FakePool(2)
    pump = _Pump(
        pool.probe_many([g.item() for g in (hit, busy, never)], stop_on=_MATCH), hold=hold
    )
    _await(hit.started, "hit")
    _await(busy.started, "busy")
    hit.release.set()
    assert pump.next()[0] == "hit"
    _settle()
    _settle()
    assert not never.started.is_set(), "a probe started while the caller held the generator"
    hold.set()
    busy.release.set()
    assert pump.finish() == []


def test_a_non_matching_answer_is_delivered_before_more_work_is_pulled():
    # A finished answer must not be held hostage to a producer that has not produced yet.
    first, second = _Gate("first"), _Gate("second")
    keep_producing = threading.Event()

    def producer():
        yield first.item()
        assert keep_producing.wait(timeout=5), "producer was never released"
        yield second.item()

    pool = _FakePool(1)
    pump = _Pump(pool.probe_many(producer()))
    # Only one item was pulled to fill the one-deep window, so the producer is still parked.
    _await(first.started, "the first probe")
    assert not keep_producing.is_set(), "the test released the producer too early"
    first.release.set()
    assert pump.next()[0] == "first", "the answer waited on the producer"

    keep_producing.set()
    second.release.set()
    assert [key for key, _ in pump.finish()] == ["second"]


def test_an_endless_producer_is_fine():
    gates = {}

    def producer():
        index = 0
        while True:
            gate = _Gate(f"g{index}", "fillable" if index == 3 else "unknown")
            gate.release.set()  # these do not need to be held open
            gates[gate.name] = gate
            yield gate.item()
            index += 1

    pool = _FakePool(2)
    seen = [key for key, _ in pool.probe_many(producer(), stop_on=_MATCH)]
    assert seen[-1] == "g3", seen
    assert len(gates) <= len(seen) + 2, f"pulled {len(gates)} items for {len(seen)} answers"


def test_without_a_match_every_item_is_probed():
    gates = [_Gate(f"g{i}") for i in range(9)]
    for gate in gates:
        gate.release.set()
    pool = _FakePool(3)
    seen = [key for key, _ in pool.probe_many([gate.item() for gate in gates])]
    assert sorted(seen) == sorted(gate.name for gate in gates)


def test_more_workers_than_items_is_fine():
    gates = [_Gate("a"), _Gate("b")]
    for gate in gates:
        gate.release.set()
    pool = _FakePool(4)
    seen = [key for key, _ in pool.probe_many([gate.item() for gate in gates], stop_on=_MATCH)]
    assert sorted(seen) == ["a", "b"]


def test_a_worker_error_reaches_the_caller():
    class _Boom(_GatedOracle):
        def probe(self, *args, **kwargs):
            raise RuntimeError("boom")

    pool = _FakePool(1)
    pool._all = [_Boom()]
    pool._free = queue.Queue()
    pool._free.put(pool._all[0])
    gate = _Gate("a")
    gate.release.set()
    try:
        list(pool.probe_many([gate.item()]))
    except RuntimeError as error:
        assert str(error) == "boom"
    else:
        raise AssertionError("a failing probe must not be swallowed")


def test_verdict_has_no_truth_value():
    verdict = Verdict("unknown", 1, 1, 0, 0, 0)
    assert verdict.unknown and not verdict.fillable and not verdict.unfillable
    try:
        bool(verdict)
    except TypeError:
        return
    raise AssertionError("a three-state verdict must not coerce to bool")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
