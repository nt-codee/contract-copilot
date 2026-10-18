"""Grounding: what the model says about the contract must be in the contract; citations must fit the
claim; asks must fit the governing law and the relationship. Uses the real sample contracts and the
real knowledge library, no model."""

import json

import pytest

from app import grounding
from app.config import REPO_ROOT
from app.knowledge import Knowledge
from app.models import Citation, Finding, LLMCitation, LLMFinding, MissingClause, RemovedClaim
from app.pipeline import add_caveats, verify_findings

SRC = REPO_ROOT / "samples" / "contracts" / "src"
SCHEMA = json.loads((REPO_ROOT / "shared" / "contracts" / "review.schema.json").read_text())


@pytest.fixture(scope="module")
def kb() -> Knowledge:
    return Knowledge.load(REPO_ROOT / "knowledge")


def _pages(sample: str) -> list[str]:
    return [(SRC / f"{sample}.md").read_text(encoding="utf-8")]


def _finding(clause_type="fees_charges", quote="q", why="w", ask="Please fix this.", title="t", **kw) -> Finding:
    return Finding(id="f1", clause_type=clause_type, title=title, risk="medium", quote=quote, page=1,
                   why_risky=why, standard="s", suggested_ask=ask, **kw)


# ---- 1. grounding --------------------------------------------------------------------------------


def test_hp02_compounding_late_fee_ask_is_rewritten_without_it():
    """The graded failure: hp-02 f7 asked to 'remove the daily compounding late fee', but clause 3.3 is a
    flat RM 50 late charge plus 1% a month. The playbook phrase leaked into the ask."""
    f = _finding(quote="The Owner may change any fee or charge in this Agreement by notice on its website.",
                 title="Unilateral power to modify fees",
                 ask="Please list every fee and charge with its amount, remove the daily compounding late fee in "
                     "favour of simple interest on the overdue amount, and limit legal costs to those actually "
                     "and reasonably incurred.")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("hp-02-used-car"), "my", removed)
    assert "compound" not in f.suggested_ask.lower() and "daily" not in f.suggested_ask.lower()
    assert f.suggested_ask == ("Please list every fee and charge with its amount, and limit legal costs to those "
                               "actually and reasonably incurred.")
    assert [(r.kind, r.reason) for r in removed] == [("statement", "ungrounded")]
    assert "daily compounding late fee" in removed[0].text and removed[0].finding_title == f.title


def test_compounding_is_kept_when_the_contract_has_it():
    f = _finding(ask="Please remove the compounding of late interest.")
    pages = ["5.1 Late interest is charged at 2% a month, compounded monthly on the overdue balance."]
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], pages, "my", removed)
    assert f.suggested_ask == "Please remove the compounding of late interest." and removed == []


def test_a_negated_mention_asserts_nothing():
    f = _finding(ask="Please confirm late payment attracts simple interest on the overdue amount, with no compounding.")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("hp-02-used-car"), "my", removed)
    assert removed == [] and f.suggested_ask.endswith("with no compounding.")


def test_why_risky_sentence_with_a_number_not_in_the_contract_is_removed():
    f = _finding(why="The late charge is RM 50.00 for each late instalment. On top of that interest compounds "
                     "to 26.8% a year.")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("hp-02-used-car"), "my", removed)
    assert f.why_risky == "The late charge is RM 50.00 for each late instalment."
    assert removed[0].reason == "ungrounded" and "26.8%" in removed[0].text


def test_numbers_that_follow_from_the_contract_are_grounded():
    # ten-02: rent RM6,800, deposit three (3) months = RM20,400 stated; 1% a month is 12% a year
    pages = _pages("ten-02-shoplot")
    nums = grounding.contract_numbers(pages)
    assert {"6800", "20400", "3", "12"} <= nums
    f = _finding(why="You could lose the RM20,400 deposit, three months of the RM 6,800 rent, see page 2.")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], pages, "my", removed)
    assert removed == []
    assert grounding.ungrounded_numbers("a fee of RM 7,777", nums) == ["7,777"]


def test_all_sentences_removed_leaves_a_neutral_explanation():
    f = _finding(why="The interest compounds daily at 3.9% a month.")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("hp-02-used-car"), "my", removed)
    assert f.why_risky == grounding.FALLBACK_WHY and len(removed) == 1


