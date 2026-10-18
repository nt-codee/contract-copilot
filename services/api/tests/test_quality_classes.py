"""The five defect classes from the fixed-eight quality grade, with synthetic inputs only.

1. a legal test that contradicts the library entry (restraint of trade framed as reasonableness under MY s 28)
2. notice-deadline arithmetic off by one ("N days before the end of the term")
3. overstated loss of rights ("strips you of all legal recourse", "no legal right")
4. compliance reassurance ("follows the Employment Act")
5. key dates the contract does not state, shown as if it did
plus the general legal-outcome rule (firm "void" only when a cited entry backs it) and verdict consistency.
"""

from datetime import date

import pytest

from app import dates, restraint
from app.calibration import LEVEL_HEADLINE, calibrate_review
from app.config import REPO_ROOT
from app.knowledge import LawEntry, load_law
from app.models import Citation, Finding, LLMKeyDate, MissingClause
from app.pipeline import build_key_dates

# ---- helpers ------------------------------------------------------------------------------------

REAL_LAW = {e.id: e for e in load_law(REPO_ROOT / "knowledge" / "law")}
S28 = REAL_LAW["my-ca1950-s28"]

EXCLUSION_CASE = LawEntry(
    id="my-case-synthetic-exclusion", jurisdiction="my", kind="case", title="Synthetic exclusion case",
    citation="[2099] 1 XX 1", clause_types=("liability",), source_url="https://example.org/x",
    text="An exclusion that leaves no remedy at all for a proven breach would be void.", plain="",
    asserts=(),  # the entry's text names 'void', but its frontmatter allows no firm outcome
    caveat="The case struck down an exclusion that left no remedy at all; it did not decide every exclusion is void.")
PENALTY_ACT = LawEntry(
    id="sg-synthetic-penalty", jurisdiction="sg", kind="statute", title="Synthetic penalty rule",
    citation="Synthetic Act s 1", clause_types=("payment",), source_url="https://example.org/y",
    text="A sum stated as a penalty for breach shall not be enforced beyond the loss caused.", plain="")
LAW = {e.id: e for e in (S28, EXCLUSION_CASE, PENALTY_ACT)}


def law_by_id(ref: str):
    return LAW.get(ref)


def cite(entry: LawEntry) -> Citation:
    return Citation(kind="law" if entry.kind == "statute" else "case", ref=entry.id, title=entry.title,
                    jurisdiction=entry.jurisdiction, excerpt=entry.text[:80])


def finding(clause_type, why, *, cites=(), clause_ref=None, title="Synthetic finding", standard="A balanced term."):
    return Finding(id="f1", clause_type=clause_type, title=title, risk="medium", quote="q" * 20, page=1,
                   clause_ref=clause_ref, why_risky=why, standard=standard, suggested_ask="Please change it.",
                   citations=[cite(e) for e in cites])


def run(findings, summary=(), headline="Some points are worth raising.", jurisdiction="my", missing=()):
    removed = []
    kept, head = restraint.restrain_review(list(findings), list(missing), list(summary), headline, "medium",
                                           jurisdiction, list(LAW.values()), law_by_id, removed)
    return kept, head, removed


# ---- 1. a legal test the library entry rejects -----------------------------------------------------


def test_reasonableness_test_under_my_restraint_rule_is_removed_from_finding():
    f = finding("non_solicit", "The supplier bars you from hiring its staff for three years. Courts here strike "
                "down unreasonable restraints of this kind. Section 28 has no general reasonableness exception.",
                cites=[S28])
    _, _, removed = run([f])
    assert "unreasonable restraints" not in f.why_risky
    assert "no general reasonableness exception" in f.why_risky  # the source's own rule stays
    assert any(r.reason == "citation_scope" and "unreasonable restraints" in r.text for r in removed)


