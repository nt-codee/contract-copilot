"""Security hardening: response headers, upload limits, filenames, rate limits, cross-workspace authz, log hygiene."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security import build_csp, clean_filename
from conftest import bearer
from pdfgen import make_pdf

PDF = make_pdf()


def tok(uid, domain=None):
    return f"test:{uid}:{uid}@{domain or 'gmail.test'}" + (f":hd={domain}" if domain else "")


def upload(c, name="a.pdf", data=PDF, headers=None):
    return c.post("/api/reviews", files={"file": (name, data, "application/pdf")}, headers=headers or {})


# ---- 1. response headers -----------------------------------------------------------------------------------


def test_security_headers_on_api_static_and_errors(client, anon):
    for r in (client.get("/api/reviews"), anon.get("/"), anon.get("/assets/app.js"), anon.get("/api/nope"),
              anon.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")})):  # a 401
        h = r.headers
        assert h["cross-origin-opener-policy"] == "same-origin-allow-popups", r.request.url
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert h["x-frame-options"] == "DENY"
        assert "camera=()" in h["permissions-policy"]
    assert client.get("/api/reviews").headers["cache-control"] == "no-store"  # per-user data
    assert "cache-control" not in anon.get("/assets/app.js").headers or "no-store" not in anon.get("/assets/app.js").headers["cache-control"]


def test_an_unhandled_500_carries_the_headers_and_no_detail(services, monkeypatch):
    monkeypatch.setattr(services.store, "list", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internal detail")))
    c = TestClient(create_app(services.settings, services), raise_server_exceptions=False)
    r = c.get("/api/reviews")
    assert r.status_code == 500 and "secret" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cross-origin-opener-policy"] == "same-origin-allow-popups"
    assert r.headers["cache-control"] == "no-store"


def test_hsts_only_over_https(anon):
    assert "strict-transport-security" not in anon.get("/api/health").headers
    assert anon.get("/api/health", headers={"X-Forwarded-Proto": "https"}).headers["strict-transport-security"].startswith("max-age=")


def test_csp_is_report_only_by_default_and_names_what_the_app_loads(anon):
    h = anon.get("/").headers
    assert "content-security-policy" not in h  # never blocks by default
    csp = h["content-security-policy-report-only"]
    for need in ("default-src 'self'", "https://apis.google.com", "https://lh3.googleusercontent.com",
                 "https://*.firebaseapp.com", "https://identitytoolkit.googleapis.com",
                 "https://securetoken.googleapis.com", "worker-src 'self' blob:", "'wasm-unsafe-eval'",
                 "object-src 'none'", "frame-ancestors 'none'"):
        assert need in csp, need
    assert "'unsafe-eval'" not in csp and "script-src 'self' https://apis.google.com 'wasm-unsafe-eval'" in csp


@pytest.mark.parametrize("mode,header,absent", [("enforce", "content-security-policy", "content-security-policy-report-only"),
                                                ("off", None, "content-security-policy")])
def test_csp_modes(services, mode, header, absent):
    import dataclasses

    c = TestClient(create_app(dataclasses.replace(services.settings, csp_mode=mode), services))
    h = c.get("/").headers
    assert absent not in h and (header is None or header in h)
    assert "content-security-policy-report-only" not in h or mode != "off"


def test_csp_adds_the_configured_auth_domain():
    csp = build_csp(json.dumps({"apiKey": "k", "authDomain": "auth.example.test", "projectId": "p"}))
    assert "https://auth.example.test" in csp
    assert build_csp("not json") == build_csp("")  # never fails startup


# ---- 2. uploads ----------------------------------------------------------------------------------------------


def test_oversized_upload_is_refused_before_it_is_read(client, services):
    big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 200_000)
    r = upload(client, data=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"
    assert services.store.list(owner=client.get("/api/workspaces").json()[0]["workspace"]["id"]) == []


def test_oversized_chunked_upload_without_content_length_is_stopped(api):
    """No Content-Length (chunked): the byte counter, not the header, must stop it."""
    import asyncio

    big = b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024)
    boundary = "bnd"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    chunks = [head] + [big[i:i + 1_000_000] for i in range(0, len(big), 1_000_000)] + [tail]
    sent = []

    async def go():
        q = list(chunks)

        async def receive():
            if q:
                return {"type": "http.request", "body": q.pop(0), "more_body": bool(q)}
            return {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)

        scope = {"type": "http", "method": "POST", "path": "/api/reviews", "raw_path": b"/api/reviews", "query_string": b"",
                 "headers": [(b"authorization", b"Bearer " + tok("zed").encode()),
                             (b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
                 "scheme": "http", "server": ("t", 80), "client": ("1.2.3.4", 1), "http_version": "1.1", "root_path": ""}
        await api(scope, receive, send)

    asyncio.run(go())
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert [m["status"] for m in starts] == [413]  # exactly one response: the 413, nothing from the aborted handler
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert json.loads(body)["error"]["code"] == "file_too_large"


def test_pdf_magic_page_and_empty_checks(client):
    assert upload(client, data=b"hello, not a pdf").json()["error"]["code"] == "not_a_pdf"
    assert upload(client, data=b"").status_code in (415, 422)
    assert upload(client, name="x.pdf", data=b"%PDF-1.4 garbage").json()["error"]["code"] == "unreadable_pdf"
    assert upload(client, data=make_pdf(["This page has enough words on it to count as text. " * 3] * 41)).json()["error"]["code"] == "too_many_pages"
    assert upload(client, data=make_pdf(["This page has enough words on it to count as text. " * 3] * 40)).status_code == 200  # the limit itself is accepted


def test_filename_is_cleaned_before_it_is_stored_or_shown(client):
    r = upload(client, name="..\\..\\evil/../vendor\u202egnp.pdf\x00 ")
    assert r.status_code == 200
    name = r.json()["filename"]
    assert "/" not in name and "\\" not in name and "\x00" not in name and "\u202e" not in name and ".." not in name
    long = upload(client, name="a" * 400 + ".pdf").json()["filename"]
    assert len(long) <= 120 and long.endswith(".pdf")
    assert upload(client, name="   ").json()["filename"] == "contract.pdf"
    ev = [e for e in client.get("/api/activity").json() if e["action"] == "uploaded_contract"]
    assert all("\u202e" not in (e["target_label"] or "") for e in ev)


def test_clean_filename_unit():
    assert clean_filename("C:\\Users\\me\\Contract (1).pdf", "x.pdf") == "Contract (1).pdf"
    assert clean_filename("a\nb\tc.pdf", "x.pdf") == "a b c.pdf"
    assert clean_filename(None, "x.pdf") == "x.pdf" and clean_filename("../", "x.pdf") == "x.pdf"
    assert clean_filename("ok.pdf", "x.pdf") == "ok.pdf"
    assert clean_filename("rtl\u202e.pdf", "x.pdf") == "rtl.pdf"


def test_company_doc_filename_is_cleaned_too(client):
    d = client.post("/api/company/docs", files={"file": ("../../policy\u202e.pdf", PDF, "application/pdf")})
    assert d.status_code == 200 and d.json()["filename"] == "policy.pdf"


def test_per_ip_cap_applies_to_signed_in_users_and_a_per_user_cap_on_top(services, monkeypatch):
    import dataclasses

    st = dataclasses.replace(services.settings, rate_limit_per_hour=1000, rate_limit_user_per_hour=2)
    api = create_app(st, services)
    a = TestClient(api, headers=bearer(tok("ann")))
    b = TestClient(api, headers=bearer(tok("bea")))
    assert upload(a).status_code == 200 and upload(a).status_code == 200
    r = upload(a)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert upload(b).status_code == 200  # another user from the same address is unaffected by ann's cap
    # and the per-IP cap (default 10/h) binds signed-in users as well
    st2 = dataclasses.replace(services.settings, rate_limit_per_hour=2, rate_limit_user_per_hour=1000)
    c = TestClient(create_app(st2, services), headers=bearer(tok("cy")))
    assert [upload(c).status_code for _ in range(3)] == [200, 200, 429]


def test_default_limits_are_ten_per_ip_and_thirty_per_user(env):
    from app.config import Settings

    s = Settings()
    assert (s.rate_limit_per_hour, s.rate_limit_user_per_hour, s.max_bytes, s.max_pages) == (10, 30, 10 * 1024 * 1024, 40)


def test_a_sample_copy_costs_no_rate_slot(services):
    import dataclasses

    api = create_app(dataclasses.replace(services.settings, rate_limit_per_hour=1, rate_limit_user_per_hour=1), services)
    TestClient(api).post("/api/reviews/sample/demo-01", headers={"X-Workspace-Id": "demo"})  # seeds the shared demo copy (one real run)
    me = TestClient(api, headers=bearer(tok("dee")))
    assert me.post("/api/reviews/sample/demo-01").status_code == 200  # a free copy, no model call


def all_routes(app):
    """Every route, including those mounted with include_router (they sit behind an _IncludedRouter wrapper)."""
    for r in app.routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from all_routes(inner)
        else:
            yield r


# ---- 3. authorisation: every route that takes an id --------------------------------------------------------------


def test_every_id_route_hides_other_workspaces_with_404(api):
    """Walks the app's routes so a new id-taking route cannot be forgotten: user B, in another workspace, must get
    404 (never 403, never data) for user A's review, policy, workspace and member."""
    a = TestClient(api, headers=bearer(tok("ann", "acme.test")))
    b = TestClient(api, headers=bearer(tok("bob", "other.test")))
    rid = upload(a).json()["id"]
    doc = a.post("/api/company/docs", files={"file": ("p.pdf", PDF, "application/pdf")}).json()["id"]
    wid = a.get("/api/workspaces").json()[0]["workspace"]["id"]
    inv = a.post(f"/api/workspaces/{wid}/invites").json()["code"]
    fills = {"review_id": rid, "doc_id": doc, "workspace_id": wid, "uid": "ann", "code": inv}
    seen = set()
    for route in all_routes(api):
        path = getattr(route, "path", "")
        params = re.findall(r"{(\w+)(?::\w+)?}", path)
        if not params or not path.startswith("/api/") or path.startswith(("/api/admin", "/api/knowledge", "/api/playbook")):
            continue
        if path == "/api/invites/{code}/accept" or path == "/api/reviews/sample/{sample_id}":
            continue  # an invite code is a bearer secret by design; samples are public
        if path == "/api/samples/{sample_id}/pdf":
            # Public synthetic source, accessible equally from both workspaces. Private id routes below
            # still require 404; a preview must never serve either workspace's uploaded contract.
            for caller in (a, b):
                preview = caller.get("/api/samples/demo-01/pdf")
                assert preview.status_code == 200 and preview.content.startswith(b"%PDF-")
                assert caller.get(f"/api/samples/{rid}/pdf").status_code == 404
            continue
        url = path
        for p in params:
            assert p in fills, f"authz test has no fixture for {{{p}}} in {path}: add one"
            url = url.replace("{" + p + ":path}", fills[p]).replace("{" + p + "}", fills[p])
        for method in route.methods - {"HEAD", "OPTIONS"}:
            kw = {"json": {"role": "member", "name": "x"}} if method in ("PATCH", "PUT", "POST") and "reviews" not in url and "company" not in url else {}
            r = b.request(method, url, **kw)
            seen.add((method, path))
            assert r.status_code == 404, (method, url, r.status_code, r.text[:120])
            assert r.status_code != 403
    for want in [("GET", "/api/reviews/{review_id}"), ("DELETE", "/api/reviews/{review_id}"), ("GET", "/api/reviews/{review_id}/pdf"),
                 ("GET", "/api/reviews/{review_id}/history"), ("GET", "/api/reviews/{review_id}/export"),
                 ("POST", "/api/reviews/{review_id}/negotiation"), ("DELETE", "/api/company/docs/{doc_id}"),
                 ("GET", "/api/company/docs/{doc_id}/pdf"), ("GET", "/api/workspaces/{workspace_id}/overview"),
                 ("POST", "/api/workspaces/{workspace_id}/invites"), ("PATCH", "/api/workspaces/{workspace_id}/members/{uid}"),
                 ("DELETE", "/api/workspaces/{workspace_id}/members/{uid}"), ("GET", "/api/workspaces/{workspace_id}/members")]:
        assert want in seen, f"route not covered: {want}"
    # nothing of A's was changed by B's attempts
    assert a.get(f"/api/reviews/{rid}").status_code == 200 and len(a.get("/api/company/docs").json()) == 1
    assert len(a.get(f"/api/workspaces/{wid}/members").json()) == 1


