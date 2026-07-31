# Theme density

## Scope

This document explains why a generated fill reads as a generic Czech crossword even
when a theme dictionary is supplied, quantifies each contributing bottleneck on a
real configuration, and records what actually moved the number. It is a companion
to `fill-margin.md` (pre-search fillability) and `CLUES.md` (the editorial spec).

The reference configuration throughout is Brnensky Metropolitan 7-8/2026:

| artifact | what it is |
|---|---|
| `local/trials/metro_brno_grid.txt` | baseline 15x15 template, 74 slots |
| `local/trials/no_marked_n33_fill.txt` | previous best fill on that template |
| `local/trials/metro_brno_preferred.dict` | previous theme list, 447 entries |
| `local/trials/standard_clued_n33.dict` | Standard tier, 129,335 entries at min-score 30 |

Measurements were taken with `target/release/ingrid_core`, `scripts/fill_margin.py`,
and two new tools introduced here, `scripts/theme_tier.py` and `scripts/theme_seed.py`.

---

## 1. The observation to explain

`no_marked_n33_fill.txt` contains **two** of the 447 theme entries: `zabovresky`
and `savelova`. `savelova` is one of the five entries `CLUES.md` Priloha D sends
back to the dictionary as a fill defect (an unverifiable proper name), so the fill
carries exactly **one** word a Brno reader would recognize, out of 74.

The two rejected fills are not better. `metro_brno_fill.txt` has five theme hits
(`karas`, `oliva`, `starez`, `arena`, `vilem`) and still reads generic, which is the
first clue that counting Preferred hits is not the same as measuring theme density.

---

## 2. Four bottlenecks, measured

### 2.1 The theme tier is mostly people's names

Classifying all 447 entries by their MorfFlex `_;X` semantic marker:

| count | class | examples |
|---:|---|---|
| 195 | given name (`_;Y`) | `alena`, `adela`, `andrea`, `barbora`, `arnost` |
| 169 | geographic (`_;G`) | `zabovresky`, but also `afrika`, `berlin`, `amsterdam`, `antarktida` |
| 44 | no morphology | `aberl`, `archi`, `autostany`, `bohunicich` |
| 22 | other proper (`_;m`) | `babylonfest`, `bobycentrum`, `ceitec` |
| 17 | common noun | `brezen`, `kveten`, `tramvaj`, `vystaviste` |

Cross-checking the 169 geographic entries against a list of Brno toponyms leaves
**33**. So roughly **7 %** of the theme tier is unmistakably Brno.

This is not a bug in the extractor, it is a property of the source. A city magazine's
most distinctive frequent tokens are the names of the people it interviews:
councillors, athletes, artists. A crossword full of first names reads exactly like a
generic crossword, because generic crosswords are also full of first names.

**Why it matters more than it looks.** Ingrid's Preferred tier *is* the search
objective: `parallel_search` maximizes `count_preferred_words`. Every entry in the
tier that a reader would not recognize spends the objective on nothing. With 7 %
precision, two Preferred hits buy 0.15 expected recognizable words.

### 2.2 Half the grid is unreachable, and Czech makes it so

Slot lengths in the baseline template against theme supply:

| length | slots | Standard candidates | theme entries | theme share of domain |
|---:|---:|---:|---:|---:|
| 3 | 6 | 947 | **0** | 0.00 % |
| 4 | 28 | 4,112 | **2** | 0.05 % |
| 5 | 19 | 10,054 | 108 | 1.07 % |
| 6 | 10 | 16,186 | 102 | 0.63 % |
| 7 | 3 | 21,711 | 96 | 0.44 % |
| 8 | 4 | 23,072 | 68 | 0.29 % |
| 10 | 2 | 14,047 | 24 | 0.17 % |
| 12 | 2 | 5,177 | 1 | 0.02 % |

**34 of 74 slots (46 %) are length 3 or 4, and the theme tier has two entries that
short.** `scripts/metropolitan_theme_dict.py` defaults `--min-length 5`, so short
theme words are excluded by construction — but raising that flag does not fix it.
Full MorphoDiTa inflection of the 447 lemmas produces 2,549 forms and still only
**11** at length 4 and **0** at length 3. Czech toponyms and institution names are
simply not short: `Bystrc` is the shortest Brno district at 6.

