# Measurements for the two specs, before anyone writes the features

Status: results, not opinions. Both specs asked for one experiment to run before the code;
both were run. Raw data and the throwaway harness are in `local/pincap/`
(`profiles.csv`, `capacity.csv`, `fills.csv`, `trajectory.csv`, `analyze.py`,
`src/main.rs`, `src/bin/pinprof.rs`, `src/bin/deadprofile.rs`).
`ac_diagnostic_scaffold.patch` is the temporary, now-reverted core patch that the dead-grid
experiment needed; nothing under `src/` was kept. The harness `.rs` files are a frozen
snapshot — re-running them means re-applying that patch, since the working tree's dupe-index
surface has since moved on.

Setup for everything below: the Karolína corpus, because it is the one that produced the
question — the 48 existing 15×15 švédská templates in `local/karolina/tpl{,2,3,4}/s*.txt`
(59-72 slots each), `std33.dict` (160 428 entries) as Standard, `theme.dict` (79 forms) as
Preferred, `--min-score 33`, `--max-shared-substring 5`, `--dupe-exempt-preferred`.
The harness loads the dictionary **once** and asks 43 610 fillability questions against it,
which is incidentally a working demonstration of the persistent-oracle spec: 8-17 ms per
initial-AC verdict on a 15×15, against ~4.5 s per CLI invocation.

## 1. Is the post-AC domain profile predictive of pin capacity? Mostly no.

**On a bare template the profile is not a measurement of the geometry — it is the length
histogram with 2-5 % shaved off.** Median post-AC domain against the raw count of
dictionary words of that length:

| slot length | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---:|---:|---:|---:|---:|---:|
| dictionary candidates | 1 908 | 6 037 | 13 900 | 21 338 | 27 507 | 28 291 |
| median post-AC domain | 1 886 | 5 926 | 13 634 | 20 508 | 26 051 | 27 061 |
| survives | 98.8 % | 98.2 % | 98.1 % | 96.1 % | 94.7 % | 95.7 % |

Across all 48 templates and 3 183 slots, the **smallest** post-AC domain on any bare grid was
1 581. Nothing is at 4, nothing is at 0, nothing is "hanging by a thread". The
`[61, 4, 220, 0, 1, 18, 0, ...]` vector the spec is written around does not occur on a bare
15×15 with a Czech dictionary this size; initial AC has almost nothing to bite on until
letters are in the grid.

**And the correlation with capacity is carried by slot count and slot length, not by the
domains.** Pin capacity was measured as AC-guided greedy first-fit: shuffled (slot, theme
word) pairs, accept a placement iff initial AC still succeeds, 8 seeds per template
(mean 11.9 pins, between-template sd 1.46, within-template sd 0.94). Spearman ρ against the
template's mean capacity, n = 48:

| summary of the bare profile | ρ | perm. p | ρ after regressing out slot count |
|---|---:|---:|---:|
| slot count | **0.658** | <0.0001 | — |
| mean slot length | **−0.737** | <0.0001 | −0.508 |
| Σ log2(domain) | 0.637 | <0.0001 | −0.345 |
| mean log2(domain) | −0.623 | <0.0001 | −0.275 |
| min domain | 0.177 | 0.23 | 0.173 |
| mean per-length z of log2(domain) | 0.200 | 0.17 | 0.296 |
| min per-length z | 0.451 | 0.0014 | 0.274 |

Every strong correlation is a restatement of "more slots, shorter slots → more places a
theme word fits". The length-normalised summaries — the part that is genuinely about
*slack* rather than about geometry you can count for free in Python — sit at ρ ≈ 0.2-0.45,
and the partial correlations after controlling for slot count are 0.27-0.30 on n = 48.

So: **for the advertised use case — rank 16 candidate frames before spending anything on
them — the profile buys you approximately nothing over the block pattern's own length
histogram.** That use case (spec, "What I'd do with it the same afternoon", item 2) is the
one to drop.

## 2. Where the profile does carry signal: after pins, not before

The same profile measured *during* construction is a different object. Median
(post-AC domain / pre-AC unary domain) over the still-open slots, as accepted pins
accumulate, and the number of open slots where AC removes more than half of what a unary
pattern screen would have left:

| pins | 0 | 2 | 4 | 6 | 8 | 10-11 |
|---|---:|---:|---:|---:|---:|---:|
| median AC/unary (tpl2/s00) | 0.98 | 0.95 | 0.88 | 0.65 | 0.57 | 0.25 |
| slots cut >50 % by AC | 0 | 8 | 20 | 24 | 29 | 37 |
| smallest open domain | 1 780 | 3 | 3 | 3 | 2 | 1 |

Same shape on all six templates traced (`trajectory.csv`). This is the honest statement of
the feature's value: **the propagated part of the profile — the part a bitset unary screen
cannot compute — is worth 2 % on a bare frame and 75 % once eight entries are pinned.**
Directed mutation and "which pin do I drop" (spec items 1 and 3) live in that regime and
survive this experiment. Ranking bare frames does not.

