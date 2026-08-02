#!/usr/bin/env python3
"""Interleaved A/B benchmark comparator for ingrid_core builds.

Runs two binaries over the same fill workload in interleaved rounds (B, O, B, O, ...),
extracts one timing per run, and reports a paired statistical comparison as JSON.

Because each round gives both binaries the SAME seed, rounds form natural pairs. With a
randomized solver, across-seed variance dwarfs most code effects, so the primary
statistic is the paired Wilcoxon signed-rank test (exact, one-sided) on the per-round
differences (baseline - candidate), plus an exact sign test. The classic cross-pair
Mann-Whitney U is reported for reference only: under heavy across-input heterogeneity it
is underpowered and will hide real improvements.

Decision guidance: improvement iff wilcoxon_p_faster <= 0.05; regression iff
wilcoxon_p_slower <= 0.05.

Two metrics are supported:

- ``target:K``: milliseconds (from search-log ``elapsed_ms``) until the scheduler first
  reports an incumbent with at least K preferred words. Runs that never reach K are
  censored at their wall time (usually the search timeout), which counts as "slow".
- ``wall``: end-to-end process wall time in milliseconds.

The workload is the CLI argument tail after ``--``. Two placeholders are substituted
per run: ``{SEED}`` (same for both binaries within a round, different across rounds) and
``{LOG}`` (per-run search-log path; the log is deleted before each run because the CLI
appends). Every run is killed after ``--cap-seconds`` to bound total measurement time.

Example:

    python3 scripts/bench_compare.py \
        --baseline /tmp/base/ingrid_core --candidate target/release/ingrid_core \
        --rounds 10 --metric target:6 --cap-seconds 150 -- \
        --preferred-wordlist /tmp/data/theme.dict --wordlist /tmp/data/std33.dict \
        --blocklist resources/blocklist_cs.txt --min-score 33 \
        --max-shared-substring 5 --dupe-exempt-preferred --cores 10 \
        --timeout 120 --seed {SEED} --search-log {LOG} /tmp/data/grid.txt
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_SEED = 1000


@dataclass
class RunResult:
    value_ms: float
    censored: bool
    returncode: int


def parse_time_to_target(log_path: Path, target: int) -> float | None:
    """First elapsed_ms at which the incumbent reached `target` preferred words."""
    try:
        with log_path.open() as handle:
            header = handle.readline().rstrip("\n").split(",")
            try:
                elapsed_col = header.index("elapsed_ms")
                incumbent_col = header.index("incumbent_preferred_words")
            except ValueError:
                return None
            best: float | None = None
            for line in handle:
                row = line.rstrip("\n").split(",")
                if len(row) <= max(elapsed_col, incumbent_col):
                    continue
                incumbent = row[incumbent_col]
                if not incumbent:
                    continue
                if int(incumbent) >= target:
                    elapsed = float(row[elapsed_col])
                    if best is None or elapsed < best:
                        best = elapsed
            return best
    except OSError:
        return None


def run_once(binary: Path, args: list[str], cap_seconds: float) -> RunResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [str(binary), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=cap_seconds,
            check=False,
        )
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        returncode = -1
    wall_ms = (time.monotonic() - start) * 1000.0
    return RunResult(value_ms=wall_ms, censored=False, returncode=returncode)


def execute_round(
    binary: Path,
    workload: list[str],
    seed: int,
    log_path: Path,
    metric: tuple[str, int | None],
    cap_seconds: float,
) -> RunResult:
    if log_path.exists():
        log_path.unlink()
    substituted = [
        arg.replace("{SEED}", str(seed)).replace("{LOG}", str(log_path))
        for arg in workload
    ]
    result = run_once(binary, substituted, cap_seconds)
    kind, target = metric
    if kind == "target":
        reached = parse_time_to_target(log_path, target)
        if reached is not None:
            return RunResult(value_ms=reached, censored=False, returncode=result.returncode)
        # Never reached the target: censor at wall time (a lower bound on "slow").
        return RunResult(value_ms=result.value_ms, censored=True, returncode=result.returncode)
    return result


def mann_whitney_u(candidate: list[float], baseline: list[float]) -> float:
    """Count of pairs where candidate < baseline (faster wins), ties count 0.5."""
    u = 0.0
    for cand in candidate:
        for base in baseline:
            if cand < base:
                u += 1.0
            elif cand == base:
                u += 0.5
    return u


def signed_ranks(diffs: list[float]) -> list[float]:
    """Absolute-value ranks with tie averaging (1-based), zero diffs already excluded."""
    order = sorted(range(len(diffs)), key=lambda i: abs(diffs[i]))
    ranks = [0.0] * len(diffs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(diffs[order[j + 1]]) == abs(diffs[order[i]]):
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def wilcoxon_exact_p(diffs: list[float]) -> tuple[float, float]:
    """Exact one-sided p-values for the paired Wilcoxon signed-rank test.

    diffs are (baseline - candidate), so positive means candidate was faster.
    Returns (p_faster, p_slower): tail probabilities of the observed W+ under the null,
    computed exactly by enumerating all 2**n sign assignments over the observed ranks.
    """
    active = [d for d in diffs if d != 0]
    n_pairs = len(active)
    if n_pairs == 0:
        return 1.0, 1.0
    if n_pairs > 24:
        raise ValueError("exact Wilcoxon enumeration capped at n=24 rounds")
    ranks = signed_ranks(active)
    w_plus = sum(r for r, d in zip(ranks, active) if d > 0)
    # Ranks may be half-integers under ties; scale by 2 for integer DP.
    weights = [int(round(2 * r)) for r in ranks]
    observed = int(round(2 * w_plus))
    total_rank2 = sum(weights)
    dist = {0: 1}
    for weight in weights:
        nxt = dict(dist)
        for subtotal, count in dist.items():
            nxt[subtotal + weight] = nxt.get(subtotal + weight, 0) + count
        dist = nxt
    states = 2**n_pairs
    p_ge = sum(c for s, c in dist.items() if s >= observed) / states
    p_le = sum(c for s, c in dist.items() if s <= observed) / states
    # p_ge = P(W+ >= observed): small when candidate is actually SLOWER (W+ small).
    # We want: p_faster = P(W+ >= observed) small => fast improvement claim.
    return p_ge, p_le


def sign_test_p_faster(wins: int, n_pairs: int) -> float:
    from math import comb

    if n_pairs == 0:
        return 1.0
    return sum(comb(n_pairs, k) for k in range(wins, n_pairs + 1)) / 2**n_pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--metric", default="wall", help="'wall' or 'target:K'")
    parser.add_argument("--cap-seconds", type=float, default=240.0)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/krizovky_bench/logs"))
    parser.add_argument("--tag", default="run", help="log file prefix inside --workdir")
    parser.add_argument("workload", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    workload = args.workload
    if workload and workload[0] == "--":
        workload = workload[1:]
    if not workload:
        parser.error("missing workload arguments after --")

    if args.metric == "wall":
        metric: tuple[str, int | None] = ("wall", None)
    elif args.metric.startswith("target:"):
        metric = ("target", int(args.metric.split(":", 1)[1]))
    else:
        parser.error(f"unknown metric {args.metric!r}")

    for binary in (args.baseline, args.candidate):
        if not binary.exists():
            parser.error(f"binary not found: {binary}")
    args.workdir.mkdir(parents=True, exist_ok=True)

    baseline_values: list[float] = []
    candidate_values: list[float] = []
    censored = {"baseline": 0, "candidate": 0}

    for round_index in range(args.rounds):
        seed = args.base_seed + round_index
        for side, binary, sink in (
            ("baseline", args.baseline, baseline_values),
            ("candidate", args.candidate, candidate_values),
        ):
            log_path = args.workdir / f"{args.tag}.round{round_index:02d}.{side}.csv"
            result = execute_round(binary, workload, seed, log_path, metric, args.cap_seconds)
            sink.append(result.value_ms)
            censored[side] += int(result.censored)
            print(
                f"round {round_index:2d} {side:9s} seed={seed} "
                f"value_ms={result.value_ms:9.1f} censored={result.censored} rc={result.returncode}",
                file=sys.stderr,
            )

    u = mann_whitney_u(candidate_values, baseline_values)
    baseline_median = statistics.median(baseline_values)
    candidate_median = statistics.median(candidate_values)
    ratio = candidate_median / baseline_median if baseline_median else float("nan")

    diffs = [b - c for b, c in zip(baseline_values, candidate_values)]
    paired_ratios = [c / b for b, c in zip(baseline_values, candidate_values) if b]
    active = [d for d in diffs if d != 0]
    wins = sum(1 for d in active if d > 0)
    p_faster, p_slower = wilcoxon_exact_p(diffs)
    sign_p = sign_test_p_faster(wins, len(active))
    if p_faster <= 0.05:
        paired_hint = "improvement"
    elif p_slower <= 0.05:
        paired_hint = "regression"
    else:
        paired_hint = "neutral"

    report = {
        "u": u,
        "n_pairs": args.rounds * args.rounds,
        "baseline_median_ms": baseline_median,
        "candidate_median_ms": candidate_median,
        "median_ratio": ratio,
        "improvement_pct": (1.0 - ratio) * 100.0,
        "paired": {
            "n_nonzero": len(active),
            "wins": wins,
            "losses": len(active) - wins,
            "sum_positive_diffs_ms": sum(d for d in active if d > 0),
            "wilcoxon_p_faster": p_faster,
            "wilcoxon_p_slower": p_slower,
            "sign_p_faster": sign_p,
            "median_paired_diff_ms": statistics.median(diffs) if diffs else float("nan"),
            "median_paired_ratio": statistics.median(paired_ratios) if paired_ratios else float("nan"),
            "verdict_hint": paired_hint,
        },
        "baseline_timings_ms": baseline_values,
        "candidate_timings_ms": candidate_values,
        "censored_runs": censored,
        "metric": args.metric,
        "rounds": args.rounds,
        "base_seed": args.base_seed,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
