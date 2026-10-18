"""Calibration: severity and certainty follow from the contract, not from the model's tone.

- A high finding must name a concrete, grounded consequence (LLMFinding.consequence), or quote an
  explicit payment/right-loss consequence. Otherwise severity is uncertain and it is rated medium;
  that does not establish that a consequence is absent. Overall risk is a function of the findings:
  high only if a high finding is about a term
  the contract HAS (not merely a missing protection); medium if any medium or high; else low.
- Cross-clause checks on the model's wording, all deterministic:
  * a "locks you in" claim is replaced when the reader has a convenience exit elsewhere (clause and
    notice period quoted from the contract);
  * a "no remedy at all" claim is replaced when the contract still caps or imposes liability elsewhere;
  * a sentence certifying that the contract complies with a statute is removed.
Every change is recorded as a RemovedClaim(kind="statement"). Pure functions; no SDK imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Citation, Finding, RemovedClaim, Verification
from app.grounding import unsupported_permission

# ---- severity -------------------------------------------------------------------------------------

_MISSING = re.compile(r"^\s*(?:no|missing|lack(?:s|ing)?(?: of)?|absence of|without|tiada|tidak ada)\b", re.I)


def is_missing_protection(f: Finding) -> bool:
    """A finding about something the contract lacks ('No liability cap'), not a term it imposes."""
    return bool(_MISSING.match(f.title))


def overall_risk(findings: list[Finding]) -> str:
    if any(f.risk == "high" and not is_missing_protection(f) for f in findings):
        return "high"
    if any(f.risk in ("high", "medium") for f in findings):
        return "medium"
    return "low"


LEVEL_HEADLINE = {
    "high": "At least one term could cost you a lot, lock you in or take away a right; see the high-risk points.",
    "medium": "No term is rated high risk, but some points are worth raising before you sign.",
    "low": "Nothing is rated high or medium risk; a few points may still be worth checking.",
}
_LEVEL_WORDS = re.compile(r"\b(high|medium|moderate|low)[- ]risk\b", re.I)
_CERTIFY = re.compile(
    r"\b(?:compl(?:y|ies|ying|iant|iance)|in line|consistent|in accordance)\s+with\s+(?:the\s+)?(?:[A-Z][\w()]*\s+){0,6}"
    r"(?:Act|Acts|Order|Regulations?|law|laws|statute|statutory)\b|\bmeets? (?:all )?(?:the )?statutory\b|"
    r"\b(?:follow(?:s|ing)?|mirror(?:s|ing)?|reflect(?:s|ing)?|track(?:s|ing)?|satisf(?:y|ies|ying)|meet(?:s|ing)?|"
    r"conform(?:s|ing)? to|adher(?:e|es|ing) to|align(?:s|ed|ing)? with|match(?:es|ing)?)\s+(?:the\s+)?"
    r"(?:(?:minimum\s+)?requirements of\s+(?:the\s+)?)?(?:[A-Z][\w()-]*\s+){1,5}?(?:Act|Acts|Order|Regulations?)\b|"
    r"\bfully (?:legal|lawful|compliant)\b", re.I)


def headline_for(headline: str, level: str) -> str | None:
    """A replacement headline when the model's names another risk level or certifies compliance."""
    m = _LEVEL_WORDS.search(headline)
    named = m.group(1).lower().replace("moderate", "medium") if m else None
    if (named and named != level) or _CERTIFY.search(headline):
        return LEVEL_HEADLINE[level]
    return None


# ---- convenience exits ----------------------------------------------------------------------------

READER = (r"(?:the\s+)?(?:client|customer|tenant|hirer|borrower|recipient|employee|freelancer|contractor's client|"
          r"pelanggan|penyewa|pekerja|penerima|either party|each party|mana-mana pihak)")
_EXIT = re.compile(rf"\b{READER}\s+(?:may|boleh)\s+(?:at any time\s+)?(?:terminate|end|menamatkan)\b[^.\n]*", re.I)
_EXIT_STOP = re.compile(r",?\s+(?:or immediately|atau serta-merta|or forthwith)\b", re.I)
_EXIT_BAD = re.compile(r"breach|pelanggaran|melanggar|insolven|consent|persetujuan|\bonly\b|\bhanya\b|"
                       r"liquidat|default|gagal|fails?\b|remain(?:ing|der)|balance of|termination (?:fee|charge)|baki",
                       re.I)
