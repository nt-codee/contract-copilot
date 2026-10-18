"""Focused regressions for evidence, reciprocal silence and cross-clause/legal explanations.

Synthetic variations exercise the pure checks and their existing verifier/grounding/calibration
call contract. No recorded model result is rewritten and no model/network call is made.
"""

import pytest

from app import calibration, grounding, prompts
from app.config import REPO_ROOT
from app.knowledge import Knowledge
from app.models import Citation, Finding, LLMCitation, LLMFinding
from app.pipeline import verify_findings


def finding(quote="q", why="The clause is one-sided.", risk="high", clause_type="termination", **kwargs):
    return Finding(id="f1", title="One-sided term", clause_type=clause_type, risk=risk, quote=quote,
                   page=1, why_risky=why, standard="A balanced term is proposed.",
                   suggested_ask="Please make the term mutual.", **kwargs)


def calibrate(f, pages, consequences=None, grounded=lambda s: True, summary=()):
    removed = []
    result = calibration.calibrate_review([f], consequences or {}, list(summary), "Review of the draft.",
                                          pages, removed, grounded)
    return result, removed


@pytest.mark.parametrize("quote", [
    "The Customer is not responsible for paying any fee for work in progress on cancellation.",
    "Pelanggan tidak bertanggungjawab membayar apa-apa yuran bagi kerja yang sedang dijalankan.",
    "No rebate of finance charges will be given on early completion.",
    "Tiada rebat akan diberikan untuk penyelesaian awal.",
    "The Tenant shall pay all remaining rent on an early exit.",
])
@pytest.mark.parametrize("auxiliary", ["", "You lose RM 987,654."])
def test_explicit_quoted_consequence_survives_missing_or_rejected_auxiliary(quote, auxiliary):
    f = finding(quote=quote, why="Read the quoted payment restriction.")
    _, removed = calibrate(f, ["8.4 " + quote], {"f1": auxiliary}, grounded=lambda s: False)
    assert f.risk == "high"
    assert f.quote == quote and f.why_risky == "Read the quoted payment restriction."
    assert not any(r.reason == "unsupported_severity" for r in removed)


@pytest.mark.parametrize("auxiliary", ["", "The customer will lose an unstated amount."])
def test_uncertain_evidence_is_not_reported_as_absence(auxiliary):
    quote = "The parties will discuss complaints at a monthly meeting."
    f = finding(quote=quote)
    _, removed = calibrate(f, [quote], {"f1": auxiliary}, grounded=lambda s: False)
    assert f.risk == "medium"  # retain conservative existing policy for unresolved severity
    note = next(r.text for r in removed if r.reason == "unsupported_severity")
    assert "could not be verified" in note
    assert "its consequence is not in the contract" not in note
    assert f.quote == quote


@pytest.mark.parametrize("quote", [
    "Customer shall pay all undisputed invoices within 30 days.",
    "The Customer must pay all fees for services received within 30 days.",
    "Pelanggan hendaklah membayar semua invois yang tidak dipertikaikan dalam masa 30 hari.",
    "The Customer shall pay the remaining balance on the agreed maturity date.",
    "The Tenant shall pay all remaining rent when due. Either party may terminate on notice.",
])
def test_verified_routine_payment_does_not_rescue_high_without_auxiliary_consequence(quote):
    f = finding(quote=quote, clause_type="payment")
    (_, _, _), removed = calibrate(f, ["2.3 " + quote])
    assert f.risk == "medium"
    assert f.quote == quote
    note = next(r.text for r in removed if r.reason == "unsupported_severity")
    assert "no separate consequence evidence was supplied" in note
    assert "severity could not be verified" in note
    assert "its consequence is not in the contract" not in note


