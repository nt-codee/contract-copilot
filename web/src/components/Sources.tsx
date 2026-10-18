import { useState } from "react";
import type { Citation, CitationKind } from "../types";

const KIND: Record<CitationKind, string> = {
  law: "Law",
  case: "Court case",
  playbook: "Usual terms",
  company: "Your rules",
};

const PLACE: Record<string, string> = { my: "Malaysia", sg: "Singapore" };

/** Compact source chips; one opens at a time to show its excerpt. */
export default function Sources({ items, idBase }: { items: Citation[]; idBase: string }) {
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
              <span className={`tab shrink-0 !text-[0.72rem] ${open === i ? "text-desk-text" : "text-ink-mute"}`}>{KIND[it.kind]}</span>
              <span className="line-clamp-2">{it.title}</span>
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
          <blockquote className="mt-1 font-doc text-[0.98rem] leading-relaxed text-ink">“{c.excerpt}”</blockquote>
          {(c.kind === "law" || c.kind === "case") && c.url && (
            <a
              href={c.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-flex min-h-[44px] items-center gap-1 text-[0.92rem] font-semibold text-desk underline underline-offset-4 hover:text-ink"
            >
              Read the source <span aria-hidden="true">↗</span>
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )}
        </div>
      )}
    </div>
  );
}
