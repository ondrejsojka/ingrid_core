---
name: good-crossword
description: Notes on generating a recognizably magazine-tailored Czech crossword with ingrid_core — which knobs actually move theme density, sane defaults, the traps that ate a day, and what to try next. Use when tuning a fill, building a theme wordlist for a publication, choosing a grid, or judging whether a fill is good.
---

# Making a crossword that reads like it belongs to the magazine

Status: working notes, not a spec. Everything here was measured once, on one title
(Brnensky Metropolitan 7-8/2026) in one language. Numbers are real but the sample is
one. Treat the causal claims as "this is what happened when I pulled the lever", not
as laws. `theme-density.md` in the repo root has the long version with the raw runs.

Read `CLUES.md` before writing any clue. That is the editorial spec and it is better
thought-through than anything here.

## The one thing to internalise

Ingrid's Preferred tier **is** the objective function. `parallel_search` maximizes
`count_preferred_words` and nothing else. So:

> Every entry in the Preferred tier that a reader would not recognize as belonging to
> this publication spends the objective on nothing.

I lost most of a day to not taking this seriously. The old 447-entry theme list was 7%
recognizable, so the solver was cheerfully optimizing a metric that had almost no
relationship to the thing being judged. The sharpest demonstration: inflating that same
list with morphology took the Preferred count from 1 to 6 and the *recognizable* count
from 0 to 0 — the six hits were `nesokoly`, `romanem`, `milena`, `rokytovu`, `radek`,
`marty`. Five given names and a negated form. Metric up sixfold, product unchanged.

Corollary that keeps biting: **theme count is a bad fill-selection criterion.** Two
fills at 9 Preferred hits differed enormously in quality. See the critic sketch below.

## Rough workflow

1. Extract theme candidates from the issue/archive — `metropolitan_theme_dict.py`.
   Corpus work, no taste involved. Set `--min-length 3`, see below.
2. **Grade** them for recognizability — `theme_tier.py`. This is the step that did not
   exist and mattered most.
3. **Expand** to surface forms — `theme_expand.py`. Czech fills need oblique cases.
4. Fact bank — `number_facts.py`. Feeds *clues*, deliberately not the Preferred tier.
5. Pick a grid on its length histogram — `fill_margin.py` + the ratio table below.
6. Fill — `ingrid_core --preferred-wordlist`.
7. Write clues over the whole grid at once, then run the sec. 11 step-3 checks.
8. Render/send — the `crossword-email` skill next door.

Steps 2 and 5 are where the wins were. Step 3 is necessary and not sufficient. Step 1
is where most of the *candidates* come from and is the least interesting.

## Knobs, defaults, and how to tune them

### `theme_tier.py` — precision. Biggest lever.

| knob | default I'd keep | why / how to move it |
|---|---|---|
| `--max-reference-score` | **41** | Rarity cut against a national corpus. Brno toponyms score 32-39 in CSTenTen, national/international 42-62. Clean gap; 41 sits in it. For another title, probe ~10 known-local and ~10 known-national terms and read the boundary off. Don't guess. |
| `--trust-input-score` | **200** | The upstream builder's own salience. On Metropolitan the >=200 slice is 47 entries and essentially pure Brno; the 150-165 bulk is national vocabulary. **This signal already existed and was being thrown away by flattening to a binary tier.** Check your own list's score histogram before picking a number. |
| `--keep-classes` / `--drop-classes` | `GKRmgb` / `YSE` | MorfFlex `_;X` markers. Drop given names and surnames — a city magazine's most *distinctive frequent* tokens are the people it interviews, which is why 195 of 447 were first names. Important detail: a drop class must only bite when **no** keep class is present, because Czech toponyms are constantly also surnames (Petrov, Slatina). |
| `--mine-acronyms` | **on** | The only source of 3-4 letter theme entries. Reject a candidate if it also analyses as an ordinary word (`AKCE`, `CENU`, `DALSI` are headline capitals, not initialisms). |
| `--corpus` | always pass it | Enables the attestation test: a no-paradigm entry must occur as a standalone token. Without it, PDF column-break fragments (`sportov`, `jihomo`, `metropo`, `piler`) sail through, because they pass every morphology check precisely by having no morphology — the same signal that admits `DPMB`. |
| `--keep-common` | on, with the rarity cut | Lets nationally-rare common nouns in, which is the hantec/dialect class (`salina`, `statl`). |

