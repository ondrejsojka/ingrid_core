# Investigation: CSR support index for AC revision (support_index)

## Summary

Built a static per-(slot, cell, glyph) -> option-ids CSR index (built once per config
behind `OnceLock`, shared across threads) and replaced the AC revision loop's full
crossing-domain rescan with visits to exactly the dead-glyph buckets: 33.9% faster
time-to-6-preferred.

**Baseline:** 1164 ms median (primary, target:6)
**After:** 745 ms median
**Improvement:** 33.9% median paired ratio 0.661; 36.0% by raw medians; single-core
diagnostic isolating per-decision cost: also 10/10 wins (-28%); initial AC alone ~4x
(31 ms vs 145 ms)
**Statistics:** paired Wilcoxon p_faster = 0.00098 (10/10 wins)
**Regression:** none on secondaries; tests 100/100; clippy parity (20 vs 21 pre-existing)

## Problem

Round-3 HEAD flat profile: `maintain_arc_consistency` 66.2% self (with
`establish_arc_consistency` inlined). Inside the propagation loop, each queued cell of a
popped slot triggered a rescan of the CROSSING slot's ENTIRE option list (5k-28k entries):
skip already-eliminated, read the option's glyph at the crossing cell, eliminate iff the
popped slot's support count for that glyph is zero. Only options carrying a glyph that
just died can be affected — everything else is a guaranteed no-op scan.

## Solution

- `src/grid_config.rs`: `SupportIndex` — per slot, per crossing cell: CSR buckets of
  option ids grouped by the glyph the option carries in that cell. Built lazily once via
  `OnceLock` on the first AC pass for a grid and shared by all workers thereafter.
- `src/arc_consistency.rs`: the popped slot's own glyph counts identify the dead glyphs
  per queued cell; revision visits exactly `words_for_glyph(dead_g)` buckets instead of
  the full domain.
- AC closure equality verified by the worker (per-slot eliminated sets match baseline);
  oracle verdicts unchanged; same tests green.

### Key design decisions

1. **Static index over dynamic queue entries.** Rather than threading dead-glyph info
   through the propagation queue, the popped slot's counts identify dead glyphs at
   revision time and the static CSR answers "who carries glyph g at cell c" — the
   simplest structure that makes revision proportional to the affected set.
2. **`OnceLock` at config level.** The index is expensive to build and immutable
   afterwards; grid configs are per-template and shared by all workers (and the oracle),
   so lazy-once shared build beats per-worker or eager construction.

## Files changed

- `src/grid_config.rs` — `SupportIndex`, `CellSupport`, `GridConfig` field + builder hook.
- `src/arc_consistency.rs` — revision loop switches to bucket iteration.
- `src/backtracking_search.rs` — minor call-site adjustments.

## Why 33.9% and not 66%

The full-domain rescan was only part of the inlined-establish body; `eliminate_word`'s
per-elimination bookkeeping (16.0% self) and undo paths (6.5%) are untouched, as is the
singleton/dupe phase (this round's sibling candidate).

## Remaining opportunities

- `eliminate_word` per-elimination bookkeeping is now the largest single symbol (16%).
- SIPhash on the singleton/dupe path: dupe_prop_borrowed removed 9.7% -> 0.04% of self
  time but measured NEUTRAL end-to-end (see its report) — do not retry without a workload
  where the singleton phase dominates.
- Worker patch provenance: `opt-support_index.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
