# Feature: `--grids N` — several distinct fills per solver invocation

Status: user-story spec, 2026-08-04. Motivated by a real production run
(`local/brno2026/`); implement against the current engine. When this lands, the
`good-crossword` skill must be updated in the same change — see "Skill sync".

## User story

As the **puzzle-building pipeline** (the `good-crossword` skill),
I want one `ingrid_core` invocation to emit **N distinct complete grids** at or
above a quality floor,
so that the "harvest several fills and grade them" step costs **one** solver
run instead of N sequential CLI invocations with hand-varied `--seed`.

## Why this exists (all measured today, Brno puzzle build)

1. **Fill selection by quality is mandatory, not optional.**
   `count_preferred_words` is deliberately only correlated with editorial
   quality: it *manufactures* same-lemma collisions at scale (4 of 6 six-seed
   fills carried one) and farms oblique forms of tier entries (`sonům` panel).
   `fill_critic.py` therefore grades every candidate and the pipeline picks on
   defects/membership, never on count. A pool of candidates is a hard input to
   the skill's workflow (step 7).

2. **Today the only lever is `--seed` reruns** — and its default is a trap.
   `--seed` defaults to 0, making runs byte-identical. My first "harvest"
   produced five identical grids in 30 minutes of 9-core compute before anyone
   noticed. That cost is pure user error bait, and marginal grids then cost
   full wall-clock each (dictionary reload ~4 s + full search).

3. **The distinct-fill store already exists; only emission is missing.**
   Verified in-tree: `PreferredFillSuccess.certified_fills` is a
   `DistinctFillSet` (`src/fill_set.rs`) — a `BTreeSet` of canonical
   slot-indexed fill keys, capped at 100 000 — that the scheduler populates
   for **every** distinct full fill a worker finds at the incumbent threshold
   (incumbent-improving, equal-incumbent, and the post-loop cancellation-race
   drains). The CLI then prints exactly one rendered grid
   (`bin.rs:fill_once`, `render_grid(&config, &result.fill.choices)`). So the
   feature is an **emission loop over a store that already exists**, not a
   search change.
   Detail: a complete fill at that point is `FillSuccess{choices: Vec<Choice>}`
   (`backtracking_search.rs:628`), and `canonical_fill_key` can rebuild each
   fill from its key — everything needed lives in the returned
   `PreferredFillSuccess`.

4. **The estimator proves enumeration is cheap in-process.**
   `--estimate-variants`, at guide probability 0.80, enumerates *distinct
   certified fills* within one run (10 vs 7 against 0.98 in the measured
   sweep) — but they are analysis artifacts, not emitted products: it seeds
   its known-fill set from the same `certified_fills` and exposes only the
   count. The skill already documents "use p=0.80 if what you want is a
   diverse pool of fills"; that is a workaround for a flag that should exist.

## Proposed surface

- `--grids N` (default `1` → today's behavior, back-compat).
- Implementation, per the verified shape of the code:
  1. **v0 (pure emission, zero solver changes):** at `fill_once`'s render
     point (`bin.rs:485`), also iterate `result.certified_fills`, rebuild
     `Vec<Choice>` from each canonical key against `config.slot_configs`,
     `render_grid` each, print (or write one file per grid under
     `--grids-dir PATH`). Caveat carried forward: today only
     *incumbent-threshold* fills land in the set, so v0 yields exactly the
     optimum-level pool — which is precisely the harvest pool.
  2. **v1 (quality floor below the optimum), only if wanted:** relax the
     set's insertion predicate (currently equal-incumbent) to
     `>= best - --grids-delta`; one-line predicate change at the
     `with_fill`/`insert` call sites, everything else identical.
- Deterministic given `--seed` (as everything else).

## Non-goals

- No change to the objective (`count_preferred_words` losses are a separate
  spec: lemma-aware count / Σ score).
- No scheduler redesign, no async, no new third-party deps.
- Not a replacement for `--estimate-variants` (that answers "how much slack";
  this answers "give me the material to grade").

## Acceptance criteria

1. `--grids 6` on `local/brno2026/pinned.txt` (Brno testbed) emits 6 distinct
   15×15 grids in ≈ the wall-clock of one search, each filling the template
   legally and within the floor of the best.
2. The best of the N equals what `--grids 1` finds on the same seed — no
   quality regression on the classical path.
3. `fill_critic.py` runs on each emitted grid unchanged.
4. Determinism: same seed → same N grids.

## Skill sync (required in the same PR)

Update `.omp/skills/good-crossword/SKILL.md`:
- Workflow step 7 "Harvest several seeds" → one `--grids` invocation;
  delete the `--seed` determinism trap note and the "watch incumbent
  timestamps" harvesting guidance (whole step collapses).
- Toolbox table gains the flag; `--estimate-guide-probability`'s "0.80 for a
  diverse pool" note becomes obsolete (the pool is a product now).
- Keep `--estimate-variants` guidance strictly for the slack question.
