# A second title: LFŠ (Letní filmová škola Uherské Hradiště)

Working notes from applying the `theme-density.md` machinery to a **second publication**,
which `good-crossword` explicitly listed as the untested claim: *"Every
publication-independence claim here is untested — one city magazine, one language."*

Short answer: about half of it transferred. The corpus→tier→expand→fill pipeline
transferred completely. The **precision gate did not**, and the reason is structural
rather than incidental. A new failure class (seeded crossings) appeared that does not
exist in the Metropolitan setup, and one Metropolitan conclusion — that Preferred count
is the wrong selection criterion — turned out to be much stronger than stated.

Delivered artifact: a 15×12 Czech **švédská** with 50 entries, a `KRUMBACHOVÁ` tajenka,
0 hard defects, and a clue set passing all seven `clue_check.py` gates.

---

## 1. The rarity gate does not survive a topic-defined publication

`theme_tier.py --max-reference-score 41` encodes the assumption *"a publication's own
vocabulary is nationally rare."* That is true of a **place**: Brno toponyms score 32–39
in CSTenTen, national vocabulary 42–62, and 41 sits in a clean gap.

It is false of a **topic**. LFŠ's own vocabulary is the vocabulary of cinema, and cinema
is not rare:

| word | CSTenTen | in the LFŠ corpus |
|---|---:|---:|
| `film` | 59 | 944 |
| `kino` | 49 | 214 |
| `herec` | 52 | 122 |
| `divák` | 48 | 68 |
| `projekce` | 48 | 60 |
| `plátno` | 45 | 21 |

Every one of these is above the gate and every one is exactly the theme. Run the
Metropolitan recipe unchanged and the Preferred tier for a film festival contains no
film words at all.

The right instrument is **keyness**, which `metropolitan_theme_dict.py` already computes
as `corpus_salience = 10*log10(count) - reference_score`. Two things about it are worth
recording because both cost time:

- **It is proper log-odds only up to an additive constant** `10*log10(N_corpus/N_ref)`.
  Rankings are therefore correct, but *thresholds are not portable between corpora*. On
  our 278k-token corpus against CSTenTen's implied ~6e9, the offset is **−43.4 dB**, so
  "over-represented" starts at salience −43.4, not at 0, and Metropolitan's
  `--min-salience -22` is a completely different bar on a different corpus. Anyone
  reusing a salience threshold across titles is comparing two different quantities.
- **Keyness ranks names above vocabulary.** The top of our salience list is `lfš, čro,
  filmovka, famu, ačfk, antonioni, krumbachová, polanski, blier, berlinale`. The words a
  solver actually recognises as cinema — `plátno`, `titulky`, `projekce` — sit 20 dB
  lower, mixed with generic prose. So on a topic title the mined channel supplies
  *names, initialisms and realia*, and the *domain vocabulary has to be curated*. That is
  the opposite of the Metropolitan finding, where the programmatic channel was
  competitive with taste.

**What we did instead.** Two independent channels, unioned, never merged into one
`theme_tier.py` call (that trap from the skill is real and we respected it):

- machine: salience-ranked candidates + all-caps mining + person/venue/title inventories;
- hand: `resources/lfs/curated.tsv`, 193 rows, each carrying an evidence regex that must
  match the corpus or `scripts/curated_dict.py` fails the build.

`curated_dict.py` is the generalisation of the one-off `local/rich/build_metro_short.py`:
the curated list is now **data in `resources/`**, not code in `local/`, and attestation
is enforced rather than conventional.

### The membership test needs restating for a topic title

Metropolitan's test — *would a reader recognise this word as belonging to this
publication with the clue covered?* — is still right, but on a topic title it bites in a
new place. The **most frequent LFŠ-specific tokens in the corpus are not theme words**:

```
sál 851   úvod 1052   stan 1135   host 1075   hala 332   aula 89
```

Every one of those is festival *layout* — venue labels in the programme grid. An attendee
absolutely recognises `lektorský úvod` and `stan ČT`, which makes them very tempting, and
they are exactly the scarce 3–4 letter lengths the tier is starving for. They were all
rejected: with the clue covered, `SÁL` is a room. They get LFŠ-voiced clues from the
Standard tier instead, which costs nothing. This is the same "grading the (answer, clue)
pair" trap as before, wearing a new disguise.

---

## 2. The švédská is a different structural problem, and mostly an easier one

Filmové listy runs a **švédská** — legends inside the grid — not an American grid, and it
hides a tajenka the reader mails in. Matching the format turned out to matter more than
expected, because the clue spec in `CLUES.md` was already written for a švédská (the
34-character BOX budget is a legend cell) while the delivery renderer was American. The
two halves of the system disagreed and nobody had noticed.