_AFTER = re.compile(r"\b(?:after|selepas)\s+(?:the\s+)?(?:first\s+)?(?:[a-z-]+\s+)*\(?(\d{1,3})\)?\s*"
                    r"(months|years|bulan|tahun)", re.I)
_NOTICE = re.compile(r"(?:not less than |at least |sekurang-kurangnya )?(?:[a-z-]+ )*\(?(\d{1,3})\)?\s*"
                     r"(days|months|weeks|hari|bulan|minggu)", re.I)
_UNIT = {"hari": "days", "bulan": "months", "minggu": "weeks", "tahun": "years"}
_HEAD_NO = re.compile(r"(?m)^[ \t]*(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?[ \t]+")


@dataclass(frozen=True)
class Exit:
    clause: str | None
    notice: str  # "90 days"
    after: str = ""  # "after the first 24 months", when the right only starts then


def _clause_at(text: str, offset: int) -> str | None:
    best = None
    for m in _HEAD_NO.finditer(text):
        if m.start(1) > offset:
            break
        best = m.group(1)
    return best


def convenience_exit(pages: list[str]) -> Exit | None:
    """The reader's right to end the agreement on notice, without needing a breach: the first one found."""
    for text in pages:
        for m in _EXIT.finditer(text):
            part = _EXIT_STOP.split(m.group(0), maxsplit=1)[0]
            if _EXIT_BAD.search(part):
                continue
            n = _NOTICE.search(part)
            if not n:
                continue
            unit = _UNIT.get(n.group(2).lower(), n.group(2).lower())
            line = text[text.rfind("\n", 0, m.start()) + 1:m.end()]
            a = _AFTER.search(line)
            after = f"after the first {a.group(1)} {_UNIT.get(a.group(2).lower(), a.group(2).lower()).replace('tahun', 'years')}" if a else ""
            return Exit(_clause_at(text, m.start()), f"{n.group(1)} {unit}", after)
    return None


_LOCK_IN = re.compile(r"\block(?:s|ed|ing)?\b[^.]{0,30}\bin(?:to)?\b|\banother (?:entire |full |whole )?(?:year|term|"
                      r"\d+[- ]month)|\bstuck (?:with|in|for)\b|\btrapped\b|\bno way (?:out|to exit|to leave)\b|"
                      r"\bcannot (?:exit|leave|get out)\b", re.I)

_EARLY_EXIT = re.compile(r"terminat|exit|leav|ending|menamatkan|penamatan|keluar", re.I)
_REMAINING_RENT = re.compile(r"all (?:the )?(?:remaining|unaccrued|unexpired)?\s*rent|"
                             r"rent for (?:the )?(?:rest|remainder|unexpired)|"
                             r"semua (?:baki )?sewa|baki sewa", re.I)
_QUALIFIED_EXIT = re.compile(r"outside|except|unless|other than|in any other way|before|"
                             r"kecuali|melainkan|sebelum|di luar", re.I)


def qualify_exit(text: str, exit_: Exit | None) -> str | None:
    """Qualify a blanket remaining-rent assertion, not a statement already limited to other exits."""
    if not (exit_ and exit_.after and _EARLY_EXIT.search(text) and _REMAINING_RENT.search(text)):
        return None
    if _QUALIFIED_EXIT.search(text):
        return None
    where = f"clause {exit_.clause}" if exit_.clause else "the break provision"
    return (f"The clause demands remaining rent for an exit outside {where}; {where} also allows you to "
            f"leave on {exit_.notice}' notice {exit_.after}.")


_QUOTED_CONSEQUENCE = re.compile(
    r"\bno rebate\b[^.\n]{0,70}\b(?:will be given|shall be given|is payable|is allowed)\b|"
    r"\bwithout any rebate\b|"
    r"\b(?:no|not) (?:be )?(?:responsible|liable|obliged|required) (?:to|for) pay\w*\b|"
    r"\b(?:may not|cannot) (?:end|terminate|return)\b|"
    r"\btiada rebat\b[^.\n]{0,40}\b(?:diberikan|dibayar|dibenarkan)\b|\btanpa rebat\b|"
    r"\btidak bertanggungjawab membayar\b|\btidak boleh (?:menamatkan|memulangkan)\b", re.I)

