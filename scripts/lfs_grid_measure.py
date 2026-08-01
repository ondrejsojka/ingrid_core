#!/usr/bin/env python3
"""Assemble kappa.csv and drive solver smokes for LFŠ grid candidates.

Subcommands:
  metrics GRID...           one kappa.csv row per grid on stdout
  seed-grid GRID OUT        write OUT with LETNÍ/FILMOVÁ/ŠKOLA seeded across
                            the best (5,8,5) across triple
  smoke GRID [--timeout T]  run the real solver; print outcome+seconds
All path arguments are 15-line/15-char grid files. The seeder needs
scripts/lfs_grid_gen.py importable (same directory).
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lfs_grid_gen import (  # noqa: E402
    ALPHA, C_KAPPA, D_L, block_stats, slot_lengths, white_connected,
)

DICT = "local/longtail/longtail_22_l67c.dict"
BLOCKLIST = "resources/blocklist_cs.txt"
BINARY = "./target/release/ingrid_core"

TAJENKA = ("letní", "filmová", "škola")  # lengths 5, 7, 5


def analyse(path: Path):
    rows = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    hist, slots, cell_of = slot_lengths(rows)
    blocks = sum(r.count("#") for r in rows)
    white = 225 - blocks
    nslots = len(slots)
    lens = [len(s) for s in slots]
    share34 = (hist.get(3, 0) + hist.get(4, 0)) / nslots
    ksur = C_KAPPA * white / (ALPHA * sum(D_L[L] for L in lens))
    big, domino, lone, sq2, adj = block_stats(rows)
    sym = all(rows[r][c] == rows[14 - r][14 - c] for r in range(15) for c in range(15))
    conn = white_connected(rows)
    checked = all(len(v) == 2 for v in cell_of.values())
    crossdeg = len([v for v in cell_of.values() if len(v) == 2]) / nslots
    from lfs_grid_gen import tajenka_caps  # fallback shapes only

    caps = tajenka_caps(slots)
    return {
        "rows": rows, "hist": hist, "slots": slots, "blocks": blocks,
        "white": white, "nslots": nslots, "share34": share34,
        "kappa_surrogate": ksur, "big": big, "domino": domino, "lone": lone,
        "sq2": sq2, "adj": adj, "sym": sym, "conn": conn,
        "checked": checked, "crossdeg": crossdeg, "caps": caps,
    }


def _crossing_map(slots):
    owners: dict[tuple[int, int], list[int]] = {}
    for i, s in enumerate(slots):
        for cell in s:
            owners.setdefault(cell, []).append(i)
    crossing = [[False] * len(slots) for _ in slots]
    for ids in owners.values():
        if len(ids) == 2:
            a, b = ids
            crossing[a][b] = crossing[b][a] = True
    return crossing


def tajenka_info(an):
    """Best 5/7/5 tajenka: three MUTUALLY NON-CROSSING slots of lengths
    5, 7, 5 (any orientation). Prefer natural top-to-bottom order and a
    healthy spread; ordering is a nice-to-have, not a constraint. Also
    count slots of length 8-9 that cross NONE of the tajenka slots."""
    slots = an["slots"]
    lens = [len(s) for s in slots]
    crossing = _crossing_map(slots)
    fives = [i for i, L in enumerate(lens) if L == 5]
    sevens = [i for i, L in enumerate(lens) if L == 7]
    mid = [i for i, L in enumerate(lens) if L in (8, 9)]
    best = None
    best_key = None
    for i7 in sevens:
        r7 = slots[i7][0][0]
        for i5a in fives:
            if crossing[i5a][i7]:
                continue
            for i5b in fives:
                if i5b <= i5a or crossing[i5b][i7] or crossing[i5a][i5b]:
                    continue
                ra, rb = slots[i5a][0][0], slots[i5b][0][0]
                rows_sorted = sorted((ra, r7, rb))
                spread = min(rows_sorted[1] - rows_sorted[0], rows_sorted[2] - rows_sorted[1])
                taj = (i5a, i7, i5b)
                disjoint89 = sum(1 for j in mid if not any(crossing[j][t] for t in taj))
                natural = ra < r7 < rb or rb < r7 < ra
                key = (disjoint89 >= 2, natural, spread)
                if best_key is None or key > best_key:
                    best_key, best = key, taj
    if best is None:
        return None
    i5a, i7, i5b = best
    disjoint89 = sum(1 for j in mid if not any(crossing[j][t] for t in best))
    rs = (slots[i5a][0][0], slots[i7][0][0], slots[i5b][0][0])
    rows_sorted = sorted(rs)
    spread = min(rows_sorted[1] - rows_sorted[0], rows_sorted[2] - rows_sorted[1])
    return {
        "triple": best,
        "spread": spread,
        "natural_order": rs[0] < rs[1] < rs[2] or rs[2] < rs[1] < rs[0],
        "extra_89": disjoint89,
        "extra_89_cap": disjoint89 >= 2,
    }


def pick_triple_cells(an):
    """Chosen tajenka triple as three ordered cell lists (5, 7, 5 in
    reading order letní/filmová/škola) plus the tajenka_info dict."""
    info = tajenka_info(an)
    if info is None:
        return None, None
    slots = an["slots"]
    i5a, i7, i5b = info["triple"]
    # orient the 5s top-to-bottom for the reading order if possible
    if slots[i5a][0][0] > slots[i5b][0][0] or (
        slots[i5a][0][0] == slots[i5b][0][0] and slots[i5a][0][1] > slots[i5b][0][1]
    ):
        i5a, i5b = i5b, i5a
    return (slots[i5a], slots[i7], slots[i5b]), info


def cmd_seed_grid(args):
    an = analyse(Path(args.grid))
    triple, info = pick_triple_cells(an)
    if triple is None:
        print("no non-crossing 5/7/5 tajenka triple", file=sys.stderr)
        raise SystemExit(1)
    if len(TAJENKA[1]) != len(triple[1]) or len(TAJENKA[0]) != len(triple[0]):
        print(f"word/slot length mismatch: {TAJENKA}", file=sys.stderr)
        raise SystemExit(1)
    rows = [list(r) for r in an["rows"]]
    for word, cells in zip(TAJENKA, triple):
        for (r, c), ch in zip(cells, word):
            rows[r][c] = ch
    out = Path(args.out)
    out.write_text("\n".join("".join(r) for r in rows) + "\n", encoding="utf-8")
    locs = "/".join(f"{cells[0][0]},{cells[0][1]}{'a' if cells[0][0] == cells[-1][0] else 'd'}" for cells in triple)
    print(f"{out}: seeded {TAJENKA} at {locs} spread={info['spread']} natural={info['natural_order']} extra89={info['extra_89']}")


def solve(grid_path: Path, timeout_s: int, cores: int) -> tuple[str, float]:
    cmd = [
        BINARY, "--wordlist", DICT, "--blocklist", BLOCKLIST,
        "--min-score", "30", "--max-shared-substring", "4",
        "--cores", str(cores), "--timeout", str(timeout_s), "-t", str(grid_path),
    ]
    t0 = time.time()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s + 90, check=False)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", timeout_s + 90
    dt = time.time() - t0
    out = f"{done.stdout}\n{done.stderr}"
    if "finding fill" in out or ("fill" in out and "no fill" not in out and done.returncode == 0):
        return "FILL", dt
    if "unfillable" in out.lower() or "no fill" in out.lower() or "PROVEN" in out:
        return "NO FILL", dt
    return f"UNKNOWN(rc={done.returncode})", dt


def cmd_smoke(args):
    outcome, secs = solve(Path(args.grid), args.timeout, args.cores)
    print(f"{args.grid}: {outcome} in {secs:.1f}s")


def cmd_metrics(args):
    print(
        "grid,kappa_surrogate,slots,crossings,blocks,white,share_len34,"
        "len3,len4,len5,len6,len7,len8,len9,len10,len11,len12,"
        "slots_6_9,slots_8_10,adjacent_block_pairs,domino_blocks,lone_blocks,"
        "blocks_2x2,crossing_degree,symmetric_180,connected,fully_checked,"
        "taj_575,spread,natural_order,extra_89,fb_78,fb_46,fb_94,fb_10"
    )
    for g in args.grids:
        an = analyse(Path(g))
        h = an["hist"]
        info = tajenka_info(an)
        caps = an.get("caps") or {}
        crossings = an["white"] if an["checked"] else -1
        cells = [
            Path(g).stem,
            f"{an['kappa_surrogate']:.4f}", an["nslots"], crossings,
            an["blocks"], an["white"], f"{an['share34']:.3f}",
            h.get(3, 0), h.get(4, 0), h.get(5, 0), h.get(6, 0), h.get(7, 0),
            h.get(8, 0), h.get(9, 0), h.get(10, 0), h.get(11, 0), h.get(12, 0),
            sum(h.get(L, 0) for L in range(6, 10)),
            sum(h.get(L, 0) for L in range(8, 11)),
            an["adj"], an["domino"], an["lone"], an["sq2"],
            f"{an['crossdeg']:.3f}",
            "yes" if an["sym"] else "no",
            "yes" if an["conn"] else "no",
            "yes" if an["checked"] else "no",
            "yes" if info else "no",
            info["spread"] if info else "-",
            "yes" if info and info["natural_order"] else "no",
            info["extra_89"] if info else "-",
            "yes" if caps.get("fb_78") else "no",
            "yes" if caps.get("fb_46") else "no",
            "yes" if caps.get("fb_94") else "no",
            "yes" if caps.get("fb_10") else "no",
        ]
        print(",".join(str(c) for c in cells))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("metrics")
    p.add_argument("grids", nargs="+")
    p.set_defaults(fn=cmd_metrics)
    p = sub.add_parser("seed-grid")
    p.add_argument("grid")
    p.add_argument("out")
    p.set_defaults(fn=cmd_seed_grid)
    p = sub.add_parser("smoke")
    p.add_argument("grid")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--cores", type=int, default=2)
    p.set_defaults(fn=cmd_smoke)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
