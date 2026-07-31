#!/usr/bin/env python3
"""Expand a lemma-tier Czech theme dictionary into inflected Preferred entries.

Ingrid's Preferred tier is binary membership, so the theme density a search can
reach is bounded by how many *surface forms* of the theme vocabulary exist at the
lengths the grid actually demands. A lemma-only theme list supplies roughly one
form per concept and almost nothing below length five, which is where a Czech
15x15 spends about half of its slots. This script closes that gap with MorphoDiTa
form generation, under the same marked-class policy `czech_standard_dict.py` uses
for the Standard tier.

Two kinds of input:

  --lemmas PATH     `word;score` entries treated as lemmas and expanded. Proper
                    names are looked up capitalized as well, because MorfFlex
                    stores them that way.
  --literal PATH    `word;score` entries copied through verbatim. Acronyms,
                    hantec and anything already in surface form belong here.

Marked classes are rejected by default following the Standard-tier convention:
vocatives, imperatives, transgressives and every nonstandard tag variant. For
proper-name lemmas the grammatical number is locked to the number of the lemma's
own nominative, so `Brno` does not emit `Brny` and `Zabovresky` keeps its plural.

Example:

  python3 scripts/theme_expand.py \\
    --model /path/to/czech-morfflex2.1-250909.dict \\
    --lemmas local/trials/metro_brno_preferred.dict \\
    --literal local/rich/metro_short.dict \\
    --standard local/trials/standard_clued_n33.dict \\
    --output local/rich/metro_expanded.dict \\
    --report local/rich/metro_expanded.csv

Requires `ufal.morphodita` and a MorfFlex morphology (`.dict`, not the tagger).
"""

from __future__ import annotations

import argparse
import csv
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from ufal.morphodita import Morpho, TaggedLemmas, TaggedLemmasForms

REPORT_FIELDS = ["word", "length", "score", "origin", "lemma", "tag", "in_standard"]

# PDT positional tag: 1 POS, 4 number, 5 case, 10 grade, 11 negation, 15 variant.
TAG_NUMBER = 3
TAG_CASE = 4
TAG_GRADE = 9
TAG_NEGATION = 10
TAG_VARIANT = 14


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.strip().lower())


def load_scored_dict(path: Path) -> dict[str, int]:
    entries: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        word, _, score = text.partition(";")
        word = normalize_word(word)
        if not word:
            continue
        entries[word] = int(score) if score.strip() else 50
    return entries


def load_wordlist(path: Path | None) -> set[str]:
    if path is None:
        return set()
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = normalize_word(line.split("#", 1)[0])
        if word:
            words.add(word)
    return words


def tag_field(tag: str, index: int) -> str:
    return tag[index] if len(tag) > index else "-"


def is_marked(tag: str, args: argparse.Namespace) -> bool:
    """Marked classes rejected by default, mirroring the Standard-tier policy."""
    if args.keep_marked:
        return False
    if tag.startswith(("N", "A")) and tag_field(tag, TAG_CASE) == "5":
        return True  # vocative
    if tag.startswith(("Vi", "Ve")):
        return True  # imperative, transgressive
    if tag_field(tag, TAG_NEGATION) == "N":
        return True  # `ne-` prefixation turns Sokol into nesokoly
    if tag_field(tag, TAG_GRADE) not in "1-":
        return True  # a street name has no comparative: sumavsky -> nejsumavstejsi
    if args.cases and tag_field(tag, TAG_CASE) not in args.cases + "-":
        return True
    return tag_field(tag, TAG_VARIANT) not in args.allowed_variants


def is_proper(lemma_id: str) -> bool:
    """MorfFlex marks proper names with a `_;X` semantic tail."""
    return "_;" in lemma_id


