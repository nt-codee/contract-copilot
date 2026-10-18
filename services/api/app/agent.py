"""The bounded review agent: research with tools, then one structured call.

Imports only ports, models, prompts, knowledge and config. No cloud SDK. The LLM adapter turns
ToolSpec/ChatMessage into whatever the SDK needs; this module runs the loop and owns the caps.

Loop (latency is the constraint, every model call costs seconds): turn 1 forces the model to plan and
emit ALL its lookups at once; they run in parallel. One optional follow-up turn happens only if turn 1
asked for read_page (it may read more pages). Then a final structured call (LLMReview) sees the
research. At most MAX_TURNS model turns and MAX_TOOL_CALLS tool calls in total.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from typing import get_args
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app import prompts
from app.knowledge import PLAYBOOK_EXCERPT, PLAIN_EXCERPT, TEXT_EXCERPT, TOP_K, Knowledge, clip, playbook_view
from app.models import Citation, ClauseType, ContractType, LLMReview, TraceStep
from app.playbook_overrides import SUFFIX, PlaybookOverrideStore, override_excerpt, override_ref
from app.ports import (LLM, ChatMessage, CompanyStore, Embedder, LLMUsage, ToolCall, ToolResult,
                       ToolSpec, UpstreamError)

log = logging.getLogger("cc.agent")

MAX_TURNS = 2
MAX_TOOL_CALLS = 16
MAX_RESULT_CHARS = 3200  # law results also carry each entry's supports scope and caveat
MAX_PAGE_CHARS = 4000
COMPANY_EXCERPT = 600
CITATION_EXCERPT = 400

# Taken from the models (which mirror shared/contracts), so a clause or contract type added there is
# available to search_playbook without a second edit here. This list used to be hard-coded to the first
# 16 clause types and two contract types, which hid the employment, tenancy and hire-purchase playbooks.
CLAUSE_TYPES = list(get_args(ClauseType))
PLAYBOOK_CONTRACT_TYPES = [t for t in get_args(ContractType) if t != "other"]

JURISDICTION_NAME = {"my": "Malaysia", "sg": "Singapore"}

TOOLS = [
    ToolSpec(
        "set_jurisdiction",
        "Fix which country's law applies to this contract, from its governing-law clause. Call it ONCE, in "
        "your first turn, together with your other lookups. Every search_law then searches that country's "
        "law only, whatever jurisdiction it is given.",
        {"type": "object", "properties": {
            "governing_law": {"type": "string",
                              "description": "the law the contract names, in words, e.g. 'the laws of Malaysia' "
                                             "or 'the laws of the State of New York'; 'not stated' if none"},
            "jurisdiction": {"type": "string", "enum": ["my", "sg", "other"],
                             "description": "my = Malaysia, sg = Singapore, other = any other law; my if unclear"},
        }, "required": ["governing_law", "jurisdiction"]},
    ),
    ToolSpec(
        "search_law",
        "Search statutes and cases relevant to a contract clause, in the jurisdiction fixed by "
        "set_jurisdiction (Malaysia or Singapore). Returns up to 4 entries with a ref, citation, excerpt of "
        "the text and a plain-words note.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "keywords, e.g. 'penalty liquidated damages'"},
            "jurisdiction": {"type": "string", "enum": ["my", "sg"],
                             "description": "ignored once set_jurisdiction has fixed it"},
            "clause_type": {"type": "string", "enum": CLAUSE_TYPES,
                            "description": "optional; boosts entries about this clause type"},
        }, "required": ["query"]},
    ),
    ToolSpec(
        "search_playbook",
        "Get the balanced, market-standard position for one clause type of one contract type.",
        {"type": "object", "properties": {
            "contract_type": {"type": "string", "enum": PLAYBOOK_CONTRACT_TYPES},
            "clause_type": {"type": "string", "enum": CLAUSE_TYPES},
        }, "required": ["contract_type", "clause_type"]},
    ),
    ToolSpec(
        "search_company",
        "Search the user's own company policy documents. Returns up to 4 passages, or none if the user "
        "has uploaded no documents.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
    ToolSpec(
        "read_page",
        "Return the full text of one page of the contract being reviewed.",
        {"type": "object", "properties": {"n": {"type": "integer", "description": "1-based page number"}},
         "required": ["n"]},
    ),
]

# ---- the trace: plain words for the user, built from the tool name and arguments ---------------

# (pattern on the lower-cased query, phrase). First match wins, so specific entries come first.
_TOPICS: list[tuple[str, str]] = [
    (r"penalt|liquidated", "penalty clauses"),
    (r"non[- ]?compete|restraint|compete", "non-compete clauses"),
    (r"solicit|poach", "non-solicitation clauses"),
    (r"indemn", "indemnities"),
    (r"liab|\bcaps?\b|exclusion|exclude", "liability caps"),
    (r"renew", "automatic renewal"),
    (r"terminat", "ending the contract"),
    (r"confiden|disclos|secret", "confidentiality"),
    (r"personal data|pdpa|privacy|data protection|breach notif", "personal data protection"),
    (r"governing|jurisdiction|foreign law|choice of law", "governing law"),
    (r"dispute|arbitrat|court|mediat", "resolving disputes"),
    (r"payment|invoice|fees?\b|interest|late", "payment terms"),
    (r"intellectual|copyright|\bip\b|ownership", "ownership of work and IP"),
    (r"warrant", "warranties"),
    (r"assign|novat", "transferring the contract"),
    (r"limitation period|time bar|limitation act", "time limits for claims"),
    (r"\bterm\b|duration", "how long the contract runs"),
]
_CLAUSE_PHRASE = {
    "probation": "probation", "working_hours": "working hours", "leave": "leave", "notice_period": "notice periods",
    "salary_deductions": "deductions from pay", "deposit": "deposits", "rent_review": "rent reviews",
    "repairs": "repairs", "early_termination": "ending early", "interest_rate": "interest rates",
    "repossession": "repossession", "guarantor": "guarantors", "insurance": "insurance",
    "fees_charges": "fees and charges",
    "confidentiality": "confidentiality", "term": "how long the contract runs",
    "termination": "ending the contract", "auto_renewal": "automatic renewal", "liability": "liability caps",
    "indemnity": "indemnities", "ip_ownership": "ownership of work and IP", "payment": "payment terms",
    "governing_law": "governing law", "dispute_resolution": "resolving disputes",
    "non_solicit": "non-solicitation clauses", "non_compete": "non-compete clauses",
    "data_protection": "personal data protection", "assignment": "transferring the contract",
    "warranty": "warranties", "other": "other terms",
}
_JURISDICTION_NAME = {"my": "Malaysian", "sg": "Singapore"}
_CONTRACT_NAME = {"nda": "an NDA", "service_agreement": "a service agreement", "employment": "an employment or "
                  "freelance contract", "tenancy": "a tenancy", "hire_purchase": "a hire purchase or loan agreement"}


def topic_phrase(query: str, clause_type: str | None = None) -> str:
    q = (query or "").lower()
    for pattern, phrase in _TOPICS:
        if re.search(pattern, q):
            return phrase
    if clause_type in _CLAUSE_PHRASE:
        return _CLAUSE_PHRASE[clause_type]
    q = " ".join(q.split())
    return f"\u201c{clip(q, 50)}\u201d" if q else "this clause"


def tool_summary(name: str, args: dict) -> str:
    """One plain-words sentence for the user. Built here, never by the model."""
    if name == "search_law":
        jur = _JURISDICTION_NAME.get(_jurisdiction(args.get("jurisdiction")) or "", "Malaysian")
        return f"Checked {jur} law on {topic_phrase(args.get('query', ''), args.get('clause_type'))}"
    if name == "search_playbook":
        kind = _CONTRACT_NAME.get(str(args.get("contract_type")), "this kind of contract")
        return f"Looked up the standard terms for {topic_phrase('', args.get('clause_type'))} in {kind}"
    if name == "search_company":
        return f"Checked your company's rules on {topic_phrase(args.get('query', ''))}"
    if name == "read_page":
        return f"Re-read page {args.get('n', '?')} of the contract"
    return "Looked something up"


class Trace:
    """Numbered TraceSteps, appended from any phase of the pipeline."""

    def __init__(self, origin: float | None = None) -> None:
        self.origin = time.monotonic() if origin is None else origin
        self.steps: list[TraceStep] = []

    def add(self, action: str, summary: str, detail: str | None = None, ms: float | None = None, *,
            start: float | None = None, turn: int | None = None, batch: int | None = None) -> None:
        self.steps.append(TraceStep(step=len(self.steps) + 1, action=action, summary=summary,  # type: ignore[arg-type]
                                    detail=detail, ms=int(ms) if ms is not None else None,
                                    start_ms=int((start - self.origin) * 1000) if start is not None else None,
                                    turn=turn, batch=batch))


# ---- tools ----------------------------------------------------------------------------------


def _result(src: Citation, score: float | None) -> dict:
    return {"ref": src.ref, "title": src.title, "kind": src.kind, "score": score}


def supports_line(e) -> str:
    """What a citation of `e` may back, as the model reads it. Verification enforces the same scope."""
    parts = []
    for clause, stems in e.scope().items():
        name = "any clause" if clause == "*" else clause
        parts.append(f"{name} (only if the quoted clause mentions: {', '.join(stems[:4])})" if stems else name)
    return "cite only on findings of type: " + "; ".join(parts)


def _jurisdiction(value) -> str | None:
    v = str(value or "").strip().lower()
    return {"my": "my", "malaysia": "my", "malaysian": "my", "sg": "sg", "singapore": "sg"}.get(v)


def _shrink(items: list[dict]) -> None:
    """Clip the long text fields of every item by 15%, in place."""
    for it in items:
        for key in ("excerpt", "plain", "caveat"):
            v = it.get(key)
            if isinstance(v, str) and len(v) > 40:
                it[key] = clip(v, int(len(v) * 0.85))


def compact(items: list[dict], extra: dict | None = None, limit: int = MAX_RESULT_CHARS) -> str:
    """JSON for the model, at most `limit` chars. Clips excerpts before it would drop an entry."""
    def dump() -> str:
        return json.dumps({"results": items, **(extra or {})}, ensure_ascii=False)

    text = dump()
    for _ in range(40):
        if len(text) <= limit:
            return text
        _shrink(items)
        text = dump()
    while len(text) > limit and len(items) > 1:
        items.pop()
        text = dump()
    return text if len(text) <= limit else text[:limit]


@dataclass
class ToolOutput:
    payload: str  # JSON text returned to the model (<= MAX_RESULT_CHARS)
    sources: list[Citation] = field(default_factory=list)  # KB records the payload actually contained
    detail: str | None = None
    results: list[dict] = field(default_factory=list)  # [{ref, title, kind, score}] for the trace


def trace_detail(call: ToolCall, out: ToolOutput) -> str:
    """What the agent looked up, what it found; `cited` is filled in after citation verification."""
    return json.dumps({"tool": call.name, "args": call.args, "results": out.results, "cited": []},
                      ensure_ascii=False)


def fill_cited(steps: list[TraceStep], cited_refs: set[str]) -> None:
    """Mark, on each tool step, which of its results ended up cited in the findings."""
    for step in steps:
        if not step.detail or not step.detail.startswith("{"):
            continue
        try:
            d = json.loads(step.detail)
        except ValueError:
            continue
        if isinstance(d, dict) and "results" in d:
            d["cited"] = [r["ref"] for r in d["results"] if r.get("ref") in cited_refs]
            step.detail = json.dumps(d, ensure_ascii=False)


class Toolbox:
    def __init__(self, kb: Knowledge, company: CompanyStore, embedder: Embedder, pages: list[str], *,
                 owner: str, overrides: PlaybookOverrideStore | None = None, home: str = "my"):
        """`owner` scopes the company-policy lookups and the playbook overrides to one workspace."""
        self.kb, self.company, self.embedder, self.pages = kb, company, embedder, pages
        self.owner, self.overrides, self.home = owner, overrides, home
        self.company_rules = True  # False: the reader switched company rules off for this review
        self.company_doc_ids: frozenset[str] | None = None  # only these policy documents; None: all of them
        self.jurisdiction: str | None = None  # my | sg | other, once fixed
        self.governing_law: str | None = None
        self.jurisdiction_source: str | None = None  # "tool" | "majority"

    def lock(self, jurisdiction: str, governing_law: str | None, source: str) -> None:
        if self.jurisdiction is None:
            self.jurisdiction, self.governing_law, self.jurisdiction_source = jurisdiction, governing_law, source

    def search_jurisdiction(self, requested) -> str | None:
        """The jurisdiction a law search really uses: the fixed one (home for a foreign-law contract), else the argument."""
        if self.jurisdiction in ("my", "sg"):
            return self.jurisdiction
        if self.jurisdiction == "other":
            return self.home
        return _jurisdiction(requested)

    def run(self, call: ToolCall) -> ToolOutput:
        try:
            fn = {"set_jurisdiction": self._set_jurisdiction, "search_law": self._law,
                  "search_playbook": self._playbook,
                  "search_company": self._company, "read_page": self._page}.get(call.name)
            if fn is None:
                return ToolOutput(json.dumps({"error": f"unknown tool {call.name}"}))
            return fn(call.args or {})
        except UpstreamError:
            log.warning("tool %s failed upstream", call.name)
            return ToolOutput(json.dumps({"error": "that lookup is unavailable right now"}))
        except Exception:  # a broken tool must not end the review; the model sees the error
            log.exception("tool %s failed", call.name)
            return ToolOutput(json.dumps({"error": "that lookup failed"}))

    def _set_jurisdiction(self, a: dict) -> ToolOutput:
        law = " ".join(str(a.get("governing_law") or "").split())[:120] or None
        raw = str(a.get("jurisdiction") or "").strip().lower()
        jur = raw if raw in ("my", "sg", "other") else _jurisdiction(raw)
        if jur is None:
            return ToolOutput(json.dumps({"error": "jurisdiction must be 'my', 'sg' or 'other'"}))
        if self.jurisdiction is not None:  # first one wins: the lock never moves
            return ToolOutput(json.dumps({"jurisdiction": self.jurisdiction, "note": "already set; unchanged"}))
        self.lock(jur, law, "tool")
        uses = self.search_jurisdiction(None)
        return ToolOutput(json.dumps({"jurisdiction": jur, "law_searches_use": uses,
                                      **({"note": f"foreign law: {JURISDICTION_NAME[uses]} law is checked "
                                                  "only as a fallback"} if jur == "other" else {})}),
                          detail=f"{law or 'governing law not stated'} -> {jur}")

    def _law(self, a: dict) -> ToolOutput:
        asked = _jurisdiction(a.get("jurisdiction"))
        jur = self.search_jurisdiction(a.get("jurisdiction"))
        query = str(a.get("query") or "").strip()
        if jur is None or not query:
            return ToolOutput(json.dumps({"error": "give a query (and a jurisdiction of 'my' or 'sg' "
                                                   "unless set_jurisdiction was called)"}))
        a = {**a, "jurisdiction": jur}
        overridden = asked is not None and asked != jur
        hits = self.kb.search_law_scored(query, jur, a.get("clause_type") or None, TOP_K)
        items, sources, scores = [], [], {}
        for e, score in hits:
            excerpt, plain = clip(e.text, TEXT_EXCERPT), clip(e.plain, PLAIN_EXCERPT)
            item = {"ref": e.id, "title": e.title, "citation": e.citation, "excerpt": excerpt,
                    "plain": plain, "url": e.link, "supports": supports_line(e)}
            if e.caveat:
                item["caveat"] = e.caveat
            items.append(item)
            kind = "case" if e.kind == "case" else "law"
            sources.append(Citation(kind=kind, ref=e.id, title=e.title, jurisdiction=e.jurisdiction,
                                    excerpt=excerpt, url=e.source_url or None, source_url=e.link or None))
            scores[e.id] = round(score, 4)
        extra = {} if hits else {"note": "no matching law entries"}
        if overridden:
            extra["searched"] = f"{JURISDICTION_NAME[jur]} law only: that is the governing jurisdiction"
        payload = compact(items, extra)
        kept = {i["ref"] for i in json.loads(payload)["results"]}
        sources = [s for s in sources if s.ref in kept]
        return ToolOutput(payload, sources, "; ".join(s.title for s in sources) or "no matches",
                          [{"ref": s.ref, "title": s.title, "kind": s.kind, "score": scores[s.ref]} for s in sources])

    def _playbook(self, a: dict) -> ToolOutput:
        ctype, clause = str(a.get("contract_type") or ""), str(a.get("clause_type") or "")
        e = self.kb.playbook_entry(ctype, clause)
        ov = self.overrides.get(self.owner, ctype, clause) if self.overrides else None
        if ov is not None:  # the company's own position wins over the default
            title = f"{e.title if e else clause.replace('_', ' ').capitalize()}{SUFFIX}"
            ref = override_ref(self.owner, ctype, clause)
            item = {"ref": ref, "title": title, "excerpt": override_excerpt(ov, PLAYBOOK_EXCERPT)}
            src = Citation(kind="playbook", ref=ref, title=title, excerpt=override_excerpt(ov, CITATION_EXCERPT))
            return ToolOutput(compact([item]), [src], title, [_result(src, 1.0)])
        if e is None:
            have = sorted(k.split("/", 1)[1] for k in self.kb.playbook if k.startswith(f"{ctype}/"))
            near = getattr(self.kb, "related_playbook", lambda *_: [])(ctype, clause)
            items = [{"ref": n.ref, "title": n.title, "excerpt": playbook_view(n)} for n in near]
            srcs = [Citation(kind="playbook", ref=n.ref, title=n.title, excerpt=clip(n.balanced, CITATION_EXCERPT))
                    for n in near]
            payload = compact(items, {"note": f"no playbook entry for {ctype}/{clause}; closest entries shown",
                                      "available_clause_types": have})
            srcs = srcs[:len(json.loads(payload)["results"])]
            return ToolOutput(payload, srcs, "no exact entry", [_result(s, None) for s in srcs])
        item = {"ref": e.ref, "title": e.title, "excerpt": playbook_view(e)}
        src = Citation(kind="playbook", ref=e.ref, title=e.title, excerpt=clip(e.balanced, CITATION_EXCERPT))
        return ToolOutput(compact([item]), [src], e.title, [_result(src, 1.0)])

    def _company(self, a: dict) -> ToolOutput:
        if not self.company_rules:
            return ToolOutput(json.dumps({"results": [], "note": "company rules are switched off for this review"}), [],
                              "company rules switched off")
        query = str(a.get("query") or "").strip()
        if not query:
            return ToolOutput(json.dumps({"error": "give a query"}))
        if not self.company.list(owner=self.owner):
            return ToolOutput(json.dumps({"results": [], "note": "no company documents uploaded"}), [],
                              "no company documents")
        vectors, _ = self.embedder.embed([query], query=True)
        hits = self.company.search(vectors[0], TOP_K, owner=self.owner, doc_ids=self.company_doc_ids)
        items, sources = [], []
        for c, _score in hits:
            items.append({"ref": f"company:{c.doc_id}", "title": c.title, "page": c.page,
                          "excerpt": clip(c.text, COMPANY_EXCERPT)})
            sources.append(Citation(kind="company", ref=f"company:{c.doc_id}", title=c.title, page=c.page,
                                    excerpt=clip(c.text, CITATION_EXCERPT), doc_id=c.doc_id))
        payload = compact(items, {} if hits else {"note": "no matching passages"})
        sources = sources[:len(json.loads(payload)["results"])]
        return ToolOutput(payload, sources, "; ".join(f"{s.title} p.{s.page}" for s in sources) or "no matches",
                          [_result(s, round(float(sc), 4)) for s, (_, sc) in zip(sources, hits)])

    def _page(self, a: dict) -> ToolOutput:
        try:
            n = int(a.get("n"))
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= len(self.pages):
            return ToolOutput(json.dumps({"error": f"the contract has pages 1 to {len(self.pages)}"}))
        return ToolOutput(json.dumps({"page": n, "text": self.pages[n - 1].strip()[:MAX_PAGE_CHARS]},
                                     ensure_ascii=False), [], f"page {n}",
                          [{"ref": f"page:{n}", "title": f"Contract page {n}", "kind": "page", "score": None}])


# ---- the loop -------------------------------------------------------------------------------


@dataclass
class AgentOutcome:
    review: LLMReview
    known: dict[str, list[Citation]]  # ref -> KB records a tool returned in this run
    usage: LLMUsage
    turns: int = 0
    tool_calls: int = 0
    jurisdiction: str | None = None  # my | sg | other when it was fixed during research
    governing_law: str | None = None
    jurisdiction_source: str | None = None  # "tool" (set_jurisdiction) or "majority" (of the search_law calls)
    outlined: bool = False  # the planning turn read an outline because the contract is long


def _lock_jurisdiction(tools: Toolbox, calls: list[ToolCall]) -> tuple[list[ToolCall], list[ToolCall]]:
    """Fix the jurisdiction before any search runs; return (calls to run, calls skipped).

    set_jurisdiction calls run first, one by one, so that the searches in the same turn already see the
    lock. If the model never called it but its search_law calls name more than one country, the country
    most of them name is locked and only the searches for that country run (the home country wins a tie).
    """
    for c in [c for c in calls if c.name == "set_jurisdiction"]:
        tools.run(c)  # idempotent: the first valid call wins; the real answer is produced again below
    if tools.jurisdiction is not None:
        return calls, []
    named = [_jurisdiction(c.args.get("jurisdiction")) for c in calls if c.name == "search_law"]
    votes = Counter(j for j in named if j)
    if len(votes) < 2:
        return calls, []
    top = max(votes.values())
    winners = [j for j, n in votes.items() if n == top]
    chosen = tools.home if tools.home in winners else winners[0]
    tools.lock(chosen, None, "majority")
    keep, skipped = [], []
    for c in calls:
        wrong = c.name == "search_law" and _jurisdiction(c.args.get("jurisdiction")) not in (None, chosen)
        (skipped if wrong else keep).append(c)
    log.info("search_law named %s; locked to %s and skipped %d searches", dict(votes), chosen, len(skipped))
    return keep, skipped


def _run_all(tools: Toolbox, calls: list[ToolCall]) -> list[tuple[ToolOutput, float, float]]:
    """Run the calls in parallel, in order of the list."""
    with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
        return list(pool.map(_timed(tools), calls))


def _fixed_line(tools: Toolbox) -> str | None:
    """Tell the final call which jurisdiction to report."""
    if tools.jurisdiction is None:
        return None
    law = tools.governing_law or "not stated"
    line = f"GOVERNING LAW (fixed during research): {law}. Set jurisdiction to \"{tools.jurisdiction}\"."
    if tools.jurisdiction == "other":
        line += (f" Law searches used {JURISDICTION_NAME[tools.home]} law only as a fallback; cite it as a "
                 "fallback, not as the law that governs the contract.")
    return line


def clause_vocabulary(kb) -> dict[str, list[str]]:
    """contract type -> the clause types its playbook covers, from the playbook files themselves."""
    vocab: dict[str, list[str]] = {}
    for ref in sorted(kb.playbook):
        ctype, clause = ref.split("/", 1)
        vocab.setdefault(ctype, []).append(clause)
    return vocab


def _timed(tools: Toolbox):
    def run(call: ToolCall) -> tuple[ToolOutput, float, float]:
        t0 = time.monotonic()
        out = tools.run(call)
        return out, (time.monotonic() - t0) * 1000, t0

    return run


def apply_options(tools: Toolbox, options) -> list[ToolSpec]:
    """Put the reader's choices into effect and return the tools the model may use.

    A chosen jurisdiction is locked before the first turn (the lock never moves), so set_jurisdiction is withdrawn;
    switching company rules off withdraws search_company and makes the tool itself refuse."""
    specs = list(TOOLS)
    if options is None:
        return specs
    if options.jurisdiction != "auto":
        name = {"MY": "Malaysian", "SG": "Singapore"}[options.jurisdiction]
        tools.lock(options.jurisdiction.lower(), f"chosen by the reader: {name} law", "user")
        specs = [t for t in specs if t.name != "set_jurisdiction"]
    if not options.apply_company_rules:
        tools.company_rules = False
        specs = [t for t in specs if t.name != "search_company"]
    elif options.company_doc_ids:
        tools.company_doc_ids = frozenset(options.company_doc_ids)
    return specs


def run_agent(llm: LLM, tools: Toolbox, pages: list[str], trace: Trace, *,
              max_turns: int = MAX_TURNS, max_tool_calls: int = MAX_TOOL_CALLS, options=None) -> AgentOutcome:
    vocabulary = clause_vocabulary(tools.kb)
    specs = apply_options(tools, options)
    system = prompts.agent_system(pages, max_tool_calls, vocabulary, options=options)  # the whole contract: the review call
    plan_system = prompts.agent_system(pages, max_tool_calls, vocabulary, outline=True, options=options)  # planning turns
    outlined = plan_system != system
    usage = LLMUsage(model=llm.model)
    messages = [ChatMessage(role="user", text=prompts.AGENT_START)]
    known: dict[str, list[Citation]] = {}
    research: list[tuple[str, dict, str]] = []
    n_calls = turns = batches = 0
    wants_pages = True  # turn 1 always runs; later turns only if the previous one asked for read_page

    while turns < max_turns and n_calls < max_tool_calls and wants_pages:
        t_plan = time.monotonic()
        turns += 1
        failed = False
        try:
            reply, u = llm.chat(system=plan_system, messages=messages, tools=specs, purpose="agent",
                                force_tools=(turns == 1))
        except UpstreamError:
            failed = True
            if turns == 1:
                raise
            log.warning("agent follow-up turn failed; writing the review with what we have")
            break
        finally:
            trace.add("plan_research", "Planned the research",
                      "Planning call failed" if failed else None,
                      (time.monotonic() - t_plan) * 1000, start=t_plan, turn=turns)
        usage.add(u)
        # after turn 1 only read_page may run: the plan was supposed to be complete
        calls = [c for c in reply.tool_calls if turns == 1 or c.name == "read_page"]
        if not calls:
            break
        messages.append(reply)
        run_now = calls[:max_tool_calls - n_calls]  # calls beyond the budget are ignored
        run_now, skipped = _lock_jurisdiction(tools, run_now)
        batches += 1
        timed = _run_all(tools, run_now)
        results: list[ToolResult] = []
        for call in skipped:  # a function call always needs an answer in the history
            results.append(ToolResult(name=call.name, id=call.id, result={
                "error": "skipped: this search named a different country than the governing law"}))
        for call, (out, ms, started) in zip(run_now, timed, strict=True):
            n_calls += 1
            results.append(ToolResult(name=call.name, result=json.loads(out.payload), id=call.id))
            research.append((call.name, call.args, out.payload))
            for s in out.sources:
                known.setdefault(s.ref, []).append(s)
            if call.name == "set_jurisdiction":
                continue  # not a lookup; the fixed jurisdiction is shown on the first trace step
            shown = {**call.args, "jurisdiction": tools.search_jurisdiction(call.args.get("jurisdiction"))} \
                if call.name == "search_law" else call.args
            trace.add(call.name, tool_summary(call.name, shown), trace_detail(call, out), ms,
                      start=started, turn=turns, batch=batches)
        messages.append(ChatMessage(role="tool", tool_results=results))
        wants_pages = any(c.name == "read_page" for c in run_now)

    t0 = time.monotonic()
    out, u = llm.generate(system=system, parts=[prompts.research_transcript(research, _fixed_line(tools)),
                                                prompts.FINAL_USER],
                          schema=LLMReview, purpose="review")
    usage.add(u)
    trace.add("draft_findings", "Wrote the findings and the plain-English summary",
              f"{len(out.findings)} candidate findings", (time.monotonic() - t0) * 1000,
              start=t0, turn=turns + 1)
    return AgentOutcome(review=out, known=known, usage=usage, turns=turns, tool_calls=n_calls,
                        jurisdiction=tools.jurisdiction, governing_law=tools.governing_law,
                        jurisdiction_source=tools.jurisdiction_source, outlined=outlined)
