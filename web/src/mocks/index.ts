// Offline data for VITE_MOCK=1. Copied from a live review of the bundled
// synthetic sample msa-02-autorenew, with boxes located by tools/boxes.mjs.
// The id is the live one, so the PDF still loads through the dev proxy.
import type { Citation, CompanyDoc, KeyDate, NegotiationDraft, ReviewResult, ReviewSummary, Sample, SearchHit } from "../types";

export const mockReview: ReviewResult = {
  "id": "f8d3bc083e06",
  "created_at": "2026-10-18T02:03:21.698447Z",
  "filename": "msa-02-autorenew.pdf",
  "sample_id": "msa-02-autorenew",
  "contract_type": "service_agreement",
  "language": "en",
  "pages": 4,
  "parties": [
    "KIRIVANE TELEMATICS PTE. LTD.",
    "BAYU SELATAN LOGISTICS SDN. BHD."
  ],
  "verdict": {
    "overall_risk": "high",
    "headline": "Dangerous 2-year auto-renewal with 90-day post notice and automatic 20% annual fee hikes.",
    "summary": [
      "The agreement locks you into 24-month renewal periods unless you send notice by registered post 90 days early.",
      "The vendor automatically increases subscription fees by 20% every year without giving you the right to terminate.",
      "Ending the contract early triggers an obligation to pay all remaining fees for the rest of the term."
    ]
  },
  "findings": [
    {
      "id": "f1",
      "clause_type": "auto_renewal",
      "title": "Aggressive 24-month renewal with 90-day registered post notice",
      "risk": "high",
      "quote": "Upon the expiry of the Initial Term, this Agreement shall automatically renew for successive periods of twenty-four (24) months each (each a \"Renewal Term\") unless either party gives written notice of non-renewal to the other party at least ninety (90) days before the expiry of the Initial Term or the then-current Renewal Term.",
      "page": 2,
      "why_risky": "A 90-day advance notice requirement paired with a long 2-year auto-renewal term makes it very easy to miss the window and get trapped into paying for two additional years.",
      "standard": "Standard renewal periods are 12 months with a 30-day notice window and an advance reminder from the vendor.",
      "suggested_ask": "Please change renewal to 12-month periods with 30 days' notice to stop renewal, and permit notice by email.",
      "baseline_ref": "service_agreement/auto_renewal.md",
      "boxes": [
        [
          0.1552,
          0.4019,
          0.8762,
          0.4161
        ],
        [
          0.1238,
          0.42,
          0.8762,
          0.4343
        ],
        [
          0.1238,
          0.4382,
          0.8762,
          0.4524
        ],
        [
          0.1238,
          0.4564,
          0.4855,
          0.4706
        ]
      ]
    },
    {
      "id": "f2",
      "clause_type": "payment",
      "title": "Automatic 20% annual price hike without exit right",
      "risk": "high",
      "quote": "With effect from each anniversary of the Effective Date, the Provider shall be entitled to increase the Subscription Fees by twenty per cent (20%) of the Subscription Fees then in effect, without further notice to the Customer. Such increase shall apply automatically and shall not give the Customer any right to terminate this Agreement.",
      "page": 2,
      "why_risky": "A compulsory 20% annual price compounding with no exit right will rapidly escalate service costs far beyond inflation.",
      "standard": "Annual price increases should require at least 60 days' advance notice, be capped at 3% to 5% (or inflation), and give the customer a right to cancel if rejected.",
      "suggested_ask": "Please cap any annual price increase at 5% with 60 days' written notice and allow us to terminate without penalty if we do not accept the increase.",
      "baseline_ref": "service_agreement/payment.md",
      "boxes": [
        [
          0.1558,
          0.6806,
          0.8762,
          0.6949
        ],
        [
          0.1238,
          0.6988,
          0.8762,
          0.7131
        ],
        [
          0.1238,
          0.717,
          0.8762,
          0.7312
        ],
        [
          0.1238,
          0.7352,
          0.5659,
          0.7494
        ]
      ]
    },
    {
      "id": "f3",
      "clause_type": "termination",
      "title": "Early termination fee covering entire remaining term",
      "risk": "high",
      "quote": "The Customer may terminate this Agreement before the end of the Subscription Term for convenience by giving thirty (30) days' written notice, provided that it pays to the Provider, as an early termination charge, all Subscription Fees that would have been payable for the remainder of the then-current Subscription Term.",
      "page": 3,
      "why_risky": "Requiring 100% of the remaining contract fees to be paid makes termination for convenience financially prohibitive, especially during a 24-month renewal term.",
      "standard": "Termination for convenience should only require paying for services rendered and unrecoverable direct costs up to the termination date.",
      "suggested_ask": "Please remove the requirement to pay all remaining subscription fees upon early termination.",
      "baseline_ref": "service_agreement/termination.md",
      "boxes": [
        [
          0.1576,
          0.0759,
          0.8762,
          0.0902
        ],
        [
          0.1238,
          0.0941,
          0.8762,
          0.1083
        ],
        [
          0.1238,
          0.1123,
          0.8762,
          0.1265
        ],
        [
          0.1238,
          0.1305,
          0.4208,
          0.1447
        ]
      ]
    },
    {
      "id": "f4",
      "clause_type": "auto_renewal",
      "title": "Notice of non-renewal restricted to registered post only",
      "risk": "medium",
      "quote": "Notice of non-renewal is effective only if sent by registered post to the Provider's registered office marked for the attention of the Contracts Department. Notice by email or through the Portal shall not be effective for the purposes of Clause 4.2.",
      "page": 2,
      "why_risky": "Restricting notice to registered post while disqualifying email or portal communication creates an operational trap where timely electronic notices are declared void.",
      "standard": "Written notice by email to designated operational contacts should be valid.",
      "suggested_ask": "Please allow written notice of non-renewal to be delivered by email.",
      "baseline_ref": "service_agreement/auto_renewal.md",
      "boxes": [
        [
          0.1542,
          0.4813,
          0.8762,
          0.4955
        ],
        [
          0.1238,
          0.4995,
          0.8762,
          0.5137
        ],
        [
          0.1238,
          0.5177,
          0.5817,
          0.5319
        ]
      ]
    }
  ],
  "missing_clauses": [
    {
      "clause_type": "indemnity",
      "title": "Vendor IP Infringement Indemnity",
      "why_it_matters": "If a third party claims that the Provider's tracking devices, software, or portal infringe their intellectual property, the customer is unprotected and could face legal expenses or business disruption.",
      "suggested_ask": "Please add a standard indemnity from the Provider defending and holding us harmless against any third-party intellectual property infringement claims."
    }
  ],
  "metrics": {
    "latency_ms": 11886,
    "model": "gemini-3.8-flash",
    "input_tokens": 12365,
    "output_tokens": 1267,
    "cost_usd_est": 0.014485,
    "dropped_unverified": 0
  },
  "disclaimer": "This is an automated review to help you prepare. It is not legal advice."
};

