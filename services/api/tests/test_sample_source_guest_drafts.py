"""Sample origin metadata and side-effect-free explicit guest draft reads."""

from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import pipeline
from app.models import ReviewResult
from app.guest import GUEST_MESSAGE

SEED = {"X-Workspace-Id": "demo"}
GUEST = {"X-Guest": "demo"}
SAMPLE = "/api/reviews/sample/demo-01"


def assert_source(response, source):
    assert response.status_code == 200, response.text
    assert response.headers["X-Review-Source"] == source
    ReviewResult.model_validate(response.json())
    assert "sample_opening" not in response.json()
    return response.json()


def test_all_sample_cache_branches_report_cached(admin, anon, client, services):
    demo = assert_source(admin.post(SAMPLE, headers=SEED), "fresh")
    before = len(services.llm.calls)
    for headers in ({}, GUEST, SEED):
        for refresh in (False, True) if headers != SEED else (False,):
            cached = assert_source(anon.post(SAMPLE, headers=headers, params={"refresh": refresh}), "cached")
            assert cached["id"] == demo["id"]
    copied = assert_source(client.post(SAMPLE), "copied")
    cached = assert_source(client.post(SAMPLE), "cached")
    assert cached["id"] == copied["id"]
    assert len(services.llm.calls) == before


def test_a_newly_created_copy_is_still_reported_as_copied(admin, anon, client, services, monkeypatch):
    assert_source(admin.post(SAMPLE, headers=SEED), "fresh")
    copy = Mock(wraps=pipeline.copy_review)
    monkeypatch.setattr(pipeline, "copy_review", copy)
    model = Mock(side_effect=AssertionError("copy must not start a review"))
    monkeypatch.setattr(pipeline, "review", model)
    started = datetime.now(timezone.utc)
    result = assert_source(client.post(SAMPLE), "copied")
    assert datetime.fromisoformat(result["created_at"].replace("Z", "+00:00")) >= started
    copy.assert_called_once()
    model.assert_not_called()


def test_private_first_runs_refreshes_and_admin_seeds_report_fresh(client, admin, services):
    before = len(services.llm.calls)
    first = assert_source(client.post(SAMPLE), "fresh")
    assert len(services.llm.calls) > before
    before = len(services.llm.calls)
    refreshed = assert_source(client.post(SAMPLE, params={"refresh": True}), "fresh")
    assert refreshed["id"] != first["id"] and len(services.llm.calls) > before
    seeded = assert_source(admin.post(SAMPLE, headers=SEED), "fresh")
    again = assert_source(admin.post(SAMPLE, headers=SEED, params={"refresh": True}), "fresh")
    assert again["id"] != seeded["id"]


def test_refused_sample_requests_have_no_success_source_header(anon):
    for headers, status in (({}, 401), (GUEST, 403)):
        response = anon.post(SAMPLE, headers=headers)
        assert response.status_code == status and "X-Review-Source" not in response.headers
    response = anon.post("/api/reviews/sample/unknown")
    assert response.status_code == 404 and "X-Review-Source" not in response.headers


def no_draft_side_effects(monkeypatch, services):
    model = Mock(side_effect=AssertionError("guest must not call the model"))
    audit = Mock(side_effect=AssertionError("guest must not write audit events"))
    draft = Mock(side_effect=AssertionError("guest must not enter draft generation"))
    monkeypatch.setattr(services.llm, "generate", model)
    monkeypatch.setattr(services.audit, "record", audit)
    monkeypatch.setattr(pipeline, "negotiation", draft)
    return model, audit, draft


def test_guest_draft_cache_miss_refuses_before_pipeline_model_and_audit(admin, anon, services, monkeypatch):
    result = assert_source(admin.post(SAMPLE, headers=SEED), "fresh")
    before = services.audit.everyone(100)
    spies = no_draft_side_effects(monkeypatch, services)
    for _ in range(2):
        response = anon.post(f"/api/reviews/{result['id']}/negotiation", headers=GUEST)
        assert response.status_code == 403
        assert response.json() == {"error": {"code": "guest_read_only", "message": GUEST_MESSAGE}}
    for spy in spies:
        spy.assert_not_called()
    assert services.audit.everyone(100) == before


def test_guest_draft_cache_hit_reads_without_pipeline_model_or_audit(admin, anon, services, monkeypatch):
    result = assert_source(admin.post(SAMPLE, headers=SEED), "fresh")
    path = f"/api/reviews/{result['id']}/negotiation"
    # A signed-in admin generates the shared demo reply; guests only read its cache.
    calls = len(services.llm.calls)
    draft = admin.post(path, headers=SEED)
    assert draft.status_code == 200 and len(services.llm.calls) > calls
    assert any(event.action == "drafted_reply" for event in services.audit.everyone(100))
    before = services.audit.everyone(100)
    spies = no_draft_side_effects(monkeypatch, services)
    for _ in range(2):
        response = anon.post(path, headers=GUEST)
        assert response.status_code == 200 and response.json() == draft.json()
    for spy in spies:
        spy.assert_not_called()
    assert services.audit.everyone(100) == before


def test_cached_draft_still_checks_workspace_access(admin, api, anon, client):
    result = assert_source(admin.post(SAMPLE, headers=SEED), "fresh")
    path = f"/api/reviews/{result['id']}/negotiation"
    assert admin.post(path, headers=SEED).status_code == 200
    assert client.post(path).status_code == 404
