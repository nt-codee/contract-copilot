import { forwardRef } from "react";
import { openPdfTab, pdfUrl } from "../api";
import { getSession } from "../session";
import type { Marked } from "./Reader";
import { FlagTab, RiskLabel } from "./Risk";
import Sources from "./Sources";
import { Verified } from "./Checks";
import type { Citation } from "../types";

interface Props {
  m: Marked;
  reviewId: string;
  open: boolean;
  /** True while the page text is still being searched for this quote. */
  locating: boolean;
  onPick: (id: string) => void;
  /** Narrow screens: say that tapping opens the page. */
  opensPage?: boolean;
  onEvidence?: (c: Citation) => void;
}

/** One finding in the rail: a header that selects it, and its detail when open. */
const FindingItem = forwardRef<HTMLButtonElement, Props>(function FindingItem(
  { m, reviewId, open, locating, onPick, opensPage, onEvidence },
  ref,
) {
  const { f, n } = m;
  const onPage = m.boxes.length > 0;
  const panelId = `finding-${f.id}`;
  return (
    <li className={`relative border-b border-paper-line last:border-b-0 ${open ? "bg-paper-soft" : ""}`}>
      {open && <span aria-hidden="true" className={`absolute inset-y-0 left-0 w-1 ${f.risk === "high" ? "bg-high" : f.risk === "medium" ? "bg-medium-box" : "bg-low"}`} />}
      <h3>
        <button
          ref={ref}
          type="button"
          onClick={() => onPick(f.id)}
          aria-expanded={opensPage ? undefined : open}
          aria-controls={opensPage ? undefined : panelId}
          className="flex w-full items-start gap-3 px-5 py-4 text-left hover:bg-paper-soft"
        >
          <FlagTab n={n} risk={f.risk} className="mt-0.5 !rounded-[3px]" />
          <span className="min-w-0 flex-1">
            <span className="block font-semibold leading-snug text-ink">{f.title}</span>
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              <RiskLabel risk={f.risk} />
              <span className="tab text-ink-mute">Page {f.page}</span>
              {f.policy_breach && <span className="tab rounded-[2px] bg-ink px-1.5 py-0.5 text-white">Breaks your rule</span>}
              {opensPage && <span className="tab text-ink-soft">See it on the page →</span>}
            </span>
          </span>
        </button>
      </h3>
      {open && (
        <div id={panelId} className="px-5 pb-5 pl-[4.6rem]">
          {f.policy_breach && (
            <div className="mb-3 flex gap-2.5 rounded-[3px] bg-ink px-3 py-2.5 text-white">
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" className="mt-0.5 shrink-0">
                <rect x="1.5" y="1.5" width="15" height="15" rx="1.5" fill="none" stroke="#FF8A7E" strokeWidth="2" />
                <path d="M5 5l8 8M13 5l-8 8" stroke="#FF8A7E" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <p className="text-[0.95rem] leading-snug">
                <span className="tab block text-[#FFB1A8]">Breaks your company rule</span>
                {f.policy_breach}
              </p>
            </div>
          )}
          <p className="text-[0.98rem] text-ink">{f.why_risky}</p>
          <dl className="mt-3 space-y-3 text-[0.95rem]">
            <div>
              <dt className="tab text-ink-mute">Usual terms</dt>
              <dd className="mt-0.5 text-ink-soft">{f.standard}</dd>
            </div>
            <div className="rounded-[3px] border-l-4 border-ink bg-white px-3 py-2.5">
              <dt className="tab text-ink">Ask for</dt>
              <dd className="mt-0.5 font-medium text-ink">{f.suggested_ask}</dd>
            </div>
          </dl>
          <Sources items={f.citations ?? []} idBase={panelId} onEvidence={onEvidence} />
          <Verified f={f} />
          {!onPage && !locating && (
            <figure className="mt-4">
              <blockquote className="border-l-2 border-ink-mute pl-3 font-doc text-[1rem] leading-relaxed text-ink">
                “{f.quote}”
              </blockquote>
              <figcaption className="tab mt-1.5 text-ink-mute">Clause on page {f.page}</figcaption>
            </figure>
          )}
          <a
            href={pdfUrl(reviewId, f.page)}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
              if (!getSession().user) return;
              e.preventDefault();
              void openPdfTab(pdfUrl(reviewId, f.page));
            }}
            className="mt-3 inline-flex min-h-[44px] items-center gap-1 text-[0.95rem] font-semibold text-desk underline underline-offset-4 hover:text-ink"
          >
            Open page {f.page} of the PDF <span aria-hidden="true">↗</span>
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        </div>
      )}
    </li>
  );
});
export default FindingItem;
