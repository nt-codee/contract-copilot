"""Grounding: the model's own words about the contract must come from the contract.

Quote verification proves the quoted words are on the page. This module checks the rest of what the
model writes about the contract, deterministically and without another model call:

- why_risky: every number, amount or period must occur in the contract (or in a source cited for the
  finding). A sentence that fails is removed.
- why_risky, suggested_ask, missing clauses and the summary: a contract feature named in FACT_TERMS
  (daily compounding, salary, garden leave ...) must have evidence in the contract text. The sentence,
  or for an ask the part of it, that asserts it is removed.
- suggested_ask: "(or Singapore)" style alternatives are reduced to the locked jurisdiction, and under
  Malaysian law a non-compete ask never offers a shorter or paid restraint as a fallback (s 28 of the
  Contracts Act 1950 has no reasonableness exception).

Every removal is recorded as a RemovedClaim(kind="statement") so the reader sees what was taken out.
Pure functions; no SDK imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Finding, MissingClause, RemovedClaim

# ---- numbers --------------------------------------------------------------------------------

_NUM = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:st|nd|rd|th)?(?![\w])")
_PAGE_REF = re.compile(r"\bpages?\s+\d+(?:\s*(?:-|to|and)\s*\d+)?", re.I)

_EN_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_EN_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split())
            if w != "_"}
_MS_UNITS = {"satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "lapan": 8,
             "sembilan": 9}
_MS_WORD = {"sepuluh": 10, "sebelas": 11, "seratus": 100, "setahun": 1, "sebulan": 1, "sehari": 1}


def _canon(whole: str, frac: str | None) -> str:
    """'20,400' -> '20400'; '50.00' -> '50'; '1.5' -> '1.5'."""
    n = whole.replace(",", "")
    n = n.lstrip("0") or "0"
    frac = (frac or "").rstrip("0")
    return f"{n}.{frac}" if frac else n


def numbers_in(text: str) -> set[str]:
    """Every number written in digits in `text`, canonical."""
    return {_canon(m.group(1), m.group(2)) for m in _NUM.finditer(text)}


def _word_numbers(text: str) -> set[str]:
    """Numbers written in English or Malay words: 'twenty-four', 'ninety', 'dua belas', 'tiga puluh'."""
    low = text.lower()
    out: set[str] = set()
    for m in re.finditer(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](one|two|three|four|"
                         r"five|six|seven|eight|nine))?\b", low):
        out.add(str(_EN_TENS[m.group(1)] + (_EN_UNITS[m.group(2)] if m.group(2) else 0)))
    for w, v in _EN_UNITS.items():
        if re.search(rf"\b{w}\b", low):
            out.add(str(v))
    for m in re.finditer(r"\b(satu|dua|tiga|empat|lima|enam|tujuh|lapan|sembilan) puluh(?: (satu|dua|tiga|"
                         r"empat|lima|enam|tujuh|lapan|sembilan))?\b", low):
        out.add(str(10 * _MS_UNITS[m.group(1)] + (_MS_UNITS[m.group(2)] if m.group(2) else 0)))
    for m in re.finditer(r"\b(dua|tiga|empat|lima|enam|tujuh|lapan|sembilan) belas\b", low):
        out.add(str(10 + _MS_UNITS[m.group(1)]))
    for w, v in {**_MS_UNITS, **_MS_WORD}.items():
        if re.search(rf"\b{w}\b", low):
            out.add(str(v))
    return out


AMOUNT_MIN = 100  # sums are allowed only between amounts this large: small numbers are too easy to hit
CONVERSIONS = (7, 12, 24, 30, 52, 365)  # days a week, months a year ...: "2% a month is 24% a year"


def contract_numbers(pages: list[str]) -> set[str]:
    """The numbers the contract states, in digits or words, plus what follows from two of them: a product
    (rent times months; a monthly rate times 12) or a sum of two amounts (deposit plus utility deposit)."""
    text = "\n".join(pages)
    base = numbers_in(text) | _word_numbers(text)
    ints = sorted({int(n) for n in base if n.isdigit() and 1 <= int(n) <= 10_000_000})[:400]
    factors = sorted(set(ints) | set(CONVERSIONS))
    derived = {str(a * b) for a in ints for b in factors}
    big = [n for n in ints if n >= AMOUNT_MIN]
    derived |= {str(a + b) for i, a in enumerate(big) for b in big[i:]}
    years = [n for n in ints if 1990 <= n <= 2100]  # a deadline counted from a stated date may fall later
    if years:
        derived |= {str(y) for y in range(min(years), max(years) + 11)}
    return base | derived


def ungrounded_numbers(sentence: str, allowed: set[str]) -> list[str]:
    """Numbers in `sentence` that are not in `allowed`. Page references ('page 3') are not claims."""
    s = _PAGE_REF.sub(" ", sentence)
    return [m.group(0) for m in _NUM.finditer(s) if _canon(m.group(1), m.group(2)) not in allowed]


# ---- contract features ----------------------------------------------------------------------


@dataclass(frozen=True)
class FactTerm:
    name: str
    claim: re.Pattern  # the model's words that assert the feature
    evidence: re.Pattern  # words in the contract that show the feature is there


_MONEY = r"(?:interest|fee|charge|penalt|late|overdue|arrears|faedah|caj|bayaran lewat)"
_NEG = re.compile(r"\b(no|not|without|never|nor|rather than|instead of|in place of)\b[\w\s,'-]{0,25}$", re.I)

FACT_TERMS = [
    FactTerm("compounding", re.compile(
        rf"\bcompound(?:s|ed|ing)?\b(?!\s+by\b)(?=[^.]{{0,30}}{_MONEY})|{_MONEY}[^.]{{0,30}}\bcompound(?:s|ed|ing)?\b"
        rf"(?!\s+by\b)", re.I),
             re.compile(r"compound|kompaun|faedah atas faedah|interest on (?:the )?interest", re.I)),
    FactTerm("a daily charge", re.compile(
        rf"\b(?:daily|per day|each day|a day)\b(?=[^.]{{0,30}}{_MONEY})|{_MONEY}[^.]{{0,30}}\b(?:daily|per day|each "
        rf"day|a day)\b", re.I),
             re.compile(r"\bdaily\b|\bper day\b|\beach day\b|\ba day\b|\bevery day\b|\bday of\b|\bper diem\b|"
                        r"\bharian\b|\bsehari\b|setiap hari", re.I)),
    FactTerm("salary", re.compile(r"\b(?:salary|salaries|wages?)\b", re.I),
             re.compile(r"\bsalary\b|\bwages?\b|\bgaji\b|\bupah\b|\bremuneration\b|\bsaraan\b", re.I)),
    FactTerm("garden leave", re.compile(r"\bgarden[- ]leave\b", re.I),
             re.compile(r"garden[- ]leave|\bsalary\b|\bgaji\b|\bwages?\b", re.I)),
]


def unsupported_terms(text: str, contract: str) -> list[str]:
    """FACT_TERMS that `text` asserts but `contract` shows no sign of. A negated mention ('with no
    compounding') asserts nothing and is allowed."""
    out = []
    for t in FACT_TERMS:
        if t.evidence.search(contract):
            continue
        for m in t.claim.finditer(text):
            if not _NEG.search(text[: m.start()]):
                out.append(t.name)
                break
    return out


# A one-way restriction is not an affirmative permission for the other party. These checks cover
# categorical permission claims only; they do not infer the legal default from contractual silence.
_ASSIGN = r"\b(?:assign(?:s|ed|ing)?|transfer(?:s|red|ring)?|delegat(?:e|es|ed|ing)|menyerahhak\w*|memindah\w*)\b"
_USE = r"\b(?:us(?:e|es|ed|ing)|disclos\w*|shar\w*|mengguna\w*|mendedah\w*|berkongsi)\b"
_UNRESTRICTED = re.compile(r"\bfreely\b|\bfree (?:to|of)\b|\bwithout (?:any )?(?:restriction|consent)|"
                           r"\bunrestricted\b|\btanpa (?:sebarang )?(?:sekatan|persetujuan)|\bbebas\b", re.I)
_LEGAL_FREEDOM = re.compile(r"\b(?:legally|lawfully) free\b|\bsah di sisi undang-undang\b", re.I)
_DUTIES = re.compile(r"\bobligations?\b|\bduties\b|\b(?:obligasi|kewajipan|tanggungjawab)\b", re.I)
_PARTY = re.compile(r"\b(discloser|recipient|vendor|customer|client|owner|hirer|tenant|landlord|"
                    r"pihak pendedah|penerima|pembekal|pelanggan|penyewa)\b", re.I)
_PARTY_EN = {"pihak pendedah": "discloser", "penerima": "recipient", "pembekal": "vendor",
             "pelanggan": "customer", "penyewa": "tenant"}


def unsupported_permission(text: str, contract: str) -> bool:
    """Narrowly reject unrestricted permission inferred from silence, not requests for new terms.

    Positive contractual permission can support 'may assign rights'; it cannot establish freedom
    from every other legal restriction. Assignment of duties needs its own positive wording.
    """
    if not _UNRESTRICTED.search(text) or re.search(
            r"\b(?:not|never|cannot)\s+(?:(?:legally|lawfully)\s+)?free(?:ly)?\b|"
            r"\bdoes not (?:allow|permit)\b", text, re.I):
        return False
    action = _ASSIGN if re.search(_ASSIGN, text, re.I) else _USE
    if not re.search(action, text, re.I):
        return False
    if _LEGAL_FREEDOM.search(text):
        return True
    needs_duties = action == _ASSIGN and bool(_DUTIES.search(text))
    actors = _PARTY.findall(text[:re.search(action, text, re.I).start()])
    actor = _PARTY_EN.get(actors[-1].lower(), actors[-1].lower()) if actors else None
    # A no-duty statement ('not obliged ...') and a ban ('may not ...') are not permissions.
    permission = re.compile(rf"\b(?:may|can|boleh|is permitted to|is entitled to|is free to)\s+"
                            rf"(?:freely\s+)?{action}\b[^.\n]*", re.I)
    for m in permission.finditer(contract):
        line = re.split(r"[.\n]", contract[:m.start()])[-1]
        parties = _PARTY.findall(line)
        source_actor = _PARTY_EN.get(parties[-1].lower(), parties[-1].lower()) if parties else None
        if actor and actor != source_actor:
            continue
        scope = re.sub(r"without (?:any )?(?:prior )?(?:written )?consent|tanpa persetujuan", "", m.group(0),
                       flags=re.I)
        if re.search(r"\b(?:consent|only|subject to|provided|unless|except|persetujuan|hanya|tertakluk|"
                     r"melainkan|kecuali)\b", scope, re.I):
            continue
        if not needs_duties or _DUTIES.search(m.group(0)):
            return False
    return True


_REQUIRES_ACTUAL_LOSS = re.compile(
    r"\bonly\b[^.]{0,70}\b(?:for |proven |proved )?actual loss\b|"
    r"\b(?:must|needs? to|has to|required to)\s+(?:be\s+)?(?:prove[dn]?|show|establish)\b[^.]{0,30}actual loss|"
    r"\bactual loss\b[^.]{0,30}\bmust be (?:proved|proven)\b", re.I)
_NO_PROOF_REQUIRED = re.compile(
    r"whether or not actual (?:damage or )?loss is proved|"
    r"irrespective of whether actual (?:damage or )?loss (?:is|or damage is) prov(?:en|ed)|"
    r"without (?:proving|proof of) (?:the )?actual loss", re.I)


def qualify_loss_proof(sentence: str, cited: str) -> str | None:
    """Correct an actual-loss prerequisite only when the retrieved authority expressly contradicts it.

    Ordinary damages/proof statements with other sources are left alone. No source or unseen
    subsection is supplied here; the sentence below follows the explicit reasonable-compensation
    text already provided in this finding's citations.
    """
    flat = " ".join(cited.split())
    if (re.search(r"reasonable compensation", flat, re.I) and _NO_PROOF_REQUIRED.search(flat)
            and _REQUIRES_ACTUAL_LOSS.search(sentence)
            and not re.search(r"\b(?:without|need not|not required|whether or not)\b", sentence, re.I)):
        return ("The cited authority allows reasonable compensation up to the stipulated sum even without "
                "proof of actual loss; the full amount is not automatic.")
    return None


# ---- splitting and joining ------------------------------------------------------------------

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
_VERBS = ("remove|delete|limit|confirm|state|add|make|list|cap|change|replace|give|include|allow|let|set|"
          "reduce|provide|agree|pay|refund|show|treat|keep|drop|strike|narrow|clarify|explain|use|apply")
_PART_SEP = re.compile(rf"(,\s+(?:and\s+|or\s+)?|;\s+|\s+(?:and|or)\s+(?=(?:please\s+)?(?:{_VERBS})\b))", re.I)

FALLBACK_WHY = "This clause is less favourable to you than the balanced position."
FALLBACK_ASK = "Please explain how this clause would apply to us and agree a balanced wording."


def sentences(text: str) -> list[str]:
    return [s for s in _SENT.split(text.strip()) if s.strip()]


def _strip_sentences(text: str, bad) -> tuple[str, list[str]]:
    keep, gone = [], []
    for s in sentences(text):
        (gone if bad(s) else keep).append(s)
    return " ".join(keep), gone


def _strip_parts(ask: str, bad) -> tuple[str, list[str]]:
    """Remove the parts of a one-sentence ask (split on commas, semicolons and 'and <verb>') that `bad`
    flags, and rejoin the rest into a sentence."""
    pieces = _PART_SEP.split(ask.strip())
    parts, seps = pieces[0::2], pieces[1::2]
    flags = [bool(bad(p)) for p in parts]
    if not any(flags):
        return ask, []
    gone = [p.strip(" .") for p, f in zip(parts, flags) if f]
    if all(flags):
        return "", gone
    out = ""
    for i, (p, f) in enumerate(zip(parts, flags)):
        if f:
            continue
        if not out:
            out = p.strip()
        else:
            out += seps[i - 1] + p.strip()
    out = out.strip(" ,;")
    polite = ask.lstrip().lower().startswith("please")
    if polite and not out.lower().startswith("please"):
        out = "Please " + out[0].lower() + out[1:]
    out = out[0].upper() + out[1:]
    if out[-1] not in ".?!":
        out += "."
    return out, gone


# ---- jurisdiction and relationship ----------------------------------------------------------

_ALT = {"my": re.compile(r"\s*\((?:or|atau)\s+(?:in\s+)?Singapore(?:\s+law)?\)", re.I),
        "sg": re.compile(r"\s*\((?:or|atau)\s+(?:in\s+)?Malaysia(?:n)?(?:\s+law)?\)", re.I)}
# a narrower or paid restraint offered as a fallback: void either way under s 28 (Malaysia)
_MY_RESTRAINT_FALLBACK = re.compile(
    r"garden[- ]leave|\bno (?:more|longer) than\s+\S+\s+(?:\(\d+\)\s+)?(?:months?|years?|weeks?)\b|"
    r"\blimit(?:ed)? (?:it|this|the (?:non-compete|restraint|restriction))\b|\bnamed (?:direct )?competitors\b|"
    r"\b(?:shorter|narrower) (?:period|restraint|restriction)\b", re.I)


# ---- the checks -----------------------------------------------------------------------------


def _removed(text: str, f_title: str | None, page: int | None, reason: str = "ungrounded") -> RemovedClaim:
    return RemovedClaim(kind="statement", reason=reason, text=text, page=page,  # type: ignore[arg-type]
                        finding_title=f_title)


def ground_finding(f: Finding, contract: str, numbers: set[str], jurisdiction: str | None,
                   removed: list[RemovedClaim]) -> None:
    """Check one finding's why_risky and suggested_ask against the contract; edit it in place."""
    cited = " ".join(f"{c.title} {c.excerpt}" for c in f.citations)
    allowed = numbers | numbers_in(cited) | _word_numbers(cited) | numbers_in(f.quote)

    def bad_why(s: str) -> bool:
        return bool(ungrounded_numbers(s, allowed) or unsupported_terms(s, contract)
                    or unsupported_permission(s, contract))

    why, gone = _strip_sentences(f.why_risky, bad_why)
    for s in gone:
        removed.append(_removed(f"Explanation: {s}", f.title, f.page))
    if gone:
        f.why_risky = why or FALLBACK_WHY

    corrected = []
    for s in sentences(f.why_risky):
        replacement = qualify_loss_proof(s, cited)
        if replacement:
            removed.append(_removed(f"Explanation: {s}", f.title, f.page, "citation_scope"))
        corrected.append(replacement or s)
    f.why_risky = " ".join(corrected)
    # 'standard' is a proposal, but a legal assertion embedded in it still needs faithful support.
    standards = []
    for s in sentences(f.standard):
        replacement = qualify_loss_proof(s, cited)
        if replacement:
            removed.append(_removed(f"Standard: {s}", f.title, f.page, "citation_scope"))
        standards.append(replacement or s)
    f.standard = " ".join(standards)

    ask = f.suggested_ask
    alt = _ALT.get(jurisdiction or "")
    if alt and alt.search(ask):
        for m in alt.finditer(ask):
            removed.append(_removed(f"Ask: {m.group(0).strip()}", f.title, f.page, "wrong_jurisdiction"))
        ask = alt.sub("", ask)
    if jurisdiction == "my" and f.clause_type == "non_compete":
        ask, gone = _strip_parts(ask, lambda p: bool(_MY_RESTRAINT_FALLBACK.search(p)))
        for p in gone:
            removed.append(_removed(f"Ask: {p}", f.title, f.page, "remedy_scope"))
        if gone:
            ask = ask or "Please delete the restriction on working for others after the agreement ends."
    ask, gone = _strip_parts(ask, lambda p: bool(unsupported_terms(p, contract)))
    for p in gone:
        removed.append(_removed(f"Ask: {p}", f.title, f.page))
    f.suggested_ask = ask or FALLBACK_ASK


def ground_missing(m: MissingClause, contract: str, jurisdiction: str | None,
                   removed: list[RemovedClaim]) -> None:
    """Missing clauses describe what is absent, so only contract features are checked here."""
    why, gone = _strip_sentences(m.why_it_matters, lambda s: bool(unsupported_terms(s, contract)
                                                               or unsupported_permission(s, contract)))
    for s in gone:
        removed.append(_removed(f"Missing clause ({m.title}): {s}", m.title, None))
    if gone:
        m.why_it_matters = why or f"The contract has no clause on {m.title.lower()}."
    ask = m.suggested_ask
    alt = _ALT.get(jurisdiction or "")
    if alt and alt.search(ask):
        removed.append(_removed(f"Missing clause ask ({m.title}): {alt.search(ask).group(0).strip()}",
                                m.title, None, "wrong_jurisdiction"))
        ask = alt.sub("", ask)
    ask, gone = _strip_parts(ask, lambda p: bool(unsupported_terms(p, contract)))
    for p in gone:
        removed.append(_removed(f"Missing clause ask ({m.title}): {p}", m.title, None))
    m.suggested_ask = ask or f"Please add a clause on {m.title.lower()}."


def ground_summary(summary: list[str], contract: str, removed: list[RemovedClaim]) -> list[str]:
    keep = []
    for s in summary:
        if unsupported_terms(s, contract) or unsupported_permission(s, contract):
            removed.append(_removed(f"Summary: {s}", None, None))
        else:
            keep.append(s)
    return keep


def ground_review(findings: list[Finding], missing: list[MissingClause], summary: list[str], pages: list[str],
                  jurisdiction: str | None, removed: list[RemovedClaim]) -> list[str]:
    """Run every check. Edits findings and missing clauses in place; returns the kept summary lines."""
    contract = "\n".join(pages)
    numbers = contract_numbers(pages)
    for f in findings:
        ground_finding(f, contract, numbers, jurisdiction, removed)
    for m in missing:
        ground_missing(m, contract, jurisdiction, removed)
    return ground_summary(summary, contract, removed)