def test_contradiction_checked_on_uncited_finding_of_a_covered_clause_type_and_on_the_summary():
    f = finding("non_compete", "A reasonable restriction like this would be upheld by a court. Ask to delete it.")
    kept, _, removed = run([f], summary=["A reasonable restriction of this kind is enforceable in Malaysia.",
                                         "Payment is due in 45 days."])
    assert "upheld" not in f.why_risky and "Ask to delete it." in f.why_risky
    assert kept == ["Payment is due in 45 days."]
    assert sum(r.text.startswith("Summary:") for r in removed) == 1


def test_contradiction_rule_is_jurisdiction_scoped_and_ignores_negated_statements():
    sg = finding("non_compete", "A restriction that is reasonable in scope may be upheld.")
    run([sg], jurisdiction="sg")
    assert "upheld" in sg.why_risky  # Singapore applies a reasonableness test; the MY entry does not speak
    neg = finding("non_compete", "Unlike Singapore, a reasonable restraint is not enforceable here either.")
    _, _, removed = run([neg])
    # kept (a negated statement is not the rejected proposition); the uncited outcome is only hedged
    assert neg.why_risky == "Unlike Singapore, a reasonable restraint may not be enforceable here either."


def test_real_s28_entry_carries_the_frontmatter():
    assert S28.asserts == (("non_compete", ("void",)),)
    assert ("unreasonabl", "restrain") in S28.contradicts
    assert REAL_LAW["my-case-cimb-v-bourke"].asserts == ()
    assert REAL_LAW["my-ca1950-s75"].asserts is None  # absent frontmatter: decided from the entry's text


# ---- general rule: legal outcomes need a backing source ---------------------------------------------


def test_backed_void_claim_is_kept_and_unbacked_one_is_hedged():
    nc = finding("non_compete", "The post-contract non-compete is void under section 28.", cites=[S28])
    ns = finding("non_solicit", "The non-solicitation clause is void under section 28.", cites=[S28])
    ex = finding("liability", "This exclusion is void because it leaves no remedy.", cites=[EXCLUSION_CASE])
    bare = finding("payment", "The late fee is unenforceable.")
    run([nc, ns, ex, bare])
    assert nc.why_risky == "The post-contract non-compete is void under section 28."
    assert ns.why_risky.startswith("The non-solicitation clause may be void")  # s 28 asserts void for non-competes only
    assert ex.why_risky.startswith("This exclusion may be void")
    assert bare.why_risky == "The late fee may be unenforceable."


def test_entry_text_backs_an_outcome_when_no_asserts_frontmatter():
    f = finding("payment", "The fixed sum cannot be enforced beyond your actual loss.", cites=[PENALTY_ACT])
    run([f], jurisdiction="sg")
    assert f.why_risky == "The fixed sum cannot be enforced beyond your actual loss."


def test_unrewritable_outcome_is_removed_and_hedged_or_attributed_ones_are_left():
    f = finding("other", "Void terms like this still cost money to fight. It may be unenforceable. Clause 9 says a "
                "late notice is void. The vendor calls it \"null and void\" in the heading.")
    _, _, removed = run([f])
    assert "cost money" not in f.why_risky
    assert "It may be unenforceable." in f.why_risky and "Clause 9 says a late notice is void." in f.why_risky
    assert '"null and void"' in f.why_risky
    assert [r.text for r in removed] == ["Explanation: Void terms like this still cost money to fight."]


def test_statute_breach_claims_are_hedged():
    f = finding("working_hours", "This schedule breaches the Synthetic Hours Act. It also fails to comply with the "
                "rest-day rule. The Synthetic Act renders all such waivers void.")
    run([f])
    assert "may breach the Synthetic Hours Act" in f.why_risky
    assert "may not comply with the rest-day rule" in f.why_risky
    assert "The Synthetic Act may make all such waivers void." in f.why_risky


def test_library_caveat_words_are_left_untouched():
    f = finding("liability", "The cap is low. " + EXCLUSION_CASE.caveat)
    run([f])
    assert f.why_risky == "The cap is low. " + EXCLUSION_CASE.caveat


# ---- 3. absolute loss of rights ----------------------------------------------------------------------


