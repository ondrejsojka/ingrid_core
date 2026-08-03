# Investigation: Frontier-target worker swarm (scheduler_frontier_focus)

## Summary

Changed `find_best_fill_internal`'s target allocation from "one worker on the frontier,
most workers on long-lived spread probes" to "all-but-one workers swarm the frontier
target (incumbent+1) with independent RNG streams, retargeting on every improvement":
63% faster median time-to-6-preferred, with 3-12x wins on the slow seeds that dominate
perceived latency.

**Baseline:** 8045 ms median (22839, 856, 7278, 17621, 8812, 43922, 553, 12847, 2263, 1023)
**After:** 2610.5 ms median (2915, 859, 1151, 3760, 3617, 3602, 379, 4236, 2306, 540)
**Improvement:** 63.0% median; 8/10 wins (losses 3 ms and 43 ms on sub-second trivial seeds)
**Statistics:** paired Wilcoxon p_faster = 0.0049, p_slower = 0.9971
**Regression:** none — s1_american IMPROVED (p_faster = 0.002, median ratio 0.41); s2_fast_wall neutral (ratio 0.999)

## Problem

Search-log analysis (worker insight, confirmed): in the baseline scheduler the initial
spread assigns the 10 workers to targets 0,7,14,...,61. The entire incumbent climb
(3->4->5->6) runs on the single slot freed by the target-0 worker; **the other 9
initial-spread probes never terminate within the 120 s timeout** and starve the frontier.
Reaching 7 when it is feasible-but-hard (seed 3000: 36 s) happens on one core's luck.

## Solution

In `src/parallel_search.rs`:

- `frontier_worker_quota(worker_count) = worker_count - 1`: after the initial spread is
  consumed, freed workers spawn on the frontier target `lower` (= incumbent+1) until the
  quota is met — up to 9 independent RNG streams racing the same target.
- On every incumbent improvement, all but the single highest-target gap probe are
  cancelled so ~9/10 cores immediately retarget onto the new frontier; the kept probe
  continues exploring for bounds collapse.
- Bounds collapse (root hard-failures at spawn shrink `impossible_from`), cancellation on
  success/hard-failure, and the optimality-termination condition are unchanged.

### Key design decisions

1. **Swarm, not bisect, after the spread.** The calibration data showed the initial
   spread's real job is collapsing `impossible_from` cheaply at spawn; after that, every
   post-spread worker is most valuable at the frontier (either a fill = anytime progress,
   or a proof of infeasibility = optimality progress — the swarm also accelerates the
   proof tail, since 10 diverse streams race an infeasible frontier's proof).
2. **Quota = all-but-one, reserve = the single HIGHEST probe.** The one kept probe
   preserves pressure on `impossible_from` from above; half-quota variants were not
   measured (each paired bench ≈ 40 min) and the chosen design leaves little room on the
   primary workload.

## Files changed

- `src/parallel_search.rs` — quota fn, spawn-target selection, improvement-time probe
  cancellation; doc comment updated.

## Why 63% and why variance collapses too

Candidate timings cluster at 0.4-4.2 s vs baseline 0.6-44 s: the slow-seed tail (where
the frontier must be found lucky on one core) is exactly what swarming removes. The same
seeds *also* reach HIGHER counts sooner (seed 1000: 8 preferred by 26 s vs baseline's 6
at 24.9 s) and finish the full optimality proof faster (diverse streams race the proof).

## Remaining opportunities

- Quota/reserve tunables (half-quota vs all-but-one; keep-lowest vs keep-highest probe)
  unexplored; measure on a workload where the frontier is frequently feasible (more
  preferred words available relative to slots).
- Time-to-PROVEN-optimal (`--timeout 0`) as an explicit benchmark workload — the policy
  should help there too, but it is unmeasured.
- Worker patch provenance: `opt-scheduler_frontier_focus.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
