"""Negotiation email quality guards and search de-duplication."""

from app import pipeline
from app.models import LLMNegotiation, NegotiationAsk
from app.pipeline import clause_label, collapse_hits
from app.ports import Chunk
from pdfgen import PAGES


def test_clause_label_finds_the_number_before_the_quote():
    assert clause_label(PAGES[1], "The Vendor's total liability is limited to the fees", 2) == "Clause 5.2 (page 2)"
    assert clause_label(PAGES[2], "This Agreement is governed by the laws of the State of Delaware.", 3) == "Clause 9.1 (page 3)"
    assert clause_label(PAGES[1], "text that is not on the page at all", 2) == "Page 2"


def test_negotiation_prompt_has_where_and_the_new_rules(client, services, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    client.post(f"/api/reviews/{body['id']}/negotiation")
    call = services.llm.calls[-1]
    assert "where: Clause 5.1 (page 2)" in call["parts"][0]
    assert "under 220 words" in call["system"] and "no \"[Your name]\"" in call["system"].replace("\n", " ")


def test_draft_with_placeholder_is_regenerated_once(services, monkeypatch):
    review = services.store  # noqa: F841 (review built below)
    from app.models import Finding, Metrics, ReviewResult, Verdict
    from datetime import datetime, timezone

    r = ReviewResult(
        id="r1", created_at=datetime.now(timezone.utc), filename="c.pdf", contract_type="nda", language="en",
        pages=1, parties=["Acme Sdn Bhd", "Vend Pte Ltd"],
        verdict=Verdict(overall_risk="high", headline="h", summary=["s"]),
        findings=[Finding(id="f1", clause_type="liability", title="t", risk="high", quote="q" * 20, page=1,
                          why_risky="w", standard="s", suggested_ask="Cap it.")],
        missing_clauses=[], metrics=Metrics(latency_ms=1, model="m"), disclaimer="d")
    drafts = iter([
        LLMNegotiation(subject="s", body="Dear [Vendor contact],\n\nPlease cap it.\n\n[Your name]",
                       asks=[NegotiationAsk(finding_id="f1", ask="Cap it.")]),
        LLMNegotiation(subject="s", body="Dear Vend Pte Ltd,\n\nPlease cap it.\n\nKind regards,\nAcme Sdn Bhd",
                       asks=[NegotiationAsk(finding_id="f1", ask="Cap it.")]),
    ])
    prompts_seen = []

    def gen(**kw):
        prompts_seen.append(kw["parts"])
        return next(drafts), pipeline.LLMUsage("m")

    monkeypatch.setattr(services.llm, "generate", gen)
    out = pipeline.negotiation(services, r)
    assert out.body.startswith("Dear Vend Pte Ltd,") and len(prompts_seen) == 2
    assert "bracketed placeholder" in prompts_seen[1][1]
    # a draft that is too long is also sent back once
    assert pipeline._email_problems("word " * 221) and not pipeline._email_problems("word " * 200)


def _c(id_, review, page, text, finding=None):
    return Chunk(id=id_, review_id=review, filename=f"{review}.pdf", page=page, text=text, finding_id=finding)


def test_collapse_prefers_findings_dedupes_overlap_and_caps_two_per_review():
    quote = "The Customer shall be liable without limit for any loss suffered by the Vendor"
    hits = [
        (_c("a-raw", "a", 2, f"5.1 {quote}. 5.2 more words follow"), 0.90),
        (_c("a-f1", "a", 2, f"Unlimited liability: {quote}", "f1"), 0.85),
        (_c("a-other", "a", 3, "governed by the laws of Delaware and nothing else matters here"), 0.80),
        (_c("a-third", "a", 1, "services are provided with reasonable skill and care by the vendor"), 0.70),
        (_c("b-raw", "b", 2, "payment is due within thirty days of the invoice date"), 0.60),
    ]
    out = collapse_hits(hits)
    ids = [c.id for c, _ in out]
    assert "a-raw" not in ids and "a-f1" in ids  # overlapping raw chunk replaced by the finding
    assert ids.count("a-f1") == 1 and sum(1 for c, _ in out if c.review_id == "a") == 2
    assert "a-third" not in ids and "b-raw" in ids
    assert [s for _, s in out] == sorted((s for _, s in out), reverse=True)
    assert dict((c.id, s) for c, s in out)["a-f1"] == 0.90  # keeps the better score of the pair


def test_search_route_returns_at_most_two_hits_per_review(client, pdf_bytes):
    client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")})
    hits = client.get("/api/search", params={"q": "liability governed renewal notice", "limit": 20}).json()
    from collections import Counter

    assert hits and max(Counter(h["review_id"] for h in hits).values()) <= 2
