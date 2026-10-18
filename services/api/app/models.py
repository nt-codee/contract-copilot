"""Pydantic mirror of shared/contracts/review.schema.json. Change the schema first, then this file."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ContractType = Literal["nda", "service_agreement", "other"]
Risk = Literal["high", "medium", "low"]
ClauseType = Literal[
    "confidentiality", "term", "termination", "auto_renewal", "liability", "indemnity",
    "ip_ownership", "payment", "governing_law", "dispute_resolution", "non_solicit",
    "non_compete", "data_protection", "assignment", "warranty", "other",
]

CitationKind = Literal["law", "case", "playbook", "company"]
TraceAction = Literal["read_contract", "search_law", "search_playbook", "search_company", "read_page",
                      "draft_findings", "verify"]

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


class Citation(BaseModel):
    kind: CitationKind
    ref: str
    title: str
    jurisdiction: str | None = None
    excerpt: str
    url: str | None = None
    page: int | None = None


class TraceStep(BaseModel):
    step: int
    action: TraceAction
    summary: str
    detail: str | None = None
    ms: int | None = None


KeyDateKind = Literal["start", "end", "renewal", "notice_deadline", "payment", "price_change", "other"]


class KeyDate(BaseModel):
    label: str
    kind: KeyDateKind
    date: date_type | None = None  # ISO date, only when computable from the contract
    rule: str | None = None
    page: int
    finding_id: str | None = None


class KnowledgeEntry(BaseModel):
    id: str
    kind: Literal["law", "case", "playbook"]
    jurisdiction: str | None = None
    contract_type: str | None = None
    title: str
    citation: str | None = None
    clause_types: list[str] = Field(default_factory=list)
    plain: str | None = None
    text: str | None = None  # full body, markdown; detail endpoint only
    url: str | None = None


class CompanyDoc(BaseModel):
    id: str
    filename: str
    title: str | None = None
    created_at: datetime
    pages: int


class Finding(BaseModel):
    id: str
    clause_type: ClauseType
    title: str
    risk: Risk
    quote: str
    page: int = Field(ge=1)
    why_risky: str
    standard: str
    suggested_ask: str
    baseline_ref: str | None = None
    # [x0, y0, x1, y1] normalised 0-1, top-left origin, one per line fragment. Defaults to [] so
    # reviews cached before boxes existed still load.
    boxes: list[list[float]] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    policy_breach: str | None = None


class MissingClause(BaseModel):
    clause_type: ClauseType
    title: str
    why_it_matters: str
    suggested_ask: str


class Verdict(BaseModel):
    overall_risk: Risk
    headline: str = Field(max_length=160)
    summary: list[str] = Field(min_length=1, max_length=3)


class Metrics(BaseModel):
    latency_ms: int
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd_est: float | None = None
    dropped_unverified: int = 0
    dropped_unverified_citations: int = 0


class ReviewResult(BaseModel):
    id: str
    created_at: datetime
    filename: str
    sample_id: str | None = None
    contract_type: ContractType
    language: str
    pages: int
    parties: list[str]
    verdict: Verdict
    findings: list[Finding]
    missing_clauses: list[MissingClause]
    metrics: Metrics
    disclaimer: str
    jurisdiction: str | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    key_dates: list[KeyDate] = Field(default_factory=list)


def next_deadline(key_dates: list[KeyDate], today: date_type | None = None) -> KeyDate | None:
    """Earliest notice_deadline or renewal on or after today; else the earliest dated key date; else None."""
    today = today or datetime.now(timezone.utc).date()
    dated = sorted((k for k in key_dates if k.date is not None), key=lambda k: k.date)
    upcoming = [k for k in dated if k.kind in ("notice_deadline", "renewal") and k.date >= today]
    return upcoming[0] if upcoming else (dated[0] if dated else None)


class FindingCounts(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0


class ReviewSummary(BaseModel):
    id: str
    created_at: datetime
    filename: str
    contract_type: ContractType
    overall_risk: Risk
    headline: str
    finding_counts: FindingCounts
    next_deadline: KeyDate | None = None

    @classmethod
    def of(cls, r: ReviewResult, today: date_type | None = None) -> "ReviewSummary":
        counts = FindingCounts()
        for f in r.findings:
            setattr(counts, f.risk, getattr(counts, f.risk) + 1)
        return cls(id=r.id, created_at=r.created_at, filename=r.filename,
                   contract_type=r.contract_type, overall_risk=r.verdict.overall_risk,
                   headline=r.verdict.headline, finding_counts=counts,
                   next_deadline=next_deadline(r.key_dates, today))


class NegotiationAsk(BaseModel):
    finding_id: str
    ask: str


class NegotiationDraft(BaseModel):
    review_id: str
    subject: str
    body: str
    asks: list[NegotiationAsk]


class SearchHit(BaseModel):
    review_id: str
    filename: str
    page: int
    text: str = Field(max_length=400)
    clause_type: str | None = None
    finding_id: str | None = None
    score: float


class Sample(BaseModel):
    id: str
    title: str
    contract_type: ContractType
    language: str


# ---- LLM structured-output shapes (internal; not part of the API contract) -----------------
# Kept free of ids, metrics and optional unions so the response_schema stays simple.


class LLMCitation(BaseModel):
    kind: CitationKind
    ref: str = Field(description="The ref exactly as a tool returned it")
    excerpt: str = Field(description="The words of the source you relied on")


class LLMFinding(BaseModel):
    clause_type: ClauseType
    title: str
    risk: Risk
    quote: str = Field(description="Copied character for character from the contract page, original language")
    page: int = Field(description="The [PAGE n] number the quote is on")
    why_risky: str
    standard: str
    suggested_ask: str
    baseline_ref: str = Field(description="Playbook file name used, e.g. nda/liability.md, or empty")
    citations: list[LLMCitation] = Field(
        description="Sources a tool returned in this session that support the finding; [] if none")
    policy_breach: str = Field(
        description="One sentence when the clause conflicts with the company's own rules (from "
                    "search_company), else an empty string")


class LLMKeyDate(BaseModel):
    label: str = Field(description="Plain words, e.g. 'Last day to cancel before it renews for 12 months'")
    kind: KeyDateKind
    page: int = Field(description="The [PAGE n] the clause that sets this date is on")
    base_date: str = Field(
        description="YYYY-MM-DD: the date the contract states (effective date, or a stated end date) that "
                    "this date is counted from; empty string if the contract gives none")
    months: int = Field(description="Whole months to add to base_date (0 if none)")
    days: int = Field(description="Days to add to base_date after the months; negative for 'before'")
    rule: str = Field(description="How the date is derived, in words, e.g. '90 days before the end of the "
                                  "12-month term'")
    finding_title: str = Field(description="Exact title of the finding about this date, or an empty string")


class LLMVerdict(BaseModel):
    overall_risk: Risk
    headline: str = Field(description="One plain sentence, under 140 characters")
    summary: list[str] = Field(description="1 to 3 short plain-English bullet sentences")


class LLMReview(BaseModel):
    contract_type: ContractType
    language: str = Field(description="ISO 639-1 code of the contract text, e.g. en or ms")
    parties: list[str]
    verdict: LLMVerdict
    findings: list[LLMFinding]
    missing_clauses: list[MissingClause]
    jurisdiction: str = Field(
        description="Governing-law jurisdiction of the contract: my, sg or other. Use my if unclear")
    key_dates: list[LLMKeyDate]


class LLMPage(BaseModel):
    page: int
    text: str


class LLMTranscription(BaseModel):
    pages: list[LLMPage]


class LLMBox(BaseModel):
    index: int = Field(description="The quote number given in the prompt")
    box_2d: list[int] = Field(description="[ymin, xmin, ymax, xmax] on a 0-1000 scale")


class LLMBoxes(BaseModel):
    boxes: list[LLMBox]


class LLMNegotiation(BaseModel):
    subject: str
    body: str
    asks: list[NegotiationAsk]
