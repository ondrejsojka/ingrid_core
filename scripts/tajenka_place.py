#!/usr/bin/env python3
"""Choose where to seat a multi-part tajenka in a template, screened by real domains.

Seeding two long entries into a crossword is not a placement problem, it is a
*crossing* problem. Two seeded entries that share a column hand the down slot through
that column two fixed letters at once, and in Czech such a pattern is usually empty:
`ester` + `krumbachová` in rows that overlap produced `e...v...` and `e.c`, both with
zero matching words, so `ingrid_core` reported `Unfillable grid` before searching.

Forbidding shared crossings outright is too strict — it rejected 39 of 40 templates.
Instead this enumerates every cell-disjoint placement and screens each one by counting,
for every slot, how many dictionary words match the induced letter pattern. A placement
survives only if every slot keeps at least `--min-domain` candidates. That is the same
unary filter `ingrid_core` applies first, so a survivor is guaranteed not to die at
initial arc consistency for a unary reason.
"""

import argparse
import collections
import itertools
import sys
from pathlib import Path


def read_grid(path):
    rows = [line.rstrip("\n") for line in open(path, encoding="utf-8") if line.strip()]
    width = max(len(r) for r in rows)
    if any(len(r) != width for r in rows):
        sys.exit(f"{path}: ragged grid")
    return [list(r) for r in rows]


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


def load_words(paths, min_score):
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


def screen(grid, placement, words, by_length, min_domain):
    """Apply the placement, then count candidates for every slot. Returns (ok, worst)."""
    work = [row[:] for row in grid]
    for slot, text in placement:
        for (r, c), ch in zip(cells_of(slot), text):
            if work[r][c] != ".":
                return False, ("overlap", r, c)
            work[r][c] = ch
    worst = None
    for slot in slots(work):
        pattern = [work[r][c] for r, c in cells_of(slot)]
        if all(ch == "." for ch in pattern):
            continue
        count = sum(
            1
            for candidate in by_length[len(pattern)]
            if all(p == "." or candidate[i] == p for i, p in enumerate(pattern))
        )
        if worst is None or count < worst[1]:
            worst = ("".join(pattern), count, slot)
        if count < min_domain:
            return False, worst
    return True, worst


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", action="append", required=True, help="template file; repeatable")
    parser.add_argument("--wordlist", action="append", required=True)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--min-domain", type=int, default=1)
    parser.add_argument(
        "--word", action="append", required=True, help="tajenka part, in reading order; repeatable"
    )
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--max-per-grid", type=int, default=3)
    args = parser.parse_args()

    by_length = load_words(args.wordlist, args.min_score)
    words = args.word
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    total = 0
    for path in args.grid:
        grid = read_grid(path)
        pool = slots(grid)
        options = [[s for s in pool if s[3] == len(w)] for w in words]
        kept = 0
        for combo in itertools.product(*options):
            seen = set()
            clash = False
            for slot in combo:
                cells = set(cells_of(slot))
                if cells & seen:
                    clash = True
                    break
                seen |= cells
            if clash:
                continue
            ok, worst = screen(grid, list(zip(combo, words)), None, by_length, args.min_domain)
            if not ok:
                continue
            work = [row[:] for row in grid]
            for slot, text in zip(combo, words):
                for (r, c), ch in zip(cells_of(slot), text):
                    work[r][c] = ch
            name = Path(path).stem
            out = outdir / f"{name}_p{kept}.txt"
            out.write_text("\n".join("".join(row) for row in work) + "\n", encoding="utf-8")
            (outdir / f"{name}_p{kept}.place").write_text(
                "\n".join(f"{w} {s[0]} {s[1]} {s[2]} {s[3]}" for s, w in zip(combo, words)) + "\n",
                encoding="utf-8",
            )
            print(f"{out}  placement={list(zip(words, combo))}  tightest={worst}")
            kept += 1
            total += 1
            if kept >= args.max_per_grid:
                break
        if kept == 0:
            print(f"{path}: no placement survives the domain screen")
    print(f"{total} seeded template(s) written to {outdir}")


if __name__ == "__main__":
    main()
