# Investigation: Incremental preferred-word cardinality bound (preferred_support_cache)

## Summary

Replaced the O(total slot options) rescan in `can_satisfy_minimum_preferred_words` with an
incrementally maintained per-slot counter, cutting median time-to-6-preferred on the
primary 15x15 švédská workload by ~30%.

**Baseline:** 13376.0 ms median (batch: 44577, 1922, 13686, 35724, 14482, 92852, 1684, 26395, 3804, 1937 — first-measurement batch; replication batch values below)
**After:** 9369.0 ms median (27654, 1591, 10629, 12064, 11129, 42710, 1380, 16479, 2692, 536)
**Improvement:** ~30% median (primary batch), ~23% (independent replication), combined ratio 0.700
**Statistics:** paired Wilcoxon signed-rank p = 0.0098 (batch 1), p = 0.0010 (replication), p = 0.00001 (combined, N=20); sign test 18/20 wins; cross-pair U = 60-64/100 (underpowered, see protocol note)
**Regression:** none measured (semantics byte-identical; secondaries not run pre-merge — debt retired by round-1 verification of the combined merge)

## Problem

`can_satisfy_minimum_preferred_words` (flat profile: 14.83% self time on the primary
workload, perf record 90 s, 10 cores) was called after *every* successful
`maintain_arc_consistency` — i.e. on every surviving choice and elimination in the
backtracking search. With `minimum_preferred_words > 0` it rescanned
`config.slot_options[..]` of every unfixed slot for a live Preferred-tier word. Preferred
slots are rare (79 forms over a 160k-word standard tier), so nearly every call walked most
slots' entire option lists: thousands of entries per slot, ~61-72 slots, per search state.

Measured effect exceeds the flat-profile prediction because the scans are
memory-bandwidth-bound: two full walks of large option arrays per process, on a 10-core
machine where every core does it simultaneously.

## Solution

- `Slot` gains `preferred_remaining: usize` (live preferred options) and
  `preferred_by_word: Vec<bool>` (flat per-length tier lookup, built once at slot
  construction in `live_state::build_slots`).
- `add_elimination` / `remove_elimination` — the single mutation point for live options —
  adjust the counter with `usize::from(preferred_by_word[word_id])`.
- The global bound check becomes: fixed slot -> `preferred_by_word[fixed_word]`, unfixed ->
  `preferred_remaining > 0`; early exit at K slots. `config` parameter dropped;
  call sites in `maintain_arc_consistency`, `live_state::can_satisfy_target`, and
  `variant_estimate::exact_root_count` updated.

### Key design decisions

1. **Counter + flat flags, not a preferred-options list per slot.** The tier lookup had to
   be O(1) inside the hottest mutation path; a `Vec<bool>` over the whole per-length
   domain costs one byte per word and keeps `word_tier` out of the loop entirely. A
   per-slot preferred-only list would shrink memory but reintroduce a second indirection
   on every elimination.
2. **Maintain the invariant in the mutation primitives, not at call sites.** All live-set
   changes (provisional AC application, undo, backtrack blame-clearing) funnel through
   `add_elimination`/`remove_elimination`; updating there cannot miss a path.
3. **No behavior change, by construction and by measurement.** Same-seed search logs were
   byte-identical to baseline in every non-timing column, and the incumbent event sequence
   was identical; the win is pure cost removal.

## Files changed

- `src/backtracking_search.rs` — Slot fields + mutation hooks; rewritten bound check; call-site updates.
- `src/live_state.rs` — counter/flag initialization in `build_slots`; `can_satisfy_target` signature.
- `src/variant_estimate/mod.rs` — `exact_root_count` call-site update.

## Why 30% instead of 14.8%

The flat profile attributes self time, but the rescans' real cost is memory bandwidth they
steal from ALL cores concurrently. Removing them both deletes the self time and relieves
contention, so the realized gain (~30% median) is about twice the naive attribution.

## Remaining opportunities

- The check is now provably negligible; no further work on this path.
- Fast seeds (~0.5-1.9 s total) are dominated by wordlist load + initial AC — the
  `wordlist_build_parallel` candidate in ../../PERFORMANCE_INVESTIGATIONS.md.
- Worker patch provenance: `opt-preferred_support_cache.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
