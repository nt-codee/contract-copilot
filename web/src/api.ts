import type {
  AdminUserRow,
  AuditEvent,
  Invite,
  Member,
  Overview,
  PlaybookItem,
  PlaybookOverrideIn,
  User,
  Workspace,
  WorkspaceRef,
  ApiErrorBody,
  ClauseType,
  CompanyDoc,
  Risk,
  KnowledgeEntry,
  NegotiationDraft,
  ReviewResult,
  ReviewSummary,
  Sample,
  SearchHit,
} from "./types";

const MOCK = import.meta.env.VITE_MOCK === "1";

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function send(path: string, init?: RequestInit): Promise<Response> {
  const { authHeaders } = await import("./session");
  const headers = new Headers(init?.headers);
  for (const [k, v] of Object.entries(await authHeaders())) headers.set(k, v);
  return fetch(path, { ...init, headers });
}

async function request<T>(path: string, init?: RequestInit, onResponse?: (res: Response) => void): Promise<T> {
  let res: Response;
  try {
    res = await send(path, init);
  } catch {
    throw new ApiError(
      "network",
      "We could not reach the server. Check your connection and try again.",
    );
  }
  if (!res.ok) {
    let message = "Something went wrong on our side. Please try again.";
    let code = "unknown";
    try {
      const body = (await res.json()) as Partial<ApiErrorBody>;
      if (body.error?.message) message = body.error.message;
      if (body.error?.code) code = body.error.code;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(code, message);
  }
  onResponse?.(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const pdfUrl = (id: string, page?: number) =>
  (MOCK ? "/samples/msa-02-autorenew.pdf" : `/api/reviews/${encodeURIComponent(id)}/pdf`) + (page ? `#page=${page}` : "");

/** A bundled sample contract's PDF. Public: the samples need no sign-in. */
export const samplePdfUrl = (id: string) => `/api/samples/${encodeURIComponent(id)}/pdf`;

export async function listSamples(): Promise<Sample[]> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(200);
    return m.mockSamples;
  }
  return request<Sample[]>("/api/samples");
}

async function mockSignedIn(): Promise<boolean> {
  const { getSession, readOnlyReason } = await import("./session");
  for (let i = 0; getSession().loading && i < 50; i++) await wait(100);
  const s = getSession();
  const why = readOnlyReason(s);
  if (why) throw new ApiError("guest_read_only", why);
  return !!s.user;
}

export const SIGN_IN_CODE = "sign_in_required";

/** Review options; anything left out is detected from the contract. */
export interface ReviewOptions {
  contract_type?: string;
  jurisdiction?: "my" | "sg";
  apply_company_rules?: boolean;
  perspective?: "business" | "individual";
  /** which company policies to apply; left out = all */
  company_doc_ids?: string[];
}

export async function uploadReview(file: File, opts: ReviewOptions = {}): Promise<ReviewResult> {
  if (MOCK) {
    if (!(await mockSignedIn())) throw new ApiError(SIGN_IN_CODE, "Sign in with Google to review your own contract.");
    const m = await import("./mocks");
    await wait(2500 + Math.random() * 2000);
    if (/fail/i.test(file.name)) throw new ApiError("unreadable_pdf", "We could not read any text in this PDF. Try a clearer scan or the original file.");
    return { ...m.mockReview, filename: file.name, contract_type: (opts.contract_type as ReviewResult["contract_type"]) ?? m.mockReview.contract_type, jurisdiction: opts.jurisdiction ?? m.mockReview.jurisdiction };
  }
  const form = new FormData();
  form.append("file", file);
  if (opts.contract_type) form.append("contract_type", opts.contract_type);
  if (opts.jurisdiction) form.append("jurisdiction", opts.jurisdiction.toUpperCase());
  if (opts.apply_company_rules !== undefined) form.append("apply_company_rules", String(opts.apply_company_rules));
  if (opts.perspective) form.append("perspective", opts.perspective);
  if (opts.apply_company_rules !== false && opts.company_doc_ids?.length) form.append("company_doc_ids", opts.company_doc_ids.join(","));
  return request<ReviewResult>("/api/reviews", { method: "POST", body: form });
}

/** Opens a sample. The server answers from an earlier run when it has one; `refresh` re-runs it (signed in only). */
export type SampleReviewResult = ReviewResult & { sample_opening?: "cached" | "copied" | "fresh" };

export async function reviewSample(sampleId: string, refresh = false): Promise<SampleReviewResult> {
  if (MOCK) {
    if (refresh && !(await mockSignedIn())) throw new ApiError(SIGN_IN_CODE, "Sign in with Google to run a fresh review.");
    const m = await import("./mocks");
    await wait(refresh ? 6500 : 900);
    return refresh
      ? { ...m.mockReview, created_at: new Date().toISOString(), sample_opening: "fresh" }
      : { ...m.mockReview, sample_opening: "cached" };
  }
  let source: SampleReviewResult["sample_opening"];
  const result = await request<ReviewResult>(
    `/api/reviews/sample/${encodeURIComponent(sampleId)}${refresh ? "?refresh=1" : ""}`,
    { method: "POST" },
    (res) => {
      const value = res.headers.get("X-Review-Source");
      if (value === "cached" || value === "copied" || value === "fresh") source = value;
    },
  );
  return { ...result, sample_opening: source };
}

export async function getReview(id: string): Promise<ReviewResult> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(150);
    if (id === "rv_prev") {
      const w = await mw();
      return { ...m.mockReview, id, previous_review_id: null, findings: w.mockPreviousReview.findings.map((f) => ({ ...m.mockReview.findings[0], ...f, risk: f.risk as Risk, clause_type: f.clause_type as ClauseType })) };
    }
    return m.mockReview;
  }
  return request<ReviewResult>(`/api/reviews/${encodeURIComponent(id)}`);
}