The only Czech theme vocabulary in the 3-4 class is **initialisms**: `vut`, `bvv`,
`dpmb`, `mhd`, `ids`, `mmb`, `mou`, `muni`, `mzlu`, `jamu`, `tic`, `zus`, `umc` —
plus the city's own name in its oblique cases, `brna`, `brnu`, `brne` (all length 4).
That is the answer to "more weird shorthand-ish words?": **yes, and it is the only
source there is for 46 % of the grid.**

### 2.3 Every forced theme word costs a multiple of the whole search

Baseline, 10 cores, `--timeout 300`, 447-entry theme list (`local/rich/base.csv`):

| target | outcome | elapsed |
|---:|---|---:|
| 0 | fill found, 0 Preferred | 55.7 s |
| 1 | fill found | **296.4 s** |
| 2 | not reached in 300 s | — |

The first theme word costs more than four times the entire unconstrained search.
The mechanism is in the table in 2.2: forcing a Preferred entry restricts one slot
to under 1 % of its domain, and each of that word's 5-10 crossing letters then
restricts a perpendicular slot.

`scripts/theme_seed.py` measures this directly by pinning theme words into the
template and asking the solver whether the remainder fills at all:

| theme words pinned | proven unfillable | timed out | filled |
|---:|---:|---:|---:|
| 2 | 0 | 0 | 2 / 2 |
| 3 | 12 | 2 | **0 / 14** |
| 4 | 12 | 2 | **0 / 14** |
| 6 | 14 | 0 | **0 / 14** |

On the baseline template an arbitrary placement of three theme words is *proven*
unfillable about 86 % of the time. The solver's later success at six is not luck in
the ordinary sense: it comes from choosing placement and fill jointly, which is why
it needs minutes rather than seconds.

Same effect in kappa terms (`scripts/fill_margin.py`): baseline 0.9363 unseeded,
0.975-0.997 with three theme words pinned, 1.02-1.08 with six. `kappa* = 0.95`, and
1.00 is the highest kappa that ever produced a fill in the committed calibration.
Three theme words consume the entire fillability budget of this template.

> Caveat: seeded kappa is only comparable between templates at equal seed count.
> Pinned letters shrink `domain_bits`, which inflates kappa mechanically.

### 2.4 The geometry, and why it is a real exchange rate

In a **fully checked** grid every white cell lies in exactly one across and one down
slot, so every white cell is a crossing: `crossings = white = 225 - blocks`. Since
`sum_s L_s = 2 * white`, the block count cancels in kappa and

```text
kappa  =  C / (2 * alpha * <d(L)/L>)        d(L) = log2|dict_L|
```

with `C = 4.5861` bits/crossing and `alpha = 0.9878` measured over 41 grids
(RMSE 0.0061 as a surrogate for real kappa). **kappa is a pure function of the slot
length histogram.** `d(L)/L` on `standard_clued_n33.dict`:

| L | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `d(L)/L` | 3.296 | 3.001 | 2.659 | 2.330 | 2.058 | 1.812 | 1.584 | 1.378 | 1.028 |

The `kappa* = 0.95` frontier sits at 2.443, between length 5 and length 6. So a grid
made entirely of 6s is already past the cliff, and **every slot longer than 5 must be
paid for by a slot of length 3 or 4.**

That is the actual tension. Theme words want long slots; fillability wants short
ones; there is no free direction. Two consequences:

- The baseline's two 12s and two 10s are its worst feature. Removing them lowers
  kappa *and* lowers the short-slot share at the same time — not a tradeoff.
- Blocks are the currency of headroom. Fewer white cells means fewer crossings.
- A negative result worth recording: exhaustive DFS finds **no 15x15 fully checked
  symmetric grid with a minimum run length of 4**, for any allowed run set from
  `{4,5,6}` up to `{4..10}`. The 3-letter slot is structurally unavoidable at this
  size, which is another reason the initialism tier is not optional.

---

## 3. What moved the number

Two new scripts and one grid change. All searches use
`--min-score 30 --max-shared-substring 5` and `standard_clued_n33.dict`.

