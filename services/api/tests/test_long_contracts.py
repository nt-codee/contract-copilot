"""Long contracts get a longer time budget per call (msa-10, 15 pages, was cancelled at 20 s every time)."""

from __future__ import annotations

import pytest

from app.adapters import gcp
from app.adapters.gcp import (AGENT_TIMEOUT_MS, CHARS_PER_TOKEN, GENERATE_TIMEOUT_MS, MAX_TIMEOUT_MS, RETRY_STATUSES,
                              REVIEW_ATTEMPTS, TRANSCRIBE_TIMEOUT_MS, GeminiLLM, _chars, call_timeout_ms)
from app.ports import ChatMessage


def chars_for(tokens: int) -> int:
    return int(tokens * CHARS_PER_TOKEN)


def test_a_normal_contract_keeps_the_short_budget():
    assert call_timeout_ms("agent", chars_for(3_500)) == AGENT_TIMEOUT_MS == 20_000  # a stall is still cut off fast
    assert call_timeout_ms("review", chars_for(5_000)) == GENERATE_TIMEOUT_MS
    assert call_timeout_ms("agent", 0) == AGENT_TIMEOUT_MS


def test_the_planning_turn_of_a_15_page_contract_gets_more_than_20_seconds():
    """msa-10: 10,969 input tokens in the agent turn; the fixed 20 s budget was cancelled on every try."""
    t = call_timeout_ms("agent", chars_for(10_969))
    assert t > 20_000 and 35_000 <= t <= 45_000, t
    assert call_timeout_ms("review", chars_for(18_487)) > GENERATE_TIMEOUT_MS  # the drafting call: 18,487 tokens in


def test_budget_grows_with_input_and_is_capped():
    sizes = [chars_for(n) for n in (2_000, 6_000, 10_000, 20_000, 60_000, 400_000)]
    for purpose in ("agent", "review"):
        budgets = [call_timeout_ms(purpose, s) for s in sizes]
        assert budgets == sorted(budgets)
        assert budgets[-1] == MAX_TIMEOUT_MS[purpose]  # never unbounded
    # a stalled call on the largest input still ends in a bounded time: 3 tries
    assert 3 * MAX_TIMEOUT_MS["agent"] <= 180_000 and REVIEW_ATTEMPTS * MAX_TIMEOUT_MS["review"] <= 240_000


def test_transcription_budget_is_fixed():
    assert call_timeout_ms("transcribe", 0) == call_timeout_ms("transcribe", chars_for(50_000)) == TRANSCRIBE_TIMEOUT_MS


def test_499_is_retried_like_a_timeout():
    assert 499 in RETRY_STATUSES  # "The operation was cancelled" is the server answering our own deadline


def test_the_config_carries_the_scaled_timeout():
    llm = GeminiLLM.__new__(GeminiLLM)
    llm._thinking = ""
    short = llm._config("sys", "agent", chars_for(3_000))
    long = llm._config("sys", "agent", chars_for(11_000))
    assert short.http_options.timeout == 20_000 and 35_000 <= long.http_options.timeout <= 45_000
    assert llm._config("sys", "review", chars_for(18_000)).http_options.timeout > GENERATE_TIMEOUT_MS
    assert llm._config("sys", "transcribe", 0).http_options.timeout == 120_000
    assert long.http_options.retry_options.attempts == 3  # planning turns: three short tries
    review = llm._config("sys", "review", chars_for(10_000))
    assert review.http_options.retry_options.attempts == REVIEW_ATTEMPTS == 2  # review: one long try, then one more
    assert review.http_options.timeout >= 75_000  # was 35 s x 3, which Vertex answered with 504 three times


def test_size_counts_the_system_prompt_and_every_part():
    from google.genai import types

    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="a" * 100)]),
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="x", args={"q": "b" * 50}))]),
        types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name="x", response={"r": "c" * 70}))]),
        types.Part.from_text(text="d" * 30),  # generate() passes bare parts
    ]
    n = _chars("s" * 1000, contents)
    assert n >= 1000 + 100 + 30 + 50 + 70 and n < 1000 + 100 + 30 + 50 + 70 + 100


