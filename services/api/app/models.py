"""Pydantic mirror of shared/contracts/review.schema.json. Change the schema first, then this file."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ContractType = Literal["nda", "service_agreement", "employment", "tenancy", "hire_purchase", "other"]
Risk = Literal["high", "medium", "low"]
ClauseType = Literal[
    "confidentiality", "term", "termination", "auto_renewal", "liability", "indemnity",
    "ip_ownership", "payment", "governing_law", "dispute_resolution", "non_solicit",
    "non_compete", "data_protection", "assignment", "warranty", "probation", "working_hours", "leave",
    "notice_period", "salary_deductions", "deposit", "rent_review", "repairs", "early_termination",
    "interest_rate", "repossession", "guarantor", "insurance", "fees_charges", "other",
]

CitationKind = Literal["law", "case", "playbook", "company"]
TraceAction = Literal["read_contract", "plan_research", "search_law", "search_playbook", "search_company", "read_page",
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
    source_url: str | None = None  # deep link to the exact provision at the official source
    boxes: list[list[float]] = Field(default_factory=list)  # company citations: excerpt on `page` of the PDF
    doc_id: str | None = None  # company document id (GET /api/company/docs/{doc_id}/pdf)


class Verification(BaseModel):
    quote_match: Literal["exact", "normalised", "fuzzy"]
    score: float | None = None  # fuzzy match score 0-100
    page_corrected_from: int | None = None
    citations_checked: int = 0
    kb_version: str | None = None


class RemovedClaim(BaseModel):
    kind: Literal["finding", "citation", "policy_breach", "statement"]  # statement: a sentence or part of an ask
    # ungrounded: states a contract fact the contract does not contain; citation_scope: the source does not
    # back that kind of claim; remedy_scope: proposes a fix the governing law does not support
    reason: Literal["quote_not_found", "source_not_retrieved", "no_company_citation", "wrong_jurisdiction",
                    "ungrounded", "citation_scope", "remedy_scope", "unsupported_severity"]
    text: str
    page: int | None = None
    finding_title: str | None = None


class TraceStep(BaseModel):
    step: int
    action: TraceAction
    summary: str
    detail: str | None = None
    ms: int | None = None
    start_ms: int | None = None
    turn: int | None = None
    batch: int | None = None


KeyDateKind = Literal["start", "end", "renewal", "notice_deadline", "payment", "price_change", "other"]


class KeyDate(BaseModel):
    label: str
    kind: KeyDateKind
    date: date_type | None = None  # ISO date, only when computable from the contract
    rule: str | None = None
    page: int
    finding_id: str | None = None
    # stated: the contract gives this date; calculated: computed in code from a stated date and an offset the
    # contract states; inferred: not stated (the label says it is an estimate). None without a date.
    basis: Literal["stated", "calculated", "inferred"] | None = None


class KnowledgeEntry(BaseModel):
    id: str
    kind: Literal["law", "case", "playbook"]
    jurisdiction: str | None = None
    contract_type: str | None = None
    title: str
    citation: str | None = None
    clause_types: list[str] = Field(default_factory=list)
    plain: str | None = None
    source_url: str | None = None  # deep link to the provision
    text: str | None = None  # full body, markdown; detail endpoint only
    url: str | None = None
    source_url: str | None = None  # deep link to the provision


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
    clause_ref: str | None = None  # the contract's own clause number, e.g. "3.3"; None if it numbers none
    why_risky: str
    standard: str
    suggested_ask: str
    baseline_ref: str | None = None
    # [x0, y0, x1, y1] normalised 0-1, top-left origin, one per line fragment. Defaults to [] so
    # reviews cached before boxes existed still load.
    boxes: list[list[float]] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    policy_breach: str | None = None
    verification: Verification | None = None


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


class ReviewOptions(BaseModel):
    """What the reader chose for one review (optional multipart fields of POST /api/reviews)."""

    contract_type: Literal["auto", "nda", "service_agreement", "employment", "tenancy", "hire_purchase"] = "auto"
    jurisdiction: Literal["auto", "MY", "SG"] = "auto"
    apply_company_rules: bool = True
    perspective: Literal["auto", "business", "individual"] = "auto"
    company_doc_ids: list[str] | None = None  # null: every company policy. Otherwise only these documents are searched.


class Provenance(BaseModel):
    model: str
    kb_version: str
    prompt_version: str
    app_version: str | None = None
    options: ReviewOptions | None = None  # null on reviews made before options existed


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
    removed: list[RemovedClaim] = Field(default_factory=list)  # claims verification removed
    owner: str | None = None  # workspace_id, or "demo"; None only on reviews stored before workspaces existed
    created_by: str | None = None  # uid of the member who ran it
    provenance: Provenance | None = None
    previous_review_id: str | None = None
    content_hash: str | None = None


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


Role = Literal["owner", "admin", "member"]
WorkspaceKind = Literal["company", "personal", "demo"]


class Workspace(BaseModel):
    id: str
    name: str
    kind: WorkspaceKind
    domain: str | None = None  # Google Workspace domain for auto-join, e.g. example.com
    created_at: datetime | None = None
    member_count: int | None = None


class Member(BaseModel):
    uid: str
    email: str
    name: str | None = None
    role: Role
    joined_at: datetime | None = None


class Invite(BaseModel):
    code: str
    workspace_id: str
    role: Literal["admin", "member"]
    expires_at: datetime | None = None


class WorkspaceRef(BaseModel):
    """A workspace the user belongs to, with their role in it."""

    workspace: Workspace
    role: Role


class User(BaseModel):
    uid: str
    email: str
    name: str | None = None
    photo_url: str | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    workspaces: list[WorkspaceRef] = Field(default_factory=list)
    platform_admin: bool = False  # the email is in ADMIN_EMAILS and verified


class Identity(User):
    """A verified token's user. `email_verified` and `hd` (the Google Workspace domain) are internal."""

    email_verified: bool = False
    hd: str | None = None

    def public(self) -> User:
        return User.model_validate(self.model_dump(exclude={"email_verified", "hd"}))