@pytest.mark.parametrize("quote", [
    "The Tenant shall pay all remaining rent on an early exit.",
    "Upon termination, the Customer must pay the remaining balance.",
    "The Hirer shall pay the balance of future instalments if the agreement is terminated.",
    "Jika Penyewa menamatkan perjanjian, Penyewa hendaklah membayar semua baki sewa.",
    "Penyewa hendaklah membayar baki sewa bagi penamatan awal.",
    "The Hirer cannot return the goods to end the agreement.",
    "The Customer may not terminate this agreement.",
    "Penerima tidak boleh menamatkan Perjanjian ini.",
])
def test_explicit_exit_payment_or_lost_right_still_rescues_existing_high(quote):
    f = finding(quote=quote)
    _, removed = calibrate(f, ["8.2 " + quote])
    assert f.risk == "high" and f.quote == quote
    assert not any(r.reason == "unsupported_severity" for r in removed)


def test_unverified_quote_cannot_supply_consequence_and_medium_is_not_promoted():
    f = finding(quote="No rebate will be given.")
    calibrate(f, ["The amount will be discussed."])
    assert f.risk == "medium"
    g = finding(quote="No rebate will be given.", risk="medium")
    calibrate(g, [g.quote])
    assert g.risk == "medium"
    h = finding(quote="No rebate formula is specified in this agreement.")
    calibrate(h, [h.quote])
    assert h.risk == "medium"  # a missing formula is not a quoted prohibition on rebates


@pytest.mark.parametrize(("source", "claim"), [
    ("The Recipient shall not assign without consent.",
     "The discloser can transfer rights and obligations freely."),
    ("Penerima tidak boleh menyerahhakkan haknya tanpa persetujuan Pihak Pendedah.",
     "Pihak Pendedah boleh memindahkan hak dan obligasi tanpa sekatan."),
    ("The Discloser has no confidentiality duty under this Agreement.",
     "The discloser is legally free to use or disclose data without restriction."),
    ("The Discloser may assign its rights without consent.",
     "The discloser may freely transfer its rights and obligations."),
    ("The Recipient may transfer its rights and obligations without consent.",
     "The discloser may freely transfer rights and obligations."),
    ("The Discloser may transfer rights and obligations only with the Recipient's consent.",
     "The discloser may freely transfer rights and obligations without consent."),
    ("The Discloser may use the data only for the Purpose.",
     "The discloser may freely use the data without restriction."),
])
def test_reciprocal_silence_and_rights_only_permission_do_not_establish_duty_transfer(source, claim):
    assert grounding.unsupported_permission(claim, source)
    f = finding(quote=source, why=claim + " Ask for a reciprocal term.", clause_type="assignment")
    removed = []
    summary = grounding.ground_review([f], [], [claim], [source], "my", removed)
    assert f.why_risky == "Ask for a reciprocal term."
    assert summary == []
    assert len(removed) == 2
    assert f.quote == source and f.suggested_ask == "Please make the term mutual."


@pytest.mark.parametrize(("source", "claim"), [
    ("The Discloser may assign its rights without consent.",
     "The discloser may freely assign rights."),
    ("The Discloser may transfer rights and obligations without consent.",
     "The discloser may freely transfer rights and obligations under this agreement."),
    ("Pihak Pendedah boleh memindahkan hak dan obligasi tanpa persetujuan.",
     "Pihak Pendedah boleh memindahkan hak dan obligasi tanpa sekatan di bawah perjanjian ini."),
    ("The Discloser may use the information for any purpose.",
     "The discloser may freely use the information under this agreement."),
    ("Only the recipient promises confidentiality.",
     "The clause does not state a matching confidentiality promise by the discloser."),
    ("The recipient needs consent.", "The discloser is not legally free to transfer obligations."),
    ("The recipient needs consent.", "The discloser cannot freely transfer obligations."),
])
def test_explicit_permissions_and_limited_descriptions_are_not_deleted(source, claim):
    assert not grounding.unsupported_permission(claim, source)