def test_anonymous_gets_404_for_private_ids_not_data(api):
    a = TestClient(api, headers=bearer(tok("ann")))
    rid = upload(a).json()["id"]
    anon = TestClient(api)
    for path in (f"/api/reviews/{rid}", f"/api/reviews/{rid}/pdf", f"/api/reviews/{rid}/history", f"/api/reviews/{rid}/export"):
        assert anon.get(path).status_code == 404, path


def test_a_workspace_playbook_override_is_not_readable_through_the_knowledge_routes(api):
    a = TestClient(api, headers=bearer(tok("ann", "acme.test")))
    b = TestClient(api, headers=bearer(tok("bob", "other.test")))
    wid = a.get("/api/workspaces").json()[0]["workspace"]["id"]
    clause = a.get("/api/playbook?contract_type=nda").json()[0]["clause_type"]
    put = {"standard": "SECRET-STANDARD-XYZ", "red_flags": [], "ask": "SECRET-ASK", "note": ""}
    assert a.put(f"/api/playbook/nda/{clause}", json=put).status_code == 200
    for path in (f"/api/knowledge/playbook:{wid}/nda/{clause}", f"/api/knowledge/playbook:{wid}/nda/{clause}/raw", "/api/knowledge?q=SECRET-STANDARD-XYZ",
                 "/api/playbook?contract_type=nda"):
        r = b.get(path)
        assert "SECRET-STANDARD-XYZ" not in r.text and "SECRET-ASK" not in r.text, path
    anon = TestClient(api)
    assert "SECRET-STANDARD-XYZ" not in anon.get(f"/api/knowledge/playbook:{wid}/nda/{clause}").text
    assert "SECRET-STANDARD-XYZ" not in anon.get("/api/playbook?contract_type=nda").text


