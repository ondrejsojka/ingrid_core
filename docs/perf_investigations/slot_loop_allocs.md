# Investigation: Reused scratch buffers in the fill loop (slot_loop_allocs) — DISCARDED

## Summary

Hoisted all per-iteration heap allocations in the fill loop (`slot_weights`,
`choose_next_slot`'s slot-id buffer, `maintain_arc_consistency`'s
`remaining_option_counts` + `fixed_slots`) into a reused `SearchScratch`. Determinism
proven byte-identical; performance neutral. **DISCARD.**

**Primary (paired, N=10):** wilcoxon p_faster ≈ 0.5, p_slower ≈ 0.5, median paired ratio
~1.0 — no effect. **Tests:** 100/100 pass; clippy parity restored.
**Secondaries:** no regression.

## Solution (implemented and verified, then measured neutral)

- `src/backtracking_search.rs`: `SearchScratch` owned by the retry loop;
  scratch-taking variants of `calculate_slot_weights`/`choose_next_slot`/
  `maintain_arc_consistency`; live_state-facing signatures preserved as thin wrappers.
- Same-seed `--cores 1` logs byte-identical ignoring elapsed_ms (seed 1000 fully;
  seed 1003 identical through all decision events).

## Why it did not pay

glibc malloc's thread-local arenas already make these ~100-1000-cycle allocations cheap,
and at 10 workers the arenas rarely contend (workers allocate mostly in their own
threads, free in the same threads). The profile showed no allocator symbols above 1%
even in round 0; the hypothesis was speculative and the measurement answered it.

## Remaining opportunities

- None on this path. The fill loop's remaining costs are algorithmic (AC revision work),
  not allocation plumbing.
- Patch retained for the record: `opt-slot_loop_allocs.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