export const mockSamples: Sample[] = [
  {
    "id": "nda-01-fair",
    "title": "Mutual NDA: software integration talks (KL / Singapore)",
    "contract_type": "nda",
    "language": "en"
  },
  {
    "id": "nda-02-oneway",
    "title": "One-way NDA: supplier talks with a food platform",
    "contract_type": "nda",
    "language": "en"
  },
  {
    "id": "nda-03-predatory",
    "title": "NDA: distribution talks with an overseas group",
    "contract_type": "nda",
    "language": "en"
  },
  {
    "id": "nda-04-bm",
    "title": "Perjanjian Kerahsiaan (NDA in Bahasa Malaysia)",
    "contract_type": "nda",
    "language": "ms"
  },
  {
    "id": "msa-01-fair",
    "title": "Software services agreement: inventory platform",
    "contract_type": "service_agreement",
    "language": "en"
  },
  {
    "id": "msa-02-autorenew",
    "title": "Fleet tracking subscription agreement",
    "contract_type": "service_agreement",
    "language": "en"
  },
  {
    "id": "msa-03-liability",
    "title": "App development and support agreement",
    "contract_type": "service_agreement",
    "language": "en"
  },
  {
    "id": "msa-04-scanned",
    "title": "Facilities services agreement (scanned copy)",
    "contract_type": "service_agreement",
    "language": "en"
  }
];

export const mockSummaries: ReviewSummary[] = [
  {
    id: mockReview.id,
    created_at: mockReview.created_at,
    filename: mockReview.filename,
    contract_type: "service_agreement",
    overall_risk: "high",
    headline: mockReview.verdict.headline,
    finding_counts: { high: 3, medium: 1, low: 0 },
  },
];

export const mockHits: SearchHit[] = [
  {
    review_id: mockReview.id,
    filename: mockReview.filename,
    page: 2,
    text: mockReview.findings[0].quote,
    clause_type: "auto_renewal",
    finding_id: "f1",
    score: 0.82,
  },
  {
    review_id: mockReview.id,
    filename: mockReview.filename,
    page: 2,
    text: mockReview.findings[3].quote,
    clause_type: "auto_renewal",
    finding_id: "f4",
    score: 0.71,
  },
];

