"""Optional review options on POST /api/reviews (multipart form fields), threaded through pipeline and agent."""

from __future__ import annotations

import json

import pytest

from app import prompts
from app.agent import Toolbox
from app.models import ReviewOptions
from app.ports import ToolCall
from pdfgen import make_pdf

PDF = make_pdf()


def post(c, **fields):
    return c.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")}, data=fields)


@pytest.fixture
def spy(services):
    """Records the tool names offered to the model on every planning turn."""
    seen: list[list[str]] = []
    orig = services.llm.chat

    def chat(**kw):
        seen.append([t.name for t in kw["tools"]])
        return orig(**kw)

    services.llm.chat = chat
    return seen


def system_of(services, purpose="review"):
    return next(c["system"] for c in reversed(services.llm.calls) if c["purpose"] == purpose)


# ---- defaults and parsing -------------------------------------------------------------------------------------


def test_defaults_when_no_option_is_sent_and_when_fields_are_empty(client, services, spy):
    r = post(client)  # the form the UI sends today: just the file
    assert r.status_code == 200
    assert r.json()["provenance"]["options"] == {"contract_type": "auto", "jurisdiction": "auto",
                                                  "apply_company_rules": True, "perspective": "auto", "company_doc_ids": None}
    sys_default = system_of(services)
    assert "THE READER'S CHOICES" not in sys_default  # a default review's prompt is unchanged
    assert all(set(names) == {"set_jurisdiction", "search_law", "search_playbook", "search_company", "read_page"} for names in spy)
    empty = post(client, contract_type="", jurisdiction="", apply_company_rules="", perspective="")
    assert empty.status_code == 200 and empty.json()["provenance"]["options"] == r.json()["provenance"]["options"]


def test_values_are_case_insensitive_and_recorded_normalised(client):
    p = post(client, contract_type="NDA", jurisdiction="sg", apply_company_rules="False", perspective="Individual").json()["provenance"]
    assert p["options"] == {"contract_type": "nda", "jurisdiction": "SG", "apply_company_rules": False, "perspective": "individual", "company_doc_ids": None}
    for off in ("false", "0", "no", "off"):
        assert post(client, apply_company_rules=off).json()["provenance"]["options"]["apply_company_rules"] is False
    for on in ("true", "1", "yes", "on", "TRUE"):
        assert post(client, apply_company_rules=on).json()["provenance"]["options"]["apply_company_rules"] is True


@pytest.mark.parametrize("field,value", [("contract_type", "other"), ("contract_type", "lease"), ("jurisdiction", "UK"),
                                         ("jurisdiction", "my-sg"), ("perspective", "lawyer"), ("apply_company_rules", "maybe")])
def test_a_bad_option_is_422_with_a_plain_message_and_costs_nothing(client, services, field, value):
    n = len(services.llm.calls)
    r = post(client, **{field: value})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_option"
    assert field in r.json()["error"]["message"] and "must be" in r.json()["error"]["message"]
    assert len(services.llm.calls) == n and services.store.list(owner=client.get("/api/workspaces").json()[0]["workspace"]["id"]) == []


def test_a_bad_option_does_not_spend_a_rate_limit_slot(services):
    import dataclasses

    from fastapi.testclient import TestClient

    from app.main import create_app
    from conftest import ALICE, bearer

    c = TestClient(create_app(dataclasses.replace(services.settings, rate_limit_per_hour=1, rate_limit_user_per_hour=1), services),
                   headers=bearer(ALICE))
    assert post(c, jurisdiction="UK").status_code == 422
    assert post(c).status_code == 200  # the one slot was still free


def test_anonymous_upload_with_options_is_still_401(anon):
    r = post(anon, jurisdiction="MY")
    assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"


# ---- jurisdiction ---------------------------------------------------------------------------------------------


