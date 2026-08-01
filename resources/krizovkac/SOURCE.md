# Křížovkáč 0.0.1 lexicon

Czech crossword-setting program, released 2015-12-23 as
`Křížovkáč 0.0.1.tar` (gzip member, GNU tar). The lexicon lives in the archive's `data`
member; an earlier session extracted it to `local/legacy-krizovkac/` and recorded the
archaeology in that directory's `summary.json`.

Regenerate the two files here with:

```sh
python3 scripts/krizovkac_to_dict.py \
    --lexicon local/legacy-krizovkac/krizovkac.dict \
    --clues   local/legacy-krizovkac/clues.tsv \
    --out-dict  resources/krizovkac/lexicon.dict \
    --out-clues resources/krizovkac/clues.tsv
```

## Contents

- `lexicon.dict` — 68 174 entries, `word;score`, all of them clued, so all score 70.
  Lengths: 3:1833 · 4:4659 · 5:10344 · 6:16278 · 7:17938 · 8:14250 · 9:2668 · 10:190 · 11:14.
- `clues.tsv` — `answer<TAB>clue`, the shortest clue per answer (the švédská legend box
  is 34 characters, `CLUES.md` §2).

## Why this is committed rather than left in `local/`

`local/` is gitignored. This is an **input**, not a generated artifact, and it is the
scarcest thing in the whole pipeline:

- it is the only source with real coverage of the **3- and 4-letter band**, which is what
  a dense 15×15 švédská runs out of first;
- every entry carries a clue a human setter wrote, which is the difference between
  writing six clues for a puzzle and writing fifty-three.

It was nearly lost: nothing referenced it, and it surfaced only because the repo owner
remembered it existed.

## The catch

It is a *crossword* lexicon, so crosswordese is its purpose, not a defect. **22 802 of
the 68 174 entries (33 %) never occur in csTenTen17** — `aab`, `aabbcc`, `abakun`,
`obosm`, `lejzr`, `nelsn`. Gate it when you want filler that reads like language:

```sh
python3 scripts/build_tier.py \
    --gated resources/krizovkac/lexicon.dict \
    --attest local/cstenten.dict --attest-floor 25 \
    ... --out local/tier.dict
```

Measured on a 15×15 švédská: the gate cut unattested filler from 18/52 to 5/53 and
raised the median corpus score of filler from 33.5 to 38, at a cost of one theme entry.

## Licence

Unknown. The archive carries no licence file. Treat as third-party data of unclear
provenance: fine for private puzzles, check before publishing anything derived from it.