export const mockNegotiation: NegotiationDraft = {
  review_id: mockReview.id,
  subject: "Fleet Tracking Subscription Agreement: changes before we sign",
  body: [
    "Dear Kirivane Telematics team,",
    "",
    "Thank you for sending the Fleet Tracking Subscription Agreement. We are keen to go ahead, and before we sign we would like to agree a few changes.",
    "",
    "1. Renewal (clause 4.2, page 2): please change renewals to 12-month periods, with 30 days’ notice to stop renewal.",
    "2. Price increases (page 2): please cap any yearly increase at 5%, with 60 days’ written notice, and let us end the agreement without penalty if we do not accept it.",
    "3. Early termination (page 3): please remove the charge for all remaining fees if we end the agreement early.",
    "4. Notices (clause 4.3, page 2): please accept notice of non-renewal by email.",
    "",
    "We would also like to add a clause in which Kirivane covers us if a third party claims the devices or portal infringe their rights.",
    "",
    "Happy to go through these on a call this week.",
    "",
    "Kind regards,",
    "Bayu Selatan Logistics Sdn. Bhd.",
  ].join("\n"),
  asks: [
    { finding_id: "f1", ask: "12-month renewals with 30 days’ notice to stop renewal." },
    { finding_id: "f2", ask: "Yearly increase capped at 5%, 60 days’ notice, right to exit." },
    { finding_id: "f3", ask: "No charge for remaining fees on early termination." },
    { finding_id: "f4", ask: "Notice of non-renewal by email is valid." },
  ],
};

// --- Sources, company rules and the check trail (until the server returns them) ---

const POLICY = "kopi-kita-contracting-policy.pdf";
const law75 = {
  kind: "law" as const,
  ref: "my-ca1950-s75",
  title: "Contracts Act 1950 (Malaysia), s.75",
  jurisdiction: "my",
  excerpt:
    "When a contract has been broken, if a sum is named in the contract as the amount to be paid in case of such breach, the party complaining of the breach is entitled to receive reasonable compensation not exceeding the amount so named.",
  url: "https://lom.agc.gov.my/ilims/upload/portal/akta/LOM/EN/Act%20136.pdf",
  page: null,
};
const cubic = {
  kind: "case" as const,
  ref: "my-cubic-electronics-2019",
  title: "Cubic Electronics v Mars Telecommunications (Federal Court, 2019)",
  jurisdiction: "my",
  excerpt:
    "A sum payable on breach is recoverable only as reasonable compensation; a sum out of all proportion to the likely loss will not be enforced in full.",
  url: null,
  page: null,
};
const playbook = (ref: string, title: string, excerpt: string) => ({ kind: "playbook" as const, ref, title, excerpt, jurisdiction: null, url: null, page: null });
const rule = (title: string, page: number, excerpt: string) => ({
  kind: "company" as const,
  ref: "company:cd_policy",
  title,
  excerpt,
  jurisdiction: null,
  url: null,
  page,
});

const extra: Record<string, { citations: Citation[]; policy_breach: string | null }> = {
  f1: {
    citations: [
      rule(`${POLICY}, Rule 4.2`, 1, "The notice window to stop renewal must be 30 days or less, and each renewal period must be 12 months or less."),
      playbook("playbook:service_agreement/auto_renewal", "Usual terms: auto-renewal", "Renewal periods of 12 months or less, with 30 days' notice and a reminder from the vendor."),
    ],
    policy_breach: "Your rules allow renewals of 12 months or less with 30 days' notice; this is 24 months with 90 days.",
  },
  f2: {
    citations: [
      rule(`${POLICY}, Rule 5.3`, 2, "Fixed automatic increases (for example \"10% every year\") are not acceptable, even if they look small."),
      playbook("playbook:service_agreement/payment", "Usual terms: price changes", "Increases capped at inflation or 3 to 5%, with 60 days' notice and a right to leave."),
    ],
    policy_breach: "Your rules cap increases at CPI + 3% a year; this is a fixed 20%.",
  },
  f3: {
    citations: [law75, cubic, rule(`${POLICY}, Rule 7.1`, 2, "We do not pay \"all remaining fees\" as an early termination charge.")],
    policy_breach: "Your rules say you never pay all remaining fees to end a contract early.",
  },
  f4: {
    citations: [rule(`${POLICY}, Rule 4.3`, 1, "We must be able to give notice of non-renewal by email. Clauses that accept only registered post are not acceptable.")],
    policy_breach: "Your rules require that notice by email is accepted.",
  },
};
for (const f of mockReview.findings) Object.assign(f, extra[f.id] ?? { citations: [], policy_breach: null });