def test_chat_and_generate_pass_the_size_to_the_config(monkeypatch):
    """The real chat() path: a long conversation produces a long budget, a short one does not."""
    seen = []
    llm = GeminiLLM.__new__(GeminiLLM)
    llm._thinking, llm.model, llm.calls = "", "m", 0

    def fake_call(purpose, contents, config):
        seen.append(config.http_options.timeout)
        raise RuntimeError("stop here")

    monkeypatch.setattr(llm, "_call", fake_call)
    for text in ("short contract", "x" * chars_for(11_000)):
        with pytest.raises(RuntimeError):
            llm.chat(system=text, messages=[ChatMessage(role="user", text="go")], tools=[], purpose="agent")
    assert seen[0] == 20_000 and seen[1] > 35_000


# ---- long contracts: the planning turn reads an outline, the review reads everything ------------------------------

from app import prompts  # noqa: E402
from app.adapters.local import HashEmbedder, MemoryCompanyStore  # noqa: E402
from app.agent import Toolbox, Trace, run_agent  # noqa: E402
from app.config import REPO_ROOT  # noqa: E402
from app.knowledge import Knowledge  # noqa: E402
from pdfgen import PAGES, make_pdf  # noqa: E402


def long_pages(n_pages: int = 9) -> list[str]:
    """~3,000 characters a page: numbered clauses under headings, governing law on the last page."""
    pages = []
    for p in range(n_pages):
        lines = [f"{p + 1}. HEADING NUMBER {p + 1}"] + [
            f"{p + 1}.{i} The Supplier shall perform obligation {p}-{i} in accordance with the schedule and "
            f"shall notify the Customer of any delay within five working days of becoming aware of it." for i in range(1, 17)]
        pages.append("\n".join(lines))
    pages[-1] += ("\n99.1 This Agreement is governed by the laws of the State of Delaware, and the parties submit to the "
                  "exclusive jurisdiction of its courts.")
    return pages


def test_a_short_contract_is_sent_whole_to_the_planning_turn():
    assert prompts.contract_outline(PAGES) is None
    assert prompts.agent_system(PAGES, 16, {}, outline=True) == prompts.agent_system(PAGES, 16, {})


def test_a_long_contract_gets_an_outline_a_third_the_size():
    pages = long_pages()
    assert sum(len(p) for p in pages) > prompts.OUTLINE_THRESHOLD_CHARS
    outline = prompts.contract_outline(pages)
    assert len(outline) < 0.45 * len(prompts.contract_text(pages))
    for n in range(1, len(pages) + 1):
        assert f"[PAGE {n}]" in outline  # page tags kept, so read_page(n) still points at the right page
    assert all(f"{p}. HEADING NUMBER {p}" in outline for p in range(1, 10))  # every heading
    assert "governed by the laws of the State of Delaware" in outline  # the governing-law paragraph in full
    assert "OUTLINE" in outline and "read_page" in outline


def test_planning_reads_the_outline_and_the_review_reads_the_whole_contract():
    from test_agent import ScriptedLLM, call

    kb = Knowledge.load(REPO_ROOT / "knowledge")
    pages = long_pages()
    llm = ScriptedLLM([[call("set_jurisdiction", governing_law="Delaware", jurisdiction="other")]])
    outcome = run_agent(llm, Toolbox(kb, MemoryCompanyStore(), HashEmbedder(), pages, owner="demo"), pages, Trace())
    plan, review = llm.calls[0], llm.calls[-1]
    assert plan["purpose"] == "agent" and "OUTLINE" in plan["system"]
    assert plan["system"].count("within five working days") < 10  # only page 1 is in full; clause bodies are cut
    assert review["purpose"] == "review" and "OUTLINE" not in review["system"]
    assert review["system"].count("within five working days") >= 100  # the review sees every clause in full
    assert len(plan["system"]) < 0.6 * len(review["system"])
    assert outcome.outlined is True


def test_the_trace_says_when_a_long_contract_was_planned_from_an_outline(client):
    long = make_pdf(long_pages())
    body = client.post("/api/reviews", files={"file": ("long.pdf", long, "application/pdf")}).json()
    assert "planned from an outline, reviewed in full" in body["trace"][0]["detail"]
    short = client.post("/api/reviews", files={"file": ("s.pdf", make_pdf(), "application/pdf")}).json()
    assert "outline" not in (short["trace"][0]["detail"] or "")
