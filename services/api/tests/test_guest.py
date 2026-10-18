"""The read-only guest view of the demo company: `X-Guest: demo` with no token."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.guest import DEMO_MEMBERS, GUEST_MESSAGE
from app.main import create_app
from conftest import ROOT, bearer
from pdfgen import make_pdf

PDF = make_pdf()
G = {"X-Guest": "demo"}


def tok(uid, domain=None):
    return f"test:{uid}:{uid}@{domain or 'gmail.test'}" + (f":hd={domain}" if domain else "")


@pytest.fixture
def world(api, services):
    """A demo workspace with one reviewed sample and one policy, plus a real company that must stay invisible."""
    anon = TestClient(api)
    review = anon.post("/api/reviews/sample/demo-01", headers={"X-Workspace-Id": "demo"}).json()
    admin = TestClient(api, headers=bearer(ROOT))
    doc = admin.post("/api/company/docs", files={"file": ("kopi-kita-policy.pdf", PDF, "application/pdf")},
                     headers={"X-Workspace-Id": "demo"}).json()
    real = TestClient(api, headers=bearer(tok("ann", "acme.test")))
    real_ws = real.get("/api/workspaces").json()[0]["workspace"]["id"]
    real_review = real.post("/api/reviews", files={"file": ("secret.pdf", PDF, "application/pdf")}).json()
    guest = TestClient(api, headers=G)
    return dict(guest=guest, anon=anon, admin=admin, real=real, review=review["id"], doc=doc["id"], real_ws=real_ws,
                real_review=real_review["id"], services=services)


def is_blocked(r):
    assert r.status_code == 403, (r.request.method, r.request.url, r.status_code, r.text[:150])
    e = r.json()["error"]
    assert e["code"] == "guest_read_only" and e["message"] == GUEST_MESSAGE
    return True


# ---- who is a guest ------------------------------------------------------------------------------------------


def test_guest_identity_contract(world):
    g = world["guest"]
    body = g.get("/api/guest").json()
    assert body == {"workspace": {"id": "demo", "name": "Kopi Kita Sdn Bhd", "kind": "demo", "domain": None,
                                  "created_at": body["workspace"]["created_at"], "member_count": 4},
                    "role": "viewer", "read_only": True, "message": GUEST_MESSAGE}
    assert world["anon"].get("/api/guest").status_code == 404  # no header: a plain visitor, not a guest
    assert TestClient(world["anon"].app, headers={**G, **bearer(tok("ann", "acme.test"))}).get("/api/guest").status_code == 404
    assert world["guest"].get("/api/guest", headers={"X-Guest": "other"}).status_code == 404  # only the value `demo`
    assert g.get("/api/me").status_code == 401 and g.get("/api/me").json()["error"]["code"] == "sign_in_required"
    assert g.get("/api/workspaces").json() == []


def test_a_token_wins_over_the_guest_header(world):
    c = TestClient(world["anon"].app, headers={**G, **bearer(tok("ann", "acme.test"))})
    assert c.get("/api/me").status_code == 200
    ids = [r["id"] for r in c.get("/api/reviews").json()]
    assert world["real_review"] in ids and world["review"] not in ids  # her own workspace, not the demo
    bad = TestClient(world["anon"].app, headers={**G, "Authorization": "Bearer nope"})
    assert bad.get("/api/reviews").status_code == 401  # an invalid token is never downgraded to guest


def test_plain_visitor_behaviour_is_unchanged(world):
    a = world["anon"]
    r = a.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")})
    assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"
    assert a.delete(f"/api/reviews/{world['review']}").json()["error"]["code"] == "sign_in_required"


# ---- reads ---------------------------------------------------------------------------------------------------


def test_guest_can_read_the_demo_company(world):
    g, rid, doc = world["guest"], world["review"], world["doc"]
    reads = [f"/api/reviews", f"/api/reviews/{rid}", f"/api/reviews/{rid}/history", f"/api/reviews/{rid}/export",
             f"/api/reviews/{rid}/pdf", "/api/activity", "/api/company/docs", f"/api/company/docs/{doc}/pdf",
             "/api/playbook", "/api/playbook?contract_type=nda", "/api/workspaces/demo", "/api/workspaces/demo/members",
             "/api/workspaces/demo/overview?days=30", "/api/search?q=liability", "/api/knowledge?q=penalty", "/api/samples"]
    for p in reads:
        r = g.get(p)
        assert r.status_code == 200, (p, r.status_code, r.text[:120])
    assert [x["id"] for x in g.get("/api/reviews").json()] == [rid]
    assert [d["id"] for d in g.get("/api/company/docs").json()] == [doc]
    assert g.get(f"/api/reviews/{rid}").json()["owner"] == "demo"
    assert g.get("/api/workspaces/demo/overview").json()["reviews"] == 1
    assert g.get("/api/playbook?contract_type=nda").json()  # the standard positions, defaults
    # the cached sample opens (the same copy anyone sees) without a model call
    n = len(world["services"].llm.calls)
    assert g.post("/api/reviews/sample/demo-01").json()["id"] == rid
    assert g.post("/api/reviews/sample/demo-01", params={"refresh": 1}).json()["id"] == rid  # refresh needs sign-in: ignored
    assert len(world["services"].llm.calls) == n


def test_demo_members_are_four_invented_people(world):
    real = world["real"]
    members = world["guest"].get("/api/workspaces/demo/members").json()
    assert len(members) == 4 and [m["role"] for m in members] == ["owner", "admin", "member", "member"]
    assert all(m["email"].endswith("@example.com") for m in members)
    assert [m["name"] for m in members] == [m.name for m in DEMO_MEMBERS]
    assert all(m["uid"].startswith("demo-") for m in members)
    real_emails = {u.email for u in world["services"].users.list()}
    assert not real_emails & {m["email"] for m in members}  # never a real user
    assert world["guest"].get("/api/workspaces/demo").json()["member_count"] == 4
    assert world["guest"].get("/api/workspaces/demo/overview").json()["users"] == 4
    assert real.get(f"/api/workspaces/{world['real_ws']}/overview").json()["users"] == 1  # real overview untouched


# ---- writes: the matrix ---------------------------------------------------------------------------------------


def writes(world):
    rid, doc, ws = world["review"], world["doc"], "demo"
    pdf = {"files": {"file": ("a.pdf", PDF, "application/pdf")}}
    return [
        ("POST", "/api/reviews", pdf), ("POST", "/api/company/docs", pdf),
        ("DELETE", f"/api/reviews/{rid}", {}), ("DELETE", f"/api/company/docs/{doc}", {}),
        ("POST", f"/api/workspaces/{ws}/invites", {"json": {"role": "member"}}),
        ("PATCH", f"/api/workspaces/{ws}", {"json": {"name": "Hijacked"}}),
        ("PATCH", f"/api/workspaces/{ws}/members/demo-member-1", {"json": {"role": "admin"}}),
        ("DELETE", f"/api/workspaces/{ws}/members/demo-member-1", {}),
        ("PUT", "/api/playbook/nda/liability", {"json": {"standard": "x", "red_flags": [], "ask": "", "note": ""}}),
        ("DELETE", "/api/playbook/nda/liability", {}),
        ("POST", "/api/me/delete", {}), ("POST", "/api/invites/abcdefghijklmnop/accept", {}),
    ]


def test_every_write_is_403_guest_read_only_and_changes_nothing(world):
    g, svc = world["guest"], world["services"]
    before = (len(svc.store.list(owner="demo")), len(svc.company.list(owner="demo")), len(svc.audit.everyone(500)),
              len(svc.llm.calls), svc.workspaces.get("demo"))
    for method, path, kw in writes(world):
        assert is_blocked(g.request(method, path, **kw)), (method, path)
    after = (len(svc.store.list(owner="demo")), len(svc.company.list(owner="demo")), len(svc.audit.everyone(500)),
             len(svc.llm.calls), svc.workspaces.get("demo"))
    assert before == after


def test_an_unreviewed_sample_is_refused_to_a_guest_without_a_model_call(api, services):
    g = TestClient(api, headers=G)
    n = len(services.llm.calls)
    assert is_blocked(g.post("/api/reviews/sample/demo-01"))  # nothing reviewed in the demo yet
    assert len(services.llm.calls) == n and services.store.list(owner="demo") == []


def test_a_guest_cannot_become_the_seeder(world):
    g = world["guest"]
    for h in ({"X-Workspace-Id": "demo"},):
        r = g.post("/api/company/docs", files={"file": ("a.pdf", PDF, "application/pdf")}, headers=h)
        assert is_blocked(r)  # even with CLOUD=local, where an anonymous caller could seed with this header
    r = world["anon"].post("/api/company/docs", files={"file": ("seed.pdf", PDF, "application/pdf")},
                           headers={"X-Workspace-Id": "demo"})
    assert r.status_code == 200  # the offline seeder (no X-Guest) still works


def test_upload_is_refused_before_the_body_is_read(world):
    big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 200_000)
    r = world["guest"].post("/api/reviews", files={"file": ("a.pdf", big, "application/pdf")})
    assert r.status_code in (403, 413)
    r = world["guest"].post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")})
    assert is_blocked(r)


# ---- other workspaces and the admin API ------------------------------------------------------------------------


def test_a_guest_never_reaches_another_workspace(world):
    g, ws, rr = world["guest"], world["real_ws"], world["real_review"]
    for method, path in (("GET", f"/api/workspaces/{ws}"), ("GET", f"/api/workspaces/{ws}/members"),
                         ("GET", f"/api/workspaces/{ws}/overview"), ("PATCH", f"/api/workspaces/{ws}"),
                         ("POST", f"/api/workspaces/{ws}/invites"), ("DELETE", f"/api/workspaces/{ws}/members/ann"),
                         ("PATCH", f"/api/workspaces/{ws}/members/ann"), ("GET", "/api/workspaces/nope")):
        r = g.request(method, path, json={"role": "member", "name": "x"} if method in ("PATCH", "POST") else None)
        assert r.status_code == 404 and r.json()["error"]["code"] == "workspace_not_found", (method, path, r.status_code)
    for p in (f"/api/reviews/{rr}", f"/api/reviews/{rr}/pdf", f"/api/reviews/{rr}/history", f"/api/reviews/{rr}/export"):
        assert g.get(p).status_code == 404, p
    for h in ({"X-Workspace-Id": ws}, {"X-Workspace-Id": "../" + ws}, {"X-Workspace-Id": "p-1234"}):
        r = g.get("/api/reviews", headers=h)
        assert r.status_code == 404 and rr not in r.text, h
    assert g.get("/api/reviews", headers={"X-Workspace-Id": "demo"}).status_code == 200
    assert world["real_review"] not in g.get("/api/search", params={"q": "governed by the laws"}).text
    assert "secret.pdf" not in g.get("/api/activity").text + g.get("/api/reviews").text


def test_admin_api_stays_admin_only(world):
    g = world["guest"]
    for p in ("/api/admin/overview", "/api/admin/activity", "/api/admin/users", "/api/admin/workspaces"):
        r = g.get(p)
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", p
    assert world["admin"].get("/api/admin/overview").status_code == 200


# ---- audit and rate limits ------------------------------------------------------------------------------------------


def test_guest_views_are_not_recorded(world):
    g, svc, rid = world["guest"], world["services"], world["review"]
    before = len(svc.audit.everyone(1000))
    for p in (f"/api/reviews/{rid}", f"/api/reviews/{rid}/export", f"/api/reviews/{rid}/pdf", "/api/activity",
              "/api/reviews", "/api/workspaces/demo/overview", "/api/workspaces/demo/members", "/api/playbook"):
        assert g.get(p).status_code == 200
    g.post("/api/reviews/sample/demo-01")
    assert len(svc.audit.everyone(1000)) == before
    assert not any(e.action in ("viewed_review", "exported") and e.actor != "ann" for e in svc.audit.everyone(1000)
                   if e.workspace_id == "demo")


def test_guests_use_no_rate_limit_slot_and_cannot_spend_models(services):
    st = dataclasses.replace(services.settings, rate_limit_per_hour=1, rate_limit_user_per_hour=1)
    api = create_app(st, services)
    admin = TestClient(api, headers=bearer(ROOT))
    admin.post("/api/reviews/sample/demo-01", headers={"X-Workspace-Id": "demo"})  # seeds the shared copy (admins are exempt)
    g = TestClient(api, headers=G)
    n = len(services.llm.calls)
    assert [g.post("/api/reviews/sample/demo-01").status_code for _ in range(15)] == [200] * 15
    assert len(services.llm.calls) == n
    user = TestClient(api, headers=bearer(tok("ann")))
    assert user.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")}).status_code == 200  # slot still free
