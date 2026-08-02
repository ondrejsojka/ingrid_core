# Investigation: AC-3 queue management restructure (ac_queue_priority)

## Summary

Replaced `establish_arc_consistency`'s O(#slots)-per-step full-grid queue rescan with a
compact queued-slot list, constant-time cell-enqueue dedup, and dense `Vec<f32>` wipeout
blame (eliminating SipHash), cutting median time-to-6-preferred by ~27% and the secondary
American-grid workload by ~25%, order-preserving by construction.

**Baseline:** 14059.5 ms median (primary, N=10 rounds)
**After:** 10294.5 ms median
**Improvement:** 26.8% median (primary); secondary s1_american ratio 0.754; s2_fast_wall ratio 0.998 (neutral)
**Statistics:** paired Wilcoxon signed-rank p = 0.0137 (primary, 8/10 wins); secondaries not significant in either direction (s1 U=52, s2 U=56 cross-pair)
**Regression:** none (tests pass; propagation order provably identical)

## Problem

Baseline flat profile (perf record, primary workload, 90 s, 10 cores):
`maintain_arc_consistency` = 63.62% self time — the generic `establish_arc_consistency`
inlines into it; plus ~7% SipHash (`hash_one` 4.77% + siphash write 2.18%).

Three structural costs inside the AC-3 propagation loop dominated:

1. Next-slot selection ran `(0..#slots).filter(queued).min_by_key(dom/wdeg)` — a ~61-72
   slot rescan for EVERY cell-propagation step, thousands of times per search state.
2. Cell-enqueue dedup used `Vec::contains`, a linear scan per enqueue.
3. Every domain wipeout built `weight_updates: HashMap<CrossingId, f32>`, and the ageing
   pass re-read it with a hash lookup per crossing.

## Solution

- `queued_slot_ids: Vec<SlotId>` — a compact list of slots that actually have queued
  cells. Selection scans it with live priorities; the scan is O(#queued) instead of
  O(#slots), and `#queued` is small late in passes. Per-slot `queued_list_pos` gives O(1)
  swap-remove on pop.
- `queued_cell_mask: u32` per slot — cell dedup is one AND/OR instead of a linear scan
  (slot length <= 21 < 32, asserted at construction).
- `weight_updates` becomes a dense `Vec<f32>` indexed by `CrossingId`; zero entries mean
  "no blame". The consumer in `maintain_arc_consistency` drops its per-crossing hash.

### Key design decisions

1. **Ordered-scan over a heap.** A lazy BinaryHeap variant was implemented and measured
   FIRST: it regressed (cross-pair U=45) because enqueues vastly outnumber pops in the hot
   maintain passes, so per-enqueue heap pushes cost more than the per-pop full scan they
   replaced. The compact-list scan does strictly less work than baseline on every path.
   (Retained as data: do not retry a push-per-enqueue heap.)
2. **Exact propagation-order equivalence.** Selection uses `(priority, slot_id)`
   lexicographic minimum over live priorities, matching `min_by_key` ascending-id
   semantics ties and all. Verified: `--cores 1` search-log event streams are
   byte-identical to baseline; AC closure, first-wipeout bail, and blame VALUES unchanged
   (representation only). Oracle verdicts identical.
3. **Dense blame over sorted-pairs.** Wipeouts allocate one zeroed `Vec<f32>`
   (~#crossings * 4B); far cheaper than SipHash map build + probe, and the `0.0`-means-
   absent convention keeps the consumer branch-free.

## Files changed

- `src/arc_consistency.rs` — queue list + positions, cell bitmask, dense weight_updates,
  construction assert.
- `src/backtracking_search.rs` — ageing-pass consumer indexing.

## Why 27% instead of 63.6%

The 63.6% self-time figure charges ALL of the inlined establish body to the caller; queue
management is one (large) share. Glyph-count cloning in the adapter, the singleton/dupe
passes, and the fill loop's per-iteration Vec allocations are untouched — those are the
`glyph_count_refs` and `slot_loop_allocs` backlog items.

## Remaining opportunities

- `ArcConsistencyAdapter::get_glyph_counts` still clones ~1 KB per lazily-fetched slot
  per pass; borrowing would remove the last big per-revision allocation.
- Per-iteration `slot_weights` / `sorted_slot_ids` / `remaining_option_counts` /
  `fixed_slots` Vec allocations in the fill loop.
- Worker patch provenance: `opt-ac_queue_priority.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
- Lint note: `cargo clippy --all-targets -- -D warnings` fails identically on baseline
  HEAD under rust 1.96 — pre-existing, not introduced here; kept as merge gate "lint
  parity with baseline".
