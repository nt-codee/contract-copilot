"""Asymmetric termination is rated from the contract's own clauses (LLM=fake, synthetic text only).

One side may end for any reason on short notice while the reader may end only for a breach: high, with the
consequence stated. A breach-only exit for the reader on a long or renewing term is a finding of its own.
Equal rights for both sides are not raised. Other clause types keep the restraint rules.
"""

from types import SimpleNamespace

from app import calibration as cal, pipeline
from app.models import Finding, LLMReview, LLMVerdict
from app.pipeline import verify_quote_detail
from app.ports import LLMUsage
from pdfgen import make_pdf

ASYMMETRIC = """3. TERM
3.1 This Agreement starts on 1 March 2027 and runs for an initial term of twelve (12) months.

8. TERMINATION
8.1 The Supplier may terminate this Agreement at any time for any reason by giving the Customer fourteen (14) days' written notice.
8.2 The Customer may only terminate this Agreement if the Supplier commits a material breach that is not remedied within thirty (30) days of written notice.
"""

ASYMMETRIC_MS = """9. PENAMATAN
9.1 Penyedia Perkhidmatan boleh menamatkan Perjanjian ini tanpa sebab dengan memberi notis bertulis empat belas (14) hari.
9.2 Pelanggan boleh menamatkan Perjanjian ini sekiranya Penyedia Perkhidmatan melakukan pelanggaran yang tidak diperbaiki dalam masa dua puluh satu (21) hari.
"""

SYMMETRIC = """5. TERM AND TERMINATION
5.1 This Agreement has an initial term of twenty-four (24) months and renews automatically for further periods of twelve (12) months.
5.2 Either party may terminate this Agreement for any reason by giving thirty (30) days' written notice to the other.
5.3 Either party may terminate this Agreement immediately if the other commits a material breach that is not remedied within thirty (30) days.
"""

LOCK_IN = """2. TERM
2.1 This Agreement has an initial term of twenty-four (24) months and renews automatically for further periods of twelve (12) months unless notice is given.

9. ENDING THIS AGREEMENT
9.1 The Customer may terminate this Agreement only if the Provider commits a material breach that is not remedied within thirty (30) days of written notice.
"""

SHORT_TERM = LOCK_IN.replace("twenty-four (24) months and renews automatically for further periods of twelve (12) "
                             "months unless notice is given", "twelve (12) months")


def _finding(quote, risk="medium", clause_type="termination", title="Supplier may end on short notice", fid="f1"):
    return Finding(id=fid, clause_type=clause_type, title=title, risk=risk, quote=quote, page=1,
                   why_risky="The supplier can leave quickly.", standard="s", suggested_ask="a")


def _calibrate(findings, pages, contract_type="service_agreement", consequences=None):
    removed, notes = [], []
    level, _, _ = cal.calibrate_review(findings, consequences or {}, [], "Review.", pages, removed, lambda s: True,
                                       contract_type=contract_type, notes=notes)
    return level, removed, notes


SUPPLIER_81 = ("The Supplier may terminate this Agreement at any time for any reason by giving the Customer "
               "fourteen (14) days' written notice.")


def test_asymmetric_termination_is_read_from_the_contract():
    t = cal.read_termination([ASYMMETRIC])
    assert t.asymmetric and t.other_notice == "14 days"
    assert (t.other_exit.clause, t.reader_breach_exit.clause) == ("8.1", "8.2")
    assert t.other_exit.text == SUPPLIER_81
    t = cal.read_termination([ASYMMETRIC_MS])
    assert t.asymmetric and t.other_notice == "14 days" and t.reader_breach_exit.clause == "9.2"


def test_asymmetric_termination_is_eligible_for_high_with_its_consequence():
    f = _finding(SUPPLIER_81)
    level, removed, notes = _calibrate([f], [ASYMMETRIC])
    assert (f.risk, level) == ("high", "high")
    assert "Clause 8.1 lets the vendor end the agreement for any reason on 14 days' notice" in f.why_risky
    assert "only for the vendor's breach (clause 8.2)" in f.why_risky
    assert "lose the service at short notice yet stay bound" in f.why_risky
    assert not [r for r in removed if r.reason == "unsupported_severity"]
    assert any("asymmetric termination" in n for n in notes)


