# The persistent fillability oracle

Choosing a template is a search over millions of grids, and the inner question is always the same:
*is this grid still fillable?* `ingrid_core` answers that exactly — initial arc consistency decides
`Unfillable` before the search starts, and it honours the dupe index and the shared-substring
constraint, which no per-slot pattern screen can. The problem was never the answer, it was the
handshake: a one-shot invocation reloads the whole dictionary per question.

The oracle loads once and answers many. That is the entire feature.

## Three verdicts, never a boolean

| verdict | means | what you may conclude |
| --- | --- | --- |
| `unfillable` | Initial arc consistency wiped out a slot's domain, **or** the search popped its last choice and refuted the entire tree. | A proof. Prune. |
| `fillable` | A complete fill was found. | It fills; ask for the fill if you want it. |
| `unknown` | The probe budget ran out with neither a fill nor a refutation. | **Nothing.** This is not a rejection. |

Collapsing `unknown` into `unfillable` is how a constructor comes to report a perfectly good
template as saturated, and it is why the Python `Verdict.__bool__` raises instead of guessing.

With the default zero budget, every grid that survives arc consistency comes back `unknown`. That
is the intended cheap mode: it prunes hard and never lies about the remainder.

## The corpus does not move

A fully specified slot needs a `WordId` for its letters whether or not they spell a dictionary
entry, so `generate_slot_options` calls `WordList::get_word_id_or_add_hidden`, which appends a
hidden word to `words`, `word_id_by_string` and the shared-substring index. That is correct inside
one grid — the pinned entry has to participate in the dupe rules — and poison across grids: a
service answering millions of questions would end up answering them against a dictionary shaped by
whichever templates arrived first.

So every probe brackets its configuration work:

```rust
let corpus = word_list.snapshot();
let probe = {
    let owned = generate_grid_config_from_parsed(&mut word_list, &parsed, min_score, order);
    run_probe(&owned, ...)
};                                  // the config's borrow of the corpus ends here
word_list.rewind(&corpus);          // exact, not approximate
```

`WordList::rewind` replays the appends backwards, unmaps their strings, calls the new
`AnyDupeIndex::remove_word` to take them out of every substring group they joined, and drops glyphs
added for characters the dictionary had never seen. It restores appended state only: words hidden
by `--blocklist` and other in-place edits are campaign state and are left alone.

Two details are load-bearing rather than incidental:

- **Reverse insertion order, not bucket order.** The dupe index can only reclaim a substring group
  while that group is the most recent one, and a grid's fully specified slots are visited in slot
  order, so their lengths interleave. Walking the length buckets would remove a four-letter pin
  before a five-letter one appended after it and strand the four-letter pin's group — one leaked
  group and substring key per probe. `WordList` therefore keeps an `append_log` of the bucket each
  word went into, `WordListSnapshot` captures its length, and `rewind` pops it from the end.
  `test_rewind_reclaims_groups_when_lengths_are_appended_out_of_order` fails against a bucket-order
  implementation and passes against this one.
- **The borrow checker, not a comment.** `OwnedGridConfig<'a>` borrows the corpus its `WordId`s
  index into, and `generate_*` takes `&'a mut WordList` and hands back that borrow. Rewinding,
  mutating, or substituting the corpus while a config is alive is a compile error, not a
  documented caller obligation. (It used to be the latter, and pairing a live config with a
  rewound list panicked with an out-of-bounds index inside `util.rs`.)

The solver's hot path is untouched: an overlay of template-local words would have put a branch in
`word_list.words[length][word_id]`, which is read in the innermost propagation loops.

Measured: 3,500 probes against a 160,469-entry list, each pinning several runs of four to seven
random non-word letters into a 15×15 švédská in slot order — 11,538 forced entries in all.
Resident memory reaches its high-water mark within the first 500 probes and then does not move
(175.6 MiB at 500 through 3,500), and the baseline template gets a byte-identical answer before and
after (`unknown slots=61 min_domain=1591`).
`oracle::tests::probes_leave_the_corpus_byte_for_byte_unchanged` and the
`word_list::tests::test_rewind_*` cases pin the same property down exactly rather than
statistically.

## One template parser

`grid_config::ParsedTemplate::parse` is the only thing that reads a template string. It returns
dimensions, row-major fixed letters, the slot topology, and the longest run of non-block cells; the
one-shot CLI and the oracle both go through it, so validation and slot detection cannot drift.

It rejects rather than reshapes. Surrounding blank lines and per-row surrounding whitespace are
cosmetic, but an *interior* blank row is an error: in a framed request (rows joined by `/`)
`.....//.....` is a genuinely malformed grid, and quietly dropping the empty row would return a
confident verdict about a different, smaller template.

