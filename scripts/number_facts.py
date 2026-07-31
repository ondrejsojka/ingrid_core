#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Číslo z čísla (CLUES.md §7) — deterministický extraktor číselných faktů.

Dva regexové rodiny nad textem vydání:

  A) číslo + jednotka        „3,5 km", „212 000 ks", „14,5 milionu korun"
  B) číslo + podstatné jméno „33 bytů", „500 dronů", „1 600 diváků"

Žádný model. Regex definuje kandidátní rozsah, MorphoDiTa (tagger) ho jen
ověří a zlemmatizuje. §10 zakazuje volat generátor legend při hledání fillu,
takže tenhle skript běží **jednou za vydání** a výsledek se propíše do skóre
slovníku (`--output-dict`).

Šum, který §7 jmenuje, a jak se řeší:
  * zalomení sloupce rozbije slovo    -> de-hyphenace na konci sloupcového segmentu
  * zalomení rozbije `3,5 km` na `5 km` -> segment končící `číslice+,` se slepí
                                            s následujícím, který začíná číslicí
  * dvousloupcová sazba prokládá řádky -> text se před extrakcí přeskládá po
                                            sloupcích (shluky podle odsazení)
  * stránka                            -> `\\f` (form feed) = předěl stránky;
                                            číslo stránky = 1 + počet předchozích \\f
                                            (ověřeno proti 31 samostatným
                                            číslicovým řádkům v čísle 7–8/2026)

Výstupní tvar podle §7: {value, unit, subject, page} + `raw` (doslovný match)
a `score` (0–100, viz `--min-score`).

Druhý, důležitější artefakt (§7): seznam krátkých ohebných slov, která
přicházejí s hotovou dvanáctiznakovou nápovědou, protože nesou číselný fakt
(`tunel`, `lampa`, `jízda`, `byt`, `dron`, `nit`, `noc`, `les`). Ta jdou do
`--output-dict` se skóre 180.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TAGGER = (
    "/tmp/czech-morphodita/czech-morfflex2.1-pdtc2.0-250909/"
    "czech-morfflex2.1-pdtc2.0-250909.tagger"
)
CARRIER_SCORE = 180
CARRIER_MIN_LEN = 3
CARRIER_MAX_LEN = 10

# ---------------------------------------------------------------- lexikony --

# Lemmata jednotek, jak je vrací MorphoDiTa (km -> kilometr, ks -> kus,
# t -> tuna, MW -> megawatt, l -> litr, ha -> hektar ...).
UNIT_LEMMAS = {
    "kilometr", "metr", "centimetr", "milimetr", "decimetr", "míle",
    "hektar", "ar",
    "kilogram", "gram", "dekagram", "tuna", "kvintál",
    "litr", "hektolitr", "mililitr", "decilitr",
    "koruna", "Kč", "euro", "dolar",
    "procento", "promile", "stupeň", "Celsius",
    "watt", "kilowatt", "megawatt", "gigawatt", "kilowatthodina",
    "volt", "ampér", "decibel",
    "kus", "pár", "sada",
    "hodina", "minuta", "sekunda", "den", "léta", "rok", "týden", "měsíc",
    "století", "dekáda",
    "bit", "byte", "megabajt", "gigabajt", "pixel", "gigapixel",
}

# Násobitele: „14,5 milionu korun" -> jednotka „milionu korun".
MULTIPLIER_LEMMAS = {"tisíc", "milion", "miliarda", "bilion"}

# Jednotky, které samy o sobě (bez podmětu) znamenají program/otvírací dobu,
# ne fakt: „od 17 hod.".
CLOCK_LEMMAS = {"hodina"}

# Značky, na kterých se přestává hledat podmět.
STOP_TAG_PREFIXES = ("Z:", "VB", "Vp", "Vf", "Vi", "Vs", "Vc", "J,", "J^", "TT")
# Značky, které se při hledání podmětu přeskakují (přívlastky, číslovky).
SKIP_TAG_PREFIXES = ("AA", "AG", "AC", "Cl", "Cn", "Cz", "Ca", "C=", "Cr", "Cv", "P4", "PZ")

CZECH_WORD = re.compile(r"^[a-záčďéěíňóřšťúůýž]+$")

# ------------------------------------------------------------------ regexy --

# Rodina čísla: 1 234 (mezerou dělené tisíce, i NBSP / úzká mezera)
# nebo 3,5 / 98,80 (česká desetinná čárka — nesmí se rozpadnout na „5").
NUMBER_RE = re.compile(
    r"(?<![\w,.])"
    r"(\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+|\d+(?:,\d+)?)"
    r"(?![\w])"
)

