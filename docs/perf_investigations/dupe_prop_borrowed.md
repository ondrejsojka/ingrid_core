# Investigation: Borrowed dupe queries in singleton propagation (dupe_prop_borrowed) — DISCARDED

## Summary

Replaced the per-singleton-event `HashMap<usize, HashSet<WordId>>` materialization with a
zero-allocation `AnyDupeIndex::collect_dupes` into a reused sorted buffer plus binary-search
membership. The mechanism provably fired — SipHash+hash_one self time fell from ~9.7% to
~0.04% on the primary workload — and elimination order stayed byte-identical. End-to-end
effect: none. **DISCARD.**

**Primary (paired, N=10):** wilcoxon p_faster = 0.6875 (5 wins / 5 losses), median paired
ratio 1.053.
**Mechanism evidence:** perf on candidate build shows SipHash/hashing at ~0.04% self time
(baseline ~9.7%). Semantics: `--cores 1` search logs byte-identical on non-elapsed columns
(seeds 1000, 1001); tests 100/100.

## Why neutral despite a provably hot call path

On the primary workload the run reaches the target count in 0.2-3 s of the 120 s budget;
the singleton/dupe phase fires too rarely in that window for its ~7-10% of profile self
time to aggregate into wall-time. The profile share was also measured on the round-3
binary where the phase dominated more; by the time this landed, support_index had already
shrunk the total pie.

## What was done (kept for the record)

- `src/dupe_index.rs`: new `AnyDupeIndex::collect_dupes` (sorted, deduped, into a reused
  caller buffer); exempt-rule semantics preserved byte-exactly (preferred-preferred pairs
  omitted under `--dupe-exempt-preferred`; whole-word, explicit, and standard-involving
  pairs always included).
- `src/arc_consistency.rs`: singleton phase iterates peer options in unchanged option
  order, membership via binary search over the collected vec.
- `src/word_list.rs`: trait surface plumbing.

## Remaining opportunities

- Do not retry this path without a workload where the singleton phase is a proven >15%
  aggregate share (e.g., heavily constrained grids with many singletons).
- Patch retained: `opt-dupe_prop_borrowed.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b). The collect_dupes API is
  correct and cheap to resurrect if a dupe-heavy workload appears.
