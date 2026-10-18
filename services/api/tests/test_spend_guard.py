"""The global daily cap on fresh model reviews."""

from __future__ import annotations

import dataclasses
import threading

import pytest
from fastapi.testclient import TestClient

from app import spend
from app.adapters.local import MemorySpendCounter
from app.main import create_app
from app.ports import UpstreamError
from app.spend import DAILY_LIMIT_MESSAGE, next_count
from conftest import ROOT, bearer
from pdfgen import make_pdf

PDF = make_pdf()


def tok(uid):
    return f"test:{uid}:{uid}@gmail.test"


def capped(services, cap, **kw):
    st = dataclasses.replace(services.settings, max_model_reviews_per_day=cap, rate_limit_per_hour=1000,
                             rate_limit_user_per_hour=1000, **kw)
    return create_app(st, services)


def upload(c, name="a.pdf"):
    return c.post("/api/reviews", files={"file": (name, PDF, "application/pdf")})


def test_the_default_cap_is_300_and_comes_from_the_environment(env, monkeypatch):
    from app.config import Settings

    assert Settings().max_model_reviews_per_day == 300
    monkeypatch.setenv("MAX_MODEL_REVIEWS_PER_DAY", "7")
    assert Settings().max_model_reviews_per_day == 7


def test_uploads_stop_at_the_cap_with_503_daily_limit(services):
    c = TestClient(capped(services, 3), headers=bearer(tok("ann")))
    assert [upload(c).status_code for _ in range(3)] == [200, 200, 200]
    n = len(services.llm.calls)
    r = upload(c)
    assert r.status_code == 503
    assert r.json() == {"error": {"code": "daily_limit", "message":
                                  "The demo has reached today's review limit. Please try again tomorrow, or browse the sample workspace."}}
    assert r.json()["error"]["message"] == DAILY_LIMIT_MESSAGE
    assert len(services.llm.calls) == n  # no model call once the cap is reached
    assert services.spend.used(f"reviews-{spend.today()}") == 3  # the refused one did not count


def test_the_cap_is_shared_by_every_user_and_by_sample_refreshes(services):
    api = capped(services, 3)
    a, b = TestClient(api, headers=bearer(tok("ann"))), TestClient(api, headers=bearer(tok("bea")))
    assert upload(a).status_code == 200
    assert b.post("/api/reviews/sample/demo-01", params={"refresh": 1}).status_code == 200  # a fresh run counts
    assert upload(b).status_code == 200
    assert upload(a).status_code == 503 and b.post("/api/reviews/sample/demo-01", params={"refresh": 1}).status_code == 503


def test_copies_cached_samples_drafts_and_policy_uploads_are_not_counted(services):
    api = capped(services, 1)
    anon = TestClient(api)
    ann = TestClient(api, headers=bearer(tok("ann")))
    # one fresh model review spends the whole allowance
    assert anon.post("/api/reviews/sample/demo-01", headers={"X-Workspace-Id": "demo"}).status_code == 200
    assert services.spend.used(f"reviews-{spend.today()}") == 1
    # none of these is a fresh model review: they all still work
    assert anon.post("/api/reviews/sample/demo-01").status_code == 200  # cached
    assert ann.post("/api/reviews/sample/demo-01").status_code == 200  # a copy into her workspace
    rid = ann.get("/api/reviews").json()[0]["id"]
    assert ann.post(f"/api/reviews/{rid}/negotiation").status_code == 200  # a reply draft
    assert ann.post("/api/company/docs", files={"file": ("p.pdf", PDF, "application/pdf")}).status_code == 200
    assert ann.get(f"/api/reviews/{rid}/export").status_code == 200
    assert services.spend.used(f"reviews-{spend.today()}") == 1
    # but a second fresh review is refused
    assert upload(ann).status_code == 503


