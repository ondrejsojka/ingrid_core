#!/usr/bin/env python3
"""Kontrolor over a (fill, clue set) pair — CLUES.md §11, krok 3.

Deterministic, no model. Grades a completed fill grid against its clue TSV
and reports each check with PASS/FAIL plus the offending list:

  1. clue length        median <= 15, max <= 34 characters (histogram printed)
  2. leaked root        answer's root in its own clue, two signals reported
                        separately: MorphoDiTa lemmas (``--model`` — a MorfFlex
                        ``.dict`` is enough, a ``.tagger`` adds its tokenizer)
                        and a crude stem (first 4 chars, diacritics folded).
                        The stem catches what lemmatization cannot connect —
                        `vlevo` for LEVOBOKU, `ještě` for EŠTĚ — so a lemma
                        silence with a stem hit reads as a stem false
                        positive, not a real leak.
  3. band mix           S 45-50 %, O 15-25 %, H 30-35 % (§9) over the clued
                        entries
  4. fair crossing      no clued entry with ALL its crossings in band H
                        (§9; crossing graph from fill geometry — across x down
                        entries sharing a cell)
  5. shape dispersion   <= 1 crossing with the same non-default shape per
                        entry (§9; `nominální` is the default and exempt)
  6. coverage           every TSV answer exists in the fill and every fill
                        entry has exactly one TSV row (missing/extra/extra
                        row, both ways)
  7. duplicates         duplicate clues, exact and up to case/punctuation

Rows whose clue is `-` are declared fill defects (§11 krok 4): excluded from
the ratios, counted, and listed. Exit 0 on a clean set, 1 on hard violations.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

MIN_LEN = 3  # shortest entry, per Czech 15x15 convention (see fill_critic.py)

MEDIAN_CAP = 15
MAX_CAP = 34
BAND_TARGET = {"S": (0.45, 0.50), "O": (0.15, 0.25), "H": (0.30, 0.35)}
DEFAULT_SHAPE = "nominální"
BANDS = ("S", "O", "H")

DEFAULT_MODEL = "/tmp/czech-morphodita/czech-morfflex2.1-pdtc2.0-250909/czech-morfflex2.1-250909.dict"

REPORT_FIELDS = [
    "answer", "clue", "band", "shape", "length",
    "leaked_root_lemma", "leaked_root_stem",
    "crossings", "h_crossings", "shape_collisions",
]

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


# ---------------------------------------------------------------- fill

def load_grid(path: Path) -> list[str]:
    rows = [line.rstrip("\n") for line in path.open(encoding="utf-8") if line.strip()]
    if not rows:
        raise SystemExit(f"{path}: empty grid")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise SystemExit(f"{path}: ragged grid ({rows[0]!r} is {width} chars)")
    return rows


def parse_slots(template: list[str], min_len: int = MIN_LEN) -> list[tuple]:
    """White runs of length >= min_len: (direction, row, col, length)."""
    out = []
    rows, cols = len(template), len(template[0])
    for r in range(rows):
        c = 0
        while c < cols:
            if template[r][c] != "#" and c + 1 < cols and template[r][c + 1] != "#":
                c0 = c
                while c < cols and template[r][c] != "#":
                    c += 1
                if c - c0 >= min_len:
                    out.append(("A", r, c0, c - c0))
            else:
                c += 1
    for c in range(cols):
        r = 0
        while r < rows:
            if template[r][c] != "#" and r + 1 < rows and template[r + 1][c] != "#":
                r0 = r
                while r < rows and template[r][c] != "#":
                    r += 1
                if r - r0 >= min_len:
                    out.append(("D", r0, c, r - r0))
            else:
                r += 1
    return out


def fill_entries(grid: list[str]) -> dict[str, set[tuple[int, int]]]:
    """answer text -> its cells, for every slot of the fill."""
    entries = {}
    for direction, r0, c0, length in parse_slots(grid):
        cells = (
            [(r, c0) for r in range(r0, r0 + length)]
            if direction == "D"
            else [(r0, c) for c in range(c0, c0 + length)]
        )
        text = "".join(grid[r][c] for r, c in cells)
        if "#" in text:
            raise SystemExit(f"unfinished fill: slot {direction}@{r0},{c0} reads {text!r}")
        if text in entries:
            raise SystemExit(f"duplicate entry {text!r} in fill")
        entries[text] = set(cells)
    return entries


def load_clues(path: Path) -> list[dict]:
    """answer/clue/band/shape rows; `#` comment lines are skipped."""
    rows = []
    for lineno, line in enumerate(path.open(encoding="utf-8"), 1):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise SystemExit(
                f"{path}:{lineno}: expected 4 tab-separated fields, "
                f"got {len(fields)}: {line!r}"
            )
        answer, clue, band, shape = (f.strip() for f in fields)
        rows.append({
            "lineno": lineno,
            "answer": answer,
            "clue": clue,
            "band": band,
            "shape": shape or DEFAULT_SHAPE,
            "defect": clue == "-",
        })
    if not rows:
        raise SystemExit(f"{path}: no clue rows")
    return rows


# ------------------------------------------------------- morphology

def fold(text: str) -> str:
    """Lowercase, diacritics stripped (`EŠTĚ` -> `este`, `ještě` -> `jeste`)."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(ch) != "Mn"
    )


