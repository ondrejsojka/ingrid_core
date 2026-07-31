# Fillability slack

## Purpose

Fillability slack is a structural measure of how much freedom remains in a
crossword-filling problem after accounting for its shape, fixed letters, word
lists, and crossing constraints. It is intended to answer questions such as:

- How much riskier does this grid become if we restrict the Standard list?
- How much does requiring another Preferred entry consume the remaining slack?
- Is a configuration comfortably fillable, fragile, or already effectively
  unfillable?

It deliberately does **not** measure solver speed or expected work. Search time
is affected by heuristics, implementation details, random seeds, and hardware;
fillability slack is meant to describe the constraint problem itself.

## The exact quantity

For a fully specified crossword configuration and Preferred target `k`, let

- `Z_k` be the number of distinct valid fills containing at least `k` Preferred
  entries.

A valid fill must satisfy every real Ingrid constraint, including crossings,
fixed entries, duplicate prevention, the shared-substring rule, and any future
morphological-family exclusions.

The ideal fillability slack is

```text
S_k = log2(Z_k)
```

This definition gives the unit a direct meaning:

- `S_k = 10` means exactly `2^10 = 1,024` valid fills.
- Each additional bit doubles the number of valid fills.
- Losing one bit halves it.
- `S_k = 0` means **exactly one valid fill**. The puzzle is fillable, but has no
  combinatorial redundancy.
- An unfillable puzzle has `Z_k = 0`, so its slack is `-infinity`.

The exact quantity never has a finite negative value: a finite set cannot
contain a fractional number of fills. Unfortunately, computing `Z_k` exactly is
a model-counting problem and is generally much harder than finding one fill.
The practical metric below is therefore an approximation to `S_k`.

## Inputs

The metric is computed for one complete configuration:

- grid shape and dimensions;
- blocks and fixed letters or entries;
- eligible Standard word list after score and morphology filtering;
- eligible Preferred word list;
- duplicate and shared-substring policies;
- minimum Preferred count `k`.

Scores used only for search ordering do not change structural slack. A score
threshold does change slack because it changes which words are eligible.

If a word occurs in both lists, it is one candidate word and is classified as
Preferred.

## Slot domains

For every slot `s`, construct a domain `D_s` containing every eligible word
that:

1. has the correct length;
2. matches all fixed letters;
3. survives the configured unary filters.

Then enforce ordinary crossing arc consistency to a fixed point: remove an
option when it has no letter-compatible option in an intersecting slot, and
repeat until no domain changes.

If any domain becomes empty, the configuration is proven unfillable and its
slack is `-infinity`. Otherwise, the reduced domains are the input to the
approximation.

A fully fixed slot has a domain of size one and contributes zero domain bits.

## Crossing compatibility

Consider a crossing `c` between positions in slots `a` and `b`. For each glyph
`g`, define

```text
f_a,c(g) = fraction of words in D_a having g at crossing c
f_b,c(g) = fraction of words in D_b having g at crossing c
```

The probability that uniformly selected options from the two domains agree at
the crossing is

```text
p_c = sum over g of f_a,c(g) * f_b,c(g)
```

For example, `p_c = 0.05` means that about 5% of independently selected option
pairs agree at that crossing.

## Practical base-slack estimator

Before applying a Preferred minimum, define

```text
             sum                         sum
S_hat_0 =  -------- log2(|D_s|)  +  -------------- log2(p_c)
             slots                      crossings
```

Equivalently,

```text
N_hat_0 = product over slots |D_s| * product over crossings p_c
S_hat_0 = log2(N_hat_0)
```

`N_hat_0` starts with the number of assignments obtained by independently
choosing one option for each slot, then discounts that number by the estimated
cost of making every crossing agree.

This is an independence approximation. Crossings attached to the same slot are
not actually independent: letters within a word are correlated, and crossword
constraint graphs contain many loops. Duplicate, shared-substring, and
morphological-family constraints also introduce non-crossing dependencies.
Consequently, `N_hat_0` is neither a certified upper nor lower bound.

## Preferred-target slack

