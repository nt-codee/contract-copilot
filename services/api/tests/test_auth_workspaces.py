"""Sign-in, private workspaces, the audit trail, provenance and history, delete-my-data, the admin API."""

from __future__ import annotations

import dataclasses
import json
import shutil
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from app import admin as admin_mod
from app import provenance
from app.adapters import build_services
from app.adapters.local import LocalTokenVerifier
from app.config import REPO_ROOT, Settings
from app.main import create_app
from app.models import AuditAction
from app.ports import InvalidToken
from app.workspaces import personal_ws_id
from conftest import ALICE, BOB, ROOT, bearer, make_knowledge_dir
from pdfgen import make_pdf

SIGN_IN_REQUIRED = {"error": {"code": "sign_in_required",
                              "message": "Sign in with Google to review your own contract."}}
WS_ALICE, WS_BOB, WS_ROOT = (personal_ws_id(u) for u in ("alice", "bob", "root"))
PDF_BYTES = make_pdf()  # make_pdf() is not byte-stable, so tests that need "the same document" reuse this
PDF_FILE = ("c.pdf", PDF_BYTES, "application/pdf")
QUOTE = "The Vendor's total liability"  # inside a finding quote of the recorded fake review


def upload(c, name="services.pdf", data=None):
    r = c.post("/api/reviews", files={"file": (name, data or PDF_BYTES, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def actions(c, **kw):
    return [e["action"] for e in c.get("/api/activity", params={"limit": 200}, **kw).json()]


# ---- the demo visitor is never walled off -----------------------------------------------------------


def test_no_token_reaches_the_demo(anon, services):
    assert anon.get("/api/reviews").json() == []
    assert anon.get("/api/samples").status_code == 200
    assert anon.get("/api/knowledge").status_code == 200
    assert anon.get("/api/company/docs").json() == []
    assert anon.get("/api/search", params={"q": "liability"}).json() == []
    assert anon.get("/api/activity").json() == []
    r = anon.post("/api/reviews/sample/demo-01")
    assert r.status_code == 200 and r.json()["owner"] == "demo" and r.json()["created_by"] is None
    rid = r.json()["id"]
    assert [x["id"] for x in anon.get("/api/reviews").json()] == [rid]
    assert anon.get(f"/api/reviews/{rid}").status_code == 200
    assert anon.get(f"/api/reviews/{rid}/pdf").status_code == 200
    assert anon.post(f"/api/reviews/{rid}/negotiation").status_code == 200  # drafting is open to visitors
    assert anon.get("/api/search", params={"q": "governed by the laws of Delaware"}).json()
    assert anon.get("/api/reviews/sample/demo-01").status_code in (404, 405)
    assert {"reviewed", "drafted_reply"} <= set(actions(anon))  # the demo workspace's own trail


def test_demo_visitor_cannot_rewrite_the_shared_demo(anon, services):
    first = anon.post("/api/reviews/sample/demo-01").json()
    n = len(services.llm.calls)
    again = anon.post("/api/reviews/sample/demo-01", params={"refresh": 1}).json()
    assert again["id"] == first["id"] and len(services.llm.calls) == n  # refresh ignored, cache served


def test_views_by_anonymous_visitors_are_not_logged(anon):
    rid = anon.post("/api/reviews/sample/demo-01").json()["id"]
    anon.get(f"/api/reviews/{rid}")
    assert "viewed_review" not in actions(anon)


# ---- uploads and deletes need sign-in -----------------------------------------------------------------


def test_uploads_and_deletes_need_sign_in(anon, client):
    for path in ("/api/reviews", "/api/company/docs"):
        r = anon.post(path, files={"file": PDF_FILE})
        assert r.status_code == 401 and r.json() == SIGN_IN_REQUIRED, path
    rid = upload(client)["id"]
    assert anon.delete(f"/api/reviews/{rid}").json() == SIGN_IN_REQUIRED
    doc = client.post("/api/company/docs", files={"file": PDF_FILE}).json()
    assert anon.delete(f"/api/company/docs/{doc['id']}").json() == SIGN_IN_REQUIRED
    assert client.get(f"/api/reviews/{rid}").status_code == 200  # nothing was deleted


def test_demo_workspace_is_read_only_for_everyone_but_the_seeder(services, anon, client, admin):
    hdr = {"X-Workspace-Id": "demo"}
    # a signed-in non-admin may not write to it
    r = client.post("/api/reviews", files={"file": PDF_FILE}, headers=hdr)
    assert r.status_code == 403 and r.json()["error"]["code"] == "demo_read_only"
    # an admin may
    ok = admin.post("/api/company/docs", files={"file": PDF_FILE}, headers=hdr)
    assert ok.status_code == 200
    assert [d.filename for d in services.company.list(owner="demo")] == ["c.pdf"]
    assert services.company.list(owner=WS_ROOT) == []
    # an anonymous caller may only when the server runs with CLOUD=local
    assert anon.post("/api/company/docs", files={"file": PDF_FILE}, headers=hdr).status_code == 200
    services.settings = dataclasses.replace(services.settings, cloud="gcp")
    r = anon.post("/api/company/docs", files={"file": PDF_FILE}, headers=hdr)
    assert r.status_code == 403


# ---- tokens ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("header", ["Bearer garbage", "Bearer test:only-two", "Bearer test::x@y.test",
                                    "Bearer ", "Basic abc", "Bearer test:demo:demo@example.test"])
def test_invalid_token_is_401_with_a_plain_message(anon, header):
    r = anon.get("/api/reviews", headers={"Authorization": header})
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "invalid_token" and "sign in again" in err["message"].lower()
    assert "Traceback" not in r.text


def test_invalid_token_is_rejected_on_every_route_family(anon):
    bad = {"Authorization": "Bearer nope"}
    assert anon.get("/api/me", headers=bad).status_code == 401
    assert anon.get("/api/admin/overview", headers=bad).status_code == 401
    assert anon.post("/api/reviews/sample/demo-01", headers=bad).status_code == 401


def test_local_test_tokens_exist_only_for_cloud_local():
    v = LocalTokenVerifier()
    u = v.verify("test:u1:a@b.test")
    assert (u.uid, u.email, u.email_verified) == ("u1", "a@b.test", True)
    assert v.verify("test:u1:a@b.test:unverified").email_verified is False
    with pytest.raises(InvalidToken):
        v.verify("eyJhbGciOi.real.jwt")
    # CLOUD=gcp builds the Identity Platform verifier, which does not understand test tokens
    import app.adapters.gcp as gcp

    fb = gcp.IdentityPlatformVerifier.__new__(gcp.IdentityPlatformVerifier)
    fb._project, fb._request = "p", gcp._CachedRequest()
    with pytest.raises(InvalidToken):
        fb.verify("test:u1:a@b.test")


def test_identity_platform_verifier_checks_the_project_audience(monkeypatch):
    import app.adapters.gcp as gcp
    from google.oauth2 import id_token

    seen = {}

    def fake(token, request, audience=None, **kw):
        seen.update(token=token, audience=audience, request=request)
        if token == "expired":
            raise ValueError("Token expired")
        return {"user_id": "u9", "email": "Z@Example.test", "email_verified": True, "name": "Zed",
                "picture": "http://x/p.png", "iss": "https://securetoken.google.com/contract-copilot-abc26"}

    monkeypatch.setattr(id_token, "verify_firebase_token", fake)
    fb = gcp.IdentityPlatformVerifier(Settings())
    ident = fb.verify("good")
    assert seen["audience"] == "contract-copilot-abc26" and seen["request"] is fb._request
    assert (ident.uid, ident.email, ident.email_verified, ident.photo_url) == ("u9", "Z@Example.test", True,
                                                                              "http://x/p.png")
    with pytest.raises(InvalidToken):
        fb.verify("expired")


def test_signing_certs_are_cached(monkeypatch):
    import app.adapters.gcp as gcp

    class Resp:
        status = 200
        data = b"{}"

    calls = []

    class Inner:
        def __call__(self, url, method="GET", **kw):
            calls.append(url)
            return Resp()

    req = gcp._CachedRequest(ttl=60)
    req._inner = Inner()
    for _ in range(3):
        req("https://certs.test/x")
    assert calls == ["https://certs.test/x"]


def test_admin_requires_a_verified_email(api):
    unverified = TestClient(api, headers=bearer(ROOT + ":unverified"))
    assert unverified.get("/api/admin/overview").status_code == 403
    assert unverified.get("/api/me").json()["platform_admin"] is False


# ---- /api/me and /api/auth/config ------------------------------------------------------------------------


def test_me_and_signed_in_event(anon, client, admin):
    assert anon.get("/api/me").status_code == 401
    me = client.get("/api/me").json()
    assert (me["uid"], me["email"], me["platform_admin"]) == ("alice", "alice@example.test", False)
    assert [(w["workspace"]["kind"], w["role"]) for w in me["workspaces"]] == [("personal", "owner")]
    assert me["created_at"] and me["last_seen_at"]
    assert admin.get("/api/me").json()["platform_admin"] is True  # ADMIN_EMAILS is case-insensitive
    client.get("/api/me")
    assert actions(client).count("signed_in") == 1  # a reload is not a new sign-in


def test_auth_config_is_served_from_the_environment(services, monkeypatch):
    cfg = {"apiKey": "k", "authDomain": "x.firebaseapp.com", "projectId": "p"}
    off = TestClient(create_app(services.settings, services))
    assert off.get("/api/auth/config").status_code == 404
    on_settings = dataclasses.replace(services.settings, identity_web_config=json.dumps(cfg))
    on = TestClient(create_app(on_settings, services))
    assert on.get("/api/auth/config").json() == cfg  # no auth needed: the web reads it before signing in


# ---- owner isolation ------------------------------------------------------------------------------


def test_user_cannot_read_or_search_anothers_review(client, bob, anon, admin, services):
    rid = upload(client)["id"]
    for c in (bob, anon, admin):
        assert c.get(f"/api/reviews/{rid}").status_code == 404
        assert c.get(f"/api/reviews/{rid}/pdf").status_code == 404
        assert c.get(f"/api/reviews/{rid}/history").status_code == 404
        assert c.get(f"/api/reviews/{rid}/export").status_code == 404
        assert c.post(f"/api/reviews/{rid}/negotiation").status_code == 404
        assert c.get("/api/reviews").json() == []
        assert c.get("/api/search", params={"q": "governed by the laws of Delaware"}).json() == []
    assert bob.delete(f"/api/reviews/{rid}").status_code == 404
    assert services.store.get(rid, owner=WS_ALICE) is not None  # still there
    assert client.get("/api/search", params={"q": "governed by the laws of Delaware"}).json()


def test_user_cannot_read_or_use_anothers_policy(client, bob, services):
    from app.agent import Toolbox

    doc = client.post("/api/company/docs", files={"file": PDF_FILE}).json()
    assert [d["id"] for d in client.get("/api/company/docs").json()] == [doc["id"]]
    assert bob.get("/api/company/docs").json() == []
    assert bob.delete(f"/api/company/docs/{doc['id']}").status_code == 404
    assert [d["id"] for d in client.get("/api/company/docs").json()] == [doc["id"]]
    vec = services.embedder.embed(["liability"], query=True)[0][0]
    assert services.company.search(vec, 5, owner=WS_ALICE)
    assert services.company.search(vec, 5, owner=WS_BOB) == []
    assert services.company.get(doc["id"], owner=WS_BOB) is None
    bob_tools = Toolbox(services.kb, services.company, services.embedder, ["x"], owner=WS_BOB)
    assert json.loads(bob_tools.run(_call("search_company", query="liability")).payload)["results"] == []


def _call(name, **args):
    from app.ports import ToolCall

    return ToolCall(name=name, args=args)


def test_same_document_in_two_workspaces_stays_separate(client, bob):
    a, b = upload(client), upload(bob)
    assert a["owner"] == WS_ALICE and b["owner"] == WS_BOB and a["created_by"] == "alice" and a["id"] != b["id"]
    assert b["previous_review_id"] is None  # alice's earlier run is not bob's history
    assert [r["id"] for r in client.get("/api/reviews").json()] == [a["id"]]
    assert [r["id"] for r in bob.get("/api/reviews").json()] == [b["id"]]
    assert [r["id"] for r in bob.get(f"/api/reviews/{b['id']}/history").json()] == [b["id"]]


def test_signed_in_user_gets_a_private_copy_of_a_sample(anon, client, bob, services):
    demo = anon.post("/api/reviews/sample/demo-01").json()
    n = len(services.llm.calls)
    mine = client.post("/api/reviews/sample/demo-01").json()
    assert len(services.llm.calls) == n  # copied, no model call
    assert mine["id"] != demo["id"] and mine["owner"] == WS_ALICE and mine["sample_id"] == "demo-01"
    assert mine["findings"] == demo["findings"] and mine["provenance"] == demo["provenance"]
    assert mine["metrics"]["cost_usd_est"] == 0 and mine["metrics"]["input_tokens"] == 0
    assert client.get(f"/api/reviews/{mine['id']}/pdf").status_code == 200
    assert client.get("/api/search", params={"q": "governed by the laws of Delaware"}).json()
    assert client.post("/api/reviews/sample/demo-01").json()["id"] == mine["id"]  # cached in her workspace
    assert [r["id"] for r in anon.get("/api/reviews").json()] == [demo["id"]]  # the demo is untouched
    assert bob.get(f"/api/reviews/{mine['id']}").status_code == 404


# ---- the audit trail -------------------------------------------------------------------------------


def test_every_action_in_the_schema_enum_leaves_an_audit_event(client, services):
    client.get("/api/me")  # signed_in
    first = upload(client, "a.pdf")  # uploaded_contract, reviewed
    again = upload(client, "a.pdf")  # uploaded_contract, re_reviewed
    client.post(f"/api/reviews/{first['id']}/negotiation")  # drafted_reply
    client.get(f"/api/reviews/{first['id']}")  # viewed_review
    assert client.get(f"/api/reviews/{first['id']}/export").status_code == 200  # exported
    doc = client.post("/api/company/docs", files={"file": PDF_FILE}).json()  # uploaded_policy
    assert client.delete(f"/api/company/docs/{doc['id']}").status_code == 204  # deleted_policy
    assert client.delete(f"/api/reviews/{again['id']}").status_code == 204  # deleted_review

    events = client.get("/api/activity").json()
    got = {e["action"] for e in events}
    assert got == set(get_args(AuditAction)) - {"deleted_my_data"}

    by_action = {e["action"]: e for e in reversed(events)}
    assert all(e["actor"] == "alice" and e["id"] and e["at"] for e in events)
    reviewed = by_action["reviewed"]
    assert reviewed["target_id"] == first["id"] and reviewed["target_label"] == "a.pdf"
    d = reviewed["detail"]
    assert d["model"] == "fake-llm" and d["kb_version"] == services.kb_version
    assert d["prompt_version"] == services.prompt_version and d["findings"] == 4
    assert by_action["re_reviewed"]["detail"]["previous_review_id"] == first["id"]
    assert by_action["deleted_policy"]["target_id"] == doc["id"]
    assert [e["at"] for e in events] == sorted((e["at"] for e in events), reverse=True)  # newest first
    # no contract text in the trail
    assert QUOTE not in json.dumps(events)

    assert client.post("/api/me/delete").status_code == 200
    assert [e.action for e in services.audit.for_workspace(WS_ALICE, 50)] == ["deleted_my_data"]
    assert set(get_args(AuditAction)) == got | {"deleted_my_data"}


def test_a_user_sees_only_their_own_events(client, bob):
    upload(client)
    assert "reviewed" in actions(client)
    assert actions(bob) == []


def test_the_trail_has_no_edit_or_delete_path(services):
    a = services.audit
    assert not any(hasattr(a, n) for n in ("update", "delete", "remove", "edit", "clear"))
    first = a.record("alice", "reviewed", "r1", "x.pdf", {"k": 1}, workspace_id="w1")
    assert services.audit.for_workspace("w1", 10)[0].id == first.id
    assert services.audit.for_workspace("w2", 10) == []
    with pytest.raises(ValueError):
        a.record("alice", "not_an_action")


def test_firestore_audit_append_uses_create_so_events_cannot_be_overwritten():
    import app.adapters.gcp as gcp
    from app.audit import Audit

    docs: dict = {}

    class Doc:
        def __init__(self, i):
            self.i = i

        def create(self, data):
            if self.i in docs:
                raise RuntimeError("already exists")
            docs[self.i] = data

        def set(self, data):  # must never be used for events
            raise AssertionError("audit events are created, never set")

    class Col:
        def document(self, i):
            return Doc(i)

    log = gcp.FirestoreAuditLog.__new__(gcp.FirestoreAuditLog)
    log._col = Col()
    ev = Audit(log).record("alice", "signed_in")
    assert ev.id in docs
    with pytest.raises(RuntimeError):
        log.append(ev)


# ---- provenance and history ----------------------------------------------------------------------


def test_review_carries_provenance_and_content_hash(client, services, monkeypatch):
    r = upload(client)
    assert r["provenance"] == {"model": "fake-llm", "kb_version": services.kb_version,
                               "prompt_version": services.prompt_version, "app_version": "dev"}
    assert len(r["content_hash"]) == 64 and r["previous_review_id"] is None and r["owner"] == WS_ALICE


def test_reviewing_the_same_content_links_history(client):
    a = upload(client, "v1.pdf")
    b = upload(client, "v2-renamed.pdf")  # same bytes, new name: still the same content
    c = upload(client, "v3.pdf")
    assert a["content_hash"] == b["content_hash"] == c["content_hash"]
    assert (b["previous_review_id"], c["previous_review_id"]) == (a["id"], b["id"])
    other = upload(client, "other.pdf", data=PDF_BYTES + b"\n%x")  # different bytes
    assert other["content_hash"] != a["content_hash"] and other["previous_review_id"] is None
    for r in (a, b, c):
        hist = client.get(f"/api/reviews/{r['id']}/history").json()
        assert [h["id"] for h in hist] == [c["id"], b["id"], a["id"]]  # newest first, same for every run
        assert {"headline", "overall_risk", "finding_counts"} <= set(hist[0])
    assert [h["id"] for h in client.get(f"/api/reviews/{other['id']}/history").json()] == [other["id"]]


def test_provenance_app_version_comes_from_the_environment(env, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "abc1234")
    svc = build_services(Settings())
    c = TestClient(create_app(svc.settings, svc), headers=bearer(ALICE))
    assert upload(c)["provenance"]["app_version"] == "abc1234"


def test_kb_version_is_stable_and_tracks_content(env, tmp_path):
    k = make_knowledge_dir(tmp_path / "copy")
    v1, v2 = provenance.kb_version(k), provenance.kb_version(k)
    assert v1 == v2 and len(v1) == 12 and int(v1, 16) >= 0
    assert build_services(Settings()).kb_version == build_services(Settings()).kb_version  # across startups
    (k / ".DS_Store").write_bytes(b"junk")
    assert provenance.kb_version(k) == v1  # dotfiles do not count
    shutil.copy(next((k / "playbook").rglob("*.md")), k / "playbook" / "copy.md")
    v3 = provenance.kb_version(k)
    assert v3 != v1
    next((k / "law").rglob("*.md")).write_text("changed")
    assert provenance.kb_version(k) not in (v1, v3)
    assert len(provenance.kb_version(REPO_ROOT / "knowledge")) == 12
    assert provenance.kb_version(tmp_path / "missing") == provenance.kb_version(tmp_path / "also-missing")


def test_prompt_version_is_stable_and_tracks_templates(monkeypatch):
    from app import prompts

    v = provenance.prompt_version()
    assert v == provenance.prompt_version() and len(v) == 12
    monkeypatch.setattr(prompts, "NEGOTIATION_SYSTEM", prompts.NEGOTIATION_SYSTEM + " changed")
    assert provenance.prompt_version() != v


# ---- delete my data ----------------------------------------------------------------------------


def test_delete_my_data_removes_everything_of_mine_and_nothing_of_theirs(client, bob, anon, services):
    client.get("/api/me")
    mine = upload(client)
    doc = client.post("/api/company/docs", files={"file": PDF_FILE}).json()
    theirs = upload(bob)
    bob.post("/api/company/docs", files={"file": PDF_FILE})
    assert services.users.get("alice") is not None

    assert anon.post("/api/me/delete").status_code == 401
    r = client.post("/api/me/delete")
    assert r.status_code == 200
    assert r.json()["deleted"]["reviews"] == 1 and r.json()["deleted"]["company_docs"] == 1
    assert r.json()["deleted"]["workspaces"] == 1  # her personal workspace had no other member
    assert r.json()["deleted"]["audit_events"] >= 4

    assert services.workspaces.memberships("alice") == [] and services.company.list(owner=WS_ALICE) == []
    assert services.users.get("alice") is None and services.workspaces.get(WS_ALICE) is None
    # the token still verifies, so her next request starts a fresh, empty account
    assert client.get(f"/api/reviews/{mine['id']}").status_code == 404
    assert services.index.query([1.0] * 768, 5, owner=WS_ALICE) == []
    assert services.blobs.get(f"uploads/{mine['id']}.pdf") is None
    assert services.blobs.get(f"company/{doc['id']}.pdf") is None
    assert services.store.list(owner=WS_ALICE) == []
    assert [e.action for e in services.audit.for_workspace(WS_ALICE, 50)] == ["deleted_my_data"]
    assert services.audit.for_workspace(WS_ALICE, 50)[0].detail["reviews"] == 1

    # bob is untouched
    assert bob.get(f"/api/reviews/{theirs['id']}").status_code == 200
    assert len(bob.get("/api/company/docs").json()) == 1
    assert bob.get("/api/search", params={"q": "governed by the laws of Delaware"}).json()
    assert services.blobs.get(f"uploads/{theirs['id']}.pdf") is not None


def test_deleted_review_leaves_no_pdf_chunks_or_cached_draft(client, services):
    r = upload(client)
    client.post(f"/api/reviews/{r['id']}/negotiation")
    assert client.delete(f"/api/reviews/{r['id']}").status_code == 204
    assert client.get(f"/api/reviews/{r['id']}").status_code == 404
    assert client.post(f"/api/reviews/{r['id']}/negotiation").status_code == 404
    assert services.blobs.get(f"uploads/{r['id']}.pdf") is None
    assert client.get("/api/search", params={"q": "governed by the laws of Delaware"}).json() == []
    assert client.delete(f"/api/reviews/{r['id']}").status_code == 404


# ---- admin -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/admin/overview", "/api/admin/activity", "/api/admin/users"])
def test_admin_api_is_403_unless_admin_and_200_for_admin(anon, client, admin, path):
    assert anon.get(path).status_code == 403
    r = client.get(path)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert admin.get(path).status_code == 200


def test_admin_overview_aggregates(client, bob, admin, services):
    client.get("/api/me")
    bob.get("/api/me")
    upload(client, "a.pdf")
    upload(bob, "b.pdf")
    upload(client, "c.pdf", data=PDF_BYTES + b"\n%y")
    doc = client.post("/api/company/docs", files={"file": PDF_FILE}).json()
    assert doc

    o = admin.get("/api/admin/overview", params={"days": 7}).json()
    assert set(o) == {"users", "reviews", "reviews_by_day", "by_contract_type", "by_risk", "top_clause_types",
                      "policy_breaches_by_rule", "citations_by_kind", "top_cited_sources", "latency_ms",
                      "cost_usd", "tokens", "dropped_unverified", "upcoming_deadlines"}
    assert o["users"] == 3 and o["reviews"] == 3  # alice, bob and the admin (created on their first request)
    assert len(o["reviews_by_day"]) == 7 and sum(d["count"] for d in o["reviews_by_day"]) == 3
    assert o["reviews_by_day"][-1]["count"] == 3
    assert o["by_contract_type"] == {"service_agreement": 3}
    assert o["by_risk"]["high"] + o["by_risk"]["medium"] + o["by_risk"]["low"] == 3
    clause = {c["clause_type"]: c for c in o["top_clause_types"]}
    assert clause["liability"]["high"] == 6 and clause["auto_renewal"]["medium"] == 3
    assert o["dropped_unverified"] == {"quotes": 3, "citations": 3}
    assert o["citations_by_kind"]["law"] == 3 and o["citations_by_kind"]["playbook"] >= 3
    refs = {s["ref"] for s in o["top_cited_sources"]}
    assert "my-contracts-act-s75" in refs and not any(r.startswith("company:") for r in refs)
    assert all(set(s) == {"ref", "title", "count"} for s in o["top_cited_sources"])
    assert o["tokens"]["in"] > 0 and o["cost_usd"]["total"] >= 0
    assert o["latency_ms"]["max"] >= o["latency_ms"]["p95"] >= o["latency_ms"]["p50"] >= 0
    assert o["cost_usd"]["per_review_mean"] == pytest.approx(o["cost_usd"]["total"] / 3, abs=1e-6)
    assert admin.get("/api/admin/overview", params={"days": 0}).status_code == 422


def test_admin_sees_aggregates_and_metadata_never_contract_text(client, bob, admin):
    client.get("/api/me")
    rid = upload(client, "secret-vendor.pdf")["id"]
    client.post("/api/company/docs", files={"file": ("Private Policy.pdf", make_pdf(), "application/pdf")})
    for path in ("/api/admin/overview", "/api/admin/activity", "/api/admin/users"):
        body = admin.get(path).text
        for needle in (QUOTE, "Delaware", "Awan Biru", "Kedai Contoh", "why_risky", "suggested_ask"):
            assert needle not in body, (path, needle)
    assert "secret-vendor.pdf" not in admin.get("/api/admin/overview").text
    # and the review itself is as closed to an admin as to anyone else
    assert admin.get(f"/api/reviews/{rid}").status_code == 404
    assert admin.get(f"/api/reviews/{rid}/pdf").status_code == 404
    assert admin.get("/api/reviews").json() == []


def test_admin_activity_lists_every_users_events(client, bob, admin):
    upload(client)
    upload(bob)
    events = admin.get("/api/admin/activity", params={"limit": 50}).json()
    assert {e["actor"] for e in events} == {"alice", "bob"}
    assert {e["workspace_id"] for e in events} == {WS_ALICE, WS_BOB}
    assert all(set(e) <= {"id", "at", "actor", "action", "target_id", "target_label", "detail", "workspace_id"} for e in events)
    assert len(admin.get("/api/admin/activity", params={"limit": 2}).json()) == 2


def test_admin_users_have_review_counts_and_last_seen(client, bob, admin):
    client.get("/api/me")
    bob.get("/api/me")
    upload(client)
    upload(client, data=PDF_BYTES + b"\n%z")
    rows = {u["uid"]: u for u in admin.get("/api/admin/users").json()}
    assert rows["alice"]["review_count"] == 2 and rows["bob"]["review_count"] == 0
    assert rows["alice"]["last_seen_at"] and rows["alice"]["email"] == "alice@example.test"


def test_copies_of_samples_do_not_count_as_model_runs(anon, client, admin, services):
    anon.post("/api/reviews/sample/demo-01")
    client.post("/api/reviews/sample/demo-01")  # a free copy
    o = admin_mod.overview(services, 7, use_cache=False)
    assert o["reviews"] == 2
    ran = services.store.list(owner="demo")[0].metrics
    assert o["tokens"]["in"] == ran.input_tokens  # only the real run
    assert ran.cost_usd_est is None and o["cost_usd"]["total"] == 0  # the fake model has no price


def test_percentile_is_nearest_rank():
    assert admin_mod.percentile([], 95) == 0
    assert admin_mod.percentile([5], 50) == 5
    xs = list(range(1, 101))
    assert (admin_mod.percentile(xs, 50), admin_mod.percentile(xs, 95), admin_mod.percentile(xs, 100)) == (50, 95, 100)
