#!/usr/bin/env python3
"""Turn a hand-authored, machine-attested theme table into a scored dictionary.

Generalization of the one-off `local/rich/build_metro_short.py`: the curated table is
data (`resources/<pub>/curated.tsv`), not code, and the attestation rule is the same —
every row carries a regex that MUST match the publication corpus, otherwise the build
fails. Evidence is never invented.

Rows are split into two outputs because `theme_expand.py` treats them differently:
`lemma` rows get a MorphoDiTa paradigm, `literal` rows are copied verbatim (initialisms,
foreign names, fixed idiom forms).

Table format: TSV with a header line, columns

    word  kind  category  hook  score  gloss  evidence

`kind`      lemma | literal
`hook`      IDIOM | OBRAZ | CISLO | MISTO | TVAR | OBOR   (CLUES.md sec. 8; OBOR = the
            entry is domain vocabulary of the publication's subject)
`score`     integer, the Preferred-tier score
`evidence`  regex matched case-insensitively against the concatenated corpus; empty
            means `\\b<word>\\b`
"""

import argparse
import glob
import re
import sys
import unicodedata
from pathlib import Path

KINDS = {"lemma", "literal"}
HOOKS = {"IDIOM", "OBRAZ", "CISLO", "MISTO", "TVAR", "OBOR"}


def load_corpus(patterns):
    paths = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern, recursive=True))
        if not matched:
            sys.exit(f"corpus pattern matched nothing: {pattern}")
        paths.extend(matched)
    text = "\n".join(Path(p).read_text(encoding="utf-8") for p in paths)
    return unicodedata.normalize("NFC", text), len(paths)


def load_table(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        header = None
        for lineno, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split("\t")
            if header is None:
                header = [f.strip() for f in fields]
                expected = ["word", "kind", "category", "hook", "score", "gloss", "evidence"]
                if header != expected:
                    sys.exit(f"{path}:{lineno}: header must be {expected}, got {header}")
                continue
            if len(fields) != 7:
                sys.exit(f"{path}:{lineno}: expected 7 tab-separated fields, got {len(fields)}")
            word, kind, category, hook, score, gloss, evidence = (f.strip() for f in fields)
            rows.append(
                {
                    "lineno": lineno,
                    "word": unicodedata.normalize("NFC", word.casefold()),
                    "kind": kind,
                    "category": category,
                    "hook": hook,
                    "score": score,
                    "gloss": gloss,
                    "evidence": evidence,
                }
            )
    if header is None:
        sys.exit(f"{path}: no header line")
    return rows


def validate(rows, corpus, min_length, max_length):
    failures = []
    seen = {}
    for row in rows:
        word, lineno = row["word"], row["lineno"]
        if word in seen:
            failures.append(f"line {lineno}: duplicate word {word!r} (first at line {seen[word]})")
        seen[word] = lineno
        if row["kind"] not in KINDS:
            failures.append(f"line {lineno}: kind {row['kind']!r} not in {sorted(KINDS)}")
        if row["hook"] not in HOOKS:
            failures.append(f"line {lineno}: hook {row['hook']!r} not in {sorted(HOOKS)}")
        if not re.fullmatch(r"[^\W\d_]+", word, re.UNICODE):
            failures.append(f"line {lineno}: {word!r} is not a single alphabetic token")
        if not min_length <= len(word) <= max_length:
            failures.append(f"line {lineno}: {word!r} length {len(word)} outside [{min_length},{max_length}]")
        try:
            row["score"] = int(row["score"])
        except ValueError:
            failures.append(f"line {lineno}: score {row['score']!r} is not an integer")
            row["score"] = 0
        if not row["gloss"]:
            failures.append(f"line {lineno}: empty gloss for {word!r}")
        pattern = row["evidence"] or rf"\b{re.escape(word)}\b"
        try:
            matches = len(re.findall(pattern, corpus, re.IGNORECASE))
        except re.error as exc:
            failures.append(f"line {lineno}: bad evidence regex for {word!r}: {exc}")
            matches = 0
        row["pattern"] = pattern
        row["matches"] = matches
        if matches == 0:
            failures.append(f"line {lineno}: NO EVIDENCE for {word!r} — pattern {pattern!r} never matches the corpus")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", required=True, help="hand-authored TSV")
    parser.add_argument("--corpus", required=True, nargs="+", help="corpus glob(s); every row must be attested here")
    parser.add_argument("--output-lemmas", required=True)
    parser.add_argument("--output-literals", required=True)
    parser.add_argument("--report", help="CSV audit of every row")
    parser.add_argument("--min-length", type=int, default=3)
    parser.add_argument("--max-length", type=int, default=15)
    args = parser.parse_args()

    corpus, file_count = load_corpus(args.corpus)
    rows = load_table(args.table)
    failures = validate(rows, corpus, args.min_length, args.max_length)
    if failures:
        print(f"BUILD FAILED — {len(failures)} problem(s) in {args.table}:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)

    for kind, path in (("lemma", args.output_lemmas), ("literal", args.output_literals)):
        selected = sorted((r for r in rows if r["kind"] == kind), key=lambda r: (-r["score"], r["word"]))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("".join(f"{r['word']};{r['score']}\n" for r in selected), encoding="utf-8")
        print(f"{path}: {len(selected)} {kind} entries")

    if args.report:
        import csv

        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["word", "length", "kind", "category", "hook", "score", "matches", "gloss", "evidence"])
            for row in sorted(rows, key=lambda r: (r["category"], r["word"])):
                writer.writerow(
                    [
                        row["word"],
                        len(row["word"]),
                        row["kind"],
                        row["category"],
                        row["hook"],
                        row["score"],
                        row["matches"],
                        row["gloss"],
                        row["pattern"],
                    ]
                )
        print(f"{args.report}: {len(rows)} rows")

    print(f"attested against {file_count} corpus file(s), {len(corpus)} characters")


if __name__ == "__main__":
    main()