## 3. The non-bailing AC pass does not do what the spec assumes

The spec's mechanism is "keep propagating to fixpoint, let empty domains stay empty, then
dump per-slot counts", justified by the arc-consistent closure being unique. I implemented
both readings behind the scaffold patch and measured them on 12 dead grids (built by pinning
theme entries blindly until AC failed).

- **Literal reading (empty domains keep propagating): the emptiness cascades.** An empty
  domain supports no glyph in any of its cells, so every crossing loses every candidate. On
  `tpl4/s00` this wiped **57 of 61 slots** from one real wipeout (and 56 of 61 on another
  seed). The report is then all zeros: true, canonical, and useless. It did not cascade in
  the other 10 cases only because the dead slot's neighbours were themselves fully pinned,
  i.e. `fixed_slots`, which blocks propagation. You cannot tell the two situations apart
  from the output.
- **Freezing emptied slots (the only version that yields a gradient) is not canonical.**
  Perturbing the slot weights, which is exactly what decides the AC queue order and what the
  live search does to them anyway, produced **7 distinct dead-slot sets across 24
  propagation orders** on one grid (sizes 1 to 4) and 4 distinct sets on another. Ten of
  twelve grids were stable. So the freeze variant is a sample from a set dressed up as a
  diagnosis — the exact failure the spec's "The naive version, and why it's wrong" section
  rejects, reintroduced through the fixpoint.
- **The canonicity claim does hold where the grid survives AC.** Control: 24 perturbed
  orders on three healthy bare templates gave 1 distinct profile each, byte-identical.

Also, mechanically: a slot flagged for singleton propagation can be emptied before that
phase runs, and the production `expect("slot with needs_singleton_propagation must have
exactly one option")` would fire. The bail is load-bearing; removing it needs that guard.

**Consequence for the design.** Emit the profile for grids that survive AC — it is cheap,
canonical, and the regime where it is informative (§2) is a superset of that. For grids that
die, do not pretend to a canonical profile: the honest cheap product is the
`ArcConsistencyFailure.weight_updates` you already compute, **sampled over several randomised
propagation orders**, which converts the arbitrary single wipeout into a distribution over
blamed crossings at ~12 ms a sample. That is the many-sample answer the spec asks for in the
regime where the search never runs and the wdeg weights are empty.

## 4. Initial AC as an oracle: decisive per placement, optimistic in aggregate

Greedy pinning that accepts only AC-consistent placements reached 10-16 pins per template.
Running the real solver (9 workers, 12 s) on those grids:

- 5 of 12 templates: **`Unfillable grid` at the AC-accepted frontier** — proven, in 0.1-0.5 s.
- All 5 filled at roughly half the pins (5-6 instead of 10-12).
- The other 7 filled at the frontier, one of them (tpl3/s00) at 16 pins in 0.1 s.

So initial AC is a proof of death but a weak proof of life: at the greedy frontier its
false-accept rate on this corpus was 5/12. The persistent-oracle spec's open question ("does
AC pass too many templates?") is answered — yes, materially. That does not weaken the case
for the oracle: at 8-17 ms it is the difference between a screen and a search, and the same
`tpl2/s00` where the blind assign-then-check ladder filled only at 4 pins
(`local/karolina/fill_s00_*.txt`: unfillable at 6, 8, 10, 12, 14, 18, 20) is fill-verified at
5-6 here, while `tpl3/s00` is fill-verified at **16**. It does mean the constructor must keep
a real-fill check at the end, and that `unknown` must stay distinct from `unfillable`.

Note what that last comparison implies for the diagnostics spec: the biggest single factor in
how many entries a template takes was **which template**, spanning 5-16 fill-verified pins —
and that spread is predicted by slot count and mean slot length (§1), both free.

## 5. Does the score column steer selection? Yes, and by a lot

`grid_config.rs::sort_slot_options` sorts each slot's candidates by
`(tier != Preferred, −(900·mean_log10_crossing_support + 5·letter_score + 5·score))`, and
`backtracking_search.rs` picks among the first three surviving candidates in that order with
weights [4, 2, 1]. So score is a real term in candidate ordering, not just a `--min-score`
gate — though a 10-point score difference (50 units) is worth only ~0.055 of mean
log10 crossing support, so `fill_score` dominates.

Measured, because the arithmetic doesn't tell you what survives the search: same grid
(`tpl2/s00`), same 160 428-word list, same bar, three seeds each, 30 s, 2 cores; one arm with
the real scores, one with every score flattened to 50.

| arm | mean true score of the 69 filler answers | median | answers scoring <40 |
|---|---:|---:|---:|
| real scores | **54.0** | 54 | 60 / 207 (29 %) |
| flattened to 50 | 45.3 | 42 | 84 / 207 (41 %) |

The dictionary's own mean is 43.4, so the flattened arm fills essentially at random with
respect to score while the scored arm pulls +10 points. **The tier build is a control surface
for filler quality**, which means the score-weighted-objective wish (`Σ score` instead of
`count_preferred_words`) is worth building and `build_tier.py`'s score column is worth
grading carefully.
