#!/usr/bin/env python3
"""Grade a corpus-derived theme dictionary by reader recognizability.

`metropolitan_theme_dict.py` selects lemmas that are *distinctive for the
publication*. That is the right retrieval step and the wrong tier. Ingrid's
Preferred tier is the search objective: every entry the solver places counts as
a win, so an entry a reader would not recognize as belonging to this magazine
spends the objective on nothing.

Measured on `local/trials/metro_brno_preferred.dict` (447 entries, Brnensky
Metropolitan): 195 entries are given names and 169 are geographic, but only 33
are Brno toponyms. A city magazine's most distinctive frequent tokens are the
names of the people it interviews, and a crossword full of first names reads
exactly like a generic crossword.

Two publication-independent signals separate the tier:

  semantic class    MorfFlex tags proper names with a `_;X` marker. `G`/`K`/`R`/`m`
                    (place, institution, brand, other proper) carry publication
                    identity; `Y`/`S`/`E` (given name, surname, nationality) do not. A Czech toponym is very
                    often also a surname, so a drop class only bites when no keep class is present.
  national rarity   A publication's own vocabulary is nationally rare. Scoring
                    candidates against a national reference corpus separates
                    Zabovresky (CSTenTen 35) from Berlin (46) without a place list.

Acronyms are mined separately because they are the only source of three- and
four-letter theme entries in Czech: every toponym and institution name is longer,
while a 15x15 Czech grid spends about half of its slots at length three or four.

Example:

  python3 scripts/theme_tier.py \\
    --model /path/to/czech-morfflex2.1-250909.dict \\
    --input local/trials/metro_brno_preferred.dict \\
    --reference local/cstenten.dict --max-reference-score 41 \\
    --corpus /tmp/Metropolitan_2026-7-8_web.txt --mine-acronyms \\
    --output local/rich/metro_core.dict --report local/rich/metro_core.csv

Requires `ufal.morphodita` and a MorfFlex morphology (`.dict`, not the tagger).
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import unicodedata
from pathlib import Path

from ufal.morphodita import Morpho, TaggedLemmasForms

REPORT_FIELDS = ["word", "length", "score", "verdict", "classes", "reference_score", "reason"]

# MorfFlex `_;X` semantic markers.
CLASS_NAMES = {
    "G": "geographic",
    "K": "institution",
    "R": "product",
    "m": "other-proper",
    "Y": "given-name",
    "S": "surname",
    "E": "nationality",
    "H": "chemistry",
    "U": "medicine",
    "L": "natural-science",
    "j": "other",
    "g": "geographic-other",
    "b": "abbreviation",
    ".": "common",
}

ACRONYM_RE = re.compile(r"\b[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ0-9]{1,5}\b")
TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


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
        if word:
            entries[word] = int(score) if score.strip() else 50
    return entries


def load_wordlist(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {
        normalize_word(line.split("#", 1)[0])
        for line in path.read_text(encoding="utf-8").splitlines()
        if normalize_word(line.split("#", 1)[0])
    }


def parse_classes(value: str) -> frozenset[str]:
    return frozenset(character for character in value if character not in ", \t")


def semantic_classes(morpho: Morpho, forms: TaggedLemmasForms, word: str) -> set[str]:
    """MorfFlex semantic markers for every paradigm this spelling belongs to."""
    classes: set[str] = set()
    for spelling in dict.fromkeys([word, word.capitalize(), word.upper()]):
        morpho.generate(spelling, None, Morpho.NO_GUESSER, forms)
        for lemma_forms in forms:
            _, marker, tail = lemma_forms.lemma.partition("_;")
            classes.add(tail[0] if marker and tail else ".")
    return classes


def mine_acronyms(paths: list[Path], min_count: int) -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()
    for path in paths:
        for match in ACRONYM_RE.findall(path.read_text(encoding="utf-8")):
            if match.isalpha():
                counts[normalize_word(match)] += 1
    return collections.Counter(
        {word: count for word, count in counts.items() if count >= min_count}
    )


def corpus_tokens(paths: list[Path]) -> set[str]:
    """Every alphabetic token in the publication, case-folded.

    Used as an attestation test for entries MorfFlex cannot analyze. A theme list
    built from PDF text carries column-break fragments -- `sportov`, `jihomo`,
    `metropo`, `luzan` -- which look exactly like initialisms to a morphology check
    but never occur as standalone tokens. `Stetl`, `Archi` and `Stivin` do.
    """
    tokens: set[str] = set()
    for path in paths:
        for match in TOKEN_RE.findall(path.read_text(encoding="utf-8")):
            tokens.add(normalize_word(match))
    return tokens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", type=Path, required=True, help="MorfFlex morphology .dict")
    parser.add_argument("--input", type=Path, action="append", default=[], required=True, help="scored theme dictionary to grade (repeatable)")
    parser.add_argument("--reference", type=Path, help="national reference corpus dictionary, e.g. local/cstenten.dict")
    parser.add_argument("--max-reference-score", type=int, default=41, help="reject entries at or above this reference-corpus score (default: 41)")
    parser.add_argument("--keep-classes", type=parse_classes, default=parse_classes("GKRmgb"), help="MorfFlex semantic markers that carry publication identity")
    parser.add_argument("--drop-classes", type=parse_classes, default=parse_classes("YSE"), help="semantic markers that never do, even when also matching --keep-classes")
    parser.add_argument("--keep-common", action="store_true", help="also keep common nouns that clear the rarity test")
    parser.add_argument("--corpus", type=Path, action="append", default=[], help="publication text for acronym mining (repeatable)")
    parser.add_argument("--mine-acronyms", action="store_true")
    parser.add_argument("--acronym-min-count", type=int, default=1)
    parser.add_argument("--acronym-score", type=int, default=260)
    parser.add_argument("--allowlist", type=Path, help="words kept regardless of every test")
    parser.add_argument("--denylist", type=Path, help="words rejected regardless of every test")
    parser.add_argument("--min-length", type=int, default=3)
    parser.add_argument(
        "--trust-input-score",
        type=int,
        help=(
            "keep entries scoring at least this in --input without further tests. The upstream "
            "theme builder already ranks by salience against the publication, and on Metropolitan "
            "the >=200 slice is essentially pure Brno (Zabovresky 270, Spilberk 270, Luzanky 260) "
            "while the 150-165 bulk is national vocabulary (Berlin 156, Amsterdam 150). Entries "
            "below the cut still go through the class and rarity gates."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--output-literal",
        type=Path,
        help=(
            "write initialisms and other entries with no paradigm here instead of to --output. "
            "They must reach theme_expand.py through --literal, not --lemmas: MUNI is not "
            "declinable, and generating from it produces MUNU."
        ),
    )
    parser.add_argument("--report", type=Path, help="CSV audit with a verdict for every input word")
    args = parser.parse_args(argv)

    morpho = Morpho.load(str(args.model))
    if morpho is None:
        raise SystemExit(f"Could not load MorphoDiTa morphology: {args.model}")

    reference = load_scored_dict(args.reference) if args.reference else {}
    allowlist = load_wordlist(args.allowlist)
    denylist = load_wordlist(args.denylist)

    candidates: dict[str, int] = {}
    for path in args.input:
        for word, score in load_scored_dict(path).items():
            candidates[word] = max(candidates.get(word, 0), score)

    acronyms: collections.Counter[str] = collections.Counter()
    if args.mine_acronyms:
        if not args.corpus:
            raise SystemExit("--mine-acronyms requires at least one --corpus")
        acronyms = mine_acronyms(args.corpus, args.acronym_min_count)
        for word in acronyms:
            candidates.setdefault(word, args.acronym_score)

    attested = corpus_tokens(args.corpus) if args.corpus else None

    forms = TaggedLemmasForms()
    kept: dict[str, int] = {}
    literal: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    verdicts: collections.Counter[str] = collections.Counter()

    for word, score in sorted(candidates.items()):
        reference_score = reference.get(word)
        classes = semantic_classes(morpho, forms, word)
        label = ",".join(sorted(CLASS_NAMES.get(item, item) for item in classes))

        def record(verdict: str, reason: str) -> None:
            verdicts[verdict] += 1
            rows.append(
                {
                    "word": word,
                    "length": len(word),
                    "score": score,
                    "verdict": verdict,
                    "classes": label,
                    "reference_score": "" if reference_score is None else reference_score,
                    "reason": reason,
                }
            )

        if word in denylist:
            record("reject", "denylist")
            continue
        if word in allowlist:
            kept[word] = score
            record("keep", "allowlist")
            continue
        if len(word) < args.min_length:
            record("reject", f"shorter than {args.min_length}")
            continue
        if word in acronyms:
            # An all-caps run is only an acronym if it is not simply a headline set
            # in capitals: AKCE, CENU and DALSI all analyze as ordinary Czech words,
            # while DPMB, MMB and CEITEC have no common-noun paradigm at all.
            if classes and classes <= {"."}:
                record("reject", "all-caps common word, not an acronym")
                continue
            if reference_score is not None and reference_score >= args.max_reference_score:
                record("reject", f"all-caps but nationally common, reference score {reference_score}")
                continue
            literal[word] = max(score, args.acronym_score)
            record("keep", f"acronym, {acronyms[word]} occurrences in corpus")
            continue
        if args.trust_input_score is not None and score >= args.trust_input_score:
            kept[word] = score
            record("keep", f"salience {score} at or above {args.trust_input_score}")
            continue
        if (classes & args.drop_classes) and not (classes & args.keep_classes):
            record("reject", f"person/nationality class ({label})")
            continue
        if not classes:
            # No paradigm at all: an initialism or a coinage. Either way it is not a
            # word a reader meets outside this publication, which is the whole test --
            # but only if the publication actually contains it. PDF column breaks leave
            # fragments (`sportov`, `jihomo`, `metropo`) that pass every morphology
            # check because no morphology exists for them.
            if attested is not None and word not in attested:
                record("reject", "no paradigm and not attested in the corpus")
                continue
            literal[word] = score
            record("keep", "no paradigm, reads as an initialism")
            continue
        if not (classes & args.keep_classes):
            if not (args.keep_common and classes == {"."}):
                record("reject", f"no identity-bearing class ({label})")
                continue
        if reference_score is not None and reference_score >= args.max_reference_score:
            record("reject", f"nationally common, reference score {reference_score}")
            continue
        kept[word] = score
        record("keep", "distinctive " + label)

    if args.output_literal is None:
        kept.update(literal)
        literal = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{word};{score}\n" for word, score in sorted(kept.items())), encoding="utf-8"
    )
    if args.output_literal is not None:
        args.output_literal.parent.mkdir(parents=True, exist_ok=True)
        args.output_literal.write_text(
            "".join(f"{word};{score}\n" for word, score in sorted(literal.items())),
            encoding="utf-8",
        )
        print(f"wrote {args.output_literal}: {len(literal)} entries with no paradigm")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    lengths = collections.Counter(len(word) for word in list(kept) + list(literal))
    print(f"wrote {args.output}: {len(kept) + len(literal)} of {len(candidates)} candidates kept")
    print(f"  verdicts : {dict(verdicts)}")
    print(f"  by length: {dict(sorted(lengths.items()))}")
    if acronyms:
        print(f"  acronyms mined: {len(acronyms)} -> {sorted(acronyms)[:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