# Druha varianta cisla: ceska slovni zakladni cislovka od „dva" vys
# („osm dekad", „tri sta lidi"). „jeden/jedna" je vynechany — je to spis
# neurcity clen nez fakt. Shodu jeste overuje znacka z taggeru (C*, ne
# radova).
WORD_NUMBER_RE = re.compile(
    r"(?<![\wáčďéěíňóřšťúůýž])("
    r"dv[aě]|dvou|dvěma|tři|tří|třem[ai]|čtyři|čtyř|čtyřm[ai]|"
    r"pět|pěti|šest|šesti|sedm|sedmi|osm|osmi|devět|devíti|deset|deseti|"
    r"(?:jede|dva|tři|čtr|pat|šest|sedm|osm|devate)náct[iy]?|"
    r"dvacet|dvaceti|třicet|třiceti|čtyřicet|čtyřiceti|"
    r"(?:pade|šede|sedmde|osmde|devade)sát[iy]?|"
    r"st[oaě]|set|stovk[ay]|tisíc[eů]?|"
    r"desítk[ay]|dvojic[ei]|trojic[ei]|stovek"
    r")(?![\wáčďéěíňóřšťúůýž])",
    re.IGNORECASE,
)
# Znacky slovnich cislovek, ktere bereme (zakladni, ne radove Cr / nasobne Cv).
WORD_NUMBER_TAGS = ("Cl", "Cn", "Cy", "Ca", "C}")

HYPHENS = ("-", "\u2010", "\u2011", "\u00ad")
# Souvislý úsek textu = běh neprázdných znaků oddělený 3+ mezerami (sloupec).
SEGMENT_RE = re.compile(r"\S(?:.*?\S)?(?=\s{3,}|$)")


@dataclass
class Fact:
    value: str
    unit: str
    subject: str
    page: int
    raw: str
    score: int
    line: int


# -------------------------------------------------------- reflow / cleanup --


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def paginate(text: str) -> list[tuple[int, int, str]]:
    """(page, line_no, line) — stránka = 1 + počet form feedů od začátku."""
    out: list[tuple[int, int, str]] = []
    page = 1
    for idx, line in enumerate(text.split("\n"), start=1):
        page += line.count("\f")
        out.append((page, idx, line.replace("\f", " ")))
    return out


def _columns(page_lines: list[tuple[int, str]], tol: int = 3) -> list[list[tuple[int, str]]]:
    """Rozdělí stránku na sloupce podle odsazení a vrátí je shora dolů."""
    pieces: list[tuple[int, int, str]] = []
    for line_no, line in page_lines:
        for m in SEGMENT_RE.finditer(line):
            pieces.append((m.start(), line_no, m.group()))
    if not pieces:
        return []
    anchor: dict[int, int] = {}
    current = None
    for x in sorted({p[0] for p in pieces}):
        if current is None or x - current > tol:
            current = x
        anchor[x] = current
    buckets: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for x, line_no, chunk in pieces:
        buckets[anchor[x]].append((line_no, chunk))
    return [sorted(buckets[x]) for x in sorted(buckets)]