### 3.1 `scripts/theme_expand.py` — supply

MorphoDiTa form generation over theme lemmas, under the Standard tier's marked-class
policy: no vocatives, imperatives, transgressives, negated forms, or nonstandard tag
variants. Proper names are additionally locked to the grammatical number of their own
nominative, so `Brno` does not emit `Brny` while `Zabovresky` keeps its plural.

Effect on its own, 447 lemmas to 2,549 forms: Preferred count in 300 s rose from
**1 to 6**. Effect on the product: **none**. The six were `nesokoly`, `romanem`,
`milena`, `rokytovu`, `radek`, `marty` — five personal names and one negated form
(the negation filter was added in response). Expanding a low-precision tier makes the
metric go up six-fold and the crossword no more thematic, which is the sharpest
available demonstration that supply and precision are separate problems.

### 3.2 `scripts/theme_tier.py` — precision

Grades a corpus-derived theme dictionary on three publication-independent signals
plus one small manual list.

**Semantic class.** Keep `_;G` place, `_;K` institution, `_;R` brand, `_;m` other
proper. Drop `_;Y` given name, `_;S` surname, `_;E` nationality. A Czech toponym is
very often also a surname (`Petrov`, `Slatina`), so a drop class only bites when no
keep class is present.

**National rarity.** A publication's own vocabulary is nationally rare. CSTenTen
scores separate the two classes cleanly:

| | CSTenTen score |
|---|---|
| Brno toponyms | `zabovresky` 35, `spilberk` 39, `luzanky` 35, `bystrc` 38, `recovice` 34, `medlanky` 34 |
| national / international | `berlin` 46, `amsterdam` 42, `afrika` 45, `australie` 49, `praha` 62, `ostrava` 54 |

Threshold 41 separates them with no place list. `--max-reference-score` defaults there.

**The upstream salience score, which was already right.** `metro_brno_preferred.dict`
scores `zabovresky` 270, `spilberk` 270, `luzanky` 260, `bystrc` 230 and `berlin` 156,
`amsterdam` 150, `afrika` 154. The existing pipeline had already computed the locality
ranking and then discarded it by flattening into a binary tier. The `>= 200` slice is
47 entries and essentially pure Brno. `--trust-input-score 200` keeps it outright.

**Acronym mining.** All-caps runs of 2-6 characters from the issue text, rejected when
the token also analyzes as an ordinary Czech word (`AKCE`, `CENU`, `DALSI` are headline
capitals, not initialisms) or when it is nationally common. This is the only source of
length-3 theme entries.

**`resources/metropolitan/allowlist.txt`.** The gates get one class wrong by construction:
words the publication owns outright but that are nationally common or parse as
personal names — `brno` (CSTenTen 57), `vut` (45), `jamu` (42), `masna` and `prygl`
(read as surnames), `ceska`, `komin`, `hody`. About forty entries, written once per
publication rather than once per issue. This is the only irreducibly manual part.

Precision of the graded tier, checked by hand on the length-3/4 class:
`brna brno brnu brne bvv cejl dpmb hody hady ids jamu kroj mhd mmb mou muni mzlu orli
paro tic vut zus umc uvoz cudu slus siml` — 45 entries, essentially all Brno, against
**2** before.

### 3.3 The grid

`local/rich/grids/` holds ten verified candidates (symmetric, connected, fully
checked). The winner `g09_headroom48c` trades the baseline's long slots for headroom:

| | baseline | g09 |
|---|---:|---:|
| kappa | 0.9363 | **0.9157** |
| slots | 74 | 70 |
| blocks | 34 | **48** |
| crossings (= white cells) | 191 | **177** |
| length 3-4 share | 45.9 % | **28.6 %** |
| slots at length 6-9 | 17 | 20 |
| adjacent block pairs (two-cell legends, `CLUES.md` sec. 2) | 16 | **34** |
| unconstrained fill time | 29-56 s | **3.9 s** |
| random 3-theme placements the solver accepts | **0 / 14** | **3 / 7** |

The last row is the geometry effect measured independently of the search, with
`theme_seed.py --verify-binary`: pinning three theme words in arbitrary positions is
proven unfillable on the baseline every time it was tried, and fills on `g09` in
roughly half of attempts. At six theme words both templates are still 0.

