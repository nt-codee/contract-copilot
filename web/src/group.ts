// Turns raw search passages into one entry per contract: best match first,
// near-duplicate passages folded together, page furniture stripped.
import type { SearchHit } from "./types";

export interface Match {
  hit: SearchHit;
  /** finding title when the passage came from a finding ("Title: quote") */
  title: string | null;
  /** the contract words */
  text: string;
}

export interface ContractMatches {
  review_id: string;
  filename: string;
  best: Match;
  more: Match[];
}

const FURNITURE = [/Sample document for testing\.?/gi, /All parties are fictional\.?/gi, /\bPage \d+ of \d+\b/gi];

function clean(s: string): string {
  let t = s;
  for (const re of FURNITURE) t = t.replace(re, " ");
  return t.replace(/\s+/g, " ").trim();
}

function words(s: string): Set<string> {
  return new Set(s.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? []);
}

/** Share of the smaller passage's words found in the larger one. */
function overlap(a: Set<string>, b: Set<string>): number {
  const [s, l] = a.size < b.size ? [a, b] : [b, a];
  if (s.size === 0) return 1;
  let n = 0;
  s.forEach((w) => l.has(w) && n++);
  return n / s.size;
}

function toMatch(hit: SearchHit): Match {
  const text = clean(hit.text);
  if (hit.finding_id) {
    const i = text.indexOf(": ");
    if (i > 0 && i < 120) return { hit, title: text.slice(0, i), text: text.slice(i + 2) };
  }
  return { hit, title: null, text };
}

export function groupHits(hits: SearchHit[], perContract = 2): ContractMatches[] {
  const groups = new Map<string, Match[]>();
  for (const h of [...hits].sort((a, b) => b.score - a.score)) {
    const m = toMatch(h);
    if (m.text.length < 25) continue;
    const list = groups.get(h.review_id) ?? [];
    const w = words(m.text);
    const dup = list.findIndex((x) => overlap(words(x.text), w) > 0.6);
    if (dup >= 0) {
      // Keep the version tied to a finding: it carries a title and a flag.
      if (!list[dup].hit.finding_id && h.finding_id) list[dup] = { ...m, hit: { ...h, score: list[dup].hit.score } };
      continue;
    }
    list.push(m);
    groups.set(h.review_id, list);
  }
  return [...groups.entries()]
    .map(([review_id, list]) => ({
      review_id,
      filename: list[0].hit.filename,
      best: list[0],
      more: list.slice(1, 1 + perContract),
    }))
    .sort((a, b) => b.best.hit.score - a.best.hit.score);
}

/** A window of about `max` characters around the first query word that appears. */
export function excerpt(text: string, query: string, max = 240): string {
  if (text.length <= max) return text;
  const terms = (query.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? []).filter(Boolean);
  const low = text.toLowerCase();
  const at = terms.map((t) => low.indexOf(t)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0;
  let start = Math.max(0, at - Math.floor(max / 3));
  const sp = text.lastIndexOf(" ", start);
  if (start > 0 && sp > 0) start = sp + 1;
  let end = Math.min(text.length, start + max);
  const sp2 = text.indexOf(" ", end);
  if (end < text.length && sp2 > 0) end = sp2;
  return `${start > 0 ? "… " : ""}${text.slice(start, end)}${end < text.length ? " …" : ""}`;
}
