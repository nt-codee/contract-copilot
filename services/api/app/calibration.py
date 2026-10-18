"""Calibration: severity and certainty follow from the contract, not from the model's tone.

- A high finding must name a concrete, grounded consequence (LLMFinding.consequence), else it is rated
  medium. Overall risk is then a function of the findings: high only if a high finding is about a term
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

from app.models import Finding, RemovedClaim

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
_AFTER = re.compile(r"\b(?:after|selepas)\s+(?:the\s+)?(?:first\s+)?(?:[a-z-]+\s+)*\((\d{1,3})\)\s*"
                    r"(months|years|bulan|tahun)", re.I)
_NOTICE = re.compile(r"(?:not less than |at least |sekurang-kurangnya )?(?:[a-z-]+ )*\((\d{1,3})\)\s*"
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
        if exit_ and f.clause_type in ("auto_renewal", "term") and _LOCK_IN.search(s):
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
                     pages: list[str], removed: list[RemovedClaim], grounded) -> tuple[str, str, list[str]]:
    """Severity, wording and the verdict. `consequences` maps finding id -> the model's consequence;
    `grounded(text)` is True when a sentence states only contract facts. Returns (overall, headline, summary)."""
    for f in findings:
        if f.risk == "high":
            c = consequences.get(f.id, "").strip()
            if not c or not grounded(c):
                f.risk = "medium"
                why = "no concrete consequence was given" if not c else "its consequence is not in the contract"
                removed.append(_note(f"High risk: {f.title} (shown as medium: {why})", f.title, f.page,
                                     "unsupported_severity"))
    exit_, remedy = convenience_exit(pages), remaining_remedy(pages)
    for f in findings:
        calibrate_finding(f, exit_, remedy, removed)
    kept = []
    for s in summary:
        if _CERTIFY.search(s) or (exit_ and not exit_.after and _LOCK_IN.search(s)) or (remedy and _ABSOLUTE.search(s)):
            removed.append(_note(f"Summary: {s}", None, None))
        else:
            kept.append(s)
    level = overall_risk(findings)
    new_head = headline_for(headline, level)
    if new_head:
        removed.append(_note(f"Headline: {headline}", None, None))
    return level, new_head or headline, kept
