#!/usr/bin/env python3
"""Theme-driven švédská TEMPLATE constructor.

`ingrid_core` fills a template; it cannot choose one. When the theme vocabulary is tiny
(a personal list of ~80 words) an off-the-shelf template gives the solver almost nothing
to hit, so the theme count has to be bought in the *template*: this script lays the theme
words out as an interlocking skeleton with their letters prefilled, opens a bounded number
of empty "glue" runs that ingrid resolves from the standard tier, and emits an ordinary
ingrid grid file (`#` block, `.` empty, letters fixed).

Grid legality (classic criss-cross, so every maximal run of >= 2 white cells is exactly
one intended answer):
  * a word occupies a maximal run — the cells immediately before and after are blocks,
  * a cell is either a crossing with a perpendicular word, or its two perpendicular
    neighbours are blocks,
  * every word after the first crosses an already placed word (single component).

Švédská legality: an across word at (r, c) takes its legend from (r, c-1), a down word at
(r, c) from (r-1, c). Both are blocks by construction and two words never share a legend
cell in the same direction, so the only extra rule is that no word starts in row 0 or
column 0 — enforced here, and re-checked by the renderer.

Glue runs are screened with the same unary filter the solver applies first (does any
standard-tier word of that length match the induced pattern), which is what keeps
`ingrid_core` from being handed a template that dies at initial arc consistency.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import sys
import time
from multiprocessing import Pool

BLOCK = None
WILD = "."


# --------------------------------------------------------------------------- dictionary

class LengthIndex:
    """Bitset index over one length class: `matches(pattern)` counts candidates."""

    __slots__ = ("n", "bits", "length")

    def __init__(self, words, length):
        import numpy as np

        self.length = length
        self.n = len(words)
        nb = (self.n + 63) // 64
        self.bits = {}
        cols = [collections.defaultdict(list) for _ in range(length)]
        for i, w in enumerate(words):
            for p, ch in enumerate(w):
                cols[p][ch].append(i)
        for p in range(length):
            for ch, idxs in cols[p].items():
                arr = np.zeros(nb * 64, dtype=bool)
                arr[idxs] = True
                self.bits[(p, ch)] = np.packbits(arr)

    def any_match(self, pattern):
        import numpy as np

        acc = None
        for p, ch in enumerate(pattern):
            if ch == WILD:
                continue
            b = self.bits.get((p, ch))
            if b is None:
                return False
            acc = b if acc is None else np.bitwise_and(acc, b)
            if not acc.any():
                return False
        return True if acc is None else bool(acc.any())


def load_dict(paths, min_score, max_len):
    by_len = collections.defaultdict(list)
    for path in paths:
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            word, _, score = ln.partition(";")
            if score and float(score) < min_score:
                continue
            word = word.strip().lower()
            if 3 <= len(word) <= max_len:
                by_len[len(word)].append(word)
    return {k: LengthIndex(sorted(set(v)), k) for k, v in by_len.items()}


# ------------------------------------------------------------------------------- grid

class Grid:
    __slots__ = ("h", "w", "cell", "hocc", "vocc", "words", "bylet", "wild", "ncomp")

    def __init__(self, h, w):
        self.h, self.w = h, w
        self.ncomp = 0
        self.cell = [[BLOCK] * w for _ in range(h)]
        self.hocc = [[-1] * w for _ in range(h)]
        self.vocc = [[-1] * w for _ in range(h)]
        self.words = []           # (text|None, r, c, horiz, length)
        self.bylet = collections.defaultdict(set)
        self.wild = set()

    def copy_prefix(self, k):
        g = Grid(self.h, self.w)
        for text, r, c, horiz, length in self.words[:k]:
            g.place(text, r, c, horiz, length)
        return g

    # -- legality ----------------------------------------------------------
    def fits(self, text, r, c, horiz, length=None):
        """Crossing count, or None if illegal. `text=None` means a wildcard glue run."""
        n = length if text is None else len(text)
        h, w, cell = self.h, self.w, self.cell
        if horiz:
            if r < 1 or c < 1 or c + n > w:
                return None
            if cell[r][c - 1] is not BLOCK:
                return None
            if c + n < w and cell[r][c + n] is not BLOCK:
                return None
            cross = 0
            for i in range(n):
                cc = c + i
                cur = cell[r][cc]
                if cur is BLOCK:
                    if (r > 0 and cell[r - 1][cc] is not BLOCK) or \
                       (r + 1 < h and cell[r + 1][cc] is not BLOCK):
                        return None
                else:
                    if self.hocc[r][cc] != -1:
                        return None
                    if text is not None and cur != WILD and cur != text[i]:
                        return None
                    cross += 1
            return cross
        else:
            if c < 1 or r < 1 or r + n > h:
                return None
            if cell[r - 1][c] is not BLOCK:
                return None
            if r + n < h and cell[r + n][c] is not BLOCK:
                return None
            cross = 0
            for i in range(n):
                rr = r + i
                cur = cell[rr][c]
                if cur is BLOCK:
                    if (c > 0 and cell[rr][c - 1] is not BLOCK) or \
                       (c + 1 < w and cell[rr][c + 1] is not BLOCK):
                        return None
                else:
                    if self.vocc[rr][c] != -1:
                        return None
                    if text is not None and cur != WILD and cur != text[i]:
                        return None
                    cross += 1
            return cross

    def place(self, text, r, c, horiz, length=None):
        n = length if text is None else len(text)
        if all(self.cell[r][c + i] is BLOCK if horiz else self.cell[r + i][c] is BLOCK
               for i in range(n)):
            self.ncomp += 1
        idx = len(self.words)
        self.words.append((text, r, c, horiz, n))
        for i in range(n):
            rr, cc = (r, c + i) if horiz else (r + i, c)
            ch = WILD if text is None else text[i]
            cur = self.cell[rr][cc]
            if cur is BLOCK or (cur == WILD and ch != WILD):
                if cur == WILD:
                    self.wild.discard((rr, cc))
                else:
                    pass
                self.cell[rr][cc] = ch
                if ch == WILD:
                    self.wild.add((rr, cc))
                else:
                    self.bylet[ch].add((rr, cc))
            if horiz:
                self.hocc[rr][cc] = idx
            else:
                self.vocc[rr][cc] = idx

    # -- inspection --------------------------------------------------------
    def pattern(self, widx):
        text, r, c, horiz, n = self.words[widx]
        if horiz:
            return "".join(self.cell[r][c + i] for i in range(n))
        return "".join(self.cell[r + i][c] for i in range(n))

    def glue_ok(self, index, touched=None):
        for widx, (text, r, c, horiz, n) in enumerate(self.words):
            if text is not None:
                continue
            if touched is not None and widx not in touched:
                continue
            pat = self.pattern(widx)
            li = index.get(n)
            if li is None or not li.any_match(pat):
                return False
        return True

    def words_touching(self, r, c, horiz, n):
        out = set()
        for i in range(n):
            rr, cc = (r, c + i) if horiz else (r + i, c)
            j = self.vocc[rr][cc] if horiz else self.hocc[rr][cc]
            if j != -1:
                out.add(j)
        return out

    def bbox(self):
        rs = [r for r in range(self.h) if any(x is not BLOCK for x in self.cell[r])]
        cs = [c for c in range(self.w) if any(self.cell[r][c] is not BLOCK for r in range(self.h))]
        if not rs:
            return 0, 0, 0, 0
        return min(rs), min(cs), max(rs), max(cs)

    def render(self):
        r0, c0, r1, c1 = self.bbox()
        r0, c0 = max(0, r0 - 1), max(0, c0 - 1)
        r1, c1 = min(self.h - 1, r1 + 1), min(self.w - 1, c1 + 1)
        if r0 == 0 and r1 - r0 + 1 < 2:
            r1 += 1
        rows = ["".join("#" if self.cell[r][c] is BLOCK else self.cell[r][c]
                        for c in range(c0, c1 + 1)) for r in range(r0, r1 + 1)]
        return rows, (r0, c0)


# ------------------------------------------------------------------------------ search

def theme_placements(g, word):
    """Legal placements of a fixed-letter word that cross something already placed."""
    out = []
    seen = set()
    anchors = collections.defaultdict(set)
    for i, ch in enumerate(word):
        anchors[i] |= g.bylet.get(ch, set())
        anchors[i] |= g.wild
    for i, cells in anchors.items():
        for (r, c) in cells:
            if g.hocc[r][c] == -1:
                key = (r, c - i, True)
                if key not in seen:
                    seen.add(key)
                    n = g.fits(word, r, c - i, True)
                    if n:
                        out.append((n, r, c - i, True))
            if g.vocc[r][c] == -1:
                key = (r - i, c, False)
                if key not in seen:
                    seen.add(key)
                    n = g.fits(word, r - i, c, False)
                    if n:
                        out.append((n, r - i, c, False))
    return out


def glue_placements(g, lengths):
    out = []
    seen = set()
    cells = list(g.bylet_all())
    for (r, c) in cells:
        for n in lengths:
            for i in range(n):
                if g.hocc[r][c] == -1:
                    key = (r, c - i, True, n)
                    if key not in seen:
                        seen.add(key)
                        k = g.fits(None, r, c - i, True, n)
                        if k:
                            out.append((k, r, c - i, True, n))
                if g.vocc[r][c] == -1:
                    key = (r - i, c, False, n)
                    if key not in seen:
                        seen.add(key)
                        k = g.fits(None, r - i, c, False, n)
                        if k:
                            out.append((k, r - i, c, False, n))
    return out


def _bylet_all(self):
    out = set()
    for s in self.bylet.values():
        out |= s
    return out | self.wild


Grid.bylet_all = _bylet_all


def grow(g, pool, index, rng, glue_budget, glue_lengths, greed, cw=10.0, lw=0.0):
    used_glue = sum(1 for t, *_ in g.words if t is None)
    pool = list(pool)
    while True:
        cands = []
        for word in pool:
            for n, r, c, horiz in theme_placements(g, word):
                cands.append((n * cw + len(word) * lw + rng.random(), word, r, c, horiz))
        cands.sort(key=lambda t: -t[0])
        placed = False
        tries = 0
        while cands and tries < 15:
            tries += 1
            k = 1 if rng.random() < greed else min(len(cands), rng.randint(1, 5))
            _, word, r, c, horiz = cands.pop(rng.randrange(k))
            g.place(word, r, c, horiz)
            touched = {len(g.words) - 1} | g.words_touching(r, c, horiz, len(word))
            if g.glue_ok(index, touched):
                pool.remove(word)
                placed = True
                break
            g = g.copy_prefix(len(g.words) - 1)
        if placed:
            continue
        # nothing thematic fits: open one glue run, which may unlock theme words
        if used_glue >= glue_budget:
            return g
        gp = glue_placements(g, glue_lengths)
        rng.shuffle(gp)
        gp.sort(key=lambda t: -t[0])
        opened = False
        for k, r, c, horiz, n in gp[:60]:
            g.place(None, r, c, horiz, n)
            touched = {len(g.words) - 1} | g.words_touching(r, c, horiz, n)
            if g.glue_ok(index, touched):
                used_glue += 1
                opened = True
                break
            g = g.copy_prefix(len(g.words) - 1)
        if not opened:
            return g


def score(g):
    n_theme = sum(1 for t, *_ in g.words if t is not None)
    n_glue = len(g.words) - n_theme
    r0, c0, r1, c1 = g.bbox()
    cross = sum(1 for r in range(g.h) for c in range(g.w)
                if g.hocc[r][c] != -1 and g.vocc[r][c] != -1)
    return (n_theme, -g.ncomp, -n_glue, cross, -((r1 - r0 + 1) * (c1 - c0 + 1)))


def seed_word(g, word, rng):
    """Place `word` with zero crossings, i.e. as a fresh isolated component."""
    spots = []
    n = len(word)
    for horiz in (True, False):
        rmax = g.h if horiz else g.h - n + 1
        cmax = g.w - n + 1 if horiz else g.w
        for r in range(1, rmax):
            for c in range(1, cmax):
                if g.fits(word, r, c, horiz) == 0:
                    spots.append((r, c, horiz))
    if not spots:
        return False
    r, c, horiz = spots[rng.randrange(len(spots))]
    g.place(word, r, c, horiz)
    return True


def build_once(h, w, theme, index, rng, glue_budget, glue_lengths, greed, components=1,
               cw=10.0, lw=0.0):
    g = Grid(h, w)
    longest = sorted(theme, key=len, reverse=True)[:8]
    first = longest[rng.randrange(len(longest))]
    if rng.random() < 0.5:
        r = rng.randint(1, h - 2)
        c = min(max(1, (w - len(first)) // 2 + rng.randint(-3, 3)), w - len(first))
        g.place(first, r, c, True)
    else:
        c = rng.randint(1, w - 2)
        r = min(max(1, (h - len(first)) // 2 + rng.randint(-3, 3)), h - len(first))
        g.place(first, r, c, False)
    rest = [x for x in theme if x != first]
    g = grow(g, rest, index, rng, glue_budget, glue_lengths, greed, cw, lw)
    return extend(g, theme, index, rng, glue_budget, glue_lengths, greed, components, cw, lw)


def extend(g, theme, index, rng, glue_budget, glue_lengths, greed, components,
           cw=10.0, lw=0.0):
    """Seed further isolated components while under the component cap."""
    while g.ncomp < components:
        done = {t for t, *_ in g.words if t is not None}
        left = [x for x in theme if x not in done]
        if not left:
            break
        rng.shuffle(left)
        left.sort(key=len, reverse=True)
        for word in left[:12]:
            if seed_word(g, word, rng):
                g = grow(g, [x for x in left if x != word], index, rng,
                         glue_budget, glue_lengths, greed, cw, lw)
                break
        else:
            break
    return g


def worker(job):
    (seed, h, w, theme, dict_paths, min_score, glue_budget, glue_lengths,
     greed, seconds, components, cw, lw) = job
    rng = random.Random(seed)
    index = load_dict(dict_paths, min_score, max(glue_lengths) if glue_lengths else 3)
    best, best_key, restarts = None, None, 0
    t_end = time.time() + seconds
    while time.time() < t_end:
        restarts += 1
        g = build_once(h, w, theme, index, rng, glue_budget, glue_lengths, greed,
                       components, cw, lw)
        key = score(g)
        if best_key is None or key > best_key:
            best, best_key = g, key
        cur, cur_key = g, key
        for _ in range(80):
            if time.time() > t_end:
                break
            k = len(cur.words)
            if k < 3:
                break
            keep = max(1, k - rng.randint(1, min(9, k - 1)))
            trial = cur.copy_prefix(keep)
            done = {t for t, *_ in trial.words if t is not None}
            trial = grow(trial, [x for x in theme if x not in done], index, rng,
                         glue_budget, glue_lengths, greed, cw, lw)
            trial = extend(trial, theme, index, rng, glue_budget, glue_lengths, greed,
                           components, cw, lw)
            tkey = score(trial)
            if tkey >= cur_key:
                cur, cur_key = trial, tkey
            if cur_key > best_key:
                best, best_key = cur, cur_key
    return best_key, [list(x) for x in best.words], (h, w), restarts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theme", required=True)
    ap.add_argument("--wordlist", action="append", required=True)
    ap.add_argument("--min-score", type=float, default=20)
    ap.add_argument("--size", type=int, nargs=2, default=[19, 19], metavar=("H", "W"))
    ap.add_argument("--glue-budget", type=int, default=12)
    ap.add_argument("--glue-lengths", type=int, nargs="+", default=[3, 4, 5])
    ap.add_argument("--greed", type=float, default=0.5)
    ap.add_argument("--cross-weight", type=float, default=10.0,
                    help="placement bias toward more crossings (negative = fewer)")
    ap.add_argument("--len-weight", type=float, default=0.0,
                    help="placement bias toward longer words first")
    ap.add_argument("--components", type=int, default=1,
                    help="max disconnected clusters (1 = a single interlocked grid)")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fold", action="store_true", help="ablation: strip diacritics")
    ap.add_argument("--out")
    args = ap.parse_args()

    import unicodedata

    def fold(s):
        for a, b in (("ě", "e"), ("ů", "u"), ("ľ", "l"), ("ĺ", "l"),
                     ("ô", "o"), ("ä", "a"), ("ŕ", "r")):
            s = s.replace(a, b)
        return "".join(c for c in unicodedata.normalize("NFD", s)
                       if unicodedata.category(c) != "Mn")

    theme = []
    for ln in open(args.theme, encoding="utf-8"):
        if not ln.strip():
            continue
        wd = (ln.split("\t", 1)[0] if "\t" in ln else ln.split(" ", 1)[0]).strip().lower()
        if args.fold:
            wd = fold(wd)
        if wd and wd not in theme:
            theme.append(wd)

    h, w = args.size
    jobs = [(args.seed * 7919 + i, h, w, theme, args.wordlist, args.min_score,
             args.glue_budget, args.glue_lengths, args.greed, args.seconds,
             args.components, args.cross_weight, args.len_weight)
            for i in range(args.jobs)]
    with Pool(args.jobs) as pool:
        res = pool.map(worker, jobs)
    res.sort(key=lambda t: t[0], reverse=True)
    key, words, size, _ = res[0]
    restarts = sum(r[3] for r in res)

    g = Grid(*size)
    for text, r, c, horiz, n in words:
        g.place(text, r, c, horiz, n)
    rows, (r0, c0) = g.render()
    print(f"theme={key[0]} comps={-key[1]} glue={-key[2]} crossings={key[3]} area={-key[4]} "
          f"restarts={restarts} grid={len(rows)}x{len(rows[0])}", file=sys.stderr)
    print("\n".join(rows))
    if args.out:
        json.dump({"rows": rows,
                   "theme_count": key[0], "components": -key[1],
                   "glue_count": -key[2], "crossings": key[3],
                   "words": [[t, r - r0, c - c0, ho, n] for t, r, c, ho, n in words]},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
