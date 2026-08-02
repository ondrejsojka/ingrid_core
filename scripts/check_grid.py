#!/usr/bin/env python3
"""Validate a švédská crossword grid: run legality, connectivity, legend legality.

Exits non-zero and prints every violation. Takes the grid as `ingrid_core` prints it
(`#` block, letters elsewhere; `.` is treated as an unresolved cell and reported).
"""

from __future__ import annotations

import argparse
import sys

from pin_long import cells_of, slots


def runs(rows):
    """(direction, r, c, text) for every maximal run of >= 2 non-block cells."""
    return [
        (slot[0], slot[1], slot[2], "".join(rows[r][c] for r, c in cells_of(slot)))
        for slot in slots(rows, min_run=2)
    ]


def check(rows, dict_words=None, allow_unclued=()):
    h, w = len(rows), len(rows[0])
    bad = []
    if any(len(r) != w for r in rows):
        bad.append("rows are not all the same width")
        return bad, []
    rs = runs(rows)
    for d, r, c, text in rs:
        if len(text) == 2:
            bad.append(f"two-letter run {d} at ({r},{c}) {text!r}: the renderer drops it")
        if "." in text:
            bad.append(f"unresolved cell in {d} run at ({r},{c}) {text!r}")
        if d == "A" and c == 0:
            bad.append(f"across word {text!r} starts in column 0 — legend cell off-grid")
        if d == "D" and r == 0:
            bad.append(f"down word {text!r} starts in row 0 — legend cell off-grid")
        if d == "A" and c > 0 and rows[r][c - 1] != "#":
            bad.append(f"legend cell for across {text!r} at ({r},{c-1}) is not a block")
        if d == "D" and r > 0 and rows[r - 1][c] != "#":
            bad.append(f"legend cell for down {text!r} at ({r-1},{c}) is not a block")

    # isolated white cells: in a word-free cell nothing can be clued
    covered = set()
    for d, r, c, text in rs:
        for i in range(len(text)):
            covered.add((r, c + i) if d == "A" else (r + i, c))
    for r in range(h):
        for c in range(w):
            if rows[r][c] != "#" and (r, c) not in covered and (r, c) not in allow_unclued:
                bad.append(f"orphan cell ({r},{c})={rows[r][c]!r} belongs to no answer")

    # legend load: at most one across + one down legend per block
    load = {}
    for d, r, c, text in rs:
        cellrc = (r, c - 1) if d == "A" else (r - 1, c)
        key = (cellrc, d)
        if key in load:
            bad.append(f"legend cell {cellrc} carries two {d} legends: {load[key]!r} / {text!r}")
        load[key] = text

    # connectivity of the white area
    white = [(r, c) for r in range(h) for c in range(w) if rows[r][c] != "#"]
    if white:
        seen = {white[0]}
        stack = [white[0]]
        while stack:
            r, c = stack.pop()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p = (r + dr, c + dc)
                if 0 <= p[0] < h and 0 <= p[1] < w and rows[p[0]][p[1]] != "#" and p not in seen:
                    seen.add(p)
                    stack.append(p)
        if len(seen) != len(white):
            bad.append(f"white area is not connected: {len(seen)} of {len(white)} cells reachable")

    if dict_words is not None:
        for d, r, c, text in rs:
            if text not in dict_words:
                bad.append(f"{d} answer {text!r} at ({r},{c}) is not in the supplied wordlist")
    return bad, rs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("grid")
    ap.add_argument("--wordlist", action="append", default=[])
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    rows = [ln.rstrip("\n") for ln in open(args.grid, encoding="utf-8") if ln.strip()]
    words = None
    if args.wordlist:
        words = set()
        for p in args.wordlist:
            for ln in open(p, encoding="utf-8"):
                ln = ln.strip()
                if ln:
                    words.add(ln.split(";")[0].strip().lower())
    bad, rs = check(rows, words)
    n_a = sum(1 for d, *_ in rs if d == "A")
    if not args.quiet:
        print(f"{len(rows)}x{len(rows[0])}  answers={len(rs)} (across {n_a}, down {len(rs)-n_a})")
        lens = sorted(len(t) for *_, t in rs)
        print("lengths:", lens)
    for b in bad:
        print("FAIL:", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
