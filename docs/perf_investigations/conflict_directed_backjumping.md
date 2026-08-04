# Investigation: Conflict-directed backjumping (conflict_directed_backjumping) — DISCARDED

## Summary

Implemented certified conflict-directed backjumping: blame tags seed a candidate
conflict set, and every non-chronological jump is certified by cloning the state and
re-proving both values of the choice being undone under the surviving prefix. Sound by
construction — and neutral, because the certification costs what the jump saves.
**DISCARD.**

**Primary (paired, N=10, target:8):** wilcoxon p_faster = 0.385 (5/5 wins/losses),
median paired ratio 0.9947.

## The structural finding (the valuable part)

Sound CBJ was NOT available in this architecture, for reasons worth recording:

- `Slot::eliminations` u16 blame tags record which previous CHOICE propagated an
  elimination, but inside an AC wipeout the provisional elimination sets are only
  attributable to the MOST RECENT open choice — the tags give a conflict set that is
  provably too shallow. Used directly it degenerates to a chronological no-op.
- The only sound jump rule the worker found is: candidate jump + full certification
  (clone, unmake deeper choices with the exact undo machinery, replay
  maintain_arc_consistency for BOTH values of the being-undone choice; jump only if both
  still fail). That certification replay costs roughly one full backtrack's work per
  jump, which is what the jump was supposed to save.

## What was built and kept

- `src/backtracking_search.rs` (patch retained: `opt-conflict_directed_backjumping.patch`,
  agent session 2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
- Tests 100/100; soundness harness (no false HardFailure where baseline fills) passed.

## Remaining opportunities

- None on backjumping without a different conflict-tracking architecture (e.g.,
  implication-graph AC variants with per-elimination reason recording built in rather
  than reconstructed). That is a ground-up redesign; park it unless the metric regime
  changes to proof-bound workloads (time-to-proven-optimum), where CBJ-style space
  reduction actually pays.