# 'Shall pay all' alone includes ordinary invoices. Only rescue a payment consequence when
# remaining-term rent/instalments or a balance is demanded on exit, in the same sentence.
_EXIT_PAYMENT = re.compile(
    r"\b(?:shall|must) pay\s+(?:all\s+)?(?:the\s+)?(?:"
    r"(?:remaining|unexpired|outstanding)\s+(?:rent|instalments?|installments?|balance)|"
    r"balance(?:\s+of\b[^.]{0,40})?|rent for (?:the )?(?:rest|remainder|unexpired)\b)|"
    r"\bhendaklah membayar\s+(?:semua\s+)?(?:baki (?:sewa|ansuran|pinjaman)|"
    r"sewa bagi (?:baki|seluruh) tempoh)\b", re.I)
_EXIT_PAYMENT_TRIGGER = re.compile(
    r"\bearly (?:exit|termination|settlement)\b|"
    r"\b(?:on|upon|if|when|after)\b[^.]{0,70}\b(?:terminat\w*|cancel\w*|leav\w*|return\w*)\b|"
    r"\b(?:penamatan|penyelesaian) awal\b|\b(?:jika|apabila|sekiranya)\b[^.]{0,70}"
    r"\b(?:menamatkan|ditamatkan|memulangkan)\b", re.I)


def quoted_consequence(quote: str, pages: list[str]) -> bool:
    """A small positive fallback for explicit consequences, never a test that one is absent.

    The normal pipeline has already verified this quote. Also require its whitespace-normalised
    presence here so standalone callers cannot preserve a high using invented quotation text.
    This recognises direct payment/right-loss terms, not severity or implied legal consequences.
    """
    flat = " ".join(quote.split()).casefold()
    contract = " ".join(" ".join(pages).split()).casefold()
    if not flat or flat not in contract:
        return False
    return bool(_QUOTED_CONSEQUENCE.search(flat) or any(
        _EXIT_PAYMENT.search(sentence) and _EXIT_PAYMENT_TRIGGER.search(sentence)
        for sentence in re.split(r"[.!?]", flat)))

# ---- asymmetric termination -----------------------------------------------------------------------
# One side may end the agreement for any reason on short notice while the reader may end it only for a
# breach (or not at all): the reader can lose the service suddenly yet stays bound. A recognised high-risk
# pattern (playbook service_agreement/termination). Detected from the contract text only, and applied
# only to contract types whose reader is the customer of a vendor.

ASYMMETRY_TYPES = ("service_agreement",)
SHORT_NOTICE_DAYS = 30  # "very short notice (under 30 days)" in the playbook
LONG_TERM_MONTHS = 12  # a breach-only exit on a term longer than this, or one that renews, locks the reader in
_OTHER = (r"(?:the\s+)?(?:vendor|supplier|service provider|provider|contractor|pembekal|penyedia(?:\s+perkhidmatan)?|"
          r"kontraktor)")
_OTHER_EXIT = re.compile(rf"\b{_OTHER}\s+(?:may|boleh|shall be entitled to|is entitled to)\s+(?:at any time\s+)?"
                         r"(?:terminate|end|menamatkan)\b[^.]*", re.I)
_ANY_REASON = re.compile(r"\bfor any reason\b|\bany reason whatsoever\b|\bwithout (?:cause|reason|giving (?:any )?reasons?)\b|"
                         r"\bfor (?:its )?convenience\b|\bat any time\b|\batas apa-apa sebab\b|\btanpa (?:sebab|alasan)\b|"
                         r"\bpada bila-bila masa\b", re.I)
_BREACH = re.compile(r"breach|pelanggaran|melanggar|insolven|liquidat|default|fails? to pay|gagal membayar|muflis", re.I)
_READER_EXIT = re.compile(rf"\b{READER}\s+(?:(?:may|boleh|can)\s+)?(?:(?:only|hanya)\s+)?(?:(?:may|boleh|can)\s+)?"
                          r"(?:terminate|end|menamatkan)\b[^.]*", re.I)
_RENEWS = re.compile(r"\brenew(?:s|ed)?\s+automatically\b|\bautomatic(?:ally)?\s+renew|\bauto-?renew|"
                     r"\b(?:diperbaharui|dilanjutkan|disambung)\s+secara\s+automatik\b", re.I)
_TERM_LEN = re.compile(r"\b(?:initial\s+term|term\s+of|for\s+a\s+(?:fixed\s+)?period\s+of|tempoh\s+awal|"
                       r"tempoh\s+perjanjian|untuk\s+suatu\s+tempoh)\b[^.]{0,80}?\(?(\d{1,3})\)?\s*"
                       r"(months?|years?|bulan|tahun)\b", re.I)