Note that `g09` has **more** blocks and **fewer** crossings. That is the whole trade:
blocks buy headroom, and the baseline was spending its headroom on two 12s and two 10s.

One caveat from the sweep: `--max-shared-substring 5` is not priced by kappa and is
decisive in practice. `g01` at kappa 0.9212 does not fill in 60 s while `g09` at
0.9157 fills in 3.9 s; four of the five no-fills are the designs with the most
length-5 slots. Cap length-5 slots near 30.

### 3.4 Result

All runs on `g09_headroom48c` use `metro_v*.dict`, the graded and expanded tier.

| configuration | theme entries | grid | cores | budget | Preferred in fill | recognizable |
|---|---:|---|---:|---:|---:|---:|
| previous best (`no_marked_n33`) | 447 | baseline | — | — | 2 / 74 | **1** |
| 447 lemmas, searched fresh | 447 | baseline | 10 | 300 s | 1 / 74 | 0 |
| + inflection, ungraded | 2,549 | baseline | 10 | 300 s | 6 / 74 | **0** |
| graded + inflected (`v2`) | 791 | baseline | 8 | 900 s | 6 / 74 | 4 |
| graded + inflected (`v2`) | 791 | `g04_fixed52` | 3 | 900 s | 6 / 68 | 4 |
| graded + inflected (`v2`) | 791 | `g10_dense50` | 3 | 900 s | 6 / 66 | 5 |
| graded + inflected (`v2`) | 791 | `g09_headroom48c` | 3 | 900 s | 9 / 70 | 8 |
| graded + inflected (`v2`) | 791 | `g09_headroom48c` | 8 | 1800 s | 9 / 70 | 8 |
| + lemmatised inputs (`v3`) | 975 | `g09_headroom48c` | 10 | 1800 s | 9 / 70 | 7 |
| + literal initialisms (`v4`) | 912 | `g09_headroom48c` | 10 | 1800 s | **10 / 70** | **8** |

Time to reach a given count also collapsed. On the baseline with the original list the
*first* theme word arrived at 296 s. On `g09` with `v4`, five arrived at 24 s, eight at
26 s, nine at 183 s and ten at 400 s.

The `v4` fill: `tic`, `adamov`, `scala`, `cejlu`, `zelná`, `orlím`, `vut` and `lipek`
are Brno; `psč` is a number-fact carrier rather than a local answer, and `sportov` is a
PDF column-break fragment that should never have been in the tier (fixed in `v5`, see
3.5). Ten Preferred hits with eight recognizable, against two hits with one.

Brno-adjacent towns inside the magazine's catchment — `adamov`, `borotín`, `kuřim`,
`ivančice`, `blansko` — count as recognizable. That is not a concession, it is what the
national-rarity threshold is selecting for: they are exactly the places nationally rare
and locally frequent.

### 3.5 Residual precision leaks, and the fixes

Three classes of junk survived into `v4` and are closed in `v5` (896 entries):

**PDF column-break fragments.** `sportov`, `jihomo`, `metropo`, `lužán`, `piler` are
truncations left by column breaks in the source PDFs. They pass every morphology check
precisely because no morphology exists for them, which is the same signal that admits
`dpmb` and `štetl`. The discriminating test is attestation: a no-paradigm entry must
occur as a standalone token in the publication. `stetl` occurs 3 times, `archi` 2,
`stivin` 1; every fragment occurs 0. `theme_tier.py --corpus` now enforces this.

**Declined initialisms.** `MUNI` has a MorfFlex paradigm, so expansion produced `munu`.
`theme_tier.py --output-literal` separates entries with no paradigm, and
`theme_expand.py --literal` exempts anything on a literal list from generation —
including entries that *do* have a paradigm, which is how `vut`, `jamu` and `muni` are
handled. Same mechanism for lexicalised idiom forms: `v čudu`, `konec, šlus`.