### `theme_expand.py` — supply. Necessary, not sufficient.

| knob | default I'd keep | why |
|---|---|---|
| `--allowed-variants` | **`-`** (not `-1`) | The Standard-tier convention accepts variant `1`. On proper names that admits colloquial obliques like `veverima`. Tighter is better here. |
| lemmatize unexpandable inputs | **on** | A list harvested from running text is not all lemmas. `bohunicich`, `cejlu`, `luzankami`, `zidenic`, `masarykovy` cannot be generated *from*. Analysing them back to `Bohunice_;G` etc. recovers both the base form the list was missing and the paradigm: 775 → 975 forms, unanalyzable residue 55 → 24. |
| `--literal` | use it for initialisms **and** idiom forms **and** indeclinable street names | `MUNI` has a MorfFlex paradigm, so it declines to `MUNU`. Same for `v cudu`/`slus` (one fixed shape inside a set phrase) and `na Orli`/`na Veveri`. |
| number lock on proper names | on | `Brno` is singular, MorfFlex generates a plural paradigm anyway; `Zabovresky` is genuinely plural-only. Compare each number's nominative against the paradigm's lemma. **Anchor on the recovered lemma, not the input word** — `brna` matches the plural nominative of `Brno` and otherwise readmits `brn`, `brnech`. |
| negation + grade filters | on | Otherwise `Sokol` → `nesokoly` and `sumavsky` → `nejsumavstejsi` among 524 forms. |

### `ingrid_core`

| knob | default I'd keep | why |
|---|---|---|
| `--max-shared-substring` | **4**, not 5 | 5 lets `luzanky` + `luzanek` coexist. 4 blocks that and cost nothing measurable. It still does **not** catch `opat`/`opatem` or `kope`/`kopali`, whose shared run is only 4. |
| `--cores` | 5 was plenty | 8 theme words at 92 s on 5 cores. Ten cores for 1800 s got 9-10 — steeply diminishing. Most of the gain arrives in the first 30-60 s; the tail is one worker grinding at target N+1. |
| `--timeout` | 600-900 s | Watch `--search-log` incumbent timestamps rather than waiting: on a good grid 7-8 arrive inside 30 s and the rest is tail. |
| `--min-score` | 30 for L3-5, **~22 for L6-L7**, don't bother at L8 | Swept. See "The long-tail sweep" below — worth ~1 theme word, and there is a free win before you touch the bar at all. Beware: the Standard list's scores are cstenten + a canonical bonus, so `--min-score 30` on a rebuilt list silently discards most additions. |

### Grid choice — second biggest lever, and cheap

The derivation that made this tractable (credit to a subagent, verified on 11 grids):
in a **fully checked** grid every white cell is a crossing, so `crossings = white =
225 - blocks`, and since `sum L_s = 2 * white` the block count cancels:

```
kappa  ~=  C / (2 * alpha * <d(L)/L>)     C = 4.5861 bits/crossing, alpha = 0.9878
```

**kappa is a pure function of the slot-length histogram.** Surrogate RMSE 0.006 against
real `fill_margin.py` over 41 grids, which is what made annealing over grid patterns
affordable. `d(L)/L` on a 129k-word Czech list:

| L | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 12 |
|---|---|---|---|---|---|---|---|---|---|
| `d(L)/L` | 3.30 | 3.00 | 2.66 | 2.33 | 2.06 | 1.81 | 1.58 | 1.38 | 1.03 |

Frontier at `kappa* = 0.95` is **2.443**, between length 5 and 6. Read that as: a grid
of all 6s is already over the cliff, and **every slot longer than 5 must be paid for by
a slot of length 3-4.** Practical consequences:

- Long slots are where the recognizable theme words live *and* the expensive ones. No
  free direction. Blocks are the currency of headroom.
- The old grid's two 12s and two 10s were its worst feature — dropping them lowers kappa
  *and* the short-slot share simultaneously, which is not a tradeoff.