def test_missing_clause_and_summary_are_checked_for_contract_features():
    m = MissingClause(clause_type="fees_charges", title="Fee schedule", why_it_matters="Fees can change.",
                      suggested_ask="Please add a fee schedule, and remove the daily compounding late fee.")
    removed: list[RemovedClaim] = []
    kept = grounding.ground_review([], [m], ["High risk overall.", "It charges a daily compounding late fee."],
                                   _pages("hp-02-used-car"), "my", removed)
    assert m.suggested_ask == "Please add a fee schedule." and kept == ["High risk overall."]
    assert {r.reason for r in removed} == {"ungrounded"} and len(removed) == 2


# ---- 2. remedy by jurisdiction and relationship ---------------------------------------------------

NON_COMPETE_ASK = ("Please remove the non-compete, or limit it to named direct competitors for no more than 6 "
                   "months, with my salary paid as garden leave for that period.")


def test_my_non_compete_ask_offers_no_shorter_or_paid_fallback():
    """emp-03 f5: a Malaysian freelancer was offered a '6 months, salary as garden leave' fallback. Under
    s 28 a post-contract restraint is void with no reasonableness exception, and a freelancer has no salary."""
    f = _finding(clause_type="non_compete", ask=NON_COMPETE_ASK, title="Post-contract restraint")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("emp-03-freelance-bm"), "my", removed)
    assert f.suggested_ask == "Please remove the non-compete."
    assert [r.reason for r in removed] == ["remedy_scope", "remedy_scope"]


def test_sg_employee_non_compete_fallback_is_kept():
    f = _finding(clause_type="non_compete", ask=NON_COMPETE_ASK)
    pages = ["4.1 The Employer shall pay the Employee a monthly salary of S$6,000."]
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], pages, "sg", removed)
    assert f.suggested_ask == NON_COMPETE_ASK and removed == []


def test_freelancer_gets_no_salary_in_lieu():
    f = _finding(clause_type="notice_period",
                 ask="Please make the notice period the same for both sides, for example 1 month in writing, with "
                     "either side able to pay salary in lieu of notice.")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("emp-03-freelance-bm"), "my", removed)
    assert "salary" not in f.suggested_ask and removed[0].reason == "ungrounded"
    # an employee contract that pays a salary keeps it
    g = _finding(clause_type="notice_period", ask="Please let either side pay salary in lieu of notice.")
    grounding.ground_review([g], [], [], _pages("emp-01-fair"), "my", removed := [])
    assert "salary" in g.suggested_ask and removed == []


def test_other_jurisdiction_alternative_is_removed():
    f = _finding(clause_type="dispute_resolution",
                 ask="Please confirm that disputes will be heard in the courts of Malaysia (or Singapore).")
    removed: list[RemovedClaim] = []
    grounding.ground_review([f], [], [], _pages("hp-02-used-car"), "my", removed)
    assert f.suggested_ask == "Please confirm that disputes will be heard in the courts of Malaysia."
    assert removed[0].reason == "wrong_jurisdiction" and removed[0].kind == "statement"


def test_playbook_no_longer_teaches_the_wrong_remedies(kb):
    nc = kb.playbook["employment/non_compete"].text
    my_ask = next(line for line in nc.splitlines() if line.startswith("Malaysia (employee or freelancer)"))
    assert "delete" in my_ask and "garden leave" not in nc.split("## Suggested ask")[1].split("Singapore")[0]
    assert "my-ca1950-s28" in nc
    assert "compounding late fee" not in kb.playbook["hire_purchase/fees_charges"].text.split("## Suggested ask")[1]
    assert "(or Singapore)" not in kb.playbook["hire_purchase/dispute_resolution"].text
    assert "(or, as a freelancer" not in kb.playbook["employment/ip_ownership"].text


# ---- 3. citation scope ----------------------------------------------------------------------------


def _known(kb, *refs) -> dict[str, list[Citation]]:
    out = {}
    for r in refs:
        e = kb.law_by_id(r)
        out[r] = [Citation(kind="case" if e.kind == "case" else "law", ref=e.id, title=e.title,
                           jurisdiction=e.jurisdiction, excerpt=e.text[:200])]
    return out


def _llm(clause_type, quote, *refs, title="t") -> LLMFinding:
    return LLMFinding(clause_type=clause_type, title=title, risk="high", quote=quote, page=1, why_risky="w.",
                      standard="s", suggested_ask="a", baseline_ref="",
                      citations=[LLMCitation(kind="law", ref=r, excerpt="x") for r in refs], policy_breach="")


HP02 = _pages("hp-02-used-car")
WARRANTY = "The Owner and the Dealer give no warranty about its condition, its mileage, its history"
NO_REBATE = "no rebate of terms charges will be given for early settlement"
REPOSSESS = "The Owner may take possession of the Vehicle immediately and without notice"


