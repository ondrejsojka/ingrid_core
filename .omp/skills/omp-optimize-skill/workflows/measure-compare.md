# Measure and Compare

Run A/B measurement comparing current working tree against a baseline. Use this to measure an optimization you've already implemented.

## Arguments

- `<ref>` (optional): Git ref for baseline (default: `HEAD~1`)

## Workflow

### Step 1: Discover Project

Complete project discovery (SKILL.md Step 0) if not already done. Identify:
- How to build a release/optimized binary
- How to run the primary benchmark (what command, what output format)
- What secondary benchmarks exist for regression checking

### Step 2: Determine Baseline

If a baseline binary already exists at `/tmp/<project>_baseline_*`, ask the user if they want to use it or build fresh.

To build baseline from a git ref:
```bash
current_branch=$(git branch --show-current)
git stash push -m "optimize-measure-stash" 2>/dev/null || true
git checkout <ref>
<BUILD_CMD>
cp <binary> /tmp/<project>_baseline_<binary-name>
git checkout "$current_branch"
git stash pop 2>/dev/null || true
```

### Step 3: Build Optimized

Build from current working tree:
```bash
<BUILD_CMD>
```

### Step 4: A/B Measurement

Perform interleaved A/B measurement following the protocol in `skill://omp-optimize-skill/references/measurement-protocol.md`.

For N=10 rounds:
1. Run baseline binary, capture timing
2. Run optimized binary, capture timing
3. Record both values

The specific benchmark invocation command depends on the project. The agent must:
- Discover how to invoke the benchmark binary with the right arguments
- Extract a timing value (wall-clock microseconds or milliseconds) from the output
- Store raw timings for Mann-Whitney U computation

### Step 5: Compute Mann-Whitney U

With the collected timing arrays (baseline[10], optimized[10]):

```
U = count of pairs (i,j) where optimized[i] < baseline[j]
Total pairs = 10 × 10 = 100
```

Critical values (one-tailed, n1=n2=10):
- U >= 73: p < 0.05 → KEEP
- U >= 78: p < 0.01
- U >= 83: p < 0.001
- U < 73: not significant → DISCARD

### Step 6: Regression Check

If primary workload shows KEEP, run the same A/B comparison on secondary workloads. A regression is U <= 27 on any secondary workload (significantly slower).

### Step 7: Report

Summarize results:
- Primary: verdict, U statistic, improvement percentage, all raw timings
- Secondary: verdict for each, any regressions
- Overall recommendation: KEEP or DISCARD

If KEEP, suggest committing with a descriptive message including the improvement percentage and workload name.
