#!/usr/bin/env python3
"""Merge several review pages into one e-mail document.

`render_puzzle.py --mode review` emits a complete document per candidate. For the
approval mail we want them in one message, so this lifts each `<body>` into a section
and keeps a single copy of the `<style>` block (both pages carry the same CSS).
"""

from __future__ import annotations

import argparse
import re


def split(html):
    style = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    return style, (body.group(1) if body else html)


def drop_empty_grid(body):
    """Remove the unsolved grid: at e-mail scale its in-cell legends are illegible,
    and the reviewer gets the readable version as an attachment."""
    return re.sub(r"<h2>Křížovka</h2>.*?(?=<h2>Riešenie)", "", body, flags=re.S)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--page", action="append", nargs=2, metavar=("LABEL", "PATH"),
                    required=True)
    ap.add_argument("--title", default="Křížovka — kandidáti")
    ap.add_argument("--intro", help="HTML fragment placed above the first candidate")
    ap.add_argument("--drop-empty-grid", action="store_true",
                    help="omit the unsolved grid; its in-cell legends are illegible at e-mail scale")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    styles, sections = [], []
    for label, path in args.page:
        style, body = split(open(path, encoding="utf-8").read())
        if style not in styles:
            styles.append(style)
        if args.drop_empty_grid:
            body = drop_empty_grid(body)
        sections.append(
            f'<section class="cand"><div class="candhead">{label}</div>{body}</section>'
        )

    intro = open(args.intro, encoding="utf-8").read() if args.intro else ""
    # with only the solved grid left, in-cell legend text is unreadable noise at this
    # scale — the answer key below carries every clue in full size
    hide = ".cand .clue-box{visibility:hidden}" if args.drop_empty_grid else ""
    extra = hide + """
.cand{border-top:3px solid #2f5d50;margin:38px 0 0}
.candhead{background:#2f5d50;color:#fff;font:700 15px/1.4 system-ui,sans-serif;
  padding:8px 14px;letter-spacing:.04em;text-transform:uppercase}
.wrap{max-width:1500px;margin:0 auto;padding:26px 18px 60px;
  font:16px/1.55 system-ui,-apple-system,sans-serif;color:#202020;background:#f7f4ee}
"""
    html = (
        '<!doctype html><html lang="cs"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{args.title}</title><style>{''.join(styles)}{extra}</style></head>"
        f'<body><div class="wrap">{intro}{"".join(sections)}</div></body></html>'
    )
    open(args.out, "w", encoding="utf-8").write(html)
    print(f"{args.out}: {len(html)/1024:.0f} kB, {len(sections)} candidates")


if __name__ == "__main__":
    main()
