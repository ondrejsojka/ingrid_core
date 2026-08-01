#!/usr/bin/env python3
"""Generate LFŠ-tuned 15x15 crossword grid templates by simulated annealing.

Search space: 8x8 quadrant (64 cells, bit 7 of each row = centre column),
completed to 15x15 by 180-degree rotation. Every candidate is verified:
full white connectivity, no unchecked cells (every white cell in an across
and a down slot of length >= 3), slot lengths in [3, 11], block components
<= 6 cells, <= 4 all-block 2x2 squares.

Objective (minimise, lexicographic):
  1. kappa surrogate  C / (2 * alpha * <d(L)/L>)   (block count cancels)
  2. LFŠ profile: hard need = across slots of lengths 5, 8, 5 in top-to-bottom
     order (tajenka LETNÍ FILMOVÁ ŠKOLA, three parts, reading order natural);
     fallbacks measured and priced: non-crossing (7,8) UHERSKÉ+HRADIŠTĚ and
     (4,6) KINO+HVĚZDA; maximise slots at 6-9; len(3-4) share <= 0.32;
     4 <= len5 <= 30; prefer adjacent block pairs, prefer the 8 mid-band.

d(L) = log2 of the number of words of length L in the STANDARD list that
will actually be used: local/longtail/longtail_22_l67c.dict at min-score 30
(136,784 eligible length slots).

Usage:
  lfs_grid_gen.py sample --n 20000 --seed ... > pool.csv       (fast survey)
  lfs_grid_gen.py enum22 --max-rows 500000 --seed ...          (2x2-square dfs)
  lfs_grid_gen.py anneal --state X --iters 400000 ...          (optimise one)
  lfs_grid_gen.py stats --state X                              (inspect state)
  lfs_grid_gen.py write-grid --state X --path g.txt            (materialise)
State X is a 4-bit-per-row hex string giving quadrant rows 0..7 (x = 0..7).
"""
from __future__ import annotations

import argparse
import math
import random
import re
import sys
import time
from collections import Counter

C_KAPPA = 4.5861
ALPHA = 0.9878
# longtail_22_l67c.dict at --min-score 30 (see local/lfs/grids/report.md)
D_L = {
    3: 9.8872,
    4: 12.0056,
    5: 13.2955,
    6: 14.2145,
    7: 14.6847,
    8: 14.4939,
    9: 14.2584,
    10: 13.7780,
    11: 13.1152,
}

N = 15
Q = 8  # quadrant size (rows 0..7, x = cols 0..7); x=8 mirror of x=6

CAP_KEYS = ["main_511", "natural_order", "fb_78", "fb_46", "fb_94", "fb_10"]


def caps_str(caps: dict) -> str:
    return "".join("1" if caps[k] else "0" for k in CAP_KEYS)


# ----------------------------------------------------------------------------
# state -> grid
# ----------------------------------------------------------------------------

def parse_state(s: str) -> tuple[int, ...]:
    s = s.strip()
    nbytes = (NPAIR + 7) // 8
    if re.fullmatch(r"[01]+", s) and len(s) == NPAIR:
        rows = [int(s[i * 8:(i + 1) * 8].ljust(8, "0"), 2) for i in range(nbytes)]
    elif re.fullmatch(r"[0-9a-fA-F]+", s) and len(s) == 2 * nbytes:
        rows = [int(s[i * 2:(i + 1) * 2], 16) for i in range(nbytes)]
    else:
        raise ValueError(f"state must be {NPAIR} bits or {2 * nbytes} hex chars")
    return tuple(rows)

def state_str(rows: tuple[int, ...]) -> str:
    return "".join(f"{r:02x}" for r in rows)


FIXED_BLOCKS: tuple = ()   # all 113 orbits free, including the centre
FIXED_WHITE: tuple = ()