export async function listReviews(): Promise<ReviewSummary[]> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(150);
    return m.mockSummaries;
  }
  return request<ReviewSummary[]>("/api/reviews");
}

export async function draftNegotiation(id: string): Promise<NegotiationDraft> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(1800);
    return m.mockNegotiation;
  }
  return request<NegotiationDraft>(
    `/api/reviews/${encodeURIComponent(id)}/negotiation`,
    { method: "POST" },
  );
}

export async function searchContracts(q: string, limit = 10): Promise<SearchHit[]> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(300);
    return m.mockHits;
  }
  return request<SearchHit[]>(
    `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );
}

export async function listCompanyDocs(): Promise<CompanyDoc[]> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(200);
    return [...m.mockCompanyDocs];
  }
  try {
    return await request<CompanyDoc[]>("/api/company/docs");
  } catch (e) {
    // No documents stored yet reads the same as an empty list.
    if (e instanceof ApiError && e.code === "not_found") return [];
    throw e;
  }
}

export async function uploadCompanyDoc(file: File): Promise<CompanyDoc> {
  if (MOCK) {
    if (!(await mockSignedIn())) throw new ApiError(SIGN_IN_CODE, "Sign in with Google to add your own company rules.");
    const m = await import("./mocks");
    await wait(2500);
    const doc: CompanyDoc = {
      id: `cd_${Date.now().toString(36)}`,
      filename: file.name,
      title: null,
      created_at: new Date().toISOString(),
      pages: 3,
    };
    m.mockCompanyDocs.unshift(doc);
    return doc;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    return await request<CompanyDoc>("/api/company/docs", { method: "POST", body: form });
  } catch (e) {
    if (e instanceof ApiError && e.code === "not_found")
      throw new ApiError("not_found", "The policy could not be added right now. Try again in a few minutes.");
    throw e;
  }
}

export async function deleteCompanyDoc(id: string): Promise<void> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(300);
    const i = m.mockCompanyDocs.findIndex((d) => d.id === id);
    if (i >= 0) m.mockCompanyDocs.splice(i, 1);
    return;
  }
  await request<void>(`/api/company/docs/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export interface KnowledgeQuery {
  kind?: string;
  jurisdiction?: string;
  contract_type?: string;
  q?: string;
}

export async function listKnowledge(query: KnowledgeQuery = {}): Promise<KnowledgeEntry[]> {
  if (MOCK) {
    const m = await import("./mocks/knowledge");
    await wait(200);
    return m.filterKnowledge(query);
  }
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v) p.set(k, v);
  const qs = p.toString();
  return request<KnowledgeEntry[]>(`/api/knowledge${qs ? `?${qs}` : ""}`);
}

export async function getKnowledge(id: string): Promise<KnowledgeEntry> {
  if (MOCK) {
    const m = await import("./mocks/knowledge");
    await wait(150);
    const e = m.mockKnowledge.find((k) => k.id === id);
    if (!e) throw new ApiError("not_found", "That entry is not in the knowledge base.");
    return e;
  }
  return request<KnowledgeEntry>(`/api/knowledge/${encodeURIComponent(id)}`);
}

// ---- signed-in workspace (round 4) ----

const mw = () => import("./mocks/workspace");

export async function getMe(): Promise<User> {
  if (MOCK) return (await mw()).mockMe();
  return request<User>("/api/me");
}

export async function deleteMyData(): Promise<{ deleted: Record<string, number> }> {
  if (MOCK) return (await mw()).mockDeleteMe();
  return request("/api/me/delete", { method: "POST" });
}

export async function listActivity(limit = 100): Promise<AuditEvent[]> {
  if (MOCK) return (await mw()).mockActivity();
  return request<AuditEvent[]>(`/api/activity?limit=${limit}`);
}

export async function reviewHistory(id: string): Promise<ReviewSummary[]> {
  if (MOCK) return (await mw()).mockHistory(id);
  return request<ReviewSummary[]>(`/api/reviews/${encodeURIComponent(id)}/history`);
}

