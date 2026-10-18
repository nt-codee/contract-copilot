"""Model fallback: a stalled agent or review call is retried on LLM_FALLBACK_MODEL, through a fake client."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from google.genai import types
from pydantic import BaseModel

from app.adapters import gcp
from app.adapters.gcp import FOREIGN_SIGNATURE, GeminiLLM, call_attempts
from app.ports import ChatMessage, LLMUsage, ToolSpec, UpstreamError

PRIMARY, FALLBACK = "gemini-3.8-flash", "gemini-3.5-flash"


class Out(BaseModel):
    a: int = 1


class Boom(Exception):
    def __init__(self, code: int):
        super().__init__(f"{code} upstream")
        self.code = code


def _resp():
    return SimpleNamespace(
        text='{"a": 1}', parsed=None,
        usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=50),
        candidates=[SimpleNamespace(finish_reason=None, content=types.Content(
            role="model", parts=[types.Part.from_text(text="ok")]))])


class _Models:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    def generate_content(self, *, model, contents, config):
        self.seen.append((model, contents, config))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _resp()


def make_llm(*script, fallback=FALLBACK) -> GeminiLLM:
    llm = GeminiLLM.__new__(GeminiLLM)
    llm.model, llm.fallback_model, llm._thinking, llm.calls, llm._record_dir = PRIMARY, fallback, "", 0, None
    llm._client = SimpleNamespace(models=_Models(script))
    return llm


def _seen(llm):
    return [(m, c.http_options.retry_options.attempts, c.http_options.timeout) for m, _, c in llm._client.models.seen]


@pytest.mark.parametrize("failure", [Boom(504), Boom(499), Boom(429), httpx.ReadTimeout("timed out")])
def test_review_stall_is_answered_by_the_fallback_model(failure):
    llm = make_llm(failure, "ok")
    out, usage = llm.generate(system="s", parts=["a normal prompt"], schema=Out, purpose="review")
    assert out.a == 1
    # primary gets one try, the fallback the remaining one, each with the same per-try budget as before
    assert _seen(llm) == [(PRIMARY, 1, 75_000), (FALLBACK, call_attempts("review") - 1, 75_000)]
    assert (usage.model, usage.fallback_model, usage.fallback_purposes) == (PRIMARY, FALLBACK, ["review"])
    assert (usage.input_tokens, usage.fallback_input_tokens, usage.fallback_output_tokens) == (0, 1000, 150)


def test_agent_turn_falls_back_with_the_same_request_and_foreign_signatures():
    llm = make_llm(Boom(504), "ok")
    earlier = types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(name="search_law", args={"query": "x"}), thought_signature=b"primary-sig")])
    msgs = [ChatMessage(role="user", text="start"), ChatMessage(role="model", raw=earlier)]
    reply, usage = llm.chat(system="s", messages=msgs, tools=[ToolSpec("search_law", "d", {"type": "object"})],
                            purpose="agent")
    assert reply.text == "ok" and usage.fallback_purposes == ["agent"]
    assert _seen(llm) == [(PRIMARY, 1, 20_000), (FALLBACK, 2, 20_000)]
    (_, first, cfg1), (_, second, cfg2) = llm._client.models.seen
    assert first[1].parts[0].thought_signature == b"primary-sig"  # the primary gets its own signature
    assert second[1].parts[0].thought_signature == FOREIGN_SIGNATURE  # the fallback the documented sentinel
    assert second[1].parts[0].function_call.name == "search_law" and second[0] == first[0]
    assert cfg2.system_instruction == cfg1.system_instruction and cfg2.tools == cfg1.tools  # same request


def test_both_models_stall_gives_the_plain_upstream_error():
    llm = make_llm(Boom(504), Boom(504))
    with pytest.raises(UpstreamError) as e:
        llm.generate(system="s", parts=["p"], schema=Out, purpose="review")
    assert str(e.value) == "The AI service did not respond. Please try again in a minute." and e.value.status == 504
    assert [m for m, *_ in _seen(llm)] == [PRIMARY, FALLBACK]


def test_quota_on_the_fallback_still_gets_the_long_waits(monkeypatch):
    waits = []
    monkeypatch.setattr(gcp.time, "sleep", waits.append)
    llm = make_llm(Boom(429), Boom(429), "ok")
    llm.generate(system="s", parts=["p"], schema=Out, purpose="review")
    assert [m for m, *_ in _seen(llm)] == [PRIMARY, FALLBACK, FALLBACK] and len(waits) == 1


def test_other_failures_do_not_switch_models():
    llm = make_llm(Boom(503), "ok")  # a server error: the primary is retried, as before
    _, usage = llm.generate(system="s", parts=["p"], schema=Out, purpose="review")
    assert [m for m, *_ in _seen(llm)] == [PRIMARY, PRIMARY] and usage.fallback_model is None
    llm = make_llm(Boom(400))  # a refused request is not retried at all
    with pytest.raises(UpstreamError):
        llm.generate(system="s", parts=["p"], schema=Out, purpose="review")
    assert len(_seen(llm)) == 1


def test_only_agent_and_review_fall_back_and_it_can_be_turned_off():
    llm = make_llm("ok")
    llm.generate(system="s", parts=["p"], schema=Out, purpose="negotiation")
    assert _seen(llm) == [(PRIMARY, 3, 20_000)]  # unchanged: one call, the SDK's three tries
    llm = make_llm("ok", fallback="")
    llm.generate(system="s", parts=["p"], schema=Out, purpose="review")
    assert _seen(llm) == [(PRIMARY, 2, 75_000)]


def test_worst_case_tries_are_unchanged():
    for purpose in ("agent", "review"):
        llm = make_llm(Boom(504), Boom(504))
        with pytest.raises(UpstreamError):
            llm.generate(system="s", parts=["p"], schema=Out, purpose=purpose)
        assert sum(n for _, n, _ in _seen(llm)) == call_attempts(purpose)  # same tries, same per-try budget


def test_review_records_the_model_that_answered(client, services, monkeypatch, pdf_bytes):
    real = services.llm.generate

    def generate(**kw):
        out, u = real(**kw)
        if kw["purpose"] != "review":
            return out, u
        return out, LLMUsage(model=u.model, fallback_model=FALLBACK, fallback_input_tokens=u.input_tokens,
                             fallback_output_tokens=u.output_tokens, fallback_purposes=["review"])

    monkeypatch.setattr(services.llm, "generate", generate)
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    assert body["provenance"]["model"] == FALLBACK and body["metrics"]["model"] == FALLBACK
    assert f"answered by fallback model {FALLBACK} (review)" in body["trace"][0]["detail"]


def test_usage_add_keeps_the_fallback_apart():
    total = LLMUsage(model=PRIMARY)
    total.add(LLMUsage(model=PRIMARY, input_tokens=10, output_tokens=1))
    total.add(LLMUsage(model=PRIMARY, fallback_model=FALLBACK, fallback_input_tokens=20, fallback_output_tokens=2,
                       fallback_purposes=["review"]))
    assert (total.input_tokens, total.total_input, total.total_output) == (10, 30, 3)
    assert total.fallback_model == FALLBACK and total.fallback_purposes == ["review"]
