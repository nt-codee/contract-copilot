import { useEffect, useRef, useState } from "react";
import type { CSSProperties, DragEvent } from "react";
import { Link } from "react-router-dom";
import { companyPdfUrl, deleteCompanyDoc, getReview, listCompanyDocs, listReviews, uploadCompanyDoc } from "../api";
import { FlagTab } from "../components/Risk";
import Reader from "../components/Reader";
import type { Marked, ReaderState } from "../components/Reader";
import { deriveRules, docTitle } from "../rules";
import type { PolicyRule } from "../rules";
import { breakable, formatDate } from "../format";
import type { CompanyDoc, ReviewResult } from "../types";
import { readOnlyReason } from "../session";
import { useSession } from "../useSession";

const MAX_BYTES = 10 * 1024 * 1024;
const SAMPLE = "/samples/kopi-kita-contracting-policy.pdf";

export default function Company() {
  const inputRef = useRef<HTMLInputElement>(null);
  const msgRef = useRef<HTMLDivElement>(null);
  const [docs, setDocs] = useState<CompanyDoc[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [reviews, setReviews] = useState<ReviewResult[] | null>(null);
  // The read-only demo can browse the rules but not change them; the API would refuse with 403.
  const readOnly = readOnlyReason(useSession());

  // Full reviews carry the citations and breaches the rules are derived from.
  useEffect(() => {
    let live = true;
    listReviews()
      .then((list) => Promise.all(list.slice(0, 24).map((x) => getReview(x.id).catch(() => null))))
      .then((rs) => {
        if (!live) return;
        const seen = new Set<string>();
        setReviews(rs.filter((x): x is ReviewResult => !!x && !seen.has(x.id) && !!seen.add(x.id)));
      })
      .catch(() => live && setReviews([]));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    listCompanyDocs()
      .then((d) => live && setDocs(d))
      .catch((e: Error) => live && setLoadError(`Your documents could not be loaded. ${e.message}`));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (error) msgRef.current?.focus();
  }, [error]);

  async function upload(file: File | undefined | null) {
    if (!file) return;
    setNotice(null);
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("That file is not a PDF. Save or export the policy as a PDF and try again.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("That file is larger than 10 MB. Try a smaller PDF.");
      return;
    }
    setError(null);
    setUploading(file.name);
    try {
      const doc = await uploadCompanyDoc(file);
      setDocs((d) => [doc, ...(d ?? [])]);
      setNotice(`Added ${docTitle(doc)}. New reviews will check against it.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The document was not added. Try again.");
    } finally {
      setUploading(null);
    }
  }

  async function remove(doc: CompanyDoc) {
    setRemoving(doc.id);
    setError(null);
    try {
      await deleteCompanyDoc(doc.id);
      setDocs((d) => (d ?? []).filter((x) => x.id !== doc.id));
      setNotice(`Removed ${docTitle(doc)}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The document was not removed. Try again.");
    } finally {
      setRemoving(null);
      setConfirm(null);
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    void upload(e.dataTransfer.files?.[0]);
  }

  const empty = docs !== null && docs.length === 0 && !uploading;

  return (
    <div className="mx-auto max-w-[1040px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="page-eyebrow">Your company rules</p>
      <h1 className="display mt-3 text-2xl text-white sm:text-2xl">Your company rules</h1>
      <p className="mt-4 max-w-2xl text-lg text-desk-text">
        Upload your contracting policy once. Every review after that flags the clauses that break it, and quotes the rule.
      </p>

      {readOnly ? (
        <p className="mt-7 rounded-[3px] border border-desk-line px-4 py-3 text-desk-text">{readOnly}</p>
      ) : (
      <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:items-center">
        <button type="button" className="btn-go btn-flag !min-h-[52px] !px-6" onClick={() => inputRef.current?.click()} disabled={!!uploading} aria-describedby="rules-help">
          {uploading ? "Adding your document…" : "Upload a policy PDF"}
        </button>
        <p id="rules-help" className="text-sm text-desk-text">
          PDF up to 10 MB and 40 pages. Only your reviews use it.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            void upload(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>
      )}

      <div aria-live="polite" className="mt-5">
        {error && (
          <div ref={msgRef} tabIndex={-1} role="alert" className="rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
            {error}
          </div>
        )}
        {notice && !error && <p className="rounded-[3px] bg-low-tint px-4 py-3 font-medium text-low">{notice}</p>}
      </div>

      <section
        aria-labelledby="docs-title"
        className="mt-6"
        onDragOver={(e) => {
          e.preventDefault();
          if (!readOnly) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => (readOnly ? e.preventDefault() : onDrop(e))}
      >
        <h2 id="docs-title" className="text-xl font-bold text-white">
          Rules your reviews check
        </h2>

        {loadError ? (
          <p role="alert" className="mt-4 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
            {loadError}
          </p>
        ) : docs === null ? (
          <p className="mt-4 text-desk-text" role="status">
            Loading your documents…
          </p>
        ) : empty ? (
          <div className={`sheet mt-4 grid gap-6 p-6 sm:grid-cols-[1fr_auto] sm:items-center sm:p-8 ${dragging ? "outline-dashed outline-2 outline-offset-4 outline-white" : ""}`}>
            <div>
              <p className="text-lg font-semibold text-ink">No rules yet. Add the policy you want contracts checked against.</p>
              <p className="mt-2 text-ink-soft">
                For example: never accept uncapped liability, renewals of 12 months or less, notice by email allowed. Drop the PDF
                here or use the button above.
              </p>
              <p className="mt-4 text-sm text-ink-soft">
                No policy to hand?{" "}
                <a href={SAMPLE} download className="font-semibold text-desk underline underline-offset-4 hover:text-ink">
                  Download the sample policy for Kopi Kita Sdn Bhd
                </a>{" "}
                (PDF), then upload it here.
              </p>
            </div>
            <svg width="96" height="120" viewBox="0 0 96 120" aria-hidden="true" className="hidden sm:block">
              <rect x="4" y="4" width="76" height="108" rx="3" fill="#F3F5F9" stroke="#DCE2EC" strokeWidth="2" />
              {[22, 34, 46, 64, 76, 88].map((y, i) => (
                <rect key={y} x="16" y={y} width={i % 3 === 2 ? 34 : 52} height="5" rx="1" fill="#DCE2EC" />
              ))}
              <path d="M80 30h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H80z" fill="#121A2B" />
            </svg>
          </div>
        ) : (
          <ul className="sheet mt-4 divide-y divide-paper-line">
            {uploading && (
              <li className="flex items-center gap-4 px-5 py-4" role="status">
                <span className="h-2 w-2 rounded-full bg-desk" aria-hidden="true" />
                <span className="text-ink-soft">Reading {uploading}…</span>
              </li>
            )}
            {docs!.map((d) => (
              <li key={d.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                <span className="flex min-w-0 items-center gap-4">
                <svg width="40" height="50" viewBox="0 0 40 50" aria-hidden="true" className="shrink-0">
                  <rect x="1" y="1" width="30" height="46" rx="2" fill="#F3F5F9" stroke="#DCE2EC" strokeWidth="2" />
                  <rect x="6" y="10" width="18" height="3" rx="1" fill="#DCE2EC" />
                  <rect x="6" y="17" width="18" height="5" fill="rgba(217,58,43,.22)" />
                  <rect x="6" y="27" width="14" height="3" rx="1" fill="#DCE2EC" />
                  <path d="M31 12h7a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-7z" fill="#121A2B" />
                </svg>
                <span className="min-w-0">
                  <span className="block break-words font-semibold text-ink">{docTitle(d)}</span>
                  <span className="meta mt-1 block text-ink-mute">
                    {d.title ? `${d.filename}, ` : ""}
                    {d.pages} {d.pages === 1 ? "page" : "pages"}, added {formatDate(d.created_at)}, checked in every new review
                  </span>
                </span>
                </span>
                {readOnly ? null : confirm === d.id ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-ink-soft">Stop checking against it?</span>
                    <button type="button" className="btn !min-h-[44px] bg-high px-4 text-white hover:bg-[#8f1e18]" onClick={() => void remove(d)} disabled={removing === d.id}>
                      {removing === d.id ? "Removing…" : "Remove"}
                    </button>
                    <button type="button" className="btn-line !min-h-[44px] px-4" onClick={() => setConfirm(null)}>
                      Keep
                    </button>
                  </span>
                ) : (
                  <button type="button" className="btn-line !min-h-[44px] self-start px-4 sm:self-auto" onClick={() => setConfirm(d.id)} aria-label={`Remove ${docTitle(d)}`}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {docs && docs.length > 0 && <PolicyDocs docs={docs} reviews={reviews} />}
    </div>
  );
}

function PolicyDocs({ docs, reviews }: { docs: CompanyDoc[]; reviews: ReviewResult[] | null }) {
  const rules = reviews ? deriveRules(reviews, docs) : null;
  const broken = rules?.filter((r) => r.breaches.length > 0) ?? [];
  const contracts = new Set(broken.flatMap((r) => r.breaches.map((b) => b.review.id)));
  return (
    <section aria-labelledby="policy-title" className="mt-12">
      <h2 id="policy-title" className="text-xl font-bold text-white">
        Your rules, and the contracts that break them
      </h2>
      <p className="mt-1 text-desk-text">
        {rules === null
          ? "Reading your reviews…"
          : rules.length === 0
            ? "No review has quoted these rules yet. Review a contract and every rule it breaks will show here."
            : `${rules.length} ${rules.length === 1 ? "rule has" : "rules have"} come up so far, ${contracts.size} ${contracts.size === 1 ? "contract breaks" : "contracts break"} at least one.`}
      </p>
      {rules && rules.length > 0 &&
        docs.map((d) => {
          const mine = rules.filter((r) => !r.docId || r.docId === d.id);
          if (mine.length === 0) return null;
          return (
            <div key={d.id} className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <PolicyPdf doc={d} reviews={reviews ?? []} />
            <article className="sheet relative px-5 py-6 pr-14 sm:px-10 sm:py-9 sm:pr-20" aria-label={docTitle(d)}>
              <p className="meta text-ink-mute">
                {d.filename}, {d.pages} {d.pages === 1 ? "page" : "pages"}
              </p>
              <h3 className="mt-1 font-doc text-xl font-bold leading-snug text-ink">{docTitle(d)}</h3>
              <ol className="mt-5 space-y-6">
                {mine.map((r) => (
                  <Rule key={r.key} r={r} />
                ))}
              </ol>
            </article>
            </div>
          );
        })}
    </section>
  );
}

function Rule({ r }: { r: PolicyRule }) {
  const n = r.breaches.length;
  return (
    <li className="relative">
      <p className="font-doc text-base leading-[1.6] text-ink">
        <span className="font-bold">{r.number ? `${r.number} ` : ""}</span>
        {r.excerpt ? (
          <span
            className={n ? "hl" : undefined}
            style={n ? ({ ["--hl" as string]: "rgba(217,58,43,.16)", borderBottom: "2px solid #D93A2B" } as CSSProperties) : undefined}
          >
            {r.excerpt}
          </span>
        ) : (
          <span className="text-ink-soft">Rule {r.number} of your policy</span>
        )}
      </p>
      <p className="meta mt-1 text-ink-mute">
        {r.number ? `Rule ${r.number}` : r.title}
        {r.page ? `, page ${r.page}` : ""}
      </p>
      {/* Margin tab: how many contracts break this rule. */}
      <span
        className={`tab absolute -right-12 top-0 inline-flex h-7 min-w-[2.6rem] items-center justify-center rounded-r-[3px] px-2 text-xs shadow-lift sm:-right-[4.6rem] ${
          n ? "bg-high text-white" : "bg-paper-soft text-ink-mute"
        }`}
        aria-label={n ? `${n} ${n === 1 ? "contract breaks" : "contracts break"} this rule` : "No contract breaks this rule"}
      >
        {n ? `✕ ${n}` : "✓"}
      </span>
      {n > 0 && (
        <ul className="mt-2 space-y-1.5 border-l-2 border-high pl-3">
          {r.breaches.map((b) => (
            <li key={b.review.id + b.finding.id}>
              <Link
                to={`/reviews/${b.review.id}?finding=${encodeURIComponent(b.finding.id)}`}
                className="group flex min-h-[44px] items-start gap-2.5 py-1 text-sm leading-snug"
              >
                <FlagTab n={b.n} risk={b.finding.risk} className="!h-6 !min-w-[2rem] shrink-0 !rounded-[3px]" />
                <span className="min-w-0">
                  <span className="block break-words font-semibold text-ink group-hover:underline">{breakable(b.review.filename)}</span>
                  <span className="block text-ink-soft">{b.finding.policy_breach}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** The policy PDF itself, in the same reader as contracts, with each quoted rule boxed. */
function PolicyPdf({ doc, reviews }: { doc: CompanyDoc; reviews: ReviewResult[] }) {
  const [state, setState] = useState<ReaderState>("loading");
  const [found, setFound] = useState<Record<string, [number, number, number, number][]>>({});
  const marks: Marked[] = [];
  const seen = new Set<string>();
  for (const r of reviews)
    for (const f of r.findings)
      for (const c of f.citations ?? []) {
        if (c.kind !== "company" || (c.doc_id && c.doc_id !== doc.id) || !c.page) continue;
        const key = `${c.page}|${c.excerpt.slice(0, 40)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const mid = `pol-${marks.length}`;
        marks.push({ n: marks.length + 1, boxes: c.boxes?.length ? c.boxes : found[mid] ?? [], f: { ...f, id: mid, page: c.page, quote: c.excerpt, title: c.title, risk: f.policy_breach ? "high" : "medium" } });
      }
  if (state === "error") return null;
  const noop = () => undefined;
  return (
    <section aria-label={`${docTitle(doc)}, the PDF`} className="hidden h-[80vh] min-h-[560px] overflow-hidden rounded-[3px] bg-desk-deep xl:block">
      <Reader url={companyPdfUrl(doc.id)} items={marks} selected={null} focus={null} onSelect={noop} onLocated={(x) => setFound((cur) => ({ ...cur, ...x }))} onState={setState} />
    </section>
  );
}