def generate_forms(
    morpho: Morpho,
    lemma: str,
    forms: TaggedLemmasForms,
    lemmas: TaggedLemmas | None = None,
) -> list[tuple[str, str, str]]:
    """(form, tag, lemma_id) for a lemma, trying the capitalized spelling too.

    A theme list harvested from running text is not all lemmas. `metro_brno_preferred`
    holds `bohunicich`, `cejlu`, `luzankami`, `zidenic` and `masarykovy` -- oblique
    forms that MorfFlex cannot generate *from*, because they are not dictionary
    entries. With `lemmas` supplied, those are analyzed back to `Bohunice_;G`,
    `Cejl_;G`, `Luzanky_;G`, `Zidenice_;G`, `Masarykuv_;Y` first, which recovers both
    the base form the list was missing and the rest of the paradigm.
    """
    produced: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()

    def expand(spelling: str) -> None:
        morpho.generate(spelling, None, Morpho.NO_GUESSER, forms)
        for lemma_forms in forms:
            if lemma_forms.lemma in seen_ids:
                continue
            seen_ids.add(lemma_forms.lemma)
            for form in lemma_forms.forms:
                produced.append((form.form, form.tag, lemma_forms.lemma))

    spellings = list(dict.fromkeys([lemma, lemma.capitalize(), lemma.upper()]))
    for spelling in spellings:
        expand(spelling)
    if produced or lemmas is None:
        return produced

    recovered: set[str] = set()
    for spelling in spellings:
        morpho.analyze(spelling, Morpho.NO_GUESSER, lemmas)
        for analysis in lemmas:
            # MorfFlex tags an unrecognized token `X@` and echoes it as its own lemma.
            if not analysis.tag.startswith("X@"):
                recovered.add(analysis.lemma)
    for lemma_id in sorted(recovered):
        expand(lemma_id)
    return produced


def raw_lemma(lemma_id: str) -> str:
    """`Brno_;G` -> `brno`, `Masarykuv_;Y_^(*2)` -> `masarykuv`."""
    return normalize_word(lemma_id.split("_", 1)[0])