- Cap length-5 slots near 30. `--max-shared-substring` is **not priced by kappa** and is
  decisive at 60 s: kappa 0.9212 with 34 fives did not fill, kappa 0.9157 with 30 fives
  filled in 3.9 s.
- Negative result worth not rediscovering: exhaustive DFS finds **no 15x15 fully-checked
  symmetric grid with minimum run 4**, for any run set from {4,5,6} to {4..10}. The
  3-letter slot is unavoidable at this size. Hence initialisms are not optional.

What the change bought: baseline kappa 0.9363 / 74 slots / 46% short → `g09` 0.9157 /
70 slots / 28.6% short, and 6 → 9 theme words on **a third** of the cores.

### The long-tail sweep — and why kappa lied about it

Question: does lowering the CSTenTen frequency bar for **long words only** buy headroom?
Measured on `g09` against `local/cstenten.dict` (559,565 entries, of which the Standard
list keeps 129,350). Artifacts in `local/longtail/`.

Answer: yes, about **one theme word**, and there is a free win first.

| candidate | words added | kappa | delta | ~theme words |
|---|---:|---:|---:|---:|
| base | 0 | 0.9157 | — | — |
| score 30-32 inflected forms, L6-L7 | 9,063 | 0.9105 | 0.0052 | 0.3 |
| **bar 22, L6+L7, junk-gated** | 33,821 | **0.8999** | **0.0158** | **0.9** |
| bar 17, everything | 110,073 | 0.8893 | 0.0264 | 1.6 |

- **Break-even predictions held.** L6 reachable (26,391 at bar 24 vs 25,880 needed; ceiling
  1.41x at the corpus floor). L7 ceiling **0.42x**. L8 ceiling **0.099x**. So drop L8
  entirely — it cannot be fixed by vocabulary in this language, at any bar.
- **Free win, take it unconditionally.** 18,742 length-6/7/8 entries score 30-32 and are
  missing from the base *only* because the original recipe used
  `--min-noncanonical-score 33`, which excluded inflected forms of perfectly common
  lemmas. 16,272 survive the quality gate. That is a third of the total available gain for
  a quarter of the words, at **zero** frequency-quality risk. It is a recipe bug, not a
  bar drop.
- **Diminishing hard.** Gain per 1,000 words added: 0.71 milli-kappa at L6-only/bar 24,
  0.47 at bar 22, **0.24** at bar 17. Going deep costs efficiency and doubles the junk
  rate (machine-detectable junk 4.3-5.1% in the base vs 9.5-11.3% in bar-22 additions).
  Refusals in the test fill: `zalomen`, and near-misses `pilina`, `odrodit`. Note the base
  already ships `novej`, `pude`, `nar`, `ukraden` — worse in degree, not in kind.

**The methodological finding, which matters more than the dictionary.**
I predicted the measured kappa gain would undershoot the nominal count-based gain, because
rare long words cluster into paradigms of one stem. The letter-pattern discount **is real
and large** — at L8/bar 22, word count goes up 2.42x while distinct 4-grams go up only
1.60x, a 0.42 discount, worsening monotonically with length and bar depth. But
measured/nominal kappa ratio came out **1.01-1.05, i.e. no discount at all.**

> **kappa is structurally blind to letter-pattern diversity.** `domain_bits` is a raw
> post-arc-consistency count, and the crossing term is a per-position marginal collision
> probability which clustering actually *raises* — lowering kappa. So a dense-but-clustered
> dictionary looks *better* to kappa than it is.

The predicted ordering does show up (canonical-only additions 1.20, mixed 1.01-1.05,
inflection-only 0.96) but at ~40x too small a magnitude to matter. Practical consequence:
kappa is fine for comparing **grids at a fixed dictionary**, which is all we used it for.
It is the wrong instrument for comparing **dictionaries**, and a diversity-aware measure
(distinct n-grams per length, or effective domain size) is needed before trusting any
"bigger dictionary" claim.

My stated diagnostic was also wrong: I expected added-L8 lemma/form ratio ~3, measured
1.17-1.85, *below* the base's own 1.76. Rare words are rare precisely because only one
paradigm member ever occurred. The clustering is against the **base**, not within the
additions — 52-69% of added L8 forms are inflections of a lemma already present. Wrong
axis measured.