mockReview.jurisdiction = "sg";
mockReview.trace = [
  { step: 1, action: "read_contract", summary: "Read all 4 pages and found the parties, term and governing law", ms: 1400 },
  { step: 2, action: "search_playbook", summary: "Compared renewal, fees and termination with usual terms", ms: 2100 },
  { step: 3, action: "search_law", summary: "Checked Malaysian law on charges for ending a contract early", ms: 1800 },
  { step: 4, action: "search_company", summary: "Checked your contracting policy: 4 rules apply", ms: 1200 },
  { step: 5, action: "read_page", summary: "Re-read page 2 to confirm how notice must be sent", ms: 900 },
  { step: 6, action: "draft_findings", summary: "Wrote 4 findings and 1 missing protection", ms: 3300 },
  { step: 7, action: "verify", summary: "Matched every quote to the contract's own words; none dropped", ms: 400 },
];

export const mockCompanyDocs: CompanyDoc[] = [
  { id: "cd_policy", filename: POLICY, title: "Kopi Kita Sdn Bhd: Contracting policy", created_at: "2026-10-18T03:10:00Z", pages: 4 },
  { id: "cd_privacy", filename: "kopi-kita-data-handling-rules.pdf", title: "Kopi Kita Sdn Bhd: Customer data handling rules", created_at: "2026-10-16T02:00:00Z", pages: 2 },
];

mockReview.key_dates = [
  { label: "Agreement starts", kind: "start", date: "2026-08-15", rule: "The Effective Date", page: 1, finding_id: null },
  { label: "Last day to cancel before it renews for 24 months", kind: "notice_deadline", date: "2027-05-17", rule: "90 days before the initial 12-month term ends, by registered post only", page: 2, finding_id: "f1" },
  { label: "Fees go up 20%", kind: "price_change", date: "2027-08-15", rule: "Each anniversary of the Effective Date", page: 2, finding_id: "f2" },
  { label: "Renews for 24 months", kind: "renewal", date: "2027-08-15", rule: "End of the initial 12-month term", page: 2, finding_id: "f1" },
  { label: "Invoices due", kind: "payment", date: null, rule: "Monthly, within 30 days of each invoice", page: 2, finding_id: null },
];

const dl = (label: string, kind: KeyDate["kind"], date: string | null, page: number, finding_id: string | null): KeyDate => ({ label, kind, date, page, finding_id, rule: null });
const day = (n: number) => `2026-10-${String(18 - n).padStart(2, "0")}T0${n % 9}:15:00Z`;
mockSummaries.length = 0;
mockSummaries.push(
  { id: mockReview.id, created_at: day(0), filename: mockReview.filename, contract_type: "service_agreement", overall_risk: "high", headline: mockReview.verdict.headline, finding_counts: { high: 3, medium: 1 }, next_deadline: mockReview.key_dates[1] },
  { id: "rv_pos", created_at: day(1), filename: "hp-03-pos-terminals.pdf", contract_type: "service_agreement", overall_risk: "high", headline: "Five-year rental with a fee for every month left if you leave early.", finding_counts: { high: 2, medium: 2 }, next_deadline: dl("Last day to cancel before it renews for 12 months", "notice_deadline", "2026-12-01", 3, "f1") },
  { id: "rv_clean", created_at: day(2), filename: "svc-02-outlet-cleaning.pdf", contract_type: "service_agreement", overall_risk: "medium", headline: "Fair overall, but the vendor can change the scope with 7 days' notice.", finding_counts: { medium: 2, low: 1 }, next_deadline: dl("Renews for 12 months", "renewal", "2027-01-31", 2, null) },
  { id: "rv_nda3", created_at: day(3), filename: "nda-03-predatory.pdf", contract_type: "nda", overall_risk: "high", headline: "Secrecy lasts forever and blocks you from working with any competitor for 3 years.", finding_counts: { high: 2, medium: 1 }, next_deadline: null },
  { id: "rv_app", created_at: day(4), filename: "svc-08-software-dev.pdf", contract_type: "service_agreement", overall_risk: "medium", headline: "Your liability has no cap while the developer's is one month of fees.", finding_counts: { high: 1, medium: 2, low: 1 }, next_deadline: dl("Support term ends", "end", "2027-03-02", 4, null) },
  { id: "rv_nda1", created_at: day(5), filename: "nda-01-fair.pdf", contract_type: "nda", overall_risk: "low", headline: "A balanced mutual NDA with a sensible 3-year term.", finding_counts: { low: 1 }, next_deadline: null },
);


