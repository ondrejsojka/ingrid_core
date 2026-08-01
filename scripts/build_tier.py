#!/usr/bin/env python3
"""Assemble a standard tier by provenance + junk filters, not by a score bar.

Raising `--min-score` is a bad junk filter: it is a frequency threshold wearing a quality
costume. It throws away perfectly clueable low-frequency words (a flat-scored supplement
like `capek_to_dict.py`'s `word;30` output disappears wholesale) while keeping
high-frequency garbage, and on a dense grid the words it discards are exactly the short
ones the fill cannot do without.

So gate on where a word came from and on what is demonstrably wrong with it:

  * **Křížovkáč** — a published Czech crossword lexicon. Every entry is by construction
    something a setter was willing to clue, and each one ships with its clue. Admitted at
    every length, no questions.
  * **Czech canonical base** — already POS-filtered and lemma-checked upstream. Admitted.
  * **Long tail / Slovak** — admitted, but junk-filtered:
      - de-accented doublets: a form with no diacritics whose accented sibling also
        exists (`udeli` beside `udělí`, `vedome` beside `vědomé`, `nedele` beside
        `neděle`). These are OCR/encoding debris and they are unclueable, because the
        clue would have to be the clue for the real word;
      - short words (<= `--short-len`) that no curated source vouches for. This is where
        the damage was: `srz`, `spu`, `htm`, `evo`, `ase`, `ide`, `alo`. A dense 15x15
        has ~22 three-letter slots, so the short band decides the whole fill's quality;
      - letters-only, no triple repeats, sane length.

The output keeps each source's own score, so `--min-score` can go back to being what it
says it is.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter

ALPHA = re.compile(r"^[a-záäčďéěíĺľňóôŕřšťúůýž]+$")
TRIPLE = re.compile(r"(.)\1\1")


def strip_acc(word):
    w = word.replace("ě", "e").replace("ů", "u").replace("ľ", "l").replace("ĺ", "l")
    w = w.replace("ô", "o").replace("ä", "a").replace("ŕ", "r")
    return "".join(c for c in unicodedata.normalize("NFD", w)
                   if unicodedata.category(c) != "Mn")


def read(path):
    out = {}
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        w, _, s = ln.partition(";")
        try:
            score = int(float(s))
        except ValueError:
            continue
        if w and (w not in out or score > out[w]):
            out[w] = score
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--curated", action="append", default=[],
                    help="vouched source (Křížovkáč, canonical base): admitted at any length")
    ap.add_argument("--gated", action="append", default=[],
                    help="curated, but every entry must be corpus-attested (see --attest). "
                         "Křížovkáč needs this: it is a crossword lexicon, so a third of it "
                         "is deep crosswordese (aabbcc, obosm, lejzr) that occurs nowhere.")
    ap.add_argument("--attest", help="frequency dict used as the attestation corpus")
    ap.add_argument("--attest-floor", type=int, default=25)
    ap.add_argument("--tail", action="append", default=[],
                    help="long tail / other language: admitted only through the junk filters")
    ap.add_argument("--short-len", type=int, default=4,
                    help="words this long or shorter need a curated source to vouch for them")
    ap.add_argument("--max-len", type=int, default=14)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    curated = {}
    for p in args.curated:
        for w, s in read(p).items():
            curated[w] = max(curated.get(w, 0), s)

    attest = read(args.attest) if args.attest else {}
    gated_in = gated_out = 0
    for p in args.gated:
        for w, s in read(p).items():
            if attest.get(w, -1) < args.attest_floor:
                gated_out += 1
                continue
            gated_in += 1
            curated[w] = max(curated.get(w, 0), s)
    tail = {}
    for p in args.tail:
        for w, s in read(p).items():
            tail[w] = max(tail.get(w, 0), s)

    accented = {strip_acc(w) for w in set(curated) | set(tail) if strip_acc(w) != w}

    keep, drop = {}, Counter()
    for w, s in curated.items():
        if not ALPHA.match(w) or TRIPLE.search(w) or not (3 <= len(w) <= args.max_len):
            drop["curated: malformed"] += 1
            continue
        keep[w] = s
    for w, s in tail.items():
        if w in keep:
            keep[w] = max(keep[w], s)
            continue
        if not ALPHA.match(w) or TRIPLE.search(w) or not (3 <= len(w) <= args.max_len):
            drop["tail: malformed"] += 1
            continue
        if w in accented and strip_acc(w) == w:
            drop["tail: de-accented doublet"] += 1
            continue
        if len(w) <= args.short_len:
            drop["tail: short and unvouched"] += 1
            continue
        keep[w] = s

    with open(args.out, "w", encoding="utf-8") as fh:
        for w in sorted(keep):
            fh.write(f"{w};{keep[w]}\n")
    print(f"{args.out}: {len(keep)} entries "
          f"(curated {len(curated)}, tail {len(tail)})")
    if args.gated:
        print(f"  attestation gate (floor {args.attest_floor}): kept {gated_in}, "
              f"dropped {gated_out} unattested")
    for reason, n in drop.most_common():
        print(f"  dropped {n:>7}  {reason}")
    hist = Counter(len(w) for w in keep)
    print("  lengths:", " ".join(f"{k}:{hist[k]}" for k in sorted(hist) if k <= 12))


if __name__ == "__main__":
    main()
