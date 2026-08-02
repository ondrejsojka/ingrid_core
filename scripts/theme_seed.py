#!/usr/bin/env python3
"""Place theme entries into a template first, then screen the remainder.

Measured on `local/trials/metro_brno_grid.txt` with a 447-entry theme list: an
unconstrained fill took 56 s, the same search took 296 s to reach *one* preferred
word, and never reached two inside five minutes on ten cores. Asking the solver to
discover theme words is asking it to hit a target that occupies well under one
percent of every slot domain, and the cost compounds with each additional word.

Themed American crosswords are not built that way. Theme entries are placed first
and the grid is filled around them. Ingrid already accepts fixed letters in a
template, so the only missing piece is choosing placements that leave a fillable
remainder. This script searches the small space of theme placements instead of the
large space of fills, and screens each candidate with the committed kappa
heuristic from `fill_margin.py` before any solver time is spent.

    python3 scripts/theme_seed.py \\
      --grid local/trials/metro_brno_grid.txt \\
      --theme local/rich/metro_core_exp.dict \\
      --wordlist local/trials/standard_clued_n33.dict --min-score 30 \\
      --target 6 --attempts 4000 --keep 12 \\
      --outdir local/rich/seeds

Each emitted template is a normal Ingrid grid file whose theme cells carry fixed
letters, so it can be handed straight to `ingrid_core`. Requires numpy and the
same environment as `fill_margin.py`.
"""

from __future__ import annotations

import argparse
import csv
import random
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from fill_margin import DEFAULT_KAPPA_STAR, Grid, arc_consistency, load_words, measure
from pin_long import cells_of, slots


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.strip().lower())


def load_theme(path: Path, min_score: int) -> dict[str, int]:
    entries: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        word, _, raw = text.partition(";")
        word = normalize_word(word)
        score = int(raw) if raw.strip() else 50
        if word and score >= min_score:
            entries[word] = max(entries.get(word, 0), score)
    return entries


class Placement:
    """Slot geometry for one template, indexed the way seeding needs it."""

    def __init__(self, rows: list[str]):
        self.rows = rows
        self.height = len(rows)
        self.width = len(rows[0])
        slot_tuples = slots(rows, min_run=2)
        self.slots: list[list[tuple[int, int]]] = [cells_of(s) for s in slot_tuples]
        self.direction: list[str] = [s[0] for s in slot_tuples]
        self.cell_slots: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index, slot in enumerate(self.slots):
            for cell in slot:
                self.cell_slots[cell].append(index)
        # Crossing degree drives the seeding order: a theme word in a slot whose
        # every cell is checked constrains that many perpendicular slots at once.
        self.degree = [
            sum(1 for cell in slot if len(self.cell_slots[cell]) > 1)
            for slot in self.slots
        ]

    def render(self, letters: dict[tuple[int, int], str]) -> list[str]:
        return [
            "".join(letters.get((r, c), self.rows[r][c]) for c in range(self.width))
            for r in range(self.height)
        ]


