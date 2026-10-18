"""Legal-claim restraint and consistency: the review may not state the law more firmly than its sources.

Runs after grounding and calibration, before the library caveats are appended. Deterministic, no model
call. Every change is recorded as a RemovedClaim(kind="statement", reason="citation_scope").

1. Contradiction. A law entry may list propositions it rejects (`contradicts` frontmatter: pairs of word
   stems, e.g. s 28 of the Malaysian Contracts Act rejects a reasonableness test for restraints). A
   sentence in a finding, a missing clause, the summary or the headline that states such a proposition
   is removed, when the entry is of the review's jurisdiction and is cited on, or covers the clause type
   of, a finding. The summary and headline are checked against the entries of every finding, so the
   verdict cannot contradict a finding's source.
2. Legal outcomes. "is void", "unenforceable", "illegal", "breaches the Act" must be backed by a cited
   entry whose own text gives that outcome for this clause type (or whose `asserts` frontmatter allows
   it). Otherwise the sentence is rewritten in a hedged form ("may be void"), or removed if it cannot be.
   A summary sentence is backed only by a finding that is itself backed (the clause it names, if any).
3. Absolute loss of rights. "strips you of legal recourse", "you lose all remedies", "no legal right",
   "no remedy" go beyond what a contract term can establish on its own; they are rewritten to what the
   contract's words do ("tries to limit your legal recourse", "no express right in this contract").
4. Compliance. A sentence certifying that the contract complies with or follows a statute is removed.
Pure functions; no SDK imports.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from app.calibration import _CERTIFY, LEVEL_HEADLINE
from app.grounding import FALLBACK_WHY, sentences
from app.knowledge import LawEntry
from app.models import Finding, MissingClause, RemovedClaim

REASON = "citation_scope"

# ---- legal outcomes -------------------------------------------------------------------------------

_V = r"(?:null and )?void\b|invalid\b|of no (?:legal )?effect\b|tidak sah\b|terbatal\b"
_U = r"unenforceable\b|not (?:legally )?enforceable\b|cannot (?:legally )?be enforced\b|tidak boleh dikuatkuasakan\b"
_I = (r"illegal\b|unlawful\b|against the law\b|prohibited by (?:law|statute)\b|"
      r"(?:breach(?:es|ing)?|violat(?:es|ing)|contraven(?:es|ing))\s+(?:the\s+|section\s+|s\.?\s*)?[^.]{0,40}?\bAct\b|"
      r"(?:does|do) not comply with\b|fails? to comply with\b")
OUTCOMES: dict[str, tuple[re.Pattern, re.Pattern]] = {
    # family: (the review's words that assert it, the source words that back it)
    "void": (re.compile(rf"\b(?:{_V})", re.I), re.compile(r"\bvoid\b|\bof no effect\b|\btidak sah\b", re.I)),
    "unenforceable": (re.compile(rf"\b(?:{_U})", re.I),
                      re.compile(r"unenforceable|not (?:be )?enforceable|shall not be enforced|cannot be enforced|"
                                 r"\bvoid\b", re.I)),
    "illegal": (re.compile(rf"\b(?:{_I})", re.I),
                re.compile(r"\boffence\b|\bunlawful\b|\billegal\b|\bprohibit\w*|\bshall not\b|\bcontraven\w+", re.I)),
}
_HEDGE = re.compile(r"\b(?:may|might|could|can be|likely|unlikely|possibly|probably|arguabl\w*|risk\w*|"
                    r"challeng\w*|question\w*|whether|if a court|tends? to|generally|potentially|perhaps|"
                    r"not settled|unclear|uncertain)\b", re.I)
HEDGE_WINDOW = 40  # a hedge counts only shortly before the claim: "the seller may cancel and it is void" is firm
_OUTCOME_WORD = r"(?=(?:null and )?void\b|unenforceable\b|illegal\b|unlawful\b|invalid\b)"
_REWRITES: list[tuple[re.Pattern, str | Callable[[re.Match], str]]] = [
    (re.compile(r"\b(?:is|are|was|were|will be|would be)\s+not\s+(?:legally\s+)?enforceable\b", re.I),
     "may not be enforceable"),
    (re.compile(r"\bcannot (?:legally )?be enforced\b", re.I), "may not be enforceable"),
    (re.compile(r"\b(?:is|are|was|were|will be|would be|becomes?|remains?)\s+(?:(?:therefore|thus|also|clearly|"
                rf"legally|entirely|wholly|completely|simply|automatically)\s+)*{_OUTCOME_WORD}", re.I), "may be "),
    (re.compile(rf"\b(?:makes?|renders?)\s+((?:[\w'-]+\s+){{1,6}}?){_OUTCOME_WORD}", re.I),
     lambda m: f"may make {m.group(1)}"),
    (re.compile(r"\b(?:the\s+)?(?:(?:Malaysian|Singapore)\s+)?courts?\s+(?:will\s+|would\s+)?(?:void|strike down|"
                r"strikes down|refuse to enforce|refuses to enforce)\b", re.I),
     lambda m: ("A court may refuse to enforce" if m.start() == 0 else "a court may refuse to enforce")),
    (re.compile(r"\b(breaches|violates|contravenes)\b", re.I),
     lambda m: "may " + {"breaches": "breach", "violates": "violate", "contravenes": "contravene"}[m.group(1).lower()]),
    (re.compile(r"\b(?:does|do) not comply with\b|\bfails? to comply with\b", re.I), "may not comply with"),
]


def hedged_before(before: str) -> bool:
    """A hedge shortly before a claim and in the same clause (not across a comma, semicolon or colon)."""
    tail = re.split(r"[,;:]", before[-HEDGE_WINDOW:])[-1]
    return bool(_HEDGE.search(tail))


# the contract's own words reported, not a legal conclusion: "clause 9 says a late notice is void"
_ATTRIBUTED = re.compile(r"\b(?:says?|states?|provides?|declares?|stipulates?|menyatakan|memperuntukkan)\b", re.I)
_QUOTED = re.compile(r"[\"\u201c][^\"\u201d]*[\"\u201d]|\u2018[^\u2019]*\u2019")


def unhedged_outcomes(sentence: str) -> list[str]:
    """Outcome families `sentence` asserts without a hedge before the assertion. Words inside quotation
    marks, and outcomes the sentence attributes to the contract itself, are not the review's claim."""
    bare = _QUOTED.sub(lambda m: " " * len(m.group(0)), sentence)
    out = []
    for family, (claim, _) in OUTCOMES.items():
        for m in claim.finditer(bare):
            before = bare[:m.start()]
            if not hedged_before(before) and not _ATTRIBUTED.search(before):
                out.append(family)
                break
    return out


