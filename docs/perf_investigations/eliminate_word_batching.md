# Investigation: eliminate_word batching + prefetch (eliminate_word_batching) — DISCARDED

## Summary

Restructured the support-index bucket loop with batched CSR-order iteration plus
software prefetch. First variant regressed reproducibly; final variant (prefetch only
the adapter's unconditionally-read elimination-state line) measured exactly neutral.
**DISCARD.**

**Primary (paired, N=10, target:8):** wilcoxon p_faster = 0.42 (4 wins / 6 losses),
median paired ratio 1.038. Tests 100/100; same-seed cores-1 event streams byte-identical.

## What was measured (three variants; the whole micro-path is now closed)

- V1 (prefetch next bucket word's glyph row + local elimination flags): reproducible
  regression in two full primary runs (8/10 losses, p_slower 0.042 first run). The
  prefetch traffic fired on mostly-SKIPPED words (already-eliminated options) and
  displaced useful cache lines. Bisection pinned wasted prefetch bandwidth as the cause.
- V3 (prefetch only the adapter's elimination-state line, which every iteration reads,
  via a new `ArcConsistencyAdapter.prefetch_word_state` hook): neutral.
- Conclusion: `eliminate_word`'s self time is NOT memory-starved on the read side; the
  glyph-count decrement traffic is already well-behaved, and the per-word closure
  bookkeeping is too cheap to batch profitably.

## Remaining opportunities

- Do not retry glyph-row prefetch or bucket batching on this path.
- Patch retained: `opt-eliminate_word_batching.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
