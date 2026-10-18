// Offline knowledge base for VITE_MOCK=1, built from ../knowledge by tools/knowledge-mock.mjs.
import type { KnowledgeQuery } from "../api";
import type { KnowledgeEntry } from "../types";
import data from "./knowledge.data.json";

export const mockKnowledge = data as KnowledgeEntry[];

export function filterKnowledge(q: KnowledgeQuery): KnowledgeEntry[] {
  const words = (q.q ?? "").toLowerCase().split(/\s+/).filter((w) => w.length > 1);
  return mockKnowledge
    .filter((e) => !q.kind || e.kind === q.kind)
    .filter((e) => !q.jurisdiction || e.jurisdiction === q.jurisdiction)
    .filter((e) => !q.contract_type || !e.contract_type || e.contract_type === q.contract_type)
    .filter((e) => {
      if (words.length === 0) return true;
      const hay = `${e.title} ${e.citation ?? ""} ${e.plain ?? ""} ${(e.clause_types ?? []).join(" ")}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    })
    .map(({ text: _t, ...rest }) => rest);
}