def hedge_outcome(sentence: str, unbacked: Callable[[str], list[str]]) -> str | None:
    """A hedged rewrite of the sentence, or None if no rewrite hedges every outcome in `unbacked(s)`."""
    s = sentence
    for pattern, repl in _REWRITES:
        s = pattern.sub(repl, s, count=1)
        if not unbacked(s):
            return s[0].upper() + s[1:] if s else s
    return None


def backed(f: Finding, family: str, law_by_id: Callable[[str], LawEntry | None]) -> bool:
    """A citation on `f` whose library entry gives outcome `family` for f's clause type."""
    evidence = OUTCOMES[family][1]
    for c in f.citations:
        e = law_by_id(c.ref) if c.kind in ("law", "case") else None
        if e is not None and e.asserts_outcome(f.clause_type, family, evidence):
            return True
    return False


# ---- absolute loss of rights ----------------------------------------------------------------------

_SCOPED = re.compile(r"\b(?:under|in|by) this (?:agreement|contract)\b|\bthis (?:agreement|contract) (?:gives|provides|"
                     r"offers|grants)\b|\bthe (?:agreement|contract) (?:gives|provides|offers|grants)\b|\bexpress\b",
                     re.I)
_PARTY = r"(?:you|the (?:hirer|tenant|customer|client|employee|recipient|buyer|borrower|freelancer|contractor))"
_NOUN = r"(recourse|remed(?:y|ies)|rights?|claims?|protections?)"
_STRIP = (r"(strips?|stripping|deprives?|depriving|removes?|removing|takes? away|taking away|eliminates?|eliminating|"
          r"extinguish(?:es|ing)?|waives?|waiving|wipes? out|wiping out|denies|denying)")


