#!/usr/bin/env python3
"""Greedy theme-entry constructor with the real solver as the oracle.

`parallel_search` maximises Preferred count, but it does so from a cold grid and the
Preferred tier is ~1% of the dictionary at every length, so it finds whatever theme
words happen to be reachable — typically short oblique forms nobody recognises. A human
constructor works the other way round: place the marquee theme entries first, build the
grid around them. `theme_seed.py` approximates that but places greedily without ever
consulting the solver, so it cannot tell a placement that merely looks fine from one
that is unfillable.

This does the constructive version properly. Each round it tries every (empty slot,
marquee word) pair of matching length, screens each candidate with a cheap unary domain
count and then with a short real `ingrid_core` run — a template that dies at initial arc
consistency returns `Unfillable grid` in well under a second, so the oracle is cheap —
and keeps the survivor with the best score. Then it does it again on top of the survivor.

Scoring a survivor: prefer the word earlier in the marquee list (the list is ordered by
how unmistakably the word reads as theme), break ties by longer word, then by faster fill.
"""

import argparse
import collections
import concurrent.futures
import subprocess
import sys
import tempfile
from pathlib import Path


def read_grid(path):
    return [list(line.rstrip("\n")) for line in open(path, encoding="utf-8") if line.strip()]


def dump(grid):
    return "\n".join("".join(row) for row in grid) + "\n"


def slots(grid, min_run=3):
    height, width = len(grid), len(grid[0])
    out = []
    for r in range(height):
        c = 0
        while c < width:
            if grid[r][c] == "#":
                c += 1
                continue
            start = c
            while c < width and grid[r][c] != "#":
                c += 1
            if c - start >= min_run:
                out.append(("A", r, start, c - start))
    for c in range(width):
        r = 0
        while r < height:
            if grid[r][c] == "#":
                r += 1
                continue
            start = r
            while r < height and grid[r][c] != "#":
                r += 1
            if r - start >= min_run:
                out.append(("D", start, c, r - start))
    return out


def cells_of(slot):
    direction, r, c, length = slot
    return [(r, c + i) if direction == "A" else (r + i, c) for i in range(length)]


def load_by_length(paths, min_score):
    words = set()
    for path in paths:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            word, _, score = line.partition(";")
            if int(score or 50) >= min_score:
                words.add(word)
    by_length = collections.defaultdict(list)
    for word in words:
        by_length[len(word)].append(word)
    return by_length


def unary_ok(grid, by_length):
    """Every slot must keep at least one candidate. Cheap, catches most placements."""
    for slot in slots(grid):
        pattern = [grid[r][c] for r, c in cells_of(slot)]
        if all(ch == "." for ch in pattern):
            continue
        if not any(
            all(p == "." or candidate[i] == p for i, p in enumerate(pattern))
            for candidate in by_length[len(pattern)]
        ):
            return False
    return True


def oracle(grid, args):
    """Short real solve. Returns (filled, seconds) — 'Unfillable' comes back instantly."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(dump(grid))
        path = handle.name
    cmd = [
        args.binary,
        "--wordlist", args.wordlist,
        "--preferred-wordlist", args.preferred,
        "--blocklist", args.blocklist,
        "--min-score", str(args.min_score),
        "--max-shared-substring", str(args.max_shared_substring),
        "--cores", str(args.oracle_cores),
        "--timeout", str(args.oracle_timeout),
        "-t", path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.oracle_timeout + 40)
    except subprocess.TimeoutExpired:
        Path(path).unlink(missing_ok=True)
        return False, None
    Path(path).unlink(missing_ok=True)
    blob = proc.stdout + proc.stderr
    if "Unfillable" in blob or "No fill found" in blob:
        return False, None
    for line in blob.splitlines():
        if "finding fill" in line:
            seconds = float(line.split("finding fill")[0].split(",")[-1].strip().rstrip("s"))
            return True, seconds
    return False, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", required=True, help="template, may already carry fixed letters")
    parser.add_argument("--marquee", required=True, help="one word per line, best first")
    parser.add_argument("--wordlist", required=True)
    parser.add_argument("--preferred", required=True)
    parser.add_argument("--blocklist", required=True)
    parser.add_argument("--binary", default="./target/release/ingrid_core")
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--max-shared-substring", type=int, default=4)
    parser.add_argument("--oracle-timeout", type=int, default=12)
    parser.add_argument("--oracle-cores", type=int, default=2)
    parser.add_argument("--max-seeds", type=int, default=4)
    parser.add_argument("--max-trials-per-round", type=int, default=48)
    parser.add_argument("--jobs", type=int, default=5, help="oracle calls in flight")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    by_length = load_by_length([args.wordlist, args.preferred], args.min_score)
    marquee = [
        line.strip().lower()
        for line in open(args.marquee, encoding="utf-8")
        if line.strip() and not line.startswith("#")
    ]
    rank = {word: i for i, word in enumerate(marquee)}

    grid = read_grid(args.grid)
    placed = []

    for round_index in range(args.max_seeds):
        empty = [s for s in slots(grid) if all(grid[r][c] == "." for r, c in cells_of(s))]
        trials = []
        for word in marquee:
            if word in placed:
                continue
            for slot in empty:
                if slot[3] != len(word):
                    continue
                work = [row[:] for row in grid]
                for (r, c), ch in zip(cells_of(slot), word):
                    work[r][c] = ch
                if not unary_ok(work, by_length):
                    continue
                trials.append((rank[word], -len(word), word, slot, work))
        trials.sort(key=lambda t: (t[0], t[1]))
        trials = trials[: args.max_trials_per_round]
        print(f"round {round_index}: {len(trials)} candidate placements survive the unary screen")

        best = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(oracle, work, args): (rank_index, word, slot, work)
                for rank_index, _, word, slot, work in trials
            }
            accepted = []
            for future in concurrent.futures.as_completed(futures):
                rank_index, word, slot, work = futures[future]
                ok, seconds = future.result()
                print(f"  {word:<14} {slot}  {'FILL %.1fs' % seconds if ok else 'reject'}")
                if ok:
                    accepted.append((rank_index, -len(word), seconds, word, slot, work))
            if accepted:
                accepted.sort()
                _, _, seconds, word, slot, work = accepted[0]
                best = (word, slot, work, seconds)
        if best is None:
            print(f"round {round_index}: nothing placeable, stopping")
            break
        word, slot, work, seconds = best
        grid = work
        placed.append(word)
        print(f"round {round_index}: PLACED {word} at {slot} ({seconds:.1f}s)")

    Path(args.out).write_text(dump(grid), encoding="utf-8")
    print(f"\nseeded {len(placed)} marquee entries: {placed}")
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