def test_a_model_high_on_asymmetric_termination_is_not_demoted_without_a_consequence_field():
    f = _finding(SUPPLIER_81, risk="high")
    other = _finding("q", risk="high", clause_type="liability", title="Unlimited liability for you only", fid="f2")
    _, removed, _ = _calibrate([f, other], [ASYMMETRIC])
    assert f.risk == "high"
    # restraint is unchanged for other clause types: a high with no consequence is still medium
    assert other.risk == "medium"
    assert [r.finding_title for r in removed if r.reason == "unsupported_severity"] == [other.title]


def test_the_readers_breach_only_exit_is_a_finding_of_its_own_beside_the_merged_one():
    findings = [_finding(SUPPLIER_81)]
    _calibrate(findings, [ASYMMETRIC])
    assert [(f.clause_type, f.risk, f.clause_ref) for f in findings] == [
        ("termination", "high", None), ("termination", "high", "8.2")]
    assert findings[1].title == cal.LOCK_IN_TITLE


def test_symmetric_mutual_termination_is_not_raised():
    t = cal.read_termination([SYMMETRIC])
    assert not t.asymmetric and not t.lock_in
    findings = [_finding("Either party may terminate this Agreement for any reason by giving thirty (30) days' "
                         "written notice to the other.", title="Mutual 30-day termination")]
    level, removed, notes = _calibrate(findings, [SYMMETRIC])
    assert [f.risk for f in findings] == ["medium"] and level == "medium" and notes == []


def test_breach_only_exit_on_a_24_month_renewing_term_is_flagged():
    findings: list[Finding] = []
    level, _, notes = _calibrate(findings, [LOCK_IN])
    assert level == "high" and len(findings) == 1 and notes
    f = findings[0]
    assert (f.clause_type, f.risk, f.page, f.clause_ref) == ("termination", "high", 1, "9.1")
    assert f.quote == ("The Customer may terminate this Agreement only if the Provider commits a material breach "
                       "that is not remedied within thirty (30) days of written notice.")
    assert verify_quote_detail(f.quote, f.page, [LOCK_IN], set()) is not None  # the quote is on its page
    assert "clause 9.1" in f.why_risky.lower() and "renews automatically" in f.why_risky
    # the same exit on a 12-month term that does not renew is not a lock-in
    assert not cal.read_termination([SHORT_TERM]).lock_in
    assert _calibrate([], [SHORT_TERM])[0] == "low"


def test_a_finding_already_about_the_readers_exit_is_raised_not_duplicated():
    quote = LOCK_IN.split("9.1 ", 1)[1].strip()
    findings = [_finding(quote, title="Exit only for breach")]
    _calibrate(findings, [LOCK_IN])
    assert [(f.risk, f.title) for f in findings] == [("high", "Exit only for breach")]


def test_other_contract_types_are_left_to_their_own_playbooks():
    f = _finding(SUPPLIER_81)
    findings = [f]
    _calibrate(findings, [ASYMMETRIC], contract_type="tenancy")
    assert len(findings) == 1 and f.risk == "medium"
    _calibrate(findings, [ASYMMETRIC], contract_type=None)
    assert len(findings) == 1 and f.risk == "medium"


def test_pipeline_adds_the_lock_in_finding_with_its_playbook_source(services, monkeypatch):
    review = LLMReview(contract_type="service_agreement", language="en", parties=["Provider", "Customer"],
                       verdict=LLMVerdict(overall_risk="low", headline="Looks fine.", summary=["Looks fine."]),
                       findings=[], missing_clauses=[], jurisdiction="my", key_dates=[])
    monkeypatch.setattr(pipeline, "run_agent", lambda *a, **k: SimpleNamespace(
        review=review, usage=LLMUsage(services.llm.model, 0, 0), jurisdiction="my", outlined=False,
        governing_law=None, known={}))
    result = pipeline.review(services, make_pdf([LOCK_IN]), "synthetic.pdf")
    assert result.verdict.overall_risk == "high"
    [f] = result.findings
    assert (f.title, f.risk, f.page) == (cal.LOCK_IN_TITLE, "high", 1) and f.boxes
    assert [c.ref for c in f.citations] == ["service_agreement/termination"]
    assert "added 'Exit only for the vendor's breach'" in result.trace[-1].detail