AuditAction = Literal["signed_in", "uploaded_contract", "reviewed", "re_reviewed", "drafted_reply",
                      "viewed_review", "deleted_review", "uploaded_policy", "deleted_policy", "exported",
                      "deleted_my_data"]


class AuditEvent(BaseModel):
    id: str
    at: datetime
    actor: str  # uid, or "demo" for the public demo workspace
    action: AuditAction
    target_id: str | None = None
    target_label: str | None = None
    detail: dict | None = None
    workspace_id: str | None = None  # the workspace the action happened in; "demo" for the sample workspace


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
    title: str = Field(description="At most 8 words, a noun phrase naming the risk, e.g. 'Auto-renewal with "
                                   "90-day notice'; never a sentence, no full stop")
    risk: Risk
    quote: str = Field(description="Copied character for character from the contract page, original language")
    page: int = Field(description="The [PAGE n] number the quote is on")
    clause_ref: str = Field(default="", description="The contract's own number for the quoted clause, e.g. '3.3' or "
                                        "'5.1(b)'; empty string if the contract does not number its clauses")
    why_risky: str = Field(description="First sentence: a stand-alone one-line summary of the risk, at most 25 "
                                       "words; then the detail")
    consequence: str = Field(default="", description=(
        "Only when risk is high: one sentence naming the concrete consequence for the reader under THIS contract's "
        "terms (an amount, a lock-in period, a right lost), with the clause number. Empty for medium and low. A "
        "high finding without it is rated medium."))
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
        description="YYYY-MM-DD: a date the contract states in full (effective date, commencement date or a "
                    "stated end date) that this date is counted from; empty string if the contract gives none")
    term_months: int = Field(description=(
        "0 when the anchor is base_date itself. Otherwise the length in months of the term that starts on "
        "base_date, when the date is counted from the END of that term (a 1-year term is 12)"))
    offset: int = Field(description="How many units before or after the anchor, as the contract states it; 0 "
                                    "for the anchor itself")
    unit: Literal["days", "weeks", "months", "years"] = Field(description="The unit of offset")
    direction: Literal["before", "after"] = Field(description="Whether the date is before or after the anchor")
    source_quote: str = Field(description=(
        "The contract's words that set this date (the offset and its unit), copied exactly from that page, "
        "at most 30 words; empty string for a date stated outright"))
    rule: str = Field(description="How the date is derived, in words, e.g. '60 days before the end of the "
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