**Over-declined street names.** Automated inflection is grammatical and not always
idiomatic. `na Orlí` and `na Veveří` are fixed in use, so `orlímu` is wrong even though
it parses; and `líšně` analyses to both `Líšeň_;G` (the Brno district) and `Líšno_;G`
(a different village), so generating from the second yielded `líšna`. Both go on the
literal list. This is the one place where the pipeline still needs a human to name a
dozen words per city.

**Masthead names.** Editors and staff recur in every issue, so salience promotes them.
`metropolitan_theme_dict.py` already carries some in `DEFAULT_STOPWORDS`;
`resources/metropolitan/denylist.txt` adds the ones it misses for this title.

### 3.6 Delivered artifact

`local/trials/metro_theme_g09_fill.txt`, grid `local/rich/grids/g09_headroom48c.txt`,
dictionary `local/rich/metro_v5.dict` (875 entries), `--max-shared-substring 4`,
**5 cores, 8 theme words reached at 92 s** of a 900 s budget, zero same-lemma defects:

```
##ztp#srpnem###      ZTP     karta v sale, str. 20
ubere#borotín##      ZELNÉM  trh, kde se mele
polit#obstaral#      PETROVA katedrala nad Denisovymi sady
sunar#ratan#dom      BOROTÍN obec v okrese Blansko
adélou####opera      PÍTKA   mesto jich osazuje dalsi
lam#vzlet#líšeň      LÍŠEŇ   mestska cast s hradem Belcredi
###galaxie#teta      LIPKA   skolske zarizeni pro ekologickou vychovu
kope#edikt#klas      PARO    participativni rozpocet
otok#milenka###
návod#nutil#rop
alana####kopali
tel#lipka#budil
#telemark#olovo
##čekáren#uznat
###týmové#kyu##
```

`--max-shared-substring 4` rather than the project's usual 5 is deliberate: it is what
blocks the same-lemma pair described in section 7. It costs nothing measurable here —
the run still reached eight at 92 s.

---

## 4. Ranked bottlenecks

1. **Tier precision.** 7 % of the old theme list was recognizable. This is the
   cheapest fix and the largest multiplier, and the signal needed for it was already
   sitting in the score column. Fixed by `theme_tier.py`.
2. **Grid geometry.** Worth 6 to 9 theme words at a third of the compute. Fixed by
   choosing a template on `<d(L)/L>` rather than by eye.
3. **Supply at the demanded lengths.** Necessary but not sufficient: inflection alone
   moved the metric and not the product. Fixed by `theme_expand.py`, and by admitting
   initialisms, which are the only theme words that fit 46 % of a Czech 15x15.
4. **Crossing cost.** The residual. It is not addressable by more dictionary; it is a
   property of pinning rare letter patterns into a dense lattice, and the only levers
   are geometry (2) and short theme entries (3).

More wordlist is therefore the *third* answer, not the first — and only wordlist of
the right two kinds: initialisms, and inflected forms of an already-precise tier.

---

## 5. Two channels, and do not confuse them

A word can read as thematic in two ways, and only one of them belongs in the
Preferred tier.

**Local answer.** `ZABOVRESKY`, `SALINA`, `VUT`, `LUZANKY`. Expensive: limited by
Czech morphology to length 5+ except initialisms, and each one costs crossing
freedom. This is what the Preferred tier is for.

**Local clue.** `BYT` clued as "obecnich jich Brno vlastni 28 tisic, str. 14".
Free: works at any length, costs no crossing freedom, and needs only the fact bank.
`scripts/number_facts.py` extracts 140 such facts from this issue with page
provenance, and 48 carrier lemmas (`byt`, `dum`, `tym`, `vuz`, `dron`, `metr`,
`jizda`, `razba`, `zastavka`).

Putting carriers in the Preferred tier is a mistake, and it is the mistake that
produced the intermediate result in 3.1 and a later one where a 1,312-entry tier
reached nine Preferred hits that were `popa`, `opatem`, `arena`, `hora`, `paro`,
`opat`, `kinu`, `aren`, `hala` — nationally common words with a Brno hook in the
clue and none in the grid. Carriers belong to `CLUES.md` sec. 11 step 2, the clue
assignment pass, which runs after the fill and spends no search budget.

Test for tier membership: **would a reader recognize this word with the clue covered?**
`HORA` no. `LUZANEK` yes.

