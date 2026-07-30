#!/usr/bin/env python3
"""Build an Ingrid preferred list from contextually tagged Metropolitan editions.

The emitted dictionary still uses Ingrid's binary Preferred/Standard model.  Corpus
frequency, document recurrence, morphology, entity status, and CSTenTen commonness
are only used here to decide which lemmas qualify for the preferred tier.

Use ``--preset broad-reader`` for the full archive.  For one issue, use
``--preset edition-reader`` and pass the archive cache as
``--background-analysis`` so recurring publication chrome is not thematic.

Requires ``ufal.morphodita`` and ``pdftotext`` when PDF inputs are used.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import re
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from ufal.morphodita import Morpho
from ufal.morphodita import Forms, TaggedLemmas, Tagger, TokenRanges

DEFAULT_STOPWORDS = {
    "brněnský",
    "číslo",
    "editorial",
    "foto",
    "inzerce",
    "magazín",
    "metropolitan",
    "fotoreportáž",
    "brnět",
    "křížovka",
    "luštit",
    "kolařík",
    "loukotová",
    "myslit",
    "obsah",
    "obalilová",
    "ročník",
    "prostora",
    "schmerková",
    "řešení",
    "soutěžní",
    "strana",
    "tiráž",
    "tajenka",
    "vydání",
    "zugalsko",
}
PROPER_MARKER_RE = re.compile(r"_;([A-Za-z])", re.UNICODE)
ANALYSIS_VERSION = 3
STANDARD_LEMMA_VERSION = 1
REPORT_FIELDS = [
    "lemma",
    "selected",
    "reason",
    "output_score",
    "count",
    "documents",
    "entity_count",
    "entity_documents",
    "background_documents",
    "noun_count",
    "adjective_count",
    "verb_count",
    "standard_score",
    "salience",
]


@dataclass
class LemmaStats:
    count: int = 0
    entity_count: int = 0
    documents: int = 0
    entity_documents: int = 0
    noun_count: int = 0
    adjective_count: int = 0
    verb_count: int = 0


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
            words[word] = max(score, words.get(word, 0))
    return words


def load_blocklist(path: Path | None) -> set[str]:
    """One word per line, lowercase NFC; `#` starts a comment."""
    if path is None:
        return set()
    words: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.split("#", 1)[0].strip().lower()
        if word:
            words.add(unicodedata.normalize("NFC", word))
    return words


def standard_lemma_provenance(
    standard_path: Path, model_path: Path
) -> dict[str, object]:
    return {
        "version": STANDARD_LEMMA_VERSION,
        "standard": file_fingerprint(standard_path),
        "model": file_fingerprint(model_path),
    }


def load_or_build_standard_lemmas(
    standard_path: Path,
    cache_path: Path,
    model_path: Path,
    refresh: bool = False,
) -> dict[str, int]:
    metadata_path = cache_path.with_name(f"{cache_path.name}.meta.json")
    expected_provenance = standard_lemma_provenance(standard_path, model_path)
    cached_provenance: dict[str, object] | None = None
    if metadata_path.is_file():
        try:
            cached_provenance = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cached_provenance = None
    if (
        not refresh
        and cache_path.is_file()
        and cached_provenance == expected_provenance
    ):
        return load_scored_dict(cache_path)

    surface_scores = load_scored_dict(standard_path)
    tagger = Tagger.load(str(model_path))
    if tagger is None:
        raise SystemExit(f"Could not load MorphoDiTa tagger: {model_path}")
    morphology = tagger.getMorpho()
    analyses = TaggedLemmas()
    lemma_frequencies: collections.defaultdict[str, float] = collections.defaultdict(float)
    for surface, score in surface_scores.items():
        analyses.clear()
        morphology.analyze(surface, Morpho.NO_GUESSER, analyses)
        candidates = {
            normalize_word(morphology.rawLemma(analysis.lemma))
            for analysis in analyses
            if analysis.tag and analysis.tag[0] in "NAV"
        }
        candidates = {lemma for lemma in candidates if lemma.isalpha()}
        lemma = (
            max(
                candidates,
                key=lambda candidate: (
                    surface_scores.get(candidate, 0),
                    candidate == surface,
                    candidate,
                ),
            )
            if candidates
            else surface
        )
        # The dictionary is lowercased, so some proper names lose their
        # MorphoDiTa analysis. Keeping their surface as a fallback still gives
        # the corresponding Metropolitan lemma a conservative commonness floor.
        lemma_frequencies[lemma] += 10.0 ** (score / 10.0)

    lemma_scores = {
        lemma: round(10.0 * math.log10(frequency))
        for lemma, frequency in lemma_frequencies.items()
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        "".join(
            f"{lemma};{score}\n"
            for lemma, score in sorted(
                lemma_scores.items(), key=lambda item: (-item[1], item[0])
            )
        ),
        encoding="utf-8",
    )
    metadata_path.write_text(
        json.dumps(expected_provenance, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {cache_path}: {len(lemma_scores)} CSTenTen lemmas")
    return lemma_scores


def edition_identity(path: Path) -> str | None:
    name = path.stem.casefold()
    long_name = re.search(
        r"(20\d{2})[-_](1[0-2]|0?[1-9])(?:[-_](1[0-2]|0?[1-9]))?(?=[_-]|$)",
        name,
    )
    if long_name:
        year, first_month, second_month = long_name.groups()
        months = f"{int(first_month):02d}"
        if second_month:
            months += f"-{int(second_month):02d}"
        return f"{year}-{months}"
    short_name = re.search(r"(?:bm|mb)_(\d{2})(0[1-9]|1[0-2])(?=[_.-]|$)", name)
    if short_name:
        year, month = short_name.groups()
        return f"20{year}-{month}"
    return None


def discover_inputs(paths: Iterable[Path]) -> list[Path]:
    candidates: set[Path] = set()
    for path in paths:
        if path.is_dir():
            candidates.update(path.rglob("*.pdf"))
            candidates.update(
                text_path
                for text_path in path.rglob("*.txt")
                if edition_identity(text_path) is not None
            )
        elif path.suffix.casefold() in {".pdf", ".txt"} and path.is_file():
            candidates.add(path)

    by_identity: dict[str, Path] = {}
    for path in sorted(candidates):
        identity = edition_identity(path)
        if identity is None and path.suffix.casefold() == ".txt":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            identity = f"text:{digest}"
        identity = identity or f"path:{path.resolve()}"
        current = by_identity.get(identity)
        priority = (path.suffix.casefold() == ".pdf", "web" in path.stem.casefold())
        current_priority = (
            (current.suffix.casefold() == ".pdf", "web" in current.stem.casefold())
            if current
            else (False, False)
        )
        if current is None or priority > current_priority:
            by_identity[identity] = path
    return sorted(by_identity.values())


def read_document(path: Path) -> str:
    if path.suffix.casefold() == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout

def normalized_line(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.casefold().split()))


def page_edge_indices(lines: list[str], edge_line_count: int = 2) -> set[int]:
    nonempty = [index for index, line in enumerate(lines) if normalized_line(line)]
    return set(nonempty[:edge_line_count] + nonempty[-edge_line_count:])


def remove_repeated_lines(
    documents: list[tuple[Path, str]], minimum_documents: int
) -> list[tuple[Path, str]]:
    edge_line_documents: collections.Counter[str] = collections.Counter()
    repeated_within_documents: set[str] = set()
    for _, text in documents:
        document_edge_lines: collections.Counter[str] = collections.Counter()
        for page in text.split("\f"):
            lines = page.splitlines()
            for index in page_edge_indices(lines):
                document_edge_lines[normalized_line(lines[index])] += 1
        edge_line_documents.update(document_edge_lines)
        repeated_within_documents.update(
            line for line, count in document_edge_lines.items() if count >= 4
        )
    repeated = repeated_within_documents | {
        line
        for line, count in edge_line_documents.items()
        if count >= minimum_documents
    }

    cleaned_documents = []
    for path, text in documents:
        cleaned_pages = []
        for page in text.split("\f"):
            lines = page.splitlines()
            edge_indices = page_edge_indices(lines)
            cleaned_pages.append(
                "\n".join(
                    line
                    for index, line in enumerate(lines)
                    if index not in edge_indices or normalized_line(line) not in repeated
                )
            )
        cleaned_documents.append((path, "\f".join(cleaned_pages)))
    return cleaned_documents


def proper_markers(tagged_lemma: str) -> set[str]:
    return set(PROPER_MARKER_RE.findall(tagged_lemma))


def analyze_documents(
    paths: list[Path], model_path: Path, boilerplate_documents: int
) -> tuple[dict[str, LemmaStats], int]:
    tagger = Tagger.load(str(model_path))
    if tagger is None:
        raise SystemExit(f"Could not load MorphoDiTa tagger: {model_path}")
    tokenizer = tagger.newTokenizer()
    if tokenizer is None:
        raise SystemExit("The MorphoDiTa model does not contain a tokenizer")
    morphology = tagger.getMorpho()
    forms = Forms()
    ranges = TokenRanges()
    tagged = TaggedLemmas()
    stats: dict[str, LemmaStats] = {}
    content_tokens = 0

    documents = remove_repeated_lines(
        [(path, read_document(path)) for path in paths], boilerplate_documents
    )
    for index, (path, text) in enumerate(documents, start=1):
        tokenizer.setText(text)
        document_lemmas: set[str] = set()
        document_entity_lemmas: set[str] = set()
        while tokenizer.nextSentence(forms, ranges):
            tagger.tag(forms, tagged)
            markers = [proper_markers(token.lemma) for token in tagged]
            for token_index, token in enumerate(tagged):
                if not token.tag or token.tag[0] not in "NAV":
                    continue
                lemma = normalize_word(morphology.rawLemma(token.lemma))
                if not lemma.isalpha():
                    continue
                own_markers = markers[token_index]
                adjacent_person = "Y" in own_markers and any(
                    markers[neighbor] & {"S", "Y"}
                    for neighbor in (token_index - 1, token_index + 1)
                    if 0 <= neighbor < len(markers)
                )
                is_entity = bool(own_markers & {"G", "K", "R", "S", "m"}) or adjacent_person
                item = stats.setdefault(lemma, LemmaStats())
                item.count += 1
                item.entity_count += int(is_entity)
                if is_entity:
                    document_entity_lemmas.add(lemma)
                item.noun_count += int(token.tag[0] == "N")
                item.adjective_count += int(token.tag[0] == "A")
                item.verb_count += int(token.tag[0] == "V")
                document_lemmas.add(lemma)
                content_tokens += 1
        for lemma in document_lemmas:
            stats[lemma].documents += 1
        for lemma in document_entity_lemmas:
            stats[lemma].entity_documents += 1
        print(f"tagged {index}/{len(paths)}: {path.name}")
    return stats, content_tokens


def file_fingerprint(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def analysis_provenance(
    paths: list[Path], model_path: Path, boilerplate_documents: int
) -> dict[str, object]:
    return {
        "version": ANALYSIS_VERSION,
        "model": file_fingerprint(model_path),
        "boilerplate_documents": boilerplate_documents,
        "inputs": [file_fingerprint(path) for path in paths],
    }


def save_analysis(
    path: Path,
    stats: dict[str, LemmaStats],
    documents: int,
    content_tokens: int,
    provenance: dict[str, object],
) -> None:
    payload = {
        "provenance": provenance,
        "documents": documents,
        "content_tokens": content_tokens,
        "lemmas": {lemma: asdict(item) for lemma, item in stats.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_analysis(
    path: Path,
) -> tuple[dict[str, LemmaStats], int, int, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    provenance = payload.get("provenance", {})
    if provenance.get("version") != ANALYSIS_VERSION:
        raise SystemExit(
            f"Analysis cache {path} is obsolete; provide its source inputs "
            "and use --refresh-analysis"
        )
    stats = {lemma: LemmaStats(**item) for lemma, item in payload["lemmas"].items()}
    return (
        stats,
        int(payload["documents"]),
        int(payload["content_tokens"]),
        provenance,
    )


def corpus_salience(count: int, standard_score: int | None) -> float | None:
    if standard_score is None:
        return None
    # Both terms are on the same 10*log10 scale.  The absolute corpus-size
    # offset is constant, so thresholds are calibrated empirically.
    return 10.0 * math.log10(count) - standard_score


def choose_reason(
    lemma: str,
    item: LemmaStats,
    standard_score: int | None,
    salience: float | None,
    background_item: LemmaStats | None,
    background_documents: int,
    denylist: set[str],
    curated: set[str],
    stopwords: set[str],
    args: argparse.Namespace,
) -> str:
    if lemma in denylist:
        return "denylist"
    if lemma in curated:
        return "curated"
    if len(lemma) < args.min_length:
        return "too_short"
    if lemma in stopwords:
        return "stopword"
    if (
        background_item is not None
        and background_documents > 0
        and background_item.documents / background_documents
        > args.background_max_document_ratio
    ):
        return "background_ubiquitous"
    is_entity = (
        item.entity_count >= args.entity_min_count
        and item.entity_count / item.count >= args.entity_min_fraction
    )
    if is_entity:
        if item.entity_documents < args.entity_min_documents:
            return "entity_documents"
        if (
            args.document_count >= 4
            and item.entity_documents / args.document_count
            > args.entity_max_document_ratio
        ):
            return "ubiquitous_entity"
        if standard_score is None and (
            item.entity_count < args.unknown_entity_min_count
            or item.entity_documents < args.unknown_entity_min_documents
        ):
            return "unknown_entity_frequency"
        if standard_score is not None and (
            salience is None or salience < args.entity_min_salience
        ):
            return "generic_entity"
        return "entity"
    if args.entities_only:
        return "not_entity"
    dominant_part = max(
        ("N", item.noun_count),
        ("A", item.adjective_count),
        ("V", item.verb_count),
        key=lambda part: part[1],
    )[0]
    if dominant_part not in args.content_parts_of_speech:
        return "part_of_speech"
    if item.count < args.min_count:
        return "frequency"
    if item.documents < args.min_documents:
        return "documents"
    if (
        args.document_count >= 4
        and item.documents / args.document_count > args.max_document_ratio
    ):
        return "ubiquitous"
    if standard_score is None:
        return "not_in_standard"
    if standard_score < args.min_standard_score:
        return "low_standard_quality"
    if standard_score > args.max_standard_score:
        return "too_common"
    if salience is None or salience < args.min_salience:
        return "below_salience"
    return "content"


def preferred_score(item: LemmaStats, reason: str) -> int:
    frequency_bonus = min(60, round(20 * math.log10(max(1, item.count))))
    entity_bonus = 30 if reason == "entity" else 0
    return 100 + frequency_bonus + entity_bonus


def apply_preset(args: argparse.Namespace) -> None:
    presets = {
        "broad-reader": {
            "min_length": 7,
            "content_parts_of_speech": "N",
            "min_count": 5,
            "min_documents": 2,
            "entity_min_count": 3,
            "entity_min_documents": 2,
            "entity_min_fraction": 0.6,
            "entity_max_document_ratio": 0.8,
            "unknown_entity_min_count": 10,
            "unknown_entity_min_documents": 5,
            "entity_min_salience": -20.0,
            "min_standard_score": 32,
            "max_standard_score": 50,
            "min_salience": -22.0,
            "max_document_ratio": 0.9,
        },
        "edition-reader": {
            "min_length": 7,
            "content_parts_of_speech": "N",
            "min_count": 2,
            "min_documents": 1,
            "entity_min_count": 2,
            "entity_min_documents": 1,
            "entity_min_fraction": 0.6,
            "entity_max_document_ratio": 1.0,
            "unknown_entity_min_count": 2,
            "unknown_entity_min_documents": 1,
            "entity_min_salience": -28.0,
            "min_standard_score": 28,
            "max_standard_score": 52,
            "min_salience": -32.0,
            "max_document_ratio": 1.0,
        },
    }
    if args.preset:
        for name, value in presets[args.preset].items():
            setattr(args, name, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        choices=("broad-reader", "edition-reader"),
        help="Apply a calibrated complete filter configuration; overrides filter flags",
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="Metropolitan PDF/text files or directories")
    parser.add_argument("--model", type=Path, required=True, help="MorphoDiTa contextual tagger model")
    parser.add_argument("--standard", type=Path, required=True, help="CSTenTen-style scored standard dictionary")
    parser.add_argument(
        "--standard-lemma-cache",
        type=Path,
        help="Lemma-frequency cache; default: <standard stem>_lemmas.dict",
    )
    parser.add_argument("--refresh-standard-lemmas", action="store_true")
    parser.add_argument("--curated", type=Path, help="Optional scored words always placed in Preferred")
    parser.add_argument(
        "--denylist",
        type=Path,
        help="UTF-8 words excluded from Preferred even when curated",
    )
    parser.add_argument("--output", type=Path, required=True, help="Preferred Ingrid word;score dictionary")
    parser.add_argument("--report", type=Path, help="CSV report containing every analyzed lemma and decision")
    parser.add_argument("--analysis-cache", type=Path, help="Reusable contextual-analysis JSON cache")
    parser.add_argument(
        "--background-analysis",
        type=Path,
        help="Optional broader analysis cache used to remove recurring boilerplate",
    )
    parser.add_argument("--background-max-document-ratio", type=float, default=0.8)
    parser.add_argument("--refresh-analysis", action="store_true")
    parser.add_argument(
        "--boilerplate-documents",
        type=int,
        default=4,
        help="Drop recurring normalized lines only at PDF page edges",
    )
    parser.add_argument("--min-length", type=int, default=5)
    parser.add_argument("--min-count", type=int, default=5)
    parser.add_argument("--min-documents", type=int, default=2)
    parser.add_argument("--entity-min-count", type=int, default=3)
    parser.add_argument("--entity-min-documents", type=int, default=2)
    parser.add_argument("--entity-min-fraction", type=float, default=0.5)
    parser.add_argument("--entity-max-document-ratio", type=float, default=0.8)
    parser.add_argument("--unknown-entity-min-count", type=int, default=6)
    parser.add_argument("--unknown-entity-min-documents", type=int, default=3)
    parser.add_argument("--entity-min-salience", type=float, default=-20.0)
    parser.add_argument("--entities-only", action="store_true")
    parser.add_argument("--content-parts-of-speech", default="NAV")
    parser.add_argument("--min-standard-score", type=int, default=30)
    parser.add_argument("--max-standard-score", type=int, default=55)
    parser.add_argument("--min-salience", type=float, default=-25.0)
    parser.add_argument("--max-document-ratio", type=float, default=0.9)
    parser.add_argument("--stopword", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    apply_preset(args)
    paths = discover_inputs(args.inputs)
    if args.analysis_cache and args.analysis_cache.is_file() and not args.refresh_analysis:
        stats, documents, content_tokens, provenance = load_analysis(
            args.analysis_cache
        )
        current = analysis_provenance(paths, args.model, args.boilerplate_documents)
        if not paths:
            current["inputs"] = provenance.get("inputs")
        if provenance != current:
            raise SystemExit(
                f"Analysis cache {args.analysis_cache} does not match its "
                "inputs or analysis configuration; use --refresh-analysis"
            )
        print(f"loaded {len(stats)} lemmas from {args.analysis_cache}")
    else:
        if not paths:
            raise SystemExit("No PDF/text inputs found and no analysis cache is available")
        stats, content_tokens = analyze_documents(
            paths, args.model, args.boilerplate_documents
        )
        documents = len(paths)
        if args.analysis_cache:
            save_analysis(
                args.analysis_cache,
                stats,
                documents,
                content_tokens,
                analysis_provenance(
                    paths, args.model, args.boilerplate_documents
                ),
            )
    args.document_count = documents

    standard_lemma_cache = args.standard_lemma_cache or args.standard.with_name(
        f"{args.standard.stem}_lemmas.dict"
    )
    standard = load_or_build_standard_lemmas(
        args.standard,
        standard_lemma_cache,
        args.model,
        args.refresh_standard_lemmas,
    )
    denylist = load_blocklist(args.denylist)
    curated_scores = load_scored_dict(args.curated) if args.curated else {}
    blocked_curated = set(curated_scores) & denylist
    curated_scores = {
        lemma: score
        for lemma, score in curated_scores.items()
        if lemma not in denylist
    }
    curated = set(curated_scores)
    stopwords = DEFAULT_STOPWORDS | {normalize_word(word) for word in args.stopword}
    selected: dict[str, int] = dict(curated_scores)
    report_rows: list[dict[str, object]] = []
    decisions: collections.Counter[str] = collections.Counter()
    background_stats: dict[str, LemmaStats] = {}
    background_documents = 0
    if args.background_analysis:
        background_stats, background_documents, _, _ = load_analysis(
            args.background_analysis
        )

    for lemma, item in stats.items():
        standard_score = standard.get(lemma)
        salience = corpus_salience(item.count, standard_score)
        reason = choose_reason(
            lemma,
            item,
            standard_score,
            salience,
            background_stats.get(lemma),
            background_documents,
            denylist,
            curated,
            stopwords,
            args,
        )
        decisions[reason] += 1
        if reason in {"curated", "entity", "content"}:
            selected[lemma] = max(
                selected.get(lemma, 0), preferred_score(item, reason)
            )
        report_rows.append(
            {
                "lemma": lemma,
                "selected": reason in {"curated", "entity", "content"},
                "reason": reason,
                "output_score": selected.get(lemma, ""),
                "count": item.count,
                "documents": item.documents,
                "entity_count": item.entity_count,
                "entity_documents": item.entity_documents,
                "background_documents": (
                    background_stats[lemma].documents
                    if lemma in background_stats
                    else 0
                ),
                "noun_count": item.noun_count,
                "adjective_count": item.adjective_count,
                "verb_count": item.verb_count,
                "standard_score": "" if standard_score is None else standard_score,
                "salience": "" if salience is None else f"{salience:.3f}",
            }
        )
    for lemma in sorted(blocked_curated - stats.keys()):
        decisions["denylist"] += 1
        report_rows.append(
            {
                "lemma": lemma,
                "selected": False,
                "reason": "denylist",
                "output_score": "",
                "count": 0,
                "documents": 0,
                "entity_count": 0,
                "entity_documents": 0,
                "background_documents": 0,
                "noun_count": 0,
                "adjective_count": 0,
                "verb_count": 0,
                "standard_score": standard.get(lemma, ""),
                "salience": "",
            }
        )
    for lemma in sorted(curated - stats.keys()):
        decisions["curated"] += 1
        report_rows.append(
            {
                "lemma": lemma,
                "selected": True,
                "reason": "curated",
                "output_score": selected[lemma],
                "count": 0,
                "documents": 0,
                "entity_count": 0,
                "entity_documents": 0,
                "background_documents": 0,
                "noun_count": 0,
                "adjective_count": 0,
                "verb_count": 0,
                "standard_score": standard.get(lemma, ""),
                "salience": "",
            }
        )

    ordered = sorted(selected.items(), key=lambda item: (-item[1], item[0]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{word};{score}\n" for word, score in ordered), encoding="utf-8"
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(report_rows, key=lambda row: str(row["lemma"])))

    print(
        f"wrote {args.output}: {len(selected)} preferred lemmas from "
        f"{documents} documents / {content_tokens} content tokens; {dict(decisions)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