def _orbit_index() -> tuple[list, list]:
    """Map every board cell to its orbit index 0..112; orbits of
    FIXED_BLOCKS and FIXED_WHITE are listed first, the 112 free pair
    orbits after."""
    fixed = {(r, c) for r, c in FIXED_BLOCKS}
    fixed |= {(14 - r, 14 - c) for r, c in FIXED_BLOCKS}
    fixed |= {(r, c) for r, c in FIXED_WHITE}
    fixed |= {(14 - r, 14 - c) for r, c in FIXED_WHITE}
    seen: dict[tuple[int, int], int] = {}
    orbits: list[list[tuple[int, int]]] = []
    for r in range(N):
        for c in range(N):
            if (r, c) in seen or (r, c) in fixed:
                continue
            orb = {(r, c), (14 - r, 14 - c)}
            idx = len(orbits)
            orbits.append(sorted(orb))
            for cell in orb:
                seen[cell] = idx
    return orbits, seen


FREE_ORBITS, _ = _orbit_index()
NPAIR = len(FREE_ORBITS)  # 112 - (112 - number of free orbits)


def rows15(state: tuple[int, ...]) -> list[str]:
    """Expand a free-orbit bitmask to a 180-degree symmetric 15x15.

    Orbits of FIXED_BLOCKS are blocks, orbits of FIXED_WHITE stay open,
    and each remaining orbit gets bit `k` (`state[k // 8] >> (k % 8)`):
    1 = both cells blocked, 0 = both white. `state` is a tuple of
    enough bytes to hold every free orbit ((NPAIR + 7) // 8 = 14).
    """
    grid = [["."] * N for _ in range(N)]
    for r, c in FIXED_BLOCKS:
        grid[r][c] = "#"
    for k, orbit in enumerate(FREE_ORBITS):
        if (state[k // 8] >> (k % 8)) & 1:
            for r, c in orbit:
                grid[r][c] = "#"
    return ["".join(r) for r in grid]




def centre_fixed_move(state: tuple[int, ...], rng: random.Random) -> tuple[int, int, int]:
    """Flip one free-orbit bit: byte = bit // 8, mask = 1 << (bit % 8)."""
    bit = rng.randrange(NPAIR)
    return bit // 8, 0, 1 << (bit % 8)


# ----------------------------------------------------------------------------
# grid analysis
# ----------------------------------------------------------------------------

def slot_lengths(rows: list[str]) -> tuple[Counter, list[list[tuple[int, int]]], dict]:
    """Return (length histogram, slot cell lists, per-cell across/down ids)."""
    slots: list[list[tuple[int, int]]] = []
    cell_of: dict[tuple[int, int], list[int]] = {}

    def add(run):
        if len(run) >= 2:
            cell_of.update({})
            slots.append(run)
            for cell in run:
                cell_of.setdefault(cell, []).append(len(slots) - 1)

    for r in range(N):
        run = []
        for c in range(N):
            if rows[r][c] == "#":
                add(run)
                run = []
            else:
                run.append((r, c))
        add(run)
    for c in range(N):
        run = []
        for r in range(N):
            if rows[r][c] == "#":
                add(run)
                run = []
            else:
                run.append((r, c))
        add(run)
    hist = Counter(len(s) for s in slots)
    return hist, slots, cell_of


def white_connected(rows: list[str]) -> bool:
    white = [(r, c) for r in range(N) for c in range(N) if rows[r][c] != "#"]
    if not white:
        return False
    seen = {white[0]}
    stack = [white[0]]
    while stack:
        r, c = stack.pop()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (r + dr, c + dc)
            if 0 <= n[0] < N and 0 <= n[1] < N and rows[n[0]][n[1]] != "#" and n not in seen:
                seen.add(n)
                stack.append(n)
    return len(seen) == len(white)


def block_stats(rows: list[str]) -> tuple[int, int, int, int, int]:
    """(max block component, components==2, lone blocks, 2x2 squares, adjacent pairs)."""
    seen: set[tuple[int, int]] = set()
    big = domino = lone = pairs = 0
    for r in range(N):
        for c in range(N):
            if rows[r][c] == "#" and (r, c) not in seen:
                comp = [(r, c)]
                seen.add((r, c))
                stack = [(r, c)]
                while stack:
                    a, b = stack.pop()
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        n = (a + dr, b + dc)
                        if 0 <= n[0] < N and 0 <= n[1] < N and rows[n[0]][n[1]] == "#" and n not in seen:
                            seen.add(n)
                            comp.append(n)
                            stack.append(n)
                big = max(big, len(comp))
                pairs += len(comp) - 1  # tree edges lower bound; recounted below
                if len(comp) == 2:
                    domino += 1
                elif len(comp) == 1:
                    lone += 1
    sq2 = sum(
        1
        for r in range(N - 1)
        for c in range(N - 1)
        if rows[r][c] == rows[r][c + 1] == rows[r + 1][c] == rows[r + 1][c + 1] == "#"
    )
    adj = sum(
        1
        for r in range(N)
        for c in range(N)
        if rows[r][c] == "#"
        and ((c + 1 < N and rows[r][c + 1] == "#") or (r + 1 < N and rows[r + 1][c] == "#"))
    )
    return big, domino, lone, sq2, adj


def tajenka_caps(slots: list[list[tuple[int, int]]]) -> dict:
    """Tajenka seatings, client spec ESTER(5)+KRUMBACHOVÁ(11), with
    SEDMIKRÁSKY(11) on the symmetric mirror.

    Hard gate (main_511): two NON-CROSSING length-11 slots plus one
    length-5 slot crossing neither (180-degree symmetry gives the second
    11 for free once one exists). extra_89 counts length-8/9 slots
    crossing none of the three tajenka slots.
    Fallbacks (secondary columns): (7,8) UHERSKÉ+HRADIŠTĚ,
      (4,6) KINO+HVĚZDA, (9,4), (10,).
    """
    by_cell: dict[tuple[int, int], list[int]] = {}
    for i, s in enumerate(slots):
        for cell in s:
            by_cell.setdefault(cell, []).append(i)
    crossing = [[False] * len(slots) for _ in slots]
    for ids in by_cell.values():
        if len(ids) == 2:
            a, b = ids
            crossing[a][b] = crossing[b][a] = True
    lens = [len(s) for s in slots]
    fives = [i for i, L in enumerate(lens) if L == 5]
    elevens = [i for i, L in enumerate(lens) if L == 11]
    mid = [i for i, L in enumerate(lens) if L in (8, 9)]
    main = False
    best_spread = -1
    best_extra89 = -1
    best_natural = True
    paircount = 0
    for i11a in range(len(elevens)):
        for i11b in range(i11a + 1, len(elevens)):
            a, b = elevens[i11a], elevens[i11b]
            if crossing[a][b]:
                continue
            paircount += 1
            for i5 in fives:
                if crossing[i5][a] or crossing[i5][b]:
                    continue
                main = True
                taj = (a, b, i5)
                x89 = sum(1 for j in mid if not any(crossing[j][t] for t in taj))
                ra, rb, r5 = slots[a][0][0], slots[b][0][0], slots[i5][0][0]
                rs = sorted((ra, rb, r5))
                spread = min(rs[1] - rs[0], rs[2] - rs[1])
                key = (x89, spread)
                if key > (best_extra89, best_spread):
                    best_extra89, best_spread = key

    def noncross(*shape_lens: int) -> bool:
        cand = [[i for i, s in enumerate(slots) if len(s) == want] for want in shape_lens]
        if not all(cand):
            return False
        if len(cand) == 1:
            return True
        import itertools

        return any(
            all(not crossing[a][b] for a, b in itertools.combinations(combo, 2))
            for combo in itertools.product(*cand)
        )

    return {
        "main_511": main,
        "n11pairs": paircount,
        "spread": best_spread,
        "natural_order": best_natural,
        "extra_89": best_extra89,
        "fb_78": noncross(7, 8),
        "fb_46": noncross(4, 6),
        "fb_94": noncross(9, 4),
        "fb_10": noncross(10),
    }


def evaluate(state: tuple[int, ...]):
    """Full evaluation. Returns dict or None if hard constraints fail."""
    rows = rows15(state)
    blocks = sum(row.count("#") for row in rows)
    if not 40 <= blocks <= 56:
        return None
    hist, slots, cell_of = slot_lengths(rows)
    if any(L < 3 or L > 11 for L in hist):
        return None
    if not white_connected(rows):
        return None
    # fully checked: every white cell in exactly 2 slots (slots are all >=3 here)
    if any(len(v) != 2 for v in cell_of.values()):
        return None
    big, domino, lone, sq2, adj = block_stats(rows)
    if big > 6 or sq2 > 4:
        return None
    nslots = len(slots)
    white = N * N - blocks
    lens = [len(s) for s in slots]
    denom = sum(D_L[L] for L in lens)
    kappa = C_KAPPA * white / (ALPHA * denom)  # = C / (2*alpha*<d/L>)
    share34 = (hist.get(3, 0) + hist.get(4, 0)) / nslots
    caps = tajenka_caps(slots)
    return {
        "rows": rows,
        "hist": hist,
        "slots": slots,
        "kappa": kappa,
        "blocks": blocks,
        "white": white,
        "nslots": nslots,
        "share34": share34,
        "len5": hist.get(5, 0),
        "s69": sum(hist.get(L, 0) for L in range(6, 10)),
        "n810": sum(hist.get(L, 0) for L in range(8, 11)),
        "adj": adj,
        "domino": domino,
        "lone": lone,
        "sq2": sq2,
        "big": big,
        "caps": caps,
    }


# ----------------------------------------------------------------------------
# objective
# ----------------------------------------------------------------------------

def tajenka_term(m) -> tuple[float, int]:
    """(penalty raising energy, bonus lowering energy)."""
    caps = m["caps"]
    if not caps["main_511"]:  # ESTER + KRUMBACHOVÁ + SEDMIKRÁSKY hard gate
        return float("inf"), 0
    pen = 0.0
    for fb in ("fb_78", "fb_46"):  # secondary fallbacks, measured and priced
        if not caps[fb]:
            pen += 0.012
    if caps["extra_89"] < 2:
        pen += 0.012  # want two tajenka-disjoint 8-9 slots for marquee hand-seed
    if not caps["natural_order"]:
        pen += 0.006  # reading-order nice-to-have
    pen += max(0, 3 - caps["spread"]) * 0.003  # prefer the three slots spread out
    # contact count of all need slots (lower = seeding slack)
    lens = [len(s) for s in m["slots"]]
    need_lens = {4, 5, 6, 7, 8, 9, 10}
    owners: dict[tuple[int, int], list[int]] = {}
    for i, s in enumerate(m["slots"]):
        for cell in s:
            owners.setdefault(cell, []).append(i)
    contacts = 0
    for ids in owners.values():
        if len(ids) == 2:
            a, b = ids
            if lens[a] in need_lens and lens[b] in need_lens:
                contacts += 1
    return pen, contacts


def energy(m, w_adj: float = 0.00035, w_contacts: float = 0.004) -> float:
    """Lower is better. inf if hard gates fail. kappa <= 0.95 is a hard
    gate: feasibility and fill margin dominate everything; profile
    rewards (s69, n810, adjacent block pairs, low need-contacts) are
    small nudges inside the feasible region."""
    if m is None:
        return float("inf")
    if (
        m["kappa"] > 0.97   # two 11s are expensive; smoke decides under 0.97
        or m["share34"] > 0.34
        or m["len5"] > 30
        or m["len5"] < 3
        or m["hist"].get(3, 0) < 3
        or m["hist"].get(11, 0) < 2
    ):
        return float("inf")
    pen, contacts = tajenka_term(m)
    if pen == float("inf"):
        return float("inf")
    e = (
        m["kappa"]
        - 0.0020 * m["s69"]
        - 0.0006 * m["n810"]
        + pen
        - w_adj * m["adj"]
        - w_contacts * contacts
    )
    return e


# ----------------------------------------------------------------------------
# sampling / enumeration / annealing
# ----------------------------------------------------------------------------

POP = [bin(i).count("1") for i in range(256)]


def random_state(rng: random.Random, p: float = 0.45) -> tuple[int, ...]:
    """Uniform random 112-bit start state with the expected block density."""
    st = []
    for _ in range((NPAIR + 7) // 8):
        v = 0
        for b in range(8):
            if rng.random() < p:
                v |= 1 << b
        st.append(v)
    return tuple(st)

def cmd_seed(args):
    rng = random.Random(args.seed)
    print(state_str(random_state(rng, args.p)))


def cmd_sample(args):
    rng = random.Random(args.seed)
    out = sys.stdout
    print("state,pop,survived,kappa,s34,s69,n810,l5,l3,adj,dom,sq2,lone,big,caps(585,mid,78,46,94,10)", file=out)
    for i in range(args.n):
        state = tuple(rng.getrandbits(8) for _ in range(8))
        m = evaluate(state)
        caps = caps_str(m["caps"]) if m else "------"
        row = (
            f"{state_str(state)},{sum(POP[r] for r in state)},{1 if m else 0},"
            + (
                f"{m['kappa']:.4f},{m['share34']:.3f},{m['s69']},{m['n810']},"
                f"{m['len5']},{m['hist'].get(3, 0)},{m['adj']},{m['domino']},"
                f"{m['sq2']},{m['lone']},{m['big']},{caps}"
                if m
                else ",,,,,,,,,,"
            )
        )
        print(row, file=out)


def cmd_enum22(args):
    """DFS over quadrant rows with running 2x2-square count <= 4 (rows 0..6 + row 7)."""
    rng = random.Random(args.seed)
    best = []

    def sq_between(a, b):
        return POP[(a & b) & ((a & b) << 1) & 0xFF]

    out = sys.stdout
    print("state,kappa,s34,s69,n810,l5,l3,adj,caps(585,mid,78,46,94,10)", file=out)
    tried = emitted = 0
    t0 = time.time()

    def rec(r, rows, sq):
        nonlocal tried, emitted
        if len(best) >= args.max_rows or time.time() - t0 > args.max_seconds:
            return True
        if r == 8:
            state = tuple(rows)
            tried += 1
            m = evaluate(state)
            if m and energy(m) != float("inf"):
                caps = caps_str(m["caps"])
                print(
                    f"{state_str(state)},{m['kappa']:.4f},{m['share34']:.3f},"
                    f"{m['s69']},{m['n810']},{m['len5']},{m['hist'].get(3, 0)},"
                    f"{m['adj']},{caps}",
                    file=out,
                )
                emitted += 1
            return False
        order = list(range(256))
        rng.shuffle(order)
        for cand in order:
            extra = sq_between(rows[r - 1], cand) if r > 0 else 0
            if sq + extra > 4:
                continue
            if rec(r + 1, rows + [cand], sq + extra):
                return True
        return False

    rec(0, [], 0)
    print(f"# tried={tried} emitted={emitted} secs={time.time() - t0:.1f}", file=sys.stderr)


def full_energy(state, w_adj, w_contacts):
    """Soft energy used by SA; feasible states get `energy()`, infeasible
    ones a penalty cost above MAX_FEASIBLE plus a violation measure."""
    m = evaluate(state)
    if m is not None:
        e = energy(m, w_adj, w_contacts)
        if e != float("inf"):
            return e, m
    # penalty = 10 + violation mass (all feasible energies stay << 10)
    rows = rows15(state)
    blocks = sum(r.count("#") for r in rows)
    pen = 10.0 + 0.02 * max(0, 40 - blocks) + 0.02 * max(0, blocks - 56)
    hist, slots, cell_of = slot_lengths(rows)
    pen += 4.0 * sum(n for L, n in hist.items() if L < 3)
    pen += 2.0 * sum(n for L, n in hist.items() if L > 11)
    if not white_connected(rows):
        pen += 5.0
    pen += 0.5 * sum(1 for v in cell_of.values() if len(v) != 2)
    big, domino, lone, sq2, adj = block_stats(rows)
    pen += 2.0 * max(0, big - 6)
    pen += 1.5 * max(0, sq2 - 4)
    if m is not None:
        e = energy(m, w_adj, w_contacts)
        pen += min(5.0, 0.02 * (e - 0.9) if e != float("inf") else 5.0)
    return pen, m


def cmd_anneal(args):
    rng = random.Random(args.seed)
    state = parse_state(args.state)
    cur_e, cur_m = full_energy(state, args.w_adj, args.w_contacts)
    best_state, best_m, best_e = state, cur_m, cur_e
    t0, t1 = args.t0, args.t1
    iters = args.iters
    log_every = max(1, iters // 100)
    acc = feas_acc = 0
    for it in range(iters):
        t = t0 * (t1 / t0) ** (it / iters)
        r, _c, bit = centre_fixed_move(state, rng)
        cand_rows = list(state)
        cand_rows[r] ^= bit
        cand = tuple(cand_rows)
        e, m = full_energy(cand, args.w_adj, args.w_contacts)
        if e <= cur_e or rng.random() < math.exp(min(0.0, (cur_e - e) / max(t, 1e-9))):
            state, cur_e, cur_m = cand, e, m
            acc += 1
            if m is not None:
                feas_acc += 1
            if e < best_e:
                best_state, best_m, best_e = cand, m, e
        if it % log_every == 0 and args.verbose:
            print(
                f"it={it} t={t:.5f} cur={cur_e:.5f} best={best_e:.5f} acc={acc} feas_acc={feas_acc}",
                file=sys.stderr,
            )
    m = best_m
    if m is None:
        print(f"# no feasible state found; best_e={best_e:.4f}", file=sys.stderr)
        return
    caps = caps_str(m["caps"])
    print(
        f"{state_str(best_state)},{best_e:.5f},{m['kappa']:.4f},{m['share34']:.3f},"
        f"{m['s69']},{m['n810']},{m['len5']},{m['hist'].get(3, 0)},{m['adj']},"
        f"{m['domino']},{m['sq2']},{m['lone']},{caps}"
    )
    if args.grid_out:
        with open(args.grid_out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(m["rows"]) + "\n")


def cmd_stats(args):
    state = parse_state(args.state)
    m = evaluate(state)
    if m is None:
        print("FAILS hard constraints")
        return
    print("\n".join(m["rows"]))
    print("histogram:", dict(sorted(m["hist"].items())))
    print(f"slots={m['nslots']} white={m['white']} kappa={m['kappa']:.4f} "
          f"share34={m['share34']:.3f} len5={m['len5']} s69={m['s69']} n810={m['n810']}")
    print(f"adj={m['adj']} dominoes={m['domino']} lone={m['lone']} sq2={m['sq2']} "
          f"maxcomp={m['big']} caps(585,midband,78,46,94,10)={ {k: m['caps'][k] for k in CAP_KEYS} }")


def cmd_write_grid(args):
    state = parse_state(args.state)
    with open(args.path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows15(state)) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seed")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--p", type=float, default=0.45)
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("sample")
    p.add_argument("--n", type=int, default=20000)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("enum22")
    p.add_argument("--max-rows", type=int, default=500000)
    p.add_argument("--max-seconds", type=float, default=600)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(fn=cmd_enum22)

    p = sub.add_parser("anneal")
    p.add_argument("--state", required=True)
    p.add_argument("--iters", type=int, default=400000)
    p.add_argument("--t0", type=float, default=0.006)
    p.add_argument("--t1", type=float, default=0.0006)
    p.add_argument("--w-adj", type=float, default=0.00035)
    p.add_argument("--w-contacts", type=float, default=0.004)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--verbose", type=int, default=0)
    p.add_argument("--grid-out")
    p.set_defaults(fn=cmd_anneal)

    p = sub.add_parser("stats")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("write-grid")
    p.add_argument("--state", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(fn=cmd_write_grid)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
