#!/usr/bin/env python3
"""Render puzzle JSON into a single self-contained HTML file.

CLI Usage:
    render_puzzle.py --in puzzle.json --out out.html --mode {interactive,review} [--intro FILE]
"""

import argparse
import base64
import json
import sys
import unicodedata
from pathlib import Path


def obfuscate(text: str, key: str = "Karolina2026") -> str:
    raw_bytes = text.encode("utf-8")
    key_bytes = key.encode("utf-8")
    xor_bytes = bytes([b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(raw_bytes)])
    return base64.b64encode(xor_bytes).decode("ascii")


def fold_diacritics(text: str) -> str:
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").upper()


SCALE = 1.0  # set from --scale; shrinks cells and legend text for very large grids


def _px(value: str) -> str:
    return f"{float(value.rstrip('px')) * SCALE:g}px"


def get_legend_font_size(text: str, is_dual: bool):
    L = len(text)
    if not is_dual:
        if L <= 10:
            return "11px", "1.15"
        elif L <= 16:
            return "10px", "1.12"
        elif L <= 22:
            return "9px", "1.1"
        elif L <= 28:
            return "8px", "1.08"
        elif L <= 36:
            return "7.5px", "1.05"
        else:
            return "7px", "1.02"
    else:
        if L <= 10:
            return "9px", "1.08"
        elif L <= 16:
            return "8.2px", "1.05"
        elif L <= 24:
            return "7.5px", "1.02"
        else:
            return "6.8px", "1.0"


def render_legend_cell(entries: list) -> str:
    # entries is a list of entry dicts for this legend cell (up to 1 across and 1 down)
    is_dual = len(entries) > 1
    # Sort so Across is first, Down is second
    sorted_entries = sorted(entries, key=lambda e: 0 if e["dir"] == "A" else 1)
    
    parts = []
    for e in sorted_entries:
        prefix = "▶ " if e["dir"] == "A" else "▼ "
        clue_upper = e["clue"].upper()
        full_text = prefix + clue_upper
        fs, lh = get_legend_font_size(full_text, is_dual)
        entry_id = e["id"]
        direction = e["dir"]
        parts.append(
            f'<div class="clue-box clue-{direction.lower()}" '
            f'data-entry-id="{entry_id}" data-dir="{direction}" '
            f'style="font-size: {fs}; line-height: {lh};">'
            f'{prefix}{clue_upper}'
            f'</div>'
        )
    
    return "".join(parts)


