#!/usr/bin/env python3
"""Fill a running instance with demo data so it is not an empty shell. Idempotent. Standard library only.

    python3 scripts/seed_demo.py --base-url https://<service-url> [--refresh]

1. Uploads samples/company/kopi-kita-contracting-policy.pdf as a company document, unless a document
   with that filename is already listed.
2. Reviews every sample in samples/contracts/index.json, one at a time with a 2 s pause. The server
   caches sample reviews, so a re-run is cheap; --refresh sends ?refresh=1 and re-runs the model.
3. Prints a table: id, status, seconds, findings, citations, policy breaches, key dates.

Each real review is a few Gemini calls and counts against the server's per-IP rate limit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POLICY = REPO / "samples" / "company" / "kopi-kita-contracting-policy.pdf"
PAUSE_S = 2.0
TIMEOUT_S = 300


def call(method: str, url: str, *, body: bytes | None = None, content_type: str | None = None):
    """(status, parsed JSON or None). HTTP errors return their status instead of raising."""
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, None


def multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def seed_policy(base: str) -> str:
    status, docs = call("GET", f"{base}/api/company/docs")
    if status != 200:
        return f"company docs: list failed (HTTP {status})"
    if any(d.get("filename") == POLICY.name for d in docs or []):
        return f"company doc {POLICY.name}: already there"
    if not POLICY.is_file():
        return f"company doc: {POLICY} not found"
    body, ctype = multipart("file", POLICY.name, POLICY.read_bytes())
    status, doc = call("POST", f"{base}/api/company/docs", body=body, content_type=ctype)
    return (f"company doc {POLICY.name}: uploaded ({doc['pages']} pages)" if status == 200
            else f"company doc {POLICY.name}: upload failed (HTTP {status})")


def review_row(base: str, sample_id: str, refresh: bool) -> list[str]:
    t0 = time.monotonic()
    url = f"{base}/api/reviews/sample/{sample_id}" + ("?refresh=1" if refresh else "")
    status, r = call("POST", url)
    secs = f"{time.monotonic() - t0:.1f}"
    if status != 200 or not r:
        msg = ((r or {}).get("error") or {}).get("message", "") if isinstance(r, dict) else ""
        return [sample_id, f"HTTP {status}", secs, "-", "-", "-", msg[:40] or "-"]
    findings = r.get("findings", [])
    return [sample_id, "ok", secs, str(len(findings)), str(sum(len(f.get("citations", [])) for f in findings)),
            str(sum(1 for f in findings if f.get("policy_breach"))), str(len(r.get("key_dates", [])))]


def print_table(rows: list[list[str]]) -> None:
    head = ["id", "status", "seconds", "findings", "citations", "policy breaches", "key dates"]
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    for r in [head, ["-" * w for w in widths], *rows]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--refresh", action="store_true", help="re-run each sample review instead of using the cache")
    ap.add_argument("--pause", type=float, default=PAUSE_S, help="seconds between calls (default 2)")
    args = ap.parse_args(argv)
    base = args.base_url.rstrip("/")

    print(seed_policy(base), flush=True)
    status, samples = call("GET", f"{base}/api/samples")
    if status != 200 or not samples:
        print(f"could not list samples (HTTP {status})", file=sys.stderr)
        return 1
    rows = []
    for s in samples:
        time.sleep(args.pause)
        rows.append(review_row(base, s["id"], args.refresh))
        print(f"  {rows[-1][0]}: {rows[-1][1]} in {rows[-1][2]}s", file=sys.stderr, flush=True)
    print_table(rows)
    return 0 if all(r[1] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
