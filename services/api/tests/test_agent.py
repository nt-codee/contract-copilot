"""The tool-using agent: loop, caps, tools, citation verification, trace, company knowledge base."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import agent as agent_mod
from app.adapters.local import FakeLLM, HashEmbedder, MemoryCompanyStore
from app.agent import TOOLS, Toolbox, Trace, run_agent, tool_summary
from app.knowledge import Knowledge
from app.models import Citation, CompanyDoc, LLMCitation, LLMReview
from app.pipeline import verify_citations
from app.workspaces import personal_ws_id
from app.ports import ChatMessage, ToolCall, UpstreamError
from conftest import make_knowledge_dir
from pdfgen import PAGES, make_pdf



class ScriptedLLM(FakeLLM):
    """chat() plays `turns` (each a list of ToolCall); generate() the usual fixture."""

    def __init__(self, turns, **kw):
        super().__init__(**kw)
        self.turns = turns
        self.seen: list[list[ChatMessage]] = []

    def chat(self, *, system, messages, tools, purpose, force_tools=False):
        self.calls.append({"purpose": purpose, "system": system, "parts": [], "force_tools": force_tools})
        self.seen.append(list(messages))
        n = sum(1 for m in messages if m.role == "model")
        calls = self.turns(n) if callable(self.turns) else (self.turns[n] if n < len(self.turns) else [])
        return (ChatMessage(role="model", text="" if calls else "DONE", tool_calls=list(calls)),
                agent_mod.LLMUsage(self.model, 10, 5))


@pytest.fixture
def kb(tmp_path):
    return Knowledge.load(make_knowledge_dir(tmp_path))


@pytest.fixture
def toolbox(kb):
    return Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")


def call(name, **args):
    return ToolCall(name=name, args=args, id=f"id-{name}")


# ---- search_law ranking -------------------------------------------------------------------------


def test_search_law_ranks_by_keywords(kb):
    penalty = kb.search_law("penalty liquidated damages fixed sum breach", "my")
    assert penalty[0].id == "my-contracts-act-s75"
    restraint = kb.search_law("restraint of trade profession", "my")
    assert restraint[0].id == "my-contracts-act-s28"


def test_search_law_filters_jurisdiction_and_boosts_clause_type(kb):
    assert [e.id for e in kb.search_law("liability negligence reasonable", "sg")] == ["sg-ucta-s2"]
    assert all(e.jurisdiction == "my" for e in kb.search_law("liability contract", "my"))
    # the same weak query ranks s.28 first only when its clause type is boosted
    assert kb.search_law("agreement", "my", "non_compete")[0].id == "my-contracts-act-s28"
    assert kb.search_law("zzzz nothing", "my") == []


def test_search_law_tool_shape_and_size(toolbox):
    out = toolbox.run(call("search_law", query="penalty damages", jurisdiction="my"))
    body = json.loads(out.payload)
    first = body["results"][0]
    assert set(first) == {"ref", "title", "citation", "excerpt", "plain", "url"}
    assert first["ref"] == "my-contracts-act-s75" and len(first["excerpt"]) <= 400
    assert len(out.payload) <= 2500
    assert [s.ref for s in out.sources] == [r["ref"] for r in body["results"]]
    assert out.sources[0].jurisdiction == "my" and out.sources[0].kind == "law"
    bad = json.loads(toolbox.run(call("search_law", query="x", jurisdiction="fr")).payload)
    assert "error" in bad


def test_results_are_capped_at_2500_chars():
    big = [{"ref": f"r{i}", "title": "t", "excerpt": "word " * 200, "plain": "plain " * 100} for i in range(4)]
    text = agent_mod.compact(big)
    assert len(text) <= 2500 and len(json.loads(text)["results"]) == 4


def test_search_playbook_and_read_page(toolbox):
    body = json.loads(toolbox.run(call("search_playbook", contract_type="service_agreement",
                                       clause_type="liability")).payload)
    r = body["results"][0]
    assert r["ref"] == "service_agreement/liability" and len(r["excerpt"]) <= 1200
    missing = json.loads(toolbox.run(call("search_playbook", contract_type="nda", clause_type="payment")).payload)
    assert missing["results"] == [] and "payment" not in missing["available_clause_types"]
    page = json.loads(toolbox.run(call("read_page", n=2)).payload)
    assert page["page"] == 2 and "liable without limit" in page["text"]
    assert "error" in json.loads(toolbox.run(call("read_page", n=9)).payload)
    assert "error" in json.loads(toolbox.run(call("delete_everything")).payload)


def test_search_company_with_nothing_uploaded(toolbox):
    out = toolbox.run(call("search_company", query="liability cap"))
    assert json.loads(out.payload) == {"results": [], "note": "no company documents uploaded"}
    assert out.sources == []


# ---- the loop and its caps --------------------------------------------------------------------------


def _run(llm, kb, **kw):
    trace = Trace()
    tools = Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    return run_agent(llm, tools, PAGES, trace, **kw), trace


def test_loop_tool_call_then_result_then_final(kb):
    llm = ScriptedLLM([[call("search_law", query="penalty", jurisdiction="my"),
                        call("search_playbook", contract_type="service_agreement", clause_type="liability")]])
    outcome, trace = _run(llm, kb)
    # no read_page asked, so no follow-up turn and no "DONE" turn: one agent call, then the review
    assert [c["purpose"] for c in llm.calls] == ["agent", "review"]
    assert llm.calls[0]["force_tools"] is True  # turn 1 must emit tool calls
    # the final call saw the research, and knows exactly which refs came back
    assert set(outcome.known) == {"my-contracts-act-s75", "service_agreement/liability"}
    assert outcome.turns == 1 and outcome.tool_calls == 2
    assert [t.action for t in trace.steps] == ["search_law", "search_playbook", "draft_findings"]
    assert [t.step for t in trace.steps] == [1, 2, 3]


def test_final_call_is_prompted_with_the_research(kb):
    llm = ScriptedLLM([[call("search_law", query="penalty", jurisdiction="my")]])
    seen = {}
    real = llm.generate

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    llm.generate = spy
    _run(llm, kb)
    assert "### search_law(" in seen["parts"][0] and "my-contracts-act-s75" in seen["parts"][0]
    assert seen["schema"] is LLMReview


def test_tool_call_cap_16_in_the_one_planning_turn(kb):
    llm = ScriptedLLM(lambda n: [call("read_page", n=1 + i % 3) for i in range(20)])
    outcome, trace = _run(llm, kb)
    assert outcome.tool_calls == 16 and len(trace.steps) == 16 + 1
    assert llm.calls[-1]["purpose"] == "review"
    assert sum(c["purpose"] == "agent" for c in llm.calls) == 1  # budget gone: no follow-up turn


def test_follow_up_turn_only_when_read_page_was_asked(kb):
    # turn 1 asks for a page -> exactly one follow-up (not forced), which may read more pages
    llm = ScriptedLLM(lambda n: [call("read_page", n=1)] if n < 5 else [])
    outcome, trace = _run(llm, kb)
    assert outcome.turns == 2 and outcome.tool_calls == 2  # never a third turn
    assert [c["force_tools"] for c in llm.calls if c["purpose"] == "agent"] == [True, False]


def test_follow_up_ignores_searches(kb):
    llm = ScriptedLLM([[call("read_page", n=1)], [call("search_law", query="x", jurisdiction="my"),
                                                  call("read_page", n=2)]])
    outcome, _ = _run(llm, kb)
    assert outcome.tool_calls == 2 and "my-contracts-act-s75" not in outcome.known


def test_first_turn_failure_propagates_later_failure_degrades(kb):
    class Boom(ScriptedLLM):
        def chat(self, **kw):
            if self.fail_first or any(m.role == "model" for m in kw["messages"]):
                raise UpstreamError("down")
            return super().chat(**kw)

    first = Boom([[call("read_page", n=1)]])
    first.fail_first = True
    with pytest.raises(UpstreamError):
        _run(first, kb)
    later = Boom([[call("read_page", n=1)]])
    later.fail_first = False
    outcome, _ = _run(later, kb)  # the follow-up fails: we still write the review
    assert outcome.tool_calls == 1 and later.calls[-1]["purpose"] == "review"


def test_tools_declared_for_the_model():
    assert {t.name for t in TOOLS} == {"set_jurisdiction", "search_law", "search_playbook", "search_company", "read_page"}
    assert all(t.parameters["type"] == "object" for t in TOOLS)


# ---- citation verification --------------------------------------------------------------------------


def _known():
    return {
        "my-contracts-act-s75": [Citation(kind="law", ref="my-contracts-act-s75", title="Contracts Act 1950, s.75",
                                          jurisdiction="my", excerpt="KB text", url="https://example.test/s75")],
        "company:abc": [
            Citation(kind="company", ref="company:abc", title="Policy", page=1, excerpt="vendor liability cap"),
            Citation(kind="company", ref="company:abc", title="Policy", page=3, excerpt="payment within 30 days"),
        ],
    }


def test_invented_ref_dropped_and_model_copy_overwritten():
    cites, dropped = verify_citations([
        LLMCitation(kind="law", ref="my-contracts-act-s75", excerpt="the model made this up"),
        LLMCitation(kind="law", ref="my-contracts-act-s999", excerpt="not in any tool result"),
        LLMCitation(kind="case", ref="my-contracts-act-s75", excerpt="wrong kind too"),
    ], _known())
    assert dropped == 1
    assert len(cites) == 1  # the same source cited twice is shown once
    c = cites[0]
    assert (c.kind, c.title, c.excerpt, c.url, c.jurisdiction) == (
        "law", "Contracts Act 1950, s.75", "KB text", "https://example.test/s75", "my")


def test_company_citation_takes_the_matching_passage():
    cites, dropped = verify_citations(
        [LLMCitation(kind="company", ref="company:abc", excerpt="payment within 30 days")], _known())
    assert dropped == 0 and cites[0].page == 3 and cites[0].excerpt == "payment within 30 days"


# ---- trace summaries ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,args,expected", [
    ("search_law", {"query": "penalty liquidated damages", "jurisdiction": "my"},
     "Checked Malaysian law on penalty clauses"),
    ("search_law", {"query": "unfair exclusion", "jurisdiction": "sg", "clause_type": "liability"},
     "Checked Singapore law on liability caps"),
    ("search_law", {"query": "restraint of trade", "jurisdiction": "my"},
     "Checked Malaysian law on non-compete clauses"),
    ("search_company", {"query": "liability cap"}, "Checked your company's rules on liability caps"),
    ("search_playbook", {"contract_type": "service_agreement", "clause_type": "auto_renewal"},
     "Looked up the standard terms for automatic renewal in a service agreement"),
    ("search_playbook", {"contract_type": "nda", "clause_type": "non_compete"},
     "Looked up the standard terms for non-compete clauses in an NDA"),
    ("read_page", {"n": 3}, "Re-read page 3 of the contract"),
    ("search_law", {"query": "something odd", "jurisdiction": "my"}, "Checked Malaysian law on \u201csomething odd\u201d"),
])
def test_trace_summaries_are_plain_words_from_the_arguments(name, args, expected):
    assert tool_summary(name, args) == expected


def test_trace_in_agent_run_uses_generated_summaries_and_times(kb):
    llm = ScriptedLLM([[call("search_law", query="penalty liquidated damages", jurisdiction="my")]])
    _, trace = _run(llm, kb)
    assert trace.steps[0].summary == "Checked Malaysian law on penalty clauses"
    d = json.loads(trace.steps[0].detail)  # what the agent looked up and found, for "show your work"
    assert trace.steps[0].ms is not None and d["tool"] == "search_law" and d["args"]["jurisdiction"] == "my"
    assert d["results"][0]["ref"] == "my-contracts-act-s75" and d["results"][0]["kind"] == "law"
    assert d["results"][0]["title"] == "Contracts Act 1950, s.75" and d["results"][0]["score"] > 0
    assert d["cited"] == []  # filled in after citation verification
    assert trace.steps[-1].action == "draft_findings"


# ---- company knowledge base, over HTTP ------------------------------------------------------------------

POLICY = [
    "COMPANY CONTRACT POLICY\n\n"
    "1. Vendor liability must never be capped below 12 months of fees paid.\n"
    "2. We do not accept automatic renewal with more than 30 days notice.\n",
]


def test_company_upload_list_search_delete(client, services):
    assert client.get("/api/company/docs").json() == []
    r = client.post("/api/company/docs", files={"file": ("Contract Policy.pdf", make_pdf(POLICY), "application/pdf")})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["filename"] == "Contract Policy.pdf" and doc["title"] == "Contract Policy" and doc["pages"] == 1
    assert CompanyDoc.model_validate(doc)
    assert [d["id"] for d in client.get("/api/company/docs").json()] == [doc["id"]]
    assert services.blobs.get(f"company/{doc['id']}.pdf") is not None

    tools = Toolbox(services.kb, services.company, services.embedder, PAGES, owner=personal_ws_id("alice"))
    out = tools.run(call("search_company", query="vendor liability capped below 12 months of fees"))
    body = json.loads(out.payload)
    assert body["results"][0]["ref"] == f"company:{doc['id']}" and body["results"][0]["page"] == 1
    assert "12 months" in body["results"][0]["excerpt"]
    assert out.sources[0].kind == "company" and out.sources[0].title == "Contract Policy"

    assert client.delete(f"/api/company/docs/{doc['id']}").status_code == 204
    assert client.get("/api/company/docs").json() == []
    assert services.blobs.get(f"company/{doc['id']}.pdf") is None
    after = json.loads(tools.run(call("search_company", query="liability")).payload)
    assert after["results"] == [] and "no company documents" in after["note"]
    assert client.delete(f"/api/company/docs/{doc['id']}").status_code == 404
    assert client.delete("/api/company/docs/..%2Fx").status_code in (404, 405)


def test_company_upload_validation_and_rate_limit(client):
    r = client.post("/api/company/docs", files={"file": ("x.docx", b"PK\x03\x04", "application/pdf")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "not_a_pdf"
    from pdfgen import make_blank_pdf

    assert client.post("/api/company/docs", files={"file": ("big.pdf", make_blank_pdf(41), "application/pdf")}
                       ).status_code == 422
    h = {"x-forwarded-for": "198.51.100.77"}
    for _ in range(10):
        assert client.post("/api/company/docs", headers=h,
                           files={"file": ("p.pdf", make_pdf(POLICY), "application/pdf")}).status_code == 200
    assert client.post("/api/company/docs", headers=h,
                       files={"file": ("p.pdf", make_pdf(POLICY), "application/pdf")}).status_code == 429


def test_review_cites_company_policy_and_keeps_policy_breach(services, client, pdf_bytes):
    """A scripted agent finds the company rule; the final answer cites it; the breach survives."""
    doc = client.post("/api/company/docs",
                      files={"file": ("policy.pdf", make_pdf(POLICY), "application/pdf")}).json()
    llm = ScriptedLLM([[call("search_company", query="vendor liability cap months of fees")]])
    final = LLMReview.model_validate_json((llm.fixtures_dir / "review.json").read_text())
    final.findings[2].citations = [LLMCitation(kind="company", ref=f"company:{doc['id']}", excerpt="made up")]
    final.findings[2].policy_breach = "Our policy requires at least 12 months of fees."
    llm.generate = lambda **kw: (final, agent_mod.LLMUsage(llm.model, 10, 5))
    services.llm = llm

    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    f = next(f for f in body["findings"] if f["policy_breach"])
    assert f["policy_breach"] == "Our policy requires at least 12 months of fees."
    c = f["citations"][0]
    assert c["kind"] == "company" and c["ref"] == f"company:{doc['id']}" and c["title"] == "policy"
    assert "12 months" in c["excerpt"] and c["page"] == 1
    assert body["trace"][1]["summary"] == "Checked your company's rules on liability caps"
    # the other findings, whose citations were only in the fixture, lose a breach they could not back
    assert sum(1 for f in body["findings"] if f["policy_breach"]) == 1


# ---- negotiation mentions a citation title ----------------------------------------------------------------


def test_negotiation_prompt_carries_law_title_as_context(client, services, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    client.post(f"/api/reviews/{body['id']}/negotiation")
    call_ = services.llm.calls[-1]
    assert call_["purpose"] == "negotiation"
    assert "context: Contracts Act 1950, s.75" in call_["parts"][0]
    assert "never present it as legal advice" in call_["system"].lower().replace("\n", " ")


# ---- Gemini retry hardening ---------------------------------------------------------------------------------


def test_retry_options():
    from app.adapters.gcp import _retry_options

    o = _retry_options()
    r = o.retry_options
    assert o.timeout == 35_000  # a stalled generate call is abandoned and retried
    assert (r.attempts, r.initial_delay, r.max_delay, r.exp_base) == (3, 2.0, 20.0, 2.0)
    assert sorted(r.http_status_codes) == [429, 500, 503, 504]
    assert _retry_options(5, 30_000).retry_options.attempts == 5


def test_final_failure_logs_status_but_user_message_stays_plain(caplog):
    from app.adapters.gcp import _upstream_failure

    err = SimpleNamespace(code=503, message="The model is overloaded")
    with caplog.at_level("WARNING", logger="cc.gcp"):
        e = _upstream_failure("gemini review call", err, "The AI service did not respond. Please try again in a minute.")  # type: ignore[arg-type]
    assert e.status == 503 and "503" not in str(e)
    assert any("status=503" in r.message and "overloaded" in r.message and r.levelname == "WARNING"
               for r in caplog.records)