def test_a_crafted_workspace_header_cannot_reach_another_workspace(api):
    a = TestClient(api, headers=bearer(tok("ann", "acme.test")))
    b = TestClient(api, headers=bearer(tok("bob", "other.test")))
    wid = a.get("/api/workspaces").json()[0]["workspace"]["id"]
    rid = upload(a).json()["id"]
    for h in ({"X-Workspace-Id": wid}, {"X-Workspace-Id": "demo"}, {"X-Workspace-Id": "../" + wid}, {"X-Workspace-Id": wid + "\x00"}):
        try:
            r = b.get(f"/api/reviews/{rid}", headers=h)
        except Exception:  # the HTTP client may refuse a NUL byte in a header
            continue
        assert r.status_code in (403, 404), h
        assert rid not in r.text


# ---- 4. nothing sensitive in the logs -----------------------------------------------------------------------------


def test_a_rejected_token_is_never_logged(caplog, monkeypatch):
    import app.adapters.gcp as gcp
    from google.oauth2 import id_token

    secret = "eyJSECRETHEADER.eyJSECRETPAYLOAD.SECRETSIG"

    def fake(token, request, audience=None, **kw):
        raise ValueError(f"Wrong number of segments in token: {token}")  # what google-auth really does

    monkeypatch.setattr(id_token, "verify_firebase_token", fake)
    from app.config import Settings
    from app.ports import InvalidToken

    v = gcp.IdentityPlatformVerifier(Settings())
    with caplog.at_level(logging.DEBUG), pytest.raises(InvalidToken) as ei:
        v.verify(secret)
    assert "SECRET" not in caplog.text and "SECRET" not in str(ei.value)
    assert "ValueError" in caplog.text


