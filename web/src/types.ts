// Mirrors shared/contracts/review.schema.json field for field.
// Change the schema first, then this file.

export type ContractType = "nda" | "service_agreement" | "employment" | "tenancy" | "hire_purchase" | "other";
export type Risk = "high" | "medium" | "low";
export type ClauseType =
  | "confidentiality"
  | "term"
  | "termination"
  | "auto_renewal"
  | "liability"
  | "indemnity"
  | "ip_ownership"
  | "payment"
  | "governing_law"
  | "dispute_resolution"
  | "non_solicit"
  | "non_compete"
  | "data_protection"
  | "assignment"
  | "warranty"
  | "probation"
  | "working_hours"
  | "leave"
  | "notice_period"
  | "salary_deductions"
  | "deposit"
  | "rent_review"
  | "repairs"
  | "early_termination"
  | "interest_rate"
  | "repossession"
  | "guarantor"
  | "insurance"
  | "fees_charges"
  | "other";

export interface Finding {
  /** f1, f2, ... stable within a review */
  id: string;
  clause_type: ClauseType;
  /** short plain label */
  title: string;
  risk: Risk;
  /** exact text from the contract, verified against the page */
  quote: string;
  /** minimum 1 */
  page: number;
  why_risky: string;
  standard: string;
  suggested_ask: string;
  baseline_ref?: string | null;
  clause_ref?: string | null;
  /**
   * Where the quote sits on `page`: one [x0, y0, x1, y1] per line fragment,
   * normalised 0-1 to the page, top-left origin. Empty when not located.
   * Optional because the schema defaults it to [] and older reviews omit it.
   */
  boxes?: [number, number, number, number][];
  /** Sources relied on; default []. */
  citations?: Citation[];
  /** One sentence when it conflicts with the user's own company rules. */
  policy_breach?: string | null;
  verification?: Verification | null;
}

export interface Verification {
  quote_match: "exact" | "normalised" | "fuzzy";
  /** fuzzy match score 0-100 */
  score?: number | null;
  /** set when the model gave a neighbouring page */
  page_corrected_from?: number | null;
  citations_checked?: number;
  kb_version?: string | null;
}

export type RemovedReason =
  | "quote_not_found"
  | "source_not_retrieved"
  | "no_company_citation"
  | "wrong_jurisdiction"
  | "ungrounded"
  | "citation_scope"
  | "remedy_scope"
  | "unsupported_severity";

export interface RemovedClaim {
  kind: "finding" | "citation" | "policy_breach" | "statement";
  reason: RemovedReason;
  /** what the model claimed */
  text: string;
  page?: number | null;
  finding_title?: string | null;
}

/** What the reader chose on upload; defaults are "auto" / true. */
export interface ReviewOptions {
  contract_type: "auto" | "nda" | "service_agreement" | "employment" | "tenancy" | "hire_purchase";
  jurisdiction: "auto" | "MY" | "SG";
  apply_company_rules: boolean;
  perspective: "auto" | "business" | "individual";
  /** null = every policy */
  company_doc_ids?: string[] | null;
}

export interface Provenance {
  options?: ReviewOptions | null;
  model: string;
  /** content hash of the knowledge base at review time */
  kb_version: string;
  prompt_version: string;
  app_version?: string | null;
}

export type CitationKind = "law" | "case" | "playbook" | "company";

export interface Citation {
  kind: CitationKind;
  /** knowledge-base id, e.g. my-contracts-act-s75, company:<doc_id> */
  ref: string;
  /** human label, e.g. "Contracts Act 1950, s.75" */
  title: string;
  /** my | sg | null */
  jurisdiction?: string | null;
  /** verbatim, 400 chars or fewer */
  excerpt: string;
  url?: string | null;
  /** for company documents */
  page?: number | null;
  /** deep link to the exact provision at the official source */
  source_url?: string | null;
  /** where the cited text sits on `page` of the company document, normalised 0-1 */
  boxes?: [number, number, number, number][];
  doc_id?: string | null;
}

/** TraceStep.detail, a JSON string, for tool steps. */
export interface TraceDetail {
  tool: string;
  args: Record<string, unknown>;
  results: { ref: string; title: string; kind: string; score?: number | null }[];
  cited: string[];
}

export type TraceAction =
  | "read_contract"
  | "plan_research"
  | "search_law"
  | "search_playbook"
  | "search_company"
  | "read_page"
  | "draft_findings"
  | "verify";

export interface TraceStep {
  step: number;
  action: TraceAction;
  /** plain words */
  summary: string;
  detail?: string | null;
  ms?: number | null;
  /** Elapsed milliseconds from the review's single monotonic origin; absent on older runs. */
  start_ms?: number | null;
  /** Recorded model turn and parallel tool batch identifiers; never inferred by the UI. */
  turn?: number | null;
  batch?: number | null;
}

export interface CompanyDoc {
  id: string;
  filename: string;
  title?: string | null;
  /** date-time */
  created_at: string;
  pages: number;
}

export interface MissingClause {
  clause_type: ClauseType;
  title: string;
  why_it_matters: string;
  suggested_ask: string;
}

export interface Verdict {
  overall_risk: Risk;
  /** max 160 chars */
  headline: string;
  /** 1 to 3 lines */
  summary: string[];
}

export interface Metrics {
  latency_ms: number;
  model: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd_est?: number | null;
  /** default 0 */
  dropped_unverified?: number;
  dropped_unverified_citations?: number;
}

