#!/usr/bin/env python3
"""Smoke-check a deployed Contract Co-pilot in well under a minute. Standard library only.

    python3 scripts/smoke_live.py --base-url https://<service-url> [--token "$ID_TOKEN"]

Prints one PASS/FAIL line per check and exits 1 if any check fails. Read-only: it only issues GETs, plus
two unauthenticated POSTs that must be refused. It never spends a model call.

--token (or env SMOKE_ID_TOKEN) is an Identity Platform ID token and adds the signed-in checks. The token
is sent only as a bearer header and is never printed.
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

TIMEOUT_S = 15
DEADLINE_S = 60


def call(method: str, url: str, *, token: str | None = None, body: bytes | None = None,
         content_type: str | None = None):
    """(status, content-type, raw bytes). HTTP errors return their status; network errors raise."""
    req = urllib.request.Request(url, data=body, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def jsonify(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def error_code(raw: bytes) -> str | None:
    d = jsonify(raw)
    return ((d or {}).get("error") or {}).get("code") if isinstance(d, dict) else None


class Smoke:
    def __init__(self, base: str, token: str | None):
        self.base, self.token = base.rstrip("/"), token
        self.results: list[tuple[bool, str, str]] = []
        self.t0 = time.monotonic()

    def check(self, name: str, fn) -> None:
        if time.monotonic() - self.t0 > DEADLINE_S:
            ok, detail = False, f"skipped: over the {DEADLINE_S}s budget"
        else:
            try:
                detail = fn()
                ok = True
            except AssertionError as e:
                ok, detail = False, str(e) or "assertion failed"
            except Exception as e:  # network error, timeout, bad JSON ...
                ok, detail = False, f"{type(e).__name__}: {e}"
        if self.token:
            detail = detail.replace(self.token, "<token>")
        self.results.append((ok, name, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)

    def url(self, path: str) -> str:
        return self.base + path

    # ---- the checks ---------------------------------------------------------------------------

    def run(self) -> bool:
        u = self.url

        def health():
            s, _, raw = call("GET", u("/api/health"))
            assert s == 200, f"HTTP {s}"
            assert (jsonify(raw) or {}).get("ok") is True, f"body {raw[:80]!r}"
            return ""

        self.check("/api/health ok", health)

        for path in ("/", "/privacy", "/terms"):
            def page(path=path):
                s, ct, raw = call("GET", u(path))
                assert s == 200, f"HTTP {s}"
                assert "html" in ct.lower(), f"content-type {ct!r}"
                head = raw[:2000].lower()
                assert b"<!doctype html" in head or b"<html" in head, "not an HTML document"
                return ""
            self.check(f"GET {path} is 200 HTML", page)

        def auth_config():
            s, _, raw = call("GET", u("/api/auth/config"))
            assert s == 200, f"HTTP {s}"
            d = jsonify(raw)
            assert isinstance(d, dict), "not a JSON object"
            missing = [k for k in ("apiKey", "authDomain", "projectId") if not d.get(k)]
            assert not missing, f"missing {missing}"
            assert str(d["authDomain"]).endswith(".firebaseapp.com"), f"authDomain {d['authDomain']!r}"
            return "apiKey present"

        self.check("/api/auth/config has apiKey, authDomain (.firebaseapp.com), projectId", auth_config)

        def me_anon():
            s, _, raw = call("GET", u("/api/me"))
            assert s == 401, f"HTTP {s}"
            return error_code(raw) or ""

        self.check("GET /api/me without token is 401", me_anon)

        def me_test_token():
            s, _, raw = call("GET", u("/api/me"), token="test:x:y")
            assert s == 401, f"HTTP {s}: test tokens must be rejected on a deployed service"
            return error_code(raw) or ""

        self.check("GET /api/me with 'Bearer test:x:y' is 401", me_test_token)

        def upload_anon():
            boundary = uuid.uuid4().hex
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.pdf\"\r\n"
                    "Content-Type: application/pdf\r\n\r\n%PDF-1.4\r\n"
                    f"--{boundary}--\r\n").encode()
            s, _, raw = call("POST", u("/api/reviews"), body=body,
                             content_type=f"multipart/form-data; boundary={boundary}")
            assert s == 401, f"HTTP {s}"
            code = error_code(raw)
            assert code == "sign_in_required", f"error code {code!r}"
            return ""

        self.check("POST /api/reviews without token is 401 sign_in_required", upload_anon)

        def reviews():
            s, _, raw = call("GET", u("/api/reviews"))
            assert s == 200, f"HTTP {s}"
            d = jsonify(raw)
            assert isinstance(d, list), "not a JSON list"
            return f"{len(d)} demo reviews"

        self.check("GET /api/reviews is 200 list", reviews)

        def samples():
            s, _, raw = call("GET", u("/api/samples"))
            assert s == 200, f"HTTP {s}"
            d = jsonify(raw)
            assert isinstance(d, list) and d, "empty or not a list"
            return f"{len(d)} samples"

        self.check("GET /api/samples is non-empty", samples)

        def knowledge():
            s, _, raw = call("GET", u("/api/knowledge?q=penalty"))
            assert s == 200, f"HTTP {s}"
            d = jsonify(raw)
            assert isinstance(d, list) and d, "empty or not a list"
            with_src = [e for e in d if e.get("source_url")]
            assert with_src, f"none of {len(d)} entries has source_url"
            law = [e for e in d if e.get("kind") in ("law", "case")]
            assert all(e.get("source_url") for e in law), (
                f"{sum(1 for e in law if not e.get('source_url'))} of {len(law)} law/case entries lack source_url")
            return f"{len(d)} entries, {len(with_src)} with source_url"

        self.check("GET /api/knowledge?q=penalty is non-empty, law/case entries have source_url", knowledge)

        def search():
            s, _, raw = call("GET", u("/api/search?q=terminate"))
            assert s == 200, f"HTTP {s}"
            assert isinstance(jsonify(raw), list), "not a JSON list"
            return ""

        self.check("GET /api/search?q=terminate is 200", search)

        def admin_anon():
            s, _, _ = call("GET", u("/api/admin/overview"))
            assert s in (401, 403), f"HTTP {s}"
            return f"HTTP {s}"

        self.check("GET /api/admin/overview without token is 401 or 403", admin_anon)

        def nope():
            s, ct, raw = call("GET", u(f"/api/nope-{uuid.uuid4().hex[:8]}"))
            assert s == 404, f"HTTP {s}: the SPA fallback may be swallowing /api"
            assert "html" not in ct.lower(), "an HTML page was served for an /api path"
            return ""

        self.check("GET /api/<random> is 404, not the SPA page", nope)

        if self.token:
            def me():
                s, _, raw = call("GET", u("/api/me"), token=self.token)
                assert s == 200, f"HTTP {s} {error_code(raw) or ''}".strip()
                d = jsonify(raw) or {}
                assert d.get("uid") and d.get("email"), "no uid/email in /api/me"
                return f"platform_admin={bool(d.get('platform_admin'))}"

            self.check("signed in: GET /api/me is 200", me)

            def workspaces():
                s, _, raw = call("GET", u("/api/workspaces"), token=self.token)
                assert s == 200, f"HTTP {s}"
                d = jsonify(raw)
                assert isinstance(d, list) and d, "no workspaces"
                return f"{len(d)} workspace(s)"

            self.check("signed in: GET /api/workspaces is non-empty", workspaces)

            def activity():
                s, _, raw = call("GET", u("/api/activity"), token=self.token)
                assert s == 200, f"HTTP {s}"
                assert isinstance(jsonify(raw), list), "not a JSON list"
                return ""

            self.check("signed in: GET /api/activity is 200", activity)

        failed = [r for r in self.results if not r[0]]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed in "
              f"{time.monotonic() - self.t0:.1f}s", flush=True)
        return not failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--token", default=os.environ.get("SMOKE_ID_TOKEN", ""),
                    help="Identity Platform ID token for the signed-in checks (or env SMOKE_ID_TOKEN); never printed")
    args = ap.parse_args(argv)
    return 0 if Smoke(args.base_url, args.token.strip() or None).run() else 1


if __name__ == "__main__":
    sys.exit(main())
