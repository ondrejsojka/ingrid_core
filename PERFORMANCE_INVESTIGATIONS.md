# Performance Investigations

Goal (user brief, 2026-08-02): make the parallel fill search (`parallel_search` /
`backtracking_search` / `arc_consistency`) reach a fill with as many preferred-tier words
as possible, as fast as possible. 10-core machine. Low-hanging fruit elsewhere is also in
scope.

## Benchmark protocol

Comparator: `scripts/bench_compare.py` — interleaved rounds (B, O, B, O, ...), Mann-Whitney
U over N=10 per arm, same seed for both arms within a round (base seed 1000 + round index).
KEEP: U >= 73 + all tests pass + no secondary regression (U > 27 on every secondary).
Data staged at `/tmp/krizovky_bench/data/`; baseline binary `/tmp/krizovky_bench/ingrid_baseline`.

**Primary (P)** — "preferred climb", Karolína corpus:
`theme.dict` (79 preferred forms) over `std33.dict` (160,428 standard), blocklist_cs,
`--min-score 33 --max-shared-substring 5 --dupe-exempt-preferred --cores 10 --timeout 120
--seed {SEED} --search-log {LOG}` on 15x15 švédská `s03.txt`.
Metric: `target:K` = search-log elapsed_ms until incumbent >= K preferred. Baseline
trajectory (seed 1000): 3 @ 1.0s, 4 @ 16.9s, 5 @ 23.4s, 6 @ 24.3s; timeout at 180s still 6.

**Secondary (S1)** — American-grid topology, LFŠ-style:
`lfs_preferred.dict` over `standard_clued_n33.dict` (129k), blocklist_cs,
`--min-score 30 --max-shared-substring 4 --cores 10 --timeout 120` on `g09_headroom48c.txt`.
Metric: `target:3`.

**Secondary (S2)** — fast end-to-end wall: 9x9 `gen_9x9.txt`, embedded spreadthewordlist,
defaults (`--min-score 50`), `--timeout 30`. Metric: `wall` (includes wordlist build).

## Baseline profile (2026-08-02, perf record, P workload, 90s, 10 cores)

| self % | symbol |
|---:|---|
| 63.6 | `backtracking_search::maintain_arc_consistency` (body; `establish_arc_consistency` inlines here) |
| 14.8 | `backtracking_search::can_satisfy_minimum_preferred_words` |
| 6.5 | `arc_consistency::establish_arc_consistency::{{closure}}` (eliminate_word) |
| 6.2 | `backtracking_search::undo_provisional` |
| 4.8 | `core::hash::BuildHasher::hash_one` (SipHash) |
| 2.2 | `siphash::Hasher::write` |

Reading of the profile + code:

1. `can_satisfy_minimum_preferred_words` rescans every unfixed slot's full option list after
   every successful AC propagation. Preferred-capable slots are rare -> near-worst-case scan
   every state. -> incremental per-slot live-preferred counter (candidate
   `preferred_support_cache`).
2. `establish_arc_consistency` picks the next queued slot with an O(#slots) `min_by_key`
   rescan per propagated cell, dedupes queued cells with `Vec::contains`, re-sorts cells per
   step. -> priority queue + generation stamps (candidate `ac_queue_priority`).
3. ~7% SipHash: `weight_updates: HashMap<CrossingId, f32>` built per wipeout + `.get()` per
   crossing in the weight-ageing pass. -> dense per-crossing Vec.
4. `Slot.eliminations` is a dense `Vec<Option<Option<SlotId>>>` over ALL words of the slot's
   length (~16B x 10-28k) -> MBs cloned per worker fork and per retry. Candidate for a
   bitset-domain rewrite (speculative, later round).
5. Per-iteration Vec allocations in the fill loop: `calculate_slot_weights`,
   `choose_next_slot`'s sorted ids, `remaining_option_counts`, `fixed_slots`. GLibC malloc is
   not itself in the >1% flat profile, so bundled into 2 rather than a standalone candidate.

## Candidate proposals (uninvestigated)

- `preferred_support_cache` (R1) — incremental live-preferred counting. Est. 10-14%.
- `ac_queue_priority` (R1) — heap-based AC queue + dense weight updates. Est. 15-30%.
- `bitset_domains` — slot domains as bitsets over `slot_options`; trail-based undo. Big
  rewrite, big fork/clone and elimination-apply win. Speculative.
- `scheduler_frontier_focus` — scheduler steers more cores onto the frontier target
  (incumbent+1) with diverse seeds instead of spreading over doomed/duplicate targets.
- `wordlist_build_parallel` — dictionary load is ~4-4.5s of every CLI run; parallelize /
  cheapen normalization + glyph encoding. Shows on S2 and real CLI use.
- `glyph_count_refs` — `ArcConsistencyAdapter::get_glyph_counts` clones ~1KB per call per
  revised slot; make it borrowed.

## Round log

### Round 1 (2026-08-02) — 2 candidates, BOTH KEEP after protocol correction

- `preferred_support_cache` — KEEP. Median -30% time-to-6 (replication -23%); paired
  Wilcoxon p=0.0098 / p=0.0010. Report: docs/perf_investigations/preferred_support_cache.md.
- `ac_queue_priority` — KEEP. Median -27% primary, -25% s1_american, s2 neutral; paired
  Wilcoxon p=0.0137. Report: docs/perf_investigations/ac_queue_priority.md.
  Sub-finding (do not retry): lazy BinaryHeap AC queue regressed (U=45) — enqueues
  outnumber pops, per-enqueue pushes cost more than the rescan they replace.
- Combined merge: src commit bed1f5e. Verification comparator run merged-vs-original-
  baseline recorded in local logs (verify_r1).

**PROTOCOL CORRECTION (important):** both workers returned DISCARD under the mandated
cross-pair U >= 73 despite large real effects, because same-seed pairing + cross-pair U
is a statistical mismatch — across-seed variance (0.5s-93s time-to-6) dominates cross
pairs. The comparator now computes paired Wilcoxon signed-rank (exact, 2^n enumeration)
and sign test; KEEP gate is p_faster <= 0.05, secondary regression gate p_slower <= 0.05.
The skill's measurement-protocol.md and optimizer-worker.md were updated generically.
Workers must use the comparator's `paired` block, not bare U, for verdicts from now on.

### Round 2 candidates

- `bitset_domains` — replace 16B/word dense eliminations with compact per-length-domain
  representation (bitset + u8 blame or equivalent): fork/clone collapse, cache locality
  for `is_word_eliminated` in AC. Must reproduce identical same-seed event streams.
- `wordlist_build_parallel` — dictionary load ~4.3s constant per CLI run: profile the
  load+config build and parallelize/cheapen (normalization, glyph encode, dupe index,
  sort_slot_options). Visible on s2_fast_wall and every real invocation.