export interface ReviewResult {
  id: string;
  /** date-time */
  created_at: string;
  filename: string;
  sample_id?: string | null;
  contract_type: ContractType;
  /** ISO 639-1, e.g. en, ms */
  language: string;
  pages: number;
  parties: string[];
  verdict: Verdict;
  /** sorted high, medium, low */
  findings: Finding[];
  missing_clauses: MissingClause[];
  metrics: Metrics;
  disclaimer: string;
  /** my | sg | other, from the governing-law clause */
  jurisdiction?: string | null;
  /** default [] */
  trace?: TraceStep[];
  /** default [] */
  key_dates?: KeyDate[];
  owner?: string | null;
  provenance?: Provenance | null;
  previous_review_id?: string | null;
  content_hash?: string | null;
  created_by?: string | null;
  /** claims dropped because they could not be confirmed; default [] */
  removed?: RemovedClaim[];
}

export type KeyDateKind = "start" | "end" | "renewal" | "notice_deadline" | "payment" | "price_change" | "other";

export interface KeyDate {
  /** plain words, e.g. "Last day to cancel before it renews for 24 months" */
  label: string;
  kind: KeyDateKind;
  /** ISO date when it can be computed from the contract, else null */
  date?: string | null;
  /** how it is derived */
  rule?: string | null;
  page: number;
  finding_id?: string | null;
}

export type KnowledgeKind = "law" | "case" | "playbook";

export interface KnowledgeEntry {
  id: string;
  kind: KnowledgeKind;
  jurisdiction?: string | null;
  contract_type?: string | null;
  title: string;
  citation?: string | null;
  clause_types?: string[];
  plain?: string | null;
  /** markdown; only on the detail endpoint */
  text?: string | null;
  url?: string | null;
  source_url?: string | null;
}

export interface FindingCounts {
  high?: number;
  medium?: number;
  low?: number;
}

export interface ReviewSummary {
  id: string;
  created_at: string;
  filename: string;
  contract_type: ContractType;
  overall_risk: Risk;
  headline: string;
  finding_counts: FindingCounts;
  /** earliest upcoming notice_deadline or renewal */
  next_deadline?: KeyDate | null;
}

export interface NegotiationAsk {
  finding_id: string;
  ask: string;
}

export interface NegotiationDraft {
  review_id: string;
  subject: string;
  /** plain text */
  body: string;
  asks: NegotiationAsk[];
}

export interface SearchHit {
  review_id: string;
  filename: string;
  page: number;
  /** matching passage, 400 chars or fewer */
  text: string;
  clause_type?: string | null;
  finding_id?: string | null;
  score: number;
}

export interface Sample {
  id: string;
  title: string;
  contract_type: ContractType;
  language: string;
}

/** Error body: {"error": {"code", "message"}} */
export interface ApiErrorBody {
  error: { code: string; message: string };
}

export type Role = "owner" | "admin" | "member";

export interface Workspace {
  id: string;
  name: string;
  kind: "company" | "personal" | "demo";
  domain?: string | null;
  created_at?: string | null;
  member_count?: number | null;
}

export interface WorkspaceRef {
  workspace: Workspace;
  role: Role;
}

export interface User {
  uid: string;
  email: string;
  name?: string | null;
  photo_url?: string | null;
  created_at?: string | null;
  last_seen_at?: string | null;
  workspaces?: WorkspaceRef[];
  platform_admin?: boolean;
}

export interface Member {
  uid: string;
  email: string;
  name?: string | null;
  role: Role;
  joined_at?: string | null;
}

export interface Invite {
  code: string;
  workspace_id: string;
  role: "admin" | "member";
  expires_at?: string | null;
}

export type AuditAction =
  | "signed_in"
  | "uploaded_contract"
  | "reviewed"
  | "re_reviewed"
  | "drafted_reply"
  | "viewed_review"
  | "deleted_review"
  | "uploaded_policy"
  | "deleted_policy"
  | "exported"
  | "deleted_my_data";

export interface AuditEvent {
  id: string;
  at: string;
  /** uid, or "demo" for the public demo workspace */
  actor: string;
  action: AuditAction;
  target_id?: string | null;
  target_label?: string | null;
  detail?: Record<string, unknown> | null;
  workspace_id?: string | null;
}

/** GET /api/playbook: the default position, or this workspace's override of it. */
export interface PlaybookItem {
  /** playbook:<type>/<clause>, or playbook:<owner>/<type>/<clause> when overridden */
  id: string;
  contract_type: string;
  clause_type: string;
  title: string;
  standard: string;
  red_flags: string[];
  ask: string;
  note: string;
  overridden: boolean;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface PlaybookOverrideIn {
  standard: string;
  red_flags: string[];
  ask: string;
  note: string;
}

/** GET /api/admin/overview and /api/workspaces/:id/overview */
export interface Overview {
  users: number;
  reviews: number;
  reviews_by_day: { date: string; count: number }[];
  by_contract_type: Record<string, number>;
  by_risk: Record<Risk, number>;
  top_clause_types: { clause_type: string; high: number; medium: number; low: number }[];
  policy_breaches_by_rule: { rule: string; count: number }[];
  citations_by_kind: Record<CitationKind, number>;
  top_cited_sources: { ref: string; title: string; count: number }[];
  latency_ms: { p50: number; p95: number; max: number };
  cost_usd: { total: number; per_review_mean: number };
  tokens: { in: number; out: number };
  dropped_unverified: { quotes: number; citations: number };
  upcoming_deadlines: { date: string; kind: string; contract_type: string; days_left: number }[];
}

export interface AdminUserRow {
  uid: string;
  email: string;
  name?: string | null;
  photo_url?: string | null;
  created_at?: string | null;
  last_seen_at?: string | null;
  review_count: number;
  workspaces: { id: string; name: string; role: Role }[];
}

export interface AuthConfig {
  apiKey: string;
  authDomain: string;
  projectId: string;
}
