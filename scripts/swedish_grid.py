#!/usr/bin/env python3
"""Generate a Czech švédská-style crossword template of arbitrary size.

Unlike `lfs_grid11.py` this drops 180-degree rotational symmetry, which Czech
magazine crosswords do not use — the blocks are legend cells, and a legend cell goes
wherever a word starts. Dropping symmetry is what makes a single long tajenka run
affordable: under symmetry every long slot forces a mirrored twin, and the ratio
table prices a length-11 slot at about -13.8 bits against the kappa* = 0.95 frontier.

Still enforced, because they are quality rather than convention:
  * fully checked — every white cell belongs to an across run and a down run, both
    at least `--min-run`. With `--min-run 3` there are no two-letter answers, which
    is stricter than the real Filmové listy grid and deliberately so: its two-letter
    slots are all `SPZ RAKOVNÍKA`-class initialisms.
  * connected white area, no block component larger than `--max-block-component`.
  * a forced set of run lengths for the tajenka, mutually non-crossing.

Objective: the cheap kappa surrogate from `local/rich/grids/report.md`,
kappa ~= C / (2 * alpha * <d(L)/L>), plus hygiene penalties.
"""

import argparse
import collections
import math
import random
from pathlib import Path

C_BITS, ALPHA = 4.5861, 0.9878


def load_dl(path, min_score):
    counts = collections.Counter()
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        word, _, score = line.partition(";")
        if int(score or 50) >= min_score:
            counts[len(word)] += 1
    return {length: math.log2(n) for length, n in counts.items() if n}


ALLOW_UNCHECKED = False


def run_lengths(grid, width, height):
    """Run lengths in both directions.

    With ALLOW_UNCHECKED, runs of length 1 are dropped rather than reported. A length-1
    run is a cell that belongs to a word in one direction only — an *unchecked* cell,
    which a real Czech švédská is full of. Reporting it makes `min_run 3` reject the
    grid, which is what forces the fully-checked American density this generator
    produced by default. Length-2 runs are still reported and still rejected: those
    would be two-letter answers."""
    out = []
    for r in range(height):
        c = 0
        while c < width:
            if grid[r][c]:
                c += 1
                continue
            start = c
            while c < width and not grid[r][c]:
                c += 1
            if not (ALLOW_UNCHECKED and c - start == 1):
                out.append(c - start)
    for c in range(width):
        r = 0
        while r < height:
            if grid[r][c]:
                r += 1
                continue
            start = r
            while r < height and not grid[r][c]:
                r += 1
            if not (ALLOW_UNCHECKED and r - start == 1):
                out.append(r - start)
    return out


def kappa_surrogate(lengths, dl):
    total = sum(lengths)
    bits = sum(dl.get(length, 1.0) for length in lengths)
    return C_BITS / (2 * ALPHA * (bits / total)) if total else 9.0


def connected(grid, width, height):
    cells = [(r, c) for r in range(height) for c in range(width) if not grid[r][c]]
    if not cells:
        return False
    seen, stack = {cells[0]}, [cells[0]]
    while stack:
        r, c = stack.pop()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p = (r + dr, c + dc)
            if 0 <= p[0] < height and 0 <= p[1] < width and not grid[p[0]][p[1]] and p not in seen:
                seen.add(p)
                stack.append(p)
    return len(seen) == len(cells)


def block_components(grid, width, height, skip_frame=False):
    """Sizes of orthogonally connected block clumps. With `skip_frame` the top row and
    left column are ignored: in a švédská they are legend cells by construction, so
    their being one big clump is the design, not a defect."""
    lo = 1 if skip_frame else 0
    seen, sizes = set(), []
    for r in range(lo, height):
        for c in range(lo, width):
            if grid[r][c] and (r, c) not in seen:
                stack, size = [(r, c)], 0
                seen.add((r, c))
                while stack:
                    a, b = stack.pop()
                    size += 1
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        p = (a + dr, b + dc)
                        if (
                            lo <= p[0] < height
                            and lo <= p[1] < width
                            and grid[p[0]][p[1]]
                            and p not in seen
                        ):
                            seen.add(p)
                            stack.append(p)
                sizes.append(size)
    return sizes or [0]


def adjacent_pairs(grid, width, height):
    return sum(
        1
        for r in range(height)
        for c in range(width)
        if grid[r][c]
        for dr, dc in ((0, 1), (1, 0))
        if r + dr < height and c + dc < width and grid[r + dr][c + dc]
    )


def answer_starts(grid, width, height, min_run):
    starts = set()
    for r in range(height):
        c = 0
        while c < width:
            if grid[r][c]:
                c += 1
                continue
            start = c
            while c < width and not grid[r][c]:
                c += 1
            if c - start >= min_run:
                starts.add(("A", r, start))
    for c in range(width):
        r = 0
        while r < height:
            if grid[r][c]:
                r += 1
                continue
            start = r
            while r < height and not grid[r][c]:
                r += 1
            if r - start >= min_run:
                starts.add(("D", start, c))
    return starts


