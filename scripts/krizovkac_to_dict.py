#!/usr/bin/env python3
"""Turn the Křížovkáč 0.0.1 lexicon export into a scored dict plus a clue bank.

Křížovkáč is a Czech crossword-setting program from 2015 whose lexicon is 68k
answer/clue pairs. Two properties make it worth more than its size suggests:

* **Every entry ships with a clue a setter actually wrote.** That is the difference
  between "write 53 clues" and "write 6", and no frequency list can supply it.
* **It is dense where corpora are thin** — 1 833 three-letter and 4 659 four-letter
  answers. A dense 15x15 švédská has ~24 three-letter slots, so the short band decides
  the whole fill, and this is the only source that covers it.

And one property that will bite you: it is a *crossword* lexicon, so roughly a third of
it is deep crosswordese — `aabbcc`, `abakun`, `obosm`, `lejzr`, `nelsn` — words that
occur nowhere in a 5.6-billion-token corpus. Gate it with
`build_tier.py --gated ... --attest <corpus> --attest-floor 25` unless you want them.

Input is `local/legacy-krizovkac/`, extracted from the program's tar archive by an
earlier session (see its `summary.json` for the archaeology).
"""

from __future__ import annotations

import argparse
import collections
import csv
import re

ALPHA = re.compile(r"^[a-záäčďéěíĺľňóôŕřšťúůýž]+$")
TRIPLE = re.compile(r"(.)\1\1")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lexicon", required=True, help="krizovkac.dict (word;score)")
    ap.add_argument("--clues", required=True, help="clues.tsv with answer/clue columns")
    ap.add_argument("--out-dict", required=True)
    ap.add_argument("--out-clues", required=True)
    ap.add_argument("--min-len", type=int, default=3)
    ap.add_argument("--max-len", type=int, default=12)
    ap.add_argument("--score-clued", type=int, default=70)
    ap.add_argument("--score-unclued", type=int, default=55)
    args = ap.parse_args()

    clues = collections.defaultdict(list)
    with open(args.clues, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            clue = (row.get("clue") or "").strip()
            if clue:
                clues[row["answer"]].append(clue)

    words, skipped = [], collections.Counter()
    for ln in open(args.lexicon, encoding="utf-8"):
        w = ln.strip().partition(";")[0]
        if not w:
            continue
        if not ALPHA.match(w):
            skipped["non-alpha"] += 1
        elif TRIPLE.search(w):
            skipped["triple repeat"] += 1
        elif not (args.min_len <= len(w) <= args.max_len):
            skipped["length"] += 1
        else:
            words.append(w)

    with open(args.out_dict, "w", encoding="utf-8") as fh:
        for w in sorted(set(words)):
            fh.write(f"{w};{args.score_clued if clues.get(w) else args.score_unclued}\n")
    with open(args.out_clues, "w", encoding="utf-8") as fh:
        for w in sorted(clues):
            if ALPHA.match(w) and args.min_len <= len(w) <= args.max_len:
                # shortest clue: the box budget is 34 characters (CLUES.md §2)
                fh.write(f"{w}\t{min(clues[w], key=len)}\n")

    hist = collections.Counter(len(w) for w in set(words))
    print(f"{args.out_dict}: {len(set(words))} entries; "
          f"{sum(1 for w in set(words) if clues.get(w))} clued")
    print("  lengths:", " ".join(f"{k}:{hist[k]}" for k in sorted(hist)))
    for reason, n in skipped.most_common():
        print(f"  skipped {n:>5}  {reason}")


if __name__ == "__main__":
    main()
