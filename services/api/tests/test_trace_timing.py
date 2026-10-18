"""Observed timing, concurrency and legacy compatibility without model/network calls."""

import json
import threading

import pytest

from app import agent as agent_mod, pipeline
from app.agent import Toolbox, Trace, fill_cited, run_agent
from app.models import TraceStep, ReviewResult
from app.ports import UpstreamError
from test_agent import ScriptedLLM, call
from test_schema import assert_valid, RECORDED
from pdfgen import PAGES


class Clock:
    """A thread-safe monotonic clock; barriers, rather than wall-clock sleeps, prove overlap."""
    def __init__(self):
        self.value = 100.0
        self.lock = threading.Lock()
        self.local = threading.local()
        self.observed = []

    def now(self):
        with self.lock:
            self.value += .01
            self.observed.append(self.value)
            self.local.last = self.value
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(agent_mod.time, "monotonic", clock.now)
    return clock


def tools_for(services):
    return Toolbox(services.kb, services.company, services.embedder, PAGES, owner="demo")


def test_parallel_tools_have_observed_overlapping_offsets_and_keep_source_detail(services, clock):
    trace = Trace()
    tools = tools_for(services)
    barrier = threading.Barrier(2)
    actual_starts = {}
    original = tools.run

    def concurrent(call):
        # Last timestamp in this worker is _timed's start, before execution reaches the barrier.
        actual_starts[call.name] = clock.local.last
        barrier.wait(timeout=5)
        clock.advance(.02)
        return original(call)

    tools.run = concurrent
    llm = ScriptedLLM([[call("search_law", query="penalty", jurisdiction="my"),
                        call("search_playbook", contract_type="service_agreement", clause_type="liability")]])
    outcome = run_agent(llm, tools, PAGES, trace)
    plan, law, playbook, draft = trace.steps
    assert [s.action for s in trace.steps] == ["plan_research", "search_law", "search_playbook", "draft_findings"]
    assert outcome.turns == 1 and draft.turn == 2
    for tool in (law, playbook):
        assert tool.start_ms == int((actual_starts[tool.action] - trace.origin) * 1000)
        assert tool.turn == tool.batch == 1 and tool.ms > 0
        assert tool.start_ms >= plan.start_ms + plan.ms
    # Both workers started before either could finish (the barrier enforces actual concurrency).
    assert max(law.start_ms, playbook.start_ms) < min(law.start_ms + law.ms, playbook.start_ms + playbook.ms)
    assert law.start_ms != playbook.start_ms  # independent observed offsets, not one invented batch start
    assert draft.start_ms >= max(s.start_ms + s.ms for s in (law, playbook))
    assert plan.batch is None and draft.batch is None
    refs = set(outcome.known)
    assert refs == {"my-contracts-act-s75", "service_agreement/liability"}
    fill_cited(trace.steps, {"my-contracts-act-s75"})
    for step in (law, playbook):
        detail = json.loads(step.detail)
        assert detail["results"][0]["ref"] in refs
        assert detail["cited"] == (["my-contracts-act-s75"] if step is law else [])
        assert_valid("TraceStep", step.model_dump(mode="json"))


@pytest.mark.parametrize("turns,cap,expected", [([], 2, 1),
    ([[call("search_company", query="liability")]], 2, 1),
    ([[call("read_page", n=1)], [call("read_page", n=2)]], 2, 2),
    ([], 0, 0)])
def test_actual_planning_call_count_and_final_turn(services, clock, turns, cap, expected):
    llm = ScriptedLLM(turns)
    original_chat, original_generate = llm.chat, llm.generate

    def chat(**kwargs):
        clock.advance(.025)
        return original_chat(**kwargs)

    def generate(**kwargs):
        clock.advance(.04)
        return original_generate(**kwargs)

    llm.chat, llm.generate = chat, generate
    trace = Trace()
    outcome = run_agent(llm, tools_for(services), PAGES, trace, max_turns=cap)
    plans = [s for s in trace.steps if s.action == "plan_research"]
    assert len(plans) == outcome.turns == expected
    assert [s.turn for s in plans] == list(range(1, expected + 1))
    assert all(s.ms >= 25 for s in plans)
    assert trace.steps[-1].turn == expected + 1 and trace.steps[-1].ms >= 40
    for previous, current in zip(plans, plans[1:]):
        assert current.start_ms >= previous.start_ms + previous.ms
    for step in trace.steps:
        assert step.start_ms >= 0 and step.ms > 0
    tools = [s for s in trace.steps if s.batch is not None]
    assert [s.batch for s in tools] == list(range(1, len(tools) + 1))


def test_failed_follow_up_is_an_observed_planning_attempt(services, clock):
    llm = ScriptedLLM([[call("read_page", n=1)]])
    original = llm.chat
    calls = 0

    def chat(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            clock.advance(.03)
            raise UpstreamError("unavailable")
        return original(**kwargs)

    llm.chat = chat
    trace = Trace()
    outcome = run_agent(llm, tools_for(services), PAGES, trace)
    plans = [s for s in trace.steps if s.action == "plan_research"]
    assert outcome.turns == calls == len(plans) == 2
    assert plans[-1].detail == "Planning call failed" and plans[-1].ms >= 30
    assert trace.steps[-1].turn == 3
    assert trace.steps[-1].start_ms >= plans[-1].start_ms + plans[-1].ms


def test_pipeline_read_and_verification_share_the_review_origin(services, pdf_bytes, clock, monkeypatch):
    instances = []

    class ObservedTrace(Trace):
        def __init__(self, origin=None):
            super().__init__(origin)
            instances.append(self)

    monkeypatch.setattr(pipeline, "Trace", ObservedTrace)
    result = pipeline.review(services, pdf_bytes, "contract.pdf")
    trace = instances[0]
    assert trace.origin == clock.observed[0]  # review entry, not a later agent-specific clock reset
    assert result.trace[0].action == "read_contract" and result.trace[-1].action == "verify"
    assert result.trace[0].start_ms > 0
    assert result.trace[0].turn is None and result.trace[-1].turn is None
    for previous, current in zip(result.trace, result.trace[1:]):
        if current.batch is None:  # tool spans may overlap within their batch
            assert current.start_ms >= previous.start_ms + previous.ms
    assert all(s.ms > 0 and s.start_ms >= 0 for s in result.trace)
    assert_valid("ReviewResult", result.model_dump(mode="json"))


def test_old_traces_deserialize_without_fabricating_offsets_or_turns():
    for filename in ("review_result.json", "review_result_scanned.json"):
        raw = json.loads((RECORDED / filename).read_text())
        # These recordings predate traces entirely; both absence and old nonempty traces must load.
        assert ReviewResult.model_validate(raw).trace == []
        raw["trace"] = [{"step": 1, "action": "read_contract", "summary": "Read the contract", "ms": 9},
                        {"step": 2, "action": "verify", "summary": "Checked the quotes"}]
        result = ReviewResult.model_validate(raw)
        assert len(result.trace) == 2 and result.trace[0].ms == 9 and result.trace[1].ms is None
        for step in result.trace:
            assert step.start_ms is step.turn is step.batch is None
        assert_valid("ReviewResult", result.model_dump(mode="json"))
    trace = Trace()
    trace.add("read_contract", "Legacy direct caller", ms=5)
    assert trace.steps[0].start_ms is trace.steps[0].turn is trace.steps[0].batch is None
    assert TraceStep.model_validate({"step": 1, "action": "verify", "summary": "Checked"}).ms is None
