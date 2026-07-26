#!/usr/bin/env python3
"""Locate words in Metropolitan PDF/text sources without generating clues.

PDFs are read with ``pdftotext -layout``, matching the Metropolitan corpus
pipeline. Queries are case-insensitive NFC whole-token matches by default.
Use ``--diacritic-insensitive`` only when accent folding is desired.

Examples:
  metropolitan_word_sources.py archive/ -q Jinačovice
  metropolitan_word_sources.py issue.pdf -q Jinačovice -q Bystrc --jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from metropolitan_theme_dict import discover_inputs, read_document
from ufal.morphodita import Morpho, TaggedLemmas, Tagger


TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SourceRecord:
    query: str
    found: bool
    surface: str
    matched_as: str
    source: str
    page: int | None
    page_offset: int
    context: str
    context_truncated_before: bool
    context_truncated_after: bool




def normalize_token(value: str, diacritic_insensitive: bool) -> str:
    normalized = unicodedata.normalize("NFC", value.casefold())
    if not diacritic_insensitive:
        return normalized
    decomposed = unicodedata.normalize("NFD", normalized)
    return unicodedata.normalize(
        "NFC", "".join(character for character in decomposed if not unicodedata.combining(character))
    )


def query_token(value: str) -> str:
    token = unicodedata.normalize("NFC", value.strip())
    if not token or TOKEN_RE.fullmatch(token) is None:
        raise argparse.ArgumentTypeError(f"query must be one whole token: {value!r}")
    return token


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def find_records(
    paths: Iterable[Path],
    queries: list[str],
    context_chars: int,
    diacritic_insensitive: bool,
    morphology: Morpho | None = None,
) -> list[list[SourceRecord]]:
    """Return records grouped in the same order as the supplied queries."""
    records: list[list[SourceRecord]] = [[] for _ in queries]
    query_indexes: dict[str, list[int]] = {}
    for index, query in enumerate(queries):
        key = normalize_token(query, diacritic_insensitive)
        query_indexes.setdefault(key, []).append(index)
    analyses = TaggedLemmas()

    for path in paths:
        text = read_document(path)
        has_page_numbers = "\f" in text
        pages = text.split("\f") if has_page_numbers else [text]
        source = str(path.resolve())
        for page_index, page_text in enumerate(pages, start=1):
            page_number = page_index if has_page_numbers else None
            for match in TOKEN_RE.finditer(page_text):
                surface = match.group(0)
                matched_indexes: dict[int, str] = {}
                surface_key = normalize_token(surface, diacritic_insensitive)
                for query_index in query_indexes.get(surface_key, []):
                    matched_indexes[query_index] = "surface"
                if morphology is not None:
                    analyses.clear()
                    morphology.analyze(surface, Morpho.NO_GUESSER, analyses)
                    for analysis in analyses:
                        lemma = morphology.rawLemma(analysis.lemma)
                        lemma_key = normalize_token(lemma, diacritic_insensitive)
                        for query_index in query_indexes.get(lemma_key, []):
                            matched_indexes.setdefault(query_index, "lemma")
                if not matched_indexes:
                    continue
                context_start = max(0, match.start() - context_chars)
                context_end = min(len(page_text), match.end() + context_chars)
                for query_index, matched_as in matched_indexes.items():
                    records[query_index].append(
                        SourceRecord(
                            query=queries[query_index],
                            found=True,
                            surface=surface,
                            matched_as=matched_as,
                            source=source,
                            page=page_number,
                            page_offset=match.start(),
                            context=page_text[context_start:context_end],
                            context_truncated_before=context_start > 0,
                            context_truncated_after=context_end < len(page_text),
                        )
                    )
    return records


def empty_record(query: str) -> dict[str, object]:
    return {
        "query": query,
        "found": False,
        "surface": None,
        "matched_as": None,
        "source": None,
        "page": None,
        "page_offset": None,
        "context": None,
        "context_truncated_before": False,
        "context_truncated_after": False,
    }


def printable_context(record: SourceRecord) -> str:
    context = " ".join(record.context.split())
    if record.context_truncated_before:
        context = f"…{context}"
    if record.context_truncated_after:
        context = f"{context}…"
    return context


def write_human(queries: list[str], grouped_records: list[list[SourceRecord]]) -> None:
    for query_index, (query, records) in enumerate(zip(queries, grouped_records)):
        if query_index:
            print()
        print(f"Query: {query}")
        if not records:
            print("  No occurrences found.")
            continue
        print(f"  Occurrences: {len(records)}")
        for record in records:
            location = record.source
            if record.page is not None:
                location += f", page {record.page}"
            location += f", offset {record.page_offset}"
            print(f"  - {location}")
            print(f"    surface: {record.surface}")
            print(f"    matched as: {record.matched_as}")
            print(f"    context: {printable_context(record)}")


def write_jsonl(queries: list[str], grouped_records: list[list[SourceRecord]]) -> None:
    for query, records in zip(queries, grouped_records):
        if records:
            for record in records:
                print(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(empty_record(query), ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Metropolitan PDF/text files or directories",
    )
    parser.add_argument(
        "-q",
        "--query",
        dest="queries",
        action="append",
        required=True,
        type=query_token,
        metavar="WORD",
        help="Whole-token word to locate; repeat for multiple words",
    )
    parser.add_argument(
        "--model",
        type=Path,
        help="Optional MorphoDiTa tagger enabling inflected-form lemma matches",
    )
    parser.add_argument(
        "--context-chars",
        type=nonnegative_int,
        default=160,
        metavar="N",
        help="Maximum source characters retained on each side of a match (default: 160)",
    )
    parser.add_argument(
        "--diacritic-insensitive",
        action="store_true",
        help="Opt in to matching words without regard to diacritics",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Emit one JSON object per occurrence (and per empty query) instead of text",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = discover_inputs(args.inputs)
    if not paths:
        raise SystemExit("No Metropolitan PDF/text inputs found")
    morphology = None
    if args.model is not None:
        tagger = Tagger.load(str(args.model))
        if tagger is None:
            raise SystemExit(f"Could not load MorphoDiTa tagger: {args.model}")
        morphology = tagger.getMorpho()
    try:
        grouped_records = find_records(
            paths,
            args.queries,
            args.context_chars,
            args.diacritic_insensitive,
            morphology,
        )
    except FileNotFoundError as error:
        if error.filename == "pdftotext":
            raise SystemExit("pdftotext is required for PDF inputs") from error
        raise
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() if error.stderr else "unknown extraction error"
        raise SystemExit(f"Could not read PDF with pdftotext: {detail}") from error

    if args.jsonl:
        write_jsonl(args.queries, grouped_records)
    else:
        write_human(args.queries, grouped_records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
