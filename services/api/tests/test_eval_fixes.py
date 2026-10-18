"""Fixes prototyped from the 25-sample live eval: every playbook reachable, red flags always visible,
severity and clause-type rules in the prompt, 429 back-off."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from app import agent as agent_mod
from app import prompts
from app.adapters.local import HashEmbedder, MemoryCompanyStore
from app.agent import CLAUSE_TYPES, PLAYBOOK_CONTRACT_TYPES, TOOLS, Toolbox, clause_vocabulary, tool_summary
from app.config import REPO_ROOT
from app.knowledge import PLAYBOOK_VIEW_CHARS, Knowledge, playbook_view, sections, _section
from app.models import ClauseType, ContractType
from app.ports import ToolCall
from pdfgen import PAGES

KB = Knowledge.load(REPO_ROOT / "knowledge")
REFS = sorted(KB.playbook)


def test_search_playbook_offers_every_contract_and_clause_type():
    spec = next(t for t in TOOLS if t.name == "search_playbook").parameters["properties"]
    assert spec["contract_type"]["enum"] == [t for t in get_args(ContractType) if t != "other"]
    assert set(PLAYBOOK_CONTRACT_TYPES) == {"nda", "service_agreement", "employment", "tenancy", "hire_purchase"}
    assert spec["clause_type"]["enum"] == list(get_args(ClauseType)) == CLAUSE_TYPES
    assert {"probation", "salary_deductions", "interest_rate", "insurance", "fees_charges", "deposit"} <= set(CLAUSE_TYPES)


def test_every_playbook_file_is_reachable_through_the_tool():
    """The live eval never reached employment, tenancy or hire purchase: the tool's enum stopped at two types."""
    tools = Toolbox(KB, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    spec = next(t for t in TOOLS if t.name == "search_playbook").parameters["properties"]
    for ref in REFS:
        ctype, clause = ref.split("/", 1)
        assert ctype in spec["contract_type"]["enum"] and clause in spec["clause_type"]["enum"], ref
        out = tools.run(ToolCall("search_playbook", {"contract_type": ctype, "clause_type": clause}))
        assert json.loads(out.payload)["results"][0]["ref"] == ref


@pytest.mark.parametrize("ref", REFS)
def test_red_flags_are_always_in_what_the_agent_reads(ref):
    """Before: the first 1,200 characters of the file, which cut the red flags off in 26 of 53 files."""
    e = KB.playbook[ref]
    flags = " ".join(_section(sections(e.text), "red flags").split())
    view = playbook_view(e)
    assert flags and flags in view, ref  # whole section, every High / Medium / Low line
    assert len(view) <= PLAYBOOK_VIEW_CHARS < agent_mod.MAX_RESULT_CHARS
    tools = Toolbox(KB, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    ctype, clause = ref.split("/", 1)
    payload = tools.run(ToolCall("search_playbook", {"contract_type": ctype, "clause_type": clause})).payload
    assert len(payload) <= agent_mod.MAX_RESULT_CHARS
    assert flags in json.loads(payload)["results"][0]["excerpt"]  # not clipped by the tool-result cap either


def test_the_three_severity_lines_that_the_eval_got_wrong_are_visible():
    """msa-04 rated a 3-month cap High and a 7-day vendor exit High; nda-04 rated one-way confidentiality High."""
    assert "3-6 months" in playbook_view(KB.playbook["service_agreement/liability"])
    assert "7 days" in playbook_view(KB.playbook["service_agreement/termination"])
    assert "one-way" in playbook_view(KB.playbook["nda/confidentiality"])
    assert "flat rate" in playbook_view(KB.playbook["hire_purchase/interest_rate"])


def test_prompt_defines_all_contract_types_and_the_rules():
    vocab = clause_vocabulary(KB)
    assert set(vocab) == set(PLAYBOOK_CONTRACT_TYPES) and "probation" in vocab["employment"]
    text = prompts.agent_system(PAGES, 16, vocab)
    for t in ("nda", "service_agreement", "employment", "tenancy", "hire_purchase"):
        assert f'- "{t}":' in text and f"- {t}: " in text  # defined, and its clause types listed
    assert "freelance" in text and "personal loans" in text  # the routing the answer keys expect
    assert "assign\n   exactly that level" in text  # severity comes from the playbook's red-flag tag
    assert "an early termination fee belongs under" in text.replace("\n   ", " ")
    heading = "CLAUSE TYPES (the playbook has one file"
    assert heading in text and heading not in prompts.agent_system(PAGES, 16)  # the list is added with the vocabulary


def test_run_agent_puts_the_vocabulary_in_the_system_prompt(kb_fixture=None):
    from test_agent import ScriptedLLM, call
    from app.agent import Trace, run_agent

    llm = ScriptedLLM([[call("search_playbook", contract_type="employment", clause_type="probation")]])
    tools = Toolbox(KB, MemoryCompanyStore(), HashEmbedder(), PAGES, owner="demo")
    outcome = run_agent(llm, tools, PAGES, Trace())
    system = llm.calls[0]["system"]
    assert "- hire_purchase: " in system and "interest_rate" in system
    assert "employment/probation" in outcome.known


def test_trace_summaries_for_the_new_contract_types():
    assert tool_summary("search_playbook", {"contract_type": "employment", "clause_type": "probation"}) == \
        "Looked up the standard terms for probation in an employment or freelance contract"
    assert tool_summary("search_playbook", {"contract_type": "hire_purchase", "clause_type": "fees_charges"}) == \
        "Looked up the standard terms for fees and charges in a hire purchase or loan agreement"
    assert tool_summary("search_playbook", {"contract_type": "tenancy", "clause_type": "deposit"}).endswith("in a tenancy")


# ---- 429 back-off ----------------------------------------------------------------------------------------


def test_quota_errors_wait_and_retry_but_other_errors_do_not(monkeypatch):
    from google.genai import errors

    from app.adapters import gcp
    from app.ports import UpstreamError

    llm = gcp.GeminiLLM.__new__(gcp.GeminiLLM)
    llm.model, llm.calls = "m", 0
    sleeps, attempts = [], []
    monkeypatch.setattr(gcp.time, "sleep", lambda s: sleeps.append(s))

    class Models:
        def __init__(self, fail_times, code):
            self.fail_times, self.code = fail_times, code

        def generate_content(self, **kw):
            attempts.append(1)
            if len(attempts) <= self.fail_times:
                raise errors.ClientError(self.code, {"error": {"code": self.code, "message": "Resource exhausted"}})
            return type("R", (), {"usage_metadata": None, "text": "{}"})()

    class Client:
        def __init__(self, models):
            self.models = models

    llm._client = Client(Models(2, 429))
    assert llm._call("review", [], None) is not None
    assert len(attempts) == 3 and len(sleeps) == 2 and all(s >= gcp.QUOTA_BACKOFF_S[0] for s in sleeps)

    attempts.clear(), sleeps.clear()
    llm._client = Client(Models(99, 429))
    with pytest.raises(UpstreamError) as e:
        llm._call("review", [], None)
    assert e.value.status == 429 and len(attempts) == 1 + len(gcp.QUOTA_BACKOFF_S) and len(sleeps) == len(gcp.QUOTA_BACKOFF_S)

    attempts.clear(), sleeps.clear()
    llm._client = Client(Models(99, 400))  # a bad request is not retried
    with pytest.raises(UpstreamError):
        llm._call("review", [], None)
    assert len(attempts) == 1 and sleeps == []