---

## 6. Generalizing to another magazine

Everything above except one file is publication-independent.

| step | tool | publication-specific input |
|---|---|---|
| 1. extract candidates | `metropolitan_theme_dict.py` | issue PDFs/text |
| 2. grade the tier | `theme_tier.py` | `--corpus` for acronym mining, `--allowlist` |
| 3. expand to surface forms | `theme_expand.py` | none |
| 4. fact bank | `number_facts.py` | issue text |
| 5. choose a template | `fill_margin.py`, `<d(L)/L>` | none |
| 6. search | `ingrid_core --preferred-wordlist` | none |

The signals in step 2 hold for any publication: a magazine's own vocabulary is
nationally rare, its most frequent distinctive tokens are people's names, and its
initialisms are the only short theme words it has. Step 5 depends only on the target
language's word-length distribution.

Two things must be authored per publication: the ~40-entry allowlist of terms the
publication owns outright, and `--min-length 3` instead of 5 so the initialisms
survive extraction. Both are one-time.

### The recipe, and the one shortcut that quietly ruins it

```sh
MD=/path/to/czech-morfflex2.1-250909.dict
cat resources/blocklist_cs.txt resources/metropolitan/denylist.txt > /tmp/deny.txt

# 2a. the salience-scored theme list: its own ranking is authoritative above 200
python3 scripts/theme_tier.py --model $MD \
  --input local/trials/metro_brno_preferred.dict \
  --reference local/cstenten.dict --max-reference-score 41 \
  --trust-input-score 200 --keep-common \
  --corpus issue.txt --mine-acronyms \
  --allowlist resources/metropolitan/allowlist.txt --denylist /tmp/deny.txt \
  --output tier_a.dict --output-literal tier_a_literal.dict

# 2b. a hand-curated short list: NO --trust-input-score, its scores mean something else
python3 scripts/theme_tier.py --model $MD \
  --input local/rich/metro_short_local.dict \
  --reference local/cstenten.dict --max-reference-score 41 --keep-common \
  --corpus issue.txt \
  --allowlist resources/metropolitan/allowlist.txt --denylist /tmp/deny.txt \
  --output tier_b.dict --output-literal tier_b_literal.dict

cat tier_a.dict tier_b.dict | sort -u -t';' -k1,1 > tier.dict
cat tier_a_literal.dict tier_b_literal.dict | sort -u -t';' -k1,1 > literal.dict

# 3. expand; initialisms and idiom forms bypass generation
python3 scripts/theme_expand.py --model $MD --lemmas tier.dict \
  --literal literal.dict --literal resources/metropolitan/initialisms.dict \
  --allowed-variants '-' --standard local/trials/standard_clued_n33.dict \
  --denylist /tmp/deny.txt --output theme.dict --report theme.csv

# 6. fill
./target/release/ingrid_core --preferred-wordlist theme.dict \
  --wordlist local/trials/standard_clued_n33.dict --blocklist resources/blocklist_cs.txt \
  --min-score 30 --max-shared-substring 4 --cores 5 --timeout 900 \
  local/rich/grids/g09_headroom48c.txt
```

**Steps 2a and 2b must be separate calls.** Passing both lists to one invocation is
the obvious simplification and it silently destroys precision, because the two score
columns mean different things: 200 in the salience-ranked list means "distinctive for
this publication", while 200 in a hand-curated list means "the curator liked it".
`--trust-input-score 200` then waves through `cena`, `den`, `kino`, `kolo`, `drak`,
`pop` and `vila`. Measured: the merged call produces a 1,036-entry tier that reaches
**8** Preferred hits on `g09` in 240 s — the same count as the correct recipe — of
which only `bvv` and `lipka` are recognizable. Same metric, a quarter of the product.
That is the failure mode of this whole exercise in miniature.

---

## 7. Open items