def test_verifier_pins_the_issuer(monkeypatch):
    import app.adapters.gcp as gcp
    from google.oauth2 import id_token

    from app.config import Settings
    from app.ports import InvalidToken

    claims = {"user_id": "u", "email": "a@b.test", "email_verified": True, "iss": "https://securetoken.google.com/someone-else"}
    monkeypatch.setattr(id_token, "verify_firebase_token", lambda *a, **k: dict(claims))
    v = gcp.IdentityPlatformVerifier(Settings())
    with pytest.raises(InvalidToken):
        v.verify("t")
    claims["iss"] = "https://securetoken.google.com/contract-copilot-abc26"
    assert v.verify("t").uid == "u"
    claims.pop("iss")
    with pytest.raises(InvalidToken):
        v.verify("t")


def test_a_full_review_run_does_not_log_tokens_contract_text_or_emails(client, caplog):
    with caplog.at_level(logging.DEBUG):
        r = upload(client, name="vendor-secret-name.pdf")
        client.post(f"/api/reviews/{r.json()['id']}/negotiation")
        client.get("/api/search", params={"q": "private search words"})
        client.get("/api/me")
    text = caplog.text
    assert "test:alice" not in text and "Bearer" not in text
    for finding in r.json()["findings"]:
        assert finding["quote"] not in text, "a contract quote reached the logs"
    assert "alice@example.test" not in text


