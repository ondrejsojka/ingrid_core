#!/usr/bin/env python3
"""Contract tests for `scripts/oracle.py` that need no `ingrid_core` binary.

The two things worth pinning here are the ones green Rust tests cannot see: the row handling that
decides *which* grid the engine is asked about, and the pool's concurrency contract. Both were
wrong in ways that produced confident answers to the wrong question.

Run with `python3 scripts/test_oracle_client.py` (or under pytest; the assertions are plain).
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import OraclePool, Verdict, _as_rows  # noqa: E402


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


class _FakeOracle:
    """Sleeps for the requested time and records that it started."""

    def __init__(self, started, lock):
        self.started = started
        self.lock = lock

    def probe(self, grid, ms=None, want_fill=False):
        with self.lock:
            self.started.append(grid["name"])
        time.sleep(grid["sleep"])
        return Verdict(grid["state"], 1, 1, 0, 0, 0)

    def close(self):
        pass


class _FakePool(OraclePool):
    def __init__(self, jobs):
        self.started: list[str] = []
        lock = threading.Lock()
        self._free = queue.Queue()
        self._all = [_FakeOracle(self.started, lock) for _ in range(jobs)]
        for oracle in self._all:
            self._free.put(oracle)


def _run(jobs, specs, stop_on=None, consumer_delay=0.0):
    pool = _FakePool(jobs)
    items = [(name, {"name": name, "sleep": sleep, "state": state}) for name, sleep, state in specs]
    started_at = time.time()
    yielded = []
    for key, _ in pool.probe_many(items, stop_on=stop_on):
        yielded.append(key)
        if consumer_delay:
            time.sleep(consumer_delay)
    return time.time() - started_at, yielded, pool.started


_MATCH = lambda verdict: verdict.fillable  # noqa: E731


def test_no_probe_starts_after_a_match_is_observed():
    # Two workers: `fast` matches at 50 ms while `slow` is still running, so `extra` must never be
    # picked up. The generator returning a credit before evaluating stop_on would start it.
    _, yielded, started = _run(
        2,
        [("fast", 0.05, "fillable"), ("slow", 0.4, "unknown"), ("extra", 0.01, "unknown")],
        _MATCH,
    )
    assert yielded == ["fast"], yielded
    assert started == ["fast", "slow"], started


def test_no_probe_starts_while_the_caller_holds_the_generator():
    # The decision must be recorded before the value is yielded, not after the caller resumes us.
    _, yielded, started = _run(
        2,
        [("fast", 0.05, "fillable"), ("slow", 0.3, "unknown"), ("extra", 0.01, "unknown")],
        _MATCH,
        consumer_delay=0.15,
    )
    assert yielded == ["fast"], yielded
    assert "extra" not in started, started


def test_a_match_costs_one_in_flight_probe_not_the_remaining_work():
    elapsed, _, started = _run(
        2, [("fast", 0.05, "fillable")] + [(f"slow{i}", 0.1, "unknown") for i in range(9)], _MATCH
    )
    assert len(started) <= 2, started
    assert elapsed < 0.25, elapsed


def test_without_a_match_every_item_is_probed():
    _, yielded, started = _run(3, [(f"i{i}", 0.01, "unknown") for i in range(9)])
    assert len(yielded) == 9 and len(started) == 9


def test_more_workers_than_items_is_fine():
    _, yielded, started = _run(4, [("a", 0.01, "unknown"), ("b", 0.01, "unknown")], _MATCH)
    assert sorted(yielded) == ["a", "b"] and len(started) == 2


def test_a_worker_error_reaches_the_caller():
    class _Boom(_FakeOracle):
        def probe(self, *args, **kwargs):
            raise RuntimeError("boom")

    pool = _FakePool(1)
    pool._all = [_Boom(pool.started, threading.Lock())]
    pool._free = queue.Queue()
    pool._free.put(pool._all[0])
    try:
        list(pool.probe_many([("a", {"name": "a", "sleep": 0, "state": "unknown"})]))
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
