#!/usr/bin/env python3
"""Build a flat-score Ingrid supplement from a ČNK ``capek`` word-form export.

Export the complete word-form frequency list from KonText's ``capek`` corpus as
plain text.  This script reads its first field (separated by a tab, semicolon,
or whitespace), normalizes each form, and writes every usable form as
``word;30``.  Frequency is intentionally ignored: corpus attestation is the
only inclusion criterion.

The output is an *addition* to the CSTenTen Standard dictionary.  Concatenate
it after the filtered CSTenTen dictionary; Ingrid preserves the first
normalized duplicate, so existing CSTenTen entries retain their scores.

Example:
  python3 scripts/capek_to_dict.py \\
      --input local/capek-word-forms.txt \\
      --output local/capek.dict \\
      --denylist resources/blocklist_cs.txt
"""

from __future__ import annotations

import argparse
import unicodedata
from collections import Counter
from pathlib import Path


SCORE = 30


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.casefold()).strip()


def load_denylist(path: Path | None) -> set[str]:
    if path is None:
        return set()
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.split("#", 1)[0].strip()
        if word:
            words.add(normalize_word(word))
    return words


def first_field(line: str) -> str:
    """Read a form from KonText TXT or a simple delimited frequency export."""
    # A tab is whitespace, so split(maxsplit=1) covers the tab and whitespace
    # exports; partition handles the semicolon-delimited one.
    return line.split(maxsplit=1)[0].partition(";")[0] if line.strip() else ""


def build_words(
    source: Path,
    denylist: set[str],
    *,
    min_len: int,
    max_len: int,
) -> tuple[set[str], Counter[str]]:
    words = set()
    stats: Counter[str] = Counter()

    for line in source.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            stats["blank_or_comment"] += 1
            continue
        word = normalize_word(first_field(raw).strip('"'))
        if not word:
            stats["empty"] += 1
        elif not word.isalpha():
            stats["non_alpha"] += 1
        elif not min_len <= len(word) <= max_len:
            stats["bad_len"] += 1
        elif word in denylist:
            stats["denylist"] += 1
        elif word in words:
            stats["duplicate"] += 1
        else:
            words.add(word)
            stats["kept"] += 1
    return words, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Full KonText capek word-form TXT export")
    parser.add_argument("--output", type=Path, required=True, help="Flat-score Ingrid supplement")
    parser.add_argument("--denylist", type=Path, help="UTF-8 words to exclude; # begins a comment")
    parser.add_argument("--min-len", type=int, default=1)
    parser.add_argument("--max-len", type=int, default=32)
    parser.add_argument("--source-note", type=Path, help="Optional provenance sidecar")
    args = parser.parse_args()

    if args.min_len < 1 or args.max_len < args.min_len:
        parser.error("require 1 <= --min-len <= --max-len")

    denylist = load_denylist(args.denylist)
    words, stats = build_words(
        args.input,
        denylist,
        min_len=args.min_len,
        max_len=args.max_len,
    )
    if not words:
        raise SystemExit("No usable word forms in the requested export")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{word};{SCORE}\n" for word in sorted(words)),
        encoding="utf-8",
    )
    print(f"wrote {args.output} ({len(words)} words; score {SCORE})")
    print("stats:", dict(stats))

    if args.source_note:
        args.source_note.parent.mkdir(parents=True, exist_ok=True)
        args.source_note.write_text(
            "\n".join(
                [
                    "corpus: capek",
                    f"export: {args.input}",
                    "selection: every normalized alphabetic word form",
                    f"length: {args.min_len}-{args.max_len}",
                    f"score: {SCORE}",
                    "frequency: ignored",
                    f"denylist: {args.denylist or 'none'}",
                    f"entries: {len(words)}",
                    f"stats: {dict(stats)}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
