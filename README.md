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

### Acknowledgments

* The backtracking search implementation in this library owes a lot to
  "Adaptive Strategies for Solving Constraint Satisfaction Problems" by
  Thanasis Balafoutis, which was helpful both as an overview of the CSP space
  and a source of specific implementation ideas.

* The CLI tool includes a copy of the free [Spread the
  Wordlist](https://www.spreadthewordlist.com) dictionary published by Brooke
  Husic and Enrique Henestroza Anguiano.
