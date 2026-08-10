# Template diagnostics: tell me where the grid is tight

Status: **measured, and narrowed.** This started as a want. It demanded one experiment before
any code; the experiment was run and killed roughly half of it, including the headline
mechanism. What follows is what survived contact with the numbers. Raw data and method:
`template-diagnostics-findings.md`. Companion to `persistent-oracle-spec.md` — that one is
about *throughput*, this one is about *signal*.

## The story

I run a search over templates. Not over fills — `ingrid_core` owns that and does it well —
over **block patterns**, because on a small grid the geometry decides everything
downstream: answer count, then short-slot count, then how deep the dictionary has to go,
then whether the filler reads like Czech.

Today that search was **rejection sampling**. I generated 16 legal 15×15 frames, and then
had no way to tell which of them had any room in it. So I probed: pin 20 theme entries,
`Unfillable`; pin 18, `Unfillable`; 16, 14, 12, 10, 8, 6 — all `Unfillable` — 4, fills.
An hour, one bit per probe, and at the end I still didn't know *why* 6 failed or whether a
different 6 would have worked.

The engine knows more than one bit. Arc consistency computed which slots collapsed, and the
search — when it ran — accumulated a weight per crossing recording which crossings kept
causing wipeouts. Both get thrown away at process exit.

## The naive version, and why it's wrong

My first instinct was "on failure, tell me which slot's domain wiped out."

That's a bad ask, and I'm recording why so nobody builds it. AC-3 works off a queue, so
*which* domain empties first depends on propagation order. There may be five equally
culpable slots; you get an arbitrary one. It's a single sample from a set, dressed up as
a diagnosis, and an outer loop that mutates the block pattern "there" would be chasing
queue order.

This section is the one part of the original document that got *more* right than I knew:
the fixpoint version I proposed as the fix turns out to reintroduce exactly this failure.
See below.

## What the measurement killed

Setup for all of it: the 48 existing 15×15 švédská templates (59–72 slots), a 160 428-entry
standard list, 79 preferred forms, 43 610 fillability questions at 8–17 ms each.

**1. The domain profile on a *bare* grid is not a measurement of the geometry.** It is the
dictionary's length histogram with 2–5 % shaved off:

| slot length | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---:|---:|---:|---:|---:|---:|
| dictionary candidates | 1 908 | 6 037 | 13 900 | 21 338 | 27 507 | 28 291 |
| median post-AC domain | 1 886 | 5 926 | 13 634 | 20 508 | 26 051 | 27 061 |

Across 48 templates and 3 183 slots the **smallest** post-AC domain on any bare grid was
**1 581**. The `[61, 4, 220, 0, 1, 18, 0, ...]` vector this document was originally written
around does not occur on a bare 15×15 with a Czech dictionary this size. Initial AC has
almost nothing to bite on until letters are in the grid.

**2. So "rank 16 candidate frames before spending anything on them" is dead.** Spearman ρ
against measured pin capacity (n = 48): slot count **0.658**, mean slot length **−0.737**,
but the length-normalised summaries — the part that is genuinely about *slack* rather than
about geometry I can count for free in Python — sit at ρ ≈ 0.20–0.45, and 0.27–0.30 after
controlling for slot count. The profile buys approximately nothing over the block pattern's
own length histogram. Drop that use case. Same for "stop guessing at the short band": on a
bare grid nothing is at ≤2 candidates, so the profile cannot tell me the length-3 slots are
a dictionary problem.

**3. The non-bailing AC pass does not work.** Both readings were implemented behind a
throwaway patch and measured on 12 dead grids:

- *Let empty domains keep propagating.* The emptiness cascades — an empty domain supports no
  glyph in any cell, so every crossing loses everything. One real wipeout wiped **57 of 61
  slots**. The report is all zeros: true, canonical, useless. It failed to cascade in 10 of
  12 cases only because the dead slot's neighbours were fully pinned, and you cannot tell
  those two situations apart from the output.
- *Freeze emptied slots* — the only variant that yields a gradient — **is not canonical**.
  Perturbing the slot weights that decide AC queue order produced **7 distinct dead-slot sets
  across 24 propagation orders** on one grid. That is a sample from a set dressed up as a
  diagnosis: the failure the section above rejects, smuggled back in through the fixpoint.
- Mechanically, a slot flagged for singleton propagation can be emptied before that phase
  runs, and `arc_consistency.rs:469`'s `expect("slot with needs_singleton_propagation must
  have exactly one option")` would fire. The bail is load-bearing.

The uniqueness argument that justified the whole mechanism only holds where **no domain
empties**. Control: 24 perturbed orders on three healthy bare templates gave 1 distinct
profile each, byte-identical.

