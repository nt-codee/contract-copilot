"""HTTP paid-call authorization, real chunk keyword search and persistent free drafts."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import budget, pipeline
from app.adapters.gcp import FirestoreIndex
from app.main import create_app
from app.ports import Chunk, BudgetReservation
from app.models import Member, Workspace
from conftest import ROOT, bearer
from pdfgen import make_pdf
from test_account_budget import Models, embedder

SAMPLE = "/api/reviews/sample/demo-01"
DEMO = {"X-Workspace-Id": "demo"}
GUEST = {"X-Guest": "demo"}


def seed(admin):
    response = admin.post(SAMPLE, headers=DEMO)
    assert response.status_code == 200
    return response.json()["id"]


def exhaust(services, uid):
    assert services.budget_ledger.reserve(BudgetReservation(uid, budget.today(), uid + "-full", 1_000_000), 1_000_000)


@pytest.mark.parametrize("headers,status", [({},401),(GUEST,403),(DEMO,401)])
def test_no_anonymous_http_paid_path_reaches_adapter(anon, admin, services, monkeypatch, headers, status):
    rid = seed(admin)
    gen, emb = Mock(side_effect=AssertionError("no anonymous generation")), Mock(side_effect=AssertionError("no anonymous embedding"))
    monkeypatch.setattr(services.llm, "generate", gen)
    monkeypatch.setattr(services.embedder, "embed", emb)
    # Refresh ignores anonymous refresh flags on a cached sample, so use a distinct uncached indexed id.
    index = services.settings.samples_dir / "index.json"
    import json
    data = json.loads(index.read_text())
    data.append({**data[0], "id":"uncached"})
    index.write_text(json.dumps(data))
    for path in ("/api/reviews", "/api/company/docs", "/api/reviews/sample/uncached", f"/api/reviews/{rid}/negotiation"):
        response = anon.post(path, headers=headers,
            files={"file": ("a.pdf", make_pdf(), "application/pdf")} if path in ("/api/reviews","/api/company/docs") else None)
        assert response.status_code == status, (path,response.text)
    gen.assert_not_called()
    emb.assert_not_called()


def test_bare_visitors_and_guests_search_real_unflagged_chunks_without_embedding(anon, services, monkeypatch):
    services.index.upsert([Chunk("plain","demo-review","demo.pdf",2,"The otter transport obligation.",owner="demo"),
                           Chunk("private","private-review","secret.pdf",1,"The otter secret.",owner="private")])
    embed = Mock(side_effect=AssertionError("visitor search must be keyword only"))
    monkeypatch.setattr(services.embedder,"embed",embed)
    for headers in ({},GUEST):
        response = anon.get("/api/search",params={"q":"otter"},headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 1 and response.json()[0]["review_id"] == "demo-review"
        assert response.json()[0]["finding_id"] is None
        assert "secret" not in response.text
    embed.assert_not_called()


def test_cloud_keyword_query_filters_workspace_and_reads_full_chunks():
    filters=[]
    class Query:
        def where(self,*,filter):
            filters.append((filter.field_path,filter.value))
            return self
        def stream(self):
            assert filters == [("owner","demo")]
            return [NS(id="plain",to_dict=lambda:dict(review_id="r",filename="a.pdf",page=1,
                text="Unflagged platypus obligation",owner="demo"))]
    index=FirestoreIndex.__new__(FirestoreIndex)
    index._col=Query()
    result=index.keyword("platypus",10,owner="demo")
    assert len(result)==1 and result[0][0].finding_id is None


def test_signed_in_search_uses_real_uid_across_workspaces_and_admin_has_no_exemption(client,admin,services):
    models=Models()
    services.embedder=embedder(models)
    for caller,uid in ((client,"alice"),(admin,"root")):
        response=caller.get("/api/search",params={"q":"first"})
        assert response.status_code==200
        assert services.budget_ledger.used(uid,budget.today())==1
    from datetime import datetime,timezone
    now=datetime.now(timezone.utc)
    services.workspaces.create(Workspace(id="co-second",name="Second",kind="company",created_at=now))
    services.workspaces.put_member("co-second",Member(uid="alice",email="alice@example.test",role="owner",joined_at=now))
    assert client.get("/api/search",params={"q":"second"},headers={"X-Workspace-Id":"co-second"}).status_code==200
    assert services.budget_ledger.used("alice",budget.today())==2
    # Admin still obeys the dollar cap, even though old count caps exempt admins.
    assert services.budget_ledger.reserve(BudgetReservation("root",budget.today(),"rest",999_999),1_000_000)
    denied=admin.get("/api/search",params={"q":"third"})
    assert denied.status_code==429 and denied.json()["error"]["code"]=="budget_exceeded"


def test_indexing_and_parallel_knowledge_embeddings_are_charged_to_request_uid(admin,services):
    models=Models()
    services.embedder=embedder(models)
    rid=seed(admin)
    assert services.budget_ledger.used("root",budget.today())==len(models.embeddings)
    assert len(models.embeddings)>1 and services.budget_ledger.used("demo",budget.today())==0
    assert any(row["config"].task_type=="RETRIEVAL_DOCUMENT" for row in models.embeddings)
    assert admin.get(f"/api/reviews/{rid}",headers=DEMO).status_code==200


def test_copy_cache_and_persistent_draft_are_free_at_full_budget_after_cold_app(anon,admin,client,services,monkeypatch):
    rid=seed(admin)
    path=f"/api/reviews/{rid}/negotiation"
    draft=admin.post(path,headers=DEMO)
    assert draft.status_code==200
    key=f"negotiations/{rid}.json"
    assert services.blobs.get(key) is not None
    exhaust(services,"root")
    exhaust(services,"alice")
    gen,emb=Mock(side_effect=AssertionError("cache must not generate")),Mock(side_effect=AssertionError("cache/copy must not embed"))
    monkeypatch.setattr(services.llm,"generate",gen)
    monkeypatch.setattr(services.embedder,"embed",emb)
    cold=TestClient(create_app(services.settings,services))
    for headers in ({},GUEST,{**DEMO,**bearer(ROOT)}):
        assert cold.post(path,headers=headers).json()==draft.json()
        assert cold.post(SAMPLE,headers=headers).headers["X-Review-Source"]=="cached"
    copied=client.post(SAMPLE)
    assert copied.status_code==200 and copied.headers["X-Review-Source"]=="copied"
    assert services.index.chunks(copied.json()["id"],owner=copied.json()["owner"])
    assert client.post(path).status_code==404  # different workspace, even though the blob is present
    gen.assert_not_called();emb.assert_not_called()


def test_malformed_cache_is_a_safe_miss_and_delete_removes_persisted_draft(anon,admin,services):
    rid=seed(admin)
    key=f"negotiations/{rid}.json";path=f"/api/reviews/{rid}/negotiation"
    for malformed in (b"not JSON private detail",b'{"review_id":"some-other-id","subject":"s","body":"b","asks":[]}'):
        services.blobs.put(key,malformed,"application/json")
        denied=anon.post(path)
        assert denied.status_code==401 and "private detail" not in denied.text
        assert anon.post(path,headers=GUEST).status_code==403
    assert admin.post(path,headers=DEMO).status_code==200
    assert admin.delete(f"/api/reviews/{rid}",headers=DEMO).status_code==204
    assert services.blobs.get(key) is None
    cold=TestClient(create_app(services.settings,services))
    assert cold.post(path,headers=GUEST).status_code==404


def test_account_deletion_removes_draft_but_not_spending_ledger(client,services):
    rid=client.post(SAMPLE).json()["id"]
    assert client.post(f"/api/reviews/{rid}/negotiation").status_code==200
    key=f"negotiations/{rid}.json"
    assert services.blobs.get(key)
    services.budget_ledger.reserve(BudgetReservation("alice",budget.today(),"known",500),1_000_000)
    assert client.post("/api/me/delete").status_code==200
    assert services.blobs.get(key) is None
    assert services.budget_ledger.used("alice",budget.today())==500
    assert client.post(f"/api/reviews/{rid}/negotiation").status_code==404


@pytest.mark.parametrize("accept", ["application/json","text/event-stream"])
def test_budget_refusal_preflight_preserves_http_error_parity(admin,services,accept):
    exhaust(services,"root")
    for path in (SAMPLE,"/api/reviews"):
        response=admin.post(path,headers={"Accept":accept},
            files={"file":("a.pdf",make_pdf(),"application/pdf")} if path.endswith("reviews") else None)
        assert response.status_code==429 and response.headers["content-type"]=="application/json"
        assert response.json()=={"error":{"code":"budget_exceeded","message":budget.BUDGET_MESSAGE}}
    assert services.llm.calls==[]


def test_late_budget_denial_is_a_safe_streamed_error(admin,services,monkeypatch):
    original=services.llm.chat
    def fill_budget(**kwargs):
        result=original(**kwargs)
        exhaust(services,"root")
        return result
    monkeypatch.setattr(services.llm,"chat",fill_budget)
    response=admin.post(SAMPLE,headers={"Accept":"text/event-stream"})
    assert response.status_code==200 and response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text and "budget_exceeded" in response.text
    assert "event: result" not in response.text and "Traceback" not in response.text
