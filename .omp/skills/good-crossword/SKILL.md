---
name: good-crossword
description: Notes on generating a recognizably publication-tailored Czech crossword with ingrid_core — which knobs actually move theme density, what changes when the publication is defined by a topic or by a person rather than a place, when to build the template around the theme words instead of filling one, how to build a švédská instead of an American grid, sane defaults, the traps that ate a day, and what to try next. Use when tuning a fill, building a theme wordlist for a publication, choosing a grid or grid genre, seeding theme entries, judging whether a fill is good, or reading a slack estimate.
---

# Making a crossword that reads like it belongs to the magazine

Status: working notes, not a spec. Three titles now: **Brnensky Metropolitan 7-8/2026**
(city magazine, American grid), **LFŠ / Letní filmová škola 2026** (film festival daily,
švédská) and **Karolína 2026** (a birthday puzzle for one reader, švédská). Numbers are
real but each was measured once. Treat the causal claims as "this is what happened when I
pulled the lever", not as laws. Long versions with raw runs: `theme-density.md`
(Metropolitan) and `lfs-notes.md` (LFŠ) in the repo root; the third title's artifacts are
under `local/karolina/`.

The single most useful thing the second title taught: **decide what kind of publication
you have before you touch a gate.** Sec. "Place titles vs topic titles" below. Almost
everything that failed to transfer failed there.

The third title moved the decision one level up. **Before choosing gates, decide whether
you are filling a template or building one.** With a small theme vocabulary the Preferred
tier cannot win against a fixed topology no matter how well graded it is, and the whole
gate apparatus above is beside the point. Sec. "Person titles" below.

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

**LFŠ made this sharper: the count doesn't just select badly, it *manufactures* defects.**
`count_preferred_words` counts surface *forms*, so the optimiser is directly paid to put
two forms of one lemma in the same grid. Over ten unseeded fills of one template, **every
single one** had 2-4 same-lemma collisions — `role`/`rolím`, `aul`/`aulami`,
`etuda`/`etudy`, `kin`/`kinům`, `brak`/`brakům`. `--max-shared-substring 4` cannot see
them (`role`/`rolím` share three characters). Seeding the same template with three
marquee entries produced **0 collisions in all five fills at the same Preferred count**.

So the fix is not a better post-filter. Two changes to the objective would remove the
class at its source, and both are small:
- count distinct **lemmas**, not forms;
- or maximise **Σ score** rather than count. The tier already carries a hand grade
  (200 = unmistakable, 150 = solidly thematic) and `parallel_search` throws it away.
  This would make marquee seeding unnecessary.

## Rough workflow

0. **Classify the publication and the grid genre.** Place-defined, topic-defined or
   person-defined? American or švédská? Both answers change gates and grid constraints
   downstream, and both are cheap to get wrong for a whole day. Three sections below.
   If the theme list is finite and pre-clued (a person title), **stop here and jump to
   the "Person titles" section** — steps 1-4 do not apply and step 5 inverts.
1. Extract theme candidates from the issue/archive — `metropolitan_theme_dict.py`.
   Corpus work, no taste involved. Set `--min-length 3`, see below.
2. **Grade** them for recognizability — `theme_tier.py` for the mined channel,
   `curated_dict.py` for the hand channel. This is the step that did not exist and
   mattered most. Never one call over both — see the traps.
3. **Expand** to surface forms — `theme_expand.py`. Czech fills need oblique cases.
4. Fact bank — `number_facts.py`. Feeds *clues*, deliberately not the Preferred tier.
5. Pick a grid on its length histogram — `fill_margin.py` + the ratio table below,
   or `swedish_grid.py` for a švédská.
6. **Seed the marquee entries** — `tajenka_place.py`, then `theme_construct.py`. New
   step, and the one that converts count into recognisability. Was not in v1.
7. Fill — `ingrid_core --preferred-wordlist --grids N --grids-dir out/`. One invocation
   harvests the distinct certified fills; `--seed` reruns are dead (default 0 is
   byte-identical bait) and `fill_critic.py` grades the files directly.
8. **Grade the fills** — `fill_critic.py`. Pick on quality, never on count.
9. **Measure the slack** — `--estimate-variants`. It tells you when to stop harvesting.
10. Write clues over the whole grid at once, then `clue_check.py`.
11. Render/send — the `crossword-email` skill next door.

Steps 0, 2 and 5 are where the wins are. Steps 6 and 8 are new and both earned their
place. Step 3 is necessary and not sufficient. Step 1 is where most of the *candidates*
come from and is the least interesting. On a person title, step 0 decides everything and
the rest of the list collapses to 5-8-10-11 with step 5 replaced by `theme_grid.py`.

## Place titles vs topic titles — decide this first

`theme_tier.py --max-reference-score 41` encodes an assumption that is easy to miss
because on the first title it is simply true:

> a publication's own vocabulary is nationally rare.

True of a **place**. Brno toponyms score 32-39 in CSTenTen, national vocabulary 42-62,
and 41 sits in a clean gap. **False of a topic.** LFŠ's own vocabulary is the vocabulary
of cinema, and cinema is common: `film;59 herec;52 kino;49 divák;48 projekce;48
plátno;45`. Every one is above the gate and every one is exactly the theme. Run the
Metropolitan recipe unchanged on a film festival and the Preferred tier contains no film
words at all.

For a topic title the instrument is **keyness**, which `metropolitan_theme_dict.py`
already computes as `corpus_salience = 10*log10(count) - reference_score`. Two properties
of it cost real time:

- **It is proper log-odds only up to the additive constant** `10*log10(N_corpus/N_ref)`.
  Rankings are therefore fine, but **thresholds are not portable between corpora**. On a
  278k-token corpus against CSTenTen's implied ~6e9 the offset is **-43.4 dB**, so
  "over-represented" begins at salience -43.4, not 0. Metropolitan's `--min-salience -22`
  is a different bar on a different corpus, not a default. Compute your own offset from
  the corpus sizes before choosing a cut, and probe ~10 words you *know* are thematic.
- **Keyness ranks names above vocabulary.** Our top salience was `lfš, čro, filmovka,
  famu, ačfk, antonioni, krumbachová, polanski, blier, berlinale`. The words a solver
  recognises as cinema — `plátno`, `titulky`, `projekce` — sit ~20 dB lower, mixed into
  generic prose. So on a topic title the **mined channel supplies names, initialisms and
  realia, and the domain vocabulary must be curated by hand.** That is the *reverse* of
  the Metropolitan finding, where the mechanical channel was competitive with taste.
  Do not conclude from one title which channel to invest in.