def build_html(puzzle: dict, mode: str, intro_html: str = "") -> str:
    title = puzzle.get("title", "Křížovka")
    height, width = puzzle["size"]
    grid = puzzle["grid"]
    entries = puzzle.get("entries", [])
    tajenka = puzzle.get("tajenka")
    stats = puzzle.get("stats", {})

    # Map legends to entries
    legend_map = {} # (r, c) -> list of entry dicts
    # Map (r, c) to list of entry dicts for answer cells
    cell_entries_map = {} # (r, c) -> list of entry dicts
    
    prefill = {tuple(x) for x in puzzle.get("prefill", [])}

    for e in entries:
        # A prefilled entry is written into the grid, so it gets no legend and is not
        # asked. Its letters are simply already there.
        if not e.get("prefilled"):
            lr, lc = e["legend"]
            legend_map.setdefault((lr, lc), []).append(e)
        
        # Calculate answer cell coordinates
        r, c = e["r"], e["c"]
        length = e["len"]
        dr = 1 if e["dir"] == "D" else 0
        dc = 1 if e["dir"] == "A" else 0
        for i in range(length):
            cr, cc = r + i * dr, c + i * dc
            cell_entries_map.setdefault((cr, cc), []).append(e)

    # Map tajenka cells
    tajenka_cells_set = {} # (r, c) -> 1-based index
    if tajenka and "cells" in tajenka:
        for idx, (tr, tc) in enumerate(tajenka["cells"], 1):
            tajenka_cells_set[(tr, tc)] = idx

    # Build solution matrix and obfuscate
    sol_matrix = []
    for r in range(height):
        row_str = ""
        for c in range(width):
            ch = grid[r][c]
            row_str += ch if ch != "#" else " "
        sol_matrix.append(row_str)
    
    solution_flat = "\n".join(sol_matrix)
    obfuscated_solution = obfuscate(solution_flat)

    # Calculate total answer cells
    total_answer_cells = sum(
        1 for r in range(height) for c in range(width)
        if grid[r][c] != "#" and (r, c) not in prefill
    )

    def render_grid_table(is_solved: bool = False, is_interactive: bool = False) -> str:
        rows_html = []
        for r in range(height):
            cells_html = []
            for c in range(width):
                ch = grid[r][c]
                is_block = (ch == "#")
                leg_entries = legend_map.get((r, c))
                taj_idx = tajenka_cells_set.get((r, c))
                cell_ents = cell_entries_map.get((r, c), [])
                
                across_id = next((e["id"] for e in cell_ents if e["dir"] == "A"), "")
                down_id = next((e["id"] for e in cell_ents if e["dir"] == "D"), "")

                if is_block:
                    if leg_entries:
                        # Legend cell
                        content = render_legend_cell(leg_entries)
                        leg_ids = ",".join(str(e["id"]) for e in leg_entries)
                        cells_html.append(
                            f'<td class="cell-legend" data-r="{r}" data-c="{c}" '
                            f'data-legend-ids="{leg_ids}">{content}</td>'
                        )
                    else:
                        # Dead cell
                        cells_html.append(f'<td class="cell-dead"></td>')
                else:
                    # Answer cell
                    is_pre = (r, c) in prefill
                    classes = ["cell-answer"] + (["cell-prefilled"] if is_pre else [])
                    if taj_idx is not None:
                        classes.append("cell-tajenka")
                    
                    class_str = " ".join(classes)
                    sup_html = f'<span class="tajenka-num">{taj_idx}</span>' if taj_idx else ""
                    
                    letter_display = ""
                    if is_solved or is_pre:
                        letter_display = f'<span class="answer-letter">{ch.upper()}</span>'
                    elif is_interactive:
                        letter_display = '<span class="cell-letter"></span>'

                    # No data-solution attribute: it put the whole answer key in the DOM
                    # in plain text, which made the obfuscated solution matrix pointless.
                    cells_html.append(
                        f'<td class="{class_str}" data-r="{r}" data-c="{c}" '
                        f'data-across-id="{across_id}" data-down-id="{down_id}">'
                        f'{sup_html}{letter_display}</td>'
                    )
            rows_html.append(f'  <tr>{"".join(cells_html)}</tr>')
        return f'<table class="grid-table">\n' + "\n".join(rows_html) + '\n</table>'

    # Build styles
    css_styles = """
        * { box-sizing: border-box; }
        body {
            margin: 0;
            padding: 20px;
            background-color: #f4f3ef;
            color: #1e293b;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.5;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 {
            text-align: center;
            margin-top: 0;
            margin-bottom: 16px;
            color: #0f172a;
            font-size: 28px;
        }
        h2 {
            margin-top: 32px;
            margin-bottom: 12px;
            color: #1e293b;
            border-bottom: 2px solid #cbd5e1;
            padding-bottom: 6px;
            font-size: 22px;
        }
        .intro-box {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .grid-wrapper {
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            margin-bottom: 24px;
            padding: 8px 0;
            /* NOT flex+justify-content:center — a centred flex item that is wider than
               its scroll container overflows on BOTH sides and the left half becomes
               unreachable. Auto margins collapse to 0 when there is no spare room, so
               the grid centres when it fits and scrolls fully when it does not. */
            display: block;
        }
        .grid-table {
            margin-left: auto;
            margin-right: auto;
            border-collapse: collapse;
            table-layout: fixed;
            user-select: none;
            background: transparent;
        }
        .grid-table td {
            width: 64px;
            height: 64px;
            min-width: 64px;
            max-width: 64px;
            min-height: 64px;
            max-height: 64px;
            box-sizing: border-box;
            position: relative;
            padding: 0;
            text-align: center;
            vertical-align: middle;
        }
        .cell-dead {
            background: transparent;
            border: none;
        }
        .cell-answer {
            background: #ffffff;
            border: 1px solid #333333;
            cursor: pointer;
        }
        .cell-legend {
            background: #ede9e1;
            border: 1px solid #333333;
            cursor: pointer;
            overflow: hidden;
        }
        .cell-prefilled {
            background: #eef2f7;
            color: #64748b;
        }
        .cell-tajenka {
            background: #fef08a;
        }
        .clue-box {
            width: 100%;
            height: 100%;
            padding: 2px 3px;
            display: flex;
            align-items: center;
            justify-content: flex-start;
            text-align: left;
            font-weight: 700;
            color: #1e293b;
            font-stretch: condensed;
            letter-spacing: -0.2px;
            word-break: break-word;
            overflow-wrap: break-word;
            hyphens: auto;
        }
        .clue-box.clue-a {
            border-bottom: 1px solid #a8a29e;
            height: 50%;
        }
        .clue-box.clue-d {
            height: 50%;
        }
        .legend-cell-single .clue-box {
            height: 100%;
        }
        .tajenka-num {
            position: absolute;
            top: 2px;
            left: 3px;
            font-size: 10px;
            font-weight: 800;
            color: #854d0e;
            line-height: 1;
            pointer-events: none;
        }
        .answer-letter, .cell-letter {
            font-size: 24px;
            font-weight: 800;
            text-transform: uppercase;
            color: #0f172a;
            line-height: 64px;
            display: block;
            width: 100%;
            height: 100%;
            pointer-events: none;
        }

        /* Review mode specific styles */
        .answer-key-container {
            display: flex;
            gap: 24px;
            flex-wrap: wrap;
            margin-top: 16px;
        }
        .key-column {
            flex: 1;
            min-width: 300px;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
        }
        .key-column h3 {
            margin-top: 0;
            margin-bottom: 12px;
            font-size: 18px;
            color: #0f172a;
            border-bottom: 1px solid #cbd5e1;
            padding-bottom: 4px;
        }
        .key-list {
            list-style: none;
            padding-left: 0;
            margin: 0;
        }
        .key-list li {
            padding: 4px 0;
            border-bottom: 1px baseline #f1f5f9;
            font-size: 14px;
        }
        .badge-theme {
            display: inline-block;
            background: #fef3c7;
            color: #92400e;
            font-size: 11px;
            font-weight: 700;
            padding: 1px 6px;
            border-radius: 4px;
            margin-left: 6px;
        }
        .stats-table {
            width: 100%;
            max-width: 500px;
            border-collapse: collapse;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            overflow: hidden;
            margin-top: 12px;
        }
        .stats-table td, .stats-table th {
            padding: 8px 14px;
            border-bottom: 1px solid #e2e8f0;
            font-size: 14px;
        }
        .stats-table th {
            text-align: left;
            background: #f8fafc;
            color: #475569;
        }
        .tajenka-display-box {
            background: #fef08a;
            border: 2px solid #eab308;
            border-radius: 8px;
            padding: 14px 20px;
            font-size: 18px;
            font-weight: 700;
            color: #713f12;
            margin-top: 12px;
        }

        /* Interactive mode specific styles */
        .controls-bar {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 16px;
            background: #ffffff;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid #e2e8f0;
        }
        .btn-group {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 8px 14px;
            font-size: 14px;
            font-weight: 600;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            transition: background-color 0.15s ease;
        }
        .btn-primary { background: #2563eb; color: white; }
        .btn-primary:hover { background: #1d4ed8; }
        .btn-secondary { background: #64748b; color: white; }
        .btn-secondary:hover { background: #475569; }
        .btn-danger { background: #dc2626; color: white; }
        .btn-danger:hover { background: #b91c1c; }
        .progress-text {
            font-size: 15px;
            font-weight: 700;
            color: #334155;
        }

        .clue-banner {
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-left: 4px solid #2563eb;
            border-radius: 6px;
            padding: 10px 16px;
            margin-bottom: 16px;
            font-size: 16px;
            font-weight: 600;
            color: #0f172a;
            min-height: 46px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .clue-banner-dir {
            color: #2563eb;
            font-weight: 800;
        }
        .clue-banner-text {
            flex: 1;
        }

        .tajenka-section {
            margin-top: 24px;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px 20px;
        }
        .tajenka-section-title {
            margin: 0 0 12px 0;
            font-size: 18px;
            font-weight: 700;
            color: #1e293b;
        }
        .tajenka-strip {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 12px;
        }
        .tajenka-box {
            width: 44px;
            height: 48px;
            border: 2px solid #d97706;
            background: #fef3c7;
            border-radius: 6px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            position: relative;
            box-sizing: border-box;
        }
        .tajenka-box-idx {
            position: absolute;
            top: 2px;
            left: 4px;
            font-size: 9px;
            font-weight: 800;
            color: #92400e;
        }
        .tajenka-box-letter {
            font-size: 20px;
            font-weight: 800;
            color: #78350f;
            text-transform: uppercase;
        }
        .celebration-banner {
            background: linear-gradient(135deg, #fef3c7, #fde68a);
            border: 2px solid #f59e0b;
            border-radius: 8px;
            padding: 14px 20px;
            text-align: center;
            font-size: 18px;
            font-weight: 700;
            color: #92400e;
            box-shadow: 0 4px 12px rgba(245, 158, 11, 0.2);
            animation: popIn 0.4s ease-out;
            margin-top: 12px;
        }
        .celebration-banner.hidden {
            display: none;
        }

        /* Highlighting states for interactive mode */
        .cell-active {
            background-color: #93c5fd !important;
            outline: 3px solid #2563eb !important;
            outline-offset: -3px;
            z-index: 10;
        }
        .cell-entry-selected {
            background-color: #e0f2fe !important;
        }
        .cell-tajenka.cell-entry-selected {
            background-color: #fde68a !important;
        }
        .cell-tajenka.cell-active {
            background-color: #fde047 !important;
            outline: 3px solid #ca8a04 !important;
        }
        .legend-selected {
            background-color: #bae6fd !important;
            border-color: #0284c7 !important;
        }
        .cell-wrong {
            background-color: #fecaca !important;
            color: #dc2626 !important;
            animation: shake 0.3s;
        }
        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-3px); }
            75% { transform: translateX(3px); }
        }
        @keyframes popIn {
            0% { transform: scale(0.95); opacity: 0; }
            100% { transform: scale(1); opacity: 1; }
        }
    """

    if mode == "review":
        # Group entries by direction
        across_entries = [e for e in entries if e["dir"] == "A"]
        down_entries = [e for e in entries if e["dir"] == "D"]

        def build_key_items(entry_list):
            items = []
            for e in entry_list:
                theme_badge = '<span class="badge-theme">téma</span>' if e.get("theme") else ""
                ans_str = e["answer"].upper()
                clue_str = e["clue"]
                items.append(f'<li><strong>{ans_str}</strong> — {clue_str}{theme_badge}</li>')
            return "\n".join(items)

        across_html = build_key_items(across_entries)
        down_html = build_key_items(down_entries)

        stats_rows = []
        labels = {
            "answers": "Počet odpovedí",
            "theme": "Tématické slová",
            "glue": "Výplňové slová",
            "max_clue": "Max dĺžka legendy",
            "median_clue": "Medián dĺžky legendy"
        }
        for k, v in stats.items():
            lbl = labels.get(k, k)
            stats_rows.append(f'<tr><th>{lbl}</th><td>{v}</td></tr>')
        stats_html = "\n".join(stats_rows)

        tajenka_html_sec = ""
        if tajenka and "text" in tajenka:
            tajenka_html_sec = f'''
            <h2>Tajenka</h2>
            <div class="tajenka-display-box">
                TAJENKA: {tajenka["text"].upper()}
            </div>
            '''

        body_content = f'''
        <div class="container">
            <h1>{title}</h1>
            {f'<div class="intro-box">{intro_html}</div>' if intro_html else ''}
            
            <h2>Křížovka</h2>
            <div class="grid-wrapper">
                {render_grid_table(is_solved=False, is_interactive=False)}
            </div>

            <h2>Riešenie křížovky</h2>
            <div class="grid-wrapper">
                {render_grid_table(is_solved=True, is_interactive=False)}
            </div>

            <h2>Zoznam odpovedí</h2>
            <div class="answer-key-container">
                <div class="key-column">
                    <h3>Vodorovně</h3>
                    <ul class="key-list">
                        {across_html}
                    </ul>
                </div>
                <div class="key-column">
                    <h3>Svisle</h3>
                    <ul class="key-list">
                        {down_html}
                    </ul>
                </div>
            </div>

            {tajenka_html_sec}

            <h2>Štatistika</h2>
            <table class="stats-table">
                {stats_html}
            </table>
        </div>
        '''

        full_html = f'''<!DOCTYPE html>
<html lang="sk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Review</title>
    <style>
    {css_styles}
    </style>
</head>
<body>
{body_content}
</body>
</html>
'''
        return full_html

    elif mode == "interactive":
        # Build JS data structures
        # The JS navigates by geometry and checks against OBFUSCATED_SOL, so it never
        # needs `answer` — and embedding it here would put every solution in plain sight
        # of Ctrl-F, defeating the obfuscated solution matrix two lines below.
        entries_json = json.dumps(
            [{k: v for k, v in e.items() if k != "answer"} for e in entries],
            ensure_ascii=False,
        )
        tajenka_json = json.dumps(tajenka, ensure_ascii=False) if tajenka else "null"
        puzzle_hash = obfuscate(json.dumps(puzzle["grid"]), key="GridHashKey")[:24]

        # Tajenka strip HTML
        tajenka_strip_boxes = []
        if tajenka and "cells" in tajenka:
            for idx in range(1, len(tajenka["cells"]) + 1):
                tajenka_strip_boxes.append(
                    f'<div class="tajenka-box" data-taj-idx="{idx}">'
                    f'<span class="tajenka-box-idx">{idx}</span>'
                    f'<span class="tajenka-box-letter" id="taj-box-{idx}"></span>'
                    f'</div>'
                )
        tajenka_strip_html = "".join(tajenka_strip_boxes)

        js_script = f'''
        (function() {{
            const OBFUSCATED_SOL = "{obfuscated_solution}";
            const SOL_KEY = "Karolina2026";
            const PUZZLE_HASH = "{puzzle_hash}";
            const TOTAL_CELLS = {total_answer_cells};
            const ENTRIES = {entries_json};
            const TAJENKA = {tajenka_json};

            function deobfuscate(b64, key) {{
                const binary = atob(b64);
                const keyBytes = new TextEncoder().encode(key);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {{
                    bytes[i] = binary.charCodeAt(i) ^ keyBytes[i % keyBytes.length];
                }}
                return new TextDecoder().decode(bytes);
            }}

            const solutionStr = deobfuscate(OBFUSCATED_SOL, SOL_KEY);
            const solutionGrid = solutionStr.split('\\n');

            let activeR = null;
            let activeC = null;
            let activeDir = "A"; // "A" or "D"

            const hiddenInput = document.getElementById('hidden-input');
            const filledCountSpan = document.getElementById('filled-count');
            const clueBanner = document.getElementById('active-clue-banner');
            const clueDirIcon = document.getElementById('clue-dir-icon');
            const clueTextSpan = document.getElementById('clue-text');

            function foldDiacritics(str) {{
                if (!str) return "";
                return str.normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toUpperCase();
            }}

            function isLetterCorrect(typed, sol) {{
                if (!typed || !sol) return false;
                return foldDiacritics(typed) === foldDiacritics(sol);
            }}

            function getCellEl(r, c) {{
                return document.querySelector(`td[data-r="${{r}}"][data-c="${{c}}"]`);
            }}

            function getEntriesForCell(r, c) {{
                const el = getCellEl(r, c);
                if (!el) return {{ across: null, down: null }};
                const aid = el.getAttribute('data-across-id');
                const did = el.getAttribute('data-down-id');
                const across = aid ? ENTRIES.find(e => e.id == aid) : null;
                const down = did ? ENTRIES.find(e => e.id == did) : null;
                return {{ across, down }};
            }}

            function getActiveEntry() {{
                if (activeR === null || activeC === null) return null;
                const {{ across, down }} = getEntriesForCell(activeR, activeC);
                if (activeDir === 'A' && across) return across;
                if (activeDir === 'D' && down) return down;
                return across || down || null;
            }}

            function updateHighlights() {{
                document.querySelectorAll('.cell-answer:not(.cell-prefilled)').forEach(el => {{
                    el.classList.remove('cell-active', 'cell-entry-selected');
                }});
                document.querySelectorAll('.cell-legend').forEach(el => {{
                    el.classList.remove('legend-selected');
                }});

                if (activeR === null || activeC === null) {{
                    clueBanner.style.display = 'none';
                    return;
                }}

                clueBanner.style.display = 'flex';
                const activeTd = getCellEl(activeR, activeC);
                if (activeTd) activeTd.classList.add('cell-active');

                const entry = getActiveEntry();
                if (entry) {{
                    clueDirIcon.textContent = entry.dir === 'A' ? '▶' : '▼';
                    clueTextSpan.textContent = `${{entry.clue.toUpperCase()}} (${{entry.len}})`;

                    // Highlight all cells in entry
                    const dr = entry.dir === 'D' ? 1 : 0;
                    const dc = entry.dir === 'A' ? 1 : 0;
                    for (let i = 0; i < entry.len; i++) {{
                        const er = entry.r + i * dr;
                        const ec = entry.c + i * dc;
                        const td = getCellEl(er, ec);
                        if (td) td.classList.add('cell-entry-selected');
                    }}

                    // Highlight legend cell
                    const [lr, lc] = entry.legend;
                    const legTd = getCellEl(lr, lc);
                    if (legTd) legTd.classList.add('legend-selected');
                }}
            }}

            function updateProgressAndTajenka() {{
                let filledCount = 0;
                const answerCells = document.querySelectorAll('.cell-answer:not(.cell-prefilled)');
                answerCells.forEach(td => {{
                    const span = td.querySelector('.cell-letter');
                    if (span && span.textContent.trim() !== '') {{
                        filledCount++;
                    }}
                }});
                filledCountSpan.textContent = filledCount;

                // Update Tajenka strip
                let tajenkaComplete = true;
                if (TAJENKA && TAJENKA.cells) {{
                    TAJENKA.cells.forEach(([tr, tc], idx) => {{
                        const td = getCellEl(tr, tc);
                        const span = td ? td.querySelector('.cell-letter') : null;
                        const val = span ? span.textContent.trim() : '';
                        const boxSpan = document.getElementById(`taj-box-${{idx + 1}}`);
                        if (boxSpan) boxSpan.textContent = val;

                        const solVal = solutionGrid[tr] ? solutionGrid[tr][tc] : '';
                        if (!val || !isLetterCorrect(val, solVal)) {{
                            tajenkaComplete = false;
                        }}
                    }});

                    const celebBanner = document.getElementById('celebration-banner');
                    if (tajenkaComplete) {{
                        celebBanner.classList.remove('hidden');
                    }} else {{
                        celebBanner.classList.add('hidden');
                    }}
                }}

                saveState();
            }}

            function saveState() {{
                const state = {{}};
                document.querySelectorAll('.cell-answer:not(.cell-prefilled)').forEach(td => {{
                    const r = td.getAttribute('data-r');
                    const c = td.getAttribute('data-c');
                    const span = td.querySelector('.cell-letter');
                    const val = span ? span.textContent.trim() : '';
                    if (val) state[`${{r}},${{c}}`] = val;
                }});
                localStorage.setItem('crossword_state_' + PUZZLE_HASH, JSON.stringify(state));
            }}

            function loadState() {{
                const raw = localStorage.getItem('crossword_state_' + PUZZLE_HASH);
                if (!raw) return;
                try {{
                    const state = JSON.parse(raw);
                    Object.entries(state).forEach(([key, val]) => {{
                        const [r, c] = key.split(',');
                        const td = getCellEl(r, c);
                        if (td) {{
                            const span = td.querySelector('.cell-letter');
                            if (span) span.textContent = val;
                        }}
                    }});
                }} catch (e) {{ console.error("Error loading state", e); }}
            }}

            function focusCell(r, c, forceDir = null) {{
                const td = getCellEl(r, c);
                if (!td || !td.classList.contains('cell-answer')) return;
                if (td.classList.contains('cell-prefilled')) return;

                if (activeR === r && activeC === c && forceDir === null) {{
                    // Toggle direction if cell has both
                    const {{ across, down }} = getEntriesForCell(r, c);
                    if (across && down) {{
                        activeDir = activeDir === 'A' ? 'D' : 'A';
                    }}
                }} else {{
                    activeR = r;
                    activeC = c;
                    const {{ across, down }} = getEntriesForCell(r, c);
                    if (forceDir) {{
                        activeDir = forceDir;
                    }} else {{
                        if (activeDir === 'A' && !across && down) activeDir = 'D';
                        else if (activeDir === 'D' && !down && across) activeDir = 'A';
                    }}
                }}

                updateHighlights();
                if (hiddenInput) {{
                    hiddenInput.focus();
                }}
            }}

            function stepForward() {{
                const entry = getActiveEntry();
                if (!entry) return;
                const dr = entry.dir === 'D' ? 1 : 0;
                const dc = entry.dir === 'A' ? 1 : 0;

                // Find index of current cell in entry
                const idx = (activeR - entry.r) * dr + (activeC - entry.c) * dc;
                if (idx < entry.len - 1) {{
                    activeR += dr;
                    activeC += dc;
                    updateHighlights();
                }}
            }}

            function stepBackward() {{
                const entry = getActiveEntry();
                if (!entry) return;
                const dr = entry.dir === 'D' ? 1 : 0;
                const dc = entry.dir === 'A' ? 1 : 0;

                const idx = (activeR - entry.r) * dr + (activeC - entry.c) * dc;
                if (idx > 0) {{
                    activeR -= dr;
                    activeC -= dc;
                    updateHighlights();
                }}
            }}

            function jumpEntry(direction = 1) {{
                const entry = getActiveEntry();
                let idx = 0;
                if (entry) {{
                    idx = ENTRIES.findIndex(e => e.id === entry.id);
                }}
                let nextIdx = (idx + direction + ENTRIES.length) % ENTRIES.length;
                const nextEntry = ENTRIES[nextIdx];
                if (nextEntry) {{
                    // Find first empty cell in next entry, or start cell
                    const dr = nextEntry.dir === 'D' ? 1 : 0;
                    const dc = nextEntry.dir === 'A' ? 1 : 0;
                    let targetR = nextEntry.r;
                    let targetC = nextEntry.c;
                    for (let i = 0; i < nextEntry.len; i++) {{
                        const er = nextEntry.r + i * dr;
                        const ec = nextEntry.c + i * dc;
                        const td = getCellEl(er, ec);
                        const span = td ? td.querySelector('.cell-letter') : null;
                        if (!span || span.textContent.trim() === '') {{
                            targetR = er;
                            targetC = ec;
                            break;
                        }}
                    }}
                    activeR = targetR;
                    activeC = targetC;
                    activeDir = nextEntry.dir;
                    updateHighlights();
                    if (hiddenInput) hiddenInput.focus();
                }}
            }}

            // Event Listeners
            document.querySelectorAll('.cell-answer:not(.cell-prefilled)').forEach(td => {{
                td.addEventListener('click', (e) => {{
                    const r = parseInt(td.getAttribute('data-r'));
                    const c = parseInt(td.getAttribute('data-c'));
                    focusCell(r, c);
                }});
            }});

            document.querySelectorAll('.cell-legend').forEach(td => {{
                td.addEventListener('click', (e) => {{
                    const idsStr = td.getAttribute('data-legend-ids');
                    if (!idsStr) return;
                    const ids = idsStr.split(',').map(n => parseInt(n));
                    // Check if clicked directly on a clue box
                    const clueBox = e.target.closest('.clue-box');
                    let targetEntry = null;
                    if (clueBox) {{
                        const eid = parseInt(clueBox.getAttribute('data-entry-id'));
                        targetEntry = ENTRIES.find(ent => ent.id === eid);
                    }}
                    if (!targetEntry && ids.length > 0) {{
                        targetEntry = ENTRIES.find(ent => ent.id === ids[0]);
                    }}

                    if (targetEntry) {{
                        activeR = targetEntry.r;
                        activeC = targetEntry.c;
                        activeDir = targetEntry.dir;
                        updateHighlights();
                        if (hiddenInput) hiddenInput.focus();
                    }}
                }});
            }});

            // Input handling for dead-key/compose and standard typing
            if (hiddenInput) {{
                hiddenInput.addEventListener('input', (e) => {{
                    const val = hiddenInput.value;
                    hiddenInput.value = '';
                    if (!val || activeR === null || activeC === null) return;

                    const lastChar = val[val.length - 1];
                    if (/[a-zA-ZáäčďéěíĺľňóôřŕšťúůýžÁÄČĎÉĚÍĹĽŇÓÔŘŔŠŤÚŮÝŽ]/u.test(lastChar)) {{
                        const td = getCellEl(activeR, activeC);
                        if (td) {{
                            const span = td.querySelector('.cell-letter');
                            if (span) {{
                                span.textContent = lastChar.toUpperCase();
                                updateProgressAndTajenka();
                                stepForward();
                            }}
                        }}
                    }}
                }});
            }}

            document.addEventListener('keydown', (e) => {{
                if (activeR === null || activeC === null) return;

                if (e.key === 'ArrowRight') {{
                    e.preventDefault();
                    if (activeC < {width - 1}) focusCell(activeR, activeC + 1, activeDir);
                }} else if (e.key === 'ArrowLeft') {{
                    e.preventDefault();
                    if (activeC > 0) focusCell(activeR, activeC - 1, activeDir);
                }} else if (e.key === 'ArrowDown') {{
                    e.preventDefault();
                    if (activeR < {height - 1}) focusCell(activeR + 1, activeC, activeDir);
                }} else if (e.key === 'ArrowUp') {{
                    e.preventDefault();
                    if (activeR > 0) focusCell(activeR - 1, activeC, activeDir);
                }} else if (e.key === 'Tab') {{
                    e.preventDefault();
                    jumpEntry(e.shiftKey ? -1 : 1);
                }} else if (e.key === 'Backspace') {{
                    e.preventDefault();
                    const td = getCellEl(activeR, activeC);
                    const span = td ? td.querySelector('.cell-letter') : null;
                    if (span && span.textContent.trim() !== '') {{
                        span.textContent = '';
                        updateProgressAndTajenka();
                    }} else {{
                        stepBackward();
                        const prevTd = getCellEl(activeR, activeC);
                        const prevSpan = prevTd ? prevTd.querySelector('.cell-letter') : null;
                        if (prevSpan) prevSpan.textContent = '';
                        updateProgressAndTajenka();
                    }}
                }} else if (e.key === 'Delete') {{
                    e.preventDefault();
                    const td = getCellEl(activeR, activeC);
                    const span = td ? td.querySelector('.cell-letter') : null;
                    if (span) {{
                        span.textContent = '';
                        updateProgressAndTajenka();
                    }}
                }}
            }});

            // Button handlers
            document.getElementById('btn-check').addEventListener('click', () => {{
                document.querySelectorAll('.cell-answer:not(.cell-prefilled)').forEach(td => {{
                    const r = parseInt(td.getAttribute('data-r'));
                    const c = parseInt(td.getAttribute('data-c'));
                    const span = td.querySelector('.cell-letter');
                    const typed = span ? span.textContent.trim() : '';
                    const sol = solutionGrid[r] ? solutionGrid[r][c] : '';

                    if (typed !== '' && !isLetterCorrect(typed, sol)) {{
                        td.classList.add('cell-wrong');
                    }}
                }});

                setTimeout(() => {{
                    document.querySelectorAll('.cell-answer:not(.cell-prefilled)').forEach(td => {{
                        td.classList.remove('cell-wrong');
                    }});
                }}, 2000);
            }});

            document.getElementById('btn-reveal-cell').addEventListener('click', () => {{
                if (activeR === null || activeC === null) return;
                const sol = solutionGrid[activeR] ? solutionGrid[activeR][activeC] : '';
                if (sol) {{
                    const td = getCellEl(activeR, activeC);
                    const span = td ? td.querySelector('.cell-letter') : null;
                    if (span) {{
                        span.textContent = sol.toUpperCase();
                        updateProgressAndTajenka();
                        stepForward();
                    }}
                }}
            }});

            document.getElementById('btn-reveal-all').addEventListener('click', () => {{
                if (confirm("Naozaj chcete odhaliť celú křížovku?")) {{
                    document.querySelectorAll('.cell-answer:not(.cell-prefilled)').forEach(td => {{
                        const r = parseInt(td.getAttribute('data-r'));
                        const c = parseInt(td.getAttribute('data-c'));
                        const sol = solutionGrid[r] ? solutionGrid[r][c] : '';
                        const span = td.querySelector('.cell-letter');
                        if (span && sol) {{
                            span.textContent = sol.toUpperCase();
                        }}
                    }});
                    updateProgressAndTajenka();
                }}
            }});

            // Initial setup
            loadState();
            updateProgressAndTajenka();

            // Auto-focus first entry cell
            if (ENTRIES.length > 0) {{
                const first = ENTRIES[0];
                focusCell(first.r, first.c, first.dir);
            }}
        }})();
        '''

        body_content = f'''
        <div class="container">
            <h1>{title}</h1>
            {f'<div class="intro-box">{intro_html}</div>' if intro_html else ''}

            <div class="clue-banner" id="active-clue-banner">
                <span class="clue-banner-dir" id="clue-dir-icon">▶</span>
                <span class="clue-banner-text" id="clue-text">Vyberte políčko</span>
            </div>

            <div class="controls-bar">
                <div class="progress-text">
                    vyplnené <span id="filled-count">0</span>/{total_answer_cells}
                </div>
                <div class="btn-group">
                    <button class="btn btn-primary" id="btn-check">Skontrolovať</button>
                    <button class="btn btn-secondary" id="btn-reveal-cell">Odhaliť políčko</button>
                    <button class="btn btn-danger" id="btn-reveal-all">Odhaliť všetko</button>
                </div>
            </div>

            <div class="grid-wrapper">
                {render_grid_table(is_solved=False, is_interactive=True)}
            </div>

            {'<div class="tajenka-section"><div class="tajenka-section-title">Tajenka</div><div class="tajenka-strip">' + tajenka_strip_html + '</div><div class="celebration-banner hidden" id="celebration-banner">🎉 Gratulujeme! Tajenka je správne vyriešená! 🎉</div></div>' if tajenka else ''}
            
            <input type="text" id="hidden-input" style="position: absolute; opacity: 0; left: -9999px; top: -9999px;" autocomplete="off" spellcheck="false" autocorrect="off" autocapitalize="characters">
        </div>
        '''

        full_html = f'''<!DOCTYPE html>
<html lang="sk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
    {css_styles}
    </style>
</head>
<body>
{body_content}
<script>
{js_script}
</script>
</body>
</html>
'''
        return full_html

    else:
        raise ValueError(f"Unknown mode: {mode}")


