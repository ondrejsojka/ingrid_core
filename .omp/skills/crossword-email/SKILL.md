---
name: crossword-email
description: Render a generated crossword fill plus its Czech clue set as an HTML email and send it with Resend. Use when asked to email a crossword, send a puzzle, mail the newest fill, or deliver a grid with clues to a reader.
---

# Emailing a crossword via Resend

Turns a raw fill (`local/trials/*_fill.txt`) plus a clue table into a self-contained
HTML mail: numbered grid, Across/Down clue lists, solution grid, answer key with
band/shape tags, and a metrics table. One command does render + send.

## Do this

```bash
python3 .omp/skills/crossword-email/send_crossword_email.py \
  --fill local/trials/no_marked_n33_fill.txt \
  --clues local/trials/no_marked_n33_clues.tsv \
  --subject "Metropolitan: nejnovější mřížka" \
  --to ondrej.sojka@gmail.com \
  --intro intro.html \
  --out local/metropolitan-krizovka-2026-07-30.html
```

Add `--dry-run` to write the HTML and skip sending. Always eyeball the HTML in the
browser tool before sending — the grid is a `<table>`, and a bad block pattern is
obvious visually and invisible in the source.

## Inputs

- `--fill`: the grid as the solver prints it, `#` for blocks, one row per line,
  letters lowercase with diacritics.
- `--clues`: TSV, one line per entry: `answer<TAB>clue[<TAB>band<TAB>shape]`.
  `answer` must match the fill exactly (lowercase, diacritics). `band` is `S`/`O`/`H`
  (slovník / obraz / hra, see `CLUES.md` § 9); `shape` is free text (`tázací`,
  `výpustka`, …). Entries with no row, or with clue `-`, are rendered as
  „— vada fillu" and excluded from the metrics.
- `--intro`: optional HTML fragment dropped under the headline. This is where the
  commentary goes; keep it out of the script.

## Hard-won details

- **The key is send-only.** `~/.env` holds `RESEND_API_KEY`. It cannot list domains
  or read sent mail — `GET /domains` returns 401 `restricted_api_key`. Don't try to
  verify delivery through the API; check with the recipient.
- **`from` must be `onboarding@resend.dev`** unless a verified domain has been set up.
  That shared sender only delivers to the account owner's own address.
- **Send with `curl`, not `urllib`.** `urllib.request` to `api.resend.com` gets
  `403 error code: 1010` (Cloudflare rejects the default Python user agent). The
  script shells out to `curl --data-binary @payload.json`; keep it that way.
- **Payload goes through a file.** Czech diacritics plus 25 kB of HTML inline in an
  argv string is how you get a mangled mail; the script writes JSON to a temp file.
- **Gmail keeps `<style>` in `<head>`** for this layout, so the grid CSS survives.
  Don't switch to inline styles unless something actually breaks.

## Clue conventions

The clue text rules live in `CLUES.md`; the email is only the carrier. The two things
worth re-checking before sending, because they are the ones that go wrong:

- median clue length ≤ 15 characters, maximum ≤ 34 (the Swedish box budget),
- no clue contains the root of its own answer.

The script prints both, plus the S/O/H split, so a bad set is visible before it ships.
