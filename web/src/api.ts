import type {
  ApiErrorBody,
  CompanyDoc,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
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
  return (await res.json()) as T;
}

export const pdfUrl = (id: string, page?: number) =>
  `/api/reviews/${encodeURIComponent(id)}/pdf${page ? `#page=${page}` : ""}`;

export async function listSamples(): Promise<Sample[]> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(200);
    return m.mockSamples;
  }
  return request<Sample[]>("/api/samples");
}

export async function uploadReview(file: File): Promise<ReviewResult> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(6500);
    return { ...m.mockReview, filename: file.name };
  }
  const form = new FormData();
  form.append("file", file);
  return request<ReviewResult>("/api/reviews", { method: "POST", body: form });
}

export async function reviewSample(sampleId: string): Promise<ReviewResult> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(6500);
    return m.mockReview;
  }
  return request<ReviewResult>(
    `/api/reviews/sample/${encodeURIComponent(sampleId)}`,
    { method: "POST" },
  );
}

export async function getReview(id: string): Promise<ReviewResult> {
  if (MOCK) {
    const m = await import("./mocks");
    await wait(150);
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
  const res = await fetch(`/api/company/docs/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => null);
  if (!res) throw new ApiError("network", "We could not reach the server. Check your connection and try again.");
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as Partial<ApiErrorBody> | null;
    throw new ApiError(body?.error?.code ?? "unknown", body?.error?.message ?? "The document was not removed. Try again.");
  }
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