def main():
    parser = argparse.ArgumentParser(description="Render puzzle JSON to self-contained HTML.")
    parser.add_argument("--in", dest="input_path", required=True, help="Input puzzle.json file")
    parser.add_argument("--out", dest="output_path", required=True, help="Output html file")
    parser.add_argument("--mode", choices=["interactive", "review"], required=True, help="Render mode")
    parser.add_argument("--intro", dest="intro_path", help="Optional intro HTML fragment file")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="shrink cells and legend text (0.7 suits a 33x33 grid in e-mail)")

    args = parser.parse_args()

    in_file = Path(args.input_path)
    if not in_file.exists():
        print(f"Error: Input file '{in_file}' not found.", file=sys.stderr)
        sys.exit(1)

    with open(in_file, "r", encoding="utf-8") as f:
        puzzle = json.load(f)

    intro_html = ""
    if args.intro_path:
        intro_file = Path(args.intro_path)
        if intro_file.exists():
            with open(intro_file, "r", encoding="utf-8") as f:
                intro_html = f.read().strip()

    global SCALE
    SCALE = args.scale
    html_content = build_html(puzzle, mode=args.mode, intro_html=intro_html)
    if abs(args.scale - 1.0) > 1e-9:
        n = 64 * args.scale
        override = (
            "<style>"
            f".grid-table td{{width:{n:g}px;height:{n:g}px;min-width:{n:g}px;"
            f"max-width:{n:g}px;min-height:{n:g}px;max-height:{n:g}px}}"
            f".answer-letter,.cell-letter{{font-size:{24*args.scale:g}px;"
            f"line-height:{n:g}px}}"
            "</style>"
        )
        html_content = html_content.replace("</head>", override + "</head>", 1)

    out_file = Path(args.output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Successfully rendered puzzle to {out_file} in {args.mode} mode.")


if __name__ == "__main__":
    main()
