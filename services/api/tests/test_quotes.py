"""Robust quote matching on PDFs that behave like real ones (app/quotes.py).

Each test builds a PDF with PyMuPDF that reproduces one quirk of real contracts, runs the same
verification and box location the review uses, and checks the quote is kept and boxed on the right
words. The negative tests check that loosening the comparison did not let a paraphrase, a changed
number or a dropped "not" through.
"""

import pymupdf
import pytest

from app import quotes
from app.models import LLMFinding
from app.pipeline import extract_pages, locate_boxes, page_furniture, verify_findings, verify_quote_detail

FONT = "F0"  # an embedded Unicode font (PyMuPDF's bundled Droid Sans Fallback): ligatures, curly quotes
W, H = 595, 842


def _doc(pages: list[list[tuple[float, float, str]]]) -> pymupdf.Document:
    """Each page is a list of (x, y, text) lines, inserted in that order: the content-stream order,
    which is the order PyMuPDF extracts text in. So a footer listed between two lines is extracted
    between them, as it is in many real PDFs."""
    doc = pymupdf.open()
    buf = pymupdf.Font("cjk").buffer
    for lines in pages:
        page = doc.new_page(width=W, height=H)
        page.insert_font(fontname=FONT, fontbuffer=buf)
        for x, y, text in lines:
            page.insert_text((x, y), text, fontname=FONT, fontsize=10)
    return doc


def _f(quote: str, page: int = 1) -> LLMFinding:
    return LLMFinding(clause_type="liability", title="t", risk="high", quote=quote, page=page, why_risky="w",
                      standard="s", suggested_ask="a", baseline_ref="", citations=[], policy_breach="")


def _review(doc: pymupdf.Document, quote: str, page: int = 1):
    """verify + boxes, as pipeline._review does for a text PDF."""
    pages = extract_pages(doc)
    removed: list = []
    found, dropped, _ = verify_findings([_f(quote, page)], pages, removed=removed)
    if not found:
        return None, removed
    f = found[0]
    f.boxes = locate_boxes(doc[f.page - 1], f.quote, page_furniture(pages))
    return f, removed


def _in(box, y_top: float, y_bottom: float) -> bool:
    """The box lies within the band of baselines y_top..y_bottom (points), with a line of slack."""
    return box[1] * H >= y_top - 14 and box[3] * H <= y_bottom + 6


# ---- quirks --------------------------------------------------------------------------------------


def test_ligature_in_the_pdf_plain_letters_in_the_quote():
    doc = _doc([[(56, 100, "2. Confidentiality"),
                 (56, 120, "2.1 The Recipient shall keep all con\ufb01dential information secret for \ufb01ve years"),
                 (56, 134, "after the date of disclosure.")]])
    assert "\ufb01" in extract_pages(doc)[0]  # the text layer really holds the ligature
    f, removed = _review(doc, "The Recipient shall keep all confidential information secret for five years")
    assert f is not None and removed == []
    assert f.verification.quote_match == "normalised" and f.verification.score == 100.0
    assert len(f.boxes) == 1 and _in(f.boxes[0], 120, 120)


def test_word_hyphenated_across_a_line_end():
    doc = _doc([[(56, 100, "5.1 The Customer shall indemnify the Vendor against all claims, losses and termi-"),
                 (56, 114, "nation costs arising from any breach of this Agreement by the Customer.")]])
    quote = "The Customer shall indemnify the Vendor against all claims, losses and termination costs arising"
    f, _ = _review(doc, quote)
    assert f is not None and f.verification.quote_match == "normalised"
    assert "termination costs" in f.quote and "termi-" not in f.quote  # the displayed quote is rejoined
    assert len(f.boxes) == 2 and _in(f.boxes[0], 100, 100) and _in(f.boxes[1], 114, 114)
    assert f.boxes[1][2] < 0.6  # the second box stops at "arising", not the end of the line


