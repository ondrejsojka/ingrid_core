# Ingrid Core

This crate contains the core crossword-solving code used in the Ingrid
construction app, as well as a standalone binary that can be used to solve
grids from the command line.

### Usage

After [setting up Rust](https://rustup.rs), you can install the Ingrid Core CLI
tool with `cargo`:
```
$ cargo install ingrid_core
```

Then you just need to provide a grid as an input file:

```
$ cat example_grid.txt
....#.....#....
....#.....#....
...............
......##.......
###.....#......
............###
.....#.....#...
....#.....#....
...#.....#.....
###cremebrulees
......#.....###
.......##......
...............
....#.....#....
....#.....#....
$ ingrid_core example_grid.txt
bile#seeit#slaw
room#lasso#pone
intimateapparel
garret##whirred
###amens#easels
wisterialane###
aloes#nuevo#tnt
ssns#betty#ciao
pas#wipes#pelts
###cremebrulees
dealin#deere###
imgonna##aesops
goingintodetail
utne#anise#atta
pegs#lemur#shay
```

You can provide separate preferred and standard scored word lists. The standard list defaults to
[Spread the Wordlist](https://www.spreadthewordlist.com). Ingrid uses all available CPU cores by
default; `--cores` sets an explicit limit.

With a preferred list, workers search at different minimum preferred-word counts. A completed fill
cancels workers at easier minima while harder workers continue, and the freed cores are reassigned
across the remaining viable counts. The CLI returns the best fill found after 60 seconds by default;
`--timeout 0` instead waits until the largest attainable preferred-word count is proven.

`--estimate-variants` runs a bounded post-search estimator for the number of distinct valid fills
containing at least as many Preferred entries as the returned fill. Each randomized root-to-leaf
walk reuses the solver's prepared post-AC state and applies Ingrid's actual incremental arc
consistency, duplicate/shared-substring rules, fixed entries, and Preferred threshold. Accepted
leaves are inverse-probability weighted, so the incumbent guide improves acceptance without
excluding other live values.

The estimator budget defaults to 45% of the completed search time.
`--estimate-runtime-ratio` changes that fraction but is capped at 50%, and
`--estimate-max-time` adds an absolute seconds cap. `--estimate-walks` selects the fixed cohort size
(16 by default), `--estimate-guide-probability` controls the incumbent proposal mass, and
`--estimate-seed` makes every numbered walk reproducible independently of worker count. A cohort
that cannot finish before the deadline is reported as interrupted rather than averaged
selectively. The report includes certified distinct fills found, the arithmetic estimate expressed
as both a count and slack bits, a confidence interval, accepted walks, effective sample size, and
measured overhead. Zero accepted samples are reported as insufficient evidence, never as zero
variants.

`--blocklist` takes a file of words that may never appear in a fill, one per line, with `#`
starting a comment. Matching is exact after the same normalization applied to the word lists, so
`--ignore-diacritics` folds the blocklist too, and the words are hidden from the preferred and
standard tiers alike — a blocked word cannot sneak back in through the preferred list. With
`--time`, the number of hidden words is reported alongside the fill timings.

```
$ ingrid_core --help
Crossword-generating library and CLI tool

Usage: ingrid_core [OPTIONS] <GRID_PATH>

Arguments:
  <GRID_PATH>  Path to the grid file, as ASCII with # representing blocks and . representing empty squares

Options:
      --wordlist <WORDLIST>
          Path to the standard-tier scored wordlist [default: embedded Spread the Wordlist]
      --preferred-wordlist <PREFERRED_WORDLIST>
          Path to a preferred-tier scored wordlist
      --blocklist <BLOCKLIST>
          Path to a blocklist of words to exclude from every tier, one per line; `#` starts a comment
      --min-score <MIN_SCORE>
          Minimum allowable word score [default: 50]
      --max-shared-substring <MAX_SHARED_SUBSTRING>
          Maximum shared substring length between entries [default: none]
      --ignore-diacritics
          Convert accented letters to their unaccented forms in the grid and word lists
      --cores <CORES>
          Number of CPU cores to use [default: all available cores]
      --timeout <TIMEOUT>
          Maximum search time in seconds; 0 waits for a proven optimum [default: 60]
      --search-log <PATH>
          Append scheduler convergence telemetry to this CSV path
      --estimate-variants
          Estimate how many distinct fills are at least as Preferred-heavy as the returned fill
      --estimate-runtime-ratio <ESTIMATE_RUNTIME_RATIO>
          Maximum estimator/search runtime ratio; values above 0.5 are capped [default: 0.45]
      --estimate-max-time <ESTIMATE_MAX_TIME>
          Absolute estimator time cap in seconds
      --estimate-seed <ESTIMATE_SEED>
          Random seed for variant-estimation walks [default: 0]
      --estimate-walks <ESTIMATE_WALKS>
          Fixed number of variant-estimation walks [default: 16]
      --estimate-guide-probability <ESTIMATE_GUIDE_PROBABILITY>
          Probability of following the incumbent value at each sampled decision [default: 0.98]
  -t, --time
          Print timing information along with the grid
  -h, --help
          Print help
  -V, --version
          Print version
```

`--ignore-diacritics` can substantially enlarge compatible crossing domains for languages that use
accented letters. Output is unaccented in that mode.

For example:

```
$ ingrid_core --preferred-wordlist theme.dict --wordlist standard.dict --cores 8 example_grid.txt
```

### Czech Metropolitan recipe

The scripts under `scripts/` build a reproducible Czech Standard tier and a
Metropolitan-specific Preferred tier. They require Python 3.10 or newer,
`curl`, `pdftotext`, [`ufal.morphodita`](https://pypi.org/project/ufal.morphodita/),
and a Czech MorphoDiTa `.tagger` from the
[Czech MorphoDiTa model](https://hdl.handle.net/11234/1-5985).

Fetch Metropolitan editions and retain their extracted text:

```
$ python3 scripts/metropolitan_pdf_to_dict.py --fetch-years 2020-2026 \
    --outdir local/metropolitan --keep-txt
```

Build the Standard list from `cs-all-cstenten.wls` and `cstenten17.frqwl` in
[`cshyphen`](https://github.com/ondrejsojka/cshyphen), then bias its scores
toward canonical dictionary forms:

```
$ python3 scripts/cstenten_wls_to_dict.py \
    --wls /path/to/cshyphen/src/cs-all-cstenten.wls \
    --frqwl /path/to/cshyphen/src/cstenten17.frqwl \
    --output local/cstenten.dict --min-freq 50
$ python3 scripts/czech_standard_dict.py \
    --model /path/to/czech-morfflex.tagger \
    --input local/cstenten.dict \
    --output local/cstenten-canonical-bias.dict \
    --min-score 30 --canonical-bonus 20 \
    --denylist resources/blocklist_cs.txt
```

By default, the Standard filter keeps analyzed noun, adjective, verb, and
adverb forms, excludes entries whose only analyses are foreign/unknown, and
retains noncanonical inflections. Use `--exclude-vocatives`,
`--exclude-imperatives`, and `--exclude-transgressives` to reject entries whose
only eligible analyses belong to those marked classes.
`--min-noncanonical-score` adds a frequency floor without removing low-frequency
canonical lemmas. Use `--allowlist` and `--denylist` for reviewed exceptions;
add `--drop-noncanonical` only for intentionally strict experiments.

`resources/blocklist_cs.txt` is this project's curated blocklist. Pass it to the
search with `--blocklist`, which is the enforcing path since it applies to
whatever dictionaries are handed in. The builders accept the same file with
`--denylist` so blocked entries never enter a generated dictionary, and
`apply_blocklist.py` filters a dictionary that already exists:

```
$ python3 scripts/apply_blocklist.py \
    --input local/metropolitan-preferred.dict \
    --output local/metropolitan-preferred-filtered.dict \
    --blocklist resources/blocklist_cs.txt \
    --report local/metropolitan-preferred-blocklist.csv
```

Build a high-precision Preferred list for readers of the full publication
archive. The JSON analysis and lemma-frequency dictionary are reusable caches;
the CSV records every accepted and rejected lemma.

```
$ python3 scripts/metropolitan_theme_dict.py local/metropolitan \
    --preset broad-reader \
    --model /path/to/czech-morfflex.tagger \
    --standard local/cstenten.dict \
    --output local/metropolitan-preferred.dict \
    --report local/metropolitan-preferred.csv \
    --analysis-cache local/metropolitan-analysis.json \
    --denylist resources/blocklist_cs.txt
```

For a crossword tied to one edition, use the edition preset and the archive
analysis to reject recurring publication boilerplate:

```
$ python3 scripts/metropolitan_theme_dict.py path/to/issue.pdf \
    --preset edition-reader \
    --model /path/to/czech-morfflex.tagger \
    --standard local/cstenten.dict \
    --output local/issue-preferred.dict \
    --report local/issue-preferred.csv \
    --analysis-cache local/issue-analysis.json \
    --background-analysis local/metropolitan-analysis.json \
    --denylist resources/blocklist_cs.txt
```

To locate the publication source and surrounding text for a candidate entry
without generating a clue:

```
$ python3 scripts/metropolitan_word_sources.py local/metropolitan \
    --model /path/to/czech-morfflex.tagger -q Jinacovice
```

Existing analysis and standard-lemma caches are reused when their source
fingerprints match. Pass `--refresh-analysis` or `--refresh-standard-lemmas`
only to request an intentional rebuild.

Then run the adaptive multicore search. Omitting `--cores` uses every available
core. Preserve diacritics by default; use `--ignore-diacritics` only for an
explicit accent-folded experiment. `--search-log` appends scheduler targets,
incumbent improvements, failures, and cancellations to CSV.

```
$ cargo run --release -- \
    --preferred-wordlist local/metropolitan-preferred.dict \
    --wordlist local/cstenten-canonical-bias.dict \
    --blocklist resources/blocklist_cs.txt \
    --min-score 30 --max-shared-substring 5 \
    --timeout 900 --search-log local/search.csv grid.txt
```

`--blocklist` here is the same mechanism described above; it covers the Preferred
list as well, so a blocked word cannot return through the theme dictionary.

For the edition-specific workflow, run the same search with
`--preferred-wordlist local/issue-preferred.dict` instead.

### Acknowledgments

* The backtracking search implementation in this library owes a lot to
  "Adaptive Strategies for Solving Constraint Satisfaction Problems" by
  Thanasis Balafoutis, which was helpful both as an overview of the CSP space
  and a source of specific implementation ideas.

* The CLI tool includes a copy of the free [Spread the
  Wordlist](https://www.spreadthewordlist.com) dictionary published by Brooke
  Husic and Enrique Henestroza Anguiano.