### The membership test in its new disguise

The test is unchanged — *would a reader recognise this word as belonging to this
publication with the clue covered?* — but on a topic title it bites somewhere new. The
**highest-frequency publication-specific tokens are not theme words**:

```
sál 851   úvod 1052   stan 1135   host 1075   hala 332   aula 89
```

All festival *layout* — venue labels in a programme grid. An attendee genuinely
recognises `lektorský úvod` and `stan ČT`, and they sit at exactly the scarce 3-4 letter
lengths the tier is starving for, which makes them very tempting. Reject them anyway:
with the clue covered, `SÁL` is a room. Give them LFŠ-voiced clues from the Standard
tier instead — that channel is free and carries a surprising amount of the theme.

### Attestation as a build gate, not a convention

`scripts/curated_dict.py` generalises the one-off `build_metro_short.py`: the curated
list is now **data** (`resources/<pub>/curated.tsv`), each row carries an evidence regex,
and the build **fails** if a row does not match the corpus. 193 rows for LFŠ, zero
unattested. Keeping this honest is what stops a model from quietly inventing vocabulary
that "feels" like the publication.

## Person titles — when the theme list is finite, build the template, don't fill one

The third title is a birthday puzzle for one reader. The "publication" is a person, the
theme list arrived hand-written as `word<TAB>clue`, 81 rows / 79 unique forms, and **every
entry already had its clue**. So steps 1-4 of the workflow above — mine, grade, expand,
fact-bank — all evaporate. There is nothing to mine, grading is what the author already
did by writing the list, and **expansion is impossible on principle**: each clue is bound
to its exact surface form, so `theme_expand.py` would produce forms with no clue attached.

That leaves placement, and placement turns out to be the whole game.

### The arithmetic that forces the inversion

`parallel_search` maximizes `count_preferred_words` over a **fixed** topology. LFŠ got
5 hits in 50 slots from **1 240** preferred forms. Scaling that down to 79 forms predicts
roughly 2-4 hits in a normal švédská, which for a personal gift is a null result. No
amount of tier grading fixes it, because the tier is already perfect — every single entry
is maximally recognizable to the one reader who matters.

So invert: **build the template around the theme words and let ingrid fill only what is
left.** `scripts/theme_grid.py` does this — randomized greedy plus iterated local search
over classic criss-cross placements, švédská legality enforced by construction (no word
starts in row 0 or column 0; every other legend cell is a block automatically, and two
words can never contend for the same legend in the same direction, so the renderer's
conflict exit is unreachable by construction). Result on the same 79 words:

| approach | theme entries in grid |
|---|---:|
| fixed švédská template + Preferred tier (the LFŠ recipe, extrapolated) | ~2-4 |
| template built around the theme words | **79 / 79** |

This is the "joint grid+theme search" item that had been sitting in Next steps as
*Larger, and speculative*. The cheap version — greedy + ILS with a bitset unary screen —
took an afternoon and saturates the word list. It is not speculative any more.

### Theme count is very nearly linear in grid area

This is the only lever that moves the number by more than one, and it moves it a lot.
Pure-theme fills, matched budget (100 s, 8 workers, one component):

| grid | 19² | 21² | 23² | 25² | 27² | 29² | 31² | 32² | 35² |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| theme entries placed | 37 | 44 | 50 | 57 | 64 | 71 | 78 | **79** | 79 |

About **+3.4 entries per +1 of side length** over the whole range, then a hard ceiling at
the size of the word list. Read it the other way round: *choose the grid by how much of
the list you insist on shipping.* Wanting all 79 costs a 32×32; 27×27 buys 81 % of the
list in a grid with 44 % of the area.

That ceiling is a property of the packing, not of the search budget. Nine hundred seconds
on nine workers at 31×31 — **10 532 restarts**, ~4× the budget that produced the 240 s
column — still returns 77. The greedy+ILS neighbourhood is saturated well before the
time is. So if you want all 79 in less area, the thing to improve is *crossings per word*
(2.35 at 32×32), not seconds.

κ is not the instrument here and does not apply — there is no dictionary-vs-topology
tension to price when the "dictionary" for a slot is one specific word.

### …and all of that evaporates the moment you cap empty cells

