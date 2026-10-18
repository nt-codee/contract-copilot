#!/usr/bin/env python3
"""Fill a workspace with the showcase company's contracts. Resumable. Standard library only.

    SEED_ID_TOKEN=<admin ID token> python3 scripts/seed_showcase.py \
        --base-url https://<service-url> --workspace-id <workspace id> [--pause 3] [--dry-run]
    python3 scripts/seed_showcase.py --base-url http://localhost:8088 --dry-run     # list only, no network

What it does, in order:
1. Uploads every company policy in samples/company/policies.json (the contracting policy, plus the people,
   premises and equipment policies) as a company document, unless a document with that filename is already
   listed in the workspace.
2. Uploads every PDF listed in samples/showcase/index.json as a contract review, one at a time with a pause
   between uploads. A contract whose filename already appears in the workspace's reviews is skipped, so an
   interrupted run can simply be started again.
3. Prints a summary table: id, status, seconds, overall risk, findings, key dates.

The ID token is read from the environment variable SEED_ID_TOKEN (or --token-env to name another variable).
It is sent only as a Bearer header and is never printed. A server running with CLOUD=local also accepts
test tokens of the form test:<uid>:<email>[:hd=<domain>]. `--workspace-id` is sent as `X-Workspace-Id`;
omit it to use the caller's default workspace.

Each upload is a real review (a few model calls), so a run takes several minutes against a live service.
Platform administrators are exempt from the per-address and per-user review limits; other callers are not,
and a 429 is retried after a wait.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHOWCASE = REPO / "samples" / "showcase"
COMPANY = REPO / "samples" / "company"
FALLBACK_POLICY = "kopi-kita-contracting-policy.pdf"
PAUSE_S = 3.0
TIMEOUT_S = 300
RETRIES_429 = 3
WAIT_429_S = 60


class Client:
    def __init__(self, base: str, token: str, workspace_id: str):
        self.base, self._token, self.workspace_id = base.rstrip("/"), token, workspace_id

    def call(self, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None):
        """(status, parsed JSON or None). HTTP errors return their status instead of raising."""
        for attempt in range(RETRIES_429 + 1):
            req = urllib.request.Request(self.base + path, data=body, method=method)
            if self._token:
                req.add_header("Authorization", f"Bearer {self._token}")
            if self.workspace_id:
                req.add_header("X-Workspace-Id", self.workspace_id)
            if content_type:
                req.add_header("Content-Type", content_type)
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                    raw = r.read()
                    return r.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                if e.code == 429 and attempt < RETRIES_429:
                    print(f"  rate limited, waiting {WAIT_429_S}s", file=sys.stderr, flush=True)
                    time.sleep(WAIT_429_S)
                    continue
                return e.code, parsed
            except (urllib.error.URLError, TimeoutError) as e:
                return 0, {"error": {"message": str(getattr(e, "reason", e))}}
        return 429, None


def multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            "Content-Type: application/pdf\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def error_text(parsed) -> str:
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            return str(err.get("message", ""))[:60]
    return ""


def load_index() -> list[dict]:
    path = SHOWCASE / "index.json"
    if not path.is_file():
        raise SystemExit(f"{path} not found: run python3 samples/showcase/make_showcase.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def load_policies() -> list[Path]:
    """Company policy PDFs to upload, in order: those in samples/company/policies.json, else the original one."""
    path = COMPANY / "policies.json"
    names = ([e["filename"] for e in json.loads(path.read_text(encoding="utf-8"))] if path.is_file()
             else [FALLBACK_POLICY])
    return [COMPANY / n for n in names]


def seed_policies(c: Client, policies: list[Path]) -> list[str]:
    status, docs = c.call("GET", "/api/company/docs")
    if status != 200:
        return [f"policies: could not list company documents (HTTP {status})"]
    have = {d.get("filename") for d in docs or []}
    out = []
    for pol in policies:
        if pol.name in have:
            out.append(f"policy {pol.name}: already there, skipped")
            continue
        body, ctype = multipart("file", pol.name, pol.read_bytes())
        status, doc = c.call("POST", "/api/company/docs", body=body, content_type=ctype)
        out.append(f"policy {pol.name}: uploaded ({doc.get('pages', '?')} pages)" if status == 200 and doc
                   else f"policy {pol.name}: upload failed (HTTP {status}) {error_text(doc)}")
        time.sleep(1)
    return out


def upload_row(c: Client, entry: dict) -> list[str]:
    path = SHOWCASE / entry["filename"]
    t0 = time.monotonic()
    body, ctype = multipart("file", entry["filename"], path.read_bytes())
    status, r = c.call("POST", "/api/reviews", body=body, content_type=ctype)
    secs = f"{time.monotonic() - t0:.1f}"
    if status != 200 or not isinstance(r, dict):
        return [entry["id"], f"HTTP {status}", secs, "-", "-", "-", error_text(r) or "-"]
    return [entry["id"], "ok", secs, r.get("verdict", {}).get("overall_risk", "-"),
            str(len(r.get("findings", []))), str(len(r.get("key_dates", []))), ""]


def print_table(rows: list[list[str]]) -> None:
    head = ["id", "status", "seconds", "risk", "findings", "key dates", "note"]
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    for r in [head, ["-" * w for w in widths], *rows]:
        print("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--workspace-id", default="", help="sent as X-Workspace-Id; omit for the caller's default workspace")
    ap.add_argument("--token-env", default="SEED_ID_TOKEN", help="environment variable holding the ID token")
    ap.add_argument("--pause", type=float, default=PAUSE_S, help=f"seconds between uploads (default {PAUSE_S:g})")
    ap.add_argument("--dry-run", action="store_true", help="list what would be uploaded; make no network calls")
    args = ap.parse_args(argv)

    index = load_index()
    policies = load_policies()
    missing = ([e["filename"] for e in index if not (SHOWCASE / e["filename"]).is_file()]
               + [p.name for p in policies if not p.is_file()])
    if missing:
        raise SystemExit(f"missing files: {', '.join(missing)}")

    if args.dry_run:
        where = f"{args.base_url} (workspace {args.workspace_id or 'default'})"
        print(f"dry run against {where}; no network calls made")
        for p in policies:
            print(f"policy    : POST /api/company/docs  {p.name}  (skipped if already listed)")
        for i, e in enumerate(index, 1):
            print(f"{i:>2} contract: POST /api/reviews       {e['filename']}  [{e['type']}, {e['language']}, {e['jurisdiction']}]"
                  "  (skipped if already reviewed)")
        print(f"total: {len(policies)} policies + {len(index)} contracts, {args.pause:g}s pause between uploads")
        return 0

    token = os.environ.get(args.token_env, "").strip()
    c = Client(args.base_url, token, args.workspace_id.strip())
    if not token:
        print(f"note: {args.token_env} is empty; fine for a CLOUD=local server, rejected by a deployed one", file=sys.stderr)

    for line in seed_policies(c, policies):
        print(line, flush=True)
    status, existing = c.call("GET", "/api/reviews?limit=200")
    if status != 200:
        print(f"could not list existing reviews (HTTP {status}); is the token valid for this workspace?", file=sys.stderr)
        return 1
    present = {r.get("filename") for r in existing or []}

    rows, uploaded = [], 0
    for e in index:
        if e["filename"] in present:
            rows.append([e["id"], "skipped", "-", "-", "-", "-", "already present"])
            continue
        if uploaded:
            time.sleep(args.pause)
        rows.append(upload_row(c, e))
        uploaded += 1
        print(f"  {rows[-1][0]}: {rows[-1][1]} in {rows[-1][2]}s", file=sys.stderr, flush=True)
    print_table(rows)
    failed = [r for r in rows if r[1] not in ("ok", "skipped")]
    print(f"\n{sum(r[1] == 'ok' for r in rows)} uploaded, {sum(r[1] == 'skipped' for r in rows)} skipped, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
