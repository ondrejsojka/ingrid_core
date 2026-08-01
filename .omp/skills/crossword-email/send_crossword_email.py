#!/usr/bin/env python3
"""Render a crossword fill plus clue set as an HTML email and send it with Resend.

See SKILL.md next to this file. The Resend key is send-only, the sender must be
onboarding@resend.dev unless a domain is verified, and the request must go through
curl -- urllib gets a Cloudflare 403 (error code 1010).
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from collections import Counter

CSS = """
body{margin:0;background:#f7f4ee;color:#202020;font:16px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:860px;margin:auto;padding:32px 22px 56px}
h1,h2,h3{font-family:Georgia,serif;line-height:1.25;margin:1.6em 0 .5em}
h1{margin-top:0;font-size:27px}h2{font-size:21px;border-top:1px solid #ddd8cc;padding-top:18px}h3{font-size:17px}
.muted,.mut{color:#6d675c}.small{font-size:13.5px}
.pill{display:inline-block;background:#2f5d50;color:#fff;border-radius:11px;padding:2px 10px;font-size:13px}
.pill code{background:rgba(255,255,255,.18);color:#fff}
code{background:#efeade;padding:1px 4px;border-radius:3px;font-size:13.5px}
table.cw{border-collapse:collapse;margin:14px 0}
table.cw td{width:30px;height:30px;border:1px solid #b9b2a2;text-align:center;vertical-align:middle;
  font:600 15px/1 Georgia,serif;position:relative;background:#fff}
table.cw td.b{background:#26231d;border-color:#26231d}
table.cw td.t{background:#f6e6b4}
.taj{font:600 19px/1.5 Georgia,serif;letter-spacing:.22em}
.tajbox{border:1px solid #ddd8cc;background:#fffdf6;padding:12px 14px;border-radius:5px;margin:12px 0}
table.cw .n{position:absolute;top:1px;left:2px;font:400 8.5px/1 system-ui,sans-serif;color:#6d675c}
ol.cl{margin:0;padding-left:0;list-style:none}
ol.cl li{padding:1px 0;font-size:14.5px}
.ans{color:#9a9284;font-size:12.5px}
.cols{display:flex;gap:28px;flex-wrap:wrap}.cols>div{flex:1;min-width:250px}
pre{background:#efeade;padding:12px;border-radius:5px;overflow-x:auto;font-size:13px}
table.d{border-collapse:collapse;font-size:14px;margin:10px 0}
table.d th,table.d td{border:1px solid #ddd8cc;padding:5px 9px;text-align:left}
table.d th{background:#efeade;font-weight:600}
"""

BAND_NAMES = {"S": "slovník", "O": "obraz", "H": "hra"}


def read_grid(path):
    rows = [ln.rstrip("\n") for ln in open(path, encoding="utf-8") if ln.strip()]
    width = max(len(r) for r in rows)
    if any(len(r) != width for r in rows):
        sys.exit(f"{path}: ragged grid, rows are {sorted({len(r) for r in rows})}")
    return rows


def number(rows):
    """Standard crossword numbering. Returns (numbers, across, down)."""
    h, w = len(rows), len(rows[0])
    nums = [[0] * w for _ in range(h)]
    across, down, n = [], [], 0
    for r in range(h):
        for c in range(w):
            if rows[r][c] == "#":
                continue
            starts_a = (c == 0 or rows[r][c - 1] == "#") and c + 1 < w and rows[r][c + 1] != "#"
            starts_d = (r == 0 or rows[r - 1][c] == "#") and r + 1 < h and rows[r + 1][c] != "#"
            if not (starts_a or starts_d):
                continue
            n += 1
            nums[r][c] = n
            if starts_a:
                cc = c
                word = ""
                while cc < w and rows[r][cc] != "#":
                    word += rows[r][cc]
                    cc += 1
                across.append((n, word))
            if starts_d:
                rr = r
                word = ""
                while rr < h and rows[rr][c] != "#":
                    word += rows[rr][c]
                    rr += 1
                down.append((n, word))
    return nums, across, down


def read_clues(path):
    clues = {}
    for lineno, ln in enumerate(open(path, encoding="utf-8"), 1):
        ln = ln.rstrip("\n")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) < 2:
            sys.exit(f"{path}:{lineno}: need at least answer<TAB>clue")
        answer, clue = parts[0].strip().lower(), parts[1].strip()
        band = parts[2].strip() if len(parts) > 2 else ""
        shape = parts[3].strip() if len(parts) > 3 else ""
        clues[answer] = (clue, band, shape)
    return clues


def check(entries, clues):
    """Returns (live answers, warnings). Mirrors the CLUES.md checker."""
    live, warn = [], []
    for _, word in entries:
        entry = clues.get(word)
        if not entry or entry[0] in ("", "-"):
            warn.append(f"no clue: {word}")
            continue
        clue = entry[0]
        live.append(word)
        if len(clue) > 34:
            warn.append(f"over box budget ({len(clue)}): {word} — {clue}")
        stem = word[:4]
        if len(word) > 3 and stem in clue.lower():
            warn.append(f"answer root leaks: {word} — {clue}")
    return live, warn


def locate(rows, nums, across, down, answers):
    """Map each tajenka answer to its cells. Answers are matched in the order given;
    an answer occurring twice in the fill is an error, because the shading would be
    ambiguous and the solver could not tell which copy to read."""
    h, w = len(rows), len(rows[0])
    spans = []
    for want in answers:
        hits = []
        for r in range(h):
            for c in range(w):
                if rows[r][c] == "#":
                    continue
                if (c == 0 or rows[r][c - 1] == "#") and "".join(
                    rows[r][c:c + len(want)]
                ) == want and (c + len(want) == w or rows[r][c + len(want)] == "#"):
                    hits.append([(r, c + i) for i in range(len(want))])
                if (r == 0 or rows[r - 1][c] == "#") and "".join(
                    rows[rr][c] for rr in range(r, min(h, r + len(want)))
                ) == want and (r + len(want) == h or rows[r + len(want)][c] == "#"):
                    hits.append([(r + i, c) for i in range(len(want))])
        if not hits:
            sys.exit(f"tajenka: {want!r} is not an entry in the fill")
        if len(hits) > 1:
            sys.exit(f"tajenka: {want!r} occurs {len(hits)} times in the fill, shading would be ambiguous")
        num = nums[hits[0][0][0]][hits[0][0][1]] if nums else 0
        spans.append((want, hits[0], num))
    return spans


def render(rows, nums, across, down, clues, title, headline, intro, tajenka=()):
    shaded = {cell for _, cells, _ in tajenka for cell in cells}

    def cell(r, c, solved):
        ch = rows[r][c]
        if ch == "#":
            return '<td class="b"></td>'
        klass = ' class="t"' if (r, c) in shaded else ""
        tag = f'<span class="n">{nums[r][c]}</span>' if nums[r][c] else ""
        return f"<td{klass}>{tag}{ch.upper() if solved else ''}</td>"

    def table(solved):
        body = "".join(
            "<tr>" + "".join(cell(r, c, solved) for c in range(len(rows[0]))) + "</tr>"
            for r in range(len(rows))
        )
        return f'<table class="cw">{body}</table>'

    def clue_list(items):
        out = []
        for n, word in items:
            entry = clues.get(word)
            text = (
                f'<i class="mut">— vada fillu</i>'
                if not entry or entry[0] in ("", "-")
                else entry[0]
            )
            out.append(f'<li><b>{n}.</b> {text} <span class="ans">({len(word)})</span></li>')
        return '<ol class="cl">' + "".join(out) + "</ol>"

    def key_list(items):
        out = []
        for n, word in items:
            entry = clues.get(word)
            tags = " · ".join(t for t in (entry[1], entry[2]) if t) if entry else "—"
            out.append(
                f'<li><b>{n}.</b> {word.upper()} <span class="mut">· {tags or "—"}</span></li>'
            )
        return '<ol class="cl">' + "".join(out) + "</ol>"

    live = [w for _, w in across + down if clues.get(w) and clues[w][0] not in ("", "-")]
    lengths = [len(clues[w][0]) for w in live]
    bands = Counter(clues[w][1] for w in live if clues[w][1])
    total = sum(bands.values()) or 1
    band_rows = "".join(
        f"<tr><td>{b} — {BAND_NAMES.get(b, b)}</td><td>{k}</td><td>{round(100 * k / total)} %</td></tr>"
        for b, k in sorted(bands.items())
    )
    stats = (
        f"<table class='d'><thead><tr><th>ukazatel</th><th>hodnota</th><th></th></tr></thead><tbody>"
        f"<tr><td>hesel v mřížce</td><td>{len(across) + len(down)}</td><td></td></tr>"
        f"<tr><td>legend</td><td>{len(live)}</td><td></td></tr>"
        f"<tr><td>medián délky legendy</td><td>{statistics.median(lengths):g}</td><td>cíl ≤ 15</td></tr>"
        f"<tr><td>maximum délky</td><td>{max(lengths)}</td><td>strop krabičky 34</td></tr>"
        f"{band_rows}</tbody></table>"
    )

    if tajenka:
        parts = " ".join(
            f'<b>{i}.</b> <span class="taj">{" ".join(w.upper())}</span>'
            for i, (w, _, _) in enumerate(tajenka, 1)
        )
        numbers = ", ".join(str(n) for _, _, n in tajenka)
        prompt = (
            f'<div class="tajbox"><b>Tajenka</b> — žlutá pole, hesla {numbers}. '
            f'Vylušti je a pošli znění tajenky.</div>'
        )
        answer = f'<div class="tajbox"><b>Tajenka</b>: {parts}</div>'
    else:
        prompt = answer = ""

    return f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><main>
<h1>{headline}</h1>
{intro}
<h2>Mřížka</h2>
{prompt}
{table(False)}
<div class="cols"><div><h3>Vodorovně</h3>{clue_list(across)}</div>
<div><h3>Svisle</h3>{clue_list(down)}</div></div>
<h2>Řešení</h2>
{table(True)}
{answer}
<h2>Klíč s pásmy a tvary</h2>
<div class="cols"><div><h3>Vodorovně</h3>{key_list(across)}</div>
<div><h3>Svisle</h3>{key_list(down)}</div></div>
<h2>Čísla</h2>
{stats}
</main></body></html>"""


def render_swedish(rows, clues, title, headline, intro, tajenka=()):
    h, w = len(rows), len(rows[0])

    # Derive across slots >= 3
    across_slots = []
    for r in range(h):
        c = 0
        while c < w:
            if rows[r][c] != "#":
                start_c = c
                while c < w and rows[r][c] != "#":
                    c += 1
                word = "".join(rows[r][start_c:c])
                if len(word) >= 3:
                    across_slots.append((r, start_c, word))
            else:
                c += 1

    # Derive down slots >= 3 (ordered top-to-bottom, left-to-right)
    down_slots = []
    for r in range(h):
        for c in range(w):
            if rows[r][c] != "#" and (r == 0 or rows[r - 1][c] == "#"):
                end_r = r
                while end_r < h and rows[end_r][c] != "#":
                    end_r += 1
                word = "".join(rows[rr][c] for rr in range(r, end_r))
                if len(word) >= 3:
                    down_slots.append((r, c, word))

    # Resolve legend cells
    legend_map = {}  # (r, c) -> {'across': word, 'down': word}

    for r, c, word in across_slots:
        lr, lc = r, c - 1
        if lr < 0 or lr >= h or lc < 0 or lc >= w:
            sys.exit(f"legend cell ({lr}, {lc}) for across answer '{word}' at ({r}, {c}) is off-grid")
        if rows[lr][lc] != "#":
            sys.exit(f"legend cell ({lr}, {lc}) for across answer '{word}' at ({r}, {c}) is not '#'")
        if (lr, lc) not in legend_map:
            legend_map[(lr, lc)] = {'across': None, 'down': None}
        if legend_map[(lr, lc)]['across'] is not None:
            sys.exit(f"legend cell ({lr}, {lc}) has multiple across legends: '{legend_map[(lr, lc)]['across']}' and '{word}'")
        legend_map[(lr, lc)]['across'] = word

    for r, c, word in down_slots:
        lr, lc = r - 1, c
        if lr < 0 or lr >= h or lc < 0 or lc >= w:
            sys.exit(f"legend cell ({lr}, {lc}) for down answer '{word}' at ({r}, {c}) is off-grid")
        if rows[lr][lc] != "#":
            sys.exit(f"legend cell ({lr}, {lc}) for down answer '{word}' at ({r}, {c}) is not '#'")
        if (lr, lc) not in legend_map:
            legend_map[(lr, lc)] = {'across': None, 'down': None}
        if legend_map[(lr, lc)]['down'] is not None:
            sys.exit(f"legend cell ({lr}, {lc}) has multiple down legends: '{legend_map[(lr, lc)]['down']}' and '{word}'")
        legend_map[(lr, lc)]['down'] = word

    shaded = {cell for _, cells, _ in tajenka for cell in cells}

    def render_legend_cell_content(r, c):
        entry = legend_map.get((r, c))
        if not entry:
            return ""
        a_word = entry['across']
        d_word = entry['down']
        parts = []
        if a_word:
            clue_entry = clues.get(a_word)
            clue_text = clue_entry[0] if clue_entry and clue_entry[0] not in ("", "-") else "— VADA FILLU"
            parts.append(f'<div class="leg-a">&#9654; {clue_text.upper()}</div>')
        if d_word:
            clue_entry = clues.get(d_word)
            clue_text = clue_entry[0] if clue_entry and clue_entry[0] not in ("", "-") else "— VADA FILLU"
            parts.append(f'<div class="leg-d">&#9660; {clue_text.upper()}</div>')
        if len(parts) == 2:
            return f'{parts[0]}<div class="leg-hr"></div>{parts[1]}'
        elif len(parts) == 1:
            return parts[0]
        return ""

    def cell_puzzle(r, c):
        if rows[r][c] == "#":
            content = render_legend_cell_content(r, c)
            if content:
                return f'<td class="leg">{content}</td>'
            return '<td class="b"></td>'
        else:
            klass = ' class="a t"' if (r, c) in shaded else ' class="a"'
            return f'<td{klass}></td>'

    def cell_solution(r, c):
        if rows[r][c] == "#":
            return '<td class="b"></td>'
        else:
            klass = ' class="a t"' if (r, c) in shaded else ' class="a"'
            return f'<td{klass}>{rows[r][c].upper()}</td>'

    def table_sw(solved):
        fn = cell_solution if solved else cell_puzzle
        body = "".join(
            "<tr>" + "".join(fn(r, c) for c in range(w)) + "</tr>"
            for r in range(h)
        )
        return f'<table class="cw sw">{body}</table>'

    def key_list(items):
        out = []
        for i, (_, _, word) in enumerate(items, 1):
            entry = clues.get(word)
            tags = " · ".join(t for t in (entry[1], entry[2]) if t) if entry else "—"
            out.append(
                f'<li><b>{i}.</b> {word.upper()} <span class="mut">· {tags or "—"}</span></li>'
            )
        return '<ol class="cl">' + "".join(out) + "</ol>"

    live = [w for _, _, w in across_slots + down_slots if clues.get(w) and clues[w][0] not in ("", "-")]
    lengths = [len(clues[w][0]) for w in live]
    bands = Counter(clues[w][1] for w in live if clues[w][1])
    total = sum(bands.values()) or 1
    band_rows = "".join(
        f"<tr><td>{b} — {BAND_NAMES.get(b, b)}</td><td>{k}</td><td>{round(100 * k / total)} %</td></tr>"
        for b, k in sorted(bands.items())
    )
    stats = (
        f"<table class='d'><thead><tr><th>ukazatel</th><th>hodnota</th><th></th></tr></thead><tbody>"
        f"<tr><td>hesel v mřížce</td><td>{len(across_slots) + len(down_slots)}</td><td></td></tr>"
        f"<tr><td>legend</td><td>{len(live)}</td><td></td></tr>"
        f"<tr><td>medián délky legendy</td><td>{statistics.median(lengths):g}</td><td>cíl ≤ 15</td></tr>"
        f"<tr><td>maximum délky</td><td>{max(lengths)}</td><td>strop krabičky 34</td></tr>"
        f"{band_rows}</tbody></table>"
    )

    if tajenka:
        # A one-part tajenka is not "1." of anything; numbering it reads as a bug.
        parts = " ".join(
            (f'<b>{i}.</b> ' if len(tajenka) > 1 else "")
            + f'<span class="taj">{" ".join(w.upper())}</span>'
            for i, (w, _, _) in enumerate(tajenka, 1)
        )
        prompt = '<div class="tajbox"><b>Tajenka</b> — žlutá pole. Vylušti je a pošli znění tajenky.</div>'
        answer = f'<div class="tajbox"><b>Tajenka</b>: {parts}</div>'
    else:
        prompt = answer = ""

    css_sw = """
body{margin:0;background:#f7f4ee;color:#202020;font:16px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:1060px;margin:auto;padding:32px 22px 56px}
h1,h2,h3{font-family:Georgia,serif;line-height:1.25;margin:1.6em 0 .5em}
h1{margin-top:0;font-size:27px}h2{font-size:21px;border-top:1px solid #ddd8cc;padding-top:18px}h3{font-size:17px}
.muted,.mut{color:#6d675c}.small{font-size:13.5px}
.pill{display:inline-block;background:#2f5d50;color:#fff;border-radius:11px;padding:2px 10px;font-size:13px}
.pill code{background:rgba(255,255,255,.18);color:#fff}
code{background:#efeade;padding:1px 4px;border-radius:3px;font-size:13.5px}
table.cw{border-collapse:collapse;margin:14px auto;table-layout:fixed}
table.cw td{width:68px;height:68px;min-width:68px;max-width:68px;box-sizing:border-box;border:1px solid #b9b2a2;
  text-align:center;vertical-align:middle;padding:1px 2px;background:#fff;overflow:hidden;
  font:600 8.5px/1.05 system-ui,-apple-system,sans-serif;word-break:break-word;overflow-wrap:anywhere;hyphens:auto;position:relative}
table.cw td.b{background:#26231d;border-color:#26231d}
table.cw td.t{background:#f6e6b4}
table.cw td.a{font:600 22px/1 Georgia,serif}
.leg-a,.leg-d{color:#111}
.leg-hr{border-top:1px solid #b9b2a2;margin:1px 0;height:0}
.taj{font:600 19px/1.5 Georgia,serif;letter-spacing:.22em}
.tajbox{border:1px solid #ddd8cc;background:#fffdf6;padding:12px 14px;border-radius:5px;margin:12px 0}
ol.cl{margin:0;padding-left:0;list-style:none}
ol.cl li{padding:1px 0;font-size:14.5px}
.ans{color:#9a9284;font-size:12.5px}
.cols{display:flex;gap:28px;flex-wrap:wrap}.cols>div{flex:1;min-width:250px}
pre{background:#efeade;padding:12px;border-radius:5px;overflow-x:auto;font-size:13px}
table.d{border-collapse:collapse;font-size:14px;margin:10px 0}
table.d th,table.d td{border:1px solid #ddd8cc;padding:5px 9px;text-align:left}
table.d th{background:#efeade;font-weight:600}
"""

    return f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{css_sw}</style></head>
<body><main>
<h1>{headline}</h1>
{intro}
<h2>Mřížka</h2>
{prompt}
{table_sw(False)}
<h2>Řešení</h2>
{table_sw(True)}
{answer}
<h2>Klíč s pásmy a tvary</h2>
<div class="cols"><div><h3>Vodorovně</h3>{key_list(across_slots)}</div>
<div><h3>Svisle</h3>{key_list(down_slots)}</div></div>
<h2>Čísla</h2>
{stats}
</main></body></html>"""

def send(subject, html, to, sender, key):
    payload = {
        "from": sender,
        "to": to,
        "subject": subject,
        "html": html,
        "text": "Tento e-mail je v HTML: mřížka, legendy, řešení a klíč.",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
        path = fh.name
    try:
        # curl, not urllib: api.resend.com answers urllib with 403 error code 1010.
        proc = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST", "https://api.resend.com/emails",
             "-H", f"Authorization: Bearer {key}", "-H", "Content-Type: application/json",
             "--data-binary", f"@{path}"],
            capture_output=True, text=True, check=True,
        )
    finally:
        os.unlink(path)
    body, _, status = proc.stdout.rpartition("\n")
    if status.strip() != "200":
        sys.exit(f"resend failed: HTTP {status.strip()} {body}")
    return json.loads(body)


def load_key(env_path):
    for ln in open(os.path.expanduser(env_path), encoding="utf-8"):
        if ln.startswith("RESEND_API_KEY"):
            return ln.split("=", 1)[1].strip()
    sys.exit(f"RESEND_API_KEY not found in {env_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fill", required=True, help="grid file, # for blocks")
    ap.add_argument("--clues", required=True, help="TSV: answer<TAB>clue[<TAB>band<TAB>shape]")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--to", action="append", default=[])
    ap.add_argument("--headline", help="H1 text; defaults to the subject")
    ap.add_argument("--intro", help="file with an HTML fragment placed under the H1")
    ap.add_argument("--out", help="also write the HTML here")
    ap.add_argument("--from-address", default="Ingrid <onboarding@resend.dev>")
    ap.add_argument("--env", default="~/.env")
    ap.add_argument("--dry-run", action="store_true", help="render only, do not send")
    ap.add_argument(
        "--layout",
        choices=["american", "swedish"],
        default="american",
        help="grid layout: american (default) or swedish",
    )
    ap.add_argument(
        "--tajenka",
        help="comma-separated answers, in reading order, that spell the hidden phrase; "
        "their cells are shaded and the phrase is printed with the solution",
    )
    args = ap.parse_args()

    rows = read_grid(args.fill)
    clues = read_clues(args.clues)

    if args.layout == "american":
        nums, across, down = number(rows)
        _, warnings = check(across + down, clues)
        for w in warnings:
            print(f"warn: {w}", file=sys.stderr)

        tajenka = ()
        if args.tajenka:
            wanted = [a.strip().lower() for a in args.tajenka.split(",") if a.strip()]
            tajenka = locate(rows, nums, across, down, wanted)
            print("tajenka: " + " ".join(w.upper() for w, _, _ in tajenka))

        intro = open(args.intro, encoding="utf-8").read() if args.intro else ""
        html = render(rows, nums, across, down, clues, args.subject,
                      args.headline or args.subject, intro, tajenka)
    else:
        # derive slots for warning checks
        h, w = len(rows), len(rows[0])
        across_slots, down_slots = [], []
        for r in range(h):
            c = 0
            while c < w:
                if rows[r][c] != "#":
                    start_c = c
                    while c < w and rows[r][c] != "#":
                        c += 1
                    word = "".join(rows[r][start_c:c])
                    if len(word) >= 3:
                        across_slots.append((0, word))
                else:
                    c += 1
        for r in range(h):
            for c in range(w):
                if rows[r][c] != "#" and (r == 0 or rows[r - 1][c] == "#"):
                    end_r = r
                    while end_r < h and rows[end_r][c] != "#":
                        end_r += 1
                    word = "".join(rows[rr][c] for rr in range(r, end_r))
                    if len(word) >= 3:
                        down_slots.append((0, word))

        _, warnings = check(across_slots + down_slots, clues)
        for w in warnings:
            print(f"warn: {w}", file=sys.stderr)

        tajenka = ()
        if args.tajenka:
            wanted = [a.strip().lower() for a in args.tajenka.split(",") if a.strip()]
            tajenka = locate(rows, None, None, None, wanted)
            print("tajenka: " + " ".join(w.upper() for w, _, _ in tajenka))

        intro = open(args.intro, encoding="utf-8").read() if args.intro else ""
        html = render_swedish(rows, clues, args.subject,
                             args.headline or args.subject, intro, tajenka)

    if args.out:
        open(args.out, "w", encoding="utf-8").write(html)
        print(f"wrote {args.out} ({len(html)} bytes)")
    if args.dry_run:
        return
    if not args.to:
        sys.exit("--to is required unless --dry-run")
    print(send(args.subject, html, args.to, args.from_address, load_key(args.env)))


if __name__ == "__main__":
    main()
