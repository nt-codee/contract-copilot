import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { listSamples, reviewSample, uploadReview } from "../api";
import HeroPreview from "../components/HeroPreview";
import Progress from "../components/Progress";
import { contractTypeLabel, languageLabel } from "../format";
import type { ReviewResult, Sample } from "../types";

const MAX_BYTES = 10 * 1024 * 1024;
const FEATURED = "msa-02-autorenew";

export default function Home() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    let live = true;
    listSamples()
      .then((s) => live && setSamples(s))
      .catch(() => live && setSamples([]));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  async function run(label: string, job: () => Promise<ReviewResult>) {
    setError(null);
    setBusyLabel(label);
    window.scrollTo(0, 0);
    try {
      const result = await job();
      navigate(`/reviews/${result.id}`);
    } catch (e) {
      setBusyLabel(null);
      setError(e instanceof Error ? e.message : "The review did not finish. Try again.");
    }
  }

  function handleFile(file: File | undefined | null) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("That file is not a PDF. Save or export the contract as a PDF and try again.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("That file is larger than 10 MB. Try a smaller PDF, or only the pages that matter.");
      return;
    }
    void run(file.name, () => uploadReview(file));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    handleFile(e.dataTransfer.files?.[0]);
  }

  if (busyLabel) return <Progress subject={busyLabel} />;

  const featured = samples?.find((s) => s.id === FEATURED) ?? samples?.[0];

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={onDrop}
      className="relative"
    >
      {dragging && (
        <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-30 grid place-items-center bg-desk-deep/90">
          <p className="display border-2 border-dashed border-white px-10 py-8 text-[2rem] text-white">Drop the PDF to review it</p>
        </div>
      )}

      <section aria-labelledby="promise" className="mx-auto grid max-w-[1240px] gap-x-16 px-5 pb-16 pt-8 [grid-template-areas:'head'_'copy'_'preview'] sm:px-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:content-center lg:pb-24 lg:pt-16 lg:[grid-template-areas:'head_preview'_'copy_preview']">
        <div className="self-end text-white [grid-area:head]">
          <p className="tab text-desk-text">For founders who sign without a lawyer</p>
          <h1 id="promise" className="display mt-3 text-[2.25rem] sm:mt-4 sm:text-[3.5rem]">
            Your vendor’s contract, marked up before you sign.
          </h1>
        </div>
        <div className="mt-12 self-center [grid-area:preview] lg:mt-0">
          <HeroPreview />
        </div>
        <div className="text-white [grid-area:copy] lg:mt-0">
          <p className="mt-5 max-w-xl text-[1.1rem] text-desk-text sm:text-[1.2rem]">
            Upload an NDA or service agreement. In about a minute you see its pages with every risky clause boxed, what it
            means in plain words, what to ask for, and a reply ready to send.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
            <button
              type="button"
              className="btn-go btn-flag !min-h-[52px] !px-6 text-[1.05rem]"
              disabled={!featured}
              onClick={() => featured && void run(featured.title, () => reviewSample(featured.id))}
            >
              Try a sample contract <span aria-hidden="true">→</span>
            </button>
            <button type="button" className="btn-desk !min-h-[52px] !px-6 text-[1.05rem]" onClick={() => inputRef.current?.click()} aria-describedby="upload-help">
              Upload a PDF
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => {
                handleFile(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
          </div>
          <p id="upload-help" className="mt-4 text-[0.95rem] text-desk-text">
            PDF up to 10 MB and 40 pages, in English or Bahasa Malaysia. Or drop it anywhere on this page.
          </p>
          {error && (
            <div ref={errorRef} tabIndex={-1} role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
              {error}
            </div>
          )}
        </div>
      </section>

      <section aria-labelledby="samples-title" className="bg-desk-deep">
        <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-8">
          <h2 id="samples-title" className="text-[1.35rem] font-bold text-white">
            Or open one of our sample contracts
          </h2>
          <p className="mt-1 text-desk-text">All made up, so you can see a full review without sharing your own.</p>
          {samples === null ? (
            <p className="mt-6 text-desk-text" role="status">
              Loading samples…
            </p>
          ) : samples.length === 0 ? (
            <p className="mt-6 text-desk-text">Samples are not available right now. You can still upload your own PDF.</p>
          ) : (
            <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {samples.map((s) => (
                <li key={s.id} className="pr-3">
                  <button
                    type="button"
                    onClick={() => void run(s.title, () => reviewSample(s.id))}
                    className="group relative flex h-full min-h-[132px] w-full flex-col items-start justify-between gap-3 rounded-[3px] bg-paper p-4 pr-5 text-left shadow-page transition-transform hover:-translate-y-0.5"
                  >
                    <span className="font-doc text-[1.02rem] font-bold leading-snug text-ink">{s.title}</span>
                    <span aria-hidden="true" className="block w-full space-y-1.5">
                      <span className="block h-[5px] w-full rounded-sm bg-paper-line" />
                      <span className="block h-[5px] w-4/5 rounded-sm bg-paper-line" />
                    </span>
                    <span className="tab flex w-full items-center justify-between text-ink-mute">
                      {contractTypeLabel(s.contract_type)}
                      <span className="text-ink">
                        Review it <span aria-hidden="true" className="inline-block transition-transform group-hover:translate-x-0.5">→</span>
                      </span>
                    </span>
                    {/* Edge tab: the contract's language, like a tab on a file copy. */}
                    <span className="tab absolute -right-3 top-4 rounded-r-[3px] bg-desk px-1.5 py-1 !text-[0.72rem] text-white shadow-lift">
                      {s.language === "ms" ? "BM" : s.language.toUpperCase()}
                      <span className="sr-only"> · {languageLabel(s.language)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
