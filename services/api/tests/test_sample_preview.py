"""Public source previews and the sign-in boundary before fresh sample reviews."""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app import spend
from conftest import ROOT, bearer


def test_preview_is_public_read_only_and_returns_the_original_pdf(anon, services):
    before = (len(services.llm.calls), services.store.list(owner="demo"), services.audit.everyone(100))
    expected = (services.settings.samples_dir / "demo-01.pdf").read_bytes()
    for headers in ({}, {"X-Guest": "demo"}):
        response = anon.get("/api/samples/demo-01/pdf", headers=headers)
        assert response.status_code == 200
        assert response.content == expected
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == 'inline; filename="demo-01.pdf"'
        assert response.headers["x-content-type-options"] == "nosniff"
    assert before == (len(services.llm.calls), services.store.list(owner="demo"), services.audit.everyone(100))


@pytest.mark.parametrize("sample_id", ["unknown", "demo-01.pdf", "..%2Fdemo-01", "%2Fetc%2Fpasswd",
                                        "..%252Fdemo-01"])
def test_preview_requires_an_exact_indexed_id(anon, sample_id):
    response = anon.get(f"/api/samples/{sample_id}/pdf")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_unknown_and_missing_files_return_sample_not_found(anon, services):
    (services.settings.samples_dir / "demo-01.pdf").unlink()
    for sample_id in ("unknown", "demo-01"):
        response = anon.get(f"/api/samples/{sample_id}/pdf")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "sample_not_found"


@pytest.mark.parametrize("escape", ["relative", "absolute", "symlink"])
def test_indexed_files_cannot_escape_the_sample_directory(anon, services, tmp_path, escape):
    root = services.settings.samples_dir
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private outside data")
    if escape == "symlink":
        (root / "linked.pdf").symlink_to(outside)
        name = "linked.pdf"
    else:
        name = "../outside.pdf" if escape == "relative" else str(outside)
    index = [{"id": "escape", "file": name}]
    (root / "index.json").write_text(json.dumps(index))
    response = anon.get("/api/samples/escape/pdf")
    assert response.status_code == 404 and b"private outside data" not in response.content


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("cloud", ["local", "gcp"])
def test_plain_visitors_cannot_start_an_uncached_sample(services, refresh, cloud):
    # Reuse only local fake adapters while checking the production auth settings too.
    services.settings = dataclasses.replace(services.settings, cloud=cloud)
    app = create_app(services.settings, services)
    anon = TestClient(app)
    before = len(services.llm.calls)
    response = anon.post("/api/reviews/sample/demo-01", params={"refresh": refresh})
    assert response.status_code == 401 and response.json()["error"]["code"] == "sign_in_required"
    assert len(services.llm.calls) == before
    assert services.store.list(owner="demo") == []
    assert services.spend.used(f"reviews-{spend.today()}") == 0


def test_cached_samples_stay_public_and_never_refresh_for_visitors(api, services):
    admin = TestClient(api, headers=bearer(ROOT))
    seeded = admin.post("/api/reviews/sample/demo-01", headers={"X-Workspace-Id": "demo"})
    assert seeded.status_code == 200
    before = len(services.llm.calls)
    for headers in ({}, {"X-Guest": "demo"}):
        visitor = TestClient(api, headers=headers)
        for refresh in (False, True):
            response = visitor.post("/api/reviews/sample/demo-01", params={"refresh": refresh})
            assert response.status_code == 200 and response.json()["id"] == seeded.json()["id"]
    assert len(services.llm.calls) == before


def test_guest_and_bad_token_cannot_use_the_offline_seed_exception(api, services):
    visitor = TestClient(api)
    before = len(services.llm.calls)
    for headers, status in (({"X-Guest": "demo", "X-Workspace-Id": "demo"}, 403),
                            ({"Authorization": "Bearer invalid", "X-Workspace-Id": "demo"}, 401)):
        response = visitor.post("/api/reviews/sample/demo-01", headers=headers)
        assert response.status_code == status
    assert len(services.llm.calls) == before
