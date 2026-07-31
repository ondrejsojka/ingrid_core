#!/usr/bin/env python3
"""Constrainedness (kappa) and fill margin for a crossword configuration.

Fill margin is a pre-search budget: how much further a configuration can be
tightened before it stops filling. See fill-margin.md for the definitions and
the calibration behind `--kappa-star`.

    kappa = 1 - S_hat_0 / sum_s log2|D_s|

where D_s are slot domains after crossing arc consistency and S_hat_0 is the
annealed log-count, sum_s log2|D_s| + sum_c log2 p_c.

Two subcommands:

  kappa   Score one configuration. Cheap; this is the everyday use.
  sweep   Re-measure the calibration: kappa plus a real solver outcome for
          every (shape x policy) pair in a manifest. Writes one CSV.

Examples:

  python3 scripts/fill_margin.py kappa \\
    --wordlist local/cstenten.dict --min-score 30 \\
    local/trials/czech_15x15_split_long.txt

  python3 scripts/fill_margin.py kappa \\
    --wordlist local/cstenten.dict --min-score 36 --ignore-diacritics \\
    --compare local/cstenten.dict:30 \\
    local/trials/czech_15x15_split_long.txt

  python3 scripts/fill_margin.py sweep \\
    --manifest calibration/policies.tsv \\
    --shapes calibration/shapes.tsv \\
    --binary target/release/ingrid_core \\
    --timeout 180 --jobs 8 \\
    --output calibration/kappa_measurements.csv

Requires numpy. `sweep` additionally requires a built `ingrid_core`.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import subprocess
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

DEFAULT_KAPPA_STAR = 0.95
# Highest kappa that produced a fill anywhere in the committed calibration.
OBSERVED_FILL_CEILING = 1.00


# --------------------------------------------------------------------------
# grid and word list
# --------------------------------------------------------------------------


class Grid:
    """Slots, crossings and fixed letters parsed from an Ingrid grid file."""

    def __init__(self, path: Path, rows: list[str] | None = None):
        if rows is None:
            rows = [line for line in path.read_text(encoding="utf-8").split() if line]
        if not rows or any(len(row) != len(rows[0]) for row in rows):
            raise ValueError(f"{path}: grid rows must be non-empty and equal length")
        self.path = path
        self.rows = rows
        self.slots: list[list[tuple[int, int]]] = []
        by_cell: dict[tuple[int, int], list[int]] = defaultdict(list)

        def add(run: list[tuple[int, int]]) -> None:
            if len(run) > 1:
                self.slots.append(run)
                for cell in run:
                    by_cell[cell].append(len(self.slots) - 1)

        height, width = len(rows), len(rows[0])
        for r in range(height):
            run: list[tuple[int, int]] = []
            for c in range(width):
                if rows[r][c] == "#":
                    add(run)
                    run = []
                else:
                    run.append((r, c))
            add(run)
        for c in range(width):
            run = []
            for r in range(height):
                if rows[r][c] == "#":
                    add(run)
                    run = []
                else:
                    run.append((r, c))
            add(run)

        self.fixed = {
            (r, c): rows[r][c]
            for r in range(height)
            for c in range(width)
            if rows[r][c] not in "#."
        }
        # A crossing is a cell shared by exactly two slots.
        self.crossings = [
            (slots[0], slots[1], cell)
            for cell, slots in by_cell.items()
            if len(slots) == 2
        ]

    def lengths(self) -> dict[int, int]:
        return dict(sorted(Counter(len(slot) for slot in self.slots).items()))

    @classmethod
    def from_rows(cls, rows: list[str], path: Path | None = None) -> "Grid":
        """Score a candidate template without writing it to disk first."""
        return cls(path or Path("<memory>"), rows=rows)


def fold_diacritics(word: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", word)
        if not unicodedata.combining(ch)
    )


def load_words(
    paths: list[Path], min_score: int, ignore_diacritics: bool
) -> dict[int, np.ndarray]:
    """Union of the given `word;score` lists as per-length codepoint matrices.

    Tiers are irrelevant to kappa, so preferred and standard lists are merged.
    Folding is applied before deduplication, matching `--ignore-diacritics`.
    """
    best: dict[str, int] = {}
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                word, _, raw = line.strip().partition(";")
                if not word:
                    continue
                try:
                    score = int(raw)
                except ValueError:
                    continue
                if score < min_score:
                    continue
                if ignore_diacritics:
                    word = fold_diacritics(word)
                if score > best.get(word, -1):
                    best[word] = score
    by_length: dict[int, list[str]] = defaultdict(list)
    for word in best:
        by_length[len(word)].append(word)
    return {
        length: np.array([[ord(ch) for ch in w] for w in words], dtype=np.int32)
        for length, words in by_length.items()
        if length > 1
    }


# --------------------------------------------------------------------------
# arc consistency and kappa
# --------------------------------------------------------------------------


def arc_consistency(
    grid: Grid, words: dict[int, np.ndarray]
) -> list[np.ndarray] | None:
    """Crossing arc consistency to a fixed point. None if a domain empties.

    This is weaker than Ingrid's own initial consistency phase, which also
    propagates dupe and shared-substring eliminations from singleton slots.
    """
    domains: list[np.ndarray] = []
    for slot in grid.slots:
        matrix = words.get(len(slot))
        if matrix is None or len(matrix) == 0:
            return None
        live = np.ones(len(matrix), dtype=bool)
        for index, cell in enumerate(slot):
            if cell in grid.fixed:
                live &= matrix[:, index] == ord(grid.fixed[cell])
        if not live.any():
            return None
        domains.append(live)

    offsets = {
        (a, b, cell): (grid.slots[a].index(cell), grid.slots[b].index(cell))
        for a, b, cell in grid.crossings
    }
    changed = True
    while changed:
        changed = False
        for a, b, cell in grid.crossings:
            ia, ib = offsets[(a, b, cell)]
            wa, wb = words[len(grid.slots[a])], words[len(grid.slots[b])]
            shared = np.intersect1d(
                np.unique(wa[domains[a], ia]),
                np.unique(wb[domains[b], ib]),
                assume_unique=True,
            )
            for slot_id, matrix, offset in ((a, wa, ia), (b, wb, ib)):
                reduced = domains[slot_id] & np.isin(matrix[:, offset], shared)
                if int(reduced.sum()) != int(domains[slot_id].sum()):
                    domains[slot_id] = reduced
                    changed = True
                if not domains[slot_id].any():
                    return None
    return domains


def measure(grid: Grid, words: dict[int, np.ndarray]) -> dict[str, float] | None:
    """kappa, annealed bits and domain statistics. None if proven unfillable."""
    domains = arc_consistency(grid, words)
    if domains is None:
        return None
    sizes = np.array([int(d.sum()) for d in domains], dtype=np.int64)
    domain_bits = float(np.log2(sizes).sum())

    crossing_bits = 0.0
    for a, b, cell in grid.crossings:
        ia = grid.slots[a].index(cell)
        ib = grid.slots[b].index(cell)
        wa, wb = words[len(grid.slots[a])], words[len(grid.slots[b])]
        ca = np.bincount(wa[domains[a], ia])
        cb = np.bincount(wb[domains[b], ib])
        width = max(ca.size, cb.size)
        fa = np.pad(ca, (0, width - ca.size)) / ca.sum()
        fb = np.pad(cb, (0, width - cb.size)) / cb.sum()
        crossing_bits += math.log2(float((fa * fb).sum()))

    annealed = domain_bits + crossing_bits
    return {
        "slots": len(grid.slots),
        "crossings": len(grid.crossings),
        "min_domain": int(sizes.min()),
        "median_domain": int(np.median(sizes)),
        "geomean_domain": float(np.exp(np.log(sizes).mean())),
        "domain_bits": domain_bits,
        "annealed_bits": annealed,
        "kappa": 1.0 - annealed / domain_bits,
    }


def verdict(kappa: float, kappa_star: float) -> str:
    if kappa <= kappa_star:
        return "fillable within budget"
    if kappa <= OBSERVED_FILL_CEILING:
        return "marginal: fills exist here but cost minutes"
    return "no fill observed above 1.00 in calibration; do not search"


# --------------------------------------------------------------------------
# subcommand: kappa
# --------------------------------------------------------------------------


def run_kappa(args: argparse.Namespace) -> int:
    grid = Grid(args.grid)
    words = load_words(args.wordlist, args.min_score, args.ignore_diacritics)
    stats = measure(grid, words)

    print(f"grid:                     {grid.path}")
    print(f"slots / crossings:        {len(grid.slots)} / {len(grid.crossings)}")
    print(f"slot lengths:             {grid.lengths()}")
    print(f"eligible words:           {sum(len(m) for m in words.values())}")
    if stats is None:
        print("empty domain after crossing AC: PROVEN UNFILLABLE")
        return 1

    print(
        "min / median / geomean domain: "
        f"{stats['min_domain']} / {stats['median_domain']} / "
        f"{stats['geomean_domain']:.0f}"
    )
    kappa = stats["kappa"]
    print(f"kappa:                    {kappa:.3f}      [cliff {args.kappa_star}]")
    print(f"fill margin:              {args.kappa_star - kappa:+.3f}")
    print(f"verdict:                  {verdict(kappa, args.kappa_star)}")
    print(
        f"log2 <Z> (annealed):      {stats['annealed_bits']:+.1f}"
        "     [first moment, not a fill count]"
    )

    for spec in args.compare:
        path, _, raw = spec.rpartition(":")
        other = load_words([Path(path)], int(raw), args.ignore_diacritics)
        other_stats = measure(grid, other)
        label = f"{Path(path).name} @{raw}"
        if other_stats is None:
            print(f"kappa-cost vs {label}: baseline is unfillable")
            continue
        print(f"kappa-cost vs {label}: {kappa - other_stats['kappa']:+.3f}")
    return 0


# --------------------------------------------------------------------------
# subcommand: sweep
# --------------------------------------------------------------------------


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle, delimiter="\t")
            if row and not next(iter(row.values()), "").startswith("#")
        ]
    return rows


def solve(
    binary: Path, grid: Path, dict_path: Path, min_score: int, extra: str, timeout: int
) -> tuple[str, str]:
    """Return (outcome, seconds). Outcome is FILL, NOT_FOUND or PROVEN."""
    command = [
        str(binary),
        "--wordlist",
        str(dict_path),
        "--min-score",
        str(min_score),
        "--max-shared-substring",
        "5",
        "--timeout",
        str(timeout),
        "--cores",
        "1",
        "-t",
    ]
    command += extra.split()
    command.append(str(grid))
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "NOT_FOUND", ""
    output = f"{done.stdout}\n{done.stderr}"
    if "finding fill" in output:
        marker = output.split("finding fill")[0].rsplit(",", 1)[-1].strip()
        return "FILL", marker
    if "nfillable" in output:
        return "PROVEN", ""
    return "NOT_FOUND", ""


def run_sweep(args: argparse.Namespace) -> int:
    shapes = [(row["label"], Path(row["path"])) for row in read_tsv(args.shapes)]
    policies = read_tsv(args.manifest)
    grids = {label: Grid(path) for label, path in shapes}
    print(f"{len(shapes)} shapes x {len(policies)} policies = "
          f"{len(shapes) * len(policies)} points, timeout {args.timeout}s")

    records: list[dict[str, object]] = []
    for policy in policies:
        dict_paths = [Path(p) for p in policy["dicts"].split(",")]
        min_score = int(policy.get("min_score") or 0)
        extra = policy.get("extra", "") or ""
        words = load_words(dict_paths, min_score, "--ignore-diacritics" in extra)
        eligible = sum(len(m) for m in words.values())

        pending = []
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for label, path in shapes:
                pending.append(
                    (
                        label,
                        pool.submit(
                            solve,
                            args.binary,
                            path,
                            dict_paths[0],
                            min_score,
                            extra,
                            args.timeout,
                        ),
                    )
                )
            outcomes = {label: future.result() for label, future in pending}

        for label, _ in shapes:
            stats = measure(grids[label], words)
            outcome, seconds = outcomes[label]
            records.append(
                {
                    "shape": label,
                    "policy": policy["label"],
                    "knob": policy["knob"],
                    "words": eligible,
                    "kappa": "" if stats is None else round(stats["kappa"], 6),
                    "annealed_bits": ""
                    if stats is None
                    else round(stats["annealed_bits"], 3),
                    "min_domain": 0 if stats is None else stats["min_domain"],
                    "geomean_domain": ""
                    if stats is None
                    else round(stats["geomean_domain"], 1),
                    "outcome": "PROVEN" if stats is None else outcome,
                    "seconds": seconds,
                    "fill": outcome == "FILL",
                    "timeout_s": args.timeout,
                }
            )
            print(
                f"  {label:<22}{policy['label']:<32}"
                f"kappa={records[-1]['kappa']!s:<9}{records[-1]['outcome']}"
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"\nwrote {len(records)} rows to {args.output}")
    report_threshold(records, args.kappa_star)
    return 0


def report_threshold(records: list[dict[str, object]], kappa_star: float) -> None:
    """Re-derive the best separating threshold, the point of the whole sweep."""
    scored = [r for r in records if r["kappa"] != ""]
    if not scored:
        return
    fills = [float(r["kappa"]) for r in scored if r["fill"]]
    fails = [float(r["kappa"]) for r in scored if not r["fill"]]
    if not fills or not fails:
        return
    candidates = sorted({round(k, 3) for k in fills + fails})
    best = min(
        candidates,
        key=lambda t: sum(k > t for k in fills) + sum(k <= t for k in fails),
    )
    errors = sum(k > best for k in fills) + sum(k <= best for k in fails)
    print(
        f"best separating kappa* = {best:.3f}  "
        f"({errors}/{len(scored)} misclassified; current default {kappa_star})"
    )
    print(f"highest kappa that filled: {max(fills):.3f}")
    print(f"lowest  kappa that failed: {min(fails):.3f}")
    print(f"median fill kappa: {statistics.median(fills):.3f}")


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--kappa-star",
        type=float,
        default=DEFAULT_KAPPA_STAR,
        help=f"critical constrainedness (default: {DEFAULT_KAPPA_STAR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("kappa", help="score one configuration")
    score.add_argument("grid", type=Path)
    score.add_argument(
        "--wordlist",
        type=Path,
        action="append",
        required=True,
        help="`word;score` list; repeat to union preferred and standard tiers",
    )
    score.add_argument("--min-score", type=int, default=0)
    score.add_argument("--ignore-diacritics", action="store_true")
    score.add_argument(
        "--compare",
        action="append",
        default=[],
        metavar="DICT:MIN_SCORE",
        help="report kappa-cost against this baseline; repeatable",
    )
    score.set_defaults(func=run_kappa)

    sweep = sub.add_parser("sweep", help="re-measure the calibration")
    sweep.add_argument("--manifest", type=Path, default=Path("calibration/policies.tsv"))
    sweep.add_argument("--shapes", type=Path, default=Path("calibration/shapes.tsv"))
    sweep.add_argument("--binary", type=Path, default=Path("target/release/ingrid_core"))
    sweep.add_argument("--timeout", type=int, default=180, help="per-run seconds")
    sweep.add_argument("--jobs", type=int, default=8)
    sweep.add_argument(
        "--output", type=Path, default=Path("calibration/kappa_measurements.csv")
    )
    sweep.set_defaults(func=run_sweep)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
