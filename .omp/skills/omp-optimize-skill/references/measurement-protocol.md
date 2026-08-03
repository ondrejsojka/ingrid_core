# Measurement Protocol: Mann-Whitney U Test

## Why Non-Parametric

Execution timings are not normally distributed. They exhibit:
- Right skew (occasional GC pauses, context switches)
- Discrete modes (cache hot vs cold)
- Heavy tails

The Mann-Whitney U test compares two independent samples without assumptions about their distribution. It tests whether observations from one group tend to be smaller than observations from the other.

## The Test

Given two groups of size n1 and n2:

**U = count of pairs (i, j) where optimized_i < baseline_j**

For n1 = n2 = 10: total pairs = 100.

- U = 100: optimized is ALWAYS faster (no overlap)
- U = 50: no tendency either way (null hypothesis)
- U = 0: optimized is ALWAYS slower

## Critical Values (n1 = n2 = 10, one-tailed)

| U threshold | p-value    | Interpretation           |
|-------------|------------|--------------------------|
| U >= 73     | p < 0.05   | Significant              |
| U >= 78     | p < 0.01   | Highly significant       |
| U >= 83     | p < 0.001  | Very highly significant  |
| U = 100     | p < 0.0001 | Complete separation      |
| U < 73      | n.s.       | Not significant          |

## Decision Rule

- **KEEP**: U >= 73 (p < 0.05) AND all tests pass AND no regression on secondary workloads
- **DISCARD**: U < 73 OR test failures OR regressions on secondary workloads

## Interleaved Execution

Measurements are interleaved (B, O, B, O, ...) rather than sequential (B×10, O×10) to control for:
- Thermal throttling (CPU heats up over time)
- Background load drift
- Memory pressure changes
- OS scheduler state changes

Each round: run baseline once, then optimized once. This ensures both versions experience the same environmental conditions within each round.

## Paired Designs (same-input rounds)

Some harnesses can give BOTH binaries the same input within a round — the same RNG seed,
the same request id, the same checkpoint file. When rounds are paired, per-round
differences (baseline_i - optimized_i) are far more informative than the raw samples,
because across-input variance (often larger than the code effect) cancels inside each
pair.

**If your design is paired, cross-pair U is the WRONG scorer.** U compares every optimized
run against every baseline run, so the across-input variance dominates and real 20-30%
median improvements score U ~ 55-60 (a guaranteed DISCARD under the U >= 73 rule).

Use the paired Wilcoxon signed-rank test instead:

1. diffs_i = baseline_i - optimized_i (positive = optimized faster); drop exact zeros.
2. Rank |diffs|, tie-averaged; W+ = sum of ranks of positive diffs.
3. Exact one-sided p from the full sign-assignment distribution (2^n enumerations; exact
   is trivial for n <= 20) — no normal approximation needed.
4. Decision: improvement iff p_faster <= 0.05 (one-sided); a regression on a secondary
   workload iff its p_slower <= 0.05.
5. Also report: wins/losses count, exact sign-test p, median paired ratio, and the raw
   per-round arrays. Keep reporting cross-pair U for continuity; it is just no longer
   the gate.

Use the paired rule ONLY when pairing is genuine (identical seed/input delivered to both
arms within each round). If arms see different inputs, stay on the unpaired U protocol.

## Tie Handling

When optimized_i == baseline_j (exact tie):
- Count 0.5 per tie (standard convention)
- U_adjusted = U_raw + ties × 0.5
- Round to nearest integer for threshold comparison

In practice, ties are rare with microsecond-resolution floating-point timings.

## Effect Size

The median ratio (median_optimized / median_baseline) gives the practical effect size:
- ratio = 0.90 means ~10% faster
- ratio = 0.85 means ~15% faster

Report both the U statistic (significance) and median ratio (effect size). A statistically significant result with tiny effect size (e.g., 0.5% faster) may not be worth the code complexity.

## Sample Size Choice: N = 10

With N = 10 per group (unpaired) or N = 10 rounds (paired):
- Minimum detectable effect at p < 0.05: roughly 20-30% improvement (depends on variance)
- For smaller effects, increase N (N = 20 gives finer discrimination)
- N = 10 balances measurement time against statistical power for most optimization workflows

## Regression Check

After the primary workload passes, run the same comparison on secondary workloads (diverse benchmark cases that exercise different code paths).

A statistically significant regression (U <= 27, i.e., optimized is significantly SLOWER) on any secondary workload is grounds for DISCARD even if the primary workload improved.

Non-significant differences on secondary workloads (27 < U < 73) are acceptable — the optimization is neutral on those workloads.

## How the Agent Performs Measurement

There is no external script. The agent performs measurement inline:

1. **Discover the benchmark command** from project context (provided by orchestrator)
2. **Run 10 interleaved rounds**: for each round, invoke baseline binary then optimized binary, extract timing from output
3. **Collect two arrays** of 10 timing values each
4. **Compute U**: iterate all 100 pairs, count wins + 0.5 × ties
5. **Compute medians and ratio**
6. **Apply decision rule**: U >= 73 → KEEP, else DISCARD
7. **Report all raw timings** (not just summary statistics)
