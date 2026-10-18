// Offline data for the signed-in workspace (VITE_MOCK=1), until the server ships it.
// Shapes follow shared/contracts/review.schema.json and the playbook routes.
import type {
  AdminUserRow,
  AuditEvent,
  Invite,
  Member,
  Overview,
  PlaybookItem,
  PlaybookOverrideIn,
  ReviewSummary,
  User,
  Workspace,
  WorkspaceRef,
} from "../types";
import { mockReview, mockSummaries } from "./index";
import kb from "./knowledge.data.json";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

const KOPI: Workspace = { id: "co-kopikita", name: "Kopi Kita Sdn Bhd", kind: "company", domain: "kopikita.example", created_at: "2026-10-01T02:00:00Z", member_count: 4 };
const MINE: Workspace = { id: "pe-aisyah", name: "Aisyah (personal)", kind: "personal", created_at: "2026-10-01T02:00:00Z", member_count: 1 };

let me: User = {
  uid: "u_aisyah",
  email: "aisyah@kopikita.example",
  name: "Aisyah Rahman",
  photo_url: null,
  created_at: "2026-10-01T02:00:00Z",
  last_seen_at: new Date().toISOString(),
  workspaces: [
    { workspace: KOPI, role: "owner" },
    { workspace: MINE, role: "owner" },
  ],
  platform_admin: true,
};

export async function mockMe(): Promise<User> {
  await wait(250);
  return me;
}

export async function mockDeleteMe() {
  await wait(800);
  return { deleted: { reviews: 6, uploads: 6, company_docs: 1, audit_events: 41 } };
}

let members: Member[] = [
  { uid: "u_aisyah", email: "aisyah@kopikita.example", name: "Aisyah Rahman", role: "owner", joined_at: "2026-10-01T02:00:00Z" },
  { uid: "u_wei", email: "weiming@kopikita.example", name: "Tan Wei Ming", role: "admin", joined_at: "2026-10-03T04:10:00Z" },
  { uid: "u_priya", email: "priya@kopikita.example", name: "Priya Nair", role: "member", joined_at: "2026-10-09T06:30:00Z" },
  { uid: "u_hafiz", email: "hafiz@kopikita.example", name: "Hafiz Osman", role: "member", joined_at: "2026-10-14T01:45:00Z" },
];

export async function mockMembers(): Promise<Member[]> {
  await wait(200);
  return [...members];
}
export async function mockSetRole(uid: string, role: Member["role"]): Promise<Member> {
  await wait(300);
  members = members.map((m) => (m.uid === uid ? { ...m, role } : m));
  return members.find((m) => m.uid === uid)!;
}
export async function mockRemoveMember(uid: string): Promise<void> {
  await wait(300);
  members = members.filter((m) => m.uid !== uid);
}
export async function mockInvite(workspace_id: string, role: Invite["role"]): Promise<Invite> {
  await wait(400);
  return { code: "kq7Xw2pLm9Rt", workspace_id, role, expires_at: new Date(Date.now() + 7 * 864e5).toISOString() };
}
export async function mockAccept(code: string): Promise<WorkspaceRef> {
  await wait(500);
  if (code.length < 8) throw Object.assign(new Error("That invite link is not valid."), { code: "invite_not_found" });
  return { workspace: KOPI, role: "member" };
}
export async function mockWorkspace(id: string): Promise<Workspace> {
  await wait(150);
  return [KOPI, MINE].find((w) => w.id === id) ?? KOPI;
}
export async function mockRename(id: string, name: string): Promise<Workspace> {
  await wait(300);
  const w = [KOPI, MINE].find((x) => x.id === id) ?? KOPI;
  w.name = name;
  me = { ...me, workspaces: me.workspaces?.map((r) => (r.workspace.id === id ? { ...r, workspace: { ...w } } : r)) };
  return { ...w };
}

const ago = (mins: number) => new Date(Date.now() - mins * 60000).toISOString();
const ev = (i: number, mins: number, actor: string, action: AuditEvent["action"], target_label: string | null, detail?: Record<string, unknown> | null, target_id: string | null = mockReview.id): AuditEvent => ({
  id: `ev${i}`,
  at: ago(mins),
  actor,
  action,
  target_id,
  target_label,
  detail: detail ?? null,
  workspace_id: KOPI.id,
});

