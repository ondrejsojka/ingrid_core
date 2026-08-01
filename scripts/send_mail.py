#!/usr/bin/env python3
"""Send an HTML file (plus optional attachments) through Resend.

Kept deliberately dumb: the crossword renderers produce complete HTML documents, this
only carries them. Same hard-won details as the crossword-email skill —

* the API key in `~/.env` is send-only; it cannot list domains or read sent mail,
* `from` must be `onboarding@resend.dev` until a domain is verified, and that shared
  sender only delivers to the account owner's own address,
* POST with curl, not urllib: api.resend.com answers urllib with 403 error code 1010,
* the payload goes through a temp file, because 100 kB of Czech HTML in argv gets mangled.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys
import tempfile


def load_key(env_path="~/.env"):
    for ln in open(os.path.expanduser(env_path), encoding="utf-8"):
        if ln.startswith("RESEND_API_KEY"):
            return ln.split("=", 1)[1].strip()
    sys.exit(f"RESEND_API_KEY not found in {env_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", required=True, help="HTML document to send as the body")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--to", action="append", required=True)
    ap.add_argument("--cc", action="append", default=[])
    ap.add_argument("--attach", action="append", default=[])
    ap.add_argument("--from-address", default="onboarding@resend.dev")
    ap.add_argument("--text", default="Tento e-mail je v HTML.")
    ap.add_argument("--env", default="~/.env")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = {
        "from": args.from_address,
        "to": args.to,
        "subject": args.subject,
        "html": open(args.html, encoding="utf-8").read(),
        "text": args.text,
    }
    if args.cc:
        payload["cc"] = args.cc
    if args.attach:
        payload["attachments"] = [
            {
                "filename": os.path.basename(p),
                "content": base64.b64encode(open(p, "rb").read()).decode("ascii"),
                "content_type": mimetypes.guess_type(p)[0] or "application/octet-stream",
            }
            for p in args.attach
        ]
    size = len(json.dumps(payload))
    print(f"payload {size/1024:.0f} kB, {len(args.attach)} attachment(s) -> {args.to}")
    if size > 38 * 1024 * 1024:
        sys.exit("payload over Resend's 40 MB limit")
    if args.dry_run:
        return

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
        path = fh.name
    try:
        proc = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
             "https://api.resend.com/emails",
             "-H", f"Authorization: Bearer {load_key(args.env)}",
             "-H", "Content-Type: application/json",
             "--data-binary", f"@{path}"],
            capture_output=True, text=True, check=True,
        )
    finally:
        os.unlink(path)
    body, _, status = proc.stdout.rpartition("\n")
    if status.strip() != "200":
        sys.exit(f"resend failed: HTTP {status.strip()} {body}")
    print(body)


if __name__ == "__main__":
    main()
