# Investigation: Parallel dictionary load + normalization fast paths (wordlist_build_parallel)

## Summary

Parallelized dictionary parsing across line-boundary chunks (`std::thread::scope`, exact
file-order replay) and removed SipHash/unicode overhead from the load path: 37.6% faster
end-to-end wall on the fast workload, ~2x on the constant every CLI invocation pays.

**Baseline:** 1849.6 ms median (1676, 1468, 1822, 1320, 1873, 2381, 2277, 1826, 1972, 2375)
**After:** 1172.0 ms median (1628, 921, 1075, 1173, 976, 1067, 1319, 1170, 1477, 1472)
**Improvement:** 37.6% wall; 10/10 paired wins
**Statistics:** paired Wilcoxon p = 0.00098 (10/10), p_slower = 1.0; secondary P
(preferred climb, search-phase metric) neutral: p_slower = 0.348, 5/5 wins/losses
**Regression:** none

## Problem

Every CLI run spent ~4.3-4.5 s before the search started (dictionary load for 160k words
+ grid config build). On fast primary seeds the whole preferred climb is 0.5-1.9 s — the
process was load-dominated. Worker perf attribution of the pre-search phase: ~19% SipHash
write+glue, ~20% allocator churn, ~15% unicode normalization iterators, ~8% parse/split,
~5-6% config build/sort.

## Solution

- Parse in parallel: file split at `b'\n'` boundaries (byte search — arbitrary offsets can
  split multi-byte UTF-8 and panic on `str` slicing), chunks parsed on scoped threads,
  results merged in exact file order. Entries, word ids, error-cap semantics byte-identical.
- FxHash-style hasher for Eq-checked hot maps (`word_id_by_string`, `glyph_id_by_char`,
  parse index). SipHash KEPT for the cross-source dedupe set: there the 64-bit hash value
  itself is the identity, and FxHash collides on short structured Czech words (3 exact
  collisions found in 160k: převezen/převezme, šéfovat/šéfovia, hudobníci/hudobnými) —
  silently dropping words. Eq-checked maps resolve collisions; identity sets cannot.
- ASCII fast path in `normalize_word`; match-based `letter_points`; presized maps.

### Key design decisions

1. **Determinism as a hard gate.** Parallel parse replays in exact sequential order so
   word ids (which feed search decisions and same-seed comparability) are unchanged.
   Verified by equivalence probes, not just the test suite.
2. **No new dependencies** — `std::thread::scope` + channels, matching the repo's
   concurrency model (no rayon).

## Files changed

- `src/word_list.rs` — chunked parallel parse, hasher swaps, ASCII fast path, presizing.
- `src/bin.rs` — 3 trivial clone->deref adjustments (NormalizationSettings became Copy).

## Why 37.6% and not more

Remaining wall is the sequential merge (`add_word_silent`: glyph encode + string clones +
hashmap inserts, ~450 ms) and grid config build (untouched here). Follow-up: two-phase
parallel glyph discovery with deterministic id replay, est. another 100-150 ms.

## Remaining opportunities

- `sort_slot_options` ranking (~5-6% of pre-search) measurable separately.
- `--serve` oracle startup benefits identically (same WordList build path).
- Worker patch provenance: `opt-wordlist_build_parallel.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
