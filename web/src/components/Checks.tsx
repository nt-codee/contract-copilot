import { useEffect, useState } from "react";
import { getReview, reviewHistory } from "../api";
import { aiVersion, formatDate } from "../format";
import type { Finding, RemovedClaim, ReviewResult, ReviewSummary, Verification } from "../types";

const MATCH: Record<Verification["quote_match"], string> = {
  exact: "exact match",
  normalised: "same words, different spacing or quotes",
  fuzzy: "close match",
};

/** One line under a finding: how its quote and sources were checked. */
export function Verified({ f }: { f: Finding }) {
  const v = f.verification;
  if (!v) return null;
  const checked = v.citations_checked ?? (f.citations ?? []).length;
  return (
    <details className="group mt-3 rounded-[3px] border border-paper-line bg-white">
      <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-3 py-1.5 text-[0.9rem] text-ink [&::-webkit-details-marker]:hidden">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" className="shrink-0 text-low">
          <path d="M2 7.5 5.5 11 12 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>
          <span className="font-semibold">Verified</span> · quote found on page {f.page}
          {v.quote_match !== "exact" ? ` (${v.quote_match === "fuzzy" ? "close match" : "exact words"})` : ""} · {checked} {checked === 1 ? "source" : "sources"} checked
        </span>
        <span aria-hidden="true" className="ml-auto text-ink-mute group-open:rotate-180">▾</span>
      </summary>
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-1 border-t border-paper-line px-3 py-2.5 text-[0.88rem]">
        <dt className="text-ink-mute">Quote</dt>
        <dd className="text-ink">
          {MATCH[v.quote_match]}
          {v.score != null && v.quote_match === "fuzzy" ? `, ${Math.round(v.score)}% the same` : ""}
        </dd>
        {v.page_corrected_from != null && (
          <>
            <dt className="text-ink-mute">Page</dt>
            <dd className="text-ink">Corrected from page {v.page_corrected_from} to page {f.page}, where the words are</dd>
          </>
        )}
        <dt className="text-ink-mute">Sources</dt>
        <dd className="text-ink">{checked} looked up in the knowledge library and confirmed to exist</dd>
        {v.kb_version && (
          <>
            <dt className="text-ink-mute">Library version</dt>
            <dd className="font-mono text-[0.85rem] text-ink">{v.kb_version}</dd>
          </>
        )}
      </dl>
    </details>
  );
}

const WHY: Record<RemovedClaim["reason"], string> = {
  quote_not_found: "its words are not in the contract",
  source_not_retrieved: "it cited something that is not in our library",
  no_company_citation: "it claimed a company rule it could not point to",
  wrong_jurisdiction: "it cited law from a different country than this contract’s",
};
const KIND: Record<RemovedClaim["kind"], string> = { finding: "Point", citation: "Source", policy_breach: "Rule breach" };

/** Claims the AI raised that verification removed. A trust feature, stated calmly. */
export function LeftOut({ review }: { review: ReviewResult }) {
  const list = review.removed ?? [];
  const n = list.length || (review.metrics.dropped_unverified ?? 0) + (review.metrics.dropped_unverified_citations ?? 0);
  if (!n) return null;
  return (
    <section aria-labelledby="leftout-title" className="border-t border-paper-line px-5 py-5">
      <h2 id="leftout-title" className="tab text-ink-mute">
        Left out
      </h2>
      <p className="mt-1.5 text-[0.95rem] text-ink">
        We removed {n} {n === 1 ? "point" : "points"} the AI raised because we could not confirm {n === 1 ? "it" : "them"}.
      </p>
      {list.length > 0 && (
        <ul className="mt-3 space-y-3">
          {list.map((c, i) => (
            <li key={i} className="border-l-2 border-dashed border-ink-mute pl-3 text-[0.92rem]">
              <span className="tab block text-ink-mute">
                {KIND[c.kind]}
                {c.page ? ` · page ${c.page}` : ""}
              </span>
              <span className="block text-ink-soft line-through decoration-ink-mute/60">{c.finding_title && c.kind !== "finding" ? `${c.text} (on “${c.finding_title}”)` : c.text}</span>
              <span className="block text-ink">Removed because {WHY[c.reason]}.</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** "Reviewed with" plus the earlier runs of this contract, compared. */
export function History({ review }: { review: ReviewResult }) {
  const [runs, setRuns] = useState<ReviewSummary[] | null>(null);
  const [prev, setPrev] = useState<ReviewResult | null>(null);

  useEffect(() => {
    let live = true;
    reviewHistory(review.id)
      .then((r) => live && setRuns(r))
      .catch(() => live && setRuns([]));
    if (review.previous_review_id)
      getReview(review.previous_review_id)
        .then((r) => live && setPrev(r))
        .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [review.id, review.previous_review_id]);

  const p = review.provenance;
  const key = (f: Pick<Finding, "clause_type" | "title">) => `${f.clause_type}|${f.title.toLowerCase().slice(0, 40)}`;
  const before = new Set((prev?.findings ?? []).map(key));
  const now = new Set(review.findings.map(key));
  const added = prev ? review.findings.filter((f) => !before.has(key(f))) : [];
  const gone = prev ? prev.findings.filter((f) => !now.has(key(f))) : [];
  const many = (runs?.length ?? 0) > 1;

  if (!p && !many) return null;
  return (
    <section aria-labelledby="history-title" className="border-t border-paper-line px-5 py-5">
      <h2 id="history-title" className="tab text-ink-mute">
        History
      </h2>
      {p && (
        <p className="mt-1.5 text-[0.92rem] text-ink">
          Reviewed with AI version <span className="font-semibold" title={p.model}>{aiVersion(p.model)}</span> · knowledge library <span className="font-mono text-[0.85rem]">{p.kb_version}</span> · instructions {p.prompt_version} · {formatDate(review.created_at)}
        </p>
      )}
      {many && (
        <>
          <ol className="mt-3 space-y-1.5 text-[0.92rem]">
            {runs!.map((r, i) => (
              <li key={r.id} className="flex items-baseline gap-2">
                <span className={`tab shrink-0 rounded-[2px] px-1.5 py-0.5 !text-[0.7rem] ${r.id === review.id ? "bg-ink text-white" : "bg-paper-soft text-ink"}`}>Run {runs!.length - i}</span>
                <span className="text-ink-soft">
                  {formatDate(r.created_at)} · {Object.values(r.finding_counts).reduce((a, b) => a + (b ?? 0), 0)} findings
                  {r.id === review.id && " · this one"}
                </span>
              </li>
            ))}
          </ol>
          {prev && (added.length > 0 || gone.length > 0) ? (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <p className="tab text-low">New in this run</p>
                <ul className="mt-1 space-y-1 text-[0.9rem] text-ink">
                  {added.length ? added.map((f) => <li key={f.id}>+ {f.title}</li>) : <li className="text-ink-soft">None</li>}
                </ul>
              </div>
              <div>
                <p className="tab text-ink-mute">No longer flagged</p>
                <ul className="mt-1 space-y-1 text-[0.9rem] text-ink-soft">
                  {gone.length ? gone.map((f) => <li key={f.id}>− {f.title}</li>) : <li>None</li>}
                </ul>
              </div>
            </div>
          ) : prev ? (
            <p className="mt-2 text-[0.92rem] text-ink-soft">Same findings as the run before.</p>
          ) : null}
        </>
      )}
    </section>
  );
}
