# Investigation: Cross-worker crossing-weight sharing (cross_worker_weight_sharing) — DISCARDED

## Summary

Portfolio-style sharing of `dom/wdeg` crossing weights across the 10 parallel workers
via a per-`find_best_fill` pool of atomic f32 cells, max-merged in both directions at each
worker's restart boundary (first try stays private). Built cleanly, zero semantics risk
(weights are heuristic-only), tests 100/100. Measured neutral on time-to-8. **DISCARD.**

**Primary (paired, N=10, target:8):** wilcoxon p_faster = 0.385 (5 wins / 5 losses),
median paired ratio 0.951; raw medians 52.3 s -> 25.8 s but driven by baseline-side
censoring asymmetry (3 timeouts each arm, different seeds).

## Provenance (unusual; read before citing)

The `opt-cross_worker_weight_sharing` worker was killed by a model-provider brownout
after completing implementation and test review but before benchmarking (no result object,
no patch). The design was recovered from its transcript (`opt-cross_worker_weight_sharing
.jsonl`, agent session 2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b) by
replaying the edit payloads; one edit was intentionally dropped (superseded by the
worker's own later edits — an abandoned plan to thread sharing through
`maintain_arc_consistency` mid-run rather than at retry boundaries). Recovered state:
built clean, 100/100 tests. Bench protocol then run inline by the orchestrator.
Provenance patch: `/tmp/krizovky_bench/salvage_A.patch`.

## Why it did not pay

- Sharing cadence is per randomized restart (~500+ backtracks), so a wipeout lesson
  propagates to siblings infrequently relative to the intra-try learning rate; the
  frontier swarm (round 3) already compensates for unlucky trajectories by running many
  independent streams at the same target, which is a stronger form of redundancy than
  weight sharing for these budgets.
- `max`-merge is symmetric: it also amplifies noise from one worker's outlier wipeouts
  into all siblings' orderings.

## Remaining opportunities

- Intra-try sharing (through a lock-free snapshot read every N states, not only at
  restarts) is the only remaining variant worth trying, and only if a workload shows
  long unbroken grinds between restarts (the primary workload does not: restart cadence
  is high at hard targets). Low priority.
- Worker patch provenance noted above; design retained in the patch, retrievable.