`scripts/oracle.py` mirrors that rule rather than pre-empting it: `_as_rows` drops surrounding
blank rows and keeps interior ones, so a malformed grid reaches the engine and comes back as
`error row 1 is empty` instead of being quietly reshaped client-side. It also refuses a row
containing `/` or whitespace, which would reframe the request on the wire.

## One normalization policy, and it belongs to the corpus

A template's fixed letters have to be spelled the way the dictionary spelled its entries, or the
oracle looks up a glyph that cannot exist and answers `unfillable` — a verdict callers are told is
a proof — about a grid that fills. That makes the diacritic policy corpus state, not an oracle
setting, so `Oracle::new` reads it off the enabled sources with `WordList::converts_diacritics`
and fails outright when they disagree. There is no second copy to get wrong. `--serve` reports
the derived answer in its banner as `diacritics=0|1`.

The other half is *when* normalization runs. It runs after parsing, on letter cells only, via
`ParsedTemplate::fold_diacritics`. Running a dictionary normalizer over whole template rows means
pointing a word sanitiser at grid syntax: with `strip_punctuation` — a perfectly ordinary setting
for a source built from prose — every `#` and `.` is punctuation, and the entire template
dissolves into nothing. `word_list::normalize_grid_letter` is the narrow function that exists for
this: case and diacritic folding, no stripping, one character at a time. A cell whose letter folds
away to nothing is a `TemplateError::UnfoldableLetter`, not a silently empty square.

## Two surfaces

### `ingrid_core::oracle::Oracle` (library)

```rust
use ingrid_core::oracle::{Oracle, OracleOptions, ProbeOptions, Verdict};

let mut oracle = Oracle::new(word_list, OracleOptions {
    min_score: 33,
    default_probe_time: Duration::ZERO,   // arc consistency only
    seed: 0,
})?;                                      // fails if the sources disagree about diacritics

match oracle.probe(template)?.verdict {
    Verdict::Unfillable => { /* proof */ }
    Verdict::Fillable => {}
    Verdict::Unknown => {}
}
```

`Oracle::new` takes an already-configured `WordList`, because everything that varies per campaign —
sources, tiers, blocklist, `max_shared_substring`, `exempt_preferred_dupes`, diacritic folding — is
a property of the list and is therefore fixed by construction. Two policies means two oracles.

`Probe` carries the verdict plus what is free to collect: `slot_count`, `min_domain`, `setup_time`,
`arc_consistency_time`, `elapsed`, and the rendered `fill` when requested.

`min_domain` is the smallest number of candidates any slot still has *after* propagation. It is the
honest version of a "every slot must keep ≥ N candidates" screen — measured after the constraints
have been applied rather than guessed from the induced pattern — and it drops toward 1 as a template
saturates. It is a diagnostic, not a verdict: do not threshold it and call the result feasibility.

Bad input is a `ProbeError`, never a verdict: `ProbeError::Template` wraps the parser's
`TemplateError`, and `ProbeError::SlotTooLong` refuses a run longer than the corpus's longest word
bucket rather than reporting it unfillable, because that emptiness would be an artifact of the
load-time length filter rather than a proof.

### `ingrid_core --serve` (subprocess)

Portable to any language, captures essentially all of the value. Policy flags are the ordinary ones;
`--probe-time <MS>` sets the campaign's default budget and `--max-length` caps the loaded word
length (default 21).

**Handshake.** One line on stdout once loading is done. Read it before sending anything.

```
ready words=160469 max_length=21 min_score=33 probe_ms=0 blocked=0 diacritics=0 load_ms=460
```

**Request.** One line, whitespace-separated. First token is the template with rows joined by `/`;
remaining tokens are `key=value` overrides.

```
...#...#...#.../...#...#...#.../.......#.......
...#.../.......  ms=500 fill=1
quit
```

| key | meaning |
| --- | --- |
| `ms` | Search budget for this probe, in milliseconds, overriding `--probe-time`. `ms=0` forces arc consistency only even when the campaign default is larger. |
| `fill` | `1` to return the fill when one is found. |

Blank lines are ignored. `quit`, or closing stdin, ends the session.

**Response.** Exactly one line per request, flushed immediately, so a client can read lockstep.

```
unfillable slots=61 min_domain=0 setup_us=7712 ac_us=1932 us=9800
unknown    slots=61 min_domain=1591 setup_us=13998 ac_us=75712 us=102841
fillable   slots=88 min_domain=1189 setup_us=121840 ac_us=131762 us=354271 fill=cap#pod#han#pak/...
error row 1 is empty
error rows must all be the same length: row 1 has 2, expected 6
error unknown option 'depth'
```

`fill` rows are joined by `/` with blocks as `#`, so a returned fill is itself a legal template.
`error` never ends the session; the next request is answered normally.

