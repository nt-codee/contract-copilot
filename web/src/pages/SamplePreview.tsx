import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { listSamples, reviewSample, samplePdfUrl } from "../api";
import type { ReviewStage } from "../api";
import Reader from "../components/Reader";
import type { ReaderState } from "../components/Reader";
import Progress from "../components/Progress";
import { contractTypeLabel, formatDate, languageLabel } from "../format";
import { openPdf } from "../pdf";
import { useMedia } from "../useMedia";
import type { Sample } from "../types";
import { randomSample } from "../samples";

function earlierText(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  return d.toDateString() === today.toDateString() ? "Reviewed earlier today" : `Reviewed on ${formatDate(iso)}`;
}

/** A sample contract before it is reviewed: its own pages, what it is, and one button to review it. */
export default function SamplePreview() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const wide = useMedia("(min-width: 1024px)");
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [pages, setPages] = useState<number | null>(null);
  const [readerState, setReaderState] = useState<ReaderState>("loading");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<ReviewStage>();
  const [earlier, setEarlier] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listSamples()
      .then((s) => live && setSamples(s))
      .catch(() => live && setSamples([]));
    return () => {
      live = false;
    };
  }, []);

  const sample = samples?.find((s) => s.id === id) ?? null;
  const url = samplePdfUrl(id);

  useEffect(() => {
    if (sample) document.title = `${sample.title}, sample contract`;
  }, [sample]);

  useEffect(() => {
    let live = true;
    setPages(null);
    setReaderState("loading");
    openPdf(url)
      .then((d) => {
        if (live) setPages(d.numPages);
        void d.loadingTask.destroy();
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [url]);

  async function review() {
    if (!sample) return;
    setError(null);
    setStage(undefined);
    setEarlier(null);
    setBusy(true);
    window.scrollTo(0, 0);
    const start = Date.now();
    try {
      const r = await reviewSample(sample.id, false, setStage);
      // Prefer the server’s execution source: copying a cached sample creates a new timestamp.
      const old = r.sample_opening
        ? r.sample_opening !== "fresh"
        : Date.parse(r.created_at) < start;
      if (old) {
        // Keep a short opening transition and say plainly that this is an earlier result.
        setEarlier(earlierText(r.created_at));
        await new Promise((res) => setTimeout(res, 2400));
      }
      navigate(`/reviews/${r.id}`, { state: old ? { earlier: r.created_at, sampleId: sample.id } : undefined });
    } catch (e) {
      setBusy(false);
      setEarlier(null);
      setError(e instanceof Error ? e.message : "The review did not finish. Try again.");
    }
  }

  function another() {
    const next = randomSample((samples ?? []).filter((s) => s.id !== id));
    if (next) navigate(`/sample/${next.id}`);
  }

  if (busy && sample) return <Progress subject={sample.title} earlier={earlier} stage={stage} />;

  if (samples && !sample)
    return (
      <div className="mx-auto max-w-[760px] px-5 py-16 text-white sm:px-8">
        <h1 className="page-title">We could not find that sample</h1>
        <p className="page-lede">It may have been renamed.</p>
        <Link to="/" className="btn-go mt-6">
          See the samples
        </Link>
      </div>
    );

  const facts = sample
    ? [
        contractTypeLabel(sample.contract_type),
        languageLabel(sample.language),
        pages ? `${pages} ${pages === 1 ? "page" : "pages"}` : null,
      ].filter(Boolean)
    : [];

  const panel = (
    <div className="text-white">
      <p className="page-eyebrow">Sample contract</p>
      <h1 className="display mt-3 text-xl leading-tight sm:text-2xl">{sample?.title ?? "Loading the sample…"}</h1>
      {sample && <p className="meta mt-3 text-desk-text">{facts.join(", ")}</p>}
      <p className="mt-4 max-w-md text-desk-text">
        A made-up contract, so you can see a full review without sharing your own. Read it first if you like; the review marks
        each risky clause on these pages.
      </p>
      <div className="mt-6 flex flex-col gap-3 sm:flex-row lg:flex-col">
        <button type="button" className="btn-go !min-h-[52px] !px-6 text-base" disabled={!sample} onClick={() => void review()}>
          Review this contract
        </button>
        <button type="button" className="btn-desk !min-h-[52px] !px-5" disabled={!samples || samples.length < 2} onClick={another}>
          Choose another
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
          {error}
        </p>
      )}
      {readerState === "error" && <p className="mt-5 text-desk-text">The pages could not be shown here, but the review still works.</p>}
    </div>
  );

  const reader = (
    <Reader url={url} items={[]} selected={null} focus={null} onSelect={() => undefined} onLocated={() => undefined} onState={setReaderState} compact={!wide} />
  );

  if (wide)
    return (
      <div className="flex h-screen">
        <section aria-label="The contract" className="min-w-0 flex-1">
          {reader}
        </section>
        <aside aria-label="About this sample" className="w-[400px] shrink-0 overflow-y-auto border-l border-white/5 bg-desk-deep/60 px-8 py-10">
          {panel}
        </aside>
      </div>
    );

  return (
    <div className="pb-16">
      <div className="px-5 pb-6 pt-8 sm:px-8">{panel}</div>
      <section aria-label="The contract" className="px-3">
        {reader}
      </section>
    </div>
  );
}
