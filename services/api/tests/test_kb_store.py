"""Knowledge store, hybrid ranking, playbook overrides, deep links, company citations, verification detail."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from app import deeplinks
from app.adapters.local import HashEmbedder, MemoryCompanyStore
from app.agent import Toolbox, Trace, fill_cited, run_agent
from app.knowledge import Knowledge
from app.knowledge_store import (HybridKnowledge, LocalKnowledgeStore, docs_from_files, ingest, rrf)
from app.models import Citation, CompanyDoc, LLMCitation, LLMFinding, Metrics, ReviewResult, Verdict
from app.pipeline import (UserError, locate_company_boxes, verify_citations, verify_findings)
from app.playbook_overrides import MemoryPlaybookOverrides, PlaybookOverrideIn, merged_items
from app.ports import CompanyChunk, ToolCall
from conftest import make_knowledge_dir
from pdfgen import PAGES, make_pdf
from test_agent import ScriptedLLM, call

POLICY = ["COMPANY CONTRACT POLICY\n\n1. Vendor liability must never be capped below 12 months of fees paid.\n"
          "2. We do not accept automatic renewal with more than 30 days notice.\n"]


@pytest.fixture
def kdir(tmp_path):
    return make_knowledge_dir(tmp_path)


@pytest.fixture
def kb(kdir):
    return Knowledge.load(kdir)


# ---- reciprocal-rank fusion ---------------------------------------------------------------------------


def test_rrf_rewards_agreement_between_rankings():
    fused = dict(rrf([["a", "b", "c"], ["c", "b", "d"]]))
    # b is 2nd and 2nd, c is 3rd and 1st: both beat a (1st, absent) and d (absent, 3rd); c edges b
    assert list(fused) == ["c", "b", "a", "d"]
    assert fused["b"] == pytest.approx(2 / 62) and fused["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert fused["a"] == pytest.approx(1 / 61) and fused["d"] == pytest.approx(1 / 63)
    assert rrf([]) == [] and rrf([["x"]]) == [("x", pytest.approx(1 / 61))]


class FixedVectorStore(LocalKnowledgeStore):
    """A store whose vector search returns a fixed ranking, to prove fusion changes the order."""

    def __init__(self, order: list[str], jurisdiction: str = "my"):
        super().__init__()
        self.order, self.jurisdiction = order, jurisdiction

    def vector_search(self, vector, kinds, limit):
        from app.knowledge_store import KnowledgeDoc

        return [(KnowledgeDoc(id=i, kind="law", title=i, jurisdiction=self.jurisdiction), 1.0 - n / 10)
                for n, i in enumerate(self.order)]


def test_hybrid_search_fuses_vector_and_keyword_rankings(kb):
    # keyword alone ranks s75 first for a penalty query; the vector side prefers s28
    kw = [e.id for e in kb.search_law("penalty liquidated damages restraint", "my")]
    assert kw[0] == "my-contracts-act-s75"
    hybrid = HybridKnowledge(kb, FixedVectorStore(["my-contracts-act-s28", "my-contracts-act-s75"]), HashEmbedder())
    fused = [e.id for e in hybrid.search_law("penalty liquidated damages restraint", "my")]
    assert set(fused) == {"my-contracts-act-s75", "my-contracts-act-s28"}
    # s75 is 1st by keyword and 2nd by vector; s28 is 2nd and 1st: a tie broken by first appearance
    assert fused[0] == "my-contracts-act-s75"
    hybrid2 = HybridKnowledge(kb, FixedVectorStore(["my-contracts-act-s28"]), HashEmbedder())
    assert hybrid2.search_law("penalty liquidated damages", "my")[0].id == "my-contracts-act-s75"
    only_vec = HybridKnowledge(kb, FixedVectorStore(["my-contracts-act-s28"]), HashEmbedder())
    # a vector-only hit is returned even when no keyword matches
    assert "my-contracts-act-s28" in [e.id for e in only_vec.search_law("zzzz", "my")]
    # vector hits from the other jurisdiction are ignored
    assert hybrid.search_law("zzzz", "sg") == []


def test_hybrid_search_falls_back_to_keywords_when_the_store_fails(kb):
    class Down(LocalKnowledgeStore):
        def vector_search(self, *a, **k):
            raise RuntimeError("firestore unavailable")

        def list_docs(self):
            raise RuntimeError("firestore unavailable")

    h = HybridKnowledge(kb, Down(), HashEmbedder())
    assert h.search_law("penalty liquidated damages", "my")[0].id == "my-contracts-act-s75"
    assert len(h.entries()) == len(kb.entries())  # browsing falls back to memory too
    assert h.entry("my-contracts-act-s75").text


def test_scores_come_with_the_hits(kb):
    scored = kb.search_law_scored("penalty liquidated damages", "my")
    assert scored[0][0].id == "my-contracts-act-s75" and scored[0][1] > 0


# ---- ingest (local target) ----------------------------------------------------------------------------


def test_ingest_is_idempotent_and_picks_up_changes(kdir, tmp_path):
    store, emb = LocalKnowledgeStore(tmp_path / "store"), HashEmbedder()
    first = ingest(store, docs_from_files(kdir, emb.model), emb, "v1")
    n_law = len(list((kdir / "law").glob("*/*.md")))
    n_playbook = len(list((kdir / "playbook").glob("*/*.md")))
    assert (first.total, first.new, first.updated, first.unchanged) == (n_law + n_playbook, n_law + n_playbook, 0, 0)
    second = ingest(store, docs_from_files(kdir, emb.model), emb, "v1")
    assert (second.new, second.updated, second.unchanged) == (0, 0, first.total)
    embeds = []
    real = emb.embed
    emb.embed = lambda texts, **kw: (embeds.append(len(texts)), real(texts, **kw))[1]
    ingest(store, docs_from_files(kdir, emb.model), emb, "v1")
    assert embeds == []  # nothing changed: no embedding calls
    f = kdir / "law" / "my" / "my-contracts-act-s75.md"
    f.write_text(f.read_text() + "\nA new sentence.\n")
    third = ingest(store, docs_from_files(kdir, emb.model), emb, "v1")
    assert (third.new, third.updated, third.unchanged) == (0, 1, first.total - 1)
    # a fresh store object over the same folder sees the same hashes (it persisted)
    assert len(LocalKnowledgeStore(tmp_path / "store").content_hashes()) == first.total


def test_ingested_records_have_the_agreed_fields(kdir, tmp_path):
    store, emb = LocalKnowledgeStore(tmp_path / "s"), HashEmbedder()
    ingest(store, docs_from_files(kdir, emb.model), emb, "v7")
    d = store.get_doc("my-contracts-act-s75")
    assert (d.kind, d.jurisdiction, d.kb_version, d.gcs_uri) == ("law", "my", "v7", "file://kb/v7/my-contracts-act-s75.md")
    assert d.clause_types == ["liability", "indemnity"] and d.citation and d.plain and d.excerpt and d.url
    p = store.get_doc("playbook:service_agreement/liability")
    assert p.kind == "playbook" and p.contract_type == "service_agreement" and p.clause_types == ["liability"]
    assert b"reasonable compensation" in store.read_markdown("my-contracts-act-s75")
    assert (tmp_path / "s" / "kb" / "v7" / "my-contracts-act-s75.md").is_file()
    hit = store.vector_search(emb.embed(["penalty fixed sum named in the contract breach"], query=True)[0][0], ["law"], 3)
    assert hit[0][0].id == "my-contracts-act-s75"


def test_knowledge_routes_read_the_store_and_stream_raw(client, services):
    emb = services.embedder
    ingest(services.kb.store, docs_from_files(services.settings.knowledge_dir, emb.model), emb, "v1")
    e = client.get("/api/knowledge/my-contracts-act-s75").json()
    assert e["title"] == "Contracts Act 1950, s.75" and "reasonable compensation" in e["text"]
    assert e["source_url"] == "https://example.test/my/contracts-act-s75" and not e["text"].startswith("---")
    listed = client.get("/api/knowledge", params={"jurisdiction": "my"}).json()
    assert {x["id"] for x in listed} == {"my-contracts-act-s75", "my-contracts-act-s28"} and all(x["text"] is None for x in listed)
    raw = client.get("/api/knowledge/my-contracts-act-s75/raw")
    assert raw.status_code == 200 and raw.headers["content-type"].startswith("text/markdown")
    assert raw.text.startswith("---\nid: my-contracts-act-s75")  # the stored file itself, frontmatter included
    pb = client.get("/api/knowledge/playbook:nda/liability/raw")
    assert pb.status_code == 200 and "Liability" in pb.text
    assert client.get("/api/knowledge/playbook:nda/nope/raw").status_code == 404


# ---- playbook overrides -----------------------------------------------------------------------------


def ws_of(c) -> str:
    return c.get("/api/workspaces").json()[0]["workspace"]["id"]


def test_playbook_get_put_delete(client):
    ws = ws_of(client)
    base = client.get("/api/playbook", params={"contract_type": "nda"}).json()
    assert base and all(i["contract_type"] == "nda" and i["overridden"] is False for i in base)
    liability = next(i for i in base if i["clause_type"] == "liability")
    assert liability["id"] == "playbook:nda/liability" and liability["standard"] and liability["red_flags"]

    body = {"standard": "Fixed damages never above RM 50,000 in total.", "red_flags": ["any per-breach sum", " "],
            "ask": "Delete the per-breach sum.", "note": "Board decision 2026"}
    put = client.put("/api/playbook/nda/liability", json=body)
    assert put.status_code == 200, put.text
    p = put.json()
    assert p["overridden"] is True and p["id"] == f"playbook:{ws}/nda/liability" and p["updated_by"] == "alice"
    assert p["red_flags"] == ["any per-breach sum"] and p["standard"].startswith("Fixed damages")
    merged = client.get("/api/playbook", params={"contract_type": "nda"}).json()
    assert len(merged) == len(base)  # replaced in place, not added
    assert [i["overridden"] for i in merged if i["clause_type"] == "liability"] == [True]
    assert sum(i["overridden"] for i in merged) == 1
    other = client.get("/api/playbook", params={"contract_type": "service_agreement"}).json()
    assert not any(i["overridden"] for i in other)

    assert client.delete("/api/playbook/nda/liability").status_code == 204
    back = client.get("/api/playbook", params={"contract_type": "nda"}).json()
    assert [i for i in back if i["clause_type"] == "liability"] == [liability]  # exactly the default again
    assert client.delete("/api/playbook/nda/liability").status_code == 204  # reverting twice is fine


def test_playbook_validation(client):
    assert client.put("/api/playbook/nda/liability", json={}).status_code == 422  # nothing written
    assert client.put("/api/playbook/lease/liability", json={"standard": "x"}).status_code == 404
    assert client.put("/api/playbook/nda/Bad-Name", json={"standard": "x"}).status_code == 404
    assert client.put("/api/playbook/nda/liability", json={"standard": "x" * 5000}).status_code == 422


def test_playbook_is_workspace_scoped(client, bob, services):
    client.put("/api/playbook/nda/liability", json={"standard": "Alice only"})
    assert any(i["overridden"] for i in client.get("/api/playbook").json())
    assert not any(i["overridden"] for i in bob.get("/api/playbook").json())  # another workspace sees defaults
    bob.put("/api/playbook/nda/liability", json={"standard": "Bob only"})
    a = next(i for i in client.get("/api/playbook").json() if i["id"].endswith("nda/liability"))
    assert a["standard"] == "Alice only" and ws_of(client) in a["id"]
    assert bob.delete("/api/playbook/nda/liability").status_code == 204  # bob's revert leaves alice's alone
    assert any(i["overridden"] for i in client.get("/api/playbook").json())
    services.overrides.put("acme", "nda", "liability", PlaybookOverrideIn(standard="Acme only"), "u1")
    assert any(i.overridden for i in merged_items(services.kb, services.overrides, "acme"))


def test_playbook_writes_need_sign_in_and_a_managing_role(anon, api, services):
    r = anon.put("/api/playbook/nda/liability", json={"standard": "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"
    assert anon.delete("/api/playbook/nda/liability").status_code == 401
    assert anon.get("/api/playbook").status_code == 200  # reading the defaults needs no sign-in
    assert not any(i["overridden"] for i in anon.get("/api/playbook").json())
    # a plain member of a company workspace can read but not change the company's positions
    from fastapi.testclient import TestClient

    from conftest import bearer

    ana = TestClient(api, headers=bearer("test:ana:ana@acme.test:hd=acme.test"))
    gina = TestClient(api, headers=bearer("test:gina:gina@gmail.com"))
    wid = ana.get("/api/workspaces").json()[0]["workspace"]["id"]
    inv = ana.post(f"/api/workspaces/{wid}/invites").json()
    gina.post(f"/api/invites/{inv['code']}/accept")
    h = {"X-Workspace-Id": wid}
    denied = gina.put("/api/playbook/nda/liability", json={"standard": "x"}, headers=h)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden_role"
    assert ana.put("/api/playbook/nda/liability", json={"standard": "Acme position"}).status_code == 200
    assert any(i["overridden"] for i in gina.get("/api/playbook", headers=h).json())  # members read it


def test_playbook_edits_go_to_the_audit_trail_when_the_action_exists(client, services, monkeypatch):
    import app.playbook_overrides as po

    seen = []
    monkeypatch.setattr(services.audit, "record", lambda *a, **kw: seen.append((a, kw)))
    # today the schema enum has no playbook action: the edit works and is logged as skipped, not recorded
    assert client.put("/api/playbook/nda/liability", json={"standard": "s"}).status_code == 200
    assert seen == []
    # once the two actions are added to AuditAction, the real Audit.record signature is used
    import typing

    from app import models

    monkeypatch.setattr(models, "AuditAction", typing.Literal[(*typing.get_args(models.AuditAction), po.AUDIT_SET, po.AUDIT_REVERT)])
    client.put("/api/playbook/nda/liability", json={"standard": "s2"})
    client.delete("/api/playbook/nda/liability")
    ws = ws_of(client)
    assert [a[0][1] for a in seen] == [po.AUDIT_SET, po.AUDIT_REVERT]
    assert seen[0][0][0] == "alice" and seen[0][0][2] == "nda/liability" and seen[0][1] == {"workspace_id": ws}


def test_override_wins_in_search_playbook_and_is_citable(kb):
    store = MemoryPlaybookOverrides()
    tools = Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo", overrides=store)
    default = tools.run(call("search_playbook", contract_type="nda", clause_type="liability"))
    assert json.loads(default.payload)["results"][0]["ref"] == "nda/liability"
    store.put("demo", "nda", "liability", PlaybookOverrideIn(
        standard="Fixed damages never above RM 50,000.", red_flags=["a per-breach sum"], ask="Delete it."), "demo")
    out = tools.run(call("search_playbook", contract_type="nda", clause_type="liability"))
    r = json.loads(out.payload)["results"][0]
    assert r["ref"] == "playbook:demo/nda/liability" and r["title"].endswith("(your company's position)")
    assert "RM 50,000" in r["excerpt"] and "per-breach" in r["excerpt"]
    src = out.sources[0]
    assert (src.kind, src.ref) == ("playbook", "playbook:demo/nda/liability") and "RM 50,000" in src.excerpt
    # another owner still gets the default
    other = Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="acme", overrides=store)
    assert json.loads(other.run(call("search_playbook", contract_type="nda", clause_type="liability")).payload
                      )["results"][0]["ref"] == "nda/liability"
    # citation verification accepts the override ref and takes its fields from the stored record
    known = {src.ref: [src]}
    cites, dropped = verify_citations([LLMCitation(kind="playbook", ref=src.ref, excerpt="invented")], known)
    assert dropped == 0 and cites[0].title.endswith("(your company's position)") and "invented" not in cites[0].excerpt


def test_playbook_with_no_default_suggests_closest(kb):
    tools = Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    out = json.loads(tools.run(call("search_playbook", contract_type="nda", clause_type="payment")).payload)
    assert out["results"] == [] or out["note"].startswith("no playbook entry")  # a plain Knowledge has no related search
    hybrid = HybridKnowledge(kb)
    tools = Toolbox(hybrid, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    out = json.loads(tools.run(call("search_playbook", contract_type="nda", clause_type="payment")).payload)
    assert out["results"] and all(r["ref"].startswith("nda/") for r in out["results"])
    assert "closest" in out["note"]


# ---- trace detail ("show your work") ------------------------------------------------------------------


def test_trace_detail_json_and_cited_after_verification(kb):
    llm = ScriptedLLM([[call("search_law", query="penalty liquidated damages", jurisdiction="my"),
                        call("search_playbook", contract_type="service_agreement", clause_type="liability")]])
    trace = Trace()
    outcome = run_agent(llm, Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo"), PAGES, trace)
    law, playbook = (json.loads(s.detail) for s in trace.steps[1:3])
    assert set(law) == {"tool", "args", "results", "cited"} and law["tool"] == "search_law"
    assert law["args"] == {"query": "penalty liquidated damages", "jurisdiction": "my"}
    assert {"ref", "title", "kind", "score"} == set(law["results"][0]) and law["results"][0]["ref"] == "my-contracts-act-s75"
    assert playbook["results"][0]["ref"] == "service_agreement/liability" and playbook["results"][0]["kind"] == "playbook"
    assert law["cited"] == [] and playbook["cited"] == []
    # after verification only what ended up in a finding is marked
    fill_cited(trace.steps, {"my-contracts-act-s75"})
    law, playbook = (json.loads(s.detail) for s in trace.steps[1:3])
    assert law["cited"] == ["my-contracts-act-s75"] and playbook["cited"] == []
    assert trace.steps[-1].detail == "5 candidate findings" and outcome.tool_calls == 2


def test_review_trace_detail_cited_matches_the_findings(client, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    cited_in_findings = {c["ref"] for f in body["findings"] for c in f["citations"]}
    cited_in_trace = {r for s in body["trace"] if s["action"].startswith("search_") and s["detail"]
                      for r in json.loads(s["detail"])["cited"]}
    assert cited_in_trace == cited_in_findings and "my-contracts-act-s75" in cited_in_trace
    law_step = next(s for s in body["trace"] if s["action"] == "search_law")
    assert json.loads(law_step["detail"])["tool"] == "search_law"


# ---- deep links ----------------------------------------------------------------------------------------


def test_sso_deep_links():
    assert deeplinks.sso_link("https://sso.agc.gov.sg/Act/UCTA1977?WholeDoc=1",
                              "Unfair Contract Terms Act 1977 (2020 Rev Ed), s 2") == "https://sso.agc.gov.sg/Act/UCTA1977?ProvIds=pr2-"
    assert deeplinks.sso_link("https://sso.agc.gov.sg/Act/PDPA2012?WholeDoc=1",
                              "Personal Data Protection Act 2012 (2020 Rev Ed), s 26C") == "https://sso.agc.gov.sg/Act/PDPA2012?ProvIds=pr26C-"
    assert deeplinks.sso_link("https://sso.agc.gov.sg/Act/X?WholeDoc=1", "no section here") is None
    assert deeplinks.section_of("Contracts Act 1950 (Act 136), s 75") == "75"


def test_lom_pdf_page_found_by_searching_the_pdf():
    pages = ["Preamble and contents\n\n", "24. The consideration or object of an agreement is lawful, unless it is forbidden by a law;",
             "75. When a contract has been broken, if a sum is named in the contract as the amount to be paid in case of such breach, "
             "the party complaining of the breach is entitled to reasonable compensation."]
    pdf = make_pdf(pages)
    assert deeplinks.pdf_page_of(pdf, "When a contract has been broken, if a sum is named in the contract as the amount to be paid") == 3
    assert deeplinks.pdf_page_of(pdf, "The consideration or object of an agreement is lawful, unless it is forbidden") == 2
    assert deeplinks.pdf_page_of(pdf, "a passage that is not in this document at all, sorry") is None
    assert deeplinks.lom_link("https://x/Act.pdf", 50) == "https://x/Act.pdf#page=50"


def test_build_deeplinks_reports_what_it_could_not_resolve():
    entries = [
        {"id": "sg-a", "source_url": "https://sso.agc.gov.sg/Act/A1?WholeDoc=1", "citation": "A Act, s 3", "text": "t"},
        {"id": "my-a", "source_url": "https://lom.agc.gov.my/x/Act.pdf", "citation": "s 5", "text": "found here text long enough"},
        {"id": "my-b", "source_url": "https://lom.agc.gov.my/x/Other.pdf", "citation": "s 6", "text": "t"},
        {"id": "my-c", "source_url": "https://lom.agc.gov.my/x/Missing.pdf", "citation": "s 7", "text": "t"},
        {"id": "case", "source_url": "https://www.elitigation.sg/gd/s/2020_SGCA_119", "citation": "c", "text": "t"},
    ]

    def find(url, text):
        if "Other" in url:
            raise OSError("connection reset")
        return 12 if "Act.pdf" in url else None

    links, unresolved = deeplinks.build_deeplinks(entries, find)
    assert links == {"sg-a": "https://sso.agc.gov.sg/Act/A1?ProvIds=pr3-", "my-a": "https://lom.agc.gov.my/x/Act.pdf#page=12"}
    assert "PDF not fetched" in unresolved["my-b"] and "not found in the PDF" in unresolved["my-c"]
    assert "not a statute" in unresolved["case"]


def test_deeplinks_flow_into_citations_and_entries(kdir, monkeypatch, tmp_path):
    p = tmp_path / "deeplinks.json"
    p.write_text(json.dumps({"my-contracts-act-s75": "https://lom.example/Act.pdf#page=50"}))
    monkeypatch.setattr(deeplinks, "DEEPLINKS_PATH", p)
    import app.knowledge as knowledge_mod
    monkeypatch.setattr(knowledge_mod, "load_deeplinks", lambda: deeplinks.load_deeplinks(p))
    kb = Knowledge.load(kdir)
    assert kb.entry("my-contracts-act-s75").source_url == "https://lom.example/Act.pdf#page=50"
    assert kb.entry("my-contracts-act-s28").source_url == "https://example.test/my/contracts-act-s28"  # plain URL kept
    tools = Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    out = tools.run(call("search_law", query="penalty damages", jurisdiction="my"))
    src = next(s for s in out.sources if s.ref == "my-contracts-act-s75")
    assert src.source_url == "https://lom.example/Act.pdf#page=50" and src.url == "https://example.test/my/contracts-act-s75"
    cites, _ = verify_citations([LLMCitation(kind="law", ref=src.ref, excerpt="x")], {src.ref: [src]})
    assert cites[0].source_url == "https://lom.example/Act.pdf#page=50"
    from app.knowledge_store import docs_from_files
    monkeypatch.setattr("app.knowledge_store.load_deeplinks", lambda: deeplinks.load_deeplinks(p))
    docs = {d.id: d for d in docs_from_files(kdir)}
    assert docs["my-contracts-act-s75"].source_url == "https://lom.example/Act.pdf#page=50"
    assert docs["my-contracts-act-s75"].url == "https://example.test/my/contracts-act-s75"
    assert docs["my-contracts-act-s28"].source_url == "https://example.test/my/contracts-act-s28"


def test_shipped_deeplinks_file_is_sane():
    links = deeplinks.load_deeplinks()
    assert len(links) >= 20
    assert all(v.startswith("https://") for v in links.values())
    assert all("ProvIds=pr" in v or "#page=" in v for v in links.values())


# ---- company citations: doc_id, page, boxes, and the PDF route ---------------------------------------------


def _upload_policy(client):
    return client.post("/api/company/docs", files={"file": ("policy.pdf", make_pdf(POLICY), "application/pdf")}).json()


def test_company_citation_gets_doc_id_page_and_boxes(client, services, pdf_bytes):
    doc = _upload_policy(client)
    llm = ScriptedLLM([[call("search_company", query="vendor liability cap months of fees")]])
    from app.models import LLMReview

    final = LLMReview.model_validate_json((llm.fixtures_dir / "review.json").read_text())
    final.findings[2].citations = [LLMCitation(kind="company", ref=f"company:{doc['id']}",
                                                   excerpt="Vendor liability must never be capped below 12 months of fees paid.")]  # the rule's own words
    final.findings[2].policy_breach = "Our policy requires at least 12 months of fees."
    llm.generate = lambda **kw: (final, __import__("app.ports", fromlist=["LLMUsage"]).LLMUsage(llm.model, 10, 5))
    services.llm = llm
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    c = next(f for f in body["findings"] if f["policy_breach"])["citations"][0]
    assert c["doc_id"] == doc["id"] and c["page"] == 1 and c["kind"] == "company"
    assert c["boxes"] and all(len(b) == 4 and all(0 <= v <= 1 for v in b) for b in c["boxes"])
    # the box really sits on the cited words: render the page region and read it back
    pdf = client.get(f"/api/company/docs/{doc['id']}/pdf").content
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    words = ""
    for x0, y0, x1, y1 in c["boxes"]:
        r = pymupdf.Rect(x0 * page.rect.width, y0 * page.rect.height, x1 * page.rect.width, y1 * page.rect.height)
        words += " " + page.get_text("text", clip=pymupdf.Rect(r.x0 - 2, r.y0 - 2, r.x1 + 2, r.y1 + 2)).lower()
    assert "liability" in words and "12 months" in words


def test_company_boxes_are_best_effort(services):
    c = Citation(kind="company", ref="company:gone", title="t", excerpt="Vendor liability must never", page=1, doc_id="gone")
    from app.models import Finding

    f = Finding(id="f1", clause_type="liability", title="t", risk="high", quote="q" * 20, page=1, why_risky="w",
                standard="s", suggested_ask="a", citations=[c])
    locate_company_boxes(services, [f], "demo")  # the stored PDF is missing: no error, no boxes
    assert f.citations[0].boxes == []


def test_company_pdf_route(client, bob, anon, services):
    doc = _upload_policy(client)
    r = client.get(f"/api/company/docs/{doc['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert client.get("/api/company/docs/doesnotexist/pdf").status_code == 404
    assert client.get("/api/company/docs/..%2F..%2Fx/pdf").status_code == 404
    # workspace scoping: another workspace, and the demo visitor, get 404 for this id, same as a missing one
    assert bob.get(f"/api/company/docs/{doc['id']}/pdf").status_code == 404
    assert anon.get(f"/api/company/docs/{doc['id']}/pdf").status_code == 404
    # an orphan blob the owner's store does not list is not served either
    services.blobs.put("company/hidden.pdf", make_pdf(POLICY))
    assert client.get("/api/company/docs/hidden/pdf").status_code == 404
    client.delete(f"/api/company/docs/{doc['id']}")
    assert client.get(f"/api/company/docs/{doc['id']}/pdf").status_code == 404


def test_citation_boxes_survive_firestore_storage():
    from app.adapters.gcp import _from_firestore, _to_firestore
    from app.models import Finding

    c = Citation(kind="company", ref="company:d", title="t", excerpt="e", page=1, doc_id="d", boxes=[[0.1, 0.2, 0.5, 0.3]])
    f = Finding(id="f1", clause_type="liability", title="t", risk="high", quote="q" * 20, page=1, why_risky="w",
                standard="s", suggested_ask="a", citations=[c], boxes=[[0.1, 0.1, 0.2, 0.2]])
    r = ReviewResult(id="r", created_at=datetime.now(timezone.utc), filename="f", contract_type="nda", language="en", pages=1,
                     parties=[], verdict=Verdict(overall_risk="low", headline="h", summary=["s"]), findings=[f],
                     missing_clauses=[], metrics=Metrics(latency_ms=1, model="m"), disclaimer="d")
    stored = _to_firestore(r)
    assert stored["findings"][0]["citations"][0]["boxes"] == [{"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.3}]
    assert _from_firestore(stored) == r


# ---- verification detail and removed claims -----------------------------------------------------------------


def _f(quote, page=2, title="t", risk="high", citations=(), breach=""):
    return LLMFinding(clause_type="liability", title=title, risk=risk, quote=quote, page=page, why_risky="w",
                      standard="s", suggested_ask="a", baseline_ref="", citations=list(citations), policy_breach=breach)


def test_verification_exact_normalised_fuzzy_and_page_correction():
    removed: list = []
    found, *_ = verify_findings([
        _f("The Customer shall be liable without limit for any loss suffered by the Vendor.", title="exact"),
        _f("THE CUSTOMER SHALL BE LIABLE   without limit for any loss suffered by the vendor", title="normalised"),
        _f("The Vendors total liability is limited to the fees paid in the one month before a claim", title="fuzzy"),
        _f("This Agreement is governed by the laws of the State of Delaware.", page=2, title="moved"),
    ], PAGES, {}, kb_version="v3", removed=removed)
    by = {f.title: f.verification for f in found}
    assert by["exact"].quote_match == "exact" and by["exact"].score == 100 and by["exact"].page_corrected_from is None
    assert by["normalised"].quote_match == "normalised" and by["normalised"].score == 100
    assert by["fuzzy"].quote_match == "fuzzy" and 90 <= by["fuzzy"].score < 100
    assert by["moved"].page_corrected_from == 2 and next(f for f in found if f.title == "moved").page == 3
    assert all(v.kb_version == "v3" for v in by.values()) and removed == []


def test_removed_claim_quote_not_found():
    removed: list = []
    found, dropped, _ = verify_findings([_f("The Customer shall indemnify the Vendor against everything whatsoever.", page=2,
                                            title="Broad indemnity")], PAGES, {}, removed=removed)
    assert found == [] and dropped == 1
    assert [(r.kind, r.reason, r.page, r.finding_title) for r in removed] == [("finding", "quote_not_found", 2, "Broad indemnity")]
    assert removed[0].text.startswith("Broad indemnity: The Customer shall indemnify")


def test_removed_claim_source_not_retrieved_and_checked_count():
    known = {"my-contracts-act-s75": [Citation(kind="law", ref="my-contracts-act-s75", title="s75", excerpt="e")]}
    removed: list = []
    found, _, dropped_c = verify_findings([_f(
        "The Customer shall be liable without limit for any loss", title="Cap",
        citations=[LLMCitation(kind="law", ref="my-contracts-act-s75", excerpt="x"),
                   LLMCitation(kind="law", ref="my-fake-act-s999", excerpt="x")])], PAGES, known, removed=removed)
    assert dropped_c == 1 and found[0].verification.citations_checked == 2 and len(found[0].citations) == 1
    assert [(r.kind, r.reason, r.text, r.finding_title) for r in removed] == [
        ("citation", "source_not_retrieved", "my-fake-act-s999", "Cap")]


def test_removed_claim_no_company_citation():
    removed: list = []
    found, *_ = verify_findings([_f("The Customer shall be liable without limit for any loss", title="Cap",
                                    breach="Our policy forbids unlimited liability.")], PAGES, {}, removed=removed)
    assert found[0].policy_breach is None
    assert [(r.kind, r.reason, r.text, r.page, r.finding_title) for r in removed] == [
        ("policy_breach", "no_company_citation", "Our policy forbids unlimited liability.", 2, "Cap")]
    # with a verified company citation whose rule the model quotes, the breach stays and nothing is removed
    rule = "Vendor liability must never be uncapped or capped below 12 months of fees."
    known = {"company:d": [Citation(kind="company", ref="company:d", title="p", excerpt="1. " + rule, page=1,
                                    doc_id="d")]}
    removed2: list = []
    kept, *_ = verify_findings([_f("The Customer shall be liable without limit for any loss", title="Cap",
                                   citations=[LLMCitation(kind="company", ref="company:d", excerpt=rule)],
                                   breach="Our policy forbids unlimited liability.")], PAGES, known, removed=removed2)
    assert kept[0].policy_breach and removed2 == []
    # cited but not quoted (the model's words are not the rule's): no breach
    removed3: list = []
    kept, *_ = verify_findings([_f("The Customer shall be liable without limit for any loss", title="Cap",
                                   citations=[LLMCitation(kind="company", ref="company:d", excerpt="we never accept "
                                                          "unlimited exposure to vendors")],
                                   breach="Our policy forbids unlimited liability.")], PAGES, known, removed=removed3)
    assert kept[0].policy_breach is None and kept[0].citations  # the citation itself stays
    assert [(r.kind, r.reason) for r in removed3] == [("policy_breach", "no_company_citation")]


def test_removed_claims_are_persisted_and_returned(client, services, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    got = {(r["kind"], r["reason"]) for r in body["removed"]}
    # the fixture holds an invented indemnity quote, an invented citation and an unbacked policy breach
    assert got == {("finding", "quote_not_found"), ("citation", "source_not_retrieved"), ("policy_breach", "no_company_citation")}
    again = client.get(f"/api/reviews/{body['id']}").json()
    assert again["removed"] == body["removed"]
    assert all(f["verification"]["kb_version"] for f in again["findings"])
    assert body["metrics"]["dropped_unverified"] == 1 and body["metrics"]["dropped_unverified_citations"] == 1


def test_eval_verification_block(client, pdf_bytes):
    import sys

    from app.config import REPO_ROOT

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import eval as ev

    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    counts = ev.verification_counts(ReviewResult.model_validate(body))
    # the fixture: 5 findings (1 removed), 4 citations (1 removed), 1 policy breach claimed and removed
    assert counts["finding"] == {"made": 5, "removed": 1}
    assert counts["citation"]["removed"] == 1 and counts["policy_breach"] == {"made": 1, "removed": 1}
    md = "\n".join(ev.verification_block([{"verification": counts}, {"verification": counts}]))
    assert "| finding | 10 | 2 | 20.0% |" in md and "| all claims |" in md


# ---- jurisdiction: a Malaysian contract is not judged on Singapore law, nor the reverse ----------------------

from app.knowledge import LawEntry  # noqa: E402
from app.agent import _lock_jurisdiction  # noqa: E402


def _rec(ref, jur, kind="law", title=None):
    return Citation(kind=kind, ref=ref, title=title or ref, jurisdiction=jur, excerpt="e")


KNOWN = {"my-contracts-act-s75": [_rec("my-contracts-act-s75", "my", title="Contracts Act 1950, s.75")],
         "sg-case-denka": [_rec("sg-case-denka", "sg", "case", "Denka v Seraya")],
         "nda/liability": [Citation(kind="playbook", ref="nda/liability", title="Liability", excerpt="e")]}


def _cites(*refs):
    return [LLMCitation(kind="law", ref=r, excerpt="x") for r in refs]


def test_my_contract_citing_a_singapore_case_has_it_dropped_as_wrong_jurisdiction():
    removed: list = []
    found, dropped, dropped_c = verify_findings([_f(
        "The Customer shall be liable without limit for any loss", title="Uncapped",
        citations=_cites("my-contracts-act-s75", "sg-case-denka", "nda/liability"))], PAGES, KNOWN,
        removed=removed, jurisdiction="my")
    assert [c.ref for c in found[0].citations] == ["my-contracts-act-s75", "nda/liability"]  # playbook has no country
    assert dropped_c == 1 and found[0].verification.citations_checked == 3
    assert [(r.kind, r.reason, r.text, r.finding_title) for r in removed] == [
        ("citation", "wrong_jurisdiction", "sg-case-denka", "Uncapped")]


def test_sg_contract_drops_malaysian_law_and_keeps_its_own():
    removed: list = []
    cites, dropped = verify_citations(_cites("my-contracts-act-s75", "sg-case-denka"), KNOWN, removed=removed,
                                      finding_title="t", jurisdiction="sg")
    assert [c.ref for c in cites] == ["sg-case-denka"] and dropped == 1
    assert removed[0].reason == "wrong_jurisdiction" and removed[0].text == "my-contracts-act-s75"


def test_the_kb_record_decides_the_country_not_the_model():
    # the model calls it "kind: case" and a Malaysian one; the record says Singapore, so it goes
    cites, dropped = verify_citations([LLMCitation(kind="law", ref="sg-case-denka", excerpt="Malaysian s.75")], KNOWN,
                                      jurisdiction="my")
    assert cites == [] and dropped == 1


def test_not_retrieved_still_wins_over_wrong_jurisdiction():
    removed: list = []
    verify_citations(_cites("sg-never-returned"), KNOWN, removed=removed, jurisdiction="my")
    assert removed[0].reason == "source_not_retrieved"


def test_foreign_law_contract_gets_prefixed_fallback_titles():
    removed: list = []
    cites, dropped = verify_citations(_cites("my-contracts-act-s75", "sg-case-denka", "nda/liability"), KNOWN,
                                      removed=removed, finding_title="t", jurisdiction="other", home="my")
    assert [(c.ref, c.title) for c in cites] == [
        ("my-contracts-act-s75", "If disputed in Malaysia: Contracts Act 1950, s.75"), ("nda/liability", "Liability")]
    assert dropped == 1 and removed[0].reason == "wrong_jurisdiction"  # Singapore law is not the home fallback
    sg_home, _ = verify_citations(_cites("my-contracts-act-s75", "sg-case-denka"), KNOWN, jurisdiction="other", home="sg")
    assert [c.title for c in sg_home] == ["If disputed in Singapore: Denka v Seraya"]
    # the stored record is not modified, so a second finding is not double-prefixed
    again, _ = verify_citations(_cites("my-contracts-act-s75"), KNOWN, jurisdiction="other", home="my")
    assert again[0].title == "If disputed in Malaysia: Contracts Act 1950, s.75"


def test_no_jurisdiction_means_no_filtering_for_old_callers():
    cites, dropped = verify_citations(_cites("my-contracts-act-s75", "sg-case-denka"), KNOWN)
    assert len(cites) == 2 and dropped == 0


# ---- the tool lock -----------------------------------------------------------------------------------------


@pytest.fixture
def two_country_kb(tmp_path):
    k = make_knowledge_dir(tmp_path)
    return Knowledge.load(k)


def test_set_jurisdiction_makes_search_law_ignore_a_disagreeing_argument(two_country_kb):
    tools = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    out = tools.run(call("set_jurisdiction", governing_law="the laws of Malaysia", jurisdiction="my"))
    assert json.loads(out.payload)["law_searches_use"] == "my" and tools.governing_law == "the laws of Malaysia"
    res = tools.run(call("search_law", query="liability negligence reasonable", jurisdiction="sg"))
    body = json.loads(res.payload)
    assert all(s.jurisdiction == "my" for s in res.sources) and "Malaysia law only" in body["searched"]
    assert not any(r["ref"].startswith("sg-") for r in body["results"])
    again = json.loads(tools.run(call("set_jurisdiction", governing_law="Singapore", jurisdiction="sg")).payload)
    assert again["jurisdiction"] == "my" and tools.jurisdiction == "my"  # the first one wins


def test_foreign_law_searches_the_home_jurisdiction(two_country_kb):
    tools = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, home="my", owner="demo")
    out = json.loads(tools.run(call("set_jurisdiction", governing_law="the laws of the State of New York",
                                    jurisdiction="other")).payload)
    assert out["law_searches_use"] == "my" and "fallback" in out["note"]
    res = tools.run(call("search_law", query="penalty damages", jurisdiction="sg"))
    assert res.sources and all(s.jurisdiction == "my" for s in res.sources)
    sg_home = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, home="sg", owner="demo")
    sg_home.run(call("set_jurisdiction", governing_law="New York", jurisdiction="other"))
    assert all(s.jurisdiction == "sg" for s in sg_home.run(call("search_law", query="liability negligence")).sources)


def test_lock_applies_before_the_searches_of_the_same_turn(two_country_kb):
    llm = ScriptedLLM([[call("search_law", query="liability negligence reasonable", jurisdiction="sg"),
                        call("set_jurisdiction", governing_law="the laws of Malaysia", jurisdiction="my")]])
    trace = Trace()
    tools = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    outcome = run_agent(llm, tools, PAGES, trace)
    assert (outcome.jurisdiction, outcome.governing_law, outcome.jurisdiction_source) == ("my", "the laws of Malaysia", "tool")
    assert set(outcome.known) <= {"my-contracts-act-s75", "my-contracts-act-s28"}  # the sg search ran as Malaysian
    assert [t.action for t in trace.steps] == ["plan_research", "search_law", "draft_findings"]  # set_jurisdiction is not a lookup
    assert trace.steps[1].summary.startswith("Checked Malaysian law on")  # said "sg", searched (and reported) "my"


def test_final_call_is_told_the_fixed_jurisdiction(two_country_kb):
    llm = ScriptedLLM([[call("set_jurisdiction", governing_law="the laws of Singapore", jurisdiction="sg")]])
    seen = {}
    real = llm.generate
    llm.generate = lambda **kw: (seen.update(kw), real(**kw))[1]
    run_agent(llm, Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo"), PAGES, Trace())
    assert 'Set jurisdiction to "sg"' in seen["parts"][0] and "the laws of Singapore" in seen["parts"][0]


def test_majority_locks_when_the_model_never_set_it_and_mixes_countries(two_country_kb):
    calls = [call("search_law", query="a", jurisdiction="sg"), call("search_law", query="b", jurisdiction="my"),
             call("search_law", query="c", jurisdiction="my"), call("search_playbook", contract_type="nda", clause_type="term")]
    tools = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    run, skipped = _lock_jurisdiction(tools, calls)
    assert tools.jurisdiction == "my" and tools.jurisdiction_source == "majority"
    assert [c.args.get("query") for c in skipped] == ["a"] and len(run) == 3
    tie = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, home="my", owner="demo")
    _lock_jurisdiction(tie, [call("search_law", query="a", jurisdiction="sg"), call("search_law", query="b", jurisdiction="my")])
    assert tie.jurisdiction == "my"  # a tie goes to the home country
    one = Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    only_sg = [call("search_law", query="a", jurisdiction="sg")]
    assert _lock_jurisdiction(one, only_sg) == (only_sg, []) and one.jurisdiction is None  # one country: nothing to settle


def test_skipped_searches_still_get_an_answer_in_the_history(two_country_kb):
    llm = ScriptedLLM([[call("search_law", query="a", jurisdiction="sg"), call("search_law", query="b", jurisdiction="my"),
                        call("search_law", query="c", jurisdiction="my"), call("read_page", n=1)],
                       []])
    captured = []
    real = llm.chat
    llm.chat = lambda **kw: (captured.append(list(kw["messages"])), real(**kw))[1]
    # a read_page makes a follow-up turn, whose history we can inspect
    run_agent(llm, Toolbox(two_country_kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo"), PAGES, Trace())
    tool_msg = captured[-1][-1]
    assert tool_msg.role == "tool" and len(tool_msg.tool_results) == 4
    assert any("skipped" in str(r.result) for r in tool_msg.tool_results)


# ---- the whole review ---------------------------------------------------------------------------------------


def _review_with(services, client, pdf_bytes, governing_law, jurisdiction, cites, home="my"):
    from app.models import LLMReview
    from app.ports import LLMUsage

    llm = ScriptedLLM([[call("set_jurisdiction", governing_law=governing_law, jurisdiction=jurisdiction),
                        call("search_law", query="penalty liquidated damages", jurisdiction="my")]])
    final = LLMReview.model_validate_json((llm.fixtures_dir / "review.json").read_text())
    final.jurisdiction = "my"  # the model's own final word: the fixed jurisdiction must win
    for f in final.findings:
        f.citations, f.policy_breach = [], ""
    final.findings[1].citations = cites
    real = llm.generate
    llm.generate = lambda **kw: (final, LLMUsage(llm.model, 10, 5)) if kw["purpose"] == "review" else real(**kw)
    services.llm = llm
    object.__setattr__(services.settings, "home_jurisdiction", home)
    return client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()


def test_review_of_a_foreign_law_contract_prefixes_and_says_so(services, client, pdf_bytes):
    body = _review_with(services, client, pdf_bytes, "the laws of the State of Delaware", "other",
                        _cites("my-contracts-act-s75"))
    assert body["jurisdiction"] == "other"
    c = next(c for f in body["findings"] for c in f["citations"])
    assert c["title"] == "If disputed in Malaysia: Contracts Act 1950, s.75" and c["jurisdiction"] == "my"
    assert body["verdict"]["summary"][-1] == ("This contract is governed by the laws of the State of Delaware; "
                                              "we check it against Malaysian law only as a fallback.")
    assert len(body["verdict"]["summary"]) <= 3
    assert "governing law: the laws of the State of Delaware -> other" in body["trace"][0]["detail"]
    # a negotiation email does not present fallback law as the law that governs
    client.post(f"/api/reviews/{body['id']}/negotiation")
    assert "context:" not in services.llm.calls[-1]["parts"][0]


def test_review_of_a_malaysian_contract_with_a_singapore_citation(services, client, pdf_bytes):
    """The model cites a Singapore case that no tool returned for this country: dropped, and shown as removed."""
    body = _review_with(services, client, pdf_bytes, "the laws of Malaysia", "my",
                        _cites("my-contracts-act-s75", "sg-ucta-s2"))
    assert body["jurisdiction"] == "my"
    refs = [c["ref"] for f in body["findings"] for c in f["citations"]]
    assert refs == ["my-contracts-act-s75"]
    assert [(r["reason"], r["text"]) for r in body["removed"] if r["kind"] == "citation"] == [
        ("source_not_retrieved", "sg-ucta-s2")]  # the lock stopped any Singapore search, so it was never retrieved
    assert "This contract is governed" not in " ".join(body["verdict"]["summary"])
    # and when a Singapore record does reach the final call, wrong_jurisdiction is what removes it
    removed: list = []
    verify_citations(_cites("sg-ucta-s2"), {"sg-ucta-s2": [_rec("sg-ucta-s2", "sg")]}, removed=removed, jurisdiction="my")
    assert removed[0].reason == "wrong_jurisdiction"