def test_chosen_jurisdiction_locks_the_law_instead_of_the_agent(client, services, spy):
    auto = post(client).json()
    assert auto["jurisdiction"] == "my" and any(c["ref"] == "my-contracts-act-s75" for f in auto["findings"] for c in f["citations"])
    spy.clear()
    sg = post(client, jurisdiction="SG").json()
    assert sg["jurisdiction"] == "sg"
    assert all("set_jurisdiction" not in names for names in spy)  # withdrawn: nothing left for the agent to choose
    assert "Do not call set_jurisdiction" in system_of(services) and "Singapore law" in system_of(services)
    # the Malaysian source is never retrieved (searches go to Singapore), so the model's Malaysian citation is removed
    assert not any(c["jurisdiction"] == "my" for f in sg["findings"] for c in f["citations"])
    assert any(x["kind"] == "citation" and x["text"] == "my-contracts-act-s75" and
               x["reason"] in ("source_not_retrieved", "wrong_jurisdiction") for x in sg["removed"])
    d = sg["trace"][0]["detail"]
    assert "set by the reader: jurisdiction=SG" in d and "chosen by the reader: Singapore law -> sg" in d
    # the law searches really ran against Singapore law, whatever the model asked for
    refs = [r["ref"] for s in sg["trace"] if s["action"] == "search_law" for r in json.loads(s["detail"])["results"]]
    assert refs and all(r.startswith("sg-") for r in refs), refs


def test_a_chosen_jurisdiction_cannot_be_moved_by_the_agent(services):
    from app.knowledge import Knowledge  # noqa: F401
    from app.agent import apply_options

    tools = Toolbox(services.kb, services.company, services.embedder, ["x"], owner="w")
    apply_options(tools, ReviewOptions(jurisdiction="MY"))
    out = json.loads(tools.run(ToolCall("set_jurisdiction", {"governing_law": "laws of Singapore", "jurisdiction": "sg"})).payload)
    assert out["jurisdiction"] == "my" and "unchanged" in out["note"]
    assert tools.jurisdiction == "my" and tools.jurisdiction_source == "user"


# ---- contract type ----------------------------------------------------------------------------------------------


def test_chosen_contract_type_is_used_and_told_to_the_model(client, services):
    assert post(client).json()["contract_type"] == "service_agreement"  # what the agent decides on its own
    r = post(client, contract_type="nda").json()
    assert r["contract_type"] == "nda"
    assert 'Set contract_type to "nda"' in system_of(services)
    assert "set by the reader: contract_type=nda" in r["trace"][0]["detail"]
    assert post(client, contract_type="auto").json()["contract_type"] == "service_agreement"


# ---- company rules ----------------------------------------------------------------------------------------------


def test_company_rules_off_withdraws_the_tool_and_the_tool_refuses(client, services, spy):
    client.post("/api/company/docs", files={"file": ("policy.pdf", PDF, "application/pdf")})
    on = post(client).json()
    assert any(names for names in spy if "search_company" in names)
    spy.clear()
    off = post(client, apply_company_rules="false").json()
    assert all("search_company" not in names for names in spy)
    assert OPT_NO_COMPANY in system_of(services)
    assert not any(c["kind"] == "company" for f in off["findings"] for c in f["citations"])
    assert all(f["policy_breach"] is None for f in off["findings"])
    assert "apply_company_rules=False" in off["trace"][0]["detail"]
    assert on["provenance"]["options"]["apply_company_rules"] is True

    tools = Toolbox(services.kb, services.company, services.embedder, ["x"], owner=client.get("/api/workspaces").json()[0]["workspace"]["id"])
    tools.company_rules = False
    res = tools.run(ToolCall("search_company", {"query": "liability"}))
    assert json.loads(res.payload)["results"] == [] and res.sources == []


OPT_NO_COMPANY = prompts.OPTION_NO_COMPANY


# ---- perspective ------------------------------------------------------------------------------------------------


def test_perspective_reaches_the_prompt(client, services):
    post(client, perspective="individual")
    assert prompts.OPTION_INDIVIDUAL in system_of(services)
    post(client, perspective="business")
    assert prompts.OPTION_BUSINESS in system_of(services) and prompts.OPTION_INDIVIDUAL not in system_of(services)
    post(client)
    assert "THE READER'S CHOICES" not in system_of(services)


