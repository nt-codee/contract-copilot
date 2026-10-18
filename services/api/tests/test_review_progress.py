"""Real execution stages and opt-in SSE, using only local fake adapters."""
from __future__ import annotations

import asyncio
import codecs
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import json
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app import main, pipeline, progress
from app.agent import Toolbox
from app.main import create_app, stream_error
from app.models import ReviewResult
from app.ports import UpstreamError
from conftest import ALICE, bearer
from pdfgen import make_pdf
from test_agent import ScriptedLLM, call

SSE = {"Accept": "text/event-stream"}
SAMPLE = "/api/reviews/sample/demo-01"


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    rows = []
    for frame in response.text.split("\n\n"):
        if not frame or frame.startswith(":"):
            continue
        name, data = frame.split("\n", 1)
        assert name.startswith("event: ") and data.startswith("data: ")
        rows.append((name[7:], json.loads(data[6:])))
    return rows


def upload(client, **kwargs):
    return client.post("/api/reviews", files={"file": ("珈琲 ☕.pdf", make_pdf(), "application/pdf")}, **kwargs)


def recorded():
    return ReviewResult.model_validate_json((Path(__file__).parent / "fixtures/recorded/review_result.json").read_text())


def test_stages_are_emitted_at_execution_entry_not_on_timers(services, pdf_bytes, monkeypatch):
    stages = []
    services.llm = ScriptedLLM([[call("read_page", n=1)], [call("read_page", n=2)]])
    original_chat, original_generate = services.llm.chat, services.llm.generate

    def chat(**kwargs):
        assert stages[-1] == "planning"
        return original_chat(**kwargs)

    def generate(**kwargs):
        assert stages[-1] == "drafting"
        return original_generate(**kwargs)

    services.llm.chat, services.llm.generate = chat, generate
    for target, name, expected in ((pipeline, "extract_pages", "reading"),
                                    (Toolbox, "run", "researching"),
                                    (pipeline, "verify_findings", "verifying"),
                                    (pipeline, "locate_company_boxes", "locating")):
        original = getattr(target, name)

        def checked(*args, _original=original, _expected=expected, **kwargs):
            assert stages[-1] == _expected
            return _original(*args, **kwargs)

        monkeypatch.setattr(target, name, checked)
    result = pipeline.review(services, pdf_bytes, "a.pdf", progress=stages.append)
    assert stages == ["reading", "planning", "researching", "planning", "researching", "drafting", "verifying", "locating"]
    assert result.trace and [s.turn for s in result.trace if s.action == "plan_research"] == [1, 2]


def test_no_research_or_second_plan_is_reported_when_no_tools_requested(services, pdf_bytes):
    services.llm = ScriptedLLM([[]])
    stages = []
    pipeline.review(services, pdf_bytes, "a.pdf", progress=stages.append)
    assert stages == ["reading", "planning", "drafting", "verifying", "locating"]


def test_upload_stream_has_actual_progress_then_result_and_same_audit(client, services):
    rows = events(upload(client, headers=SSE))
    stages = [data["stage"] for name, data in rows if name == "progress"]
    assert stages == ["reading", "planning", "researching", "planning", "drafting", "verifying", "locating"]
    assert all(set(data) == {"stage"} for name, data in rows if name == "progress")
    name, final = rows[-1]
    assert name == "result" and final["source"] == "fresh"
    result = ReviewResult.model_validate(final["review"])
    assert result.filename == "珈琲 ☕.pdf"
    assert client.get(f"/api/reviews/{result.id}").status_code == 200
    actions = [event.action for event in services.audit.everyone(100)]
    assert actions.count("uploaded_contract") == actions.count("reviewed") == 1


def test_sample_stream_cache_copy_and_guest_compatibility(admin, anon, client, services):
    rows = events(admin.post(SAMPLE, headers={**SSE, "X-Workspace-Id": "demo"}))
    assert rows[-1][1]["source"] == "fresh"
    shared = rows[-1][1]["review"]
    count = len(services.llm.calls)
    for headers in (SSE, {**SSE, "X-Guest": "demo"}):
        response = anon.post(SAMPLE, headers=headers)
        assert response.headers["content-type"] == "application/json"
        assert response.headers["X-Review-Source"] == "cached" and response.json()["id"] == shared["id"]
    copied = client.post(SAMPLE, headers=SSE)
    assert copied.headers["X-Review-Source"] == "copied" and copied.json()["id"] != shared["id"]
    assert client.post(SAMPLE, headers=SSE).headers["X-Review-Source"] == "cached"
    assert len(services.llm.calls) == count
    refreshed = events(client.post(SAMPLE, headers=SSE, params={"refresh": True}))
    assert refreshed[-1][1]["source"] == "fresh"