def test_footer_extracted_between_two_lines_of_the_clause():
    doc = _doc([[(56, 100, "7.1 Either party may terminate this Agreement by giving the other party ninety (90)"),
                 (56, 820, "Page 1 of 3 Sample document for testing"),  # drawn at the foot, extracted here
                 (56, 114, "days' written notice, and the Customer shall pay all fees up to the end date.")]])
    text = extract_pages(doc)[0]
    assert text.index("Page 1 of 3") < text.index("days' written")
    quote = "Either party may terminate this Agreement by giving the other party ninety (90) days' written notice"
    f, _ = _review(doc, quote)
    assert f is not None and f.verification.quote_match == "normalised"
    assert "Page 1" not in f.quote and "Sample document" not in f.quote
    assert len(f.boxes) == 2 and all(_in(b, 100, 114) for b in f.boxes)  # nothing boxed on the footer


def test_running_header_repeated_on_every_page_is_ignored():
    head = "KEDAI CONTOH SDN BHD - MASTER SERVICES AGREEMENT"
    doc = _doc([[(56, 60, head), (56, 100, "1. Services"), (56, 114, "1.1 The Vendor shall provide the services.")],
                [(56, 100, "4.1 The Customer shall pay each invoice within ninety (90) days of"),
                 (56, 60, head),
                 (56, 114, "receipt, without set-off or deduction of any kind.")],
                [(56, 60, head), (56, 100, "9. General")]])
    assert quotes.tokenize(head) and page_furniture(extract_pages(doc))
    f, _ = _review(doc, "The Customer shall pay each invoice within ninety (90) days of receipt, without set-off",
                   page=2)
    assert f is not None and f.page == 2 and "MASTER SERVICES" not in f.quote
    assert len(f.boxes) == 2 and all(_in(b, 100, 114) for b in f.boxes)


def test_curly_quotes_and_apostrophes_in_the_pdf_straight_in_the_quote():
    doc = _doc([[(56, 100, "1.2 \u201cConfidential Information\u201d means the Discloser\u2019s business plans, recipes"),
                 (56, 114, "and supplier prices \u2014 whether marked or not.")]])
    f, _ = _review(doc, '"Confidential Information" means the Discloser\'s business plans, recipes and supplier '
                        "prices - whether marked or not.")
    assert f is not None and f.verification.quote_match == "normalised"
    assert f.quote.startswith("\u201cConfidential Information\u201d")  # the page's own characters are shown
    assert len(f.boxes) == 2


def test_two_columns_box_only_the_quoted_column():
    left, right = 56, 310
    doc = _doc([[(left, 100, "3.1 The Supplier may suspend the"), (left, 114, "services at any time without"),
                 (left, 128, "notice and without liability."),
                 (right, 100, "3.2 The Customer may end this"), (right, 114, "Agreement on thirty (30) days'"),
                 (right, 128, "written notice to the Supplier.")]])
    f, _ = _review(doc, "The Supplier may suspend the services at any time without notice and without liability.")
    assert f is not None and len(f.boxes) == 3
    assert all(b[2] < right / W for b in f.boxes)  # no box reaches into the right-hand column
    g, _ = _review(doc, "The Customer may end this Agreement on thirty (30) days' written notice")
    assert g is not None and all(b[0] >= right / W - 0.01 for b in g.boxes) and len(g.boxes) == 3


def test_quote_running_onto_the_next_page_with_the_footer_copied_in():
    """emp-03 f5: the model's quote ran across the page break and swallowed the footer."""
    footer = "Page {} of 2 Sample document for testing"
    doc = _doc([[(56, 100, "5.1 For twelve (12) months after it ends, the Freelancer shall not provide web design"),
                 (56, 114, "services to any other digital agency in Malaysia or"),
                 (56, 820, footer.format(1))],
                [(56, 100, "to any client of the Client."), (56, 820, footer.format(2))]])
    quote = ("the Freelancer shall not provide web design services to any other digital agency in Malaysia or "
             "Page 1 of 2 Sample document for testing to any client of the Client.")
    f, _ = _review(doc, quote)
    assert f is not None and f.page == 1
    assert f.quote.endswith("in Malaysia or to any client of the Client.") and "Sample document" not in f.quote
    assert len(f.boxes) == 2 and all(_in(b, 100, 114) for b in f.boxes)  # the part on page 1