For the solver a švédská is just a block pattern — no engine change. The structure is:

> **Every word's legend lives in the cell before it.** Across word at `(r,c)` → legend at
> `(r,c-1)`; down word at `(r,c)` → legend at `(r-1,c)`. Therefore **row 0 and column 0
> are entirely legend cells**, and a word may never start on the top or left edge.

`scripts/swedish_grid.py --frame` enforces exactly that. Two consequences:

- **Symmetry goes away.** 180° rotational symmetry is an American convention; Filmové
  listy' grid has none. Dropping it is what made a single length-11 slot affordable —
  under symmetry every long slot forces a mirrored twin, and the ratio table prices an
  11 at about −13.8 bits against the κ\*=0.95 frontier, so `KRUMBACHOVÁ` would have cost
  ~27 bits instead of ~14.
- **Legend-cell load is a real constraint** and a cheap one to check: a cell can carry at
  most two legends (one `▶`, one `▼`). Our grid has 40 legend cells, 10 of them doubled.
  The renderer exits rather than silently dropping a legend.

The kappa surrogate is **size-independent** — its derivation only assumes a fully checked
grid, where `crossings = white` and `Σ L = 2·white`, so the block count cancels. It
transfers to 15×12 unchanged. Worth knowing before someone re-derives it.

### Recomputed d(L)/L, and a frontier that moved

On `local/longtail/longtail_22_l67c.dict` at `--min-score 30` (the enlarged Standard list
the long-tail work recommended), the ratio table is **not** the one in
`local/rich/grids/report.md`:

| L | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|
| d(L)/L | 3.30 | 3.02 | 2.66 | **2.37** | **2.20** | 1.81 | 1.58 | 1.38 | 1.19 |

Frontier 2.4435. **Length 6 moved from over the cliff to under it** (2.46 → 2.37) and 7
improved from 2.06 to 2.20 — precisely the L6/L7 band the long-tail dictionary targeted.
That is the clearest independent confirmation that the long-tail addition did what it
claimed, and it means the old advice *"every slot longer than 5 must be paid for by a
slot of length 3–4"* is now *"longer than 6"*.

---

## 3. A failure class that does not exist in an unseeded grid

Seeding two long theme entries into a template is not a placement problem, it is a
**crossing** problem, and it produced the single most expensive dead end of this run.

Seeding `ester` (5) and `krumbachová` (11) into two across rows that share columns hands
the down slot through each shared column **two** fixed letters at once. In Czech those
patterns are empty: `e...v...` and `e.c` have zero matching words in a 136k list, so
`ingrid_core` returns `Unfillable grid` at initial arc consistency, before any search.
Measured: of 16 cell-disjoint placements across 16 templates, **1** produced a fill.

Forbidding shared crossings outright is too strict — it rejected 39 of 40 templates. The
working screen is in `scripts/tajenka_place.py`: enumerate cell-disjoint placements, then
count, for every slot, how many dictionary words match the induced pattern. That is the
same unary filter the solver applies first, it costs milliseconds, and a survivor cannot
die at initial AC for a unary reason. It lifted the yield from 1/16 to 60/119.

Related, and cheap to state: **`í` is a fatal first letter.** Seeding `letní` at row 0 of
a grid that fills empty in 48.7 s made it instantly unfillable, because `í` then had to
start a down slot and Czech has no word of that length beginning with `í`. Before seeding
a word, look at where its awkward letters (`í á é ů ě`) land in their crossing slots.

Third, on the same theme: `--ignore-diacritics` folds the **entire dictionary**, not just
the grid. A subagent used it to make seeded smokes pass, and every conclusion from those
smokes was worthless — crossing domains are far larger under folding, and the delivered
grid would print unaccented. It is a rendering choice masquerading as a solver flag.

---

## 4. Preferred count is not just a weak selection criterion — it actively creates defects

Metropolitan reported that two fills at 9 Preferred hits differed enormously in quality.
The mechanism is worse than "the metric is noisy". `count_preferred_words` counts
**surface forms**, so the optimiser is directly rewarded for putting two forms of the
same lemma in one grid. Over ten unseeded fills of the same template:

| | unseeded | with 3 marquee entries seeded |
|---|---:|---:|
| Preferred count | 6–7 | 6 |
| hard defects (same-lemma collisions) | **2–4 per fill** | **0** |
| examples | `role`/`rolím`, `aul`/`aulami`, `etuda`/`etudy`, `kin`/`kinům`, `brak`/`brakům` | — |