- **`--estimate-variants` produces no estimate here.** On the baseline template the
  estimator reports `insufficient evidence, 0 / 8 accepted SMC replicates` at
  preferred >= 5, >= 3 and even >= 0. The cause looks structural rather than
  budgetary: the independent-walk path sizes its cohort from measured throughput,
  while the SMC path uses a fixed `minimum_walks.max(4)` = 8 replicates. Given
  `--timeout 900 --estimate-runtime-ratio 0.5 --estimate-max-time 600` the estimator
  used 116.9 s (13 % of search time) and stopped, so raising the budget cannot help.
  Scaling the replicate count with the remaining budget would make the tool usable on
  grids of this difficulty, which is exactly where the theme question needs it.
- **Same-lemma pairs are a systematic consequence of inflection expansion.** Observed
  live: `opat` + `opatem`, `arena` + `aren`, and `lužánky` + `lužánek` in one fill.
  `--max-shared-substring 4` blocks the last of those (`lužán` is a 5-run) and costs
  nothing measurable — the delivered run still reached eight theme words in 92 s — but
  it cannot block `opat`/`opatem`, whose shared run is only 4. The correct fix is a
  lemma-aware constraint in `dupe_index.rs`: the dictionary now knows the lemma of
  every generated form, because `theme_expand.py --report` carries it. Until then the
  check is five lines over `(fill, report CSV)` and belongs in the `CLUES.md` sec. 11
  step 3 controller, which already owns "shodny koren dvakrat v jedne mrizce".
- **`--allowed-variants` should probably default to `-` for a theme tier.** The
  Standard-tier convention accepts variant `1` (rarer but standard). On proper names
  that admits colloquial obliques like `veveříma`. The delivered dictionary was built
  with `--allowed-variants -`.
- **`theme_seed.py` is a measurement tool, not yet a construction tool.** Its greedy
  placement commits without backtracking, so it measures the difficulty of a template
  faithfully but cannot construct a high-theme fill the way a human constructor would.
  Backtracking over placements with the solver as oracle is the obvious next step.
- **Two-cell legend capacity is unexploited.** `g09` has 34 adjacent block pairs
  against the baseline's 16, which `CLUES.md` sec. 2 values at 70 characters instead
  of 34. Nothing downstream consumes that yet.

---

## Appendix — how the numbering in the HTML preview works

`local/metropolitan-krizovka-2026-07-30.html` numbers cells, not clues, in the standard
American convention. Verified against `no_marked_n33_fill.txt`:

- A cell gets the next number if it **starts an entry**, across or down. Numbers run in
  reading order, left to right then top to bottom.
- The two clue lists draw from **one shared pool of cell numbers**. A number appears in
  both lists when its cell starts both an across and a down entry, and in only one list
  otherwise.

On that grid the highest number is **63** for **74 entries** (35 across, 39 down):

| | count |
|---|---:|
| numbers starting both an across and a down entry | 11 |
| across only | 24 |
| down only | 28 |

So both lists skip numbers, and that is normal. Worked example around the numbers that
prompted the question:

| number | cell | Vodorovne | Svisle |
|---:|---|---|---|
| 40 | r9 c5 | VELET | VELEL |
| 41 | r9 c11 | VALEM | VOZE |
| 42 | r10 c4 | NEPOZOROVANE | NEVEDE |
| 43 | r10 c10 | — | RAZICI |
| 44 | r11 c1 | ODJELO | OTROK |
| 45 | r11 c2 | — | DRESY |
| 46 | r11 c3 | — | JAPAN |
| 47 | r11 c8 | DRAZE | — |

41 and 42 are in both lists because those two cells each open an across *and* a down
entry. 43, 45 and 46 are missing from Vodorovne because they sit mid-word horizontally
while opening a down entry — they are the cells directly under a block. 47 is missing
from Svisle for the mirror reason.

The asymmetry that makes Svisle look dense early and Vodorovne look sparse is the top
row: every white cell in row 1 opens a down entry, but only the leftmost cell of each
horizontal run opens an across entry. Later in the grid it reverses.

One thing to keep in mind: this is a **preview artifact, not the target format**.
`CLUES.md` sec. 2 specifies a Swedish grid with legends inside the cells and only
**6-10 numbers** on the whole grid, pointing into a margin (the NUM tier). In that
format there is no numbered clue list to skip anything, and a number is a promise that
the margin holds something that could not be compressed into 34 characters
(`CLUES.md` sec. 2, "Pravidlo pro NUM").