@pytest.mark.parametrize("path", ["/api/reviews", SAMPLE])
@pytest.mark.parametrize("headers,status", [({}, 401), ({"X-Guest": "demo"}, 403),
                                             ({"Authorization": "Bearer invalid"}, 401)])
def test_stream_auth_refused_before_any_pipeline_start(api, services, monkeypatch, path, headers, status):
    review = Mock(side_effect=AssertionError("auth must precede review start"))
    monkeypatch.setattr(pipeline, "review", review)
    response = TestClient(api).post(path, headers={**SSE, **headers},
                                    files={"file": ("a.pdf", make_pdf(), "application/pdf")} if path.endswith("reviews") else None)
    assert response.status_code == status and response.headers["content-type"] == "application/json"
    assert "error" in response.json()
    review.assert_not_called()
    assert services.llm.calls == [] and services.audit.everyone(100) == []


@pytest.mark.parametrize("path", ["/api/reviews", SAMPLE])
@pytest.mark.parametrize("limit,status,code", [("spend", 503, "daily_limit"), ("rate", 429, "rate_limited")])
def test_stream_limits_are_refused_before_reading_or_model_work(services, monkeypatch, path, limit, status, code):
    settings = dataclasses.replace(services.settings, **({"max_model_reviews_per_day": 0} if limit == "spend"
                                                        else {"rate_limit_per_hour": 0}))
    client = TestClient(create_app(settings, services), headers=bearer(ALICE))
    review = Mock(side_effect=AssertionError("limits must precede review start"))
    monkeypatch.setattr(pipeline, "review", review)
    response = upload(client, headers=SSE) if path.endswith("reviews") else client.post(path, headers=SSE)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert response.headers["content-type"] == "application/json"
    review.assert_not_called()
    assert services.llm.calls == []


def test_stream_invalid_pdf_or_options_never_starts_model(client, services):
    assert client.post("/api/reviews", headers=SSE, files={"file": ("a.pdf", b"not PDF", "application/pdf")}).status_code == 415
    assert upload(client, headers=SSE, data={"jurisdiction": "invalid"}).status_code == 422
    assert services.llm.calls == []


@pytest.mark.parametrize("error,code,message", [(UpstreamError("The AI service did not respond."), "upstream_unavailable", "The AI service did not respond."),
    (pipeline.UserError(422, "bad_result", "Could not read this document."), "bad_result", "Could not read this document."),
    (RuntimeError("INTERNAL PRIVATE DETAIL"), "internal_error", "Something went wrong on our side. Please try again.")])
def test_late_failure_is_safe_error_event_without_success_or_traceback(client, services, monkeypatch, error, code, message):
    monkeypatch.setattr(services.llm, "generate", Mock(side_effect=error))
    response = upload(client, headers=SSE)
    rows = events(response)
    assert rows[-2] == ("progress", {"stage": "drafting"})
    assert rows[-1] == ("error", {"error": {"code": code, "message": message}})
    assert not any(name == "result" for name, _ in rows)
    assert "Traceback" not in response.text and "INTERNAL PRIVATE DETAIL" not in response.text
    assert services.audit.everyone(100) == []


def test_same_workspace_concurrent_sample_requests_recheck_cache_inside_lock(services, monkeypatch):
    # Observe the second lock attempt while the first review is held inside its execution.
    original_defaultdict = main.defaultdict
    lock_attempts = 0
    guard = threading.Lock()
    second_waiting, running, release = threading.Event(), threading.Event(), threading.Event()

    class TrackedLock:
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            nonlocal lock_attempts
            with guard:
                lock_attempts += 1
                if lock_attempts == 2:
                    second_waiting.set()
            self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()

    monkeypatch.setattr(main, "defaultdict", lambda factory: original_defaultdict(TrackedLock if factory is threading.Lock else factory))
    api = create_app(services.settings, services)
    original = pipeline.review
    calls = []

    def held(*args, **kwargs):
        calls.append(1)
        running.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "review", held)

    def request():
        return TestClient(api, headers=bearer(ALICE)).post(SAMPLE, headers=SSE)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(request)
        assert running.wait(5)
        second = pool.submit(request)
        try:
            assert second_waiting.wait(5)
        finally:
            release.set()
        a, b = first.result(5), second.result(5)
    fresh = events(a)[-1][1]
    assert b.headers["X-Review-Source"] == "cached" and b.json()["id"] == fresh["review"]["id"]
    assert len(calls) == 1
    assert sum(event.action == "reviewed" for event in services.audit.everyone(100)) == 1