def stem(text: str, n: int = 4) -> str:
    return fold(text)[:n]


def lemma_base(lemma: str) -> str:
    """Strip MorfFlex decoration: `žabovřesky_^(*1ý)` -> `žabovřesky`."""
    return lemma.split("_", 1)[0].lower()


class Lemmatizer:
    """Clue words / answer forms -> MorfFlex lemma sets.

    Accepts bare MorfFlex (``.dict``) or the MorphoDiTa tagger (``.tagger``);
    the tagger additionally tokenizes clue text. The guesser is off in both
    modes — a kontrolor must never guess — and unrecognized ``X@`` echoes
    are dropped. Surface forms are tried as-is, capitalized, and lowercased,
    so lowercase fill answers and capitalized clue words both reach their
    paradigms.
    """

    def __init__(self, model_path: str) -> None:
        from ufal.morphodita import (
            Forms,
            Morpho,
            TaggedLemmas,
            Tagger,
            TokenRanges,
        )

        self._Morpho = Morpho
        self._Forms, self._TokenRanges = Forms, TokenRanges
        self._tagged = TaggedLemmas()
        self._morpho = Morpho.load(model_path)
        self._tagger = None
        self._tokenizer = None
        if self._morpho is None:
            self._tagger = Tagger.load(model_path)
            if self._tagger is None:
                raise SystemExit(f"could not load MorphoDiTa model: {model_path}")
            self._tokenizer = self._tagger.newTokenizer()
            if self._tokenizer is None:
                raise SystemExit("the tagger model carries no tokenizer")
        self._cache: dict[str, set[str]] = {}

    def _analyze(self, variant: str) -> set[str]:
        morpho = self._morpho if self._morpho is not None else self._tagger.getMorpho()
        lemmas = set()
        morpho.analyze(variant, self._Morpho.NO_GUESSER, self._tagged)
        for item in self._tagged:
            if not item.tag.startswith("X@"):
                lemmas.add(lemma_base(item.lemma))
        return lemmas

    def lemmas(self, word: str) -> set[str]:
        if word in self._cache:
            return self._cache[word]
        lemmas: set[str] = set()
        for variant in {word, word.capitalize(), word.lower()}:
            lemmas |= self._analyze(variant)
        self._cache[word] = lemmas
        return lemmas

    def tag_tokens(self, text: str) -> list[tuple[str, set[str]]]:
        """(word, lemma set) pairs for every word of the clue, in order."""
        if self._tokenizer is None:
            return [(w, self.lemmas(w)) for w in WORD_RE.findall(text)]
        forms, ranges = self._Forms(), self._TokenRanges()
        self._tokenizer.setText(text)
        words = []
        while self._tokenizer.nextSentence(forms, ranges):
            words.extend(forms.get(i) for i in range(forms.size()))
        return [(w, self.lemmas(w)) for w in words]


