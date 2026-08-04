# Investigation: Luby restart schedule (luby_restarts) — DISCARDED

## Summary

Replaced the geometric randomized-restart schedule (cap starts at 500 backtracks,
times 1.1 per retry) with a Luby-sequence schedule (500 times luby(retry)) plus
per-worker start-index desymmetrization. Clearly slower. **DISCARD.**

**Primary (paired, N=10, target:8):** 3/10 wins, median paired ratio 1.106 (10.6%
slower), wilcoxon p_faster = 0.754, sign p = 0.945.

## Measured context

Baseline backtracks-per-fill distribution (684 baseline search logs): median ~23,
p90 ~396, max ~3229. The geometric schedule's slow growth lets each worker stream
commit longer before re-rolling. In this solver the frontier swarm (round 3) already
supplies schedule diversity across 10 streams. Luby's chop-and-escalate cadence forced
premature restarts on seeds whose deep grinds would have paid off. The classic Luby
near-optimality result does not transfer to a portfolio with cheap restarts and a
strong value ordering.

## What was done

- `src/backtracking_search.rs`: Luby cap + worker-index offset; UNIT=500 justified
  from the measured distribution above.
- Tests pass; behavior otherwise unchanged.

## Remaining opportunities

- None on restart scheduling; the lever is exhausted. Do not retry other restart
  schedules without evidence the portfolio diversification regresses.
- Patch retained: `opt-luby_restarts.patch` (agent session
  2026-08-02T12-50-24-444Z_019fc286-883c-7000-87f2-c4ba71e2926b).
