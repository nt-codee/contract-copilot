"""Calibration: severity, overall risk and cross-clause wording follow from the contract (LLM=fake, no model)."""

from app import calibration as cal
from app.config import REPO_ROOT
from app.knowledge import Knowledge
from app.models import Citation, Finding, LLMCitation, LLMFinding
from app.pipeline import verify_findings

SRC = REPO_ROOT / "samples" / "contracts" / "src"


def _pages(s):
    return [(SRC / f"{s}.md").read_text(encoding="utf-8")]


def _f(risk="high", title="Unlimited liability for you only", clause_type="liability", why="w.", fid="f1"):
    return Finding(id=fid, clause_type=clause_type, title=title, risk=risk, quote="q", page=1, why_risky=why,
                   standard="s", suggested_ask="a")


def _run(findings, cons, pages, summary=(), headline="h"):
    removed = []
    level, head, kept = cal.calibrate_review(findings, cons, list(summary), headline, pages, removed, lambda s: True)
    return level, head, kept, removed


def test_high_needs_a_consequence_else_medium():
    a, b = _f(fid="f1"), _f(fid="f2", title="Auto-renewal with 90-day notice", clause_type="auto_renewal")
    level, _, _, removed = _run([a, b], {"f1": "Clause 5.1 makes you liable without limit."}, ["x"])
    assert (a.risk, b.risk, level) == ("high", "medium", "high")
    assert [(r.kind, r.reason) for r in removed] == [("statement", "unsupported_severity")]
    # an ungrounded consequence counts as none
    c = _f()
    removed = []
    cal.calibrate_review([c], {"f1": "You could owe RM 9,999,999."}, [], "h", ["x"], removed, lambda s: False)
    assert c.risk == "medium"


def test_overall_risk_is_a_function_of_the_findings():
    assert cal.overall_risk([]) == "low"
    assert cal.overall_risk([_f("low")]) == "low"
    assert cal.overall_risk([_f("medium"), _f("low")]) == "medium"
    assert cal.overall_risk([_f("high", title="No liability cap for the vendor")]) == "medium"  # missing protection
    assert cal.overall_risk([_f("high"), _f("low")]) == "high"


def test_mild_contract_with_unjustified_highs_comes_out_medium_and_headline_follows():
    fs = [_f(fid="f1", title="One-sided late interest"), _f("medium", fid="f2")]
    level, head, _, removed = _run(fs, {}, ["x"], headline="A high-risk contract you should not sign.")
    assert level == "medium" and head == cal.LEVEL_HEADLINE["medium"]
    assert any(r.text.startswith("Headline:") for r in removed)


def test_msa04_renewal_lock_in_is_replaced_by_the_exit_in_clause_10_2():
    """Grader problem: 'locks the clinics into another entire year' though clause 10.2 lets the client
    end on 90 days' notice."""
    f = _f("low", title="Auto-renewal with 60-day notice", clause_type="auto_renewal",
           why="The agreement renews for 12 months unless you give notice. Missing this deadline by a single day "
               "locks the clinics into another entire year of service fees without prior reminder.")
    _, _, kept, removed = _run([f], {}, _pages("msa-04-scanned"),
                               summary=["Miss the window and you are locked in for another year."])
    assert "entire year" not in f.why_risky and "Clause 10.2 lets you end the agreement on 90 days' notice" in f.why_risky
    assert f.why_risky.startswith("The agreement renews for 12 months")  # the first sentence stays
    assert kept == [] and len(removed) == 2


def test_a_conditional_exit_is_stated_with_its_condition():
    f = _f("medium", title="Renewal lock-in", clause_type="term", why="You are stuck for another 12-month term.")
    _run([f], {}, _pages("ten-02-shoplot"))
    assert "on 3 months' notice after the first 24 months" in f.why_risky


def test_no_exit_means_the_lock_in_stays():
    f = _f("high", title="Two-year auto-renewal", clause_type="auto_renewal", why="This locks you in for two years.")
    _run([f], {"f1": "c"}, _pages("msa-09-bm-cleaning"))  # 7.1 needs a breach; 7.2 is the vendor's
    assert f.why_risky == "This locks you in for two years."


def test_msa09_no_indemnity_is_not_presented_as_no_remedy():
    """Grader problem: 'disclaims all responsibility ... leaves you to bear the cost' though clause 6.2 caps
    (and so keeps) the vendor's liability."""
    f = _f("medium", title="No compensation for property damage",
           why="The vendor disclaims all responsibility for damage to your office property. This leaves you to "
               "bear the cost of on-site damage or theft.")
    _, _, kept, _ = _run([f], {}, _pages("msa-09-bm-cleaning"), summary=["The vendor disclaims all property damage."])
    assert "disclaims all" not in f.why_risky and "bear the cost" not in f.why_risky
    assert f.why_risky.startswith("Clause 6.2 still caps the vendor's overall liability") and kept == []


def test_compliance_certificate_is_not_given():
    _, head, kept, _ = _run([], {}, ["x"], summary=["The contract complies with the Employment Act 1955.", "Fair."],
                            headline="A balanced contract complying with the Employment Act 1955.")
    assert kept == ["Fair."] and head == cal.LEVEL_HEADLINE["low"]


# ---- citations and policy breaches, through verify_findings -----------------------------------------


def test_employee_statute_on_a_freelance_agreement_needs_the_condition():
    """Grader problem: emp-03 f4 cited s 12 (contract of service) for a freelancer without saying so."""
    kb = Knowledge.load(REPO_ROOT / "knowledge")
    pages = _pages("emp-03-freelance-bm")
    e = kb.law_by_id("my-ea1955-s12")
    known = {e.id: [Citation(kind="law", ref=e.id, title=e.title, jurisdiction="my", excerpt=e.text[:200])]}
    quote = "Freelancer boleh menamatkan Perjanjian ini dengan memberi notis bertulis tiga puluh (30) hari"

    def run(why):
        removed = []
        found, _, _ = verify_findings([LLMFinding(
            clause_type="notice_period", title="Unequal notice", risk="medium", quote=quote, page=1, why_risky=why,
            standard="s", suggested_ask="a", baseline_ref="", policy_breach="",
            citations=[LLMCitation(kind="law", ref=e.id, excerpt="x")])], pages, known, removed=removed,
            jurisdiction="my", law_by_id=kb.law_by_id)
        return found[0].citations, removed

    cites, removed = run("The client can end at once while you must give 30 days.")
    assert cites == [] and [(r.reason, r.text) for r in removed] == [("citation_scope", "my-ea1955-s12")]
    cites, removed = run("The client can end at once. If you are in substance an employee, s 12 requires equal notice.")
    assert [c.ref for c in cites] == ["my-ea1955-s12"] and removed == []
    assert e.applies_to == "employee"


def test_prompt_states_the_rules():
    from app import prompts
    assert "consequence" in prompts.AGENT_SYSTEM and "recomputed" in prompts.AGENT_SYSTEM
    assert "copied exactly as its excerpt" in prompts.AGENT_SYSTEM
    assert "CROSS-CLAUSE" in prompts.REVIEW_RULES and "contract of service" in prompts.REVIEW_RULES
