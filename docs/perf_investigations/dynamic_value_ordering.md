# Investigation: Dynamic value ordering by live crossing support (dynamic_value_ordering)

## Summary

Word-candidate selection now takes the first 12 live candidates in static rank order and
re-ranks them by live crossing support, Preferred tier pinned ahead of Standard: 54%
faster median time-to-8-preferred, and the seeds that previously hit the 120 s timeout
(3 of 10) all finish inside it.

**Baseline:** 50282 ms median (22830, 6900, 120769*, 60167, 120772*, 94477, 18953, 120714*, 40397, 4528) (*censored at timeout)
**After:** 17482 ms median (19401, 14851, 21035, 15562, 7515, 21557, 12653, 5116, 27001, 29030 — zero censored)
**Improvement:** 54.0% median paired ratio 0.463
**Statistics:** paired Wilcoxon p_faster = 0.0244 (8/10 wins), p_slower = 0.9814
**Regression:** none — s1_american neutral (7/3, p_slower = 0.862), s2_fast_wall neutral (ratio 1.000)

## Provenance

Worker `opt-dynamic_value_ordering` died to a provider brownout after implementation and
test work but before benchmarking. Recovered by replaying its transcript edits
(`opt-dynamic_value_ordering.jsonl`): dropped superseded/duplicated insertions (three
copies of the helper), repaired one duplicated declaration, applied its `.log1p() ->
.ln_1p()` rename, rebuilt, 100/100 tests. Orchestrator ran the full measurement inline.
Provenance patch: `/tmp/krizovky_bench/salvage_B.patch`.

## Problem

Candidate value ordering was fully STATIC: `sort_slot_options` ranks each slot's options
once at config build (tier, then fill-score mix), and the fill loop samples the first 3
live with weights [4,2,1]. Static order cannot see which candidates are actually
constrained NOW, so at hard targets the search repeatedly commits to words whose letters
have already gone rare at crossings, and pays for the wipeouts.

## Solution

- Pool: first `DYNAMIC_ORDERING_POOL_SIZE = 12` live candidates in the static order
  (resume semantics unchanged: retry position bookkeeping uses the pool's first static
  index, so nothing is skipped).
- Score: `live_crossing_support` = Σ over the word's crossings of `ln_1p` of the live
  number of options carrying the word's glyph at the crossing cell (read from the
  crossing slots' live glyph counts — no new state). Uncrossed words get
  `f32::INFINITY`, i.e. sort as maximally supported under the descending ordering.
- Order: preferred tier first (u8 tiebreak), then by the support score, stable sort keeps
  the static ranking within ties; weighted [4,2,1] pick among the top 3.
- Space is never restricted, only the order of consideration.

### Key design decisions

1. **Flow-toward-support polarity.** The re-rank tries the HIGHEST live-support
   candidate first (well-supported words cause fewer wipeouts). The alternative
   fail-first polarity (rarest glyph first) was not evaluated — it is the obvious
   head-to-head follow-up, listed below.
2. **Pool of 12, not full-domain re-ranking.** Cheap per step (12 × ~8-15 crossing
   lookups) and preserves most of the static ranking's value; whole-domain re-rank cost
   would exceed the saving.
3. **Tests rewritten where the contract was trajectory.** The extra-dupe-pair test
   keeps its real contract (paired words cannot co-occur; fill differs from the
   unconstrained fill) with updated expected fills for the new trajectory, documented
   in the test.

## Files changed

- `src/backtracking_search.rs` — `live_crossing_support`, pool size const, rewritten
  candidate step; test expectation updates.

## Why 54%

Time-to-8 is dominated by hard seeds where static ordering walks into dead ends: the
three baseline timeouts (120 s) became 21 s / 7.5 s / 5.1 s fills. On already-easy seeds
the effect is small-to-neutral, which is the right shape — wins where it matters.

## Remaining opportunities

- Pool size and polarity sweeps (`ordering_pool_tuning`): {8, 12, 16, 24} × {support-first,
  fail-first} × {deterministic best, weighted pick} — a tuning matrix worth one worker.
- Feeder idea: crossing-slot support could also learn from the shared weight pool (if a
  future sharing candidate lands).
