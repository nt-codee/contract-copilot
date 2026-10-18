import { useState } from "react";
import { Link } from "react-router-dom";
import { isCompanyPosition, knowledgeId } from "./Evidence";
import type { Citation, CitationKind } from "../types";

const KIND: Record<CitationKind, string> = {
  law: "Law",
  case: "Court case",
  playbook: "Usual terms",
  company: "Your rules",
};

const PLACE: Record<string, string> = { my: "Malaysia", sg: "Singapore" };

/** Knowledge excerpts are markdown; show them as plain sentences. */
function plain(md: string): string {
  return md
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(^|\s)[-*]\s+/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** "kopi-kita-contracting-policy" reads as "Kopi Kita contracting policy". */
function label(c: Citation): string {
  if (c.kind !== "company" || /\s/.test(c.title)) return c.title;
  return c.title.split(/[-_]+/).map((w, i) => (i < 2 ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}

/** Compact source chips; one opens at a time to show its excerpt. */
export default function Sources({ items, idBase, onEvidence }: { items: Citation[]; idBase: string; onEvidence?: (c: Citation) => void }) {
  const [open, setOpen] = useState<number | null>(null);
  if (items.length === 0) return null;
  const c = open == null ? null : items[open];
  return (
    <div className="mt-4">
      <p className="tab text-ink-mute" id={`${idBase}-src`}>
        Checked against
      </p>
      <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-labelledby={`${idBase}-src`}>
        {items.map((it, i) => (
          <li key={it.ref + i}>
            <button
              type="button"
              aria-expanded={open === i}
              aria-controls={`${idBase}-src-panel`}
              onClick={() => setOpen(open === i ? null : i)}
              className={`inline-flex min-h-[44px] max-w-full items-center gap-2 rounded-[3px] border px-2.5 py-1 text-left text-[0.88rem] leading-tight transition-colors ${
                open === i ? "border-ink bg-ink text-white" : "border-paper-line bg-white text-ink hover:border-ink-mute"
              }`}
            >
              <span className={`tab shrink-0 !text-[0.72rem] ${open === i ? "text-desk-text" : "text-ink-mute"}`}>{isCompanyPosition(it) ? "Your company’s position" : KIND[it.kind]}</span>
              <span className="line-clamp-2">{label(it)}</span>
            </button>
          </li>
        ))}
      </ul>
      {c && (
        <div id={`${idBase}-src-panel`} className="mt-2 rounded-[3px] border border-paper-line bg-white px-3 py-2.5">
          <p className="tab text-ink-mute">
            {KIND[c.kind]}
            {c.jurisdiction && PLACE[c.jurisdiction] ? ` · ${PLACE[c.jurisdiction]}` : ""}
            {c.kind === "company" && c.page ? ` · page ${c.page}` : ""}
          </p>
          <blockquote className="mt-1 font-doc text-[0.98rem] leading-relaxed text-ink">“{plain(c.excerpt)}”</blockquote>
          <span className="mt-1 flex flex-wrap gap-x-5">
          {onEvidence && (
            <button
              type="button"
              onClick={() => onEvidence(c)}
              className="inline-flex min-h-[44px] items-center gap-1.5 text-[0.92rem] font-semibold text-desk underline underline-offset-4 hover:text-ink"
            >
              <svg width="16" height="12" viewBox="0 0 16 12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="1" y="1" width="6" height="10" rx="1" /><rect x="9" y="1" width="6" height="10" rx="1" /></svg>
              See it side by side
            </button>
          )}
          {c.kind !== "company" && (
            <Link
              to={`/knowledge/${encodeURIComponent(knowledgeId(c))}`}
              className="inline-flex min-h-[44px] items-center gap-1 text-[0.92rem] font-semibold text-desk underline underline-offset-4 hover:text-ink"
            >
              {c.kind === "playbook" ? "Why this is the usual term" : "Read it in full"} <span aria-hidden="true">→</span>
            </Link>
          )}
          {c.kind === "company" && (
            <Link to="/rules" className="inline-flex min-h-[44px] items-center gap-1 text-[0.92rem] font-semibold text-desk underline underline-offset-4 hover:text-ink">
              See your rules <span aria-hidden="true">→</span>
            </Link>
          )}
          {(c.kind === "law" || c.kind === "case") && (c.source_url || c.url) && !onEvidence && (
            <a
              href={(c.source_url || c.url)!}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-flex min-h-[44px] items-center gap-1 text-[0.92rem] font-semibold text-desk underline underline-offset-4 hover:text-ink"
            >
              Official source <span aria-hidden="true">↗</span>
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )}
          </span>
        </div>
      )}
    </div>
  );
}
