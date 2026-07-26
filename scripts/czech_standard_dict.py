#!/usr/bin/env python3
"""Filter and score a Czech Standard dictionary with MorphoDiTa.

Words are eligible when MorphoDiTa reports an allowed part-of-speech initial.
Canonical forms receive a score bonus; with ``--drop-noncanonical``, inflected
forms are removed instead. MorphoDiTa's guesser is disabled so invented
analyses do not affect the list. Explicit allowlist entries bypass morphology,
while denylist entries always win.
"""

from __future__ import annotations

import argparse
import csv
import unicodedata
from pathlib import Path

from ufal.morphodita import Morpho, TaggedLemmas, Tagger

FOREIGN_UNKNOWN_POS = frozenset("XFB")
REPORT_FIELDS = [
    "word",
    "input_score",
    "output_score",
    "pos",
    "canonical",
    "decision",
]


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


def load_wordlist(path: Path | None) -> set[str]:
    if path is None:
        return set()

    words: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            word = normalize_word(raw)
            if word and word.isalpha():
                words.add(word)
    return words


def parse_allowed_pos(value: str) -> frozenset[str]:
    initials = [
        character
        for character in value.upper()
        if character != "," and not character.isspace()
    ]
    if not initials or any(not ("A" <= character <= "Z") for character in initials):
        raise argparse.ArgumentTypeError(
            "POS initials must be ASCII letters, optionally separated by commas or spaces"
        )
    return frozenset(initials)


def write_report(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: str(row["word"])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--canonical-bonus", type=int, default=20)
    parser.add_argument("--drop-noncanonical", action="store_true")
    parser.add_argument(
        "--allowed-pos",
        type=parse_allowed_pos,
        default=parse_allowed_pos("NAVD"),
        metavar="INITIALS",
        help="MorphoDiTa tag initials eligible for Standard (default: NAVD)",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        help="UTF-8 words allowed regardless of morphology",
    )
    parser.add_argument(
        "--denylist",
        type=Path,
        help="UTF-8 words excluded even when allowlisted",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional CSV audit containing a decision for every input word",
    )
    args = parser.parse_args()

    tagger = Tagger.load(str(args.model))
    if tagger is None:
        raise SystemExit(f"Could not load MorphoDiTa tagger: {args.model}")
    morphology = tagger.getMorpho()
    allowlist = load_wordlist(args.allowlist)
    denylist = load_wordlist(args.denylist)

    analyses = TaggedLemmas()
    output: dict[str, int] = {}
    report_rows: list[dict[str, object]] = []
    canonical_count = 0
    noncanonical_count = 0
    for word, score in load_scored_dict(args.input).items():
        denied = word in denylist
        below_min_score = score < args.min_score
        pos_initials: set[str] = set()
        canonical = False

        # Rejected words still need complete morphology columns in an audit.
        if args.report is not None or (not denied and not below_min_score):
            analyses.clear()
            morphology.analyze(word, Morpho.NO_GUESSER, analyses)
            for analysis in analyses:
                if not analysis.tag:
                    continue
                pos_initials.add(analysis.tag[0].upper())
                if normalize_word(morphology.rawLemma(analysis.lemma)) == word:
                    canonical = True

        output_score: int | str = ""
        if denied:
            decision = "denylist"
        elif below_min_score:
            decision = "below_min_score"
        else:
            if not pos_initials:
                morphology_rejection = "no_analysis"
            elif pos_initials <= FOREIGN_UNKNOWN_POS:
                morphology_rejection = "foreign_unknown"
            elif pos_initials.isdisjoint(args.allowed_pos):
                morphology_rejection = "disallowed_pos"
            else:
                morphology_rejection = None

            explicitly_allowed = word in allowlist
            if morphology_rejection is not None and not explicitly_allowed:
                decision = morphology_rejection
            elif explicitly_allowed:
                output_score = score + args.canonical_bonus if canonical else score
                output[word] = output_score
                canonical_count += int(canonical)
                noncanonical_count += int(not canonical)
                decision = "allowlist"
            elif canonical:
                output_score = score + args.canonical_bonus
                output[word] = output_score
                canonical_count += 1
                decision = "canonical"
            elif args.drop_noncanonical:
                decision = "noncanonical_dropped"
            else:
                output_score = score
                output[word] = score
                noncanonical_count += 1
                decision = "noncanonical"

        report_rows.append(
            {
                "word": word,
                "input_score": score,
                "output_score": output_score,
                "pos": "".join(sorted(pos_initials)),
                "canonical": canonical,
                "decision": decision,
            }
        )

    if args.report is not None:
        write_report(args.report, report_rows)


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
