"""Short, scannable findings: titles and first sentences (prompt rules), and the clause number
(`clause_ref`), read off the page deterministically when the model omits it."""

from app import prompts
from app.models import LLMFinding
from app.pipeline import clause_number, pick_clause_ref, verify_findings
from pdfgen import PAGES

TEXT = ("4. PAYMENT\n"
        "4.1 The Customer shall pay each invoice within ninety (90) days of receipt.\n"
        "4.2 Late payments bear interest at 1% a month for\n"
        "12 months after the due date, and the Vendor may suspend the Services.\n"
        "5. LIABILITY\n"
        "The Vendor is not liable for indirect loss.\n")


def _f(quote: str, page: int, clause_ref: str = "") -> LLMFinding:
    return LLMFinding(clause_type="liability", title="Uncapped liability for you only", risk="high", quote=quote,
                      page=page, clause_ref=clause_ref, why_risky="w", standard="s", suggested_ask="a",
                      baseline_ref="", citations=[], policy_breach="")


def test_clause_number_is_the_last_numbered_line_before_the_quote():
    assert clause_number(TEXT, TEXT.index("The Customer shall pay")) == "4.1"
    # a wrapped line starting "12 months ..." is not clause 12
    assert clause_number(TEXT, TEXT.index("the Vendor may suspend")) == "4.2"
    # an unnumbered paragraph under a numbered heading takes the heading's number
    assert clause_number(TEXT, TEXT.index("The Vendor is not liable")) == "5"
    assert clause_number("The parties agree as follows.\nThe Vendor may end it.", 30) is None
    assert clause_number("Fasal 3 (a) Penyewa hendaklah membayar sewa.", 12) == "3"


def test_the_page_wins_and_the_model_may_only_refine_it():
    off = TEXT.index("The Customer shall pay")
    assert pick_clause_ref("", TEXT, off, [TEXT]) == "4.1"  # omitted: filled from the page
    assert pick_clause_ref("Clause 4.1(b)", TEXT, off, [TEXT]) == "4.1(b)"  # a finer reference is kept
    assert pick_clause_ref("7.4", TEXT, off, [TEXT]) == "4.1"  # a contradicting one is not
    plain = "The Vendor may end this agreement at any time."
    assert pick_clause_ref("", plain, 0, [plain]) is None  # the contract numbers nothing
    assert pick_clause_ref("9.9", plain, 0, [plain]) is None  # and the model's number is not in it
    assert pick_clause_ref("Schedule 2", plain, 0, [plain]) is None


def test_verified_findings_carry_the_clause_number():
    found, dropped, _ = verify_findings([
        _f("The Customer shall be liable without limit for any loss", 2),
        _f("This Agreement renews automatically for successive 12 month terms", 3),  # really page 2
        _f("This Agreement is governed by the laws of the State of Delaware", 3, clause_ref="Clause 9.1")], PAGES)
    assert dropped == 0
    assert {f.quote[:30]: f.clause_ref for f in found} == {
        "The Customer shall be liable w": "5.1", "This Agreement renews automati": "6.1",
        "This Agreement is governed by ": "9.1"}


def test_fake_review_fills_clause_ref(client, pdf_bytes):
    body = client.post("/api/reviews", files={"file": ("c.pdf", pdf_bytes, "application/pdf")}).json()
    assert body["findings"] and all(f.get("clause_ref") for f in body["findings"])
    draft = client.post(f"/api/reviews/{body['id']}/negotiation").json()
    assert draft["asks"]


def test_prompt_and_schema_ask_for_short_titles_and_a_summary_first_sentence():
    text = prompts.AGENT_SYSTEM
    assert "at most 8 words, a noun phrase" in text and "Never a sentence" in text
    assert "FIRST sentence must stand\n   alone as a one-line summary" in text and "at most 25 words" in text
    assert "clause_ref" in text
    props = LLMFinding.model_json_schema()["properties"]
    assert "8 words" in props["title"]["description"] and "25 words" in props["why_risky"]["description"]
    assert LLMFinding.model_fields["clause_ref"].default == ""  # older responses without it still parse
