# Fill margin

## Scope

This document defines a **pre-search** measure of how much further a crossword
configuration can be tightened before it stops filling, and a **price list** that
quotes what each candidate quality policy costs against that budget.

It is deliberately disjoint from the variant estimator (`--estimate-variants`),
which is a **post-search** instrument: that one takes an incumbent fill and
reports how many distinct fills surround it, with a confidence interval and ESS.
The two answer different questions at different points in the lifecycle:

| | fill margin | variant estimator |
| --- | --- | --- |
| when | before any search | after a successful fill |
| needs an incumbent | no | yes |
| cost | one arc-consistency pass | seconds to minutes of sampling |
| reusable across shapes | yes (`kappa-cost` is shape-stable) | no, per-instance |
| answers | should I attempt this at all; what does this policy cost | how much variety do I have around this fill |

Neither subsumes the other. See "Interface with the variant estimator" below.

## Why not a solution count

The intuitive measure is the number of valid fills, `Z`, reported as
`log2(Z)` bits. That quantity is real and worth estimating, but it is not a
fillability decision procedure, because **the operational boundary does not sit
at any fixed value of `Z`**. Measured on this repository's grids and word lists:

- `gen_9x9` at `--min-score 44`: annealed `log2 <Z>` = **+18.3** bits, fills in
  **0.63 s**.
- `czech_13x13_reader` at `--min-score 30`: annealed `log2 <Z>` = **+16.3** bits,
  no fill in 180 s — two bits *lower* than `gen_9x9`, and at least 285 times
  worse to solve.
- `edition_mid` at `--min-score 36`: satisfiable — a fill exists and was found —
  but it took **889.8 s**, versus 15.7 s at `--min-score 34`.

Solutions still exist well past the point where search stops finding them. That
gap is the clustering (dynamical) transition, and it precedes the satisfiability
transition. A perfect count of `Z` will faithfully report a large positive
number for a configuration that will not produce a puzzle within any usable
budget.

## The quantity

For a fully specified configuration, run crossing arc consistency to a fixed
point and let `D_s` be the reduced domain of slot `s`. Let

```text
p_c      = sum over glyphs g of f_a,c(g) * f_b,c(g)      for crossing c between slots a, b
S_hat_0  = sum over slots log2|D_s|  +  sum over crossings log2 p_c
```

`S_hat_0` is the **annealed log-count**: the first moment `log2 <Z>` under an
independence model. It is the numerator, not the metric. Then

```text
kappa  =  1  -  S_hat_0 / sum over slots log2|D_s|
```

`kappa` is **constrainedness** in the sense of Gent, MacIntyre, Prosser & Walsh
(AAAI-96). It is dimensionless, so it is comparable across grid sizes; that is
the entire reason it works where raw bits do not.

Calibrated against measured solver outcomes (below):

```text
kappa*        =  0.95        critical constrainedness: the search cliff
fill margin   =  kappa* - kappa
```

A configuration with positive fill margin is **robust**; margin is the budget you
have left to spend on quality.

An empty domain after propagation still means proven unfillable, independent of
`kappa`. Note that Ingrid's own initial consistency phase is stronger than
crossing AC — it also propagates dupe and shared-substring eliminations from
singleton slots — so it will reject some configurations that this measure scores
as merely tight.

### Relation to Bethe free entropy

`S_hat_0` is not an ad hoc product. Build the factor graph with cells as
variables (values = glyphs) and slots as factors (the word list). Initialise
cell-to-slot messages uniform and take slot-to-cell messages one step. Then
`Z_c = sum_g f_a(g) f_b(g) = p_c` exactly, the alphabet factors cancel against
the edge terms, and the Bethe free entropy equals `S_hat_0` identically.
Verified numerically at `--min-score 30`:

| shape | `S_hat_0` | Bethe, zero iterations |
| --- | ---: | ---: |
| `edition_mid` | 84.036 | 84.036 |
| `med` | 60.930 | 60.930 |
| `split_long` | 101.376 | 101.376 |

