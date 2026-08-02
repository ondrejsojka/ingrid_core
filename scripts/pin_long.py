#!/usr/bin/env python3
"""Pin long theme entries into a dense template, one at a time, solver as the oracle.

`ingrid_core`'s preferred maximisation reliably finds short theme entries (3-5 letters)
and essentially never a long one, because a long slot has few candidates and the odds
that one of them is in a 79-word tier are tiny. Long entries are also the recognisable
ones. So pin them explicitly and let the solver keep the rest.

The accept step runs against a persistent oracle (`scripts/oracle.py`), which loads the
word lists once instead of once per probe. That buys two things this script previously
could not have:

* a **two-stage** accept. Every candidate placement is first screened by initial arc
  consistency, which is a *proof* of unfillability when it fails and costs tens of
  milliseconds. Only survivors pay for a real fill attempt.
* an **honest** stop condition. "No more theme entries fit" is now a claim about proofs:
  the round reports how many placements were refuted versus how many merely ran out of
  budget, and only calls a template saturated when every candidate was refuted.

Accepted placements are kept as fixed letters and the next round searches on top, so the
pins compose instead of being scored independently.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import OraclePool, add_oracle_arguments, oracle_kwargs  # noqa: E402


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


def trials_for(grid, theme, placed, min_len):
    """Every placement of an unplaced theme entry into a wholly empty slot of its length."""
    free = [s for s in slots(grid) if all(grid[r][c] == "." for r, c in cells_of(s))]
    out = []
    for word in sorted(theme, key=len, reverse=True):
        if word in placed or len(word) < min_len:
            continue
        for slot in free:
            if slot[3] != len(word):
                continue
            work = [row[:] for row in grid]
            for (r, c), ch in zip(cells_of(slot), word):
                work[r][c] = ch
            out.append(((word, slot), work))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--theme", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-len", type=int, default=6)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--jobs", type=int, default=3,
                    help="oracle processes; each holds its own copy of the dictionary")
    ap.add_argument("--probe-ms", type=int, default=3000,
                    help="fill-attempt budget per surviving candidate, in milliseconds")
    add_oracle_arguments(ap)
    ap.set_defaults(min_score=33, max_shared_substring=5, dupe_exempt_preferred=True)
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

    with OraclePool(jobs=args.jobs, **oracle_kwargs(args, probe_ms=0)) as pool:
        print(" ".join(f"{k}={v}" for k, v in pool.ready.items())
              + f" jobs={args.jobs}", file=sys.stderr, flush=True)

        for rnd in range(args.rounds):
            trials = trials_for(grid, theme, placed, args.min_len)
            if not trials:
                print(f"round {rnd}: no candidate placements left")
                break

            # Stage 1: arc consistency. A failure here is a proof, and it is cheap.
            print(f"round {rnd}: screening {len(trials)} placements", flush=True)
            work_by_key = dict(trials)
            refuted, survivors = 0, []
            for key, verdict in pool.probe_many(trials):
                if verdict.unfillable:
                    refuted += 1
                else:
                    survivors.append(key)
            print(f"round {rnd}: {refuted} refuted by arc consistency, "
                  f"{len(survivors)} survive", flush=True)

            if not survivors:
                print(f"round {rnd}: SATURATED -- every remaining placement is provably "
                      f"unfillable")
                break

            # Stage 2: try to fill the survivors, longest entry first, and stop at the first
            # one that actually fills.
            order = {key: i for i, (key, _) in enumerate(trials)}
            survivors.sort(key=lambda key: order[key])
            chosen, unknown = None, 0
            for key, verdict in pool.probe_many(
                [(key, work_by_key[key]) for key in survivors],
                ms=args.probe_ms,
                want_fill=True,
                stop_on=lambda verdict: verdict.fillable,
            ):
                word, slot = key
                print(f"  {word:<13} {slot} {verdict.state}", flush=True)
                if verdict.fillable:
                    chosen = (word, slot, work_by_key[key], list(verdict.fill))
                elif verdict.unknown:
                    unknown += 1

            if chosen is None:
                print(f"round {rnd}: nothing placeable, stopping -- "
                      f"{unknown} candidate(s) ran out of budget at {args.probe_ms} ms, "
                      f"so this is NOT a proof of saturation")
                break

            word, slot, work, fill = chosen
            grid = work
            placed.append(word)
            best_fill = fill
            print(f"round {rnd}: PLACED {word} at {slot}", flush=True)

    Path(args.out).write_text(dump(grid), encoding="utf-8")
    if best_fill:
        Path(args.out + ".filled").write_text("\n".join(best_fill) + "\n", encoding="utf-8")
    print(f"pinned {len(placed)}: {placed}")


if __name__ == "__main__":
    main()
