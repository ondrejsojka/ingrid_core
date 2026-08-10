# Brno 2026 Crossword Generation Recipe

This directory contains tier-building inputs, allowlists, and recipes for generating Brno-themed crosswords.

## Standard Tier Assembly (Junk-Filtering Recipe)

Instead of using a raw `--min-score` cut (which acts as a frequency threshold rather than a quality filter and discards valid low-frequency words while retaining high-frequency junk), standard tiers are built by **provenance + attestation**:

```sh
python3 scripts/build_tier.py \
  --curated local/trials/standard_no_marked_n33.dict \
  --gated resources/krizovkac/lexicon.dict \
  --attest local/cstenten.dict --attest-floor 25 \
  --out local/brno2026/standard.dict
```

1. **Curated base**: `standard_no_marked_n33.dict` (canonical POS-filtered words without nonstandard marked forms).
2. **Křížovkáč crossword lexicon**: `resources/krizovkac/lexicon.dict` gated with `--attest local/cstenten.dict --attest-floor 25` to filter out deep crosswordese while retaining short 3- and 4-letter words.
3. **Blocklist**: `resources/blocklist_cs.txt` updated with defects caught by `fill_critic.py` (e.g. `areny`, `krak`).

## Theme Tier Assembly (Brno Vocab)

```sh
# 1. Mine acronyms & candidates from magazine corpus
cat local/metropolitan/txt/Metropolitan_2026-*.txt > local/brno2026/corpus_2026.txt
cat resources/blocklist_cs.txt resources/metropolitan/denylist.txt > local/brno2026/denylist_combined.txt

python3 scripts/theme_tier.py \
  --model /tmp/czech-morphodita/czech-morfflex2.1-pdtc2.0-250909/czech-morfflex2.1-250909.dict \
  --input local/trials/metro_brno_preferred.dict \
  --reference local/cstenten.dict --max-reference-score 41 --trust-input-score 200 --keep-common \
  --corpus local/brno2026/corpus_2026.txt --mine-acronyms \
  --allowlist resources/brno2026/allowlist.txt \
  --denylist local/brno2026/denylist_combined.txt \
  --output local/brno2026/tier_a.dict --output-literal local/brno2026/tier_a_literal.dict

# 2. Merge mined + hand-curated channels and expand forms
python3 scripts/theme_expand.py \
  --model /tmp/czech-morphodita/czech-morfflex2.1-pdtc2.0-250909/czech-morfflex2.1-250909.dict \
  --lemmas local/brno2026/tier_merged.dict \
  --literal local/brno2026/literal_merged.dict \
  --allowed-variants '-' \
  --standard local/brno2026/standard.dict \
  --denylist local/brno2026/denylist_combined.txt \
  --output local/brno2026/preferred.dict \
  --report local/brno2026/preferred_report.csv
```

## Search & Quality Validation

```sh
# Search (using max 9 cores)
./target/release/ingrid_core \
  --preferred-wordlist local/brno2026/preferred.dict \
  --wordlist local/brno2026/standard.dict \
  --blocklist resources/blocklist_cs.txt \
  --min-score 33 --max-shared-substring 4 --dupe-exempt-preferred \
  --cores 9 --timeout 90 --grids 5 --grids-dir local/brno2026/grids/ \
  local/rich/grids/g09_headroom48c.txt

# Audit fill quality
python3 scripts/fill_critic.py \
  --fill local/brno2026/grids/grid-1.txt \
  --template local/rich/grids/g09_headroom48c.txt \
  --preferred local/brno2026/preferred.dict \
  --wordlist local/brno2026/standard.dict \
  --expand-report local/brno2026/preferred_report.csv \
  --corpus local/brno2026/corpus_2026.txt \
  --reference local/cstenten.dict

# Validate clues
python3 scripts/clue_check.py \
  --fill local/brno2026/grids/grid-1.txt \
  --clues local/brno2026/clues.tsv
```