const ACTIVITY: AuditEvent[] = [
  ev(1, 4, "u_aisyah", "drafted_reply", mockReview.filename, { asks: 4 }),
  ev(2, 6, "u_aisyah", "viewed_review", mockReview.filename),
  ev(3, 9, "u_aisyah", "re_reviewed", mockReview.filename, { model: "gemini-3.8-flash", kb_version: "kb-7f3a91", findings: 4, latency_ms: 11886, added: 1, removed: 1 }),
  ev(4, 52, "u_priya", "reviewed", "svc-02-outlet-cleaning.pdf", { model: "gemini-3.8-flash", kb_version: "kb-7f3a91", findings: 3, latency_ms: 17420 }, "rv_clean"),
  ev(5, 58, "u_priya", "uploaded_contract", "svc-02-outlet-cleaning.pdf", null, "rv_clean"),
  ev(6, 190, "u_wei", "uploaded_policy", "kopi-kita-contracting-policy.pdf", { pages: 4 }, "cd_policy"),
  ev(7, 260, "u_wei", "exported", "hp-03-pos-terminals.pdf", { format: "pdf" }, "rv_pos"),
  ev(8, 1440 + 30, "u_hafiz", "reviewed", "hp-03-pos-terminals.pdf", { model: "gemini-3.8-flash", kb_version: "kb-62c0d4", findings: 4, latency_ms: 21900 }, "rv_pos"),
  ev(9, 1440 + 40, "u_hafiz", "signed_in", null, null, null),
  ev(10, 2 * 1440 + 15, "u_aisyah", "reviewed", mockReview.filename, { model: "gemini-3.8-flash", kb_version: "kb-62c0d4", findings: 4, latency_ms: 14210 }),
  ev(11, 2 * 1440 + 90, "u_aisyah", "deleted_review", "old-vendor-draft.pdf", null, null),
];

export async function mockActivity(all = false): Promise<AuditEvent[]> {
  await wait(250);
  if (!all) return ACTIVITY;
  return [...ACTIVITY, { ...ev(20, 75, "u_ben", "reviewed", null, { findings: 2, latency_ms: 9800 }, "rv_x"), workspace_id: "co-bengkel" }].sort((a, b) => b.at.localeCompare(a.at));
}

export async function mockHistory(id: string): Promise<ReviewSummary[]> {
  await wait(200);
  const cur = mockSummaries.find((s) => s.id === id) ?? mockSummaries[0];
  return [
    { ...cur, id: mockReview.id, created_at: ago(9) },
    { ...cur, id: "rv_prev", created_at: new Date(Date.now() - 2 * 864e5).toISOString(), headline: "Two-year auto-renewal on 90 days' notice, and fees rise 20% a year.", finding_counts: { high: 3, low: 1 } },
  ];
}

export const mockPreviousReview = {
  id: "rv_prev",
  findings: [
    { id: "p1", clause_type: "auto_renewal", title: "Aggressive 24-month renewal with 90-day registered post notice", risk: "high" },
    { id: "p2", clause_type: "payment", title: "Automatic 20% annual price hike without exit right", risk: "high" },
    { id: "p3", clause_type: "termination", title: "Early termination fee covering entire remaining term", risk: "high" },
    { id: "p4", clause_type: "governing_law", title: "Disputes heard in Singapore", risk: "low" },
  ],
};

function days(n: number) {
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(Date.now() - (n - 1 - i) * 864e5);
    return d.toISOString().slice(0, 10);
  });
}

export async function mockOverview(n: number, admin: boolean): Promise<Overview> {
  await wait(300);
  const k = admin ? 3 : 1;
  const series = days(n).map((date, i) => ({ date, count: Math.max(0, Math.round((Math.sin(i / 3) + 1.3) * k + (i > n - 4 ? 3 * k : 0))) }));
  const reviews = series.reduce((a, b) => a + b.count, 0);
  return {
    users: admin ? 23 : 4,
    reviews,
    reviews_by_day: series,
    by_contract_type: { service_agreement: Math.round(reviews * 0.62), nda: Math.round(reviews * 0.38) },
    by_risk: { high: Math.round(reviews * 0.55), medium: Math.round(reviews * 0.3), low: Math.round(reviews * 0.15) },
    top_clause_types: [
      { clause_type: "liability", high: 9 * k, medium: 4 * k, low: k },
      { clause_type: "auto_renewal", high: 8 * k, medium: 3 * k, low: 0 },
      { clause_type: "payment", high: 5 * k, medium: 7 * k, low: 2 * k },
      { clause_type: "termination", high: 6 * k, medium: 3 * k, low: k },
      { clause_type: "ip_ownership", high: 3 * k, medium: 2 * k, low: k },
      { clause_type: "confidentiality", high: 2 * k, medium: 4 * k, low: 3 * k },
    ],
    policy_breaches_by_rule: [
      { rule: "Rule 2.3 · uncapped liability", count: 7 * k },
      { rule: "Rule 4.2 · renewal notice", count: 6 * k },
      { rule: "Rule 5.3 · fixed increases", count: 4 * k },
      { rule: "Rule 7.1 · early exit fees", count: 3 * k },
      { rule: "Rule 9.3 · foreign courts", count: 2 * k },
    ],
    citations_by_kind: { law: 18 * k, case: 6 * k, playbook: 41 * k, company: 22 * k },
    top_cited_sources: [
      { ref: "my-ca1950-s75", title: "Contracts Act 1950, s 75: compensation where a sum or penalty is named", count: 9 * k },
      { ref: "sg-case-denka", title: "Denka Advantech v Seraya Energy: Singapore keeps the Dunlop penalty test", count: 6 * k },
      { ref: "my-ca1950-s28", title: "Contracts Act 1950, s 28: agreements in restraint of trade are void", count: 5 * k },
      { ref: "sg-ucta1977-s2", title: "Unfair Contract Terms Act 1977, s 2", count: 4 * k },
      { ref: "my-pdpa2010-s9", title: "Personal Data Protection Act 2010, s 9", count: 3 * k },
    ],
    latency_ms: { p50: 17900, p95: 48200, max: 62600 },
    cost_usd: { total: 0.0176 * reviews, per_review_mean: 0.0176 },
    tokens: { in: 12400 * reviews, out: 1300 * reviews },
    dropped_unverified: { quotes: 3 * k, citations: 5 * k },
    upcoming_deadlines: [
      { date: days(1)[0].slice(0, 8) + "28", kind: "notice_deadline", contract_type: "service_agreement", days_left: 10 },
      { date: "2026-12-01", kind: "notice_deadline", contract_type: "service_agreement", days_left: 44 },
      { date: "2027-01-31", kind: "renewal", contract_type: "service_agreement", days_left: 105 },
    ],
  };
}