@pytest.mark.parametrize("break_clause", [
    "5.8 After 18 months, the Tenant may end this agreement on 2 months' written notice.",
    "5.8 Selepas lapan belas (18) bulan, Penyewa boleh menamatkan perjanjian dengan notis dua (2) bulan.",
])
def test_remaining_rent_assertion_carries_break_exception_in_primary_sentence(break_clause):
    quote = "The Tenant shall pay all remaining rent if it leaves early in any other way."
    f = finding(quote=quote, clause_type="early_termination", risk="medium",
                why="Leaving early obligates you to pay all remaining rent. The cost could be large.")
    (_, _, summary), removed = calibrate(f, [break_clause + "\n5.9 " + quote],
                                         summary=["Exiting early requires all remaining rent."])
    first = f.why_risky.split(". ")[0]
    assert "outside clause 5.8" in first
    assert "2 months' notice after the first 18 months" in f.why_risky
    assert summary and "outside clause 5.8" in summary[0]
    assert f.why_risky.endswith("The cost could be large.")
    assert len(removed) == 2


def test_already_qualified_exit_and_no_convenience_exit_are_preserved():
    pages = ["4.2 After twelve (12) months, the Tenant may end on two (2) months' notice."]
    text = "Outside clause 4.2, leaving early requires all remaining rent."
    f = finding(risk="medium", clause_type="early_termination", why=text)
    calibrate(f, pages)
    assert f.why_risky == text
    g = finding(risk="medium", clause_type="early_termination", why="Leaving early requires all remaining rent.")
    calibrate(g, ["The Tenant may terminate only for uncured breach on 30 days' notice."])
    assert g.why_risky == "Leaving early requires all remaining rent."


def test_cited_reasonable_compensation_rule_does_not_become_an_actual_loss_prerequisite():
    kb = Knowledge.load(REPO_ROOT / "knowledge")
    law = kb.law_by_id("my-ca1950-s75")
    assert "whether or not actual damage or loss is proved" in law.text
    quote = "The Tenant shall pay all remaining rent as compensation for early termination."
    raw = LLMFinding(clause_type="early_termination", title="Remaining rent on exit", risk="high",
                     quote=quote, page=1, why_risky="Courts allow only reasonable compensation for actual loss.",
                     standard="Please agree a modest break fee.", suggested_ask="Please cap the fee at two months.",
                     baseline_ref="", policy_breach="",
                     citations=[LLMCitation(kind="law", ref=law.id, excerpt="model words")])
    # Use the actual existing verified-citation boundary, not fabricated model-provided authority text.
    # Match the agent's clipped verified excerpt rather than assuming access to the whole law body.
    from app.agent import CITATION_EXCERPT, clip
    known = {law.id: [Citation(kind="law", ref=law.id, title=law.title,
                              excerpt=clip(law.text, CITATION_EXCERPT), jurisdiction="my")]}
    fs, dropped, dc = verify_findings([raw], [quote], known, jurisdiction="my", law_by_id=kb.law_by_id)
    assert dropped == dc == 0
    removed = []
    grounding.ground_review(fs, [], [], [quote], "my", removed)
    assert "even without proof of actual loss" in fs[0].why_risky
    assert "the full amount is not automatic" in fs[0].why_risky
    assert removed[0].reason == "citation_scope"
    assert fs[0].quote == quote


def test_loss_proof_negative_controls():
    cited = "Reasonable compensation is available whether or not actual damage or loss is proved."
    assert grounding.qualify_loss_proof("Actual loss need not be proved.", cited) is None
    assert grounding.qualify_loss_proof("Ordinary damages require proof of actual loss.", "Ordinary damages.") is None
    assert grounding.qualify_loss_proof("The tenant must prove actual loss.", "") is None


def test_review_prompt_requires_actor_scope_and_primary_exceptions():
    text = prompts.agent_system(["An agreement."], 16)
    assert "SILENCE IS NOT PERMISSION" in text
    assert "Assignment of rights and transfer of obligations are distinct" in text
    assert "applicable exception in the primary assertion" in text
    assert "proof of actual loss are different questions" in text
    assert "material source-version limitation in the claim itself" in text
    assert "playbook's balanced term is a proposed negotiation position" in text
