#!/usr/bin/env python3
"""Bias a scored Czech standard dictionary toward canonical dictionary forms.

The output remains one Ingrid Standard tier. Canonical forms receive a score bonus;
with ``--drop-noncanonical``, inflected forms are removed instead. MorphoDiTa's
guesser is disabled so invented analyses do not affect the list.
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

from ufal.morphodita import Morpho, TaggedLemmas, Tagger


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.casefold()).strip()


def load_scored_dict(path: Path) -> dict[str, int]:
    words: dict[str, int] = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            parts = line.rstrip("\n").split(";")
            if len(parts) < 2:
                continue
            word = normalize_word(parts[0])
            try:
                score = int(parts[1])
            except ValueError:
                continue
            if word and word.isalpha():
                words[word] = max(score, words.get(word, 0))
    return words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--canonical-bonus", type=int, default=20)
    parser.add_argument("--drop-noncanonical", action="store_true")
    args = parser.parse_args()

    tagger = Tagger.load(str(args.model))
    if tagger is None:
        raise SystemExit(f"Could not load MorphoDiTa tagger: {args.model}")
    morphology = tagger.getMorpho()

    analyses = TaggedLemmas()
    output: dict[str, int] = {}
    canonical_count = 0
    noncanonical_count = 0
    for word, score in load_scored_dict(args.input).items():
        if score < args.min_score:
            continue
        analyses.clear()
        morphology.analyze(word, Morpho.NO_GUESSER, analyses)
        canonical = any(
            normalize_word(morphology.rawLemma(analysis.lemma)) == word
            for analysis in analyses
            if analysis.tag
        )
        if canonical:
            output[word] = score + args.canonical_bonus
            canonical_count += 1
        elif not args.drop_noncanonical:
            output[word] = score
            noncanonical_count += 1

    if not output:
        raise SystemExit("No standard words matched the requested filters")
    ordered = sorted(output.items(), key=lambda item: (-item[1], item[0]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{word};{score}\n" for word, score in ordered),
        encoding="utf-8",
    )
    print(
        f"wrote {args.output}: {canonical_count} canonical and "
        f"{noncanonical_count} retained noncanonical forms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
