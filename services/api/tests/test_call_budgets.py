"""Per-purpose time budget and retry count of one Gemini call, checked through a fake client (no network)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.adapters import gcp
from app.adapters.gcp import (AGENT_TIMEOUT_MS, GENERATE_TIMEOUT_MS, LLM_ATTEMPTS, REVIEW_ATTEMPTS, SHORT_TIMEOUT_MS,
                              TRANSCRIBE_TIMEOUT_MS, GeminiLLM, call_attempts, call_timeout_ms)

# purpose -> (per-attempt timeout ms for a normal-size call, attempts)
EXPECTED = {
    "negotiation": (20_000, 3),
    "boxes": (20_000, 3),
    "agent": (20_000, 3),
    "review": (75_000, 2),
    "transcribe": (120_000, 3),
}


class Out(BaseModel):
    a: int = 1


class _Resp:
    text = '{"a": 1}'
    parsed = None
    usage_metadata = None
    candidates = []


class _Models:
    def __init__(self, seen):
        self.seen = seen

    def generate_content(self, *, model, contents, config):
        self.seen.append(config)
        return _Resp()


class _Client:
    def __init__(self, seen):
        self.models = _Models(seen)


def make_llm(seen):
    llm = GeminiLLM.__new__(GeminiLLM)
    llm.model, llm._thinking, llm.calls, llm._record_dir = "m", "", 0, None
    llm._client = _Client(seen)
    return llm


@pytest.mark.parametrize("purpose,want", EXPECTED.items())
def test_each_purpose_gets_its_own_budget_and_attempts(purpose, want):
    seen = []
    make_llm(seen).generate(system="s", parts=["a normal-size prompt"], schema=Out, purpose=purpose)
    opts = seen[0].http_options
    assert (opts.timeout, opts.retry_options.attempts) == want, purpose


def test_the_short_calls_no_longer_share_the_review_budget():
    for purpose in ("negotiation", "boxes"):
        assert call_timeout_ms(purpose, 0) == SHORT_TIMEOUT_MS == 20_000
        assert call_timeout_ms(purpose, 400_000) == SHORT_TIMEOUT_MS  # not scaled with input: they stay short
        assert call_attempts(purpose) == LLM_ATTEMPTS == 3
    assert (call_timeout_ms("review", 0), call_attempts("review")) == (GENERATE_TIMEOUT_MS, REVIEW_ATTEMPTS) == (75_000, 2)
    assert (call_timeout_ms("agent", 0), call_attempts("agent")) == (AGENT_TIMEOUT_MS, LLM_ATTEMPTS)
    assert call_timeout_ms("transcribe", 0) == TRANSCRIBE_TIMEOUT_MS


def test_worst_case_of_a_stalled_short_call_is_about_a_minute_not_three():
    # 3 x 20 s plus the SDK's back-off (2 s, 4 s), against 2 x 75 s for a review and the 80 s reply seen in production
    assert 3 * SHORT_TIMEOUT_MS + 6_000 <= 66_000 < 80_000


def test_every_purpose_the_pipeline_uses_is_covered():
    import re
    from pathlib import Path

    src = "".join(p.read_text() for p in Path(gcp.__file__).parents[1].glob("*.py"))
    used = set(re.findall(r'purpose="(\w+)"', src))
    assert used <= set(EXPECTED), f"a new purpose needs a deliberate budget: {used - set(EXPECTED)}"