The Preferred constraint consumes additional slack. For each slot, define

```text
q_s = |D_s intersect Preferred| / |D_s|
```

A fixed Preferred entry has `q_s = 1`; a fixed Standard entry has `q_s = 0`.

Under the same independence approximation, the generating polynomial for the
number of Preferred entries is

```text
G(z) = product over slots ((1 - q_s) + q_s * z)
```

The coefficient of `z^j` is the estimated probability of selecting exactly
`j` Preferred entries. Therefore,

```text
T_k = sum of coefficients of z^j for all j >= k
```

is the estimated probability of satisfying a minimum of `k` Preferred entries.
The target-specific estimator is

```text
S_hat_k = S_hat_0 + log2(T_k)
```

This produces a complete Preferred frontier rather than one number:

```text
k:       0      1      2      3      4   ...
slack:  ...    ...    ...    ...    ...
```

The decrement from `S_hat_k` to `S_hat_(k+1)` quantifies how much structural
freedom the next Preferred requirement is estimated to consume.

## What does zero mean?

There are two related answers.

### Exact slack

For the ideal definition `S_k = log2(Z_k)`, zero has an exact meaning: there is
one and only one valid fill. This is the natural zero-slack boundary. Unfillable
is not zero; it is `-infinity`.

### Estimated slack

For `S_hat_k`, zero means that the independence model predicts one compatible
fill:

```text
N_hat_k = 2^0 = 1
```

A finite negative estimate means the model predicts fewer than one fill on
average and is therefore a strong risk signal. It does not literally mean a
negative or fractional number of real fills.

Because the estimator ignores important correlations, estimated zero is not a
universal, calibrated fillable/unfillable boundary. A grid with positive
estimated slack may still be globally inconsistent, while a modestly negative
estimate could theoretically have a rare valid fill. Only an empty domain after
sound constraint propagation or an exhaustive proof can certify
unfillability.

The most reliable interpretation is comparative:

- a difference of `-1` bit means approximately half as much estimated slack;
- `-10` bits means approximately `1/1,024` as much;
- crossing from strongly positive to strongly negative is a serious warning;
- values should be calibrated against known grids built with the same rules and
  dictionary family.

Raw slack, rather than slack per slot, is the relevant distance from the
one-fill boundary. `S_hat_k / number_of_slots` can additionally describe slack
density when comparing different grid sizes, but it no longer has the direct
"approximately one fill at zero" interpretation for the whole crossword.

## Current-grid example

For the current seeded 15x15 shape, using 74 slots and 191 crossings, the simple
estimator produced:

| Standard policy | Eligible words including Preferred | Median domain after crossing AC | Geometric-mean domain | `S_hat_0` | `S_hat_3` |
| --- | ---: | ---: | ---: | ---: | ---: |
| Broad NAVD | 172,778 | 4,788 | 3,409 | +60.0 | +46.0 |
| No marked-only forms, noncanonical score >=32 | 141,864 | 4,241 | 2,945 | +45.7 | +32.3 |
| Canonical/base forms only | 42,295 | 1,720 | 993 | -31.7 | -41.4 |

The observed behavior followed the ordering:

- the broad list produced a fill with three Preferred entries;
- the restricted-inflection list remained fillable but produced two Preferred
  entries in the measured run;
- the canonical-only configuration failed Ingrid's initial consistency phase.

The absolute values remain optimistic in some regions. For example, positive
estimated slack at a four-Preferred target did not produce such a fill during a
four-hour search. This is evidence that the metric should be used as a
structural risk and comparison signal, not as a solution-count claim.

## Recommended implementation

Expose a command that computes the entire slack frontier before search:

```text
ingrid_core analyze \
  --preferred-wordlist preferred.dict \
  --wordlist standard.dict \
  --min-score 30 \
  --max-shared-substring 5 \
  grid.txt
```

Suggested output:

```text
slots: 74
crossings: 191
empty domains after crossing AC: 0
minimum / median / geometric-mean domain: 1 / 4788 / 3409

minimum preferred    estimated slack bits
0                    60.0
1                    60.0
2                    53.6
3                    46.0
4                    37.6
5                    28.6
...
```