export async function getWorkspace(id: string): Promise<Workspace> {
  if (MOCK) return (await mw()).mockWorkspace(id);
  return request<Workspace>(`/api/workspaces/${encodeURIComponent(id)}`);
}

export async function renameWorkspace(id: string, name: string): Promise<Workspace> {
  if (MOCK) return (await mw()).mockRename(id, name);
  return request<Workspace>(`/api/workspaces/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function listMembers(id: string): Promise<Member[]> {
  if (MOCK) return (await mw()).mockMembers();
  return request<Member[]>(`/api/workspaces/${encodeURIComponent(id)}/members`);
}

export async function setMemberRole(id: string, uid: string, role: Member["role"]): Promise<Member> {
  if (MOCK) return (await mw()).mockSetRole(uid, role);
  return request<Member>(`/api/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(uid)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export async function removeMember(id: string, uid: string): Promise<void> {
  if (MOCK) return (await mw()).mockRemoveMember(uid);
  return request<void>(`/api/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(uid)}`, { method: "DELETE" });
}

export async function createInvite(id: string, role: Invite["role"]): Promise<Invite> {
  if (MOCK) return (await mw()).mockInvite(id, role);
  return request<Invite>(`/api/workspaces/${encodeURIComponent(id)}/invites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export async function acceptInvite(code: string): Promise<WorkspaceRef> {
  if (MOCK) return (await mw()).mockAccept(code);
  return request<WorkspaceRef>(`/api/invites/${encodeURIComponent(code)}/accept`, { method: "POST" });
}

export async function workspaceOverview(id: string, days = 30): Promise<Overview> {
  if (MOCK) return (await mw()).mockOverview(days, false);
  return request<Overview>(`/api/workspaces/${encodeURIComponent(id)}/overview?days=${days}`);
}

export async function adminOverview(days = 30): Promise<Overview> {
  if (MOCK) return (await mw()).mockOverview(days, true);
  return request<Overview>(`/api/admin/overview?days=${days}`);
}

export async function adminActivity(limit = 100): Promise<AuditEvent[]> {
  if (MOCK) return (await mw()).mockActivity(true);
  return request<AuditEvent[]>(`/api/admin/activity?limit=${limit}`);
}

export async function adminUsers(): Promise<AdminUserRow[]> {
  if (MOCK) return (await mw()).mockAdminUsers();
  return request<AdminUserRow[]>("/api/admin/users");
}

export async function listPlaybook(contractType?: string): Promise<PlaybookItem[]> {
  if (MOCK) return (await mw()).mockPlaybook(contractType);
  return request<PlaybookItem[]>(`/api/playbook${contractType ? `?contract_type=${encodeURIComponent(contractType)}` : ""}`);
}

export async function savePosition(type: string, clause: string, body: PlaybookOverrideIn): Promise<PlaybookItem> {
  if (MOCK) return (await mw()).mockSavePosition(type, clause, body);
  return request<PlaybookItem>(`/api/playbook/${encodeURIComponent(type)}/${encodeURIComponent(clause)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function revertPosition(type: string, clause: string): Promise<void> {
  if (MOCK) return (await mw()).mockRevertPosition(type, clause);
  return request<void>(`/api/playbook/${encodeURIComponent(type)}/${encodeURIComponent(clause)}`, { method: "DELETE" });
}

export const companyPdfUrl = (docId: string) =>
  MOCK ? "/samples/kopi-kita-contracting-policy.pdf" : `/api/company/docs/${encodeURIComponent(docId)}/pdf`;

/** Opens a stored PDF in a new tab, with sign-in headers (a plain link cannot send them). */
export async function openPdfTab(url: string): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const res = await send(url.split("#")[0]);
    if (!res.ok) throw new Error();
    const blob = URL.createObjectURL(await res.blob());
    const hash = url.includes("#") ? url.slice(url.indexOf("#")) : "";
    if (tab) tab.location.href = blob + hash;
    else window.location.href = blob + hash;
  } catch {
    tab?.close();
    window.open(url, "_blank", "noopener");
  }
}

/** Headers pdf.js should send when it fetches a document. */
export async function pdfHeaders(): Promise<Record<string, string>> {
  const { authHeaders } = await import("./session");
  return authHeaders();
}

export async function deleteReview(id: string): Promise<void> {
  if (MOCK) {
    await wait(400);
    return;
  }
  return request<void>(`/api/reviews/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Downloads the review as a JSON file, sending sign-in headers. */
export async function exportReview(id: string, filename: string): Promise<void> {
  let blob: Blob;
  if (MOCK) {
    const m = await import("./mocks");
    blob = new Blob([JSON.stringify(m.mockReview, null, 2)], { type: "application/json" });
  } else {
    const res = await send(`/api/reviews/${encodeURIComponent(id)}/export`);
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as Partial<ApiErrorBody> | null;
      throw new ApiError(body?.error?.code ?? "unknown", body?.error?.message ?? "The export did not finish. Try again.");
    }
    blob = await res.blob();
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename.replace(/\.pdf$/i, "")}-review.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