def test_citation_outside_the_entrys_scope_is_dropped(kb):
    """hp-02 f3: s 34 (void terms on termination, default interest, entry) was cited for a dealer warranty."""
    removed: list[RemovedClaim] = []
    found, _, dropped_c = verify_findings([_llm("warranty", WARRANTY, "my-hpa1967-s34", title="No warranty")],
                                          HP02, _known(kb, "my-hpa1967-s34"), removed=removed,
                                          jurisdiction="my", law_by_id=kb.law_by_id)
    assert found[0].citations == [] and dropped_c == 1
    assert [(r.kind, r.reason, r.text, r.finding_title) for r in removed] == [
        ("citation", "citation_scope", "my-hpa1967-s34", "No warranty")]


def test_scope_stems_decide_within_a_clause_type(kb):
    """hp-02 f4: s 15 (end the hiring by returning the goods) does not back a no-rebate-on-early-settlement
    claim; on a clause about returning the goods it does."""
    known = _known(kb, "my-hpa1967-s15")
    found, _, n = verify_findings([_llm("early_termination", NO_REBATE, "my-hpa1967-s15")], HP02, known,
                                  jurisdiction="my", law_by_id=kb.law_by_id)
    assert found[0].citations == [] and n == 1
    pages = ["7.1 The Hirer may not return the Vehicle to end this Agreement before the end of the Term."]
    found, _, n = verify_findings([_llm("early_termination", "The Hirer may not return the Vehicle to end this "
                                         "Agreement", "my-hpa1967-s15")], pages, known,
                                  jurisdiction="my", law_by_id=kb.law_by_id)
    assert [c.ref for c in found[0].citations] == ["my-hpa1967-s15"] and n == 0


def test_in_scope_hp_citation_carries_the_2013_reprint_caveat(kb):
    found, _, n = verify_findings([_llm("repossession", REPOSSESS, "my-hpa1967-s34")], HP02,
                                  _known(kb, "my-hpa1967-s34"), jurisdiction="my", law_by_id=kb.law_by_id)
    assert n == 0 and [c.ref for c in found[0].citations] == ["my-hpa1967-s34"]
    add_caveats(found, kb.law_by_id)
    assert found[0].why_risky.startswith("w. Version warning:") and "2013 reprint" in found[0].why_risky
    add_caveats(found, kb.law_by_id)  # idempotent
    assert found[0].why_risky.count("Version warning") == 1


def test_wildcard_scope_follows_the_quote(kb):
    """s 75 (a sum named for breach) backs any clause type whose quote names a sum to pay or forfeit."""
    s75 = kb.law_by_id("my-ca1950-s75")
    assert s75.supports_claim("deposit", "Deposit itu akan dilucuthak sepenuhnya")
    assert s75.supports_claim("non_solicit", "the Employee shall pay the Company RM 50,000 as compensation")
    assert not s75.supports_claim("salary_deductions", "The Employee authorises the Company to deduct costs")
    cimb = kb.law_by_id("my-case-cimb-v-bourke")
    assert cimb.supports_claim("warranty", "Service Credits are the Customer's sole and exclusive remedy")
    assert not cimb.supports_claim("liability", "The Customer's liability to the Vendor is not limited.")


def test_entries_without_supports_default_to_their_clause_types(kb):
    e = kb.law_by_id("my-la1953-s6")
    assert e.supports == () and set(e.scope()) == set(e.clause_types)


def test_named_entries_have_scope_and_caveats(kb):
    for ref in ["my-hpa1967-s34", "my-hpa1967-s15", "my-da1951-s4-s5", "my-ca1950-s29", "my-case-cimb-v-bourke"]:
        e = kb.law_by_id(ref)
        assert e.supports and e.caveat, ref
    assert all(kb.law_by_id(e.id).caveat.startswith("Version warning") for e in kb.law if e.id.startswith("my-hpa1967"))


# ---- schema ---------------------------------------------------------------------------------------


def test_removed_claim_enums_match_schema():
    from typing import get_args
    spec = SCHEMA["$defs"]["RemovedClaim"]["properties"]
    assert list(get_args(RemovedClaim.model_fields["kind"].annotation)) == spec["kind"]["enum"]
    assert list(get_args(RemovedClaim.model_fields["reason"].annotation)) == spec["reason"]["enum"]
    assert {"ungrounded", "citation_scope", "remedy_scope"} <= set(spec["reason"]["enum"])