Every unseeded fill had at least two. `--max-shared-substring 4` cannot see them:
`role`/`rolím` share three characters. This is the defect class `good-crossword` flagged
as a five-line join and it is now implemented in `scripts/fill_critic.py`, which caught
all of them on first run.

And the headline result of seeding:

> **Seeding did not raise the Preferred count. It raised the *recognisability* at constant
> count** — 3 fixed marquee entries (`KRUMBACHOVÁ`, `PROMÍTAČ`, `PLÁTNO`) replaced 3
> discovered oblique forms (`brakem`, `kamer`, `kin`). Same number, completely different
> puzzle.

That is the strongest available argument that the objective function should not be a
count of surface forms. A lemma-aware count, or a count weighted by a recognisability
grade, would change what the solver optimises rather than what we post-filter.

`scripts/theme_construct.py` is the constructive half: greedy placement of marquee
entries with the **real solver as the oracle**, which is what `theme_seed.py` could not
do. It placed 2–3 entries per template before the grid refused more.

---

## 5. The estimator: it was spending 5% of its budget

`--estimate-variants` was advertised as spending up to 45% of the search runtime. Measured
on a real 15×15 at `--estimate-runtime-ratio 0.5`:

- budget 30 s, usable 27 s after the 0.9 deadline fraction
- calibration: one deterministic incumbent-guided walk, **0.75 s** — it always reaches a leaf
- `select_cohort_size` → `floor(throughput × remaining × 0.5)` = **18 walks**
- those 18 walks took **4.1 s**, i.e. 0.23 s each, because most randomized walks are
  rejected early and never reach leaf depth

So the calibration walk over-estimates per-walk cost by ~3×, and a 0.5 safety factor
halves the result again. **Net: 4.1 s of a 30 s budget, 7% of search time.** Raising
`--estimate-walks` from 16 to 4096 changed the cohort from 16 to 18 — the flag was not the
binding constraint and the documentation implied it was.

Two changes landed in `src/variant_estimate/`:

1. the runtime-ratio clamp went 0.5 → **1.0**, since a 100% estimator budget is a
   legitimate thing to ask for;
2. the single cohort became **budget-consuming waves**: after each wave the throughput is
   re-measured from walks that actually completed, and the next wave is sized from the
   remaining time. All waves draw from one seed stream keyed by the **global** walk index,
   so walk *i* has the same seed however the waves split — verified by a test asserting
   that `[16, 284, 600]` waves give an aggregate identical to one 900-walk cohort, and by
   the pre-existing cross-worker-count reproducibility test.

Utilisation went **7.4% → 44.7%** at ratio 0.5 and **89.8%** at ratio 1.0; accepted walks
19 → 1,368 → 2,761.

### What the extra walks actually bought — and the honest answer

They did **not** tighten the interval. They **widened** it, and that is the useful result:

| walks | accepted | effective samples | estimated slack | 95% spread |
|---:|---:|---:|---:|---|
| 29 | 19 | 19.0 | 0.2 bits | −0.3 … 0.5 |
| 1,477 | 1,368 | 43.6 | 1.1 bits | 0.4 … 2.0 |
| 2,958 | 2,761 | **13.6** | 2.4 bits | 0.4 … 4.6 |

Effective sample size *fell* from 43.6 to 13.6 while the raw count doubled. That is the
signature of an importance-sampling estimator whose weight distribution is heavy-tailed:
the extra walks found rare high-weight regions the small cohort never reached, which
raised the point estimate and correctly destroyed the false precision of the 29-walk run.
The 0.2-bit answer was not merely imprecise, it was **wrong and confidently so**.

Practical guidance, which is the thing worth keeping:

- **Treat a small-cohort estimate as a lower bound, never as a measurement.** With ESS in
  the tens, a narrow interval means "the sampler has not found the mass yet".
- Watch ESS, not the walk count. Rising walks with falling ESS = still discovering.
- `--estimate-guide-probability` defaults to 0.98, i.e. the walk follows the incumbent 98%
  of the time. That is what makes the weights heavy-tailed. Lowering it should trade point
  accuracy for variance; untested here and the obvious next experiment.
- The certified lower bound (distinct fills actually seen) is the only number in the
  report that cannot be wrong. Read it first.

### A third failure of the same kind: an undocumented cap, reported 20 minutes late

`--estimate-walks 2000000` is silently invalid — `MAX_WALK_COUNT` is 100 000 — and the
only signal was `estimate: invalid options`, printed **after** the full 1 200 s search,
naming neither the option nor the limit. Three runs, an hour of wall clock. Both
`--estimate-walks` and `--estimate-guide-probability` now validate at parse time with a
message that names the bound; the range is in `--help`.