def _limit(m: re.Match, noun: str) -> str:
    """'strips you of ...' -> 'tries to limit your ...'; 'depriving the Recipient of ...' -> 'limiting the
    Recipient's ...': the verb form and the party are kept."""
    verb, party = m.group(1).lower(), (m.group("party") or "").strip()
    who = "your" if not party or party.lower() == "you" else party + "'s"
    lead = "limiting" if verb.endswith("ing") else "tries to limit"
    return f"{lead} {who} {noun}"


_ABSOLUTE: list[tuple[re.Pattern, Callable[[re.Match], str], bool]] = [
    # (pattern, replacement, applies even when the sentence is scoped to this contract)
    (re.compile(rf"\b{_STRIP}\s+(?:(?P<party>{_PARTY})\s+)?(?:of\s+)?(?:(?:all|any|every)\s+(?:of\s+)?)?"
                rf"(?:your\s+|their\s+)?(?P<kind>(?:legal|statutory)\s+){_NOUN}\b", re.I),
     lambda m: _limit(m, f"{m.group('kind')}{m.group(m.lastindex)}"), True),
    (re.compile(rf"\b{_STRIP}\s+(?:(?P<party>{_PARTY})\s+)?(?:of\s+)?(?:all|any|every)\s+(?:of\s+)?"
                rf"(?:your\s+|their\s+)?{_NOUN}\b", re.I),
     lambda m: _limit(m, m.group(m.lastindex)), True),
    (re.compile(rf"\b(?:lose|loses|losing|lost|forfeits?|forfeiting)\s+(?:all|any|every)\s+(?:of\s+)?(?:your\s+)?"
                rf"((?:legal|statutory)\s+)?{_NOUN}\b", re.I),
     lambda m: f"may lose some of your {m.group(1) or ''}{m.group(2)}", True),
    (re.compile(rf"\bno\s+(?:(?:legal|statutory)\s+){_NOUN}\b", re.I),
     lambda m: f"no express {m.group(1)} in this contract", False),
    (re.compile(r"\b(?:no|without any|zero)\s+(recourse|remed(?:y|ies))(?:\s+(?:at all|whatsoever))?\b", re.I),
     lambda m: ("only limited recourse under this contract" if m.group(1).lower() == "recourse"
                else "only limited remedies under this contract"), False),
]


def soften_absolute(sentence: str) -> str | None:
    """The sentence with absolute loss-of-rights wording replaced, or None if it has none."""
    scoped = bool(_SCOPED.search(sentence))
    s, changed = sentence, False
    for pattern, repl, always in _ABSOLUTE:
        if scoped and not always:
            continue
        m = pattern.search(s)
        if m and not hedged_before(s[:m.start()]):
            s = s[:m.start()] + repl(m) + s[m.end():]
            changed = True
    return (s[0].upper() + s[1:]) if changed else None


# ---- contradiction --------------------------------------------------------------------------------

_CONTRA_GUARD = re.compile(r"\b(?:no|not|never|unlike|regardless|whether|without|even if|even a|rather than|"
                           r"cannot|tiada|tidak|bukan)\b|n't\b", re.I)


def contradicted(sentence: str, entries: Iterable[LawEntry]) -> LawEntry | None:
    """The first entry whose rejected proposition `sentence` states, else None."""
    low = sentence.lower()
    if _CONTRA_GUARD.search(low):
        return None
    for e in entries:
        for stems in e.contradicts:
            if all(re.search(rf"\b{re.escape(s)}", low) for s in stems):
                return e
    return None


def relevant_entries(f_clause: str, cited: set[str], jurisdiction: str | None,
                     law: Iterable[LawEntry]) -> list[LawEntry]:
    return [e for e in law if e.contradicts and e.jurisdiction == jurisdiction
            and (e.id in cited or f_clause in e.clause_types)]