def test_unicode_and_embedded_newlines_are_one_chunk_safe_sse_event():
    data = {"review": {"filename": "珈琲 ☕\n\nevent: fake\n資料.pdf"}, "source": "fresh"}
    encoded = progress.encode_event("result", data)
    decoder = codecs.getincrementaldecoder("utf-8")()
    text = "".join(decoder.decode(bytes([byte]), final=False) for byte in encoded) + decoder.decode(b"", final=True)
    assert text.count("\n\n") == 1
    assert json.loads(text.split("data: ", 1)[1]) == data


def test_progress_is_available_before_job_completes_and_disconnect_unblocks_full_queue():
    async def scenario():
        proceed = threading.Event()
        finished = threading.Event()
        executions = []

        def job(report):
            executions.append(1)
            report("reading")
            assert proceed.wait(5)
            for _ in range(progress.QUEUE_SIZE * 4):
                report("planning")
            finished.set()
            return recorded(), "fresh"

        response = await progress.stream_review(job, stream_error)
        first = await anext(response.body_iterator)
        assert first == progress.encode_event("progress", {"stage": "reading"})
        assert not finished.is_set()  # this was delivered while work was still executing
        await response.body_iterator.aclose()
        proceed.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert await asyncio.to_thread(response.channel.finished.wait, 5)
        assert response.channel.events.qsize() <= progress.QUEUE_SIZE
        assert executions == [1]
    asyncio.run(scenario())


def test_disconnect_before_body_iteration_still_closes_producer():
    async def scenario():
        proceed = threading.Event()

        def job(report):
            report("reading")
            assert proceed.wait(5)
            for _ in range(progress.QUEUE_SIZE * 4):
                report("planning")
            return recorded(), "fresh"

        response = await progress.stream_review(job, stream_error)

        async def send(message):
            raise OSError("connection closed")

        async def receive():
            return {"type": "http.disconnect"}

        try:
            with pytest.raises(ClientDisconnect):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        finally:
            proceed.set()
        assert response.channel.closed.is_set()
        assert await asyncio.to_thread(response.channel.finished.wait, 5)
    asyncio.run(scenario())


def test_heartbeat_is_only_a_comment_without_inventing_progress(monkeypatch):
    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", .001)

    async def scenario():
        proceed = threading.Event()

        def job(report):
            report("reading")
            assert proceed.wait(5)
            return recorded(), "fresh"

        response = await progress.stream_review(job, stream_error)
        assert await anext(response.body_iterator) == progress.encode_event("progress", {"stage": "reading"})
        assert await anext(response.body_iterator) == b": keep-alive\n\n"
        proceed.set()
        frames = [frame async for frame in response.body_iterator]
        assert sum(frame.startswith(b"event: result") for frame in frames) == 1
        assert not any(frame.startswith(b"event: progress") for frame in frames)
    asyncio.run(scenario())


def test_cancel_before_first_stage_releases_producer():
    async def scenario():
        started, proceed, finished = threading.Event(), threading.Event(), threading.Event()

        def job(report):
            started.set()
            assert proceed.wait(5)
            for _ in range(progress.QUEUE_SIZE * 4):
                report("reading")
            finished.set()
            return recorded(), "fresh"

        task = asyncio.create_task(progress.stream_review(job, stream_error))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        proceed.set()
        assert await asyncio.to_thread(finished.wait, 5)
    asyncio.run(scenario())


@pytest.mark.parametrize("accept", ["application/json", "text/event-stream;q=0", "text/event-stream;q=invalid"])
def test_non_opted_in_requests_keep_json(client, accept):
    response = upload(client, headers={"Accept": accept})
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    ReviewResult.model_validate(response.json())