So the closed form is rung zero of a compute ladder: running loopy BP to
convergence on `edition_mid` at `--min-score 30` gives 84.0 -> 96.5 bits
(0 -> 160 iterations, damping 0.7). Useful to know, but see the caveat below:
converged BP did **not** tighten the location of the cliff, only the estimate of
`Z`. For threshold location the cheap closed form is sufficient.

## Calibration

119 measured (shape x policy) points: 64 fill, 55 fail. Eight shapes spanning
36 to 88 slots and `sum log2|D_s|` from 348 to 959. Four kinds of knob:
frequency cut, morphological filter, corpus swap, diacritic folding. Solver run
single-core with `--max-shared-substring 5`, budget 60-300 s depending on batch.

Best single threshold on each candidate discriminator:

| discriminator | threshold | misclassified |
| --- | ---: | ---: |
| **kappa** | **0.950** | **4 / 119 (97%)** |
| `S_hat_0` raw bits | +44.0 | 7 / 119 (94%) |
| eligible word count | >= 81,000 | 25 / 119 (79%) |

All four `kappa` errors lie within +/-0.02 of the line, which is the finite
width of the transition rather than a failure of the statistic:

```text
edition_mid @min-score 36        kappa 0.970   filled, in 890 s
edition_mid no-marked+noncanon33 kappa 0.952   filled, in 179 s
med         no-marked-only       kappa 0.961   filled, in 57 s
med         @min-score 30        kappa 0.935   no fill in 60 s
```

**`kappa*` is a search threshold, so it moves with the search budget.** The last
row is the clearest illustration and the dataset contains it twice: `med` at
`--min-score 30` found nothing in 60 s but did fill in 87 s when given 180 s.
That configuration sits directly on the line, and both labels are kept rather
than reconciled. Read 0.95 as "the cliff at a one-to-three-minute budget."
Widen the budget and it drifts up slightly; the calibration should be redone
against whatever budget production actually uses.

### Why raw bits fail

The cliff location in bits moves with grid size; in `kappa` it does not.

| shape | slots | `sum log2\|D_s\|` | tightest fill: kappa | tightest fill: `S_hat_0` |
| --- | ---: | ---: | ---: | ---: |
| `gen_9x9` | 36 | 348 | 0.946 | **+18.3** |
| `gen_11x11` | 44 | 473 | 0.950 | +25.9 |
| `edition_mid` | 72 | 910 | 0.970 | +26.4 |
| `med` | 72 | 901 | 0.961 | +36.2 |
| `split_long` | 74 | 936 | 0.947 | +49.1 |
| `chambers` | 88 | 959 | 0.934 | **+52.7** |

`kappa` spans 0.934-0.970. `S_hat_0` spans +18.3 to +52.7 over the same
boundary, and the ordering inverts: `gen_9x9` fills in 0.63 s at +18.3 bits
while `med` at +34.1 bits finds nothing in 120 s. No bits threshold separates
those; `kappa` (0.946 versus 0.963) does.

Two shapes are dead at every setting tested and `kappa` says so before any
search: `czech_13x13_reader` (48 slots, 142 crossings, kappa 0.976 at the
loosest list) and `connected` (54 slots, 194 crossings, kappa 1.084 at the
loosest list). Both are provably unfillable a few score points further up.

## The price list

`kappa-cost` of each policy move relative to `cstenten.dict --min-score 30`
(185,116 words), measured on `split_long` / `edition_mid` / `med`. The scoring
scale is `score = round(10 * log10(freq))`, so ten score points is one decade of
corpus frequency. Outcome column: `F` fill, `-` not found in 180 s.

