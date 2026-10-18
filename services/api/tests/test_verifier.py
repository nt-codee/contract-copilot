from app.models import LLMFinding
from app.pipeline import chunk_page, match_on_page, verify_findings, verify_quote
from pdfgen import PAGES


def _f(quote: str, page: int, risk: str = "high") -> LLMFinding:
    return LLMFinding(clause_type="liability", title="t", risk=risk, quote=quote, page=page,
                      why_risky="w", standard="s", suggested_ask="a", baseline_ref="", citations=[], policy_breach="")


def test_exact_match_returns_original_span():
    hit = verify_quote("The Customer shall be liable without limit", 2, PAGES)
    assert hit == (2, "The Customer shall be liable without limit")


def test_case_and_whitespace_normalised():
    # the page text breaks this sentence across a newline; the quote uses different case
    hit = verify_quote("THE VENDOR'S TOTAL LIABILITY is limited to the fees paid in the one month   before the claim",
                       2, PAGES)
    assert hit is not None and hit[0] == 2
    assert hit[1] == "The Vendor's total liability is limited to the fees paid in the one month before the claim"


def test_smart_quotes_normalised():
    assert verify_quote("The Vendor\u2019s total liability is limited", 2, PAGES) is not None


def test_fuzzy_match_above_threshold():
    hit = verify_quote("The Vendors total liability is limited to the fees paid in the one month before a claim",
                       2, PAGES)
    assert hit is not None and hit[0] == 2
    assert hit[1].startswith("The Vendor's total liability")


def test_fuzzy_below_threshold_is_rejected():
    assert match_on_page("The supplier may terminate this contract at any time without notice", PAGES[1]) is None


def test_neighbour_page_corrects_page_number():
    hit = verify_quote("renews automatically for successive 12 month terms", 3, PAGES)
    assert hit is not None and hit[0] == 2
    hit = verify_quote("This Agreement is governed by the laws of the State of Delaware", 2, PAGES)
    assert hit is not None and hit[0] == 3


def test_two_pages_away_is_not_searched():
    assert verify_quote("This Agreement is governed by the laws of the State of Delaware", 1, PAGES) is None


def test_unverifiable_quote_dropped_and_counted():
    findings, dropped, _ = verify_findings(
        [_f("The Customer shall indemnify the Vendor against all claims whatsoever.", 2),
         _f("The Customer shall be liable without limit for any loss", 2)], PAGES)
    assert dropped == 1
    assert [f.quote for f in findings] == ["The Customer shall be liable without limit for any loss"]


def test_too_short_quote_dropped():
    findings, dropped, _ = verify_findings([_f("Vendor", 2)], PAGES)
    assert findings == [] and dropped == 1


def test_out_of_range_page_handled():
    hit = verify_quote("Notices must be sent by registered post", 4, PAGES)  # claimed page beyond the doc
    assert hit is not None and hit[0] == 3
    assert verify_quote("Notices must be sent by registered post", 99, PAGES) is None


def test_sorted_high_medium_low_then_page_and_ids_sequential():
    findings, _, _ = verify_findings([
        _f("Notices must be sent by registered post", 3, "low"),
        _f("This Agreement is governed by the laws of the State of Delaware", 3, "medium"),
        _f("The Vendor shall perform the services with reasonable skill", 1, "high"),
        _f("The Customer shall be liable without limit", 2, "high"),
    ], PAGES)
    assert [(f.risk, f.page) for f in findings] == [("high", 1), ("high", 2), ("medium", 3), ("low", 3)]
    assert [f.id for f in findings] == ["f1", "f2", "f3", "f4"]


def test_chunk_page_respects_size():
    text = "\n\n".join(["word " * 120] * 5)
    chunks = chunk_page(text, size=800)
    assert chunks and all(len(c) <= 800 for c in chunks)
    assert sum(len(c.split()) for c in chunks) == 600


def test_negotiation_body_unescapes_literal_newlines():
    from app.pipeline import _unescape_body

    assert _unescape_body("Dear X,\\n\\n1. Ask\\nThanks") == "Dear X,\n\n1. Ask\nThanks"
    assert _unescape_body("Dear X,\n\nPath C:\\new stays") == "Dear X,\n\nPath C:\\new stays"
