from app.config import DISCLAIMER
from pdfgen import make_scanned_pdf


def _review(client, pdf_bytes, name="services.pdf"):
    r = client.post("/api/reviews", files={"file": (name, pdf_bytes, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_review_end_to_end_with_fake_llm(client, services, pdf_bytes):
    body = _review(client, pdf_bytes)
    assert body["disclaimer"] == DISCLAIMER
    assert body["pages"] == 3 and body["filename"] == "services.pdf"
    assert body["contract_type"] == "service_agreement"
    assert body["metrics"]["dropped_unverified"] == 1  # the invented indemnity quote
    got = [(f["id"], f["risk"], f["page"], f["clause_type"]) for f in body["findings"]]
    assert got == [("f1", "high", 2, "liability"), ("f2", "high", 2, "liability"),
                   ("f3", "medium", 2, "auto_renewal"),  # page corrected from 3 to 2
                   ("f4", "medium", 3, "governing_law")]
    # quotes are replaced by the exact page text
    assert body["findings"][1]["quote"].startswith("The Vendor's total liability")
    assert body["findings"][3]["quote"] == "This Agreement is governed by the laws of the State of Delaware."
    assert all(f["boxes"] for f in body["findings"])  # every text-layer quote is located
    m = body["metrics"]
    assert m["model"] == "fake-llm" and m["latency_ms"] >= 0 and m["input_tokens"] > 0
    # the agent ran (two chat turns), then one structured call; the text was page-tagged in the system
    # prompt and the final call saw the research
    assert [c["purpose"] for c in services.llm.calls] == ["agent", "agent", "review"]
    call = services.llm.calls[-1]
    assert "[PAGE 2]" in call["system"]
    assert "### search_playbook(" in call["parts"][0] and "service_agreement/liability" in call["parts"][0]
    # citations: kept refs come from the KB (title/excerpt replaced), the invented ref is dropped
    cites = body["findings"][0]["citations"]
    assert [c["ref"] for c in cites] == ["my-contracts-act-s75", "service_agreement/liability"]
    assert cites[0]["title"] == "Contracts Act 1950, s.75" and "MODEL-WRITTEN" not in cites[0]["excerpt"]
    assert body["metrics"]["dropped_unverified_citations"] == 1 and body["jurisdiction"] == "my"
    assert [t["action"] for t in body["trace"]] == [
        "read_contract", "search_law", "search_playbook", "search_company", "read_page",
        "draft_findings", "verify"]


def test_get_list_and_pdf(client, pdf_bytes):
    body = _review(client, pdf_bytes)
    assert client.get(f"/api/reviews/{body['id']}").json()["id"] == body["id"]
    listing = client.get("/api/reviews").json()
    assert listing[0]["id"] == body["id"]
    assert listing[0]["finding_counts"] == {"high": 2, "medium": 2, "low": 0}
    pdf = client.get(f"/api/reviews/{body['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content == pdf_bytes
    assert pdf.headers["content-type"] == "application/pdf"
    assert client.get("/api/reviews/doesnotexist").status_code == 404


def test_negotiation(client, services, pdf_bytes):
    body = _review(client, pdf_bytes)
    r = client.post(f"/api/reviews/{body['id']}/negotiation")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["review_id"] == body["id"] and d["subject"]
    assert d["body"].startswith("Dear Awan Biru Pte Ltd,") and d["body"].endswith("Kedai Contoh Sdn Bhd")
    assert "[" not in d["body"] and len(d["body"].split()) < 220
    # one ask per high/medium finding plus each missing clause; invented ids filtered, gaps backfilled
    assert sorted(a["finding_id"] for a in d["asks"]) == ["f1", "f2", "f3", "f4", "m1"]
    n_calls = len(services.llm.calls)
    client.post(f"/api/reviews/{body['id']}/negotiation")
    assert len(services.llm.calls) == n_calls  # cached


def test_search_finds_indexed_passages(client, pdf_bytes):
    body = _review(client, pdf_bytes)
    hits = client.get("/api/search", params={"q": "governed by the laws of Delaware"}).json()
    assert hits and hits[0]["review_id"] == body["id"] and hits[0]["page"] == 3
    assert all(len(h["text"]) <= 400 for h in hits)
    assert any(h["finding_id"] for h in hits)
    assert client.get("/api/search").status_code == 422


def test_samples_list_and_cached_review(client, services):
    samples = client.get("/api/samples").json()
    assert samples == [{"id": "demo-01", "title": "Demo services agreement",
                        "contract_type": "service_agreement", "language": "en"}]
    first = client.post("/api/reviews/sample/demo-01")
    assert first.status_code == 200 and first.json()["sample_id"] == "demo-01"
    n_calls = len(services.llm.calls)
    second = client.post("/api/reviews/sample/demo-01")
    assert second.json()["id"] == first.json()["id"]
    assert len(services.llm.calls) == n_calls  # served from cache, no LLM call
    refreshed = client.post("/api/reviews/sample/demo-01", params={"refresh": 1})
    assert refreshed.status_code == 200 and refreshed.json()["id"] != first.json()["id"]
    assert len(services.llm.calls) == n_calls + 3  # two agent turns and the final review
    # the newest review is now the cached one
    assert client.post("/api/reviews/sample/demo-01").json()["id"] == refreshed.json()["id"]
    # traversal never reaches the sample route (ids are looked up in index.json, never used as paths)
    assert client.post("/api/reviews/sample/..%2F..%2Fetc").status_code in (404, 405)
    assert client.post("/api/reviews/sample/nope").status_code == 404


def test_scanned_pdf_goes_through_transcription(client, services):
    body = _review(client, make_scanned_pdf(), "scanned.pdf")
    purposes = [c["purpose"] for c in services.llm.calls]
    assert purposes[:4] == ["transcribe", "agent", "agent", "review"]
    assert len(body["findings"]) == 4 and body["metrics"]["dropped_unverified"] == 1
    # one bounded box call per page with findings (pages 2 and 3); the fixture's index 0 box
    # applies, its invalid and out-of-range entries are ignored
    assert purposes.count("boxes") == 2
    by_page = {}
    for f in body["findings"]:
        by_page.setdefault(f["page"], []).append(f["boxes"])
    assert all(boxes[0] == [[0.05, 0.1, 0.9, 0.13]] for boxes in by_page.values())
    assert all(b == [] for boxes in by_page.values() for b in boxes[1:])


def test_spa_static_and_fallback(client):
    assert "<title>cc</title>" in client.get("/").text
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "<title>cc</title>" in client.get("/reviews/abc123").text  # client-side route
    assert client.get("/../../etc/passwd").status_code in (200, 404)
    assert "root:" not in client.get("/..%2F..%2Fetc%2Fpasswd").text
