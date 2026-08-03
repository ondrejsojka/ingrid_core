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

### Round 4 candidates

- `glyph_count_refs` — `ArcConsistencyAdapter::get_glyph_counts` clones ~1KB
  (len × ~30 glyphs as `Vec<Vec<u32>>`, i.e. ~9 heap allocations) per lazily-fetched
  slot per AC pass; make it borrowed or restructure the counts type.
- `slot_loop_allocs` — per-iteration Vec allocations in the fill loop: `calculate_slot_weights`,
  `choose_next_slot`'s collected+sorted ids, and per-`maintain_arc_consistency`
  `remaining_option_counts` + `fixed_slots`. Hoist to reused buffers threaded from the
  caller.
- `time_to_proof_workload` — add a benchmark workload tracking time-to-PROVEN-optimal
  (`--timeout 0`) so proof-tail regressions are visible, not just time-to-target wins.

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

### Round 4 (2026-08-02) — both DISCARD (mechanically correct, measured neutral)

- `glyph_count_refs` — DISCARD. Flat Vec<u32> + copy-on-write borrow implemented and
  semantics PROVEN identical (3 seeds), but primary p_faster=0.46 — the clone is below
  the noise floor post-round-1. Report: docs/perf_investigations/glyph_count_refs.md.
- `slot_loop_allocs` — DISCARD. SearchScratch hoisting done, determinism byte-identical,
  p_faster~=0.5. glibc malloc arenas already make these allocations cheap at 10 workers.
  Report: docs/perf_investigations/slot_loop_allocs.md.
- Lesson: malloc/allocation-symbol-free profiles mean allocation hypotheses need a fresh
  allocation-specific profile first; following rounds went algorithmic.

### Round 5 (2026-08-02) — 1 KEEP, 1 DISCARD

- `support_index` — KEEP. CSR per-(slot,cell,glyph)->options; revision touches dead
  buckets only. Primary 10/10 wins (p=0.00098), -33.9% time-to-6; initial AC ~4x. Report:
  docs/perf_investigations/support_index.md.
- `dupe_prop_borrowed` — DISCARD. Mechanism verified (SipHash 9.7%->0.04% self) but
  aggregate neutral on time-to-6 (singleton phase fires too rarely on P). Do not retry
  without a dupe-heavy workload. Report: docs/perf_investigations/dupe_prop_borrowed.md.
- Metric update: K=8 calibration at HEAD reachable on both probed seeds (4.2s, 37.6s);
  **primary metric moves to target:8 from round 6** (time-to-6 compressed to ~1s floor).

### Round 6 candidates

- `cross_worker_weight_sharing` — portfolio-style sharing of dom/wdeg crossing-weight
  signals between parallel workers (lock-free atomic array, periodic merge/pull).
  Heuristic-only values => zero semantics risk; trajectories judged by bench only.
- `dynamic_value_ordering` — replace static top-3 candidate ranking with crossing-support
  -aware value ordering (fail-first on rare glyphs) in the word-candidate choice.

### Round 2/3 (2026-08-02) — 3 candidates, 3 KEEP (one via transcript salvage)

- `wordlist_build_parallel` — KEEP. s2_fast_wall median -37.6% (10/10 wins, p=0.00098);
  P neutral. Parallel chunk parse + ASCII normalize fast path; its FxHash swap was later
  isolated-measured NEGATIVE and removed (see NEGATIVE entry below).
  Report: docs/perf_investigations/wordlist_build_parallel.md. (Do NOT swap SipHash where
  the hash value is the identity — FxHash collides on short structured words; 3 real
  collisions found in the Czech corpus.)
- `bitset_domains` — KEEP via TRANSCRIPT SALVAGE (worker killed by provider outage after
  measuring primary p=0.00195 but before secondaries/patch). Recovered edits verbatim,
  orchestrator re-tested + re-measured: primary p=0.0322 (8/10), s1 p=0.00098 (10/10,
  -39%), s2 neutral, semantics bit-identical at cores 1. Report: docs/perf_investigations/bitset_domains.md.
- `scheduler_frontier_focus` — KEEP. Primary median -63% (p=0.0049, 8/10); s1 -59%, s2
  neutral. Baseline flaw exposed by search logs: 9/10 initial-spread probes at targets
  14..61 never terminated inside the 120s timeout — the climb ran on one core. All-but-one
  workers now swarm the frontier and retarget on every improvement. Report:
  docs/perf_investigations/scheduler_frontier_focus.md.
- Campaign verification (r0 baseline vs r3 HEAD): see local logs (campaign_r0_r3).
- **Cumulative verification** (r0 -> r3 HEAD, primary, 10 paired rounds): unpaired medians
  9664 -> 1262 ms (-86.9%), paired median ratio 0.224, 9/10 wins, Wilcoxon p=0.0029.
- **NEGATIVE (do not retry): hand-rolled FxHash-style hasher on short Czech keys.** Shipped
  inside wordlist_build_parallel; isolated ablation (serve load_ms, 10 paired rounds):
  1308 vs 960 ms for a std-RandomState revert — no gain, trend reversed. Plus 3 exact
  64-bit collisions in the corpus (6.9e-10 ideal expectation) from multiply-only mixing
  without finalization. Removed from main same-day; the wordlist win is the parallel
  parse + ASCII normalization fast path.