def dead_cells_exact(grid, width, height, min_run):
    legend = set()
    for kind, r, c in answer_starts(grid, width, height, min_run):
        legend.add((r, c - 1) if kind == "A" else (r - 1, c))
    return sum(1 for r in range(height) for c in range(width)
               if grid[r][c] and (r, c) not in legend)


def dead_cells(grid, width, height):
    """Blocks carrying no legend. In a fully checked grid a block at (r, c) carries the
    across legend of (r, c+1) and the down legend of (r+1, c), so it is dead exactly when
    both of those are blocks or off-grid. These are the cells a reader sees as empty."""
    return sum(
        1
        for r in range(height)
        for c in range(width)
        if grid[r][c]
        and not (c + 1 < width and not grid[r][c + 1])
        and not (r + 1 < height and not grid[r + 1][c])
    )


def energy(grid, width, height, dl, cfg):
    lengths = run_lengths(grid, width, height)
    score = 45.0 * sum(1 for length in lengths if length < cfg.min_run)
    score += 45.0 * sum(1 for length in lengths if length > cfg.max_run)
    for wanted, count in cfg.required.items():
        have = sum(1 for length in lengths if length == wanted)
        score += 70.0 * max(0, count - have)
    if not connected(grid, width, height):
        score += 130.0
    score += 3.0 * max(0, sum(1 for x in lengths if x == 5) - cfg.max_fives)
    short = sum(1 for x in lengths if x in (3, 4)) / len(lengths)
    score += 150.0 * max(0.0, short - cfg.max_short_share)
    score += 9.0 * sum(
        max(0, s - cfg.max_block_component)
        for s in block_components(grid, width, height, cfg.frame)
    )
    interior = 1 if cfg.frame else 0
    blocks = sum(
        1 for r in range(interior, height) for c in range(interior, width) if grid[r][c]
    )
    score += cfg.kappa_weight * kappa_surrogate(lengths, dl)
    score += 0.20 * abs(blocks - cfg.target_blocks)
    score += 12.0 * max(0, dead_cells_exact(grid, width, height, cfg.min_run) - cfg.max_dead)
    score -= 0.08 * min(adjacent_pairs(grid, width, height), 60)
    return score


def anneal(width, height, dl, cfg, seed):
    rnd = random.Random(seed)
    grid = [[rnd.random() < cfg.density for _ in range(width)] for _ in range(height)]
    # A švédská legend lives in the cell before its word, so no word may start on the
    # top edge or the left edge: row 0 and column 0 are legend cells by construction.
    free = [(r, c) for r in range(height) for c in range(width) if not (cfg.frame and (r == 0 or c == 0))]
    if cfg.frame:
        for c in range(width):
            grid[0][c] = True
        for r in range(height):
            grid[r][0] = True
    current = energy(grid, width, height, dl, cfg)
    best = (current, [row[:] for row in grid])
    hot, cold = 7.0, 0.04
    for i in range(cfg.iterations):
        temperature = hot * (cold / hot) ** (i / cfg.iterations)
        r, c = rnd.choice(free)
        grid[r][c] = not grid[r][c]
        candidate = energy(grid, width, height, dl, cfg)
        if candidate <= current or rnd.random() < math.exp((current - candidate) / temperature):
            current = candidate
            if candidate < best[0]:
                best = (candidate, [row[:] for row in grid])
        else:
            grid[r][c] = not grid[r][c]
    return best


def slots(grid, width, height, min_run):
    """(direction, row, col, length) for every run at or above min_run."""
    out = []
    for r in range(height):
        c = 0
        while c < width:
            if grid[r][c]:
                c += 1
                continue
            start = c
            while c < width and not grid[r][c]:
                c += 1
            if c - start >= min_run:
                out.append(("A", r, start, c - start))
    for c in range(width):
        r = 0
        while r < height:
            if grid[r][c]:
                r += 1
                continue
            start = r
            while r < height and not grid[r][c]:
                r += 1
            if r - start >= min_run:
                out.append(("D", start, c, r - start))
    return out


def cells_of(slot):
    direction, r, c, length = slot
    return [(r, c + i) if direction == "A" else (r + i, c) for i in range(length)]


def tajenka_placements(grid, width, height, min_run, wanted):
    """Seat the requested lengths in slots that are *crossing*-disjoint.

    Cell-disjointness is not enough. If a third slot crosses two tajenka parts it
    inherits two fixed letters at once, and in Czech that pattern is usually empty:
    seeding `ester` and `krumbachová` into rows that share columns produced patterns
    like `e...v...` and `e.c`, with zero matching words, so the grid was reported
    Unfillable before the search even started. Requiring that no other slot touches
    more than one tajenka part removes the whole failure class.
    """
    pool = slots(grid, width, height, min_run)
    chosen, used = [], []
    for length in wanted:
        for slot in pool:
            if slot[3] != length:
                continue
            cells = set(cells_of(slot))
            if any(cells & other for other in used):
                continue
            if any(
                sum(1 for group in used + [cells] if set(cells_of(other)) & group) > 1
                for other in pool
                if other != slot
            ):
                continue
            chosen.append(slot)
            used.append(cells)
            break
        else:
            return None
    return chosen


