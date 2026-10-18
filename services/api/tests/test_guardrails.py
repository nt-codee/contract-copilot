from pdfgen import make_blank_pdf


def _upload(client, data: bytes, name: str = "c.pdf", headers=None):
    return client.post("/api/reviews", files={"file": (name, data, "application/pdf")}, headers=headers or {})


def _assert_plain_error(resp, status: int, code: str):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["error"]["code"] == code
    msg = body["error"]["message"]
    assert msg and "Traceback" not in msg and "Error" not in msg


def test_too_large_is_413(client):
    big = b"%PDF-1.7\n" + b"0" * (10 * 1024 * 1024 + 10)
    _assert_plain_error(_upload(client, big), 413, "file_too_large")


def test_not_a_pdf_is_415(client):
    _assert_plain_error(_upload(client, b"PK\x03\x04 this is a zip", "c.docx"), 415, "not_a_pdf")


def test_corrupt_pdf_is_422(client):
    _assert_plain_error(_upload(client, b"%PDF-1.7\n garbage garbage"), 422, "unreadable_pdf")


def test_too_many_pages_is_422(client):
    _assert_plain_error(_upload(client, make_blank_pdf(41)), 422, "too_many_pages")


def test_forty_pages_is_accepted(client):
    assert _upload(client, make_blank_pdf(40)).status_code == 200


def test_missing_file_is_422(client):
    _assert_plain_error(client.post("/api/reviews"), 422, "invalid_request")


def test_rate_limit_per_ip(client, pdf_bytes):
    a = {"x-forwarded-for": "198.51.100.7"}
    for _ in range(10):
        assert _upload(client, pdf_bytes, headers=a).status_code == 200
    _assert_plain_error(_upload(client, pdf_bytes, headers=a), 429, "rate_limited")
    # a spoofed first entry does not dodge the limit; the last entry is the real client
    spoof = {"x-forwarded-for": "203.0.113.1, 198.51.100.7"}
    assert _upload(client, pdf_bytes, headers=spoof).status_code == 429
    # another client is unaffected
    assert _upload(client, pdf_bytes, headers={"x-forwarded-for": "198.51.100.8"}).status_code == 200


def test_rejected_upload_does_not_use_rate_limit(client, pdf_bytes):
    a = {"x-forwarded-for": "198.51.100.9"}
    for _ in range(12):
        _upload(client, b"not a pdf", headers=a)
    assert _upload(client, pdf_bytes, headers=a).status_code == 200


def test_unknown_api_path_is_json_404(client):
    _assert_plain_error(client.get("/api/nope"), 404, "not_found")


def test_unhandled_error_has_no_stack_trace(services, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(services.store, "list", boom)
    c = TestClient(create_app(services.settings, services), raise_server_exceptions=False)
    r = c.get("/api/reviews")
    assert r.status_code == 500
    assert "secret" not in r.text and r.json()["error"]["code"] == "internal_error"