def test_absolute_loss_of_recourse_is_rewritten_to_what_the_clause_attempts():
    f = finding("warranty", "The 'sold as is' clause strips you of all legal recourse if the goods prove faulty.")
    _, _, removed = run([f])
    assert f.why_risky == "The 'sold as is' clause tries to limit your legal recourse if the goods prove faulty."
    assert removed[0].reason == "citation_scope" and "strips you" in removed[0].text


@pytest.mark.parametrize("before,after", [
    ("You have no legal right to cancel the order.", "You have no express right in this contract to cancel the order."),
    ("This leaves you with no remedy at all.", "This leaves you with only limited remedies under this contract."),
    ("You lose all rights against the seller.", "You may lose some of your rights against the seller."),
    ("Only the seller may cancel, depriving the buyer of any right to end it.",
     "Only the seller may cancel, limiting the buyer's right to end it."),
])
def test_other_absolute_forms(before, after):
    f = finding("warranty", before)
    run([f])
    assert f.why_risky == after


@pytest.mark.parametrize("text", [
    "Under this agreement you have no remedy for late delivery.",  # scoped to the contract's own terms
    "This could leave you with no remedy.",  # already hedged
    "Clause 4 removes your right to terminate early.",  # a contractual right, stated as the clause does
])
def test_scoped_or_hedged_statements_are_left(text):
    f = finding("warranty", text)
    run([f])
    assert f.why_risky == text


def test_absolute_wording_in_summary_and_headline_is_softened():
    f = finding("warranty", "The seller gives no warranty.")
    kept, head, _ = run([f], summary=["The seller disclaims everything and strips you of legal remedies."],
                        headline="This deal deprives you of all legal protection.")
    assert kept == ["The seller disclaims everything and tries to limit your legal remedies."]
    assert head == "This deal tries to limit your legal protection."


# ---- 4. compliance reassurance -----------------------------------------------------------------------


def test_compliance_claims_are_removed_everywhere():
    f = finding("leave", "Leave is generous. The leave terms closely follow the Synthetic Employment Act 2001.")
    m = MissingClause(clause_type="leave", title="Sick leave", why_it_matters="It is consistent with the Synthetic Act.",
                      suggested_ask="Please add sick leave.")
    kept, head, removed = run([f], missing=[m], summary=["The hours and overtime terms follow the Synthetic Labour "
                                                         "Act.", "Notice is mutual."],
                              headline="The contract complies with the Synthetic Labour Act.")
    assert f.why_risky == "Leave is generous."
    assert m.why_it_matters == "The contract has no clause on sick leave."
    assert kept == ["Notice is mutual."]
    assert head == LEVEL_HEADLINE["medium"]
    assert len(removed) == 4


def test_calibration_also_drops_a_follows_the_act_summary():
    removed = []
    _, _, kept = calibrate_review([], {}, ["The terms closely follow the Synthetic Employment Act 2001 on hours.",
                                           "Notice is mutual."], "Fair terms.", ["x"], removed, lambda s: True)
    assert kept == ["Notice is mutual."]


def test_ordinary_sentences_are_not_treated_as_compliance():
    f = finding("leave", "Ask for leave that follows the balanced position. This follows from clause 4.")
    run([f])
    assert f.why_risky == "Ask for leave that follows the balanced position. This follows from clause 4."


# ---- 3 (consistency): the verdict may not be firmer than the findings ---------------------------------


def test_summary_outcome_backed_only_by_the_finding_it_names():
    nc = finding("non_compete", "The non-compete may be void.", cites=[S28], clause_ref="5.1")
    other = finding("payment", "The fee is high.", clause_ref="7.2")
    kept, _, _ = run([nc, other], summary=["The restriction in clause 5.1 is void.", "The fee in clause 7.2 is void.",
                                           "The non-compete is void under Malaysian law."])
    assert kept[0] == "The restriction in clause 5.1 is void."
    assert kept[1] == "The fee in clause 7.2 may be void."
    assert kept[2] == "The non-compete is void under Malaysian law."  # names no clause: a backed finding exists


# ---- 2 and 5. key dates: arithmetic in code, and stated / calculated / inferred ------------------------

