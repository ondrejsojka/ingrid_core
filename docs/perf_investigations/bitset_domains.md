# Investigation: u16-encoded elimination state (bitset_domains)

## Summary

Replaced `Slot::eliminations: Vec<Option<Option<SlotId>>>` (16 B per word of the slot's
entire per-length dictionary) with a 2-byte state array (0=live, 1=unblamed, slot_id+2=
blaming choice): 8x smaller live domains, collapsing worker-fork/retry clone volume and
backtrack scan width. ~17.5-25.7% faster on the primary preferred climb, 39.1% on the
American-grid secondary.

**Baseline (salvage measurement):** 5917 ms median (13871, 429, 2950, 8884, 9482, 39638, 1637, 13427, 1670, 1245)
**After:** 3516.5 ms median (18604, 334, 2676, 4357, 4500, 34548, 964, 12075, 1672, 512)
**Improvement:** median paired ratio 0.825 (-17.5%); sandbox pre-crash measurement 0.743 (-25.7%); s1_american 0.609 (-39.1%, 10/10 wins)
**Statistics:** primary paired Wilcoxon p = 0.0322 (8/10 wins); s1 p = 0.00098; s2 neutral (p_faster = 0.539, p_slower = 0.5)
**Regression:** none observed. One seed regressed +4.7 s (13.9 -> 18.6 s) during the
salvage run, measured while a second benchmark stream shared the machine; the worker's
own pre-crash sandbox measurement (9/10 wins, p = 0.00195) had no such outlier.

## Provenance (unusual; read before citing)

The `opt-bitset_domains` worker finished implementation, full tests, and three-seed
bit-identical semantics verification, and measured primary p = 0.00195 (9/10 wins, -25.7%)
before a model-provider outage killed it during the secondary benchmarks (no patch
captured). The exact edit set was recovered verbatim from its transcript
(`opt-bitset_domains.jsonl`, agent session 2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b),
applied by the orchestrator, retested (100/100), re-verified semantically (cores-1
same-seed event streams identical), and re-measured inline (numbers above). Verdict: KEEP.

## Problem

Per slot, the live-domain array spans ALL words of the slot's length in the dictionary
(reaching ~28k for std33 lengths 7-8): ~300-450 KB per slot, ~15-20 MB per worker state.

- Cloned per worker fork (`PreparedSearch` root) AND per randomized restart
  (`find_fill_for_seed_with_options` clones the slot vector each retry).
- `clear_eliminations` on every backtrack scanned the full 16-B-strided array.
- The AC adapter predicate `is_word_eliminated` read this array per candidate check in
  the hottest loops.

## Solution

`Vec<u16>` with 0=live, 1=unblamed-eliminated, slot_id+2=blamed — the same information as
the `Option<Option<SlotId>>` encoding at 1/8 the width, so every dense scan/build/clone
shrinks proportionally and `u16` compares vectorize. `build_slots` asserts the grid fits
the encoding (`slots - 1 <= u16::MAX - 2`; 65k slots, unreachable in practice).

### Key design decisions

1. **Dense u16 over bitset+sidecar.** A live-bitset + blame-sidecar would be smaller
   still but splits every mutation across two arrays and complicates the blame scan; the
   u16 array keeps one-word-per-entry semantics identical to the old structure,
   minimizing correctness risk in undo paths.
2. **Blame encoding preserves semantics exactly**: `clear_eliminations(slot)` compares
   against `encode_blame(Some(slot))`; permanent eliminations (1) survive blame undo.

## Files changed

- `src/backtracking_search.rs` — encoding constants + `encode_blame`, all mutation/check
  sites (add/remove/clear_eliminations, Debug impl, adapter predicates, filters).
- `src/live_state.rs` — `build_slots` init + capacity assert; one filter site.

## Why 17-26% instead of more

Fork/retry clones are a single-digit share of runtime; the steady-state win is cache
locality inside AC and narrower undo scans. Glyph-count maintenance per elimination
(untouched here) now dominates the per-mutation cost.

## Remaining opportunities

- `glyph_count_refs` (borrowed glyph counts in the adapter) — see backlog.
- The `eliminations` array still spans non-option words (below-min-score words of the
  same length); a slot_options-indexed compact domain would shrink further at the cost
  of per-lookup position mapping.