_AFTER_END = re.compile(r"^[^.]{0,40}\b(?:after|following|selepas)\s+(?:the\s+)?(?:termination|expiry|penamatan|tamat)",
                        re.I)
_DAYS = {"day": 1, "hari": 1, "week": 7, "minggu": 7, "month": 30, "bulan": 30}


def _flat(s: str) -> str:
    return " ".join(s.split()).casefold()


@dataclass(frozen=True)
class Clause:
    text: str  # the contract's words, whitespace collapsed
    page: int  # 1-based
    clause: str | None


@dataclass(frozen=True)
class Termination:
    """How each side may end the agreement, read from the contract."""
    other_exit: Clause | None = None  # the other side may end for any reason on short notice
    other_notice: str = ""  # "7 days"
    reader_breach_exit: Clause | None = None  # the reader may end only for the other side's breach
    reader_has_convenience_exit: bool = False
    term: str = ""  # "renews automatically" or "runs 24 months"; "" when short or not stated

    @property
    def asymmetric(self) -> bool:
        return self.other_exit is not None and not self.reader_has_convenience_exit

    @property
    def lock_in(self) -> bool:
        return (self.reader_breach_exit is not None and not self.reader_has_convenience_exit
                and (bool(self.term) or self.asymmetric))

    def consequence(self, about: Clause) -> str:
        where = lambda c: f"clause {c.clause}" if c.clause else "the contract"  # noqa: E731
        if about == self.other_exit:
            yours = (f"you may end it only for the vendor's breach ({where(self.reader_breach_exit)})"
                     if self.reader_breach_exit else "the contract gives you no matching right to end it")
            return (f"{where(about).capitalize()} lets the vendor end the agreement for any reason on "
                    f"{self.other_notice}' notice, while {yours}: you can lose the service at short notice "
                    f"yet stay bound yourself.")
        term = f"the term {self.term}" if self.term else "the vendor can still leave on short notice"
        return (f"{where(about).capitalize()} lets you end the agreement only for the vendor's breach, and "
                f"{term}: outside a breach you stay bound until the term ends.")


_STOP = re.compile(r"\.(?=\s|$)")


def _sentence(text: str, start: int) -> str:
    """The sentence holding text[start], without a leading clause number; whitespace collapsed."""
    s = max(text.rfind(". ", 0, start) + 2 if text.rfind(". ", 0, start) >= 0 else 0, text.rfind("\n", 0, start) + 1)
    head = _HEAD_NO.sub("", text[s:start], count=1)
    stop = _STOP.search(text, start)
    return " ".join((head + text[start:stop.end() if stop else len(text)]).split())


def _notice_days(part: str) -> tuple[int, str] | None:
    n = _NOTICE.search(part)
    if not n:
        return None
    unit = n.group(2).lower().rstrip("s")
    days = int(n.group(1)) * _DAYS.get(unit, 1)
    return days, f"{n.group(1)} {_UNIT.get(n.group(2).lower(), n.group(2).lower())}"


def _term(pages: list[str]) -> str:
    for text in pages:
        if _RENEWS.search(text):
            return "renews automatically"
    for text in pages:
        for m in _TERM_LEN.finditer(text):
            if _AFTER_END.search(text[m.end():m.end() + 60]):
                continue  # a survival period after the agreement ends, not its term
            unit = m.group(2).lower()
            months = int(m.group(1)) * (12 if unit.startswith(("year", "tahun")) else 1)
            if months > LONG_TERM_MONTHS:
                return f"runs {m.group(1)} {_UNIT.get(unit, unit)}"
    return ""


def read_termination(pages: list[str]) -> Termination:
    other, notice, reader = None, "", None
    for pno, text in enumerate(pages, start=1):
        for m in _OTHER_EXIT.finditer(text):
            part = m.group(0)
            nd = _notice_days(part)
            if other is None and _ANY_REASON.search(part) and not _BREACH.search(part) and nd and \
                    nd[0] < SHORT_NOTICE_DAYS:
                other, notice = Clause(_sentence(text, m.start()), pno, _clause_at(text, m.start())), nd[1]
        for m in _READER_EXIT.finditer(text):
            if reader is None and _BREACH.search(m.group(0)):
                reader = Clause(_sentence(text, m.start()), pno, _clause_at(text, m.start()))
    # any exit of the reader's that needs no breach counts, even one with a fee (that is a separate finding)
    free = convenience_exit(pages) is not None or any(
        not _BREACH.search(m.group(0)) for text in pages for m in _READER_EXIT.finditer(text))
    return Termination(other_exit=other, other_notice=notice, reader_breach_exit=reader,
                       reader_has_convenience_exit=free, term=_term(pages))