def _dehyphenate(column: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Slepí zalomená slova a rozpůlená desetinná čísla uvnitř sloupce."""
    out: list[list] = []
    for line_no, chunk in column:
        chunk = chunk.strip()
        if not chunk:
            continue
        if out:
            prev = out[-1][1]
            if prev.endswith(HYPHENS) and chunk[:1].islower():
                out[-1][1] = prev[:-1] + chunk
                continue
            # „3," + „5 km" -> „3,5 km"
            if re.search(r"\d,$", prev) and chunk[:1].isdigit():
                out[-1][1] = prev + chunk
                continue
            if prev[-1:].isdigit() and re.match(r"^,\d", chunk):
                out[-1][1] = prev + chunk
                continue
        out.append([line_no, chunk])
    return [(ln, tx) for ln, tx in out]


def reflow(text: str) -> list[tuple[int, int, str]]:
    """Text -> [(page, line_no, segment)] po sloupcích, de-hyphenováno."""
    by_page: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for page, line_no, line in paginate(_norm(text)):
        by_page[page].append((line_no, line))
    segments: list[tuple[int, int, str]] = []
    for page in sorted(by_page):
        for column in _columns(by_page[page]):
            for line_no, chunk in _dehyphenate(column):
                segments.append((page, line_no, chunk))
    return segments


# ------------------------------------------------------------- morfologie --


class Morph:
    def __init__(self, tagger_path: str) -> None:
        from ufal.morphodita import (Tagger, Forms, TaggedLemmas,
                                     TaggedLemmasForms, TokenRanges)

        tagger = Tagger.load(tagger_path)
        if tagger is None:
            raise SystemExit(f"nelze nacist tagger: {tagger_path}")
        self._tagger = tagger
        self._morphology = tagger.getMorpho()
        self._tokenizer = tagger.newTokenizer()
        self._forms = Forms()
        self._lemmas = TaggedLemmas()
        self._paradigm = TaggedLemmasForms()
        self._ranges = TokenRanges()

    def tag(self, text: str) -> list[tuple[int, int, str, str, str]]:
        """[(start, length, form, lemma, tag)]"""
        self._tokenizer.setText(text)
        out = []
        while self._tokenizer.nextSentence(self._forms, self._ranges):
            self._tagger.tag(self._forms, self._lemmas)
            for i in range(len(self._forms)):
                out.append((
                    self._ranges[i].start,
                    self._ranges[i].length,
                    self._forms[i],
                    self._lemmas[i].lemma,
                    self._lemmas[i].tag,
                ))
        return out

    def is_noun_lemma(self, word: str) -> bool:
        """Ma slovo v MorfFlexu substantivni paradigma? (kontextovy tag ho
        u homonym jako `misto` prohlasi za predlozku, slovnik ne.)"""
        from ufal.morphodita import Morpho

        self._morphology.generate(word, "NN", Morpho.NO_GUESSER, self._paradigm)
        return len(self._paradigm) > 0


def lemma_base(lemma: str) -> str:
    """`byt_^(místo_k_bydlení)` -> `byt`, `milion\u00601000000` -> `milion`."""
    return re.split(r"[_`\-]", lemma, maxsplit=1)[0]


# -------------------------------------------------------------- extrakce ---


def _score(value: str, unit: str, subject: str, unit_lemma: str) -> int:
    score = 0
    if subject:
        score += 40
        if CARRIER_MIN_LEN <= len(subject) <= CARRIER_MAX_LEN:
            score += 10
    if unit:
        score += 25
    digits = re.sub(r"\D", "", value)
    if "," in value or len(digits) >= 3:
        score += 15
    if unit_lemma in CLOCK_LEMMAS and not subject:
        score -= 30  # otvírací doba / program, ne fakt
    return max(0, min(100, score))


def extract(segments, morph: Morph) -> list[Fact]:
    facts: list[Fact] = []
    for page, line_no, segment in segments:
        tokens = morph.tag(segment)
        if not tokens:
            continue
        at_start = {tok[0]: i for i, tok in enumerate(tokens)}
        matches = [(m.start(), m.end(), m.group(1), True)
                   for m in NUMBER_RE.finditer(segment)]
        matches += [(m.start(), m.end(), m.group(1), False)
                    for m in WORD_NUMBER_RE.finditer(segment)]
        matches.sort()

        for start, stop, value, is_digit in matches:
            idx = at_start.get(start)
            if idx is None:
                continue
            tail = segment[stop:stop + 8]
            head = segment[max(0, start - 8):start]

            if is_digit:
                value = re.sub(r"[\u00a0\u202f\u2009]", " ", value)
                # --- šum: datum, čas, zlomek, PSČ / tabulka ---
                if re.match(r"^\s*\.\s*\d", tail) or re.match(r"^\s*[./]\s*$", tail):
                    continue      # 4. 7.  /  1/3
                if re.search(r"\d[.:/]$", head):
                    continue      # 11.06 | 10:43 | .../m2
                if tail[:1] == "/" or head[-1:] == "/":
                    continue
                if idx and tokens[idx - 1][4].startswith("C="):
                    continue      # „602 00 Brno" — sousedící číslo = adresa/tabulka
            else:
                # slovní číslovka: musí to tagger potvrdit jako základní číslovku
                if tokens[idx][4][:2] not in WORD_NUMBER_TAGS:
                    continue
                if idx and tokens[idx - 1][4].startswith("C"):
                    continue      # „2 tisíce lidí" už pokryl číslicový match
                value = value.lower()

            # konec vlastního čísla (skupinové tisíce mohou být víc tokenů)
            j = idx + 1
            while j < len(tokens) and tokens[j][0] < stop:
                j += 1
            if is_digit and j < len(tokens) and tokens[j][4].startswith("C=") \
                    and tokens[j][0] <= stop + 1:
                continue          # další samostatné číslo hned za tímhle

            unit_forms: list[str] = []
            unit_lemma = ""
            subject = ""
            end = stop
            k, steps, crossed = j, 0, False
            while k < len(tokens) and steps < 6:
                _, length, form, lemma, tag = tokens[k]
                base = lemma_base(lemma)
                if form == "%":
                    unit_forms.append("%")
                    unit_lemma = unit_lemma or "procento"
                    end = tokens[k][0] + length
                    k += 1
                    steps += 1
                    continue
                if tag[:2] in STOP_TAG_PREFIXES:
                    break
                # samostatné další číslo = začíná nový fakt („2 t do 30 km")
                if tag.startswith("C=") and tokens[k][0] > 0 \
                        and segment[tokens[k][0] - 1].isspace():
                    break
                # „m2" — exponent přilepený k jednotce bez mezery
                if tag.startswith("C=") and unit_forms:
                    unit_forms[-1] += form
                    end = tokens[k][0] + length
                    k += 1
                    steps += 1
                    continue
                if base in MULTIPLIER_LEMMAS and not unit_lemma:
                    unit_forms.append(form)
                    end = tokens[k][0] + length
                    k += 1
                    steps += 1
                    continue
                if not unit_lemma and base in UNIT_LEMMAS:
                    unit_forms.append(form)
                    unit_lemma = base
                    end = tokens[k][0] + length
                    k += 1
                    steps += 1
                    continue
                if tag.startswith("NN"):
                    subject = base
                    end = tokens[k][0] + length
                    break
                # „40 % z celkového počtu" — jednu předložku za jednotkou
                # přeskočíme, podmět je až za ní
                if tag.startswith("RR") and unit_lemma and not crossed:
                    crossed = True
                    k += 1
                    steps += 1
                    continue
                if tag[:2] in SKIP_TAG_PREFIXES:
                    k += 1
                    steps += 1
                    continue
                break

            unit = " ".join(unit_forms).strip(" .,")
            if not unit_lemma and unit_forms:
                unit_lemma = lemma_base(unit_forms[0])
            if not unit and not subject:
                continue
            # holý letopočet bez jednotky není fakt, je to datace
            if not unit and re.fullmatch(r"1[5-9]\d\d|20\d\d", value):
                continue
            if subject and not CZECH_WORD.match(subject.lower()):
                subject = ""
                if not unit:
                    continue

            facts.append(Fact(
                value=value,
                unit=unit,
                subject=subject.lower(),
                page=page,
                raw=" ".join(segment[start:end].split()),
                score=_score(value, unit, subject, unit_lemma),
                line=line_no,
            ))
    return facts


def dedupe(facts: list[Fact]) -> list[Fact]:
    seen: set[tuple] = set()
    out: list[Fact] = []
    for fact in facts:
        key = (fact.value, fact.unit, fact.subject, fact.page)
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    out.sort(key=lambda f: (f.page, f.line, f.value))
    return out


def carriers(facts: list[Fact], morph: Morph) -> list[str]:
    """Krátká ohebná substantiva, která nesou fakt -> hotová legenda zdarma."""
    words: set[str] = set()
    for fact in facts:
        word = fact.subject
        if not word or not CZECH_WORD.match(word):
            continue
        if not (CARRIER_MIN_LEN <= len(word) <= CARRIER_MAX_LEN):
            continue
        if not morph.is_noun_lemma(word):
            continue
        words.add(_norm(word))
    return sorted(words)


# ------------------------------------------------------------------ main ---


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="CLUES.md §7 — deterministicka banka cisel z vydani.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Vystup: CSV faktu {value,unit,subject,page,raw,score} a .dict "
               "kratkych nosicu faktu (word;score).",
    )
    ap.add_argument("inputs", nargs="+", type=Path, help="textove soubory vydani (UTF-8)")
    ap.add_argument("--output-csv", type=Path, required=True, help="cesta k CSV s fakty")
    ap.add_argument("--output-dict", type=Path, required=True, help="cesta k .dict nosicu")
    ap.add_argument("--min-score", type=int, default=0,
                    help="minimalni skore faktu (0-100), default 0")
    ap.add_argument("--carrier-score", type=int, default=CARRIER_SCORE,
                    help=f"skore zapsane do .dict, default {CARRIER_SCORE}")
    ap.add_argument("--tagger", default=DEFAULT_TAGGER, help="cesta k MorphoDiTa taggeru")
    args = ap.parse_args(argv)

    morph = Morph(args.tagger)

    segments: list[tuple[int, int, str]] = []
    for path in args.inputs:
        segments.extend(reflow(path.read_text(encoding="utf-8")))

    facts = dedupe(extract(segments, morph))
    kept = [f for f in facts if f.score >= args.min_score]

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        # `page` je skutecne cislo stranky (form feed = predel stranky, overeno
        # proti samostatnym cislicovym radkum); `line` je radek zdrojoveho textu.
        writer.writerow(["value", "unit", "subject", "page", "raw", "score", "line"])
        for fact in kept:
            writer.writerow([fact.value, fact.unit, fact.subject,
                             fact.page, fact.raw, fact.score, fact.line])

    words = carriers(kept, morph)
    args.output_dict.parent.mkdir(parents=True, exist_ok=True)
    args.output_dict.write_text(
        "".join(f"{w};{args.carrier_score}\n" for w in words), encoding="utf-8")

    print(f"fakty:   {len(kept)} / {len(facts)} (min-score {args.min_score}) -> {args.output_csv}")
    print(f"nosice:  {len(words)} slov (score {args.carrier_score}) -> {args.output_dict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