def leaked_roots(row: dict, lemm) -> tuple[list[str], list[str]]:
    """(lemma hits, stem hits) between the answer and its clue words.

    Lemma signal is MorfFlex both ways; the stem signal is the folded
    first-4-chars prefix, which connects what lemmatization cannot:
    `vlevo` ~ LEVOBOKU (answer stem hidden inside a prefix word), `ještě`
    ~ EŠTĚ (the answer is the stem of a longer clue word). Reported
    separately so a lone stem hit reads as a likely false positive.
    """
    lemma_hits: list[str] = []
    stem_hits: list[str] = []
    if lemm is not None:
        pairs = lemm.tag_tokens(row["clue"])
        a_lemmas = lemm.lemmas(row["answer"])
        for word, lemmas in pairs:
            shared = a_lemmas & lemmas
            if shared:
                lemma_hits.append(f"{word}~{sorted(shared)[0]}")
    else:
        pairs = [(w, set()) for w in WORD_RE.findall(row["clue"])]
    a_folded = fold(row["answer"])
    a_stem = a_folded[:4]
    for word, _ in pairs:
        w_folded = fold(word)
        if len(a_stem) == 4 and a_stem in w_folded:
            # the answer's 4-char stem sitting inside the clue word
            stem_hits.append(f"{word}~{a_stem}")
        elif len(a_folded) >= 3 and len(w_folded) > len(a_folded) and w_folded.startswith(a_folded):
            # the whole answer is the stem of a longer clue word (EŠTĚ ~ ještě)
            stem_hits.append(f"{word}~{a_folded}")
    return lemma_hits, stem_hits