def seed_once(
    placement: Placement,
    by_length: dict[int, list[str]],
    target: int,
    rng: random.Random,
    words: dict[int, "object"],
    trials_per_step: int,
    max_degree_first: bool,
) -> tuple[dict[tuple[int, int], str], int] | None:
    """Place `target` theme words, screening arc consistency after each one.

    Blind placement is hopeless past two entries: on `metro_brno_grid.txt` every
    one of 120 random three-word seedings was proven unfillable. Screening after
    each placement instead of at the end turns that into a search that only ever
    extends a partial template arc consistency still accepts.
    """
    eligible = [
        index for index, slot in enumerate(placement.slots) if by_length.get(len(slot))
    ]
    if len(eligible) < target:
        return None

    letters: dict[tuple[int, int], str] = {}
    used: set[str] = set()
    open_slots = list(eligible)
    rng.shuffle(open_slots)
    if max_degree_first:
        open_slots.sort(key=lambda index: -placement.degree[index])
    checks = 0

    for _ in range(target):
        accepted = False
        for index in list(open_slots):
            slot = placement.slots[index]
            candidates = by_length[len(slot)]
            for word in rng.sample(candidates, min(len(candidates), trials_per_step)):
                if word in used:
                    continue
                if any(
                    cell in letters and letters[cell] != letter
                    for cell, letter in zip(slot, word)
                ):
                    continue
                trial = dict(letters)
                trial.update(zip(slot, word))
                checks += 1
                if arc_consistency(Grid.from_rows(placement.render(trial)), words) is None:
                    continue
                letters = trial
                used.add(word)
                open_slots.remove(index)
                accepted = True
                break
            if accepted:
                break
        if not accepted:
            return None
    return letters, checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--grid", type=Path, required=True)
    parser.add_argument("--theme", type=Path, required=True, help="preferred-tier dictionary to seed from")
    parser.add_argument("--wordlist", type=Path, action="append", default=[], required=True, help="standard dictionary used for the kappa screen (repeatable)")
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--theme-min-score", type=int, default=0)
    parser.add_argument("--target", type=int, required=True, help="theme entries to place")
    parser.add_argument("--attempts", type=int, default=2000)
    parser.add_argument("--keep", type=int, default=10, help="templates to emit, best kappa first")
    parser.add_argument("--kappa-star", type=float, default=DEFAULT_KAPPA_STAR)
    parser.add_argument("--min-theme-length", type=int, default=4, help="do not seed slots shorter than this")
    parser.add_argument("--easy-first", action="store_true", help="seed low-degree slots first instead of high-degree")
    parser.add_argument("--trials-per-step", type=int, default=40, help="theme words tried per slot before moving on")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="CSV of every screened candidate")
    parser.add_argument("--verify-binary", type=Path, help="ingrid_core to confirm each candidate really fills")
    parser.add_argument("--verify-timeout", type=int, default=20)
    parser.add_argument("--verify-cores", type=int, default=2)
    parser.add_argument("--blocklist", type=Path, help="passed through to --verify-binary")
    parser.add_argument("--max-shared-substring", type=int, help="passed through to --verify-binary")
    args = parser.parse_args(argv)

    rows = [line for line in args.grid.read_text(encoding="utf-8").split() if line]
    placement = Placement(rows)
    theme = load_theme(args.theme, args.theme_min_score)
    by_length: dict[int, list[str]] = defaultdict(list)
    for word in theme:
        if len(word) >= args.min_theme_length:
            by_length[len(word)].append(word)
    by_length = {length: sorted(words) for length, words in by_length.items()}

    words = load_words(args.wordlist, args.min_score, ignore_diacritics=False)
    rng = random.Random(args.seed)

    seen: set[tuple[str, ...]] = set()
    results: list[dict[str, object]] = []
    unfillable = 0
    duplicates = 0
    incomplete = 0

    checks_total = 0
    for attempt in range(args.attempts):
        seeded = seed_once(
            placement, by_length, args.target, rng, words, args.trials_per_step, not args.easy_first
        )
        if seeded is None:
            incomplete += 1
            continue
        letters, checks = seeded
        checks_total += checks
        candidate = placement.render(letters)
        key = tuple(candidate)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        stats = measure(Grid.from_rows(candidate), words)
        if stats is None:
            unfillable += 1
            continue
        placed = sorted(
            {
                "".join(candidate[r][c] for r, c in slot)
                for slot in placement.slots
                if all((r, c) in letters for r, c in slot)
            }
        )
        results.append(
            {
                "attempt": attempt,
                "kappa": round(stats["kappa"], 4),
                "min_domain": stats["min_domain"],
                "median_domain": stats["median_domain"],
                "theme_words": " ".join(placed),
                "rows": candidate,
            }
        )

    results.sort(key=lambda item: float(item["kappa"]))

    verified = None
    if args.verify_binary:
        # Crossing arc consistency is a much weaker filter than Ingrid's own initial
        # consistency, which additionally propagates duplicate and shared-substring
        # eliminations. Every candidate that survived kappa on the baseline grid at
        # target six was still rejected by the solver in under three seconds, so the
        # only honest screen is the solver itself.
        verified = []
        with tempfile.TemporaryDirectory() as scratch:
            probe = Path(scratch) / "candidate.txt"
            for item in results:
                probe.write_text("\n".join(item["rows"]) + "\n", encoding="utf-8")
                command = [
                    str(args.verify_binary),
                    "--wordlist", str(args.wordlist[0]),
                    "--min-score", str(args.min_score),
                    "--cores", str(args.verify_cores),
                    "--timeout", str(args.verify_timeout),
                ]
                for extra in args.wordlist[1:]:
                    command += ["--wordlist", str(extra)]
                if args.max_shared_substring:
                    command += ["--max-shared-substring", str(args.max_shared_substring)]
                if args.blocklist:
                    command += ["--blocklist", str(args.blocklist)]
                command.append(str(probe))
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                if completed.returncode == 0:
                    item["verdict"] = "fill"
                    item["fill"] = completed.stdout.strip()
                    verified.append(item)
                elif "Unfillable" in completed.stderr:
                    # Proven: Ingrid's initial consistency emptied a domain.
                    item["verdict"] = "unfillable"
                else:
                    # Only a budget statement, not a proof. Keep the two apart or the
                    # seeder will report a grid as saturated when it is merely slow.
                    item["verdict"] = "timeout"
                item["verified"] = int(item["verdict"] == "fill")
                if len(verified) >= args.keep:
                    break
        results = verified + [item for item in results if not item.get("verified")]

    emit_from = verified if verified is not None else results
    args.outdir.mkdir(parents=True, exist_ok=True)
    emitted = 0
    for rank, item in enumerate(emit_from[: args.keep]):
        path = args.outdir / f"seed_{args.target:02d}_{rank:02d}.txt"
        path.write_text("\n".join(item["rows"]) + "\n", encoding="utf-8")
        item["path"] = str(path)
        emitted += 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["attempt", "kappa", "min_domain", "median_domain", "theme_words", "verified", "verdict", "path"],
                extrasaction="ignore",
            )
            writer.writeheader()
            for item in results:
                writer.writerow(item)

    print(
        f"{args.attempts} attempts ({checks_total} arc-consistency checks): "
        f"{incomplete} could not place {args.target}, "
        f"{duplicates} duplicates, {unfillable} proven unfillable, {len(results)} survived"
    )
    if results:
        under = sum(1 for item in results if float(item["kappa"]) <= args.kappa_star)
        print(f"  kappa range {results[0]['kappa']} .. {results[-1]['kappa']}, {under} at or below kappa* {args.kappa_star}")
        print(f"  best: {results[0]['theme_words']}")
    if verified is not None:
        counts = Counter(item.get("verdict", "unscreened") for item in results)
        print(f"  solver verdicts: {dict(counts)}")
        if verified:
            print(f"  best verified: {verified[0]['theme_words']}")
    print(f"  wrote {emitted} templates to {args.outdir}")
    return 0 if emit_from else 1


if __name__ == "__main__":
    raise SystemExit(main())
