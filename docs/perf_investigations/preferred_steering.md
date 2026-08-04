# Investigation: Preferred-rich slot steering (preferred_steering)

## Summary

Slot selection priority is now `dom/wdeg / (1 + BETA * min(preferred_remaining, CAP))`
(BETA=1.0, CAP=8.0): the search commits early to slots that can still host Preferred-tier
words, so the frontier target's feasible region is entered at low depth. Largest single
win of the campaign: 7x faster median time-to-8.

**Baseline:** 6264.5 ms median (primary, target:8)
**After:** 689.5 ms median
**Improvement:** 85.8% (median paired ratio 0.1417)
**Statistics:** paired Wilcoxon p_faster = 0.00098 (10/10 wins)
**Regression:** secondaries neutral by gate — s1_american ratio 1.759 but p_slower = 0.216
(insignificant; flagged: see Follow-ups), s2_fast_wall ratio 1.074 p_slower = 0.246

## Problem

`choose_next_slot` was tier-blind: pure dom/wdeg with adaptive stickiness. At preferred
targets 6-9 on a 15x15 grid, most slots are Standard-only; the search committed deeply
into preferred-hostile regions and rediscovered unattainability at high depth on
millions of branches.

## Solution

Divide the dom/wdeg priority by `1 + preferred_live_capped`, where `preferred_live_capped
= min(preferred_remaining, 8)` uses the incremental per-slot counter from round 1
(preferred_support_cache). Slots that can still host preferred words sort earlier;
with no preferred list the term is exactly 1 and the behavior is unchanged (s2
neutrality confirms).

### Key design decisions

1. **Preferred-rich-first, not preferred-scarce-first.** The winning polarity commits
   early where the frontier target CAN still be completed (slots holding live preferred
   options), instead of boosting preferred-poor slots as the original brief sketched.
   With K-preferred targets, entering the attainable region early dominates: every
   branch outside it is provably wasted work for this metric.
2. **Bounded term (CAP=8).** The divisor saturates so scale-free wdeg ratios still
   order within the preferred-rich class.
3. **Heuristic-only.** Values only order choices; fill requirements unchanged.

## Files changed

- `src/backtracking_search.rs` — `PREFERRED_STEERING_BETA/CAP` consts,
  `preferred_steered_slot_priority`, one call site in `choose_next_slot`.

## Why 7x and what it implies

The frontier-swarm policy (round 3) wins when a target is feasible; preferred steering
makes "feasible" the common case by keeping the search inside the attainable region,
so the swarm spends its streams racing real solutions instead of dead domains. Also
reaching HIGHER counts is now cheaper, which compounds with future metric targets.

## Follow-ups

- s1_american showed a 1.759x median ratio at p_slower=0.216 — underpowered to call.
  Supplemental 10-round post-merge check is logged in the backlog round entry. If a
  real American-grid tradeoff exists, a region-aware BETA or polarity is the next knob.
- `ordering_pool_tuning` (round-6 mechanism) and BETA/CAP sweep remain open.
