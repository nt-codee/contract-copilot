// Mirrors shared/contracts/review.schema.json field for field.
// Change the schema first, then this file.

export type ContractType = "nda" | "service_agreement" | "other";
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
}

export type TraceAction =
  | "read_contract"
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