**Landmine worth its own line.** The Standard list's scores are `cstenten + 20` for
canonical forms (`--canonical-bonus 20`), so its minimum output score is 33. Running the
enlarged dictionaries at `--min-score 30` would silently discard 90,083 of 110,073
additions and produce a clean null result that reads as "the long tail doesn't help".
The base recipe, previously unrecorded, reconstructs as
`--min-score 30 --min-noncanonical-score 33 --canonical-bonus 20 --allowed-pos NAVD`
plus the three marked-class exclusions — 129,267 of 129,350 entries with zero score
mismatches.

## Numbers to regress against

Same engine, same clue spec, one title.

| | before | after |
|---|---|---|
| Preferred hits in fill | 2 / 74 | 8-10 / 70 |
| reader-recognizable | **1** | **8** |
| time to first theme word | 296 s | ~2 s |
| theme tier size | 447 lemmas | ~200 lemmas → 875 forms |
| tier entries at length 3 / 4 | 0 / 2 | 14 / 25 |
| fill defects | 5 / 74 (7%) | 7 / 70 (10%) |

That last row is a **regression** and shouldn't be swept under the rug. More theme words
came with more junk.

## Traps I actually fell into

- **Two input lists whose score columns mean different things.** Merging the
  salience-ranked corpus list and a hand-curated list into one `theme_tier.py` call is
  the obvious simplification and it silently guts precision: `--trust-input-score 200`
  then waves through `cena`, `den`, `kino`, `kolo`, `drak`, `pop`, `vila`. Measured: the
  merged call reaches the *same* 8 Preferred hits of which **2** are recognizable. Same
  metric, quarter of the product. Two separate calls, union the outputs.
- **Grading the (answer, clue) pair instead of the bare answer.** A 1,312-entry tier hit
  nine Preferred words: `popa`, `opatem`, `arena`, `hora`, `paro`, `opat`, `kinu`,
  `aren`, `hala`. Each has a real Brno hook *in its clue*. In the grid they read as
  nothing. Test for tier membership: **would a reader recognize this word with the clue
  covered?** `HORA` no. `LUZANEK` yes.
- **Trusting arc consistency as a screen for seeded grids.** 25 of 25 three-word seedings
  passed `fill_margin` kappa; the real solver rejected every one. Ingrid's own initial
  consistency also propagates dupe and shared-substring eliminations. If you screen
  seeded templates, screen with `ingrid_core` — and distinguish "Unfillable grid"
  (a proof) from a timeout (a budget statement), or you'll report a grid as saturated
  when it's merely slow.
- **Seeded kappa is only comparable at equal seed count.** Pinned letters shrink
  `domain_bits`, which inflates kappa mechanically. Don't compare a 3-seed number to an
  unseeded one and conclude anything.