_READER_SIDE = re.compile(rf"\b{READER}\b[^.]{{0,40}}?\b(?:terminat\w*|end|leave|menamatkan|ditamatkan)\b", re.I)


def _about_reader_exit(f: Finding) -> bool:
    """A termination finding that quotes the reader's own exit (or the lack of one)."""
    return f.clause_type == "termination" and bool(_READER_SIDE.search(f.quote))


def _covers(f: Finding, c: Clause | None) -> bool:
    if c is None or f.clause_type != "termination":
        return False
    q, s = _flat(f.quote), _flat(c.text)
    return bool(q) and f.page == c.page and (q in s or s in q)


LOCK_IN_TITLE = "Exit only for the vendor's breach"


def lock_in_finding(t: Termination, fid: str, citation: Citation | None) -> Finding:
    c = t.reader_breach_exit
    assert c is not None
    return Finding(
        id=fid, clause_type="termination", title=LOCK_IN_TITLE, risk="high", quote=c.text, page=c.page,
        clause_ref=c.clause,
        why_risky=("You can end the agreement only if the vendor breaches it, so otherwise you stay bound. "
                   + t.consequence(c)),
        standard=("Balanced terms let the customer end the agreement without a reason on reasonable notice, "
                  "paying for work done up to that date, with the same notice for both sides."),
        suggested_ask=("Please add a right for us to end the agreement without a reason on 30 days' written "
                       "notice, paying for services delivered up to that date."),
        baseline_ref="service_agreement/termination", citations=[citation] if citation else [],
        verification=Verification(quote_match="normalised"))


def calibrate_termination(findings: list[Finding], pages: list[str], contract_type: str | None,
                          citation: Citation | None = None, notes: list[str] | None = None) -> set[str]:
    """Rate asymmetric termination high and flag the reader's breach-only exit; returns the ids of findings
    whose high rating rests on this contract's termination clauses (their consequence is stated here)."""
    if contract_type not in ASYMMETRY_TYPES:
        return set()
    t = read_termination(pages)
    held: set[str] = set()
    for f in findings:
        about = (t.other_exit if t.asymmetric and _covers(f, t.other_exit) else
                 t.reader_breach_exit if t.lock_in and _covers(f, t.reader_breach_exit) else None)
        if about is None:
            continue
        held.add(f.id)
        if f.risk != "high":
            if notes is not None:
                notes.append(f"'{f.title}' rated high: asymmetric termination ({f.risk} from the model)")
            f.risk = "high"
            if (c := t.consequence(about)) not in f.why_risky:
                f.why_risky = f"{f.why_risky.rstrip()} {c}".strip()
    if t.lock_in and not any(_covers(f, t.reader_breach_exit) or _about_reader_exit(f) for f in findings):
        f = lock_in_finding(t, f"f{len(findings) + 1}", citation)
        findings.append(f)
        held.add(f.id)
        if notes is not None:
            notes.append(f"added '{f.title}': you may end only for breach and {t.term or 'the vendor may end at will'}")
    return held


# ---- remedies -------------------------------------------------------------------------------------

_ABSOLUTE = re.compile(r"\bdisclaims? (?:all|any|every)\b|\b(?:all|any) responsibility\b|\bno (?:remedy|recourse|"
                       r"compensation)(?: at all)?\b|\bzero (?:compensation|protection|recourse)\b|"
                       r"\bbear (?:the |all )?(?:full |entire |whole )?(?:cost|loss)|\bcompletely (?:disclaim|exclud|"
                       r"escape)|\bnothing (?:you can|to) (?:claim|recover)\b|\bleav\w* you with nothing\b", re.I)
_CAP = re.compile(r"liabilit(?:y|ies|i)[^.\n]{0,160}?(?:shall not exceed|will not exceed|is limited to|limited to|"
                  r"capped at|tidak (?:akan )?melebihi|terhad kepada)", re.I)
_CARE = re.compile(r"reasonable skill and care|reasonable care and skill|kemahiran dan (?:penjagaan|ketelitian) "
                   r"yang munasabah", re.I)


