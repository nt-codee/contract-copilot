import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listReviews, listSamples, uploadReview } from "../api";
import Dashboard from "../components/Dashboard";
import { GuestButton, SignInButton } from "../components/Account";
import { ApiError, SIGN_IN_CODE } from "../api";
import { useSession } from "../useSession";
import HeroVisual from "../components/HeroVisual";
import Progress from "../components/Progress";
import { contractTypeLabel, languageLabel } from "../format";
import type { ReviewResult, ReviewSummary, Sample } from "../types";
import { pickCurated, randomSample } from "../samples";

const MAX_BYTES = 10 * 1024 * 1024;
const OK_TYPES = ["application/pdf", "image/jpeg", "image/png"];
const isAccepted = (f: File) => OK_TYPES.includes(f.type) || /\.(pdf|jpe?g|png)$/i.test(f.name);


export default function Home() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [needSignIn, setNeedSignIn] = useState(false);
  const session = useSession();

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
    if (session.loading) return;
    let live = true;
    listReviews()
      .then((r) => live && setReviews(r))
      .catch(() => live && setReviews([]));
    return () => {
      live = false;
    };
  }, [session.loading, session.workspace?.workspace.id]);

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
      if (e instanceof ApiError && e.code === SIGN_IN_CODE) {
        setNeedSignIn(true);
        return;
      }
      setError(e instanceof Error ? e.message : "The review did not finish. Try again.");
    }
  }

  function handleFile(file: File | undefined | null) {
    if (!file) return;
    if (!isAccepted(file)) {
      setError("That file is not a PDF, JPG or PNG. Save the contract as a PDF, or take a photo of each page.");
      return;
    }
    setNeedSignIn(false);
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

  // Five on show, one per contract type; the rest behind "See all samples".
  const curated = pickCurated(samples ?? []);
  const rest = (samples ?? []).filter((s) => !curated.includes(s));
  // "Try a sample": a random one each click, opened as a preview first, never straight to a finished review.
  function trySample() {
    const s = randomSample(samples ?? []);
    if (s) navigate(`/sample/${s.id}`);
  }

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
          <p className="display border-2 border-dashed border-white px-10 py-8 text-2xl text-white">Drop the PDF to review it</p>
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf,image/jpeg,.jpg,.jpeg,image/png,.png"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          handleFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {(session.user || session.guest) && (
        <>
          {error ? (
            <div className="mx-auto max-w-[1240px] px-5 pt-6 sm:px-8">
              <div ref={errorRef} tabIndex={-1} role="alert" className="rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
                {error}
              </div>
            </div>
          ) : null}
          <Dashboard
            reviews={reviews}
            onUpload={() => navigate("/review")}
            onSample={samples?.length ? trySample : null}
          />
        </>
      )}
      {!session.user && !session.guest && <section aria-labelledby="promise" className="mx-auto grid max-w-[1240px] gap-x-14 px-5 pb-16 pt-8 [grid-template-areas:'head'_'copy'_'preview'] sm:px-8 sm:pt-12 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:pb-20 lg:pt-14 lg:[grid-template-areas:'head_preview'_'copy_preview']">
        <div className="reveal self-end text-white [grid-area:head]" style={{ ["--d" as string]: "0s" }}>
          <h1 id="promise" className="display max-w-[15ch] text-2xl sm:text-3xl">
            Your vendor’s contract, marked up before you sign.
          </h1>
        </div>
        <div className="mt-12 self-center [grid-area:preview] lg:mt-0">
          <HeroVisual />
        </div>
        <div className="reveal text-white [grid-area:copy] lg:mt-0" style={{ ["--d" as string]: "0.12s" }}>
          <p className="mt-5 max-w-xl text-lg text-desk-text">
            In about a minute you see its own pages with every risky clause boxed, what it means in plain words, what to ask
            for, and a reply ready to send.
          </p>
          <p className="mt-3 max-w-xl text-desk-text">Works on NDAs, service, employment, tenancy and hire-purchase agreements.</p>
          <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
            <button type="button" className="btn-go !min-h-[52px] !px-6 text-base" disabled={!samples?.length} onClick={trySample}>
              Try a sample contract <span aria-hidden="true">→</span>
            </button>
            <button type="button" className="btn-desk !min-h-[52px] !px-6 text-base" onClick={() => inputRef.current?.click()} aria-describedby="upload-help">
              Upload a PDF
            </button>
          </div>
          <p id="upload-help" className="mt-4 text-sm text-desk-text">
            PDF, or a photo of each page (JPG or PNG), up to 10 MB and 40 pages, in English or Bahasa Malaysia. Or drop it anywhere on this page.
          </p>
          {needSignIn && (
            <div ref={errorRef} tabIndex={-1} role="alert" className="mt-5 rounded-[3px] bg-white px-4 py-4 text-ink">
              <p className="font-semibold">Sign in with Google to review your own contract.</p>
              <p className="mt-1 text-sm text-ink-soft">Your contracts stay in your own workspace. The samples work without signing in.</p>
              <div className="mt-3">
                <SignInButton />
              </div>
            </div>
          )}
          {error && (
            <div ref={errorRef} tabIndex={-1} role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
              {error}
            </div>
          )}
        </div>
      </section>}

      {!session.user && !session.guest && (
        <section aria-labelledby="demo-title" className="border-t border-white/5">
          <div className="mx-auto flex max-w-[1240px] flex-col gap-4 px-5 py-8 sm:px-8 md:flex-row md:items-center md:justify-between">
            <div className="text-white">
              <h2 id="demo-title" className="text-xl font-bold">See it as a team uses it</h2>
              <p className="mt-1 max-w-2xl text-desk-text">Look around a made-up company, Kopi Kita Sdn Bhd, with its contracts, deadlines, company rules and team. Nothing there can be changed.</p>
            </div>
            <div className="flex flex-wrap gap-3">
              <GuestButton className="!min-h-[52px] !px-5" />
              {session.available && <SignInButton className="!min-h-[52px] !px-5" />}
            </div>
          </div>
        </section>
      )}
      <section aria-labelledby="samples-title" className="border-t border-white/5 bg-desk-deep/75">
        <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-8">
          <h2 id="samples-title" className="text-xl font-bold text-white">
            Sample contracts, one of each kind
          </h2>
          <p className="mt-1 text-desk-text">All made up. Open one to read it first, then review it.</p>
          {samples === null ? (
            <p className="mt-6 text-desk-text" role="status">
              Loading samples…
            </p>
          ) : samples.length === 0 ? (
            <p className="mt-6 text-desk-text">Samples are not available right now. You can still upload your own PDF.</p>
          ) : (
            <>
              <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {curated.map((s) => (
                  <li key={s.id}>
                    <SampleCard s={s} />
                  </li>
                ))}
              </ul>
              {rest.length > 0 && (
                <details className="group mt-5">
                  <summary className="inline-flex min-h-[44px] cursor-pointer items-center font-semibold text-white underline underline-offset-4">
                    See all {samples.length} samples
                  </summary>
                  <ul className="mt-4 grid gap-x-8 sm:grid-cols-2 lg:grid-cols-3">
                    {rest.map((s) => (
                      <li key={s.id} className="border-b border-white/10">
                        <Link to={`/sample/${s.id}`} className="flex min-h-[44px] flex-col justify-center py-2 text-white hover:underline">
                          <span className="font-semibold leading-snug">{s.title}</span>
                          <span className="meta text-desk-text">
                            {contractTypeLabel(s.contract_type)}, {languageLabel(s.language)}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

/** A sample as a sheet of paper: its title, its type and language. Opens the preview, not a review. */
function SampleCard({ s }: { s: Sample }) {
  return (
    <Link
      to={`/sample/${s.id}`}
      className="flex h-full min-h-[148px] w-full flex-col justify-between gap-4 rounded-[3px] bg-paper p-4 text-left shadow-page lift"
    >
      <span className="font-doc text-base font-bold leading-snug text-ink">{s.title}</span>
      <span className="meta text-ink-mute">
        {contractTypeLabel(s.contract_type)}
        {s.language !== "en" ? `, ${languageLabel(s.language)}` : ""}
      </span>
    </Link>
  );
}