def test_invisible_and_odd_characters_in_the_text_layer():
    page = ("8.2 The\u00a0Vendor\u2019s total liability\u200b is limited to the fees paid in the twelve\u00ad\n"
            "\u2022 (12) months before the claim.")
    hit = quotes.match_text("The Vendor's total liability is limited to the fees paid in the twelve (12) months", page)
    assert hit is not None and hit.how == "normalised"
    assert hit.span == "The\u00a0Vendor\u2019s total liability\u200b is limited to the fees paid in the twelve " \
                       "\u2022 (12) months".replace("\u00a0", " ").replace("\u200b", "")


def test_small_spelling_difference_is_fuzzy_with_its_score():
    doc = _doc([[(56, 100, "6.1 This Agreement renews automatically for successive twelve (12) month terms unless"),
                 (56, 114, "the Customer gives written notice at least ninety (90) days before the renewal date.")]])
    f, _ = _review(doc, "This Agreement renew automaticaly for successive twelve (12) month terms unless the "
                        "Customer gives written notice")
    assert f is not None and f.verification.quote_match == "fuzzy"
    assert quotes.FUZZY_THRESHOLD <= f.verification.score < 100
    assert f.quote.startswith("This Agreement renews automatically") and len(f.boxes) == 2


# ---- what must still be refused ------------------------------------------------------------------

PAGE = _doc([[(56, 100, "4.1 The Customer shall pay each invoice within ninety (90) days of receipt."),
              (56, 114, "4.2 The Vendor shall not charge interest on late payments.")]])


@pytest.mark.parametrize("quote", [
    "The client must settle every bill no later than three months after it is received.",  # paraphrase
    "The Customer shall pay each invoice within thirty (30) days of receipt.",  # number changed
    "The Vendor shall charge interest on late payments.",  # "not" dropped
    "The Vendor may terminate this Agreement at any time without notice.",  # not there at all
])
def test_paraphrase_changed_number_or_dropped_negation_is_removed(quote):
    f, removed = _review(PAGE, quote)
    assert f is None
    assert [(r.kind, r.reason) for r in removed] == [("finding", "quote_not_found")]


def test_short_quotes_are_exact_only():
    pages = extract_pages(PAGE)
    assert verify_quote_detail("shall pay each invoice", 1, pages) is not None
    assert verify_quote_detail("shall pay each invoce", 1, pages) is None  # 4 tokens: no fuzzy
    assert verify_quote_detail("pay", 1, pages) is None  # under MIN_QUOTE_CHARS


def test_across_pages_is_exact_only_and_needs_a_real_start():
    doc = _doc([[(56, 100, "9.1 The Customer shall keep the Vendor's pricing secret and")],
                [(56, 100, "shall not disclose it to any third party.")]])
    pages = extract_pages(doc)
    hit = verify_quote_detail("The Customer shall keep the Vendor's pricing secret and shall not disclose it", 1, pages)
    assert hit is not None and hit.page == 1
    doc2 = _doc([[(56, 100, "9.2 The Vendor's total liability arising out of this Agreement,")],
                 [(56, 100, "whether in contract or tort, shall not exceed the fees paid.")]])
    hit2 = verify_quote_detail("The Vendor's total liability arising out of this Agreement, whether in contract or "
                               "tort, shall not exceed", 1, extract_pages(doc2))
    assert hit2.span == "The Vendor's total liability arising out of this Agreement, whether in contract or tort, " \
                        "shall not exceed"
    assert verify_quote_detail("The Customer shall keep the Vendor's pricing secret and shall not disclos it",
                               1, pages) is None  # no fuzzy across a page break
    assert verify_quote_detail("and shall not disclose it to any third party", 1, pages).page == 2  # all on page 2