def remaining_remedy(pages: list[str]) -> str | None:
    """A sentence naming what the contract still gives the reader, or None if it caps nothing and owes no care."""
    for pattern, what in ((_CAP, "still caps the vendor's overall liability rather than excluding it"),
                          (_CARE, "still requires the work to be done with reasonable skill and care")):
        for text in pages:
            m = pattern.search(text)
            if m:
                n = _clause_at(text, m.start())
                where = f"Clause {n}" if n else "Another clause"
                return (f"{where} {what}, so you may still have a claim, though a limited one; ask for the point "
                        f"to be made express.")
    return None


# ---- applying it ----------------------------------------------------------------------------------


def _note(text: str, title: str | None, page: int | None, reason: str = "ungrounded") -> RemovedClaim:
    return RemovedClaim(kind="statement", reason=reason, text=text, page=page,  # type: ignore[arg-type]
                        finding_title=title)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text.strip()) if s.strip()]


def calibrate_finding(f: Finding, exit_: Exit | None, remedy: str | None, removed: list[RemovedClaim]) -> None:
    out, added = [], set()
    for s in _sentences(f.why_risky):
        qualified = qualify_exit(s, exit_) if f.clause_type == "early_termination" else None
        if qualified:
            removed.append(_note(f"Explanation: {s}", f.title, f.page))
            if qualified not in added:
                out.append(qualified)
                added.add(qualified)
        elif exit_ and f.clause_type in ("auto_renewal", "term") and _LOCK_IN.search(s):
            where = f"Clause {exit_.clause}" if exit_.clause else "The contract"
            when = f" {exit_.after}" if exit_.after else ""
            repl = (f"{where} lets you end the agreement on {exit_.notice}' notice{when}, so missing the deadline "
                    f"need not lock you in for the whole renewal term.")
            removed.append(_note(f"Explanation: {s}", f.title, f.page))
            if repl not in added:
                out.append(repl)
                added.add(repl)
        elif remedy and _ABSOLUTE.search(s):
            removed.append(_note(f"Explanation: {s}", f.title, f.page))
            if remedy not in added:
                out.append(remedy)
                added.add(remedy)
        else:
            out.append(s)
    f.why_risky = " ".join(out)


def calibrate_review(findings: list[Finding], consequences: dict[str, str], summary: list[str], headline: str,
                     pages: list[str], removed: list[RemovedClaim], grounded, *, contract_type: str | None = None,
                     termination_citation: Citation | None = None,
                     notes: list[str] | None = None) -> tuple[str, str, list[str]]:
    """Severity, wording and the verdict. `consequences` maps finding id -> the model's consequence;
    `grounded(text)` is True when a sentence states only contract facts. Returns (overall, headline, summary).
    With `contract_type`, asymmetric termination is rated from the contract's own clauses first
    (calibrate_termination); `notes` collects what that changed."""
    held = calibrate_termination(findings, pages, contract_type, termination_citation, notes)
    for f in findings:
        if f.risk == "high" and f.id not in held:
            c = consequences.get(f.id, "").strip()
            if (not c or not grounded(c)) and not quoted_consequence(f.quote, pages):
                f.risk = "medium"
                why = ("no separate consequence evidence was supplied; severity could not be verified"
                       if not c else "the separate consequence evidence could not be verified; this does not "
                       "establish that the contract lacks a consequence")
                removed.append(_note(f"High risk: {f.title} (shown as medium: {why})", f.title, f.page,
                                     "unsupported_severity"))
    exit_, remedy = convenience_exit(pages), remaining_remedy(pages)
    for f in findings:
        calibrate_finding(f, exit_, remedy, removed)
    kept = []
    for s in summary:
        qualified = qualify_exit(s, exit_)
        if qualified:
            removed.append(_note(f"Summary: {s}", None, None))
            kept.append(qualified)
        elif _CERTIFY.search(s) or (exit_ and not exit_.after and _LOCK_IN.search(s)) or (remedy and _ABSOLUTE.search(s)):
            removed.append(_note(f"Summary: {s}", None, None))
        else:
            kept.append(s)
    level = overall_risk(findings)
    new_head = headline_for(headline, level)
    if unsupported_permission(headline, "\n".join(pages)) or qualify_exit(headline, exit_):
        new_head = LEVEL_HEADLINE[level]
    if new_head:
        removed.append(_note(f"Headline: {headline}", None, None))
    return level, new_head or headline, kept