| policy move | knob | words | **kappa-cost** | outcome |
| --- | --- | ---: | ---: | :---: |
| diacritics folded, `--min-score 30` | orthography | 172,321 | **-0.067** | FFF |
| diacritics folded, `--min-score 36` | orthography | 80,510 | **-0.016** | FFF |
| _baseline: cstenten `--min-score 30`_ | frequency | 185,116 | 0.000 | FFF |
| lemmas only | morphology | 148,560 | **+0.012** | FFF |
| NAVD POS filter, `--min-score 30` | morphology | 172,699 | +0.021 | FF- |
| no marked-only forms | morphology | 170,265 | **+0.025** | FFF |
| `--min-score 34` (freq >= 2,512) | frequency | 112,943 | +0.036 | FF- |
| no marked + noncanonical >= 33 | morphology | 129,379 | +0.041 | FF- |
| no marked + noncanonical >= 35 | morphology | 108,106 | +0.052 | F-- |
| `--min-score 36` (freq >= 3,981) | frequency | 85,846 | +0.059 | --- |
| NAVD `--min-score 35` | morphology+freq | 92,375 | +0.067 | --- |
| canonical forms only | morphology | 42,209 | **+0.126** | --- |

**`kappa-cost` is shape-stable to +/-0.008** across the three shapes. That is what
makes the table reusable: quote a price once, apply it to any grid.

**Word count is not the currency.** `lemmas only` has 148,560 words and
kappa 0.908; `NAVD POS filter` has 172,699 words and kappa 0.916. The smaller
list is less constraining, consistently on all three shapes.

## Using it

```text
budget = 0.95 - kappa
```

| shape | kappa at baseline | budget |
| --- | ---: | ---: |
| `split_long` | 0.896 | 0.054 |
| `edition_mid` | 0.911 | 0.039 |
| `med` | 0.935 | 0.015 |
| `czech_13x13_reader` | 0.976 | -0.026 (no fill ever observed) |

Consequences for the current recipe:

- **Folding diacritics refunds 0.067 — more than the entire budget on any
  shape.** It is by a wide margin the largest single lever in either direction.
  Folded, `no marked-only` plus `--min-score 36` fit together; unfolded, roughly
  one mid-sized move fits. Traditional Czech crosswords conventionally ignore
  diacritics, so the editorial cost may be smaller than it looks. This is the
  first decision to make, because it sets the size of every subsequent one.
- **`canonical forms only` costs 0.126, about three times the budget on
  `edition_mid`.** Its documented failure was structural, not bad luck.
- **`med` has essentially no budget at the current setting.** Change the shape,
  not the word list.

**Prices are sub-additive; do not sum them.** `no marked` (+0.025) plus a
`--min-score 33` cut (~+0.026) predicts +0.051; the measured composite is
**+0.041**, because the two filters remove overlapping words. Use the table to
rank single moves; price a combination by computing `kappa` for the combined
dictionary directly, which is one arc-consistency pass.

## What this does not price

- **Tailoring.** Preferred placement is a bipartite matching problem, not a
  `kappa` shift. On `czech_15x15_split_long`, 53 of 74 slots are length 3-5,
  while `metro_context_reader` (108 entries) and `metro_context_balanced` (276)
  contain no word under 7 letters: exactly **11 slots** can host any of them, and
  those 11 mutually cross at 10 points. 18 of 108 and 30 of 276 pool words are
  structurally unplaceable because the grid has no length-9 or length-11 slot.
  Budget theme placement separately, by reachability and set packing.
- **`--max-shared-substring`.** Extending the first moment with a pairwise
  5-gram conflict term over all slot pairs costs **-0.2 bits** across 780 pairs,
  `kappa-cost` 0.000. It is free at this level. (It still shapes *which* fills
  exist and can matter through singleton propagation; it does not move the
  count.)
- **Editorial value.** `kappa` is the cost side only. What a hook, a place
  anchor, or a free number-fact clue is worth is a judgement call; `kappa-cost`
  only makes the exchange rate visible.
- **Derivation of `kappa*`.** 0.95 is a calibrated constant for this dictionary
  family and this solver, not a theorem. It held across 8 shapes and 4 knob
  types. Re-measure with about six bisection runs if the corpus family or the
  search heuristics change.

## Interface with the variant estimator

`kappa` gates; the estimator reports.