### `scripts/oracle.py`

```python
from oracle import Oracle, OraclePool

with Oracle(wordlist="std33.dict", preferred="theme.dict", min_score=33,
            max_shared_substring=5, dupe_exempt_preferred=True, probe_ms=0) as oracle:
    v = oracle.probe(rows)                        # rows: list[str] or "\n"-joined str
    if v.unfillable:
        ...
    v = oracle.probe(rows, ms=500, want_fill=True)
```

Startup failures carry the child's own diagnosis: stderr is drained continuously from a daemon
thread, the banner read has a timeout, and a child that never becomes ready is killed and reaped.
`Oracle(max_length=2)` reports `stderr: Error: Word list is empty, exit status 1`, not "no output".

`OraclePool(jobs=N, ...)` starts N processes — N copies of the dictionary, N probes at once — and
`pool.probe_many(items, stop_on=...)` yields `(key, verdict)` as answers arrive. It is a rolling
window over a `ThreadPoolExecutor`: fill the window, wait, decide on everything that has
finished, deliver it, and submit one replacement per delivered non-match.

- At most `jobs` probes are in flight, and `items` is pulled one element at a time, so memory is
  `O(jobs)` and an endless generator of candidate placements is a legitimate argument. An earlier
  version drained the whole iterable before starting anything, which stalled the first probe
  behind a slow producer and would have hung forever on an infinite one.
- **No probe is started once a matching answer exists**, whether or not it has been delivered.
  Two things are needed for that, and each was wrong once. Probes finish in batches, so the turn
  decides on *every* completed probe rather than on one arbitrary member of the set `wait` hands
  back — deciding on one member lets a non-match beside the match start replacement work. And a
  probe can finish while the caller is away between answers, so the code peeks for a landed match
  before each submission rather than assuming nothing changed while it was suspended.
- A finished answer is never held back waiting on the producer: the pull happens after the yield,
  not before it.
- On a match, the probes still running and any that finished in the same batch are drained, their
  answers discarded, and the generator returns. Its runtime after a match is one probe, not the
  remaining work. Nothing here is described as cancellation, because none of it is.

A probe that starts while a match is merely *running* is not a violation and cannot be avoided
without giving up the window: nothing had been decided yet.

`scripts/test_oracle_client.py` holds this down against a fake pool, with no binary and no timing
assertions: each fake probe blocks on an event the test releases, and a `wait` shim manufactures
the simultaneous-completion batch, so every claim is decided by a handshake. Each guard was
checked by breaking it — deciding on one arbitrary batch member fails
`test_a_match_completing_alongside_a_non_match_stops_replacement_work`, dropping the pre-submit
peek fails `test_a_match_that_lands_between_answers_stops_replacement_work`, and consuming the
input eagerly hangs `test_a_non_matching_answer_is_delivered_before_more_work_is_pulled` outright.

Run the client directly to probe grid files: `python3 scripts/oracle.py grid.txt --wordlist
std.dict --probe-ms 500 --fill`. Exit status 1 means at least one grid was proven unfillable, 2
means at least one was refused.

## What a probe costs

Measured on a 15×15 švédská template (61 slots) with a 160,469-entry Czech list, `--min-score 33
--max-shared-substring 5 --dupe-exempt-preferred`, one core, medians of 15:

| probe | setup | arc consistency | total |
| --- | --- | --- | --- |
| bare template, AC only | 14 ms | 76 ms | **103 ms** |
| same template with 6 pinned theme entries, AC only | 8 ms | 25 ms | **36 ms** |
| bare template, fill probe | 122 ms | 132 ms | **~590 ms** |

Loading that list takes 460–700 ms, once. On a denser 15×15 American grid (88 slots) against a
559,681-entry list, an AC-only probe is 63 ms and loading is ~2.1 s.

Two things follow. First, the more constrained the grid — which is the case worth probing — the
cheaper the probe, because propagation has less to chew on. Second, this is not sub-millisecond and
will not become so while the template is rebuilt per probe, which is the deliberate design: the
dictionary is the only thing held across probes, and 225 cells are rebuilt every time.

An arc-consistency-only probe skips the candidate *ranking* pass (`CandidateOrder::Unranked`), which
is roughly 4× cheaper setup. Ranking is the solver's value heuristic; propagation computes the same
closure whatever order the options are in, so the verdict is unaffected.
`oracle::tests::ranking_candidates_does_not_change_what_arc_consistency_proves` holds that claim
down.

### Against what it replaces

`pin_long.py`'s old accept step ran a full one-shot `ingrid_core --timeout 90 --cores 3` per
candidate placement. On this template the preferred-maximisation search never proves optimality, so
each probe cost the **entire 90-second timeout** (measured: 1.2 s loading, 90.2 s searching) — and
returned "reject" for exactly the same reason whether the grid was unfillable or merely slow. A
greedy pass over 163 candidate placements was hours. The whole six-round run now takes 16 seconds
on six oracle processes and ends with a proof.

