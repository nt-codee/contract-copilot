// Derives "which contract breaks which rule" from reviews: company citations
// say which rule, policy_breach says how.
import type { Citation, CompanyDoc, Finding, ReviewResult } from "./types";

export interface RuleUse {
  review: ReviewResult;
  finding: Finding;
  /** finding number in risk order, as on the review screen */
  n: number;
}

export interface PolicyRule {
  key: string;
  /** e.g. "4.2", or null when the title names no rule number */
  number: string | null;
  title: string;
  excerpt: string;
  page: number | null;
  docId: string | null;
  breaches: RuleUse[];
}

const ORDER = { high: 0, medium: 1, low: 2 } as const;

function ruleNumber(...texts: (string | null | undefined)[]): string | null {
  for (const t of texts) {
    const m = t?.match(/\bRules?\s+(\d+(?:\.\d+)*)/i);
    if (m) return m[1];
  }
  return null;
}

/** The sentence(s) of rule `n` inside a chunk of policy text, else the chunk's first clause. */
export function clauseText(chunk: string, n: string | null): string {
  const text = chunk.replace(/\s+/g, " ").trim();
  const next = /\s(?=\d+\.\d+\s|Rule\s+\d+\.)/g;
  const startAt = (i: number) => {
    next.lastIndex = i + 1;
    const m = next.exec(text);
    return text.slice(i, m ? m.index : undefined).trim();
  };
  if (n) {
    const i = text.search(new RegExp(`(^|\\s)${n.replace(/\./g, "\\.")}\\s`));
    if (i >= 0) return startAt(i === 0 ? 0 : i + 1).replace(new RegExp(`^${n.replace(/\./g, "\\.")}\\s`), "");
  }
  return startAt(0).replace(/^\d+(\.\d+)*\s/, "");
}

function cmp(a: string | null, b: string | null): number {
  if (!a || !b) return a ? -1 : b ? 1 : 0;
  const x = a.split(".").map(Number);
  const y = b.split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) - (y[i] ?? 0);
  return 0;
}

export function deriveRules(reviews: ReviewResult[], docs: CompanyDoc[]): PolicyRule[] {
  const ids = new Set(docs.map((d) => d.id));
  const rules = new Map<string, PolicyRule>();
  for (const r of reviews) {
    const ranked = [...r.findings].sort((a, b) => ORDER[a.risk] - ORDER[b.risk]);
    ranked.forEach((f, i) => {
      for (const c of (f.citations ?? []) as Citation[]) {
        if (c.kind !== "company") continue;
        const docId = c.ref.startsWith("company:") ? c.ref.slice(8) : null;
        if (docId && ids.size && !ids.has(docId)) continue;
        const lead = c.excerpt.trim().match(/^(\d+(?:\.\d+)?)\s/)?.[1] ?? null;
        const named = ruleNumber(f.policy_breach, c.title);
        const inChunk = named && new RegExp(`(^|\\s)${named.replace(/\./g, "\\.")}\\s`).test(c.excerpt);
        // Trust the rule the breach names; quote its text only when the cited passage holds it.
        const number = named ?? lead;
        const quoted = !named || inChunk;
        const key = `${docId ?? ""}|${number ?? c.excerpt.slice(0, 60)}`;
        const rule = rules.get(key) ?? { key, number, title: c.title, excerpt: quoted ? clauseText(c.excerpt, number) : "", page: c.page ?? null, docId, breaches: [] };
        if (f.policy_breach && !rule.breaches.some((b) => b.review.id === r.id && b.finding.id === f.id)) {
          rule.breaches.push({ review: r, finding: f, n: i + 1 });
        }
        if (!rule.excerpt && quoted) rule.excerpt = clauseText(c.excerpt, number);
        rules.set(key, rule);
      }
    });
  }
  return [...rules.values()].sort((a, b) => cmp(a.number, b.number));
}

/** "kopi-kita-contracting-policy" -> "Kopi Kita contracting policy" */
export function docTitle(d: CompanyDoc): string {
  const t = d.title || d.filename.replace(/\.pdf$/i, "");
  if (/\s/.test(t)) return t;
  const words = t.split(/[-_]+/).filter(Boolean);
  return words.map((w, i) => (i < 2 ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}