def normalized_clue(clue: str) -> str:
    """Case- and punctuation-free form for the duplicate test."""
    return "".join(ch for ch in clue.lower() if ch.isalnum())


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fill", type=Path, required=True,
                        help="finished fill grid: one row per line, '#' = block")
    parser.add_argument("--clues", type=Path, required=True,
                        help="clue TSV: answer<TAB>clue<TAB>band<TAB>shape; "
                             "clue '-' marks a declared fill defect")
    parser.add_argument("--model", metavar="MODEL", default=DEFAULT_MODEL,
                        help="MorfFlex .dict or MorphoDiTa .tagger for the "
                             f"lemma signal (default: {DEFAULT_MODEL})")
    parser.add_argument("--report", type=Path,
                        help="write a per-entry CSV report here")
    args = parser.parse_args(argv)

    grid = load_grid(args.fill)
    entries = fill_entries(grid)
    rows = load_clues(args.clues)

    lemm = Lemmatizer(args.model) if args.model else None

    by_answer: dict[str, dict] = {}
    answer_dupes = []
    for row in rows:
        if row["answer"] in by_answer:
            answer_dupes.append(row)
        by_answer[row["answer"]] = row
    clued = [r for r in rows if not r["defect"]]
    defects = [r for r in rows if r["defect"]]

    # Crossing graph from fill geometry: entries sharing a cell.
    crossings = {
        a: sorted(b for b in entries if b != a and entries[a] & entries[b])
        for a in entries
    }

    fails: list[str] = []

    print(f"fill:    {args.fill} ({len(entries)} entries)")
    print(f"clues:   {args.clues} ({len(rows)} rows, {len(clued)} clued, "
          f"{len(defects)} declared fill defects)")
    if defects:
        print(f"defects: {', '.join(r['answer'] for r in defects)}")
    print(f"model:   {args.model if lemm else '(lemma signal skipped)'}")

    # ---- 1. clue length -------------------------------------------------
    lengths = {r["answer"]: len(r["clue"]) for r in clued}
    med = statistics.median(lengths.values())
    mx = max(lengths.values())
    ok = med <= MEDIAN_CAP and mx <= MAX_CAP
    print(f"\n== 1. clue length == med {med:g} (cap {MEDIAN_CAP}), "
          f"max {mx} (cap {MAX_CAP}) -> {'PASS' if ok else 'FAIL'}")
    buckets = Counter((max(L, 1) - 1) // 5 for L in lengths.values())
    for b in sorted(buckets):
        lo, hi = b * 5 + 1, b * 5 + 5
        print(f"  {lo:2d}-{hi:2d}: {'*' * buckets[b]} ({buckets[b]})")
    if not ok:
        bad = sorted(a for a, L in lengths.items() if L > MAX_CAP)
        print(f"  offenders: {', '.join(bad) if bad else '(median over cap)'}")
        fails.append(f"clue length med {med:g} max {mx}")

    # ---- 2. leaked root -------------------------------------------------
    leak_lemma, leak_stem = {}, {}
    for r in clued:
        lh, sh = leaked_roots(r, lemm)
        if lh:
            leak_lemma[r["answer"]] = lh
        if sh:
            leak_stem[r["answer"]] = sh
    ok = not leak_lemma and not leak_stem
    print(f"\n== 2. leaked root == lemma {len(leak_lemma)}, stem {len(leak_stem)} "
          f"-> {'PASS' if ok else 'FAIL'}")
    for answer, hits in sorted(leak_lemma.items()):
        print(f"  lemma {answer}: {', '.join(hits)}")
    for answer, hits in sorted(leak_stem.items()):
        print(f"  stem  {answer}: {', '.join(hits)}")
    if lemm is None:
        print("  note: no --model, lemma signal skipped (stem signal only)")
    if not ok:
        fails.append(f"leaked roots: {sorted(leak_lemma) + sorted(leak_stem)}")

    # ---- 3. band mix ----------------------------------------------------
    tally = Counter(r["band"] for r in clued)
    bad_bands = sorted({r["band"] for r in clued if r["band"] not in BANDS})
    n = len(clued)
    band_fail = list(bad_bands)
    print(f"\n== 3. band mix == over {n} clued entries")
    for band in BANDS:
        share = tally[band] / n if n else 0.0
        lo, hi = BAND_TARGET[band]
        in_range = lo <= share <= hi
        if not in_range:
            band_fail.append(band)
        print(f"  {band}: {tally[band]:3d} = {share * 100:4.1f}%  "
              f"target {lo * 100:.0f}-{hi * 100:.0f}%  "
              f"{'ok' if in_range else 'OUT'}")
    if bad_bands:
        print(f"  unknown band values: {bad_bands}")
    ok = not band_fail
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        fails.append(f"band mix out of target: {band_fail}")

    # ---- 4. fair crossing ----------------------------------------------
    # Crossings are counted over clued entries; a defect carries no band and
    # can neither fence in nor rescue a neighbour.
    band_of = {r["answer"]: r["band"] for r in clued}
    clued_set = set(band_of)
    fenced = []
    h_crossings = {}
    for answer in sorted(clued_set):
        xs = [b for b in crossings[answer] if b in clued_set]
        h_crossings[answer] = sum(1 for b in xs if band_of[b] == "H")
        if xs and h_crossings[answer] == len(xs):
            fenced.append((answer, xs))
    ok = not fenced
    print(f"\n== 4. fair crossing == entries with all crossings in H: "
          f"{len(fenced)} -> {'PASS' if ok else 'FAIL'}")
    for answer, xs in fenced:
        print(f"  {answer}: crossings "
              + ", ".join(f"{x}({band_of[x]})" for x in xs))
    if not ok:
        fails.append(f"all-H crossings: {sorted(a for a, _ in fenced)}")

    # ---- 5. shape dispersion ---------------------------------------------
    shape_of = {r["answer"]: r["shape"] for r in clued}
    shape_collisions = {}
    over = []
    for answer in sorted(clued_set):
        same = sorted(
            b for b in crossings[answer]
            if b in clued_set
            and shape_of[b] == shape_of[answer]
            and shape_of[answer] != DEFAULT_SHAPE
        )
        shape_collisions[answer] = same
        if len(same) > 1:
            over.append((answer, shape_of[answer], same))
    ok = not over
    print(f"\n== 5. shape dispersion == entries with >1 same-shape crossing "
          f"(non-default): {len(over)} -> {'PASS' if ok else 'FAIL'}")
    for answer, shape, same in over:
        print(f"  {answer} [{shape}]: {', '.join(same)}")
    if not ok:
        fails.append(f"shape collisions >1: {sorted(a for a, _, _ in over)}")

    # ---- 6. coverage ------------------------------------------------------
    missing_from_fill = [r for r in rows if r["answer"] not in entries]
    missing_from_tsv = sorted(a for a in entries if a not in by_answer)
    ok = not missing_from_fill and not missing_from_tsv and not answer_dupes
    print(f"\n== 6. coverage == {len(rows)} TSV rows vs {len(entries)} fill entries "
          f"-> {'PASS' if ok else 'FAIL'}")
    for r in missing_from_fill:
        print(f"  TSV answer not in fill: {r['answer']} (line {r['lineno']})")
    for a in missing_from_tsv:
        print(f"  fill entry without TSV row: {a}")
    for r in answer_dupes:
        print(f"  duplicate TSV row for {r['answer']} (line {r['lineno']})")
    if not ok:
        fails.append("coverage mismatch")

    # ---- 7. duplicate clues ------------------------------------------------
    exact = defaultdict(list)
    normal = defaultdict(list)
    for r in clued:
        exact[r["clue"]].append(r["answer"])
        normal[normalized_clue(r["clue"])].append(r)
    exact_dupes = {k: v for k, v in exact.items() if len(v) > 1}
    normal_dupes = {
        k: v for k, v in normal.items() if len({r["clue"] for r in v}) > 1
    }
    ok = not exact_dupes and not normal_dupes
    print(f"\n== 7. duplicate clues == exact {len(exact_dupes)}, "
          f"up-to-case/punct {len(normal_dupes)} -> {'PASS' if ok else 'FAIL'}")
    for clue, answers in sorted(exact_dupes.items()):
        print(f"  exact: {clue!r} -> {', '.join(answers)}")
    for rs in sorted(normal_dupes.values(), key=lambda v: v[0]["clue"]):
        print("  same up to case/punct: "
              + "; ".join(f"{r['clue']!r} -> {r['answer']}" for r in rs))
    if not ok:
        fails.append("duplicate clues")

    # ---------------------------------------------------------------- report
    if args.report:
        report_rows = []
        for r in rows:
            a = r["answer"]
            clued_x = [b for b in crossings.get(a, []) if b in clued_set]
            if r["defect"]:
                lh, sh = [], []
            else:
                lh, sh = leaked_roots(r, lemm)
            report_rows.append({
                "answer": a,
                "clue": r["clue"],
                "band": r["band"],
                "shape": "" if r["defect"] else r["shape"],
                "length": "" if r["defect"] else len(r["clue"]),
                "leaked_root_lemma": ";".join(lh),
                "leaked_root_stem": ";".join(sh),
                "crossings": len(clued_x),
                "h_crossings": h_crossings.get(a, ""),
                "shape_collisions": ";".join(shape_collisions.get(a, [])),
            })
        with args.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(report_rows)
        print(f"\nwrote {args.report} ({len(report_rows)} rows)")

    print(f"\n== verdict == {'PASS' if not fails else 'FAIL: ' + '; '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
