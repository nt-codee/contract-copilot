"""Company workspaces: domain auto-join, personal workspaces, invites, roles, isolation, hybrid search."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import workspaces as wsmod
from app.pipeline import hybrid_score
from conftest import bearer
from pdfgen import make_pdf

PDF = make_pdf()
PDF_FILE = ("p.pdf", PDF, "application/pdf")


def tok(uid, domain=None, email=None, extra=""):
    email = email or f"{uid}@{domain or 'gmail.com'}"
    return f"test:{uid}:{email}" + (f":hd={domain}" if domain else "") + extra


@pytest.fixture
def mk(api):
    def make(uid, domain=None, **kw):
        return TestClient(api, headers=bearer(tok(uid, domain, **kw)))
    return make


def upload(c, headers=None):
    r = c.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def my_ws(c):
    return c.get("/api/workspaces").json()


# ---- first sign-in: domain auto-join and personal workspaces -----------------------------------------


def test_first_user_of_a_domain_founds_a_company_workspace_as_owner(mk, services):
    ana = mk("ana", "acme.test")
    ref, = my_ws(ana)
    assert ref["role"] == "owner" and ref["workspace"]["kind"] == "company"
    assert ref["workspace"]["domain"] == "acme.test" and ref["workspace"]["id"] == wsmod.company_ws_id("acme.test")
    assert ref["workspace"]["member_count"] == 1
    assert services.workspaces.find_by_domain("acme.test").id == ref["workspace"]["id"]


def test_later_user_of_that_domain_joins_as_member(mk):
    ana, ben = mk("ana", "acme.test"), mk("ben", "acme.test")
    my_ws(ana)
    ref, = my_ws(ben)
    assert ref["role"] == "member" and ref["workspace"]["kind"] == "company"
    assert ref["workspace"]["id"] == my_ws(ana)[0]["workspace"]["id"] and ref["workspace"]["member_count"] == 2
    members = {m["uid"]: m["role"] for m in ben.get(f"/api/workspaces/{ref['workspace']['id']}/members").json()}
    assert members == {"ana": "owner", "ben": "member"}


def test_gmail_user_gets_a_personal_workspace(mk):
    gina = mk("gina")
    ref, = my_ws(gina)
    assert ref["role"] == "owner" and ref["workspace"]["kind"] == "personal" and ref["workspace"]["domain"] is None
    other = mk("gus")
    assert my_ws(other)[0]["workspace"]["id"] != ref["workspace"]["id"]  # two gmail users never share


def test_an_unverified_email_never_auto_joins(mk):
    my_ws(mk("ana", "acme.test"))
    evil = mk("eve", "acme.test", extra=":unverified")
    ref, = my_ws(evil)
    assert ref["workspace"]["kind"] == "personal"


def test_a_removed_member_is_not_auto_joined_again(mk):
    ana, ben = mk("ana", "acme.test"), mk("ben", "acme.test")
    wid = my_ws(ana)[0]["workspace"]["id"]
    my_ws(ben)
    assert ana.delete(f"/api/workspaces/{wid}/members/ben").status_code == 204
    ref, = my_ws(ben)
    assert ref["workspace"]["kind"] == "personal"


# ---- isolation between workspaces ----------------------------------------------------------------------


def test_cross_workspace_isolation_for_reviews_search_and_policies(mk, services):
    ana, zed = mk("ana", "acme.test"), mk("zed", "other.test")
    mine = upload(ana)
    pol = ana.post("/api/company/docs", files={"file": PDF_FILE}).json()
    assert mine["owner"] == wsmod.company_ws_id("acme.test")
    q = {"q": "governed by the laws of Delaware"}
    assert ana.get("/api/search", params=q).json()
    assert zed.get("/api/search", params=q).json() == []
    assert zed.get("/api/reviews").json() == [] and zed.get("/api/company/docs").json() == []
    assert zed.get(f"/api/reviews/{mine['id']}").status_code == 404
    assert zed.delete(f"/api/company/docs/{pol['id']}").status_code in (403, 404)
    assert [d["id"] for d in ana.get("/api/company/docs").json()] == [pol["id"]]
    vec = services.embedder.embed(["liability"], query=True)[0][0]
    assert services.company.search(vec, 5, owner=wsmod.company_ws_id("other.test")) == []
    assert services.index.query(vec, 5, owner=wsmod.company_ws_id("other.test")) == []
    # the agent only sees its own workspace's policies
    other = upload(zed)
    assert all(c["kind"] != "company" for f in other["findings"] for c in f["citations"])


def test_header_must_name_a_workspace_you_belong_to(mk):
    ana, zed = mk("ana", "acme.test"), mk("zed", "other.test")
    acme = my_ws(ana)[0]["workspace"]["id"]
    r = zed.get("/api/reviews", headers={"X-Workspace-Id": acme})
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"
    assert zed.get(f"/api/workspaces/{acme}").status_code == 404
    assert zed.get(f"/api/workspaces/{acme}/members").status_code == 404
    assert ana.get("/api/reviews", headers={"X-Workspace-Id": acme}).status_code == 200
    assert ana.get("/api/reviews", headers={"X-Workspace-Id": "../x"}).status_code == 403


def test_active_workspace_header_switches_between_workspaces(mk):
    ana = mk("ana", "acme.test")
    acme = my_ws(ana)[0]["workspace"]["id"]
    r = upload(ana, {"X-Workspace-Id": acme})
    assert r["owner"] == acme and [x["id"] for x in ana.get("/api/reviews").json()] == [r["id"]]


# ---- invites -----------------------------------------------------------------------------------------


def test_invite_accept_flow(mk, services):
    ana, gina = mk("ana", "acme.test"), mk("gina")
    wid = my_ws(ana)[0]["workspace"]["id"]
    inv = ana.post(f"/api/workspaces/{wid}/invites").json()
    assert inv["workspace_id"] == wid and inv["role"] == "member" and len(inv["code"]) >= 20
    from datetime import datetime, timedelta, timezone

    exp = datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=6, hours=23) < exp - datetime.now(timezone.utc) <= timedelta(days=7)
    assert len(my_ws(gina)) == 1  # gina starts with her personal workspace
    got = gina.post(f"/api/invites/{inv['code']}/accept")
    assert got.status_code == 200 and got.json()["role"] == "member" and got.json()["workspace"]["id"] == wid
    assert {w["workspace"]["id"] for w in my_ws(gina)} >= {wid} and len(my_ws(gina)) == 2
    # gina can now work in acme, and her personal one stays separate
    r = upload(gina, {"X-Workspace-Id": wid})
    assert r["owner"] == wid and r["created_by"] == "gina"
    assert gina.get("/api/reviews").json() == []  # default is still her first (personal) workspace
    # single use
    assert mk("ivy").post(f"/api/invites/{inv['code']}/accept").status_code == 404


def test_invite_expiry_and_garbage_codes(mk, services):
    from datetime import datetime, timedelta, timezone

    ana, ivy = mk("ana", "acme.test"), mk("ivy")
    wid = my_ws(ana)[0]["workspace"]["id"]
    inv = ana.post(f"/api/workspaces/{wid}/invites").json()
    stored = services.workspaces.get_invite(inv["code"])
    services.workspaces.put_invite(stored.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}))
    r = ivy.post(f"/api/invites/{inv['code']}/accept")
    assert r.status_code == 410 and r.json()["error"]["code"] == "invite_expired"
    assert ivy.post("/api/invites/nonsense-code-123/accept").status_code == 404
    assert ivy.post("/api/invites/x/accept").status_code == 404
    assert mk("zed").post(f"/api/invites/{inv['code']}/accept").status_code == 404  # expired invite was removed


def test_anonymous_cannot_accept_an_invite(mk, anon):
    ana = mk("ana", "acme.test")
    inv = ana.post(f"/api/workspaces/{my_ws(ana)[0]['workspace']['id']}/invites").json()
    assert anon.post(f"/api/invites/{inv['code']}/accept").status_code == 401


# ---- roles -------------------------------------------------------------------------------------------


@pytest.fixture
def team(mk):
    """acme.test: ana (owner), ben and cy (members), plus ben promoted later in tests."""
    ana, ben, cy = mk("ana", "acme.test"), mk("ben", "acme.test"), mk("cy", "acme.test")
    my_ws(ana), my_ws(ben), my_ws(cy)
    wid = my_ws(ana)[0]["workspace"]["id"]
    return ana, ben, cy, wid


def test_members_can_review_search_draft_and_upload_contracts(team):
    ana, ben, cy, wid = team
    r = upload(ben)
    assert r["created_by"] == "ben"
    assert ben.post(f"/api/reviews/{r['id']}/negotiation").status_code == 200
    assert ben.get("/api/search", params={"q": "governed by the laws of Delaware"}).json()
    assert cy.get(f"/api/reviews/{r['id']}").status_code == 200  # the whole company sees it


def test_members_cannot_manage_policies_invite_or_see_the_dashboard(team):
    ana, ben, cy, wid = team
    assert ben.post("/api/company/docs", files={"file": PDF_FILE}).status_code == 403
    pol = ana.post("/api/company/docs", files={"file": PDF_FILE}).json()
    assert ben.delete(f"/api/company/docs/{pol['id']}").json()["error"]["code"] == "forbidden_role"
    assert [d["id"] for d in ben.get("/api/company/docs").json()] == [pol["id"]]  # but can read them
    assert ben.post(f"/api/workspaces/{wid}/invites").status_code == 403
    assert ben.get(f"/api/workspaces/{wid}/overview").status_code == 403
    assert ben.patch(f"/api/workspaces/{wid}", json={"name": "Mine now"}).status_code == 403
    assert ben.patch(f"/api/workspaces/{wid}/members/cy", json={"role": "admin"}).status_code == 403
    assert ben.delete(f"/api/workspaces/{wid}/members/cy").status_code == 403


def test_a_member_deletes_only_their_own_reviews(team):
    ana, ben, cy, wid = team
    mine, hers = upload(ben), upload(cy)
    assert ben.delete(f"/api/reviews/{hers['id']}").status_code == 403
    assert ben.delete(f"/api/reviews/{mine['id']}").status_code == 204
    assert ana.delete(f"/api/reviews/{hers['id']}").status_code == 204  # an owner may delete any


def test_admin_can_invite_manage_policies_and_see_the_dashboard(team):
    ana, ben, cy, wid = team
    assert ana.patch(f"/api/workspaces/{wid}/members/ben", json={"role": "admin"}).json()["role"] == "admin"
    assert ben.post(f"/api/workspaces/{wid}/invites").status_code == 200
    pol = ben.post("/api/company/docs", files={"file": PDF_FILE})
    assert pol.status_code == 200
    assert ben.delete(f"/api/company/docs/{pol.json()['id']}").status_code == 204
    o = ben.get(f"/api/workspaces/{wid}/overview").json()
    assert {"reviews", "users", "by_risk", "latency_ms", "cost_usd", "upcoming_deadlines"} <= set(o)
    # an admin's limits: no admin invites, cannot touch owners
    assert ben.post(f"/api/workspaces/{wid}/invites", json={"role": "admin"}).status_code == 403
    assert ben.patch(f"/api/workspaces/{wid}/members/ana", json={"role": "member"}).status_code == 403
    assert ben.delete(f"/api/workspaces/{wid}/members/ana").status_code == 403
    assert ben.patch(f"/api/workspaces/{wid}/members/cy", json={"role": "owner"}).status_code == 403
    assert ben.delete(f"/api/workspaces/{wid}/members/cy").status_code == 204


def test_owner_invites_an_admin_and_changes_roles(team):
    ana, ben, cy, wid = team
    inv = ana.post(f"/api/workspaces/{wid}/invites", json={"role": "admin"}).json()
    assert inv["role"] == "admin"
    assert ana.patch(f"/api/workspaces/{wid}/members/cy", json={"role": "owner"}).json()["role"] == "owner"
    assert ana.patch(f"/api/workspaces/{wid}/members/nobody", json={"role": "admin"}).status_code == 404
    assert ana.patch(f"/api/workspaces/{wid}/members/cy", json={"role": "boss"}).status_code == 422


def test_the_last_owner_cannot_leave_or_be_demoted(team):
    ana, ben, cy, wid = team
    for r in (ana.patch(f"/api/workspaces/{wid}/members/ana", json={"role": "member"}),
              ana.delete(f"/api/workspaces/{wid}/members/ana")):
        assert r.status_code == 409 and r.json()["error"]["code"] == "last_owner"
    assert ben.delete(f"/api/workspaces/{wid}/members/ben").status_code == 204  # anyone else may leave


def test_workspace_patch_rename_and_domain_rules(mk):
    ana = mk("ana", "acme.test")
    wid = my_ws(ana)[0]["workspace"]["id"]
    assert ana.patch(f"/api/workspaces/{wid}", json={"name": "Acme Sdn Bhd"}).json()["name"] == "Acme Sdn Bhd"
    assert ana.patch(f"/api/workspaces/{wid}", json={"name": "  "}).status_code == 422
    # the auto-join domain must be the one the owner's own token proves
    assert ana.patch(f"/api/workspaces/{wid}", json={"domain": "rival.test"}).json()["error"]["code"] == "domain_not_verified"
    assert ana.patch(f"/api/workspaces/{wid}", json={"domain": "acme.test"}).status_code == 200
    gina = mk("gina")
    pid = my_ws(gina)[0]["workspace"]["id"]
    assert gina.patch(f"/api/workspaces/{pid}", json={"domain": "gmail.com"}).status_code in (403, 422)


def test_workspace_overview_is_scoped_to_that_workspace(mk):
    ana, zed = mk("ana", "acme.test"), mk("zed", "other.test")
    wid = my_ws(ana)[0]["workspace"]["id"]
    upload(ana), upload(ana), upload(zed)
    o = ana.get(f"/api/workspaces/{wid}/overview").json()
    assert o["reviews"] == 2 and o["users"] == 1
    assert zed.get(f"/api/workspaces/{wid}/overview").status_code == 404  # not a member: not found


def test_activity_owner_sees_everyone_member_sees_own(team):
    ana, ben, cy, wid = team
    upload(ben), upload(cy)
    assert {e["actor"] for e in ana.get("/api/activity").json()} >= {"ben", "cy"}
    assert {e["actor"] for e in ben.get("/api/activity").json()} == {"ben"}
    assert all(e["workspace_id"] == wid for e in ana.get("/api/activity").json())


# ---- platform admin ----------------------------------------------------------------------------------


def test_platform_admin_sees_all_workspaces_but_no_contract_text(mk, admin):
    ana, zed = mk("ana", "acme.test"), mk("zed", "other.test")
    upload(ana), upload(zed)
    o = admin.get("/api/admin/overview").json()
    assert o["reviews"] == 2
    rows = {w["name"]: w for w in admin.get("/api/admin/workspaces").json()}
    assert rows["acme.test"]["review_count"] == 1 and rows["other.test"]["member_count"] == 1
    assert {e["workspace_id"] for e in admin.get("/api/admin/activity").json()} >= {
        wsmod.company_ws_id("acme.test"), wsmod.company_ws_id("other.test")}
    assert "Delaware" not in admin.get("/api/admin/workspaces").text + admin.get("/api/admin/overview").text
    assert ana.get("/api/admin/workspaces").status_code == 403
    acme = wsmod.company_ws_id("acme.test")
    assert admin.get(f"/api/workspaces/{acme}/members").status_code == 404  # platform admin is not a member
    assert admin.get("/api/reviews", headers={"X-Workspace-Id": acme}).status_code == 403


# ---- hybrid search -----------------------------------------------------------------------------------


def test_hybrid_score_combines_vector_keywords_and_exact_phrase():
    terms = {"liability", "cap", "fees"}
    text = "The total liability cap is twelve months of fees."
    full = hybrid_score(terms, "liability cap fees", text, 0.5)
    no_kw = hybrid_score(terms, "liability cap fees", "Unrelated governing law text.", 0.5)
    assert full > no_kw
    phrase = hybrid_score({"liability", "cap"}, "liability cap", text, 0.5)
    scattered = hybrid_score({"liability", "cap"}, "cap liability", text, 0.5)
    assert phrase > scattered  # exact phrase boost
    assert hybrid_score(set(), "", text, 0.4) == pytest.approx(0.65 * 0.4)
    assert hybrid_score(terms, "x y", "LIABILITY, Cap — fees!", 0.0) == pytest.approx(0.35)  # normalised words


def test_search_ranks_a_keyword_hit_above_a_merely_near_vector(client, services, monkeypatch):
    r = client.post("/api/reviews", files={"file": PDF_FILE}).json()
    hits = client.get("/api/search", params={"q": "governed by the laws of the State of Delaware"}).json()
    assert hits and hits[0]["review_id"] == r["id"] and "Delaware" in hits[0]["text"]
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
