# Investigation: Borrowed/flattened glyph counts (glyph_count_refs) — DISCARDED

## Summary

Flattened `GlyphCountsByCell` (`Vec<Vec<u32>>` -> single cell-major `Vec<u32>`) and made
the AC pass borrow slot counts with copy-on-write materialization. Semantics proven
identical; performance neutral. **DISCARD.**

**Primary (paired, N=10):** wilcoxon p_faster = 0.4609 (5 wins / 5 losses), median paired
ratio 1.004 — no effect. A 12-round single-seed follow-up (median ratio 0.908,
p_faster = 0.285) hinted at a small within-seed gain that never reached significance.
**Secondaries:** no regression. **Tests:** 100/100 pass.

## Problem (hypothesis going in)

`ArcConsistencyAdapter::get_glyph_counts` cloned `Vec<Vec<u32>>` (~9 heap allocations,
~1-1.5 KB) per slot per AC pass, inside the dominant
`maintain_arc_consistency` self-time block of the round-0 profile (63.6%).

## Solution (implemented and verified, then measured neutral)

- `src/util.rs`: counts type flattened to one cell-major `Vec<u32>` (one allocation,
  memcpy clone).
- `src/arc_consistency.rs`: trait now returns `&GlyphCountsByCell`; pass slot-state
  borrows and clones on first mutation (copy-on-write).
- Equivalence proven: byte-identical `--cores 1` search logs on 3 seeds (terminal
  wall-clock-racy timeout/abort label flips within the same binary in both directions —
  not a semantic difference).

## Why it did not pay

The round-0 63.6% self-time attribution covered ALL of the inlined
`establish_arc_consistency` body, and the queue-management cost it mostly represented was
already removed in round 1 (`ac_queue_priority`). Against the round-3 baseline, the glyph
clone is simply below the noise floor on the primary workload — the fetch happens once
per slot per pass, slots-per-pass is small, and the eliminated cost was ~1 KB memcpy
against tens of thousands of domain operations.

## Remaining opportunities

- None on this path. Do not retry glyph-count storage without a fresh profile showing it
  above ~5% self time.
- Patch retained for the record: `opt-glyph_count_refs.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