export async function mockAdminUsers(): Promise<AdminUserRow[]> {
  await wait(250);
  return [
    ...members.map((m, i) => ({ uid: m.uid, email: m.email, name: m.name, photo_url: null, created_at: m.joined_at, last_seen_at: ago(4 + i * 300), review_count: [9, 4, 3, 2][i] ?? 1, workspaces: [{ id: KOPI.id, name: KOPI.name, role: m.role }] })),
    { uid: "u_ben", email: "ben@bengkel.example", name: "Ben Lim", photo_url: null, created_at: "2026-10-12T03:00:00Z", last_seen_at: ago(75), review_count: 5, workspaces: [{ id: "co-bengkel", name: "Bengkel Jaya Sdn Bhd", role: "owner" }] },
  ];
}

// ---- standard positions ----

interface KbEntry {
  id: string;
  kind: string;
  contract_type: string | null;
  title: string;
  text: string | null;
}

function section(md: string, name: string): string {
  const m = md.match(new RegExp(`## ${name}\\n([\\s\\S]*?)(\\n## |$)`));
  return m ? m[1].trim() : "";
}
const bullets = (s: string) =>
  s
    .split("\n")
    .filter((l) => /^\s*-\s/.test(l))
    .map((l) => l.replace(/^\s*-\s+/, "").replace(/\*\*/g, "").trim());

const defaults: PlaybookItem[] = (kb as KbEntry[])
  .filter((e) => e.kind === "playbook")
  .map((e) => {
    const [, ct, clause] = e.id.match(/^playbook:([^/]+)\/(.+)$/) ?? [];
    const md = e.text ?? "";
    return {
      id: e.id,
      contract_type: ct,
      clause_type: clause,
      title: e.title,
      standard: bullets(section(md, "What balanced looks like")).join(" ") || section(md, "In plain words").replace(/\s+/g, " "),
      red_flags: bullets(section(md, "Red flags")),
      ask: section(md, "Suggested ask").replace(/^"|"$/g, "").replace(/\s+/g, " "),
      note: "",
      overridden: false,
    };
  });

const overrides = new Map<string, PlaybookItem>([
  [
    "service_agreement/auto_renewal",
    {
      ...defaults.find((d) => d.id === "playbook:service_agreement/auto_renewal")!,
      id: "playbook:co-kopikita/service_agreement/auto_renewal",
      standard: "Renewal for 12 months at most, 30 days' notice to stop it, notice by email accepted, and a reminder from the vendor 60 days before.",
      ask: "Please change renewal to 12-month periods with 30 days' notice by email, and send us a reminder 60 days before the renewal date.",
      note: "Matches Rule 4 of our contracting policy. The CFO signs off any auto-renewal.",
      overridden: true,
      updated_by: "Tan Wei Ming",
      updated_at: "2026-10-16T03:20:00Z",
    },
  ],
]);

export async function mockPlaybook(contractType?: string): Promise<PlaybookItem[]> {
  await wait(250);
  return defaults
    .filter((d) => !contractType || d.contract_type === contractType)
    .map((d) => overrides.get(`${d.contract_type}/${d.clause_type}`) ?? d);
}

export async function mockSavePosition(type: string, clause: string, body: PlaybookOverrideIn): Promise<PlaybookItem> {
  await wait(400);
  const base = defaults.find((d) => d.contract_type === type && d.clause_type === clause)!;
  const item = { ...base, ...body, id: `playbook:co-kopikita/${type}/${clause}`, overridden: true, updated_by: me.name, updated_at: new Date().toISOString() };
  overrides.set(`${type}/${clause}`, item);
  return item;
}

export async function mockRevertPosition(type: string, clause: string): Promise<void> {
  await wait(300);
  overrides.delete(`${type}/${clause}`);
}
