import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { Link } from "react-router-dom";
import { companyPdfUrl, getKnowledge, pdfUrl } from "../api";
import type { Citation, Finding, KnowledgeEntry } from "../types";
import Reader from "./Reader";
import type { Marked } from "./Reader";
import { FlagTab, RISK_BOX } from "./Risk";

const KIND: Record<string, string> = { law: "Law", case: "Court case", playbook: "Usual terms", company: "Your company rules" };

export interface EvidenceTarget {
  m: Marked;
  c: Citation;
}

/** Knowledge refs from older reviews lack the playbook: prefix the library uses. */
export function knowledgeId(c: Citation): string {
  return c.kind === "playbook" && !c.ref.startsWith("playbook:") ? `playbook:${c.ref}` : c.ref;
}

export const isCompanyPosition = (c: Citation) => c.kind === "playbook" && /^playbook:[^/]+\/[^/]+\/[^/]+$/.test(c.ref);

/** Side by side: the contract clause, and the source it was checked against. Same number, same colour. */
export default function Evidence({ target, reviewId, onClose }: { target: EvidenceTarget | null; reviewId: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const headRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (target && !d.open) {
      d.showModal();
      headRef.current?.focus();
    }
    if (!target && d.open) d.close();
  }, [target]);

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="ev-title" className="m-0 h-[100dvh] max-h-none w-full max-w-none bg-transparent p-0 sm:m-auto sm:h-[calc(100vh-48px)] sm:w-[min(1320px,calc(100vw-48px))]">
      {target && <Body target={target} reviewId={reviewId} onClose={onClose} headRef={headRef} />}
    </dialog>
  );
}

function Body({ target, reviewId, onClose, headRef }: { target: EvidenceTarget; reviewId: string; onClose: () => void; headRef: RefObject<HTMLHeadingElement> }) {
  const { m, c } = target;
  const box = RISK_BOX[m.f.risk];
  const company = c.kind === "company" && c.doc_id;
  const [entry, setEntry] = useState<KnowledgeEntry | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (c.kind === "company") return;
    getKnowledge(knowledgeId(c)).then(setEntry).catch(() => setFailed(true));
  }, [c]);

  // The policy page, marked like a contract page, with the same number and colour.
  const [found, setFound] = useState<[number, number, number, number][]>([]);
  const source: Marked | null = company
    ? { n: m.n, boxes: c.boxes?.length ? c.boxes : found, f: { ...(m.f as Finding), id: "src", page: c.page ?? 1, quote: c.excerpt, title: c.title } }
    : null;
  const noop = () => undefined;
  const [mine, setMine] = useState<[number, number, number, number][]>([]);
  const contract: Marked = m.boxes.length ? m : { ...m, boxes: mine };
  const official = c.source_url ?? entry?.source_url ?? c.url ?? entry?.url;

  return (
    <div className="flex h-full flex-col bg-desk sm:rounded-[3px] sm:shadow-lift">
      <header className="flex shrink-0 items-center justify-between gap-4 bg-ink px-5 py-3 text-white">
        <h2 id="ev-title" ref={headRef} tabIndex={-1} className="flex min-w-0 items-center gap-3">
          <FlagTab n={m.n} risk={m.f.risk} className="!rounded-[3px]" />
          <span className="tab truncate !text-[0.95rem]">Evidence · {m.f.title}</span>
        </h2>
        <button type="button" onClick={onClose} className="btn -mr-3 min-w-[44px] !px-3 text-white hover:bg-white/10">
          Close
        </button>
      </header>
      <div className="grid min-h-0 flex-1 grid-rows-2 gap-px bg-ink lg:grid-cols-2 lg:grid-rows-1">
        <section aria-label={`The contract, page ${m.f.page}`} className="flex min-h-0 flex-col bg-desk">
          <p className="tab shrink-0 px-5 pt-3 text-desk-text">The contract · page {m.f.page}</p>
          <div className="min-h-0 flex-1">
            <Reader url={pdfUrl(reviewId)} items={[contract]} selected={m.f.id} focus={{ id: m.f.id, k: 0 }} onSelect={noop} onLocated={(x) => setMine(x[m.f.id] ?? [])} onState={noop} />
          </div>
        </section>
        <section aria-label={`The source: ${c.title}`} className="flex min-h-0 flex-col bg-desk">
          <p className="tab shrink-0 px-5 pt-3 text-desk-text">
            {KIND[c.kind]}
            {company && c.page ? ` · page ${c.page}` : ""}
          </p>
          {source ? (
            <div className="min-h-0 flex-1">
              <Reader url={companyPdfUrl(c.doc_id!)} items={[source]} selected="src" focus={{ id: "src", k: 0 }} onSelect={noop} onLocated={(x) => setFound(x.src ?? [])} onState={noop} />
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <article className="sheet relative px-6 py-6 sm:px-9">
                <span className="absolute -left-1 top-6 h-[calc(100%-3rem)] w-1 rounded-r-sm" style={{ background: box.edge }} aria-hidden="true" />
                <p className="tab text-ink-mute">{c.jurisdiction === "my" ? "Malaysia" : c.jurisdiction === "sg" ? "Singapore" : KIND[c.kind]}</p>
                <h3 className="mt-1 font-doc text-[1.3rem] font-bold leading-snug text-ink">{entry?.title ?? c.title}</h3>
                {entry?.citation && <p className="mt-1 font-doc text-ink-soft">{entry.citation}</p>}
                <blockquote className="mt-4 font-doc text-[1.06rem] leading-[1.65] text-ink">
                  <span className="hl" style={{ ["--hl" as string]: box.fill, borderBottom: `2px solid ${box.edge}` }}>
                    {c.excerpt.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/(^|\s)[-*]\s+/g, "$1").replace(/\s+/g, " ").trim()}
                  </span>
                </blockquote>
                {entry?.plain && (
                  <p className="mt-5 border-l-4 border-ink bg-paper-soft px-3 py-2 text-[0.95rem] text-ink">
                    <span className="tab block">In plain words</span>
                    {entry.plain}
                  </p>
                )}
                {failed && <p className="mt-4 text-[0.92rem] text-ink-soft">The full entry could not be loaded. The quoted text above is what the review relied on.</p>}
                <div className="mt-5 flex flex-wrap gap-3">
                  {official && (c.kind === "law" || c.kind === "case") && (
                    <a href={official} target="_blank" rel="noopener noreferrer" className="btn-ink">
                      Open the exact provision <span aria-hidden="true">↗</span>
                      <span className="sr-only">(official source, opens in a new tab)</span>
                    </a>
                  )}
                  <Link to={`/knowledge/${encodeURIComponent(knowledgeId(c))}`} className="btn-line">
                    Read the whole entry
                  </Link>
                </div>
              </article>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
