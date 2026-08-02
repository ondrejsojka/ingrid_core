#!/usr/bin/env python3
"""Extract Ingrid-style word;score dictionaries from Brněnský metropolitan PDFs.

Requires: pdftotext (poppler-utils), curl (only when downloading).

Examples:
  # One local PDF -> .dict (score = frequency)
  python3 scripts/metropolitan_pdf_to_dict.py path/to/Metropolitan_2026-5_web.pdf \
      -o local/metropolitan/bm_2026-5.dict

  # Download every released edition for one year; write whole-year combined dict
  python3 scripts/metropolitan_pdf_to_dict.py --fetch-year 2024 --outdir local/metropolitan

  # Fetch years 2020-2026: per-edition dicts for the current year,
  # whole-year combined dicts for every year
  python3 scripts/metropolitan_pdf_to_dict.py --fetch-years 2020-2026 \
      --outdir local/metropolitan
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE = "https://cosedeje.brno.cz"
ARCHIVE_URL = BASE + "/brnensky-metropolitan?rok={year}"

WORD_RE = re.compile(r"[A-Za-zÁÄČĎÉĚÍĹĽŇÓÔŔŘŠŤÚŮÝŽáäčďéěíĺľňóôŕřšťúůýž]+", re.UNICODE)
ISSUE_HREF_RE = re.compile(r'href="(/w/brnensky-metropolitan[^"#]+)"', re.I)
PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf[^"#]*)"', re.I)
MIN_LEN = 2
MAX_LEN = 40


def extract_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for token in WORD_RE.findall(text):
        word = token.casefold()
        if MIN_LEN <= len(word) <= MAX_LEN:
            counts[word] += 1
    return counts


def counts_to_dict_lines(counts: Counter[str]) -> list[str]:
    # Frequency is the Ingrid score. Sort by descending frequency, then alpha.
    return [f"{word};{freq}" for word, freq in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def write_dict(path: Path, counts: Counter[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = counts_to_dict_lines(counts)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


import subprocess  # noqa: E402  — kept near run() for script locality


def require_tool(name: str) -> None:
    from shutil import which

    if which(name) is None:
        raise SystemExit(f"Required tool not found on PATH: {name}")


def pdf_to_text(pdf_path: Path) -> str:
    require_tool("pdftotext")
    result = run(["pdftotext", "-layout", str(pdf_path), "-"])
    return result.stdout


def download(url: str, dest: Path) -> None:
    require_tool("curl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{dest.name}.", suffix=".part", dir=dest.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        # Brno CDN sometimes fails local CA verification in constrained environments.
        result = run(
            [
                "curl",
                "-fsSL",
                "--retry",
                "3",
                "--retry-delay",
                "1",
                "-k",
                "-o",
                str(temporary_path),
                url,
            ],
            check=False,
        )
        if result.returncode != 0 or temporary_path.stat().st_size == 0:
            error = (result.stderr or result.stdout or "").strip()
            raise SystemExit(f"Failed to download {url}\n{error}")
        temporary_path.replace(dest)
    finally:
        temporary_path.unlink(missing_ok=True)


def fetch_url_text(url: str) -> str:
    require_tool("curl")
    result = run(["curl", "-fsSL", "--retry", "3", "-k", "-A", "Mozilla/5.0", url], check=False)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise SystemExit(f"Failed to fetch {url}\n{err}")
    return result.stdout


def abs_url(href: str) -> str:
    href = unescape(href)
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE + href
    return href


def normalize_issue_id(raw: str) -> str:
    parts = [p.lstrip("0") or "0" for p in raw.split("-")]
    return "-".join(parts)


def parse_issue_id_from_slug(slug: str) -> str | None:
    # Examples:
    #   brnensky-metropolitan-01-2020
    #   brnensky-metropolitan-c-7-8-2026
    #   brnensky-metropolitan-c-12-25
    m = re.search(
        r"brnensky-metropolitan-(?:c-)?(\d+(?:-\d+)?)[-_](\d{2,4})$",
        slug,
        re.IGNORECASE,
    )
    if not m:
        return None
    return normalize_issue_id(m.group(1))


def year_matches_slug(slug: str, year: int) -> bool:
    y2 = f"{year % 100:02d}"
    return bool(re.search(rf"(?:^|-)(?:c-)?\d+(?:-\d+)?-(?:{year}|{y2})$", slug))


def discover_issue_pages(year: int) -> list[tuple[str, str]]:
    """Return [(issue_id, issue_page_url), ...] for a year from the archive page."""
    html = fetch_url_text(ARCHIVE_URL.format(year=year))
    issues: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href in ISSUE_HREF_RE.findall(html):
        href = unescape(href)
        slug = href.rstrip("/").split("/")[-1]
        if not year_matches_slug(slug, year):
            continue
        if href in seen:
            continue
        issue_id = parse_issue_id_from_slug(slug)
        if issue_id is None:
            continue
        seen.add(href)
        issues.append((issue_id, abs_url(href)))

    def sort_key(item: tuple[str, str]) -> list[int]:
        return [int(p) for p in re.findall(r"\d+", item[0])] or [999]

    issues = sorted(issues, key=sort_key)
    if not issues:
        raise SystemExit(f"No issue pages found in archive for {year}")
    return issues


def pdf_filename_from_url(url: str, fallback: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    name = next((p for p in reversed(parts) if p.lower().endswith(".pdf")), None)
    return unquote(name) if name else fallback


def score_pdf_candidate(url: str, year: int, issue_id: str) -> int:
    """Higher is better. Prefer the edition PDF; never the global current-issue chrome PDF."""
    path = unquote(urlparse(url).path)
    # Brno document URLs look like .../File.pdf/<uuid>?t=... — use the .pdf segment.
    parts = [p for p in path.split("/") if p]
    name = next((p.lower() for p in reversed(parts) if p.lower().endswith(".pdf")), parts[-1].lower() if parts else "")
    y2 = f"{year % 100:02d}"
    issue_compact = issue_id.replace("-", "")
    # padded month forms: 01, 7-8 -> 07-08 is uncommon; BM uses YYMM for single months
    score = 0
    if re.search(r"metropolitan_20\d{2}", name) or name.startswith("bm_"):
        score += 10
    # strongly prefer matching year/issue
    if f"metropolitan_{year}-{issue_id}" in name or f"metropolitan_{year}-{issue_id.zfill(2)}" in name:
        score += 100
    if re.search(rf"metropolitan_{year}-0?{issue_id.replace('-', '-0?')}", name):
        score += 80
    # Filenames vary: BM_2201.pdf, MB_2212.pdf, Metropolitan_2025-01_webdata.pdf
    month0 = int(issue_id.split("-")[0])
    if re.search(rf"(?:bm|mb)_{y2}{month0:02d}\.pdf$", name):
        score += 100
    if str(year) in name or y2 in name:
        score += 5
    return score


def resolve_issue_pdf_url(issue_page_url: str, year: int, issue_id: str) -> str:
    html = fetch_url_text(issue_page_url)
    matches = [abs_url(h) for h in PDF_HREF_RE.findall(html)]
    if not matches:
        raise SystemExit(f"No PDF link found on {issue_page_url}")

    ranked = sorted(matches, key=lambda u: score_pdf_candidate(u, year, issue_id), reverse=True)
    best = ranked[0]
    if score_pdf_candidate(best, year, issue_id) < 0:
        raise SystemExit(
            f"Could not find a plausible edition PDF on {issue_page_url}; candidates:\n"
            + "\n".join(ranked[:5])
        )
    return best


def edition_stub_from_pdf_name(name: str) -> str | None:
    m = re.search(r"Metropolitan_(\d{4}-\d+(?:-\d+)?)_(?:web(?:data)?)\.pdf$", name, re.IGNORECASE)
    if m:
        year_issue = m.group(1)
        # normalize zero-padded months: 2025-01 -> 2025-1
        ym = re.match(r"(\d{4})-(\d+)(?:-(\d+))?$", year_issue)
        if ym:
            parts = [ym.group(1), str(int(ym.group(2)))]
            if ym.group(3):
                parts.append(str(int(ym.group(3))))
            return "-".join(parts)
        return year_issue
    m = re.search(r"BM_(\d{2})(\d{2})\.pdf$", name, re.IGNORECASE)
    if m:
        year = 2000 + int(m.group(1))
        month = int(m.group(2))
        return f"{year}-{month}"
    return None


def process_pdf(
    pdf_path: Path,
    *,
    keep_txt_dir: Path | None,
) -> Counter[str]:
    text = pdf_to_text(pdf_path)
    if keep_txt_dir is not None:
        keep_txt_dir.mkdir(parents=True, exist_ok=True)
        (keep_txt_dir / (pdf_path.stem + ".txt")).write_text(text, encoding="utf-8")
    return extract_counts(text)


def merge_counters(counters: Iterable[Counter[str]]) -> Counter[str]:
    merged: Counter[str] = Counter()
    for counter in counters:
        merged.update(counter)
    return merged


def parse_year_list(spec: str) -> list[int]:
    """Parse '2024' or '2020-2025' or '2020,2021,2026' into years."""
    years: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
            if end < start:
                raise SystemExit(f"Invalid year range: {part}")
            years.extend(range(start, end + 1))
        else:
            years.append(int(part))
    # unique, sorted
    return sorted(set(years))


def fetch_year(
    year: int,
    outdir: Path,
    *,
    keep_sources: bool,
    per_edition: bool,
) -> Counter[str]:
    issues = discover_issue_pages(year)
    pdf_dir = outdir / "pdfs"
    txt_dir = outdir / "txt" if keep_sources else None
    per_edition_counts: list[tuple[str, Counter[str]]] = []

    for issue_id, page_url in issues:
        pdf_url = resolve_issue_pdf_url(page_url, year, issue_id)
        filename = pdf_filename_from_url(pdf_url, f"Metropolitan_{year}-{issue_id}_web.pdf")
        pdf_path = pdf_dir / filename
        print(f"[{year}-{issue_id}] {pdf_url}", file=sys.stderr)
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            download(pdf_url, pdf_path)
        else:
            print(f"  reusing cached {pdf_path}", file=sys.stderr)

        counts = process_pdf(pdf_path, keep_txt_dir=txt_dir)
        stub = edition_stub_from_pdf_name(pdf_path.name) or f"{year}-{issue_id}"
        if per_edition:
            out_path = outdir / f"bm_{stub}.dict"
            n = write_dict(out_path, counts)
            print(f"  wrote {out_path} ({n} words, {sum(counts.values())} tokens)", file=sys.stderr)
        else:
            print(f"  extracted {len(counts)} words, {sum(counts.values())} tokens", file=sys.stderr)
        per_edition_counts.append((stub, counts))

    combined = merge_counters(counts for _, counts in per_edition_counts)
    combined_path = outdir / f"bm_{year}_combined.dict"
    n = write_dict(combined_path, combined)
    print(
        f"wrote {combined_path} ({n} words, {sum(combined.values())} tokens from {len(per_edition_counts)} editions)",
        file=sys.stderr,
    )

    source_path = outdir / f"SOURCE_{year}.txt"
    source_lines = [
        f"Brněnský metropolitan {year} — {'per-edition and ' if per_edition else ''}combined dictionary",
        "Extraction: alphabetic tokens length 2–40 from full issue PDF text",
        "Score: raw token frequency",
        "ASCII folding: False",
        f"Editions discovered: {len(per_edition_counts)}",
        "",
        "Editions:",
    ]
    for stub, counts in per_edition_counts:
        prefix = f"  bm_{stub}.dict" if per_edition else f"  {stub}"
        source_lines.append(f"{prefix}  ({len(counts)} words)")
    source_lines.append(f"  bm_{year}_combined.dict  ({len(combined)} words)")
    source_path.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
    return combined


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "inputs",
        nargs="*",
        help="PDF path(s) and/or PDF URL(s). Ignored when --fetch-year(s) is set.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .dict path for a single input, or combined output when multiple inputs are given",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("local/metropolitan"),
        help="Directory for fetch outputs (default: local/metropolitan)",
    )
    parser.add_argument(
        "--fetch-year",
        type=int,
        help="Download all released editions for this year and write a whole-year combined dict",
    )
    parser.add_argument(
        "--fetch-years",
        type=str,
        help="Same as --fetch-year for multiple years, e.g. 2020-2025 or 2020,2022,2026",
    )
    parser.add_argument(
        "--keep-txt",
        action="store_true",
        help="Also write extracted pdftotext output under outdir/txt",
    )
    parser.add_argument(
        "--per-input",
        action="store_true",
        help="With multiple inputs, also write one .dict per PDF beside --output/outdir",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    years: list[int] = []
    if args.fetch_year is not None:
        years.append(args.fetch_year)
    if args.fetch_years:
        years.extend(parse_year_list(args.fetch_years))
    years = sorted(set(years))

    if years:
        for year in years:
            # Per-edition outputs for the current year; whole-year combined only elsewhere.
            fetch_year(
                year,
                args.outdir,
                keep_sources=args.keep_txt,
                per_edition=year == 2026,
            )
        return 0

    if not args.inputs:
        raise SystemExit("Provide PDF path(s)/URL(s), or use --fetch-year(s)")

    with tempfile.TemporaryDirectory(prefix="metro-pdf-") as tmp:
        tmp_dir = Path(tmp)
        pdf_paths: list[Path] = []
        for item in args.inputs:
            if re.match(r"^https?://", item, re.IGNORECASE):
                filename = pdf_filename_from_url(item, "download.pdf")
                dest = tmp_dir / filename
                print(f"downloading {item}", file=sys.stderr)
                download(item, dest)
                pdf_paths.append(dest)
            else:
                path = Path(item)
                if not path.is_file():
                    raise SystemExit(f"PDF not found: {path}")
                pdf_paths.append(path)

        keep_txt_dir = None
        if args.keep_txt:
            if args.output and len(pdf_paths) == 1:
                keep_txt_dir = args.output.parent
            else:
                keep_txt_dir = args.outdir / "txt"

        counters: list[Counter[str]] = []
        for pdf_path in pdf_paths:
            counts = process_pdf(pdf_path, keep_txt_dir=keep_txt_dir)
            counters.append(counts)
            if args.per_input or len(pdf_paths) > 1:
                stub = edition_stub_from_pdf_name(pdf_path.name) or pdf_path.stem
                if args.output and len(pdf_paths) == 1:
                    out_path = args.output
                elif args.output and args.output.suffix == ".dict" and len(pdf_paths) > 1:
                    out_path = args.output.parent / f"bm_{stub}.dict"
                else:
                    out_path = args.outdir / f"bm_{stub}.dict"
                n = write_dict(out_path, counts)
                print(f"wrote {out_path} ({n} words)", file=sys.stderr)

        if len(pdf_paths) == 1:
            out_path = args.output or (
                args.outdir / f"bm_{edition_stub_from_pdf_name(pdf_paths[0].name) or pdf_paths[0].stem}.dict"
            )
            n = write_dict(out_path, counters[0])
            print(f"wrote {out_path} ({n} words)", file=sys.stderr)
            return 0

        combined = merge_counters(counters)
        out_path = args.output or (args.outdir / "combined.dict")
        n = write_dict(out_path, combined)
        print(f"wrote {out_path} ({n} words)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
