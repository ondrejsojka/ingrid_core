#!/usr/bin/env python3
"""Build an Ingrid word;score dict from cshyphen csTenTen sources.

Reads:
  - cs-all-cstenten.wls   (unique Czech forms, no frequencies)
  - cstenten17.frqwl      (word<TAB>abs_freq)

Writes Ingrid lines `word;score` where:
  - absolute frequency < --min-freq is dropped (default 50; lots of junk below that)
  - score = round(10 * log10(abs_freq))
    so ranks follow corpus frequency without pretending to be STWL's 0/10/…/50 ladder.
    Ingrid's CLI --min-score default is a STWL remnant and is intentionally ignored here.

Example:
  python3 scripts/cstenten_wls_to_dict.py \\
    --wls /tmp/cshyphen/src/cs-all-cstenten.wls \\
    --frqwl /tmp/cshyphen/src/cstenten17.frqwl \\
    -o local/cstenten.dict
"""

from __future__ import annotations

import argparse
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ALPHA_RE = re.compile(r"^[A-Za-zÁÄČĎÉĚÍĹĽŇÓÔŔŘŠŤÚŮÝŽáäčďéěíĺľňóôŕřšťúůýž]+$")


def score_of(freq: int) -> int:
    return max(1, int(round(10 * math.log10(freq))))


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.casefold())


def load_frqwl(path: Path) -> dict[str, int]:
    casefold_sums: dict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8") as source:
        for line in source:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2:
                continue
            word, frequency_string = parts
            try:
                frequency = int(frequency_string)
            except ValueError:
                continue
            casefold_sums[normalize_word(word)] += frequency
    return casefold_sums


def build_counts(
    wls: Path,
    casefold_sums: dict[str, int],
    *,
    min_freq: int,
    min_len: int,
    max_len: int,
) -> tuple[dict[str, int], Counter[str]]:
    kept: dict[str, int] = {}
    stats: Counter[str] = Counter()

    with wls.open(encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                stats["empty"] += 1
                continue
            if not ALPHA_RE.match(raw):
                stats["non_alpha"] += 1
                continue
            if not (min_len <= len(raw) <= max_len):
                stats["bad_len"] += 1
                continue

            # Output words are casefolded, so their frequency must include every
            # capitalization variant from the source corpus.
            key = normalize_word(raw)
            freq = casefold_sums.get(key, 0)
            if not freq:
                stats["no_freq"] += 1
                continue
            stats["matched"] += 1

            if freq < min_freq:
                stats["below_min_freq"] += 1
                continue

            # Multiple source spellings can normalize to the same Ingrid entry.
            prev = kept.get(key)
            if prev is None or freq > prev:
                if prev is not None:
                    stats["collapsed"] += 1
                kept[key] = freq
            else:
                stats["dup_lower"] += 1

    return kept, stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wls", type=Path, required=True, help="Path to cs-all-cstenten.wls")
    p.add_argument("--frqwl", type=Path, required=True, help="Path to cstenten17.frqwl (word\\tfreq)")
    p.add_argument("-o", "--output", type=Path, required=True, help="Output .dict path")
    p.add_argument("--min-freq", type=int, default=50, help="Drop forms with abs freq below this (default: 50)")
    p.add_argument("--min-len", type=int, default=1)
    p.add_argument("--max-len", type=int, default=32)
    p.add_argument("--source-note", type=Path, help="Optional provenance sidecar to write")
    args = p.parse_args(argv)

    casefold_sums = load_frqwl(args.frqwl)
    kept, stats = build_counts(
        args.wls,
        casefold_sums,
        min_freq=args.min_freq,
        min_len=args.min_len,
        max_len=args.max_len,
    )
    if not kept:
        raise SystemExit("No words matched the requested frequency and length filters")

    lines = [
        f"{word};{score_of(freq)}"
        for word, freq in sorted(kept.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    score_hist = Counter(int(line.split(";")[1]) for line in lines)
    print(f"wrote {args.output} ({len(lines)} words)")
    print("stats:", dict(stats))
    print(f"score range: {min(score_hist)}-{max(score_hist)}")
    print("top:", ", ".join(lines[:10]))

    if args.source_note:
        args.source_note.parent.mkdir(parents=True, exist_ok=True)
        args.source_note.write_text(
            "\n".join(
                [
                    f"wls: {args.wls}",
                    f"frqwl: {args.frqwl}",
                    f"min_abs_freq: {args.min_freq}",
                    f"length: {args.min_len}-{args.max_len}",
                    "match: sum of all casefold-equivalent frequency variants",
                    "score: round(10 * log10(abs_freq))",
                    f"entries: {len(lines)}",
                    f"score_range: {min(score_hist)}-{max(score_hist)}",
                    f"stats: {dict(stats)}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