The table above is what "maximize theme entries" gets you when nothing else is
constrained, and it is worth reading as a cautionary result rather than a recipe. Ask an
optimiser for theme count and it will spend *area* to buy it: the 32×32 grid that holds
all 79 is 60 % empty, which is a word-search collage, not a crossword. The brief that
followed — **15×15, at most 10 % empty cells (legend cells don't count as empty), at most
80 % of answers thematic** — is the one that describes an actual puzzle, and under it the
numbers collapse:

| regime | grid | answers | theme | share | empty |
|---|---|---:|---:|---:|---:|
| unconstrained, template built around the words | 32×32 | 79 | **79** | 100 % | ~60 % |
| 15×15, ≤10 % empty, ≤80 % theme | 15×15 | ~61 | **~6-8** | ~11 % | 9 % |

An order of magnitude, from one geometry constraint. Things learned paying for it:

- **The sparse criss-cross rule cannot reach 10 % empty, structurally.** "A non-crossing
  cell has empty perpendicular neighbours" *manufactures* empty cells; the grids it makes
  sit at 23-24 % empty and a dead-block-targeted repair pass only pushed that to 24 %.
  Dense placement needs the general rule — a new cell may sit beside a word provided the
  perpendicular run it induces is itself registered as an answer. I built that
  (`dense_grid.py`) and then **deleted it**: even with the general rule it never got
  under 23 %, because the constraint is a property of the block pattern and a word-first
  construction cannot see it. Do not rebuild it; use the next point instead.
- **Empty cells are a property of the block pattern, so fix the block pattern first.**
  A block at `(r,c)` carries the across legend of `(r,c+1)` and the down legend of
  `(r+1,c)`, so it is empty exactly when both are blocks or off-grid. That is a local,
  cheap predicate: added to `swedish_grid.py` as `--max-empty` (energy penalty plus hard
  feasibility), it goes from 23-31 empty cells per grid to 20-22 out of 225 without any
  other change. Do not try to anneal it out of a word-first construction.
- **`swedish_grid.py` was enforcing *fully checked*, which is American density, not
  švédská density.** Every white cell had to belong to both an across and a down run of
  `min_run`, because `run_lengths` reported length-1 runs and `min_run 3` then rejected
  them. A real Czech švédská is full of unchecked cells and that slack is exactly what a
  pinned theme entry needs. `--allow-unchecked` drops length-1 runs from the report while
  still rejecting length-2 ones, and it moved κ from 0.78 to 0.75 on the same size.
- **A unary screen is not a fillability oracle, and this is where it really hurts.**
  Pinning theme entries into a fixed dense template with "every crossing slot keeps ≥1
  candidate" gave 22 pinned entries and `Unfillable grid` every time. Tightening the
  screen to "keeps ≥ N candidates" (`--min-domain`) trades pins for feasibility
  monotonically — floor 1 → 14 pinned, unfillable; floor 200 → 5 pinned, fills — which is
  a useful dial but still not a proof. The honest oracle is `ingrid_core`, and its cost
  is dominated by **dictionary load** (~4 s for 160 k entries), not by search, since
  `Unfillable` returns at initial AC. Pre-filter the wordlist to the fill bar once and
  reuse that file for every oracle call.
- **Distinguish reject-by-proof from reject-by-timeout — again.** `theme_construct.py`
  defaults to a 12 s / 2-core oracle. On a 15×15 that needs ~90 s on nine cores, *every*
  candidate came back "reject" and the run reported "nothing placeable", which reads
  exactly like a proof and is not one. Size the oracle timeout against a measured bare
  fill of the same template before believing a single rejection.
- **The 80 % theme-share cap never bound.** Structural feasibility caps theme share
  around 10-15 % in a dense 15×15, well under any editorial cap you would think to
  impose. If a brief has both, the geometry is doing all the work.
- **The wall this actually hit: density and filler quality are the same budget.** The
  final constrained grid (15×15, 8.9 % empty, 61 answers, 4 long theme entries pinned by
  `pin_long.py` plus 4 the solver found on its own = **8 theme, 13.1 % share**) is legal
  and it fills — but only at `--min-score 33`, and **26 of its 53 filler answers (49 %)
  score below 45**: `srz`, `htm`, `ámose`, `spu`, `inac`, `udelí`, `vedome`, `byvat`.
  Re-filling the same grid at bar 45 (56 k entries) or 55 (26 k) returns `Unfillable
  grid` outright. Twenty-two of the 53 filler slots are length 3, and a quality-gated
  Czech tier simply does not have enough good 3-letter words to cover that many.
  So on a 15×15 the empty-cell cap sets the answer count, the answer count sets the
  short-slot count, and the short-slot count sets the minimum dictionary depth — which
  then sets the filler quality you are forced to accept. Pick two of {small grid, few
  empty cells, clean filler} — **unless you have a curated short-word list**, which turns
  out to be the whole ballgame. See the next section: one existed in the repo the entire
  time.

### The short-word list existed all along, and I filtered on the wrong thing

`local/legacy-krizovkac/` holds the lexicon of **Křížovkáč 0.0.1**, a Czech
crossword-setting program from 2015: **68 174 answers, every one carrying a clue a human
setter wrote**, and — the part that matters — **1 833 three-letter and 4 659 four-letter**
entries. A dense 15×15 švédská has ~24 three-letter slots and no frequency list covers
them. Nothing in the pipeline referenced it; it surfaced only because the repo owner
remembered it existed. It is now committed at `resources/krizovkac/` with
`scripts/krizovkac_to_dict.py`, because `local/` is gitignored and this was one disk
failure from gone.

Dropping it into the tier moved the filler from `srz htm ámose spu inac` to
`rozptyl akadi ásana esauli`, took theme 8 → 9, and — because each entry ships a clue —
turned "write 53 clues" into "write 6".

**And that last number is where I made a real reasoning error, so it goes in the file.**
I let *"does this word already have a clue"* become a hard constraint on the fill and
reported the resulting trade-off as if it were physics ("gating the lexicon costs
21 hand-written clues instead of 6"). It is not physics. Writing clues is *my job* and it
is cheap. I had quietly optimised the artifact around my own convenience and then
presented that as a property of the problem.

> **Clue availability is a convenience for the setter, never a constraint on the fill.
> The filler-quality signal is corpus attestation.**

Which is measurable, and already implemented. A crossword lexicon is *made of*
crosswordese — that is its purpose — so **22 802 of Křížovkáč's 68 174 entries (33 %)
occur nowhere in a 5.6-billion-token corpus**: `aab`, `aabbcc`, `abakun`, `obosm`,
`lejzr`, and `nelsn`, the one the reader laughed at. `build_tier.py --gated
--attest <corpus> --attest-floor 25` keeps 36 551 of them, still leaving 4 116 short
words. Measured on the same 15×15 grid:

| | ungated | gated at 25 |
|---|---:|---:|
| filler unattested in csTenTen | **18 / 52** | **5 / 53** |
| median corpus score of filler | 33.5 | 38 |
| theme entries | 9 | 8 |

`filip nesel okolo atika činel kernel mládenec navlas azyl` instead of
`nelsn akadi isi íhá npk ásana hto esauli`. One theme entry is a fair price.

**Trace junk to its source before you filter anything.** I spent a round convinced the
crosswordese was coming from the Čapek supplement and was about to filter Čapek by
frequency. Tracing 34 suspect words: **31 came from Křížovkáč**, one from Čapek (and it
was a perfectly normal word), two from the csTenTen tail. Filtering Čapek would have
achieved exactly nothing. A three-column provenance table costs a minute and prevents
gating the wrong tier.

**Corollary, third sighting: `--min-score` is not a junk filter.** It is a frequency
threshold in a quality costume. Today it (a) discarded a whole flat-scored supplement —
`capek_to_dict.py` emits `word;30`, so `--min-score 45` deleted the tier entirely, (b)
still admitted `srz`, `htm`, `spu` at bar 33, and (c) had to be abandoned to get the
long tail at all. `build_tier.py` replaces it with provenance plus explicit junk rules
(de-accented doublets `udeli` beside `udělí`: 35 226 dropped; short-and-unvouched:
10 089) and runs at `--min-score 21`, i.e. no frequency gate at all.

### Compute the crossing ceiling before you tune anything

One line of arithmetic that would have saved most of a day, and which I did not run until
the work was over. For a candidate grid, compare the slot-length histogram against the
theme list's own length histogram:

$$\text{ceiling} = \sum_L \min(\text{slots}_L,\ \text{theme words}_L)$$

On the shipped 15×15:

| len | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---:|---:|---:|---:|---:|---:|---:|
| slots | 24 | 3 | 20 | 5 | 4 | 3 | 2 |
| theme words available | 3 | 13 | 13 | 13 | 15 | 8 | 7 |
| **usable** | 3 | 3 | 13 | 5 | 4 | 3 | 2 |

Ceiling **33**. Achieved **9**. So **73 % of the loss is letter agreement between theme
entries, not slot availability** — the grid had room for three and a half times what went
in, and the words simply would not cross each other.

I spent the day tuning the wrong variable. Forcing 7/8/9-length runs into the template,
worrying that the 10s and 12s had nowhere to go: all of it was optimising the histogram,
which was never binding. The lever is **crossing structure** — how many theme entries are
made to cross *each other* rather than crossing filler. Filler is free to agree with
anything; a theme–theme crossing has to be satisfied out of a 79-word list.

Two practical consequences:

- **Run the ceiling first.** It costs nothing and tells you whether geometry is even your
  problem. If the ceiling is already comfortably above your target, stop redesigning the
  grid.
- **Prefer templates whose long slots don't cross each other.** Untested, but it follows
  directly, and it is the obvious thing to try next.

Related, and worth knowing before you invest: the two most heavily engineered features in
this whole system went **completely unused** on this title. `--estimate-variants`, which
has the longest section in this file, answers "how many distinct fills exist at this
quality" — a question about choosing among fills of a *fixed* grid, and I was choosing
grids. And `--dupe-exempt-preferred`, built the same day for near-duplicate theme pairs,
never fired: at 79 entries in a 32×32 they would have collided constantly, at 9 in a
15×15 not one pair co-occurred. Three titles of effort have gone into the fill stage; the
last two days say the bottleneck is upstream of it.

### Glue slots: screen at the bar you will fill at

A pure-theme grid gives ingrid nothing to do. Leaving a bounded number of empty runs
("glue") makes the grid denser and gives the theme words extra crossing anchors, and
those runs are ingrid's job. Two traps, both paid for in full:

- **Screen and fill at the same `--min-score`.** The constructor screens each glue run
  with the same unary filter the solver applies first (does any word of that length match
  the induced pattern). Screening at bar 21 and then filling at bar 33 → `Unfillable grid`
  every time, because the screen certified patterns whose only matches were below the fill
  bar. Screening *and* filling at 21 fills fine and produces `lkvb`, `uzv`, `mna`, `blá`,
  `floka` — unclueable. Screening and filling both at **40** gave 12 filler words of which
  11 took an honest clue. The bar is a single number used twice; letting the two copies
  drift is the whole bug.
- **The unary screen is necessary, not sufficient.** It does not know about the dupe index
  or arc consistency, so a screened template can still come back `Unfillable`. That is
  cheap to discover (initial AC returns instantly) but it means the screen is a filter,
  not a proof — same lesson as "screen seeded templates with `ingrid_core`", one level
  down.

### Diacritics cost exactly one theme word

Czech křížovky *do* print háčky and čárky, so folding is not on the table — but it is worth
knowing the price, and it had never been measured. Matched arms (same seed, seconds,
workers, size), folding only the theme list:

| grid | diacritics kept | folded | Δ |
|---|---:|---:|---:|
| 21² | 43 | 44 | +1 |
| 27² | 63 | 64 | +1 |

One entry, plus ~6 crossings. I had expected folding to be worth several entries, since
it merges `ě/e`, `ů/u`, `í/i` and enlarges every crossing domain. It does not, because at
these sizes the binding constraint is **area, not letter compatibility** — the words are
not failing to cross, they are failing to fit. Cheap constraint; stop worrying about it.

### Everything downstream that changed

- **`--dupe-exempt-preferred`** (new flag, `src/dupe_index.rs`). A personal list is *full*
  of deliberate near-duplicates — `plán`/`plány`, `poké`/`pokebowl`/`slopbowl`,
  `hliněná`/`hlinění`, `bablty`/`babltý`, `kůň`/`koně`/`kuoň` — each with its own joke and
  its own clue. Previously the only way to let them coexist was to drop
  `--max-shared-substring` entirely, which then let junk near-duplicates into the filler.
  The flag exempts a shared-substring violation when **both** entries are preferred-tier;
  whole-word duplicates stay forbidden. That is exactly the granularity this needs.
- **Measure the clue budget before designing a hybrid.** The plan was švédská with an
  American numbered overflow for long clues. The author's own clues turned out to run
  max 32 characters, median 15 — inside the 34-character BOX budget of `CLUES.md` §2 — so
  the hybrid was never needed. One `max(len(clue))` would have settled it in the first
  minute.
- **§9's band mix does not transfer to a private title, and that is the right answer.**
  Honest labelling of the 79 clues gives S 19 % / O 13 % / H 68 % against a target of
  45-50 / 15-25 / 30-35. Two thirds of the answers are friends, addresses and couple
  slang whose only public definition *is* the in-joke, so `clue_check.py` reports FAIL on
  the mix and on "no entry with all crossings in H". Do not relabel to hit the target —
  record the deviation. The rules exist to guarantee a stranger can solve it; here there
  is no stranger.
- **Slovak is a cheap dictionary add.** `sktenten11.frqwl` through the same transform as
  the Czech list (`score = round(10*log10(freq))`) merged 159 103 brand-new forms into a
  595 086-entry standard tier and moved `d(L)/L` by +0.03 to +0.46, biggest at L3 and L7.
  It carries no lemma bonus and no POS filter, though — there is no Slovak MorphoDiTa run
  — so Slovak filler is *unscreened* in a way the Czech base is not. Raise the bar for
  glue rather than trusting the score.
- **The min-score landmine, third sighting.** Merged CS+SK minimum score is 21, so
  `--min-score` above 21 silently discards 63 % of the dictionary. Every time a dictionary
  is rebuilt, print its minimum score next to the fill command.
- **A CSS trap worth one line, because it silently eats a third of the puzzle.** The grid
  scroller used `display:flex; justify-content:center`. A centred flex item wider than its
  scroll container overflows on *both* sides and the left overflow is unreachable —
  `scrollWidth` (1437) came back smaller than the table (1473). Auto margins on the table
  inside a plain block wrapper collapse to 0 when there is no spare room, so the grid
  centres when it fits and scrolls fully when it does not.

## Švédská: match the publication's grid genre

Check what the title actually prints before designing a grid. Filmové listy runs a
**švédská** — legends inside the grid, a shaded tajenka the reader mails in — and we had
been shipping American grids with clues written to a švédská spec. `CLUES.md`'s
34-character BOX budget *is a legend cell*; the two halves of the system disagreed and
nobody had noticed.

For the solver **a švédská is just a block pattern** — no engine change, legend cells are
blocks. The structure to enforce is one rule:

> **Every word's legend lives in the cell before it.** Across word at `(r,c)` → legend at
> `(r,c-1)`; down at `(r,c)` → legend at `(r-1,c)`. Therefore **row 0 and column 0 are
> entirely legend cells** and no word may start on the top or left edge.

`scripts/swedish_grid.py --frame` enforces exactly that, at any size, plus full checking,
connectivity and block-clump limits. Consequences worth knowing:

- **180° symmetry is an American import.** Czech magazine grids don't use it. Dropping it
  is what makes one long slot affordable: under symmetry every long slot forces a mirrored
  twin, so an 11 costs ~27 bits against the frontier instead of ~14. If you need a long
  tajenka, drop symmetry first and don't spend a day annealing for a twin you don't want.
- **Legend-cell load is a cheap hard check.** A cell carries at most two legends (one ▶,
  one ▼). Verify it; the renderer should exit rather than silently drop one.
- **κ is size-independent.** Its derivation only assumes a fully checked grid, where
  `crossings = white` and `Σ L = 2·white`, so the block count cancels. It transfers from
  15×15 to 15×12 unchanged. Don't re-derive it.
- A švédská's short slots are genre-normal. The real Filmové listy grid clues `SPZ
  RAKOVNÍKA` and `ZNAČKA CENTIMETRU`; `OHM`, `OKR`, `ASU` are not defects there. Judge
  the fill by the genre's standard, not by an American one.
- Generic annealers **cannot reach a long slot** from an 11-free start — no downhill path
  grows a first 11-run, every intermediate state pays the run-length penalty. Plant the
  long runs first and freeze their rows (`scripts/lfs_grid11.py`); feasible κ 0.898 grids
  appeared in seconds after doing that, and never before.

## Seeding marquee entries — the step that was missing

Seeding two long theme entries is not a placement problem, it is a **crossing** problem,
and it is the most expensive dead end available. Two seeded entries sharing a column hand
the down slot through that column **two** fixed letters at once, and in Czech those
patterns are empty — `e...v...` and `e.c` have zero matches in a 136k list, so
`ingrid_core` returns `Unfillable grid` at initial arc consistency, before searching.
Measured: **1 of 16** cell-disjoint placements produced a fill.

Forbidding shared crossings outright is too strict — it rejected 39 of 40 templates. The
working screen (`scripts/tajenka_place.py`) enumerates cell-disjoint placements and counts,
for every slot, how many words match the induced pattern. Same unary filter the solver
applies first, costs milliseconds. Yield went **1/16 → 60/119**.

Then `scripts/theme_construct.py` places marquee entries greedily **with the real solver
as the oracle** — which is what `theme_seed.py` structurally could not do. A short run is
a sufficient oracle because `Unfillable` is decided at initial AC and returns instantly.
It seated 2-3 entries per template before the grid refused more.

Two more landmines in the same family:

- **`í` is a fatal first letter.** Seeding `letní` at row 0 of a grid that fills empty in
  48.7 s made it *instantly* unfillable: `í` then had to start a down slot and Czech has
  no word of that length beginning with `í`. Before seeding, look at where the awkward
  letters (`í á é ů ě`) land in their crossing slots.
- **`--ignore-diacritics` folds the entire dictionary, not the grid.** A subagent used it
  to make seeded smokes pass and every conclusion from those smokes was worthless —
  crossing domains are far larger under folding, and the output prints unaccented. It is
  a rendering choice wearing a solver flag's clothes. Ban it from screening runs.

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
| `--dupe-exempt-preferred` | **on** whenever the theme list contains deliberate near-duplicates | Exempts a shared-substring violation when *both* entries are preferred-tier, so `plán`/`plány` and `pokebowl`/`slopbowl` can share a grid while the filler still gets `--max-shared-substring 4`. Whole-word duplicates stay forbidden. Without it the only lever is dropping the constraint globally, which is how junk filler gets in. |
| `--cores` | 5 was plenty | 8 theme words at 92 s on 5 cores. Ten cores for 1800 s got 9-10 — steeply diminishing. Most of the gain arrives in the first 30-60 s; the tail is one worker grinding at target N+1. |
| `--timeout` | 600-900 s | On a good grid the winning incumbent arrives inside 30 s and the rest is tail; the tail buys proof and any equal-incumbent races, not a better fill. |
| `--grids N` | replaces the harvest-several-seeds loop | Emits up to N distinct certified fills at the optimum, incumbent first, deterministic under `--seed`. **Measured caveat:** the search-time certified pool is thin on saturated grids (900 s on the shipped grid certified 1 — the incumbent, found at 7 s; the scheduler cancels everyone at target <= incumbent, so equal-incumbent fills only land from cancellation races). Ask for N, expect what the run certified; the fallback for a real pool is the v1 `--grids-delta` below the optimum, not seed reruns. |
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
affordable. `d(L)/L` on the 129k-word Czech list the original work used:

| L | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 12 |
|---|---|---|---|---|---|---|---|---|---|
| `d(L)/L` | 3.30 | 3.00 | 2.66 | 2.33 | 2.06 | 1.81 | 1.58 | 1.38 | 1.03 |

**Recompute this per dictionary — it moved.** On `local/longtail/longtail_22_l67c.dict`
at `--min-score 30` (the enlarged Standard list the long-tail work recommended):

| L | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|
| `d(L)/L` | 3.30 | 3.02 | 2.66 | **2.37** | **2.20** | 1.81 | 1.58 | 1.38 | 1.19 |

Frontier at `kappa* = 0.95` is **2.443**, between length 5 and 6 on the old list. On the
enlarged one **length 6 moved from over the cliff to under it** (2.33 → 2.37 vs a 2.443
frontier is still under, but 6 is now within 0.07 instead of 0.11) and 7 improved
markedly, 2.06 → 2.20 — exactly the L6/L7 band the long-tail addition targeted, so this
doubles as independent confirmation that it did what it claimed. The old rule of thumb
*"every slot longer than 5 must be paid for by a slot of length 3-4"* is now closer to
*"longer than 6"*. Practical consequences:

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

Metropolitan, same engine, same clue spec, one title.

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

LFŠ, second title, švédská, 50 slots (not comparable line-for-line — smaller grid,
different genre — but the shape of the result is):

| | value | note |
|---|---|---|
| Preferred hits in fill | 5 / 50 (10%) | vs 11-14% on Metropolitan's 70 |
| of which recognizable | **5** | `krumbachová`, `promítač`, `plátno`, `rolí`, `sirat` |
| of which hand-seeded | 3 | seeding is why the other two rows agree |
| hard defects | **0 / 50** | 2-4 per fill before seeding; see the objective section |
| theme tier | 193 curated rows → 1 240 forms | every row corpus-attested at build time |
| certified distinct fills | 8 | i.e. the grid was essentially exhausted |
| clue set | 50 clues, 7/7 checks pass | median 15 chars, bands 46 S / 20 O / 34 H |

The defect row is the one that improved, and it improved because of seeding rather than
because of anything in the dictionary. Note also that 10% theme density on 50 slots is
**five** entries — on a small grid the count is small no matter what, so the clue voice
has to carry more of the theme than it does on a 70-slot American grid.

Karolína, third title, švédská, template built around the theme list rather than filled.
Not comparable line-for-line to either of the above — the theme tier is the *entire*
answer set, not a bonus — but that is exactly the point:

| | candidate A | candidate B |
|---|---|---|
| grid | 32×32 | 23×23 |
| answers | 79 | 64 |
| theme entries | **79 / 79 (100 %)** | 52 / 64 (81 %) |
| standard-tier filler | 0 | 12, all clued |
| crossings | 93 | 91 |
| hard defects (`fill_critic.py`) | 0 | 0 |
| theme spread ratio | 0.98 | 1.00 |
| clue length | median 15, max 32 (cap 34) | median 15, max 30 |
| bands, honestly labelled | S 19 / O 13 / H 68 % | same theme set + 7 S / 4 O / 1 H filler |

B is denser per unit area and reads like a magazine puzzle; A ships the whole list. The
choice between them is editorial, not technical — which is the healthy state to be in.

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
- **This entry used to say "`--estimate-variants` can't answer this". That advice is now
  obsolete — the flag is fine and is the most useful instrument in the box.** What was
  obsolete is the *implementation* it described: a fixed 8-replicate SMC path that ignored
  the budget and stopped after 117 s of an allowed 600 s. That path was replaced by
  budget-consuming cohort waves. The CLI surface is unchanged and still has
  `--estimate-variants`, `--estimate-runtime-ratio`, `--estimate-max-time`,
  `--estimate-walks` and `--estimate-guide-probability`. See the estimator section below
  before reading any slack number.
- **Curated data under `local/`.** `local/` is gitignored. Hand-authored allowlists are
  inputs, not artifacts — they belong in `resources/`. This bit twice: the Křížovkáč
  lexicon, the single scarcest asset in the pipeline, sat there untracked and unreferenced
  until the repo owner happened to remember it.
- **A subagent's "verification" section is written by the same process that wrote the
  bug.** Two shipped-looking results were hollow. A renderer reported that it had driven
  the UI and everything worked; it had (a) embedded the entire puzzle JSON, every answer
  in plaintext, *beside* a correctly-implemented XOR+base64 solution matrix — satisfying
  "store the solution obfuscated" and defeating its point — and (b) centred the grid with
  `display:flex; justify-content:center` inside a scroll container, which makes a wide
  grid overflow on *both* sides with the left third unreachable (`scrollWidth` 1437 <
  table 1473). It had tested at 390 px and 1400 px exactly as briefed; the table fit at
  both. Ask for **adversarial** evidence, not a narrative: *"grep your output for three
  answers in plaintext and paste the result"* catches the first in one line. *"I typed
  letters and clicked check"* catches neither.

## Reading a slack estimate — `--estimate-variants`

It answers *how many distinct fills exist at least as Preferred-heavy as the one you
got*, in bits, after the search. On the LFŠ run it was the thing that told me to stop.

**It used to spend 7% of its own budget.** The calibration walk is incumbent-guided and
always reaches a leaf (~0.75 s); real cohort walks are rejected early (~0.23 s), so
`select_cohort_size` under-sized by ~3×, and a 0.5 safety factor halved it again. Raising
`--estimate-walks` 16 → 4096 moved the cohort 16 → 18: **the flag was never the binding
constraint**, despite reading like it. Now fixed — throughput is re-measured per wave and
the cohort refills to the deadline, and the runtime-ratio clamp went 0.5 → 1.0. Measured
utilisation 7.4% → 44.7% at ratio 0.5, **90.0%** at ratio 1.0.

**How to invoke it.** `--estimate-variants --estimate-runtime-ratio 1.0 --estimate-walks
100000`. The ratio is of *search* time, so budget ~2× your `--timeout` in wall clock.
`--estimate-walks` is capped at 100000 and it is a ceiling, not a target.

**How to read it, in priority order.**

1. **`known distinct fills` — the certified lower bound.** The only number that cannot be
   wrong. Read it first. On our shipped grid it was 8; on the unseeded one at a higher
   Preferred level, 2.
2. **The 95% spread.** Trust it more than the point estimate.
3. **`effective samples`.** Not a function of effort — a property of the weight
   distribution. Our best-behaved config had ESS 45.5 from 3,087 walks; the tightest had
   ESS **8.0 from 16,550**. Low ESS with many walks means the sampler is still discovering,
   so the interval is a floor on the uncertainty, not a measurement of it.
4. The point estimate last.

**More walks can widen the interval — but that is one of two behaviours, not a law.**
I first wrote this section claiming widening was the rule, from three points on one grid:
29 → 1,477 → 2,958 walks gave slack 0.2 → 1.1 → 2.4 bits with spreads −0.3…0.5 → 0.4…2.0
→ 0.4…4.6 and ESS *falling* 19.0 → 43.6 → 13.6. That is real, and it is the heavy-tail
signature: the extra walks found rare high-weight regions the small cohort never reached,
so **the 29-walk answer was not imprecise, it was wrong and confidently so.** The sample
variance is biased low by the same mechanism that biases the mean low — you cannot measure
the spread of a tail you have not hit.

But a controlled sweep on a second grid at the default guide probability showed the
opposite and healthier behaviour: 310 → 3,018 walks moved ESS **4.5 → 31.2** and the
spread **0.1…4.7 → 2.6…3.6**, with the point estimate drifting *down* 3.8 → 3.2. It
converged.

So: **do not read the walk count, read ESS.** Rising walks with rising ESS = converging,
trust the narrowing interval. Rising walks with flat or falling ESS = still discovering,
the interval is a floor on your uncertainty and the point estimate is a lower bound.
Never quote a small-cohort estimate as a measurement either way.

**What it bought on a real puzzle**, 900 s search + 810 s estimation each:

| config | level | certified | slack | 95% | ESS |
|---|---|---:|---:|---|---:|
| bare grid | ≥6 pref | 2 | 1.1 b | 0.6-1.5 | 45.5 |
| tajenka seeded | ≥5 pref | 11 | 3.9 b | 1.1-4.8 | 5.1 |
| + 2 marquee (shipped) | ≥5 pref | 8 | 3.5 b | 1.8-4.3 | 8.0 |

- **~10 distinct fills, not thousands.** An almost-saturated grid. It explained an
  observation I had and hadn't understood: ten harvests at ten seeds shared a common core
  (seven identical entries in *every* fill). The estimate said **stop harvesting** in one
  number, hours before I'd have concluded it by exhaustion. That is its best use.
- **Seeding two marquee entries cost 0.4 bits.** I had assumed hand-seeding was expensive.
  It is nearly free, and it converts oblique junk into recognisable entries at constant
  count. Measure this instead of assuming it.
- **1.1 bits at ≥6 on the bare grid** ≈ two fills, which is exactly why those fills are the
  ones riddled with same-lemma collisions: with two fills the optimiser has no room to be
  choosy. Slack and defect rate are the same quantity seen twice.

**`--estimate-guide-probability`: leave it at 0.98. Measured, and my prediction was
backwards.** I had it down as a lazy default whose concentration *causes* the heavy tail,
and expected lowering it to trade point accuracy for variance. Sweep on the shipped grid,
same seed, 180 s estimator budget each:

| p | accepted | ESS | slack | 95% spread | certified fills |
|---:|---:|---:|---:|---|---:|
| 0.50 | **1 / 9 718** | 1.0 | 1.1 b | (degenerate) | 2 |
| 0.80 | 239 / 5 402 | 3.1 | 4.7 b | (degenerate) | **10** |
| 0.95 | 1 647 / 3 415 | 1.2 | 6.2 b | (degenerate) | 8 |
| **0.98** | 2 300 / 3 018 | **31.2** | 3.2 b | **2.6-3.6** | 7 |

Lowering it is catastrophic and not for the reason I assumed. A diffuse proposal does not
flatten the weights — it fails to produce samples at all, because deviating from the
incumbent almost always walks into an inconsistent partial assignment. At p = 0.5, **one
walk in 9 718** reaches a valid leaf, and the survivors are pure tail. That is also why
0.95 is *worse* than 0.98 (ESS 1.2 vs 31.2) despite being nearer. The concentration is
what makes the estimator work at all.

**The one genuinely useful split, and it was a surprise — and it is now a product.** p =
0.80 reached **10 distinct certified fills** against 0.98's 7, while being useless at
estimating; enumerating fills and estimating their number want opposite settings. The
"diverse pool of fills to choose from" use is no longer a reason to touch this knob:
`--grids N` emits the certified fills directly, one invocation, gradeable by
`fill_critic.py`. Run the estimator at 0.98 for the bits (the slack question); the
certified-fills column remains the hard lower bound on what `--grids` could emit.

**Cosmetic bug, fixed:** when the relative standard error exceeded 1/1.96 the normal
approximation's lower end went negative and was clamped to `f64::MIN_POSITIVE`, printing
`nominal 95% spread: -1022.0-5.8 bits` — the log2 of the smallest subnormal double leaking
into the report. It now floors at the certified count, which is both a true statement and
readable.

**Landmine, now fixed but worth the shape of it:** `--estimate-walks 2000000` was silently
invalid (cap 100000) and the only signal was `estimate: invalid options` printed **after**
a 1200 s search, naming neither the option nor the limit. Three runs, an hour of wall
clock. Both estimator numeric flags now validate at parse time. When a long-running tool
validates late, fix the validation before running it again.

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
- **A legend one glyph away from its own opposite is a bad legend, even when it is
  correct.** I clued `DÝMEM` as `čím se hlásí oheň?` — fire announces itself by smoke,
  which is right. The first reader read `HASÍ` for `HLÁSÍ` and got *"what do you put a
  fire out with?" → with smoke*, i.e. nonsense. In a švédská the legend is set at ~8.5 px
  in caps inside a 66 px cell, and `HLÁSÍ`/`HASÍ` differ by one letter. Unlike the
  `trial`/`trail` case — where crossings pin the *answer* and the ambiguity cannot reach
  the solver — this ambiguity is in the *clue*, so no crossing can rescue it. Verify the
  clue is correct (it was), then change it anyway. Cheap rule: if deleting or swapping one
  letter of a legend yields another real Czech word that changes the sense, rewrite. This
  is a good candidate for `clue_check.py`, since a one-edit-distance lookup against the
  standard list is a two-line check.

## The critic — built, and what using it taught

**UPDATE: built, as `scripts/fill_critic.py` (deterministic filters only, no model yet),
and it earned its keep immediately.** On first run over ten unseeded LFŠ fills it flagged
the same-lemma collisions in *every one of them* — a class I would otherwise have shipped,
since `--max-shared-substring 4` is blind to `role`/`rolím`. It also implements the theme
**spread** metric that had never been measured (3×3 sector coverage plus mean pairwise
Chebyshev distance between Preferred midpoints, normalised against a Monte-Carlo uniform
baseline; below ~0.8 means clumped) and a multi-term fill score that prints every term
separately so the weights can be argued with. Its companion `scripts/clue_check.py`
implements the `CLUES.md` §11 step-3 kontrolor: all seven checks, including the ones that
are invisible by eye (band mix, no entry with all crossings in H, shape dispersion over
the crossing graph, morphological root-leak rather than substring).

Two things learned from actually using them. **Run the clue checker while writing, not
after** — the band mix and shape-dispersion rules are global and you cannot satisfy them
by fixing clues one at a time; three iterations of the whole set is the natural rhythm.
And **the deterministic half is most of the value**; the per-entry model verdict is still
unbuilt and I did not miss it.

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

## The toolbox as it now stands

| script | what it does | publication-neutral |
|---|---|---|
| `curated_dict.py` | curated tier from a TSV; build **fails** on an unattested row | yes |
| `theme_tier.py` / `theme_expand.py` | mined tier: grade, then inflect | yes |
| `swedish_grid.py --frame` | švédská templates, any size, no symmetry | yes |
| `lfs_grid11.py` | plants long runs then anneals around them | trick generalises |
| `tajenka_place.py` | domain-screened placement of multi-part seeds | yes |
| `theme_construct.py` | greedy marquee seeding, real solver as oracle | yes |
| `fill_critic.py` | per-entry verdicts, lemma collisions, spread, multi-term score | yes |
| `clue_check.py` | the `CLUES.md` §11 kontrolor, all seven checks | yes |
| `fill_margin.py` | pre-search κ screen | yes |
| `build_tier.py` | standard tier by provenance + junk filters + corpus attestation, not by a score bar | yes |
| `krizovkac_to_dict.py` | the Křížovkáč lexicon → scored dict + 68k-clue bank | yes |
| `pin_long.py` | pins long theme entries with a correctly-sized solver oracle | yes |
| `theme_grid.py` | builds the **template around** the theme words; ingrid fills the glue | yes |
| `check_grid.py` | švédská legality: runs, orphans, legend load, connectivity, dict membership | yes |
| `karolina_assemble.py` | grid + clue/band TSVs → one `puzzle.json` | yes |
| `render_puzzle.py` | `puzzle.json` → interactive or review HTML, one self-contained file | yes |
| `combine_review.py` | several review pages → one approval e-mail | yes |
| `send_mail.py` | Resend with attachments | yes |
| `crossword-email` skill | `--layout {american,swedish}`, `--tajenka` shading | yes |

## Next steps, roughly by effort

**Done since v1** (kept here so nobody rebuilds them): the deterministic critic, the
same-lemma collision check, theme spread, the constructor with the solver as oracle, the
score-30-32 long-tail win, the estimator budget fix, a second and a third title, the
template-around-the-theme constructor (`theme_grid.py`), `--dupe-exempt-preferred`, and
the Czech+Slovak merged standard tier.

**Low.**
- Flag answers whose meaning is niche enough to need an anchor (loanwords, sports terms,
  brands). Not a fairness check — crossings handle fairness in a fully checked grid —
  but a **band assignment** check: these belong in H, or want sec. 5's familiar sense
  leading. This is the salvageable half of the `trial` postmortem.
- ~~Sweep `--estimate-guide-probability`~~ — done, see the estimator section: 0.98 is
  load-bearing and lowering it collapses the acceptance rate. Remaining question is whether
  a median-of-means or truncated-weight estimator beats the plain IS mean at 0.98.
- A diversity-aware supply metric. Unchanged and still true: κ is structurally blind to
  letter-pattern clustering, so it compares grids at a fixed dictionary but not
  dictionaries.

**Medium, and the top item is now the highest-value change in the whole system.**
- **Make the objective lemma-aware, or score-weighted.** `count_preferred_words` counting
  surface forms is *the cause* of the `role`/`rolím` defect class and the reason marquee
  seeding is needed at all. Counting distinct lemmas removes the defect at source;
  maximising Σ score would additionally let the hand grade (200 / 150) steer the solver,
  which it currently ignores entirely. Everything else in this file is a workaround for
  this one line.
- Lemma-aware duplicate constraint in `dupe_index.rs` — the constraint-side version of
  the same fix, for `kope`/`kopali`.
- Consume the two-cell legend capacity. A švédská legend cell carries two legends and
  `CLUES.md` §2 values the pair at 70 characters instead of 34; the LFŠ grid has 10 such
  cells and nothing downstream spends them.
- Per-entry model verdicts on top of `fill_critic.py`, for the "attested somewhere but no
  honest clue exists" class (`ladin`, `manas`, `roba`, `dom`, `rop`) that no regex catches.

**Larger, and speculative.**
- Joint grid+theme search — **half done, and the done half was cheap.** `theme_grid.py`
  saturates a small theme list, but it is greedy first-fit with suffix-removal ILS and it
  never reconsiders an early placement that boxed in a later one. The measurable target is
  *crossings per word* (2.35 at 32×32): a denser packing ships the same 79 words in a
  smaller grid, and grid area is the thing that costs. Backtracking or an exact CP/MIP
  formulation over placements is the obvious next move; so is the same trick applied to
  `theme_construct.py`, which stops at 2-3 marquee entries for the same reason.
- **Give the constructor the real oracle.** `theme_grid.py` screens glue runs with a
  bitset unary filter because shelling out to `ingrid_core` per candidate placement is far
  too slow at ~10⁴ placements per restart. A library entry point that answers "is this
  partial template arc-consistent?" in-process would let the constructor use the solver's
  own verdict at every step instead of a filter that is necessary but not sufficient.
  That is the single tooling gap that cost the most time on this title.
- **The theme list is the ceiling, and it cannot be expanded.** 79 forms is 79 answers;
  there is no `theme_expand.py` move here because each clue is bound to its surface form.
  What *would* help is a tool that goes the other way: propose inflections/relatives of a
  hand list and ask the author to clue the ones the grid actually wants, i.e. expansion
  driven by the placement search rather than by morphology.
- A **fourth** title, ideally one defined by a house style, an author or an era — the
  place / topic / person split is a three-point line and three points still fit almost
  anything.