## Open questions, answered

### Is initial arc consistency alone a good enough oracle?

Measured, not guessed. `scripts/screen_audit.py` walks the same greedy pinning loop as
`pin_long.py` but probes *every* candidate placement twice — once with arc consistency alone, once
with a 3-second fill budget — and asserts that arc consistency never refutes a grid that then
fills. On `seeded_s00.txt` with the Karolína lists:

| pins already placed | candidates | refuted by AC | passed AC | of the passers: fillable | actually unfillable | still unknown |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 163 | 5 | 158 | 158 | 0 | 0 |
| 1 | 127 | 12 | 115 | 115 | 0 | 0 |
| 2 | 93 | 23 | 70 | 69 | 1 | 0 |
| 3 | 70 | 27 | 43 | 42 | 1 | 0 |
| 4 | 41 | 36 | 5 | 3 | 2 | 0 |
| 5 | 27 | **27** | 0 | — | — | — |

- **False rejects: zero, by construction.** Arc consistency never refuted a grid that a fill probe
  then filled. It is a proof; the audit asserts this rather than hoping.
- **False accepts: 4 of 391, about 1%** overall — but concentrated where the grid is nearly
  saturated: 0% at depth 0–1, 1.4% at depth 2, 2.3% at depth 3, 40% at depth 4.
- **Decisive exactly where it matters.** On the saturated grid every one of 27 remaining placements
  was refuted by arc consistency alone. "No more theme entries fit" is now a claim about proofs.

So the instinct was right — AC is decisive for over-constrained grids — with the caveat that in the
last round or two before saturation it accepts a handful of grids that cannot fill. The recipe that
follows is the two-stage accept `pin_long.py` now implements: screen everything with arc
consistency, then spend a real fill budget only on the survivors. Note also that no AC-passer
remained `unknown` at 3 seconds on this template, so the second stage is cheap and conclusive here.

### Should there be a `--probe-time`?

Yes, and it defaults to arc consistency only, as suspected. It is a campaign default
(`--probe-time`) with a per-request override (`ms=`), because the two-stage accept needs both in one
session.

### Does the score column influence candidate *selection*, or only `--min-score` filtering?

Both, but less than the tier does. `grid_config::sort_slot_options` sorts each slot's candidates by

```
(tier != Preferred,  -(900 * fill_score + 5 * letter_score + 5 * word_score))
```

where `fill_score` is the mean over cells of `log10(number of crossing candidates carrying that
letter)`.

Read that carefully, because it determines what the tier build actually controls:

- **Tier is the primary key.** Every preferred-tier candidate is tried before any standard-tier one,
  unconditionally. This is the strong lever.
- **Score is a real but secondary term inside a tier.** A 100-point score advantage is worth
  `500 / 900 ≈ 0.55` of one `fill_score` unit, i.e. about a factor of 3.5 in crossing-domain size.
  Fillability dominates ordering, as intended.
- **This is ordering, not objective.** The parallel search maximises `count_preferred_words`; word
  scores never enter the objective. They bias which candidate the solver reaches for first, and the
  solver backtracks away from that preference whenever it must.
- **`letter_score` is a Scrabble-like rarity sum** (`aeilnorstu`=1 … `qz`=10) that sorts *earlier*,
  so rarer letters are mildly preferred. Any character not in that table scores 3 — which includes
  every Czech accented letter, so this heuristic currently gives diacritic-heavy Czech words a small
  ordering advantage as a side effect rather than a decision.

Practical consequence for the wish list: tiering is the control surface for filler quality, and
scoring within a tier is a nudge. A score-weighted objective (`Σ score` instead of
`count_preferred_words`) remains a genuinely separate feature, because nothing in the current
objective can express "prefer attested filler, penalise crosswordese" — the sort key expresses it
only as a first-guess preference the search is free to abandon.

## Deliberately absent

- No incremental grid diffing, no cached partial propagation state, no warm template handles. The
  template is rebuilt every probe.
- No per-probe policy. Word lists, `--min-score`, `--max-shared-substring` and
  `--dupe-exempt-preferred` are set at startup. Two policies, two oracles.
- No bundled template search. The oracle answers questions; the search stays in the caller's
  language.

## Now affordable

Reconsidering an earlier pin used to cost a 90-second probe, so `pin_long.py` was greedy first-fit
and stopped when the first-fit chain ran out. At 36–103 ms for a proof it is just a search:
backtracking over pinned entries, best-of-k placement instead of first-fit, and restart portfolios
all become ordinary things to write.
