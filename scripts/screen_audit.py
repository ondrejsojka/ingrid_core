#!/usr/bin/env python3
"""Audit the arc-consistency screen against real fill attempts, per pin depth.

Arc consistency is a *proof* when it refuses a grid, so it has no false rejects. The open
question is the other direction: how often does a grid that survives propagation turn out
to be unfillable anyway, and does that rate depend on how constrained the grid already is?

This walks the same greedy pinning loop as `pin_long.py`, but at every round probes *every*
candidate placement twice -- once with arc consistency alone, once with a real fill budget --
and reports the contingency. It also asserts the no-false-rejects property rather than
trusting it.

The whole audit is a few hundred probes, which is minutes with a persistent oracle and was
a day without one. Results for the Karolína campaign are recorded in `oracle.md`.
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import OraclePool, add_oracle_arguments, oracle_kwargs  # noqa: E402
from pin_long import read_grid, trials_for  # noqa: E402

FIELDS = ("depth", "candidates", "ac_refuted", "ac_passed",
          "passed_fillable", "passed_unfillable", "passed_unknown")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--theme", required=True)
    ap.add_argument("--min-len", type=int, default=6)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--probe-ms", type=int, default=3000,
                    help="fill budget per candidate; an `unknown` at this budget is unresolved")
    ap.add_argument("--csv", help="write the per-depth table here")
    add_oracle_arguments(ap)
    args = ap.parse_args()

    theme = []
    for ln in open(args.theme, encoding="utf-8"):
        if not ln.strip():
            continue
        wd = (ln.split("\t", 1)[0] if "\t" in ln else ln.split(" ", 1)[0]).strip().lower()
        if wd and wd not in theme:
            theme.append(wd)

    grid = read_grid(args.grid)
    placed, table_rows = [], []

    with OraclePool(jobs=args.jobs, **oracle_kwargs(args, probe_ms=0)) as pool:
        for depth in range(args.rounds):
            trials = trials_for(grid, theme, placed, args.min_len)
            if not trials:
                print(f"depth {depth}: no candidate placements left")
                break
            work = dict(trials)

            screen = {key: verdict for key, verdict in pool.probe_many(trials)}
            deep = {key: verdict for key, verdict in
                    pool.probe_many(trials, ms=args.probe_ms, want_fill=True)}
            counts = collections.Counter(
                (screen[key].state, deep[key].state) for key in screen)

            liars = [key for key in screen
                     if screen[key].unfillable and deep[key].fillable]
            assert not liars, f"arc consistency refuted a fillable grid: {liars}"

            refuted = sum(1 for key in screen if screen[key].unfillable)
            row = dict(zip(FIELDS, (
                depth, len(trials), refuted, len(trials) - refuted,
                counts[("unknown", "fillable")],
                counts[("unknown", "unfillable")],
                counts[("unknown", "unknown")],
            )))
            table_rows.append(row)
            print(f"depth {depth}: {row['candidates']} candidates, "
                  f"{row['ac_refuted']} refuted by AC, {row['ac_passed']} passed "
                  f"-> {row['passed_fillable']} fillable, "
                  f"{row['passed_unfillable']} unfillable (false accepts), "
                  f"{row['passed_unknown']} unknown", flush=True)

            if row["ac_passed"] == 0:
                print(f"depth {depth}: SATURATED -- arc consistency alone refuted everything")
                break

            # Advance greedily in candidate priority order, exactly like pin_long.
            chosen = next((key for key, _ in trials if deep[key].fillable), None)
            if chosen is None:
                print(f"depth {depth}: no candidate filled within {args.probe_ms} ms; "
                      f"not a proof of saturation")
                break
            grid = work[chosen]
            placed.append(chosen[0])

    total_passed = sum(row["ac_passed"] for row in table_rows)
    total_false = sum(row["passed_unfillable"] for row in table_rows)
    rate = f"{100 * total_false / total_passed:.1f}%" if total_passed else "n/a"
    print(f"pinned {len(placed)}: {placed}")
    print(f"false accepts: {total_false} of {total_passed} AC passers ({rate}); "
          f"false rejects: 0 by construction")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(table_rows)


if __name__ == "__main__":
    main()