def test_platform_admins_are_exempt_and_do_not_use_the_allowance(services):
    api = capped(services, 1)
    admin = TestClient(api, headers=bearer(ROOT))
    assert [upload(admin).status_code for _ in range(4)] == [200] * 4
    assert services.spend.used(f"reviews-{spend.today()}") == 0
    ann = TestClient(api, headers=bearer(tok("ann")))
    assert upload(ann).status_code == 200 and upload(ann).status_code == 503  # the allowance was still whole


def test_a_cap_of_zero_refuses_every_fresh_review_for_non_admins(services):
    api = capped(services, 0)
    assert upload(TestClient(api, headers=bearer(tok("ann")))).status_code == 503
    assert upload(TestClient(api, headers=bearer(ROOT))).status_code == 200


def test_the_allowance_starts_again_on_the_next_utc_day(services, monkeypatch):
    c = TestClient(capped(services, 1), headers=bearer(tok("ann")))
    monkeypatch.setattr(spend, "today", lambda: "2026-10-18")
    assert upload(c).status_code == 200 and upload(c).status_code == 503
    monkeypatch.setattr(spend, "today", lambda: "2026-10-19")
    assert upload(c).status_code == 200


def test_a_rejected_pdf_or_a_bad_option_does_not_take_from_the_allowance(services):
    c = TestClient(capped(services, 1), headers=bearer(tok("ann")))
    assert c.post("/api/reviews", files={"file": ("a.pdf", b"not a pdf", "application/pdf")}).status_code == 415
    assert c.post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")}, data={"jurisdiction": "UK"}).status_code == 422
    assert services.spend.used(f"reviews-{spend.today()}") == 0
    assert upload(c).status_code == 200


def test_guests_and_plain_visitors_cannot_spend_through_uploads(services):
    api = capped(services, 5)
    assert TestClient(api, headers={"X-Guest": "demo"}).post("/api/reviews", files={"file": ("a.pdf", PDF, "application/pdf")}).status_code == 403
    assert upload(TestClient(api)).status_code == 401
    assert services.spend.used(f"reviews-{spend.today()}") == 0


def test_an_unreachable_counter_fails_closed_without_a_model_call(services):
    class Broken:
        def take(self, key, cap):
            raise RuntimeError("firestore down")

        def used(self, key):
            return 0

    services.spend = Broken()
    c = TestClient(capped(services, 5), headers=bearer(tok("ann")))
    n = len(services.llm.calls)
    r = upload(c)
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_unavailable"
    assert "firestore" not in r.text and len(services.llm.calls) == n
    with pytest.raises(UpstreamError):
        spend.charge(Broken(), 5)
    spend.charge(None, 5)  # no counter configured: no cap


def test_the_counter_never_overshoots_under_concurrency():
    counter = MemorySpendCounter()
    results = []

    def go():
        results.append(counter.take("reviews-x", 10))

    threads = [threading.Thread(target=go) for _ in range(60)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 10 and results.count(False) == 50 and counter.used("reviews-x") == 10


def test_next_count_rule():
    assert next_count(0, 3) == 1 and next_count(2, 3) == 3 and next_count(3, 3) is None and next_count(0, 0) is None


def test_the_firestore_counter_uses_one_document_per_day_in_a_transaction():
    """The adapter's shape, checked without the network: one document named after the key, read and written through
    the same transaction object (that is what makes the increment atomic)."""
    import app.adapters.gcp as gcp

    calls = []

    class Snap:
        exists = True

        def to_dict(self):
            return {"count": 4}

    class Ref:
        def get(self, transaction=None):
            calls.append(("get", transaction))
            return Snap()

    class Col:
        def document(self, key):
            calls.append(("doc", key))
            return Ref()

    counter = gcp.FirestoreSpendCounter.__new__(gcp.FirestoreSpendCounter)
    counter._col = Col()
    assert counter.used("reviews-2026-10-18") == 4
    assert ("doc", "reviews-2026-10-18") in calls and gcp.FirestoreSpendCounter.COLLECTION == "spend_counters"