# ---- applying it ----------------------------------------------------------------------------------


def _note(text: str, title: str | None, page: int | None) -> RemovedClaim:
    return RemovedClaim(kind="statement", reason=REASON, text=text, page=page,  # type: ignore[arg-type]
                        finding_title=title)


def restrain_text(text: str, *, where: str, entries: list[LawEntry], is_backed: Callable[[str, str], bool],
                  removed: list[RemovedClaim], title: str | None = None, page: int | None = None,
                  certify: bool = True, library: tuple[str, ...] = ()) -> str:
    """Apply checks 1-4 to each sentence of `text`; returns what is kept (possibly rewritten)."""
    out = []
    for s in sentences(text):
        if any(s.strip() in c for c in library):  # the library's own caveat words are not the model's claim
            out.append(s)
            continue
        if contradicted(s, entries) or (certify and _CERTIFY.search(s)):
            removed.append(_note(f"{where}: {s}", title, page))
            continue
        def unbacked(x: str) -> list[str]:
            return [fam for fam in unhedged_outcomes(x) if not is_backed(fam, x)]

        new = s
        if unbacked(new):
            hedged = hedge_outcome(new, unbacked)
            if hedged is None:
                removed.append(_note(f"{where}: {s}", title, page))
                continue
            new = hedged
        softened = soften_absolute(new)
        if softened:
            new = softened
        if new != s:
            removed.append(_note(f"{where}: {s}", title, page))
        out.append(new)
    return " ".join(out)


_CLAUSE_IN = re.compile(r"\b(?:clause|fasal|klausa|section)\s+(\d{1,2}(?:\.\d{1,2}){0,3})", re.I)


def restrain_review(findings: list[Finding], missing: list[MissingClause], summary: list[str], headline: str,
                    level: str, jurisdiction: str | None, law: list[LawEntry],
                    law_by_id: Callable[[str], LawEntry | None],
                    removed: list[RemovedClaim]) -> tuple[list[str], str]:
    """Edit findings and missing clauses in place; return the kept summary and the headline."""
    all_entries: dict[str, LawEntry] = {}
    library = tuple(e.caveat for e in law if e.caveat)
    for f in findings:
        cited = {c.ref for c in f.citations}
        entries = relevant_entries(f.clause_type, cited, jurisdiction, law)
        all_entries.update({e.id: e for e in entries})

        def is_backed(family: str, _s: str, f=f) -> bool:
            return backed(f, family, law_by_id)

        why = restrain_text(f.why_risky, where="Explanation", entries=entries, is_backed=is_backed,
                            removed=removed, title=f.title, page=f.page, library=library)
        f.why_risky = why or FALLBACK_WHY
        std = restrain_text(f.standard, where="Standard", entries=entries, is_backed=is_backed,
                            removed=removed, title=f.title, page=f.page, library=library)
        f.standard = std or "A balanced version of this clause; see the suggested change."
    for m in missing:
        entries = relevant_entries(m.clause_type, set(), jurisdiction, law)
        all_entries.update({e.id: e for e in entries})
        why = restrain_text(m.why_it_matters, where=f"Missing clause ({m.title})", entries=entries,
                            is_backed=lambda fam, s: False, removed=removed, title=m.title, library=library)
        m.why_it_matters = why or f"The contract has no clause on {m.title.lower()}."

    entries = list(all_entries.values())

    def summary_backed(family: str, s: str) -> bool:
        named = set(_CLAUSE_IN.findall(s))
        pool = [f for f in findings if not named or (f.clause_ref and f.clause_ref in named)]
        return any(backed(f, family, law_by_id) for f in pool)

    kept = []
    for s in summary:
        new = restrain_text(s, where="Summary", entries=entries, is_backed=summary_backed, removed=removed,
                            library=library)
        if new:
            kept.append(new)
    new_head = restrain_text(headline, where="Headline", entries=entries, is_backed=summary_backed, removed=removed,
                            library=library)
    if not new_head or len(new_head) > 160:
        new_head = LEVEL_HEADLINE[level]
    return kept, new_head