```text
kappa <= 0.95         search; on success, run --estimate-variants.
0.95 < kappa <= 1.00  marginal. Fills exist up to 0.970 but cost minutes;
                      expect no incumbent at a normal budget.
kappa >  1.00         no fill observed at any point in the calibration set.
                      Do not search. Refutation is usually fast above 1.025.
```

Supporting counts: across all 119 points the highest `kappa` that ever produced
a fill is **0.970**, and no fill was observed above 1.00. Proven-unfillable
points span `kappa` 1.025 to 1.414.

This removes the class of runs where the estimator can only answer "insufficient
evidence." Conversely, `insufficient evidence` on its own is uninterpretable;
next to `kappa = 0.97` it is a decision.

Two further couplings worth testing:

1. The estimator's `Z_hat` can replace the annealed numerator:
   `kappa_hat = 1 - log2(Z_hat) / sum log2|D_s|`, giving a measured
   constrainedness with error bars and a principled recalibration of `kappa*`.
   Prediction, from the BP experiment: this improves the number and barely moves
   the threshold.
2. The estimator's ESS is an independent instrument for locating `kappa*`.
   Effective sample size should collapse at the clustering transition regardless
   of grid size. Plotting ESS against `kappa` over the 119 calibration points
   would confirm or refute 0.95 far more strongly than outcome labels do.

## Suggested output

```text
slots / crossings:        74 / 191
empty domains after AC:   0
min / median / geomean domain:  1482 / 9472 / 9109
kappa:                    0.896      [cliff 0.95, no fill observed above 1.00]
fill margin:              0.054
  fits:                   lemmas-only (+0.012) . no-marked (+0.025) . min-score 34 (+0.036)
  does not fit:           min-score 36 (+0.059) . NAVD 35 (+0.067) . canonical-only (+0.126)
log2 <Z> (annealed):      +101.4     [first moment; = Bethe at zero BP iterations;
                                      not a fill count -- see --estimate-variants]
```

Cost is one crossing-AC pass plus one bincount per crossing, on top of the
word-list load already paid.

## Data

Committed under `calibration/`, because `kappa* = 0.95` is a calibrated constant
and is otherwise unfalsifiable by anyone else. Everything else the sweep touched
(filtered dictionaries, per-run logs) lives in the gitignored `local/` tree and
is reproducible from `scripts/` plus `cstenten.dict`.

- `calibration/kappa_all_measurements.csv` — the 119 measured (shape x policy)
  points behind the threshold comparison: `shape, label, knob, kappa, S0, words,
  fill`.
- `calibration/kappa_calibration.csv` — the frequency sweep with per-run
  outcomes, timings, `minD`, `gmD`, and glyph-support statistics.
- `calibration/gen_11x11.txt`, `calibration/gen_9x9.txt` — generated small
  shapes used for the cross-size transfer test, which is what separates `kappa`
  from raw bits.

## References

- Gent, MacIntyre, Prosser & Walsh, *The Constrainedness of Search*, AAAI-96 —
  `kappa`, and the `kappa ~ 1` transition.
- Cheeseman, Kanefsky & Taylor, *Where the Really Hard Problems Are*, IJCAI-91;
  Mitchell, Selman & Levesque, AAAI-92 — the easy-hard-easy cost curve, which the
  measured `edition_mid` timings reproduce (5 s -> 890 s -> undecided at 47 min ->
  refuted in 9 min).
- Krzakala, Montanari, Ricci-Tersenghi, Semerjian & Zdeborova, PNAS 2007 —
  clustering transition before the satisfiability transition; the reason fills
  exist but are unreachable in the band above `kappa*`.
- Valiant 1979 — `#P`-completeness of counting; why `Z` is harder than a fill.
- Yedidia, Freeman & Weiss, 2005 — Bethe free energy, of which `S_hat_0` is the
  zero-iteration case.
- Mezard & Montanari, *Information, Physics, and Computation*, 2009 — annealed
  versus quenched averages; `log <Z> >= <log Z>` is why the first moment is
  optimistic.