## What survives

**1. The post-AC domain profile, emitted only for grids that survive AC, and read during
construction rather than before it.** Measured *while* pins accumulate it is a different
object entirely — the ratio of the post-AC domain to what a unary pattern screen alone would
leave, over the still-open slots of `tpl2/s00`:

| pins | 0 | 2 | 4 | 6 | 8 | 10–11 |
|---|---:|---:|---:|---:|---:|---:|
| median AC/unary | 0.98 | 0.95 | 0.88 | 0.65 | 0.57 | 0.25 |
| slots cut >50 % by AC | 0 | 8 | 20 | 24 | 29 | 37 |
| smallest open domain | 1 780 | 3 | 3 | 3 | 2 | 1 |

Same shape on all six templates traced. **The propagated part of the profile — the part a
bitset unary screen cannot compute — is worth 2 % on a bare frame and 75 % once eight entries
are pinned.** That is the honest statement of the feature's value, and it is a superset of
the regime where the profile is canonical, which is convenient.

**2. For grids that die, no profile. Sampled blame instead.** The honest cheap product is
`ArcConsistencyFailure.weight_updates`, which is already computed, **sampled over several
randomised propagation orders**. That converts the arbitrary single wipeout into a
distribution over blamed crossings at ~12 ms a sample — the many-sample answer, in the regime
where the search never runs and the `dom/wdeg` weights are empty.

**3. The final `dom/wdeg` crossing weights, unchanged from the original ask.** They exist
today in `backtracking_search.rs` (`crossing_weights: &[f32]`, `calculate_slot_weights`) and
are dropped on the floor on every run. Emitting them is not new machinery. This is the one
element of the original document that the experiment neither confirmed nor refuted — see
"still open".

The two surviving use cases are **directed mutation** (move blocks adjacent to slots that are
actually starving mid-construction, rather than re-rolling the pattern) and **choosing which
pin to drop** (when 6 pinned entries are collectively unfillable, blame the crossing rather
than bisecting on the count). Both live in the post-pin regime. Neither needs the bare-frame
ranking that died.

## How it should work

- **No non-bailing AC pass.** Keep the early exit; it is correct and load-bearing. Emit the
  profile from the successful fixpoint only.
- **Sampling, where the grid is dead.** N randomised propagation orders, N a caller knob,
  emit the per-crossing blame distribution. Nothing new is computed; the weights are already
  there in `ArcConsistencyFailure`.
- **Surface the weights that already exist.** Emit the final vectors. No new bookkeeping in
  the hot loop — just don't discard them.
- **Keyed by geometry, not by internal id.** `crossing_id: 47` means nothing to me. Give me
  `(row, col)` or `(A@(5,2), D@(6,1))`. The whole point is to map the number back onto the
  block pattern I am mutating.
- **Written where telemetry already goes.** `--search-log` is an existing CSV path; a
  `--diagnostics <path>` beside it, one row per slot and one per crossing. Scalar rows, no
  allocation in the scheduler path.
- **Off by default.** Emitted on both outcomes, but the two outcomes emit *different things*
  now, and that asymmetry is the finding, not an inconsistency to paper over.

## What I don't want

- **No prose.** No `reason: "the grid is over-constrained"`. Numeric vectors keyed by
  coordinates. I'll do the interpreting.
- **No minimal unsatisfiable core.** Tempting, genuinely useful, and a research project.
- **No per-word blame.** "The word `kirschner` is the problem" is not actionable — the pin is
  a decision I already made deliberately. Blame the *geometry*.
- **No cost when unasked.** If this shows up as a slowdown in the default path it was built
  wrong.
- **No profile for dead grids.** Not because it is expensive, but because it would be a
  number that looks canonical and isn't.

## Still open

- **Do the weights transfer?** Within one template across seeds they should be stable. But is
  "this region is hostile" a property of the block pattern (transfers to a mutated neighbour)
  or of the specific fill attempt (doesn't)? If the former, the outer search can carry
  weights across mutations and that is a much stronger search. This is now the *first*
  experiment to run, and item 3 above is worth roughly nothing until it is answered — the
  same bar the domain profile was held to.
- **Does the sampled-blame distribution concentrate?** If 24 orders blame 24 different
  crossings uniformly, sampling has converted one useless answer into a useless histogram.
  Cheap to check on the 12 dead grids already built.
- **Normalisation.** Raw domain counts are dominated by slot length. Probably want
  `log2(count)` or a per-length z-score, but hand me the raw numbers and let me normalise.
  (Weak evidence in favour: min per-length z was the only length-normalised summary that
  correlated with capacity at all, ρ = 0.451.)