// --- round 4: verification, removed claims, provenance, the agent's lookups ---
mockReview.provenance = { model: "gemini-3.8-flash", kb_version: "kb-7f3a91", prompt_version: "review-v6", app_version: "2026.10.18" };
mockReview.previous_review_id = "rv_prev";
mockReview.owner = "co-kopikita";
const V: Record<string, NonNullable<ReviewResult["findings"][number]["verification"]>> = {
  f1: { quote_match: "exact", score: 100, citations_checked: 2, kb_version: "kb-7f3a91" },
  f2: { quote_match: "normalised", score: 98, citations_checked: 2, kb_version: "kb-7f3a91" },
  f3: { quote_match: "exact", score: 100, citations_checked: 3, kb_version: "kb-7f3a91", page_corrected_from: 2 },
  f4: { quote_match: "fuzzy", score: 91, citations_checked: 1, kb_version: "kb-7f3a91" },
};
for (const f of mockReview.findings) {
  f.verification = V[f.id];
  for (const c of f.citations ?? []) {
    if (c.kind === "company") {
      c.doc_id = "cd_policy";
      c.page = 2; // where these rules sit in the sample policy PDF; boxes are found from its text
      c.boxes = [];
    }
    if (c.kind === "law") c.source_url = "https://lom.agc.gov.my/ilims/upload/portal/akta/LOM/EN/Act%20136.pdf#page=31";
  }
}
mockReview.removed = [
  { kind: "finding", reason: "quote_not_found", text: "Vendor may share your data with affiliates: “The Provider may disclose Customer Data to its group companies.”", page: 3, finding_title: "Data shared with group companies" },
  { kind: "citation", reason: "source_not_retrieved", text: "Consumer Protection Act 1999, s 24G", page: null, finding_title: "Automatic 20% annual price hike without exit right" },
];
mockReview.metrics.dropped_unverified = 1;
mockReview.metrics.dropped_unverified_citations = 1;
const detail = (tool: string, args: Record<string, unknown>, results: [string, string, string, number][], cited: string[]) =>
  JSON.stringify({ tool, args, results: results.map(([ref, title, kind, score]) => ({ ref, title, kind, score })), cited });
mockReview.trace = [
  { step: 1, action: "read_contract", summary: "Read all 4 pages and found the parties, term and governing law", ms: 1400, detail: null },
  { step: 2, action: "search_playbook", summary: "Compared renewal and notice terms with usual terms", ms: 900,
    detail: detail("search_playbook", { contract_type: "service_agreement", clause_type: "auto_renewal", query: "renewal notice period registered post" },
      [["playbook:service_agreement/auto_renewal", "Auto-renewal and price increases on renewal", "playbook", 0.91], ["playbook:service_agreement/term", "Term: how long the agreement lasts", "playbook", 0.64]], ["playbook:service_agreement/auto_renewal"]) },
  { step: 3, action: "search_law", summary: "Checked Malaysian law on charges for ending a contract early", ms: 1800,
    detail: detail("search_law", { jurisdiction: "my", query: "early termination charge all remaining fees penalty" },
      [["my-ca1950-s75", "Contracts Act 1950, s 75: compensation where a sum or penalty is named", "law", 0.88], ["my-case-cubic-electronics", "Cubic Electronics v Mars Telecommunications", "case", 0.83], ["my-ca1950-s74", "Contracts Act 1950, s 74: compensation for loss caused by breach", "law", 0.61]], ["my-ca1950-s75", "my-case-cubic-electronics"]) },
  { step: 4, action: "search_company", summary: "Checked your contracting policy: 4 rules apply", ms: 1200,
    detail: detail("search_company", { query: "auto-renewal notice price increase early termination" },
      [["company:cd_policy#4.2", "Rule 4.2 · renewal notice", "company", 0.9], ["company:cd_policy#5.3", "Rule 5.3 · fixed increases", "company", 0.86], ["company:cd_policy#7.1", "Rule 7.1 · early exit", "company", 0.81]], ["company:cd_policy#4.2", "company:cd_policy#5.3", "company:cd_policy#7.1"]) },
  { step: 5, action: "read_page", summary: "Re-read page 2 to confirm how notice must be sent", ms: 900, detail: detail("read_page", { page: 2 }, [], []) },
  { step: 6, action: "draft_findings", summary: "Wrote 5 findings and 1 missing protection", ms: 3300, detail: null },
  { step: 7, action: "verify", summary: "Matched quotes to the contract’s own words; removed 1 point and 1 source that could not be confirmed", ms: 400, detail: null },
];
