# Template diagnostics: tell me where the grid is tight

Status: a want, not a design. Companion to `persistent-oracle-spec.md` — that one is about
*throughput* (asking the fillability question cheaply), this one is about *signal* (getting
back something you can steer on). They are worth roughly nothing apart and quite a lot
together.

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

The engine knows. Arc consistency computed exactly which slots collapsed, and the search —
when it ran — accumulated a weight per crossing recording which ones kept causing wipeouts.
Both get thrown away at process exit. I am reduced to inferring, from one boolean, a
property the solver measured in detail and then discarded.

## The naive version, and why it's wrong

My first instinct was "on failure, tell me which slot's domain wiped out."

That's a bad ask, and I'm recording why so nobody builds it. AC-3 works off a queue, so
*which* domain empties first depends on propagation order. There may be five equally
culpable slots; you get an arbitrary one. It's a single sample from a set, dressed up as
a diagnosis, and an outer loop that mutates the block pattern "there" would be chasing
queue order.

## What I actually want

Two vectors. Neither is a sample.

**1. The post-AC domain profile.** For every slot, how many candidates survive initial arc
consistency. Not the first failure — the whole vector, at fixpoint.

This is well defined precisely where the naive version isn't: the arc-consistent closure
is **unique**, independent of the order arcs are processed. So the set of wiped slots, and
every surviving domain size, is canonical. `[61, 4, 220, 0, 1, 18, 0, ...]` tells me the
grid is dead at slots 4 and 7 and hanging by a thread at 2 and 5. That is a gradient.

**2. The final crossing weights.** The `dom/wdeg` counters in `backtracking_search.rs`,
which exist today and are documented right there at line 31: *"How much do we decrease the
weight of each crossing every time we wipe out a domain?"*

That is already an accumulated, whole-search record of which crossings keep causing
failure. It is the many-sample answer to the question I was clumsily asking, and it is
computed on every run and then dropped on the floor.

**The two cover different regimes, which is why I want both.** A grid that dies at initial
AC never runs a search, so weights are empty and the domain profile is all you have. A grid
that is *fillable but hostile* has a perfectly healthy-looking domain profile — nothing is
empty — and only the weights reveal where the search actually bled. Today I hit both
failure modes and could distinguish them only by wall-clock.

## Roughly how I'd expect it to work

- **A diagnostic AC pass that doesn't bail.** Normal propagation exits the moment a domain
  empties, which is right for the hot path and useless for a report. Under a flag, keep
  propagating to fixpoint, let empty domains stay empty, then dump per-slot counts. On 61
  slots this is microseconds; the cost is the lost early exit, which nobody pays unless
  they asked.
- **Surface the weights that already exist.** `crossing_weights: &[f32]` and the derived
  `calculate_slot_weights` are right there. Emit the final vectors. No new machinery, no
  new bookkeeping in the hot loop — just don't discard them.
- **Keyed by geometry, not by internal id.** A crossing means nothing to me as
  `crossing_id: 47`. Give me `(row, col)` or `(A@(5,2), D@(6,1))`. The whole point is to
  map the number back onto the block pattern I am mutating.
- **Written where telemetry already goes.** `--search-log` is an existing CSV telemetry
  path; a `--diagnostics <path>` beside it emitting one row per slot and one per crossing
  would fit the grain of the tool. Scalar rows, no allocation in the scheduler path — the
  crate is already careful about this and I don't want to be the reason it stops being.
- **Off by default, emitted on both outcomes.** I need the profile for grids that *fill*
  at least as much as for grids that don't — comparing two healthy templates is the
  common case.

## What I'd do with it the same afternoon

- **Directed mutation instead of restart.** Move blocks adjacent to the slots that are
  actually starving, rather than annealing the whole pattern and re-rolling.
- **Rank templates before spending anything on them.** Sixteen candidate frames, one cheap
  pass each, sorted by how much slack they carry and where. Today I picked by κ, which
  never applied, and then brute-forced.
- **Choose which pin to drop.** When 6 pinned entries are collectively unfillable, the
  weights say which crossing is doing the damage, so I drop *that* one instead of
  bisecting on the count.
- **Stop guessing at the short band.** If the profile says the length-3 slots are the ones
  at ≤2 candidates, that is a dictionary problem, not a geometry problem, and I should go
  find another lexicon rather than move blocks around.

## What I don't want

- **No prose.** No `reason: "the grid is over-constrained"`. Two numeric vectors, keyed by
  coordinates. I'll do the interpreting.
- **No minimal unsatisfiable core.** Tempting, genuinely useful, and a research project.
  The domain profile is 90 % of the value for ~1 % of the work.
- **No per-word blame.** "The word `kirschner` is the problem" is not actionable — the pin
  is a decision I already made deliberately. Blame the *geometry*; that's the thing I'm
  searching over.
- **No cost when unasked.** If this shows up as a slowdown in the default path it was
  built wrong.

## Open questions

- **Is the domain profile actually predictive of pin capacity?** I believe a template with
  fat post-AC domains takes more pinned theme entries, but I have never measured the
  correlation. This is the first experiment to run, and if it comes out weak the feature
  is worth much less than I think.
- **Do the weights transfer?** Within one template across seeds they should be stable. But
  is "this region is hostile" a property of the block pattern (transfers to a mutated
  neighbour) or of the specific fill attempt (doesn't)? If the former, the outer search can
  carry weights across mutations and that is a much stronger search.
- **Normalisation.** Raw domain counts are dominated by slot length — 220 candidates at
  length 3 and 220 at length 8 mean completely different things. Probably want
  `log2(count)` or a per-length z-score, but I'd rather be handed the raw numbers and
  normalise them myself than have the engine pick.
