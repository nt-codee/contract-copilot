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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app import prompts
from app.knowledge import PLAYBOOK_EXCERPT, PLAIN_EXCERPT, TEXT_EXCERPT, TOP_K, Knowledge, clip
from app.models import Citation, LLMReview, TraceStep
from app.ports import (LLM, ChatMessage, CompanyStore, Embedder, LLMUsage, ToolCall, ToolResult,
                       ToolSpec, UpstreamError)

log = logging.getLogger("cc.agent")

MAX_TURNS = 2
MAX_TOOL_CALLS = 16
MAX_RESULT_CHARS = 2500
MAX_PAGE_CHARS = 4000
COMPANY_EXCERPT = 600
CITATION_EXCERPT = 400

CLAUSE_TYPES = [
    "confidentiality", "term", "termination", "auto_renewal", "liability", "indemnity", "ip_ownership",
    "payment", "governing_law", "dispute_resolution", "non_solicit", "non_compete", "data_protection",
    "assignment", "warranty", "other",
]

TOOLS = [
    ToolSpec(
        "search_law",
        "Search Malaysian or Singapore statutes and cases relevant to a contract clause. Returns up to "
        "4 entries with a ref, citation, excerpt of the text and a plain-words note.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "keywords, e.g. 'penalty liquidated damages'"},
            "jurisdiction": {"type": "string", "enum": ["my", "sg"]},
            "clause_type": {"type": "string", "enum": CLAUSE_TYPES,
                            "description": "optional; boosts entries about this clause type"},
        }, "required": ["query", "jurisdiction"]},
    ),
    ToolSpec(
        "search_playbook",
        "Get the balanced, market-standard position for one clause type of one contract type.",
        {"type": "object", "properties": {
            "contract_type": {"type": "string", "enum": ["nda", "service_agreement"]},
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
    "confidentiality": "confidentiality", "term": "how long the contract runs",
    "termination": "ending the contract", "auto_renewal": "automatic renewal", "liability": "liability caps",
    "indemnity": "indemnities", "ip_ownership": "ownership of work and IP", "payment": "payment terms",
    "governing_law": "governing law", "dispute_resolution": "resolving disputes",
    "non_solicit": "non-solicitation clauses", "non_compete": "non-compete clauses",
    "data_protection": "personal data protection", "assignment": "transferring the contract",
    "warranty": "warranties", "other": "other terms",
}
_JURISDICTION_NAME = {"my": "Malaysian", "sg": "Singapore"}
_CONTRACT_NAME = {"nda": "an NDA", "service_agreement": "a service agreement"}


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

    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    def add(self, action: str, summary: str, detail: str | None = None, ms: float | None = None) -> None:
        self.steps.append(TraceStep(step=len(self.steps) + 1, action=action, summary=summary,  # type: ignore[arg-type]
                                    detail=detail, ms=int(ms) if ms is not None else None))


# ---- tools ----------------------------------------------------------------------------------


def _jurisdiction(value) -> str | None:
    v = str(value or "").strip().lower()
    return {"my": "my", "malaysia": "my", "malaysian": "my", "sg": "sg", "singapore": "sg"}.get(v)


def _shrink(items: list[dict]) -> None:
    """Clip the long text fields of every item by 15%, in place."""
    for it in items:
        for key in ("excerpt", "plain"):
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


class Toolbox:
    def __init__(self, kb: Knowledge, company: CompanyStore, embedder: Embedder, pages: list[str]):
        self.kb, self.company, self.embedder, self.pages = kb, company, embedder, pages

    def run(self, call: ToolCall) -> ToolOutput:
        try:
            fn = {"search_law": self._law, "search_playbook": self._playbook,
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

    def _law(self, a: dict) -> ToolOutput:
        jur = _jurisdiction(a.get("jurisdiction"))
        query = str(a.get("query") or "").strip()
        if jur is None or not query:
            return ToolOutput(json.dumps({"error": "give a query and a jurisdiction of 'my' or 'sg'"}))
        hits = self.kb.search_law(query, jur, a.get("clause_type") or None, TOP_K)
        items, sources = [], []
        for e in hits:
            excerpt, plain = clip(e.text, TEXT_EXCERPT), clip(e.plain, PLAIN_EXCERPT)
            items.append({"ref": e.id, "title": e.title, "citation": e.citation, "excerpt": excerpt,
                          "plain": plain, "url": e.source_url})
            sources.append(Citation(kind="case" if e.kind == "case" else "law", ref=e.id, title=e.title,
                                    jurisdiction=e.jurisdiction, excerpt=excerpt, url=e.source_url or None))
        payload = compact(items, {} if hits else {"note": "no matching law entries"})
        kept = {i["ref"] for i in json.loads(payload)["results"]}
        sources = [s for s in sources if s.ref in kept]
        return ToolOutput(payload, sources, "; ".join(s.title for s in sources) or "no matches")

    def _playbook(self, a: dict) -> ToolOutput:
        ctype, clause = str(a.get("contract_type") or ""), str(a.get("clause_type") or "")
        e = self.kb.playbook_entry(ctype, clause)
        if e is None:
            have = sorted(k.split("/", 1)[1] for k in self.kb.playbook if k.startswith(f"{ctype}/"))
            return ToolOutput(json.dumps({"results": [], "note": f"no playbook entry for {ctype}/{clause}",
                                          "available_clause_types": have}), [], "no entry")
        item = {"ref": e.ref, "title": e.title, "excerpt": clip(e.text, PLAYBOOK_EXCERPT)}
        src = Citation(kind="playbook", ref=e.ref, title=e.title, excerpt=clip(e.balanced, CITATION_EXCERPT))
        return ToolOutput(compact([item]), [src], e.title)

    def _company(self, a: dict) -> ToolOutput:
        query = str(a.get("query") or "").strip()
        if not query:
            return ToolOutput(json.dumps({"error": "give a query"}))
        if not self.company.list():
            return ToolOutput(json.dumps({"results": [], "note": "no company documents uploaded"}), [],
                              "no company documents")
        vectors, _ = self.embedder.embed([query], query=True)
        hits = self.company.search(vectors[0], TOP_K)
        items, sources = [], []
        for c, _score in hits:
            items.append({"ref": f"company:{c.doc_id}", "title": c.title, "page": c.page,
                          "excerpt": clip(c.text, COMPANY_EXCERPT)})
            sources.append(Citation(kind="company", ref=f"company:{c.doc_id}", title=c.title, page=c.page,
                                    excerpt=clip(c.text, CITATION_EXCERPT)))
        payload = compact(items, {} if hits else {"note": "no matching passages"})
        sources = sources[:len(json.loads(payload)["results"])]
        return ToolOutput(payload, sources, "; ".join(f"{s.title} p.{s.page}" for s in sources) or "no matches")

    def _page(self, a: dict) -> ToolOutput:
        try:
            n = int(a.get("n"))
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= len(self.pages):
            return ToolOutput(json.dumps({"error": f"the contract has pages 1 to {len(self.pages)}"}))
        return ToolOutput(json.dumps({"page": n, "text": self.pages[n - 1].strip()[:MAX_PAGE_CHARS]},
                                     ensure_ascii=False), [], f"page {n}")


# ---- the loop -------------------------------------------------------------------------------


@dataclass
class AgentOutcome:
    review: LLMReview
    known: dict[str, list[Citation]]  # ref -> KB records a tool returned in this run
    usage: LLMUsage
    turns: int = 0
    tool_calls: int = 0


def _timed(tools: Toolbox):
    def run(call: ToolCall) -> tuple[ToolOutput, float]:
        t0 = time.monotonic()
        out = tools.run(call)
        return out, (time.monotonic() - t0) * 1000

    return run


def run_agent(llm: LLM, tools: Toolbox, pages: list[str], trace: Trace, *,
              max_turns: int = MAX_TURNS, max_tool_calls: int = MAX_TOOL_CALLS) -> AgentOutcome:
    system = prompts.agent_system(pages, max_tool_calls)
    usage = LLMUsage(model=llm.model)
    messages = [ChatMessage(role="user", text=prompts.AGENT_START)]
    known: dict[str, list[Citation]] = {}
    research: list[tuple[str, dict, str]] = []
    n_calls = turns = 0
    wants_pages = True  # turn 1 always runs; later turns only if the previous one asked for read_page

    while turns < max_turns and n_calls < max_tool_calls and wants_pages:
        try:
            reply, u = llm.chat(system=system, messages=messages, tools=TOOLS, purpose="agent",
                                force_tools=(turns == 0))
        except UpstreamError:
            if turns == 0:
                raise
            log.warning("agent follow-up turn failed; writing the review with what we have")
            break
        usage.add(u)
        turns += 1
        # after turn 1 only read_page may run: the plan was supposed to be complete
        calls = [c for c in reply.tool_calls if turns == 1 or c.name == "read_page"]
        if not calls:
            break
        messages.append(reply)
        run_now = calls[:max_tool_calls - n_calls]  # calls beyond the budget are ignored
        with ThreadPoolExecutor(max_workers=max(1, len(run_now))) as pool:
            timed = list(pool.map(_timed(tools), run_now))
        results: list[ToolResult] = []
        for call, (out, ms) in zip(run_now, timed, strict=True):
            n_calls += 1
            results.append(ToolResult(name=call.name, result=json.loads(out.payload), id=call.id))
            research.append((call.name, call.args, out.payload))
            for s in out.sources:
                known.setdefault(s.ref, []).append(s)
            trace.add(call.name, tool_summary(call.name, call.args), out.detail, ms)
        messages.append(ChatMessage(role="tool", tool_results=results))
        wants_pages = any(c.name == "read_page" for c in run_now)

    t0 = time.monotonic()
    out, u = llm.generate(system=system, parts=[prompts.research_transcript(research), prompts.FINAL_USER],
                          schema=LLMReview, purpose="review")
    usage.add(u)
    trace.add("draft_findings", "Wrote the findings and the plain-English summary",
              f"{len(out.findings)} candidate findings", (time.monotonic() - t0) * 1000)
    return AgentOutcome(review=out, known=known, usage=usage, turns=turns, tool_calls=n_calls)