def test_auto_perspective_is_the_individual_for_employment_tenancy_hire_purchase_only(client, services):
    for ctype in ("employment", "tenancy", "hire_purchase"):
        post(client, contract_type=ctype)
        assert prompts.OPTION_INDIVIDUAL in system_of(services), ctype
    for ctype in ("nda", "service_agreement"):
        post(client, contract_type=ctype)
        assert prompts.OPTION_INDIVIDUAL not in system_of(services), ctype
    post(client, contract_type="employment", perspective="business")  # an explicit choice beats the inference
    assert prompts.OPTION_BUSINESS in system_of(services) and prompts.OPTION_INDIVIDUAL not in system_of(services)


def test_options_block_unit():
    assert prompts.options_block(None) == "" and prompts.options_block(ReviewOptions()) == ""
    b = prompts.options_block(ReviewOptions(contract_type="tenancy", jurisdiction="SG", apply_company_rules=False))
    assert b.count("\n- ") == 4  # type, law, rules off, individual (auto on a tenancy)
    assert "tenancy agreement" in b and "Singapore law" in b


# ---- recorded ---------------------------------------------------------------------------------------------------


def test_options_are_in_provenance_history_audit_and_survive_a_reload(client, services):
    a = post(client, contract_type="nda", jurisdiction="MY").json()
    got = client.get(f"/api/reviews/{a['id']}").json()
    assert got["provenance"]["options"]["contract_type"] == "nda" and got["provenance"]["options"]["jurisdiction"] == "MY"
    ev = next(e for e in client.get("/api/activity").json() if e["action"] == "reviewed" and e["target_id"] == a["id"])
    assert ev["detail"]["options"] == got["provenance"]["options"]
    again = post(client, contract_type="service_agreement").json()  # same document, other options: linked as a re-run
    assert again["previous_review_id"] == a["id"]
    assert again["provenance"]["options"]["contract_type"] == "service_agreement"


def test_a_review_made_before_options_existed_still_loads(services):
    from app.models import Provenance

    p = Provenance.model_validate({"model": "m", "kb_version": "k", "prompt_version": "p"})
    assert p.options is None


def test_sample_runs_record_default_options_and_copies_keep_them(anon, client):
    demo = anon.post("/api/reviews/sample/demo-01", headers={"X-Workspace-Id": "demo"}).json()
    assert demo["provenance"]["options"]["contract_type"] == "auto"
    mine = client.post("/api/reviews/sample/demo-01").json()
    assert mine["provenance"]["options"] == demo["provenance"]["options"]


# ---- which company policies ---------------------------------------------------------------------------------------


def policy(c, name):
    return c.post("/api/company/docs", files={"file": (name, PDF, "application/pdf")}).json()["id"]


def test_company_doc_ids_default_is_all_policies(client):
    policy(client, "a.pdf")
    assert post(client).json()["provenance"]["options"]["company_doc_ids"] is None
    assert post(client, company_doc_ids="").json()["provenance"]["options"]["company_doc_ids"] is None
    assert post(client, company_doc_ids=" , ,").json()["provenance"]["options"]["company_doc_ids"] is None


def test_chosen_policies_are_recorded_in_order_without_duplicates(client, services):
    a, b, c = policy(client, "a.pdf"), policy(client, "b.pdf"), policy(client, "c.pdf")
    r = post(client, company_doc_ids=f" {b} ,{a},{b}").json()
    assert r["provenance"]["options"]["company_doc_ids"] == [b, a]
    assert "set by the reader: company_doc_ids=2" in r["trace"][0]["detail"]
    assert "chose 2 of the company's policy documents" in system_of(services)
    ev = next(e for e in client.get("/api/activity").json() if e["action"] == "reviewed" and e["target_id"] == r["id"])
    assert ev["detail"]["options"]["company_doc_ids"] == [b, a]
    assert c not in json.dumps(r["provenance"])