The scheduler can use the curve to place initial workers near the steepest
slack losses while retaining the existing adaptive success, failure, and
cancellation behavior.

## Possible refinements

The estimator can be improved without changing the definition of exact slack:

1. Use loopy belief propagation and Bethe free entropy to account for more
   crossing correlations.
2. Include pairwise duplicate and shared-substring compatibility factors.
3. Add morphological-family incompatibility factors when lemma metadata becomes
   available to the solver.
4. Calibrate a correction by shape family, dictionary policy, and constraint
   settings using a corpus of known fillable and proven-unfillable grids.
5. Report both raw slack and per-slot slack density, while keeping raw slack as
   the primary distance-to-one-fill quantity.

The invariant should remain simple: fillability slack is an approximation to
`log2(number of valid fills)`, and every reported bit represents a factor of two
in estimated structural freedom.

## Review notes, 2026-07-30

### What the independence estimator mismeasures

- The sum hides the bottleneck. Failure is local (an exhausted short-slot
  corner, a Hall-deficient region); slack is global. Nothing in `S_hat_0` warns
  of an imminent AC collapse: the value degrades smoothly up to the phase edge,
  then jumps to `-infinity`. The intended use — tightening list policies until
  they bind — operates in exactly the regime where independence estimates fail
  hardest.
- The Preferred overlay is optimistic in the direction of use. Conditioning on
  validity enriches fills in high-compatibility words, so
  `Pr(slot is Preferred | valid) < q_s` systematically, and the error compounds
  in the tail of `G(z)` — i.e., at the `k` actually being targeted. Both
  calibration points on record agree: row 2 estimated +32.3 bits at `k=3` and
  produced 2 Preferred; a `k=4` target with positive estimated slack filled
  nothing in four hours.
- The estimator does not approximate the stated `Z_k`. It counts solutions of a
  relaxation: duplicate, shared-substring, and morphological-family
  constraints are in the definition of `Z` but not in the estimate. In Czech
  these are first-order: the short-word pool is shared across many slots and
  inflectional endings collide on 4–6 grams, so the substring rule binds
  constantly.
- Count is not usable freedom. `log2 Z` is inflated by morphological
  near-duplicate fills and by variance concentrated in throwaway regions; it
  cannot distinguish one skeleton with corner swaps from genuinely different
  fills. It also cannot see *position*: production needs Preferred entries in
  the numbered cells and wide-adjacent slots, not `>= k` anywhere.
- Zero is the wrong anchor for a quality-selection pipeline. The operative
  budget is the count of fills above the editorial bar — strictly below
  `S_hat_0` by the whole selection margin.
- The claim "describes the problem itself" is not exact: domains are defined
  after AC, so upgrading the propagator changes the reported slack with zero
  change to the problem.

### Per-knob biases for dictionary-policy tuning

The intended dials are unary list filters (csTenTen frequency floor, flexion
filter, Preferred-list trim) on one grid — the metric's best case, and the
ordering within one knob is trustworthy. The knobs are not commensurable:

- frequency floor: roughly honest; smooth degradation that hides the final
  discontinuity;
- flexion filter: measured cost is *overstated* — pre-filter slack counted
  morphological variants the substring rule already forbade (fake freedom);
  safe direction, but non-linear when an ending class is wiped;
- Preferred trim: measured risk is *understated* at the target `k` — an
  issue-themed list is letter-clustered, and Poisson-binomial co-occurrence
  prices the tail too cheaply.

Consequences: never compose per-knob deltas from the ledger — measure policy
combinations jointly; treat anything past the calibrated comfort zone as
unschedulable regardless of what the sum says; quote whole bits, not decimals.
And the metric prices only the fill-survival half of each knob. The editorial
cost (e.g., a flexion filter killing TVAR-hook words like `usima`) must come
from the hook/number-fact columns, not from counting — see below, leaf
predicates.

### Compute ladder for better estimates

More compute at evaluation time buys strictly better approximations to
`log2 Z`:

1. **Belief propagation / Bethe.** One factor per slot, values = words,
   letter-equality factors at crossings; messages are letter-bucketized
   (`O(|D| + alphabet)` per crossing per iteration). Captures intra-word letter
   correlation exactly; loop error remains. Use BP marginals (validity-
   conditioned) in place of raw `q_s`, and tilt by `e^{lambda}` per Preferred
   value to recover the whole `k`-frontier by saddlepoint inversion. Per-word
   marginal mass is a fill-value signal worth feeding back into wordlist
   curation. Deterministic and cheap, but biased on loopy graphs — validate
   against the next rung before trusting it across policies.
2. **Knuth/SMC randomized backwalk.** Sample a root-to-leaf path in the
   solver's own backtrack tree (uniform over surviving values, full propagation
   maintained), multiply by branching factors, validate every constraint at the
   leaf. Unbiased for the count of leaves passing *any* leaf predicate — the
   full constraint set, the `>= k` Preferred rule, positioned theme entries
   (fix NUM/WIDE slots first), and dictionary-column clue-quality predicates
   (`>= 30` hooked entries, `>= 6` number-fact entries): since clue quality is
   implemented as dictionary columns, the walk prices fill-side and clue-side
   quality in one number. Strategy-invariant: MAC, dom/wdeg, and restarts
   reduce variance for free, at zero bias cost. Common random numbers across
   policies make `log2 Z(A) - log2 Z(B)` much tighter than either level. Not
   sample-optimal: near the fill boundary the weight distribution goes
   heavy-tailed and the (honest, empirical) confidence interval widens;
   SMC resampling mitigates. Certified counts with error guarantees are the
   domain of hashing-based counters.
3. **#SAT export as a one-time calibration anchor.** Word-variable one-hot
   encoding with per-crossing clauses; crossing-only count first (D4/GANAK
   exact, or ApproxMC for an `(epsilon, delta)` guarantee), then estimate the
   dupe/substring survival fraction by sampling fills and filtering. Pins the
   absolute scale for a shape family and tells you how optimistic rung 1 is.

### Compatibility with the Ingrid implementation (verified against source)

- Search core is MAC over AC-3 with dom/wdeg slot choice, randomized restarts,
  seedable `SmallRng` (backtracking_search.rs). Propagation revises via
  aggregate cell x glyph counts, i.e., already letter-bucketized (util.rs).
  Per-slot domains are materialized and public (`GridConfig::slot_options`),
  and the incremental AC entry point `establish_arc_consistency` is public, so
  a walk driver can be built against the public API: no complexity-class shift,
  no surgery into search.
- Caveat: dupe elimination in propagation only fires on singleton slots, so
  mid-walk assignments can be dupe-violating — walks must validate dupes at the
  leaf. Preferred minimum is a post-AC hard check, which fits the leaf-
  predicate model directly.
- The parallel scheduler is already a target-minimum ladder with success-
  cancels-easier and failure-cancels-harder (parallel_search.rs) — the
  mechanism the "place workers at steepest slack loss" idea wanted; the slack
  curve is not needed for it. `SearchEvent` telemetry (elapsed, backtracks,
  retries, discovered preferred) already provides the slack-vs-success
  calibration corpus, including time-to-first-fill from first-success elapsed.
- For a `#SAT` export, domains and crossings enumerate via public config; the
  concrete dupe groups sit behind `Box<dyn AnyDupeIndex>` and need an
  accessor; morphology data does not exist in core (external MorphoDiTa only),
  so the export initially counts the same relaxation as the analytic metric.

### Roles

- BP: daily pre-search screen for "did this policy shift the structure",
  plus per-word fill-value diagnostics for curation.
- Walk: offline arbiter for "how many real fills did this dial-notch cost",
  including clue-quality leaf predicates and positioned theme counts; failure
  histograms from dead walks localize fragile regions.
- #SAT counter: one-time certified anchor per shape family.
- The analytic `S_hat` remains a within-grid, nested-list, monotone-ordering
  screen — its honest use case — and nothing more.
