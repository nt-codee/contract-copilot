import { forwardRef } from "react";
import { openPdfTab, pdfUrl } from "../api";
import { RISK_LABEL } from "../format";
import { getSession } from "../session";
import type { Marked } from "./Reader";
import { FlagTab, RiskLabel } from "./Risk";
import Sources from "./Sources";
import type { Citation, Finding } from "../types";

const NUM = /^\s*(?:clause\s+|klausa\s+|fasal\s+)?(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s/i;

/** Clause number when the quote itself starts with one ("4.2 Upon …"), else null. */
function clauseNumber(quote: string): string | null {
  const m = NUM.exec(quote);
  return m ? m[1] : null;
}

/** "Auto renewal" from "auto_renewal". */
function clauseTypeLabel(t: string): string {
  const s = t.replace(/_/g, " ");
  return s.replace(/^\w/, (c) => c.toUpperCase()).replace(/\bip\b/i, "IP");
}

/**
 * What the "Where" chip says: the API's clause_ref ("Clause 4.2") when present, else a number at
 * the start of the quote, else the clause type ("Auto renewal"); then the page.
 * `clause_ref` is read optionally until the shared types carry it.
 */
function whereLabel(f: Finding & { clause_ref?: string | null }): { clause: string; page: string } {
  const ref = f.clause_ref?.trim();
  if (ref) return { clause: /^\d/.test(ref) ? `Clause ${ref}` : ref, page: `Page ${f.page}` };
  const n = clauseNumber(f.quote);
  return { clause: n ? `Clause ${n}` : clauseTypeLabel(f.clause_type), page: `Page ${f.page}` };
}

/** First sentence, and the rest. Splits on . ! ? followed by space and a capital or quote. */
function firstSentence(text: string): [string, string] {
  const t = text.trim();
  const m = /^(.+?[.!?])\s+(?=[A-Z“"(])/.exec(t);
  if (!m) return [t, ""];
  return [m[1], t.slice(m[0].length).trim()];
}


/** One line: what was checked for this finding. Match detail only when it was not exact. */
function Checked({ f }: { f: Finding }) {
  const v = f.verification;
  if (!v) return null;
  const extra = [
    v.quote_match === "fuzzy" ? `close match${v.score != null ? `, ${Math.round(v.score)}% the same` : ""}` : null,
    v.page_corrected_from != null ? `moved from page ${v.page_corrected_from}` : null,
  ].filter(Boolean);
  return (
    <p className="meta mt-3 flex items-center gap-1.5 text-ink-soft">
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" className="shrink-0 text-low">
        <path d="M2 7.5 5.5 11 12 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      Quote checked on page {f.page}
      {extra.length > 0 && ` (${extra.join(", ")})`}
    </p>
  );
}

interface Props {
  m: Marked;
  reviewId: string;
  open: boolean;
  /** True while the page text is still being searched for this quote. */
  locating: boolean;
  onPick: (id: string) => void;
  /** Scroll the page to this finding's box and pulse it. */
  onShow?: (id: string) => void;
  /** Narrow screens: say that tapping opens the page. */
  opensPage?: boolean;
  onEvidence?: (c: Citation) => void;
}

const EDGE = { high: "bg-high", medium: "bg-medium-box", low: "bg-low" } as const;

/**
 * One finding in the rail. Closed, it is a contents line: flag (number + risk), short title,
 * clause and page. Open: a one-line summary, a "Where" chip that shows the box on the page,
 * the rule it breaks, and the rest folded under "Details".
 */
const FindingItem = forwardRef<HTMLButtonElement, Props>(function FindingItem(
  { m, reviewId, open, locating, onPick, onShow, opensPage, onEvidence },
  ref,
) {
  const { f, n } = m;
  const onPage = m.boxes.length > 0;
  const panelId = `finding-${f.id}`;
  const where = whereLabel(f);
  const [summary, rest] = firstSentence(f.why_risky);
  return (
    <li className={`relative ${open ? "bg-white shadow-lift" : ""}`}>
      {open && <span aria-hidden="true" className={`absolute inset-y-0 left-0 w-1 ${EDGE[f.risk]}`} />}
      <h3>
        <button
          ref={ref}
          type="button"
          onClick={() => onPick(f.id)}
          aria-expanded={opensPage ? undefined : open}
          aria-controls={opensPage ? undefined : panelId}
          className="grid w-full grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 px-5 py-3 text-left transition-colors hover:bg-white"
        >
          <FlagTab n={n} risk={f.risk} className="mt-0.5 !rounded-[3px]" />
          <span className="min-w-0">
            <span className="block font-semibold leading-snug text-ink [text-wrap:pretty]">
              {f.title}
              <span className="sr-only">. {RISK_LABEL[f.risk]}.</span>
            </span>
            <span className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="meta text-ink-mute">
                {where.clause}, {where.page.toLowerCase()}
              </span>
              {f.policy_breach && (
                <span className="meta rounded-[2px] bg-ink px-1.5 text-white">
                  <span className="sr-only">. </span>Breaks your rule
                </span>
              )}
              {opensPage && (
                <span className="meta text-accent">
                  <span className="sr-only">. </span>See it on the page
                </span>
              )}
            </span>
          </span>
        </button>
      </h3>
      {open && (
        <div id={panelId} className="px-5 pb-5 pl-[4.6rem]">
          <p className="text-base leading-snug text-ink">
            <span className="mr-2 inline-flex align-[0.05em]">
              <RiskLabel risk={f.risk} />
            </span>
            <span className="sr-only">: </span>
            {summary}
          </p>

          <p className="mt-3 flex flex-wrap items-center gap-2">
            <span className="meta text-ink-mute">
              Where<span className="sr-only">:</span>
            </span>
            {onShow ? (
              <button
                type="button"
                onClick={() => onShow(f.id)}
                className="meta inline-flex min-h-[44px] items-center gap-1.5 rounded-[3px] border border-accent/40 bg-white px-2.5 font-semibold text-accent transition-colors hover:border-accent hover:bg-accent-tint lg:min-h-[36px]"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10" />
                </svg>
                {where.clause}, {where.page.toLowerCase()}
                <span className="sr-only">: show it on the contract</span>
              </button>
            ) : (
              <span className="meta text-ink">
                {where.clause}, {where.page.toLowerCase()}
              </span>
            )}
          </p>

          {f.policy_breach && (
            <div className="mt-3 flex gap-2.5 rounded-[3px] bg-ink px-3 py-2.5 text-white">
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" className="mt-0.5 shrink-0">
                <rect x="1.5" y="1.5" width="15" height="15" rx="1.5" fill="none" stroke="#FF8A7E" strokeWidth="2" />
                <path d="M5 5l8 8M13 5l-8 8" stroke="#FF8A7E" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <p className="text-sm leading-snug">
                <span className="label block text-[#FFB1A8]">
                  Breaks your company rule<span className="sr-only">:</span>
                </span>
                {f.policy_breach}
              </p>
            </div>
          )}

          {!onPage && !locating && (
            <figure className="mt-3">
              <blockquote className="border-l-2 border-ink-mute pl-3 font-doc text-base leading-relaxed text-ink">“{f.quote}”</blockquote>
              <figcaption className="meta mt-1.5 text-ink-mute">Clause on page {f.page}, not marked on the page</figcaption>
            </figure>
          )}

          <details className="group mt-3 rounded-[3px] border border-paper-line bg-white">
            <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm hover:bg-paper-soft [&::-webkit-details-marker]:hidden">
              <span className="font-semibold text-ink">Details</span>
              <span className="min-w-0 truncate text-ink-mute">{[rest && "why it matters", "usual terms", "what to ask for", (f.citations?.length ?? 0) > 0 && "sources"].filter(Boolean).join(", ")}</span>
              <span aria-hidden="true" className="ml-auto text-ink-mute transition-transform group-open:rotate-180">▾</span>
            </summary>
            <div className="border-t border-paper-line px-3 pb-3 pt-3">
              <dl className="space-y-3 text-sm">
                {rest && (
                  <div>
                    <dt className="label text-ink-soft">Why it matters</dt>
                    <dd className="mt-0.5 text-ink">{rest}</dd>
                  </div>
                )}
                <div>
                  <dt className="label text-ink-soft">Usual terms</dt>
                  <dd className="mt-0.5 text-ink-soft">{f.standard}</dd>
                </div>
                <div className="rounded-[3px] border-l-4 border-ink bg-paper-soft px-3 py-2.5">
                  <dt className="label text-ink">Ask for</dt>
                  <dd className="mt-0.5 font-medium text-ink">{f.suggested_ask}</dd>
                </div>
              </dl>
              <Sources items={f.citations ?? []} idBase={panelId} onEvidence={onEvidence} />
              <Checked f={f} />
              <a
                href={pdfUrl(reviewId, f.page)}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => {
                  if (!getSession().user) return;
                  e.preventDefault();
                  void openPdfTab(pdfUrl(reviewId, f.page));
                }}
                className="mt-1 inline-flex min-h-[44px] items-center gap-1 text-sm font-semibold text-accent underline underline-offset-4 hover:text-accent-deep"
              >
                Open page {f.page} of the PDF <span aria-hidden="true">↗</span>
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            </div>
          </details>
        </div>
      )}
    </li>
  );
});
export default FindingItem;