def test_only_the_chosen_documents_are_searched_and_cited(client, services):
    a, b = policy(client, "a.pdf"), policy(client, "b.pdf")
    ws = client.get("/api/workspaces").json()[0]["workspace"]["id"]
    seen = []
    real = services.company.search

    def spy(vec, limit, *, owner, doc_ids=None):
        out = real(vec, limit, owner=owner, doc_ids=doc_ids)
        seen.append((doc_ids, {c.doc_id for c, _ in out}))
        return out

    services.company.search = spy
    post(client)  # all policies: both documents are in play
    assert seen and all(d is None for d, _ in seen) and {x for _, ids in seen for x in ids} == {a, b}
    seen.clear()
    post(client, company_doc_ids=a)
    assert seen and all(d == frozenset({a}) for d, _ in seen)
    assert {x for _, ids in seen for x in ids} == {a}  # nothing from b came back
    # and the tool itself: exactly the chosen document's passages
    tools = Toolbox(services.kb, services.company, services.embedder, ["x"], owner=ws)
    tools.company_doc_ids = frozenset({b})
    res = json.loads(tools.run(ToolCall("search_company", {"query": "liability cap"})).payload)
    assert res["results"] and all(r["ref"] == f"company:{b}" for r in res["results"])


def test_unknown_or_foreign_policy_ids_are_422_and_cost_nothing(api, client, services):
    from fastapi.testclient import TestClient

    from conftest import BOB, bearer

    mine = policy(client, "mine.pdf")
    bob = TestClient(api, headers=bearer(BOB))
    theirs = policy(bob, "theirs.pdf")
    n = len(services.llm.calls)
    for bad in ("nope", f"{mine},nope", theirs, f"{mine},{theirs}", "x" * 40):
        r = post(client, company_doc_ids=bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_option", bad
        assert "company_doc_ids" in r.json()["error"]["message"]
    foreign = post(client, company_doc_ids=theirs).json()["error"]["message"]
    unknown = post(client, company_doc_ids="nope").json()["error"]["message"].replace("nope", "<id>")
    assert foreign.replace(theirs[:40], "<id>") == unknown  # a foreign id looks exactly like a missing one
    assert len(services.llm.calls) == n
    too_many = ",".join(f"d{i}" for i in range(51))
    assert post(client, company_doc_ids=too_many).status_code == 422


def test_chosen_policies_are_ignored_when_company_rules_are_off(client):
    r = post(client, apply_company_rules="false", company_doc_ids="not-even-checked")
    assert r.status_code == 200
    o = r.json()["provenance"]["options"]
    assert o["apply_company_rules"] is False and o["company_doc_ids"] is None


def test_a_policy_deleted_after_the_review_does_not_break_the_record(client):
    a = policy(client, "a.pdf")
    r = post(client, company_doc_ids=a).json()
    client.delete(f"/api/company/docs/{a}")
    assert client.get(f"/api/reviews/{r['id']}").json()["provenance"]["options"]["company_doc_ids"] == [a]


def test_memory_store_filters_by_document(services):
    from app.models import CompanyDoc
    from app.ports import CompanyChunk
    from datetime import datetime, timezone

    s = services.company
    vec = services.embedder.embed(["liability"], query=True)[0][0]
    for did in ("d1", "d2"):
        s.put(CompanyDoc(id=did, filename=f"{did}.pdf", title=did, created_at=datetime.now(timezone.utc), pages=1),
              [CompanyChunk(id=f"{did}-0", doc_id=did, title=did, page=1, text="liability cap twelve months", embedding=vec)],
              owner="w")
    assert {c.doc_id for c, _ in s.search(vec, 5, owner="w")} == {"d1", "d2"}
    assert {c.doc_id for c, _ in s.search(vec, 5, owner="w", doc_ids=frozenset({"d2"}))} == {"d2"}
    assert s.search(vec, 5, owner="w", doc_ids=frozenset({"zzz"})) == []