def locked_numbers(entries: list[tuple[str, str, str]], lemma_id: str) -> set[str]:
    """Grammatical numbers whose nominative spells the paradigm's own lemma.

    `Brno` is a singular place; MorfFlex still generates a plural paradigm for it.
    `Zabovresky` is genuinely plural-only. Comparing each number's nominative
    against the dictionary form separates the two without a hand-written list.

    The comparison must use the paradigm's lemma, not the input word. When the input
    was an oblique form recovered by analysis, `brna` matches the plural nominative of
    `Brno` and would lock the paradigm to plural, readmitting `brn` and `brnech`.
    """
    base = raw_lemma(lemma_id)
    return {
        tag_field(tag, TAG_NUMBER)
        for form, tag, _ in entries
        if tag_field(tag, TAG_CASE) == "1" and normalize_word(form) == base
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True, help="MorfFlex morphology .dict")
    parser.add_argument("--lemmas", type=Path, action="append", default=[], help="scored lemma list to expand (repeatable)")
    parser.add_argument("--literal", type=Path, action="append", default=[], help="scored surface list copied verbatim (repeatable)")
    parser.add_argument("--standard", type=Path, help="Standard dictionary, for the collision column and --drop-standard-collisions")
    parser.add_argument("--denylist", type=Path, help="words excluded from the output")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="CSV audit, one row per emitted form")
    parser.add_argument("--min-length", type=int, default=3)
    parser.add_argument("--max-length", type=int, default=15)
    parser.add_argument("--allowed-variants", default="-1", help="acceptable PDT tag variant characters (default: standard and rare-standard)")
    parser.add_argument("--keep-marked", action="store_true", help="keep vocatives, imperatives, transgressives and nonstandard variants")
    parser.add_argument(
        "--cases",
        default="",
        help=(
            "limit noun and adjective forms to these PDT case digits, e.g. 1234 for "
            "nominative through accusative. Empty keeps the whole paradigm."
        ),
    )
    parser.add_argument(
        "--no-lemmatize",
        action="store_true",
        help=(
            "do not analyze an unexpandable entry back to its lemma first. A theme list "
            "harvested from running text contains oblique forms (bohunicich, cejlu, "
            "luzankami); without this step each contributes one form instead of a paradigm."
        ),
    )
    parser.add_argument("--no-number-lock", action="store_true", help="do not restrict proper names to the number of their own nominative")
    parser.add_argument("--drop-standard-collisions", action="store_true", help="drop generated forms that already exist in --standard")
    parser.add_argument("--inflected-penalty", type=int, default=0, help="score subtracted from generated forms other than the lemma itself")
    args = parser.parse_args(argv)

    morpho = Morpho.load(str(args.model))
    if morpho is None:
        raise SystemExit(f"Could not load MorphoDiTa morphology: {args.model}")

    standard = load_scored_dict(args.standard) if args.standard else {}
    denylist = load_wordlist(args.denylist)

    scores: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    origins: Counter[str] = Counter()
    unexpanded: list[str] = []

    def emit(word: str, score: int, origin: str, lemma: str, tag: str) -> None:
        if not (args.min_length <= len(word) <= args.max_length):
            return
        if not word.isalpha() or word in denylist:
            return
        if origin == "generated" and args.drop_standard_collisions and word in standard:
            return
        if scores.get(word, -1) >= score:
            return
        scores[word] = score
        rows.append(
            {
                "word": word,
                "length": len(word),
                "score": score,
                "origin": origin,
                "lemma": lemma,
                "tag": tag,
                "in_standard": int(word in standard),
            }
        )

    verbatim: set[str] = set()
    for path in args.literal:
        for word, score in load_scored_dict(path).items():
            verbatim.add(word)
            emit(word, score, "literal", word, "")
            origins["literal"] += 1

    forms = TaggedLemmasForms()
    lemmas = None if args.no_lemmatize else TaggedLemmas()
    for path in args.lemmas:
        for lemma, score in load_scored_dict(path).items():
            if lemma in verbatim:
                # Literal wins. MUNI, VUT and JAMU do have MorfFlex paradigms, and
                # declining an initialism yields MUNU. Listing one as literal is the
                # instruction not to expand it, whether or not morphology exists.
                continue
            produced = generate_forms(morpho, lemma, forms, lemmas)
            if not produced:
                unexpanded.append(lemma)
                emit(lemma, score, "lemma-only", lemma, "")
                continue
            by_lemma_id: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
            for form, tag, lemma_id in produced:
                by_lemma_id[lemma_id].append((form, tag, lemma_id))
            for lemma_id, entries in by_lemma_id.items():
                allowed = None
                if is_proper(lemma_id) and not args.no_number_lock:
                    allowed = locked_numbers(entries, lemma_id)
                for form, tag, _ in entries:
                    if is_marked(tag, args):
                        continue
                    if allowed and tag_field(tag, TAG_NUMBER) not in allowed:
                        continue
                    word = normalize_word(form)
                    if word == lemma:
                        emit(word, score, "lemma", lemma, tag)
                        origins["lemma"] += 1
                    else:
                        emit(word, max(score - args.inflected_penalty, 1), "generated", lemma, tag)
                        origins["generated"] += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{word};{score}\n" for word, score in sorted(scores.items())),
        encoding="utf-8",
    )

    if args.report:
        best = {row["word"]: row for row in rows}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(best.values(), key=lambda row: str(row["word"])))

    lengths = Counter(len(word) for word in scores)
    collisions = sum(1 for word in scores if word in standard)
    print(f"wrote {args.output}: {len(scores)} entries")
    print(f"  by length: {dict(sorted(lengths.items()))}")
    print(f"  origins  : {dict(origins)}")
    print(f"  also in standard: {collisions}")
    if unexpanded:
        print(f"  no morphology for {len(unexpanded)} lemmas, kept verbatim; e.g. {unexpanded[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