def test_a_model_answer_that_fails_validation_is_logged_without_its_content(caplog, monkeypatch):
    import app.adapters.gcp as gcp
    from pydantic import BaseModel

    class Out(BaseModel):
        a: int

    secret = "CONTRACT TEXT THE MODEL ECHOED"

    class Resp:
        text = json.dumps({"a": secret})
        parsed = None
        usage_metadata = None

    llm = gcp.GeminiLLM.__new__(gcp.GeminiLLM)
    llm.model, llm._thinking, llm.calls, llm._record_dir = "m", "", 0, None
    monkeypatch.setattr(llm, "_call", lambda *a, **k: Resp())
    monkeypatch.setattr(llm, "_config", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_usage", lambda r: None)
    from google.genai import types  # noqa: F401  (SDK present in the test env)

    from app.ports import UpstreamError

    with caplog.at_level(logging.DEBUG), pytest.raises(UpstreamError):
        llm.generate(system="s", parts=["p"], schema=Out, purpose="review")
    assert secret not in caplog.text and "a:" in caplog.text


def test_platform_admins_are_exempt_from_the_rate_limits_and_everyone_else_is_not(api):
    from conftest import ROOT

    admin = TestClient(api, headers=bearer(ROOT))  # ROOT's verified email is in ADMIN_EMAILS
    unverified = TestClient(api, headers=bearer(ROOT + ":unverified"))
    member = TestClient(api, headers=bearer(tok("mia")))
    anon = TestClient(api)
    path = "/api/reviews/sample/demo-01"
    # 12 refreshes (each a real model run) from one address: more than the 10/h per-IP cap
    assert [admin.post(path, params={"refresh": 1}).status_code for _ in range(12)] == [200] * 12
    assert [admin.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")}).status_code for _ in range(3)] == [200] * 3
    # the same address is still capped for everyone who is not a verified admin
    codes = [member.post(path, params={"refresh": 1}).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    assert unverified.post(path, params={"refresh": 1}).status_code == 429  # an unverified email is not an admin
    # the demo seeder path (admin token + X-Workspace-Id: demo) is exempt too
    assert admin.post(path, params={"refresh": 1}, headers={"X-Workspace-Id": "demo"}).status_code == 200


def test_the_per_user_cap_does_not_apply_to_admins(services):
    import dataclasses

    from conftest import ROOT

    api = create_app(dataclasses.replace(services.settings, rate_limit_per_hour=1, rate_limit_user_per_hour=1), services)
    admin = TestClient(api, headers=bearer(ROOT))
    assert [upload(admin).status_code for _ in range(3)] == [200] * 3
    ann = TestClient(api, headers=bearer(tok("ann")))
    assert [upload(ann).status_code for _ in range(2)] == [200, 429]
