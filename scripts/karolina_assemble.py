#!/usr/bin/env python3
"""Turn a finished švédská grid into the delivery payload.

Inputs: the grid as `ingrid_core` prints it, a theme TSV (`word<TAB>clue`), and an
optional clue TSV for the standard-tier glue words. Output is one JSON document that
both the interactive renderer and the e-mail renderer read, plus a `clues.tsv` in the
shape `send_crossword_email.py` expects.

The tajenka is chosen *after* the fill: shaded cells read in grid order spell the
message. Selection is a DP over cells that maximizes the spacing between consecutive
shaded cells, so the message is spread over the whole grid instead of clumping.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata

from check_grid import check, runs


def load_clues(path):
    out = {}
    if not path:
        return out
    for ln in open(path, encoding="utf-8"):
        ln = ln.rstrip("\n")
        if not ln.strip():
            continue
        parts = ln.split("\t") if "\t" in ln else ln.split(" ", 1)
        word = parts[0].strip().lower()
        clue = parts[1].strip() if len(parts) > 1 else ""
        out.setdefault(word, []).append(clue)
    return {k: " / ".join(dict.fromkeys(v)) for k, v in out.items()}


def strip_diacritics(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def pick_tajenka(rows, message, occupied_ok=None):
    """Shaded cells in reading order spelling `message`; maximize minimum spacing.

    DP over (message position, cell index): value = the spread score of the best
    prefix. Comparing on total spacing keeps the shaded cells from clumping.
    """
    h, w = len(rows), len(rows[0])
    cells = [(r, c) for r in range(h) for c in range(w) if rows[r][c] != "#"]
    letters = [strip_diacritics(rows[r][c]) for r, c in cells]
    target = [ch for ch in strip_diacritics(message) if ch.isalpha()]
    n, m = len(cells), len(target)
    if m == 0:
        return []
    NEG = float("-inf")
    # best[j] = (score, prev_index, cell_index) for message position j at each cell
    best = [[NEG] * n for _ in range(m)]
    prev = [[-1] * n for _ in range(m)]
    for i in range(n):
        if letters[i] == target[0]:
            best[0][i] = 0.0
    for j in range(1, m):
        run_best, run_arg = NEG, -1
        for i in range(n):
            if i > 0:
                if best[j - 1][i - 1] > run_best:
                    run_best, run_arg = best[j - 1][i - 1], i - 1
            if letters[i] == target[j] and run_arg >= 0:
                best[j][i] = run_best + min(i - run_arg, 12)
                prev[j][i] = run_arg
    end = max(range(n), key=lambda i: best[m - 1][i])
    if best[m - 1][end] == NEG:
        return None
    out = []
    j, i = m - 1, end
    while j >= 0:
        out.append(cells[i])
        i = prev[j][i]
        j -= 1
    out.reverse()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--theme", required=True)
    ap.add_argument("--glue-clues")
    ap.add_argument("--bands", action="append", default=[],
                    help="TSV word<TAB>band<TAB>shape, or word<TAB>clue<TAB>band<TAB>shape")
    ap.add_argument("--title", default="Křížovka")
    ap.add_argument("--tajenka", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--clues-out")
    args = ap.parse_args()

    rows = [ln.rstrip("\n") for ln in open(args.grid, encoding="utf-8") if ln.strip()]
    bad, rs = check(rows)
    if bad:
        for b in bad:
            print("FAIL:", b, file=sys.stderr)
        sys.exit(1)

    theme = load_clues(args.theme)
    glue = load_clues(args.glue_clues)

    bands = {}
    for path in args.bands:
        for ln in open(path, encoding="utf-8"):
            parts = ln.rstrip("\n").split("\t")
            if len(parts) == 3:
                bands[parts[0].strip().lower()] = (parts[1].strip(), parts[2].strip())
            elif len(parts) >= 4:
                bands[parts[0].strip().lower()] = (parts[2].strip(), parts[3].strip())

    entries = []
    missing = []
    for i, (d, r, c, text) in enumerate(sorted(rs, key=lambda t: (t[1], t[2], t[0])), 1):
        is_theme = text in theme
        clue = theme.get(text) or glue.get(text) or ""
        if not clue:
            missing.append(text)
        entries.append({
            "id": i, "dir": d, "r": r, "c": c, "len": len(text),
            "answer": text, "clue": clue, "theme": is_theme,
            "band": bands.get(text, ("H" if is_theme else "S", ""))[0],
            "shape": bands.get(text, ("", "nominální" if is_theme else "nominální"))[1],
            "legend": [r, c - 1] if d == "A" else [r - 1, c],
        })

    taj = None
    if args.tajenka:
        cells = pick_tajenka(rows, args.tajenka)
        if cells is None:
            print(f"WARN: tajenka {args.tajenka!r} is not a subsequence of the fill",
                  file=sys.stderr)
        else:
            taj = {"text": args.tajenka, "cells": [list(x) for x in cells]}

    # Entries declared fill defects (clue "-") are written into the grid instead of
    # being asked: the solver never sees a legend for them, and their letters are there
    # from the start. Cheaper than shipping a word nobody can honestly clue.
    prefill, prefilled_answers = [], []
    for e in entries:
        if e["clue"] in ("", "-"):
            prefilled_answers.append(e["answer"])
            e["prefilled"] = True
            for i in range(e["len"]):
                cell = [e["r"], e["c"] + i] if e["dir"] == "A" else [e["r"] + i, e["c"]]
                if cell not in prefill:
                    prefill.append(cell)

    payload = {
        "prefill": prefill,
        "prefilled_answers": prefilled_answers,
        "title": args.title,
        "size": [len(rows), len(rows[0])],
        "grid": rows,
        "entries": entries,
        "tajenka": taj,
        "stats": {
            "answers": len(entries),
            "theme": sum(1 for e in entries if e["theme"]),
            "glue": sum(1 for e in entries if not e["theme"]),
            "max_clue": max((len(e["clue"]) for e in entries), default=0),
            "median_clue": sorted(len(e["clue"]) for e in entries)[len(entries) // 2],
        },
    }
    json.dump(payload, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if args.clues_out:
        with open(args.clues_out, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(f"{e['answer']}\t{e['clue']}\t{e['band']}\t{e['shape']}\n")
    print(json.dumps(payload["stats"], ensure_ascii=False))
    if missing:
        print("no clue for:", " ".join(sorted(set(missing))), file=sys.stderr)


if __name__ == "__main__":
    main()
