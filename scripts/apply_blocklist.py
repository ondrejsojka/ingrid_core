#!/usr/bin/env python3
"""Remove blocklisted entries from an existing word;score dictionary of either tier,
writing the remaining entries sorted by word."""

from __future__ import annotations

import argparse
import csv
import unicodedata
from pathlib import Path


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


def write_report(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=["word", "score", "decision"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: str(row["word"])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Existing word;score dictionary")
    parser.add_argument("--output", type=Path, required=True, help="Filtered word;score dictionary")
    parser.add_argument("--blocklist", type=Path, required=True, help="UTF-8 words to remove")
    parser.add_argument("--report", type=Path, help="Optional CSV report of removed entries")
    args = parser.parse_args()

    words = load_scored_dict(args.input)
    blocklist = load_blocklist(args.blocklist)
    removed = [
        {"word": word, "score": score, "decision": "denylist"}
        for word, score in words.items()
        if word in blocklist
    ]
    output = {
        word: score for word, score in words.items() if word not in blocklist
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{word};{score}\n" for word, score in sorted(output.items())),
        encoding="utf-8",
    )
    if args.report is not None:
        write_report(args.report, removed)

    print(
        f"wrote {args.output}: {len(words)} input entries, "
        f"{len(removed)} removed, {len(output)} output entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
