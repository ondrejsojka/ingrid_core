#!/usr/bin/env python3
"""Constructive 15x15 grid search for templates that carry a long tajenka slot.

A plain annealer over block patterns cannot reach a length-11 slot: from an
11-free start there is no downhill path that grows a first 11-run, because every
intermediate state pays the run-length penalty. This script sidesteps that by
*planting* the long runs first and freezing their rows, then annealing only the
remaining symmetry orbits. The objective machinery (kappa surrogate, connectivity,
block clumps, adjacency) is imported from `swedish_grid.py`; what is unique here is
the 180-degree symmetric driver — `squares_2x2`, the orbit walk, and its own
feasibility gates.
"""

import argparse
import math
import random
from pathlib import Path

from swedish_grid import adjacent_pairs, block_components, connected, kappa_surrogate, load_dl

N = 15


def runs(grid):
    out = []
    for r in range(N):
        c = 0
        while c < N:
            if grid[r][c]:
                c += 1
                continue
            start = c
            while c < N and not grid[r][c]:
                c += 1
            out.append(c - start)
    for c in range(N):
        r = 0
        while r < N:
            if grid[r][c]:
                r += 1
                continue
            start = r
            while r < N and not grid[r][c]:
                r += 1
            out.append(r - start)
    return out


def squares_2x2(grid):
    return sum(
        1
        for r in range(N - 1)
        for c in range(N - 1)
        if grid[r][c] and grid[r][c + 1] and grid[r + 1][c] and grid[r + 1][c + 1]
    )


def energy(grid, dl, want_long, long_length, max_fives, max_short_share):
    lengths = runs(grid)
    score = 40 * sum(1 for length in lengths if length < 3)
    score += 40 * sum(1 for length in lengths if length > long_length)
    have = sum(1 for length in lengths if length == long_length)
    score += 60 * max(0, want_long - have) + 25 * max(0, have - want_long)
    if not connected(grid, N, N):
        score += 120
    score += 3 * max(0, sum(1 for length in lengths if length == 5) - max_fives)
    short = sum(1 for length in lengths if length in (3, 4)) / len(lengths)
    score += 140 * max(0.0, short - max_short_share)
    score += 8 * sum(max(0, size - 6) for size in block_components(grid, N, N))
    score += 8 * max(0, squares_2x2(grid) - 3)
    score += 45 * kappa_surrogate(lengths, dl)
    score += 0.12 * abs(sum(map(sum, grid)) - 50)
    score -= 0.10 * min(adjacent_pairs(grid, N, N), 44)
    return score


def orbits():
    out, seen = [], set()
    for r in range(N):
        for c in range(N):
            if (r, c) in seen:
                continue
            mirror = (N - 1 - r, N - 1 - c)
            seen.add((r, c))
            seen.add(mirror)
            out.append(((r, c), mirror) if mirror != (r, c) else ((r, c),))
    return out


def anneal(row, dl, long_length, iterations, seed, max_fives, max_short_share):
    rnd = random.Random(seed)
    grid = [[False] * N for _ in range(N)]
    for c in range(long_length, N):
        grid[row][c] = True
        grid[N - 1 - row][N - 1 - c] = True
    frozen = {(row, c) for c in range(N)} | {(N - 1 - row, c) for c in range(N)}
    free = [o for o in orbits() if not any(p in frozen for p in o)]
    for orbit in free:
        if rnd.random() < 0.22:
            for r, c in orbit:
                grid[r][c] = True

    args = (dl, 2, long_length, max_fives, max_short_share)
    current = energy(grid, *args)
    best = (current, [row_[:] for row_ in grid])
    hot, cold = 6.0, 0.05
    for i in range(iterations):
        temperature = hot * (cold / hot) ** (i / iterations)
        orbit = rnd.choice(free)
        for r, c in orbit:
            grid[r][c] = not grid[r][c]
        candidate = energy(grid, *args)
        if candidate <= current or rnd.random() < math.exp((current - candidate) / temperature):
            current = candidate
            if candidate < best[0]:
                best = (candidate, [row_[:] for row_ in grid])
        else:
            for r, c in orbit:
                grid[r][c] = not grid[r][c]
    return best


def feasible(grid, long_length):
    lengths = runs(grid)
    return (
        all(3 <= length <= long_length for length in lengths)
        and connected(grid, N, N)
        and sum(1 for length in lengths if length == long_length) == 2
        and max(block_components(grid, N, N)) <= 6
        and squares_2x2(grid) <= 4
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wordlist", required=True)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--long-length", type=int, default=11)
    parser.add_argument("--rows", default="2,3,4,5,6", help="rows to plant the long run in")
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=60000)
    parser.add_argument("--max-fives", type=int, default=28)
    parser.add_argument("--max-short-share", type=float, default=0.32)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--keep", type=int, default=8)
    args = parser.parse_args()

    dl = load_dl(args.wordlist, args.min_score)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    pool = []
    for row in [int(r) for r in args.rows.split(",")]:
        for seed in range(args.seeds):
            _, grid = anneal(
                row, dl, args.long_length, args.iterations,
                7000 + row * 97 + seed, args.max_fives, args.max_short_share,
            )
            if not feasible(grid, args.long_length):
                continue
            lengths = runs(grid)
            pool.append(
                {
                    "kappa": kappa_surrogate(lengths, dl),
                    "row": row,
                    "seed": seed,
                    "slots": len(lengths),
                    "blocks": sum(map(sum, grid)),
                    "adjacent": adjacent_pairs(grid, N, N),
                    "share34": sum(1 for x in lengths if x in (3, 4)) / len(lengths),
                    "s69": sum(1 for x in lengths if 6 <= x <= 9),
                    "fives": sum(1 for x in lengths if x == 5),
                    "grid": grid,
                }
            )
    pool.sort(key=lambda entry: entry["kappa"])
    print("kappa   row seed slots blocks adj  short  s69 fives")
    for rank, entry in enumerate(pool[: args.keep]):
        path = outdir / f"k{rank:02d}_r{entry['row']}s{entry['seed']}.txt"
        path.write_text(
            "\n".join("".join("#" if cell else "." for cell in r) for r in entry["grid"]) + "\n",
            encoding="utf-8",
        )
        print(
            f"{entry['kappa']:.4f} {entry['row']:3d} {entry['seed']:4d} {entry['slots']:5d} "
            f"{entry['blocks']:6d} {entry['adjacent']:3d} {entry['share34']:.3f} "
            f"{entry['s69']:4d} {entry['fives']:5d}  -> {path}"
        )
    print(f"{len(pool)} feasible of {len(args.rows.split(',')) * args.seeds}")


if __name__ == "__main__":
    main()
