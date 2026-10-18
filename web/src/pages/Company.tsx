import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { deleteCompanyDoc, listCompanyDocs, uploadCompanyDoc } from "../api";
import { formatDate } from "../format";
import type { CompanyDoc } from "../types";

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
      setNotice(`Added ${doc.title || doc.filename}. New reviews will check against it.`);
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
      setNotice(`Removed ${doc.title || doc.filename}.`);
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
      <p className="tab text-desk-text">Your company rules</p>
      <h1 className="display mt-3 text-[2.1rem] text-white sm:text-[2.9rem]">Check every contract against your own rules</h1>
      <p className="mt-4 max-w-2xl text-[1.1rem] text-desk-text">
        Upload your contracting policy once. Every review after that flags the clauses that break it, and quotes the rule.
      </p>

      <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:items-center">
        <button type="button" className="btn-go btn-flag !min-h-[52px] !px-6" onClick={() => inputRef.current?.click()} disabled={!!uploading} aria-describedby="rules-help">
          {uploading ? "Adding your document…" : "Upload a policy PDF"}
        </button>
        <p id="rules-help" className="text-[0.95rem] text-desk-text">
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
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <h2 id="docs-title" className="text-[1.35rem] font-bold text-white">
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
              <p className="text-[1.15rem] font-semibold text-ink">No rules yet. Add the policy you want contracts checked against.</p>
              <p className="mt-2 text-ink-soft">
                For example: never accept uncapped liability, renewals of 12 months or less, notice by email allowed. Drop the PDF
                here or use the button above.
              </p>
              <p className="mt-4 text-[0.95rem] text-ink-soft">
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
                <span className="h-2 w-2 animate-pulse rounded-full bg-desk" aria-hidden="true" />
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
                  <span className="block break-all font-semibold text-ink">{d.title || d.filename}</span>
                  <span className="tab mt-1 block text-ink-mute">
                    {d.title ? `${d.filename} · ` : ""}
                    {d.pages} {d.pages === 1 ? "page" : "pages"} · added {formatDate(d.created_at)} · checked in every new review
                  </span>
                </span>
                </span>
                {confirm === d.id ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-[0.95rem] text-ink-soft">Stop checking against it?</span>
                    <button type="button" className="btn !min-h-[44px] bg-high px-4 text-white hover:bg-[#8f1e18]" onClick={() => void remove(d)} disabled={removing === d.id}>
                      {removing === d.id ? "Removing…" : "Remove"}
                    </button>
                    <button type="button" className="btn-line !min-h-[44px] px-4" onClick={() => setConfirm(null)}>
                      Keep
                    </button>
                  </span>
                ) : (
                  <button type="button" className="btn-line !min-h-[44px] self-start px-4 sm:self-auto" onClick={() => setConfirm(d.id)} aria-label={`Remove ${d.title || d.filename}`}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