### What the estimator actually bought on this puzzle

Three configurations of the same 15×12 template, 900 s search + 810 s estimation each
(90.0% utilisation in all three, which is the fix in section 5 working as intended):

| configuration | level | certified distinct fills | estimated fills | slack | 95% spread | accepted walks | ESS |
|---|---|---:|---:|---:|---|---:|---:|
| bare grid | ≥6 pref | 2 | 2.1 | **1.1 bits** | 0.6–1.5 | 3 087 / 4 366 | 45.5 |
| tajenka only | ≥5 pref | 11 | 15.3 | **3.9 bits** | 1.1–4.8 | 4 565 / 6 204 | 5.1 |
| **shipped** (tajenka + 2 marquee) | ≥5 pref | 8 | 11.5 | **3.5 bits** | 1.8–4.3 | 16 550 / 21 872 | 8.0 |

Three things came out of this that changed what I did:

1. **The shipped configuration has on the order of ten distinct fills.** Not thousands —
   ten. That is an almost-saturated grid, and it explains an observation I had already
   made empirically and not understood: ten harvested fills at ten different seeds shared
   a *common core* (`bia`, `asu`, `umbra`, `lakros`, `dýmem`, `lupina`, `kupit` appeared
   in every single one). The estimator said, in one number, **stop harvesting** — no
   further seed was going to produce a materially different puzzle. That is the single
   most useful thing it told me, and it saved hours I would otherwise have spent
   re-rolling.
2. **Seeding two marquee entries cost 0.4 bits** (3.9 → 3.5) at equal Preferred level.
   I had assumed hand-seeding was expensive; it is nearly free, and given the section-4
   result — that seeding converts oblique junk into recognisable entries at constant
   count — it is the best trade available. Measure this before assuming it.
3. **The bare grid has 1.1 bits at ≥6.** Roughly two fills. So the extra Preferred word
   the unseeded search finds costs essentially all the remaining headroom, which is why
   those fills are the ones riddled with same-lemma collisions: at 2 fills the optimiser
   has no room left to be choosy.

On ESS: the well-behaved configuration (bare, ESS 45.5) is the one with the *fewest*
accepted walks, and the shipped one burned 16 550 walks for ESS 8.0. ESS is not a
function of effort, it is a property of the weight distribution, and a tight grid has a
worse one. Read the certified lower bound first, the interval second, and the point
estimate last.

---

## 6. What landed, and what it is worth reusing

| file | what it does | reusable beyond LFŠ |
|---|---|---|
| `scripts/curated_dict.py` | attested hand-curated tier from a TSV; build fails on unattested rows | yes, publication-neutral |
| `scripts/swedish_grid.py` | švédská templates, `--frame`, arbitrary size, no symmetry | yes |
| `scripts/tajenka_place.py` | domain-screened placement of multi-part seeds | yes |
| `scripts/theme_construct.py` | greedy marquee seeding with the solver as oracle | yes |
| `scripts/fill_critic.py` | per-entry verdicts, same-lemma collisions, theme spread, multi-term score | yes |
| `scripts/clue_check.py` | the `CLUES.md` §11 step-3 kontrolor, all seven checks | yes |
| `scripts/lfs_grid11.py` | plants long runs then anneals around them | narrow but the trick generalises |
| `.omp/skills/crossword-email` | `--layout swedish`, `--tajenka` shading | yes |
| `resources/lfs/*` | curated tier, denylists, marquee list | LFŠ-specific by design |

### Next, roughly by value

1. **Make the objective lemma-aware.** `count_preferred_words` counting surface forms is
   what produces `role`/`rolím`. Counting distinct *lemmas* would remove the defect class
   at its source instead of post-filtering it. This is the single highest-value change.
2. **Weight the objective by recognisability.** The tier already carries a hand score
   (200 = unmistakable, 150 = solidly thematic) and the solver ignores it entirely.
   Maximising Σ score instead of count would make seeding unnecessary.
3. **Sweep `--estimate-guide-probability`.** The heavy tail is a knob, not a fact.
4. **Joint grid+theme search.** Still open from the Metropolitan notes, and the švédská
   makes it more attractive: without symmetry the template space is far larger and the
   long-slot cost halves.
5. **A diversity-aware supply metric.** Unchanged from `good-crossword`: κ is structurally
   blind to letter-pattern clustering, so it can compare grids at a fixed dictionary but
   not dictionaries.