- **`--estimate-variants` can't answer this.** Returns `insufficient evidence, 0/8
  accepted SMC replicates` at preferred >= 5, >= 3 and even >= 0. The SMC path uses a
  fixed `minimum_walks.max(4)` = 8 replicates regardless of budget, so it burned 117 s
  of an allowed 600 s and stopped. More time cannot help until the replicate count
  scales with the budget.
- **Curated data under `local/`.** `local/` is gitignored. Hand-authored allowlists are
  inputs, not artifacts — they belong in `resources/`.

## Clue-writing, the parts that went wrong

The rules are in `CLUES.md`; these are the failure modes it doesn't spell out and that
a reviewer caught in my set.

- **Bare relational adjectives are not answers.** I clued `ZELNEM` as `kde stoji Parnas?`.
  The honest answer to a `kde` question is *na Zelnem trhu* — a prepositional phrase.
  `zelnem` alone is a fragment of a multi-word name. And it wasn't a length problem:
  `Parnas stoji na … trhu` is 23 characters, well inside the 34 budget. I reached for
  tazaci because sec. 4 says it fixes case agreement, but case agreement was never the
  issue — a **missing head noun** was.
  Rule of thumb that seems to hold: **`ci` licenses a bare possessive adjective**
  (`PETROVA` ← `ci je katedrala?` is fine); **nothing licenses a bare relational
  adjective** — use vypustka to supply the noun. Inflection expansion produces these
  fragments constantly, so expect them.
- **Postmortem worth more than the clue: I conceded a defect that wasn't one.** A
  reviewer flagged `TRIAL` clued as `motorka v kameni` as wrong — "trail, not trial".
  I agreed immediately and wrote it up as a fairness bug. Both of us were wrong. Moto
  trial *is* the sport of riding a motorcycle over rocks, so the clue is accurate, and
  `trail`/`trial` are unrelated words rather than spelling variants. Worse, the fairness
  worry doesn't survive ten seconds of checking: `TRIAL` sits at column 3 rows 0-4 and
  every letter is checked — `t` by ZTP, `r` by UBERE, `i` by POLIT, `a` by SUNAR, `l` by
  ADELOU. TRAIL would need POLIT to end `-alt` and SUNAR to read `sunir`. The crossings
  pin it; the ambiguity cannot reach the solver.
  Three things to take from this. (1) **In a fully checked grid, same-length lookalikes
  are resolved by crossings.** Don't build a near-homograph filter for an American grid;
  it's a Swedish-grid concern where letters go unchecked. (2) **Verify before conceding.**
  A reviewer's "this is wrong" is evidence about *their* knowledge, which is valuable,
  but it is not the same as evidence about the answer. I had the grid in hand and didn't
  look. (3) The signal in the complaint was real but pointed elsewhere: an answer the
  reviewer didn't know is a statement about **difficulty band and anchoring**, not
  correctness. `motorka v kameni` gives no anchor for someone who's never heard of trial
  bikes; sec. 5's "kotva prvni, pointa druha" would suggest leading with the familiar
  sense (`zkouska i motorka v kameni`) so the unknown one lands as the payoff.
- **Compression is mandatory, not taste** (sec. 6). My median started at 16 and only the
  checker caught it. `rozpocet, o kterem hlasuji lide` (31) → `rozpocet podle lidi` (19);
  `vede modlitbu v mesite` (22) → `vede modlitbu` (13).
- **Band and crossing rules are invisible by eye.** `LAM` had all three crossings in band
  H — a sec. 9 rule-1 violation I'd have shipped. Compute them from the crossing graph.
  In my case the fix was reclassifying one neighbour from H to O honestly, not rewriting.
- Write clues **over the whole grid at once**, never one at a time (sec. 11). You cannot
  satisfy the band mix or the shape-dispersion rule locally.

## The critic idea — worth building, sketch only

Selecting fills by Preferred count is clearly wrong; two 9-hit fills were far apart in
quality. What a grader should produce, roughly: a per-entry verdict plus a fill score
that is **not** a theme count.

Do the cheap deterministic things first and only spend a model on the residual:

1. **Deterministic pre-filters** (no model, catch most of what I shipped):
   - not present in any reference corpus above a floor → suspect (`rop`, `dom`)
   - no MorphoDiTa analysis and not attested in the issue → fragment
   - same lemma or stem as another entry in the same fill → hard defect. This catches
     `kope`/`kopali` and `luzanky`/`luzanek`, which `--max-shared-substring` cannot.
     `theme_expand.py --report` already carries the lemma of every generated form, so
     this is a join, not research.
   - bare relational adjective, or a form whose corpus occurrences are overwhelmingly
     preceded by a preposition → needs a vypustka clue or shouldn't be there
   - answer whose *unchecked* positions permit a different real word → fairness risk.
     Only meaningful where letters are unchecked; in a fully checked grid there are
     none, so this is a Swedish-grid concern, not an American-grid one. See the
     `trial`/`trail` postmortem below before building it.
2. **Then the model, per entry, with a forced format**: `{answer, verdict:
   ok|risky|defect, reason, confidence}`. Block on `defect & high confidence`. The ones
   I want caught are `ladin`, `manas`, `roba`, `dom`, `rop` — all of them are "attested
   somewhere but no honest clue exists", which is a judgement call a model is actually
   suited to and a regex is not.
3. **Fill-level score**, deliberately multi-term: recognizable-theme count, defect count,
   worst-case entry badness, crosswordese density, and how evenly theme words are spread
   over the grid (eight theme words in one corner is worse than eight spread out — I
   never measured spread and probably should have).

Open questions I don't have answers to: how to keep the model's verdicts stable enough
to compare two fills; whether to grade answers only or (answer, candidate clue) pairs;
and whether a cheap model on 70 entries is good enough or it needs the slow one. Also
suspect there's a self-consistency trick available — the same model that writes the clue
shouldn't be the only one judging it.

## How much of this is programmatic vs. a model's opinion

Worth being honest about, because it changed during the work and will change again.

- **Standard tier** (62 of 70 entries in the delivered fill): entirely corpus-derived,
  CSTenTen → `cstenten_wls_to_dict.py` → `czech_standard_dict.py`. No model.
- **Theme candidates**: ~153 of ~200 surviving lemmas came from
  `metropolitan_theme_dict.py` over the PDF archive — MorphoDiTa tagging, document
  recurrence, salience against a national corpus. Programmatic.
- **~47** came from a model-curated short list, though every issue-derived entry there
  carries a regex that must match the issue text or the build fails. So: model-proposed,
  machine-attested.
- **Gates** (rarity, semantic class, salience cut, acronym mining, attestation): all
  programmatic.
- **~105 lines hand-authored** across allowlist/initialisms/denylist. Mostly *rescue*
  rather than generation — `BRNO` scores 57 nationally so the rarity gate drops it;
  `MASNA` and `PRYGL` parse as surnames.

The part that surprised me: the two entries the reviewer liked best, **`PARO`** and
**`STETL`**, both came out of the *programmatic* channel (all-caps mining plus the
attestation test), not from curation. The model's own short list contributed `ZTP`,
`LIPKA`, `PITKA`. So the mechanical channel is currently competitive with taste, which
argues for pushing more work into gates and less into curation.

Direction of travel: shrink the hand-authored files, and replace the curated short list
with a mined one (all-caps + high-salience short tokens + issue attestation) so the
model's role moves from *proposing vocabulary* to *rejecting bad answers*, which is the
critic above.

## Next steps, roughly by effort

**Low.**
- Same-lemma collision check over `(fill, theme_expand --report)`. Five lines, catches a
  defect class that shipped twice.
- Flag answers whose meaning is niche enough to need an anchor (loanwords, sports terms,
  brands). Not a fairness check — crossings handle fairness in a fully checked grid —
  but a **band assignment** check: these belong in H, or want sec. 5's familiar sense
  leading. This is the salvageable half of the `trial` postmortem.
- Adopt the score-30-32 free win from `local/longtail/adds_30nc.dict` (recipe-bug fix, no bar drop). Then a diversity-aware supply metric, since kappa cannot see letter-pattern clustering.
- Report theme-word **spread** over the grid, not just count.

**Medium.**
- The critic above, starting with the deterministic filters only.
- Lemma-aware duplicate constraint in `dupe_index.rs`. The right home for
  `kope`/`kopali`; `--max-shared-substring` structurally cannot express it.
- Make the SMC replicate count in `variant_estimate.rs` scale with the remaining budget,
  so `--estimate-variants` becomes usable on grids this hard.
- Consume the two-cell legend capacity. `g09` has 34 adjacent block pairs against the
  old grid's 16, which `CLUES.md` sec. 2 values at 70 characters instead of 34. Nothing
  downstream uses it yet, so the grid is paying for a feature no one spends.

**Larger, and speculative.**
- Turn `theme_seed.py` into a constructor. Today its placement is greedy and doesn't
  backtrack, so it characterises a template honestly but can't build a high-theme fill
  the way a human constructor would (theme entries first, grid around them). Backtracking
  over placements with the solver as oracle is the obvious shape.
- Joint grid+theme search. Currently the grid is chosen, then filled. The histogram
  analysis says the two are tightly coupled, so choosing the grid *for a given theme
  vocabulary's length profile* should beat choosing it for generic kappa.
- Try a second title. Every publication-independence claim here is untested — one city
  magazine, one language. The rarity-gap argument should hold anywhere ("a publication's
  own vocabulary is nationally rare") but I'd want to see the CSTenTen-equivalent
  histogram for a second title before believing the 41 threshold transfers.
