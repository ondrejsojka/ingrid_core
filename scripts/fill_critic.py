#!/usr/bin/env python3
"""Deterministic pre-filter grader over a completed crossword fill.

Implements the "Deterministic pre-filters" tier of the critic sketched in
.omp/skills/good-crossword/SKILL.md ("Do the cheap deterministic things first
and only spend a model on the residual"). Everything here is a join or an
n-gram count; judgement calls ("attested somewhere but no honest clue
exists") are deliberately left to the model tier.

What it measures
----------------
Per entry (every across/down run of >= 3 white cells in the template):

  tier                    preferred | standard | UNKNOWN (in no supplied list;
                          UNKNOWN is a hard defect — the dictionaries disagree
                          with the fill)
  reference_score         score from --reference (national corpus, e.g.
                          cstenten); a non-preferred entry missing there above
                          --min-score is flagged not_in_reference — suspect,
                          per the skill (`rop`, `dom`)
  same_lemma_collision    HARD DEFECT, fill-internal: another entry shares a
                          lemma. Lemma sources, in order: --expand-report (the
                          theme_expand --report CSV carries the lemma of every
                          generated form → the check is a join), then MorphoDiTa
                          analysis via --model, then the cheap stem fallback —
                          identical first 4 characters AND intersecting
                          MorphoDiTa lemma sets. This is the class
                          --max-shared-substring 4 structurally cannot express
                          (`kope`/`kopali`, `lužánky`/`lužánek`, `velel`/`velet`).
  no_morphology           HARD DEFECT: no MorphoDiTa analysis AND not attested
                          as a standalone token in --corpus → fragment of a
                          hyphenation or column break
  bare_relational_adjective  RISKY: analyses only as adjectives and either
                          (corpus given) its corpus occurrences are
                          overwhelmingly (>= --relational-followed-by-noun,
                          default 0.7) followed by a noun, or (no corpus) its
                          MorphoDiTa lemma ends in a relational suffix
                          (-ský, -cký, -ový, -ní, -ova, -ovo, -ovy). A bare
                          relational adjective is a fragment of a nominal
                          phrase; it can only be carried by a výpustka (or
                          possessive) clue that supplies the head noun
                          (CLUES.md §5).
  crosswordese            flag: length <= 4 AND reference score below
                          --crosswordese-max-score (default 25) — the
                          `ažaž`/`rob`/`trop` class; tolerated once, priced in
                          the score

Fill score (multi-term on purpose; every term is printed separately so the
weights can be argued with):

  score = recognizable_preferred_count
          - 3 * hard_defects
          - 1 * risky
          + 2 * spread_ratio
          - 10 * crosswordese_density

  recognizable_preferred_count   preferred entries not themselves defects
  hard_defects                   UNKNOWN | same_lemma_collision | no_morphology
  risky                          bare_relational_adjective | not_in_reference
  spread_ratio                   mean pairwise Chebyshev distance between
                                 preferred-entry midpoints, normalised by the
                                 expectation for the same count of points drawn
                                 uniformly over the template's white cells
                                 (Monte Carlo, seed --spread-seed, 10000 draws).
                                 < ~0.8 = the theme is clumped in one corner.
  crosswordese_density           crosswordese flags / entry count

Exit code 1 on hard defects, 0 otherwise.

Example:
    scripts/fill_critic.py --fill local/trials/no_marked_n33_fill.txt \
        --wordlist local/trials/standard_clued_n33.dict \
        --preferred local/rich/metro_v5.dict \
        --expand-report local/rich/metro_v5.csv \
        --corpus 'local/metropolitan/txt/*.txt' \
        --reference local/cstenten.dict --report out.csv \
        --model .../czech-morfflex2.1-250909.dict
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from glob import glob
from pathlib import Path

RELATIONAL_SUFFIXES = ("ský", "cký", "ový", "ní", "ova", "ovo", "ovy")
STEM_LEN = 4
RELATIONAL_NOUN_RATIO = 0.7
SPREAD_DRAWS = 10000
MIN_LEN = 3
TOKEN_CAP = 200_000

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def load_grid(path: Path) -> list[str]:
    rows = [line.rstrip("\n") for line in path.open(encoding="utf-8") if line.strip()]
    if not rows:
        raise SystemExit(f"{path}: empty grid")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise SystemExit(f"{path}: ragged grid (rows differ in width)")
    return rows


def strip_accents(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def load_dict(path: Path) -> dict[str, int]:
    """word;score dictionary. Keys stored diacritic-stripped, lowercase."""
    out: dict[str, int] = {}
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        word, _, score = line.rpartition(";")
        try:
            value = int(score)
        except ValueError:
            value = 0
        key = strip_accents(word.lower())
        if key not in out or value > out[key]:
            out[key] = value
    return out


def parse_slots(template: list[str], min_len: int = MIN_LEN) -> list[tuple]:
    """All (direction, row, col, length) white runs of length >= min_len."""
    slots = []
    rows, cols = len(template), len(template[0])
    for r in range(rows):
        c = 0
        while c < cols:
            if template[r][c] != "#" and c + 1 < cols and template[r][c + 1] != "#":
                c0 = c
                while c < cols and template[r][c] != "#":
                    c += 1
                length = c - c0
                if length >= min_len:
                    slots.append(("A", r, c0, length))
            else:
                c += 1
    for c in range(cols):
        r = 0
        while r < rows:
            if template[r][c] != "#" and r + 1 < rows and template[r + 1][c] != "#":
                r0 = r
                while r < rows and template[r][c] != "#":
                    r += 1
                length = r - r0
                if length >= min_len:
                    slots.append(("D", r0, c, length))
            else:
                r += 1
    return slots


def extract_entries(template: list[str], fill: list[str]) -> list[dict]:
    """Per slot: text, filled flag, length, midpoint (row, col) in cells."""
    entries = []
    for direction, r0, c0, length in parse_slots(template):
        cells = (
            [(r0, c0 + i) for i in range(length)]
            if direction == "A"
            else [(r0 + i, c0) for i in range(length)]
        )
        text = "".join(fill[r][c] for r, c in cells)
        entries.append(
            {
                "direction": direction,
                "row": r0 + 1,
                "col": c0 + 1,
                "length": length,
                "text": text,
                "filled": "#" not in text,
                "midpoint": (
                    float(r0) + (length - 1) / 2 if direction == "D" else float(r0),
                    float(c0) + (length - 1) / 2 if direction == "A" else float(c0),
                ),
            }
        )
    return entries


def load_expand_report(path: Path | None) -> dict[str, set[str]]:
    """word (nondiac) -> lemmas from a theme_expand --report CSV."""
    table: dict[str, set[str]] = defaultdict(set)
    if not path:
        return table
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            word = strip_accents(row["word"].lower())
            lemma = row.get("lemma", "")
            if lemma:
                table[word].add(lemma.lower())
    return table


def analyze_forms(morpho, words: set[str], try_variants: bool = True,
                  first_lower: bool = False) -> dict[str, set[tuple[str, str]]]:
    """MorfFlex analyses of word forms: word -> {(lemma, tag)}.

    Uses the full morphology (morpho .dict), not the tagger. With
    try_variants the form is also tried capitalized (lowercase in curly case
    can hit a silly paradigm — `brna` -> `Brno` gen.sg. is real, whereas
    capitalize-or-leave, theme_expand's convention, yields `Brno_;G` itself;
    both are counted as attestation). first_lower additionally lowercases
    only the first character (`Afrika` -> `afrika`) for the relational-suffix
    lemma test.
    """
    from ufal.morphodita import Morpho, TaggedLemmas

    out: dict[str, set[tuple[str, str]]] = {}
    for word in sorted(words):
        analyses: set[tuple[str, str]] = set()
        variants = {word}
        if try_variants:
            variants |= {word.capitalize(), word.lower()}
        if first_lower:
            variants.add(word[0].lower() + word[1:])
        for variant in variants:
            tagged = TaggedLemmas()
            morpho.analyze(variant, Morpho.NO_GUESSER, tagged)
            for item in tagged:
                # MorfFlex tags an unrecognized token X@ and echoes it back.
                if not item.tag.startswith("X@"):
                    analyses.add((item.lemma.lower(), item.tag))
        out[word] = analyses
    return out


def load_corpus_tokens(patterns: list[str]) -> list[str]:
    """Lower-cased \\w+ tokens of all files matching the globs (capped)."""
    tokens: list[str] = []
    for pattern in patterns:
        paths = sorted(glob(pattern))
        if not paths and Path(pattern).is_file():
            paths = [pattern]
        for path in paths:
            with open(path, encoding="utf-8", errors="replace") as handle:
                tokens.extend(t.lower() for t in TOKEN_RE.findall(handle.read()))
            if len(tokens) > TOKEN_CAP:
                return tokens[:TOKEN_CAP]
    return tokens


def corpus_stats(corpus_tokens: list[str], morpho, wanted: set[str]):
    """For each wanted word (diacritic-stripped): occurrence count over the
    corpus and a histogram of the POS of the following token.

    Following-POS needs morphology on corpus neighbours; only tokens adjacent
    to a wanted occurrence are analyzed, so the cost is bounded by the number
    of hits, not the corpus size.
    """
    occ: Counter = Counter()
    following: dict[str, Counter] = {w: Counter() for w in wanted}
    neighbour_forms: set[str] = set()
    for i, tok in enumerate(corpus_tokens):
        base = strip_accents(tok)
        if base in wanted:
            occ[base] += 1
            if i + 1 < len(corpus_tokens):
                neighbour_forms.add(corpus_tokens[i + 1])
    pos_of: dict[str, set[str]] = {}
    for form, analyses in analyze_forms(morpho, neighbour_forms,
                                        try_variants=False).items():
        if analyses:
            pos_of[form] = {tag[0] for _, tag in analyses}
    for i, tok in enumerate(corpus_tokens):
        base = strip_accents(tok)
        if base in wanted and i + 1 < len(corpus_tokens):
            for pos in pos_of.get(corpus_tokens[i + 1], ()):
                following[base][pos] += 1
    return occ, following


def chebyshev(a, b) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def spread_metrics(template: list[str], mids: list[tuple[float, float]], seed: int):
    """(sectors hit of the 3x3 grid partition, mean pairwise Chebyshev,
    Monte-Carlo expectation under uniform over white cells, ratio)."""
    rows, cols = len(template), len(template[0])
    sectors = {
        (min(int(r * 3 / rows), 2), min(int(c * 3 / cols), 2)) for r, c in mids
    }
    if len(mids) < 2:
        return len(sectors), None, None, None
    mean_pair = sum(
        chebyshev(mids[i], mids[j])
        for i in range(len(mids))
        for j in range(i + 1, len(mids))
    ) / (len(mids) * (len(mids) - 1) / 2)

    white = [(r, c) for r in range(rows) for c in range(cols) if template[r][c] != "#"]
    k = len(mids)
    n_pairs = k * (k - 1) / 2
    rng = random.Random(seed)
    expected = sum(
        sum(chebyshev(s[i], s[j]) for i in range(k) for j in range(i + 1, k)) / n_pairs
        for s in (rng.sample(white, k) for _ in range(SPREAD_DRAWS))
    ) / SPREAD_DRAWS
    return len(sectors), mean_pair, expected, (mean_pair / expected if expected else None)


def strip_name_marks(lemma: str) -> str:
    """MorfFlex semantic/grammateme tails (`_;G`, `_^(*3...)`) off a lemma."""
    return re.split(r"[;^_]", lemma, maxsplit=1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fill", type=Path, required=True,
                        help="fill grid: one line per row, letters; "
                             "unfilled cells stay '#'")
    parser.add_argument("--template", type=Path,
                        help="block-pattern grid ('#' = block); default: derive "
                             "from the fill's own '#' cells")
    parser.add_argument("--wordlist", type=Path, action="append", default=[],
                        help="standard dictionary (word;score); repeatable")
    parser.add_argument("--preferred", type=Path,
                        help="preferred/theme dictionary (word;score)")
    parser.add_argument("--expand-report", type=Path,
                        help="theme_expand --report CSV "
                             "(word,length,score,origin,lemma,tag,in_standard) — "
                             "primary lemma source for the collision check")
    parser.add_argument("--corpus", action="append", default=[],
                        help="publication text file or glob; repeatable")
    parser.add_argument("--reference", type=Path,
                        help="national reference dictionary (word;score), "
                             "e.g. cstenten")
    parser.add_argument("--model", type=Path,
                        help="MorfFlex morphology .dict (the full morphology, "
                             "not the tagger)")
    parser.add_argument("--min-score", type=int, default=30,
                        help="reference floor above which absence is suspect")
    parser.add_argument("--crosswordese-max-score", type=int, default=25,
                        help="entries of length <= 4 scoring below this in the "
                             "reference are flagged crosswordese")
    parser.add_argument("--relational-followed-by-noun", type=float,
                        default=RELATIONAL_NOUN_RATIO,
                        help="share of corpus occurrences followed by a noun "
                             "that convicts a bare adjective")
    parser.add_argument("--spread-seed", type=int, default=12345,
                        help="Monte Carlo seed for the spread expectation")
    parser.add_argument("--report", type=Path, help="write per-entry CSV here")
    args = parser.parse_args()

    fill = load_grid(args.fill)
    template = (
        load_grid(args.template)
        if args.template
        else ["".join("#" if ch == "#" else "." for ch in row) for row in fill]
    )
    if len(template) != len(fill) or len(template[0]) != len(fill[0]):
        raise SystemExit("template and fill shapes differ")

    standard: dict[str, int] = {}
    for path in args.wordlist:
        standard.update(load_dict(path))
    preferred_dict = load_dict(args.preferred) if args.preferred else {}
    report_lemmas = load_expand_report(args.expand_report)
    reference = load_dict(args.reference) if args.reference else {}

    all_slots = extract_entries(template, fill)
    entries = [e for e in all_slots if e["filled"]]
    print(f"fill {len(fill)}x{len(fill[0])}; {len(all_slots)} slots >= {MIN_LEN}, "
          f"{len(entries)} filled entries")

    morpho = None
    word_analyses: dict[str, set[tuple[str, str]]] = {}
    word_pos: dict[str, set[tuple[str, str]]] = {}
    entry_keys = {strip_accents(e["text"].lower()) for e in entries}
    if args.model:
        from ufal.morphodita import Morpho

        morpho = Morpho.load(str(args.model))
        if morpho is None:
            raise SystemExit(f"could not load morphology {args.model}")
        # For attestation the variant set is right: `kamp` -> kampaň,
        # `tmě` -> tma count as "the form exists", and the lowercase index is
        # where curly-only paradigms (Afrika -> afriky) live. For POS identity
        # (relational-adjective test) stick to the observed spelling plus the
        # first-letter-lowered one, or `kamp` borrows kampaň's N.
        analyses_of = analyze_forms(morpho, {e["text"] for e in entries},
                                    try_variants=True, first_lower=True)
        analysis_pos = analyze_forms(morpho, {e["text"] for e in entries},
                                     try_variants=False, first_lower=True)
        word_analyses = defaultdict(set)
        word_pos = defaultdict(set)
        for e in entries:
            key = strip_accents(e["text"].lower())
            word_analyses[key].update(analyses_of.get(e["text"], set()))
            word_pos[key].update(analysis_pos.get(e["text"], set()))
        word_analyses = dict(word_analyses)
        word_pos = dict(word_pos)

    occ: Counter = Counter()
    following: dict[str, Counter] = {}
    if args.corpus:
        corpus_tokens = load_corpus_tokens(args.corpus)
        print(f"corpus: {len(corpus_tokens)} tokens from {args.corpus}")
        if morpho is not None:
            occ, following = corpus_stats(corpus_tokens, morpho, entry_keys)
        else:
            occ = Counter(
                w for w in (strip_accents(t) for t in corpus_tokens) if w in entry_keys
            )

    hard_defects: list[tuple[str, list[str]]] = []
    risky: list[tuple[str, list[str]]] = []
    crosswordese_count = 0
    tier_counts: Counter = Counter()
    preferred_mids: list[tuple[float, float]] = []
    per_entry: list[dict] = []

    # Lemmas per entry, for the fill-internal collision join.
    entry_lemmas: dict[str, set[str]] = {}
    for e in entries:
        key = strip_accents(e["text"].lower())
        lemmas = set(report_lemmas.get(key, set()))
        lemmas |= {lemma for lemma, _ in word_analyses.get(key, set())}
        entry_lemmas[key] = {
            strip_accents(strip_name_marks(l)) for l in lemmas if l
        }

    lemma_owners: dict[str, list[str]] = defaultdict(list)
    for key, lemmas in entry_lemmas.items():
        for lemma in lemmas:
            lemma_owners[lemma].append(key)
    collision_partners: dict[str, dict[str, str]] = defaultdict(dict)
    for lemma, owners in lemma_owners.items():
        for a in set(owners):
            for b in set(owners):
                if a != b:
                    collision_partners[a][b] = "same_lemma_collision"

    # Stem fallback: same first STEM_LEN chars AND intersecting lemma sets.
    keys = sorted(entry_lemmas)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if len(a) < STEM_LEN or len(b) < STEM_LEN or a[:STEM_LEN] != b[:STEM_LEN]:
                continue
            if entry_lemmas[a] & entry_lemmas[b]:
                collision_partners[a].setdefault(b, "same_stem_collision")
                collision_partners[b].setdefault(a, "same_stem_collision")

    ascii_suffixes = tuple(strip_accents(s) for s in RELATIONAL_SUFFIXES)
    for e in entries:
        text = strip_accents(e["text"].lower())
        ref = reference.get(text, 0)
        flags: list[str] = []
        defects: list[str] = []
        risks: list[str] = []

        if args.preferred and text in preferred_dict:
            tier = "preferred"
            preferred_mids.append(e["midpoint"])
        elif text in standard:
            tier = "standard"
        else:
            tier = "UNKNOWN"
            defects.append("unknown_tier")

        if tier != "preferred" and args.reference and ref < args.min_score:
            risks.append(f"not_in_reference(ref={ref}<{args.min_score})")

        for partner, kind in sorted(collision_partners.get(text, {}).items()):
            defects.append(f"{kind}:{partner}")

        if morpho is not None and not word_analyses.get(text):
            if not args.corpus or occ.get(text, 0) == 0:
                defects.append("no_morphology")

        # Bare relational adjective (POS from the observed spelling).
        anal = word_pos.get(text)
        if anal and {tag[0] for _, tag in anal} == {"A"}:
            if args.corpus and occ.get(text, 0) > 0:
                foll = following.get(text, Counter())
                total = sum(foll.values())
                noun_share = foll.get("N", 0) / total if total else 0.0
                if noun_share >= args.relational_followed_by_noun:
                    risks.append(
                        f"bare_relational_adjective:followed_by_noun="
                        f"{noun_share:.2f} ({foll.get('N', 0)}/{total}); needs a "
                        "výpustka clue supplying the head noun (CLUES.md §5)"
                    )
            elif not args.corpus:
                base_lemmas = {
                    strip_accents(strip_name_marks(l)) for l, _ in anal
                }
                if any(l.endswith(ascii_suffixes) for l in base_lemmas):
                    risks.append(
                        "bare_relational_adjective:relational_suffix_lemma "
                        "(no corpus); needs a výpustka clue supplying the head "
                        "noun (CLUES.md §5)"
                    )

        if e["length"] <= 4 and 0 < ref < args.crosswordese_max_score:
            flags.append("crosswordese")
            crosswordese_count += 1

        if defects:
            hard_defects.append((text, defects))
        if risks:
            risky.append((text, risks))

        per_entry.append(
            {
                "answer": e["text"],
                "direction": e["direction"],
                "row": e["row"],
                "col": e["col"],
                "length": e["length"],
                "tier": tier,
                "reference_score": ref,
                "verdict": "defect" if defects else ("risky" if risks else "ok"),
                "defects": ";".join(defects),
                "risks": ";".join(risks),
                "flags": ";".join(flags),
                "lemmas": "|".join(sorted(entry_lemmas.get(text, set()))),
            }
        )
        tier_counts[tier] += 1

    sectors, mean_pair, spread_expected, spread_ratio = spread_metrics(
        template, preferred_mids, args.spread_seed
    )

    # -- report -------------------------------------------------------------
    print("\n== per entry ==")
    print("%-14s %s %3s %-9s %4s %-7s %s" % ("answer", "dir", "len", "tier",
                                              "ref", "verdict", "notes"))
    order = {"defect": 0, "risky": 1, "ok": 2}
    for row in sorted(per_entry, key=lambda r: (order[r["verdict"]], -r["length"])):
        notes = "; ".join(filter(None, (row["defects"], row["risks"], row["flags"])))
        print("%-14s %s %3d %-9s %4d %-7s %s" % (
            row["answer"], row["direction"], row["length"], row["tier"],
            row["reference_score"], row["verdict"], notes))

    n = len(entries)
    n_hard = len(hard_defects)
    n_risky = len(risky)
    defect_words = {w for w, _ in hard_defects} | {w for w, _ in risky}
    recognizable = sum(
        1
        for e in entries
        if strip_accents(e["text"].lower()) in preferred_dict
        and strip_accents(e["text"].lower()) not in defect_words
    )
    xword_density = crosswordese_count / n if n else 0.0

    print("\n== fill summary ==")
    print(f"entries:            {n}")
    print(f"tier counts:        {dict(tier_counts)}")
    print(f"preferred:          {tier_counts.get('preferred', 0)} "
          f"(recognizable after defect removal: {recognizable})")
    print(f"hard defects:       {n_hard}")
    for w, ds in hard_defects:
        print(f"  - {w}: {'; '.join(ds)}")
    print(f"risky:              {n_risky}")
    for w, rs in risky:
        print(f"  - {w}: {'; '.join(rs)}")
    print(f"crosswordese:       {crosswordese_count} (density {xword_density:.3f})")
    spread_line = f"theme spread:       {sectors}/9 sectors with a preferred entry"
    if mean_pair is not None:
        spread_line += f"; mean pairwise Chebyshev {mean_pair:.2f}"
    if spread_expected is not None:
        spread_line += f"; expected under uniform {spread_expected:.2f}"
    if spread_ratio is not None:
        spread_line += f"; spread_ratio {spread_ratio:.3f} (<~0.8 = clumped)"
    else:
        spread_line += "; n<2, not measurable"
    print(spread_line)

    spread_term = 2 * spread_ratio if spread_ratio is not None else 0.0
    terms = [
        ("recognizable_preferred", float(recognizable)),
        ("-3*hard_defects", -3.0 * n_hard),
        ("-1*risky", -1.0 * n_risky),
        ("+2*spread_ratio", spread_term),
        ("-10*crosswordese_density", -10.0 * xword_density),
    ]
    score = sum(v for _, v in terms)
    print("\n== fill score ==")
    for name, value in terms:
        print(f"  {name:28s} {value:+.3f}")
    print(f"  {'FILL SCORE':28s} {score:+.3f}")

    if args.report:
        with args.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(per_entry[0]))
            writer.writeheader()
            writer.writerows(per_entry)
        print(f"\nreport written to {args.report}")

    return 1 if n_hard else 0


if __name__ == "__main__":
    sys.exit(main())