CONTRACT = [
    "1. PARTIES\nThis agreement is dated 15 Ogos 2027 between Synthetic Supplies Sdn Bhd and the Customer.\n"
    "2.1 The Services start on the 1st day of March, 2027 for an initial term of twelve (12) months.\n"
    "2.2 It renews for a further 12 months unless the Customer gives notice at least forty-five (45) days "
    "before the expiry of the initial term.\n2.3 Fees are payable on the 5th day of each month.\n"
    "2.4 A price review takes place in March 2029.\n2.5 Handover is on June 1, 2027 or 01/07/2027 at the latest.",
]


def kd(label, base, term=0, offset=0, unit="days", direction="before", quote="", page=1, kind="other"):
    return LLMKeyDate(label=label, kind=kind, page=page, base_date=base, term_months=term, offset=offset, unit=unit,
                      direction=direction, source_quote=quote, rule="r", finding_title="")


def test_deadline_counts_back_from_the_last_day_of_the_term():
    out = build_key_dates([kd("Notice deadline", "2027-03-01", 12, 45, quote="gives notice at least forty-five (45) "
                              "days before the expiry of the initial term", kind="notice_deadline"),
                           kd("Term ends", "2027-03-01", 12, kind="end")], [], 1, CONTRACT)
    by = {k.label: k for k in out}
    assert by["Term ends"].date == date(2028, 2, 29)  # leap year: the term's last day
    assert by["Notice deadline"].date == date(2028, 1, 15)  # 45 days before 29 Feb 2028, not before 1 Mar
    assert by["Notice deadline"].basis == "calculated" and by["Term ends"].basis == "calculated"


@pytest.mark.parametrize("pieces,expected", [
    (("2029-11-30", 0, 3, "months", "before"), date(2029, 8, 30)),  # months, not '91 days'
    (("2026-09-01", 12, 60, "days", "before"), date(2027, 7, 2)),
    (("2026-01-31", 0, 1, "months", "after"), date(2026, 2, 28)),
    (("2026-01-01", 0, 2, "weeks", "after"), date(2026, 1, 15)),
    (("2026-01-01", 24, 1, "years", "before"), date(2026, 12, 31)),
    (("2026-01-01", 0, -10, "days", "before"), date(2026, 1, 11)),  # a folded sign is unfolded
    (("", 12, 0, "days", "before"), None),
])
def test_resolve_arithmetic(pieces, expected):
    assert dates.resolve(*pieces) == expected


def test_stated_dates_in_common_formats():
    found = dates.stated_dates(CONTRACT[0])
    assert {date(2027, 8, 15), date(2027, 3, 1), date(2027, 6, 1), date(2027, 7, 1)} <= found
    assert date(2029, 3, 1) not in found  # "March 2029" names no day


def test_dates_the_contract_does_not_state_are_labelled_estimates():
    out = build_key_dates([
        kd("First fee due", "2027-03-01", 0, 1, "months", "after", quote="Fees are payable on the 5th day of each "
           "month", kind="payment"),
        kd("Price review", "2029-03-01", kind="price_change"),
        kd("Handover", "2027-06-01"),
        kd("Bad quote", "2027-03-01", 0, 45, quote="notice at least forty-five (45) days before renewal"),
    ], [], 1, CONTRACT)
    by = {k.label.removesuffix(dates.ESTIMATE_LABEL): k for k in out}
    for name in ("First fee due", "Price review", "Bad quote"):
        k = by[name]
        assert k.basis == "inferred" and k.label.endswith("(estimate)") and k.rule.startswith("Not stated"), name
    assert by["Handover"].basis == "stated" and by["Handover"].label == "Handover"


def test_every_asserts_and_contradicts_frontmatter_in_the_library_is_well_formed():
    for e in REAL_LAW.values():
        for clause, families in e.asserts or ():
            assert clause == "*" or clause in e.clause_types, e.id
            assert families and set(families) <= set(restraint.OUTCOMES), e.id
        for stems in e.contradicts:
            assert len(stems) >= 2 and all(s.isalpha() for s in stems), e.id