def feasible(grid, width, height, cfg):
    lengths = run_lengths(grid, width, height)
    return (
        all(cfg.min_run <= x <= cfg.max_run for x in lengths)
        and connected(grid, width, height)
        and all(
            sum(1 for x in lengths if x == wanted) >= count
            for wanted, count in cfg.required.items()
        )
        and max(block_components(grid, width, height, cfg.frame)) <= cfg.max_block_component
        and dead_cells_exact(grid, width, height, cfg.min_run) <= cfg.max_dead
    )


class Config:
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wordlist", required=True)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--width", type=int, default=14)
    parser.add_argument("--height", type=int, default=10)
    parser.add_argument("--min-run", type=int, default=3)
    parser.add_argument("--max-run", type=int, default=11)
    parser.add_argument("--required", default="11:1,5:1", help="LEN:COUNT,... run lengths to force")
    parser.add_argument("--tajenka-lengths", default="5,11")
    parser.add_argument("--max-fives", type=int, default=14)
    parser.add_argument("--max-short-share", type=float, default=0.34)
    parser.add_argument("--max-block-component", type=int, default=5)
    parser.add_argument("--allow-unchecked", action="store_true",
                        help="permit cells belonging to a word in one direction only, as a real švédská does")
    parser.add_argument("--max-empty", type=float, default=1.0,
                        help="cap on blocks carrying no legend, as a fraction of all cells")
    parser.add_argument("--target-blocks", type=int, default=34)
    parser.add_argument("--density", type=float, default=0.24)
    parser.add_argument(
        "--frame",
        action="store_true",
        help="švédská mode: row 0 and column 0 are legend cells, so every word has a cell "
        "before it to carry its legend",
    )
    parser.add_argument("--kappa-weight", type=float, default=45.0)
    parser.add_argument("--iterations", type=int, default=60000)
    parser.add_argument("--restarts", type=int, default=24)
    parser.add_argument("--seed-base", type=int, default=0)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--keep", type=int, default=10)
    args = parser.parse_args()

    cfg = Config()
    cfg.min_run, cfg.max_run = args.min_run, args.max_run
    cfg.required = {
        int(part.split(":")[0]): int(part.split(":")[1]) for part in args.required.split(",") if part
    }
    cfg.max_fives = args.max_fives
    cfg.max_short_share = args.max_short_share
    cfg.max_dead = int(args.max_empty * args.width * args.height)
    global ALLOW_UNCHECKED
    ALLOW_UNCHECKED = args.allow_unchecked
    cfg.max_block_component = args.max_block_component
    cfg.target_blocks = args.target_blocks
    cfg.density = args.density
    cfg.frame = args.frame
    cfg.kappa_weight = args.kappa_weight
    cfg.iterations = args.iterations

    dl = load_dl(args.wordlist, args.min_score)
    wanted = [int(x) for x in args.tajenka_lengths.split(",") if x.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    pool = []
    for restart in range(args.restarts):
        _, grid = anneal(args.width, args.height, dl, cfg, args.seed_base + 31 * restart)
        if not feasible(grid, args.width, args.height, cfg):
            continue
        placement = tajenka_placements(grid, args.width, args.height, cfg.min_run, wanted)
        if placement is None:
            continue
        lengths = run_lengths(grid, args.width, args.height)
        pool.append(
            {
                "kappa": kappa_surrogate(lengths, dl),
                "restart": restart,
                "slots": len(lengths),
                "blocks": sum(map(sum, grid)),
                "interior_blocks": sum(
                    1 for r in range(1, args.height) for c in range(1, args.width) if grid[r][c]
                ),
                "adjacent": adjacent_pairs(grid, args.width, args.height),
                "share34": sum(1 for x in lengths if x in (3, 4)) / len(lengths),
                "s69": sum(1 for x in lengths if 6 <= x <= 9),
                "hist": dict(sorted(collections.Counter(lengths).items())),
                "tajenka": placement,
                "grid": grid,
            }
        )
    pool.sort(key=lambda e: e["kappa"])
    print("kappa   restart slots blocks adj  short  s69  tajenka                 histogram")
    for rank, entry in enumerate(pool[: args.keep]):
        path = outdir / f"s{rank:02d}.txt"
        path.write_text(
            "\n".join("".join("#" if cell else "." for cell in row) for row in entry["grid"]) + "\n",
            encoding="utf-8",
        )
        (outdir / f"s{rank:02d}.tajenka").write_text(
            "\n".join(f"{d} {r} {c} {n}" for d, r, c, n in entry["tajenka"]) + "\n", encoding="utf-8"
        )
        print(
            f"{entry['kappa']:.4f} {entry['restart']:7d} {entry['slots']:5d} {entry['blocks']:6d} "
            f"{entry['adjacent']:3d} {entry['share34']:.3f} {entry['s69']:4d}  "
            f"{entry['tajenka']}  {entry['hist']} -> {path}"
        )
    print(f"{len(pool)} feasible of {args.restarts}")


if __name__ == "__main__":
    main()
