import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, SIGN_IN_CODE, listCompanyDocs, uploadReview } from "../api";
import type { ReviewOptions, ReviewStage } from "../api";
import Progress from "../components/Progress";
import { reducedMotion } from "../motion";
import { SignInButton } from "../components/Account";
import { RiskLabel } from "../components/Risk";
import { breakable, contractTypeLabel } from "../format";
import type { CompanyDoc, ReviewResult } from "../types";
import { docTitle } from "../rules";
import { useSession } from "../useSession";
import { readOnlyReason } from "../session";

const MAX_BYTES = 10 * 1024 * 1024;
const OK_TYPES = ["application/pdf", "image/jpeg", "image/png"];
const isAccepted = (f: File) => OK_TYPES.includes(f.type) || /\.(pdf|jpe?g|png)$/i.test(f.name);

const LAW: Record<string, string> = { my: "Malaysian law", sg: "Singapore law" };

type Status = "waiting" | "reviewing" | "done" | "failed";
interface Job {
  key: string;
  file: File;
  status: Status;
  stage?: ReviewStage;
  /** the options this file was (or will be) reviewed with */
  opts: ReviewOptions;
  result?: ReviewResult;
  error?: string;
}

const STATUS: Record<Status, string> = {
  waiting: "Waiting",
  reviewing: "Reviewing",
  done: "Done",
  failed: "Not reviewed",
};

const PHASE: Record<ReviewStage, string> = {
  reading: "Reading the contract",
  planning: "Planning what to look up",
  researching: "Looking up sources",
  drafting: "Drafting the findings",
  verifying: "Checking quotes and sources",
  locating: "Locating quotes on the pages",
};

function check(file: File): string | null {
  if (!isAccepted(file)) return "This is not a PDF, JPG or PNG. Save it as a PDF, or take a photo of each page, and add it again.";
  if (file.size > MAX_BYTES) return "This file is larger than 10 MB. Try a smaller PDF, or only the pages that matter.";
  return null;
}

/** Options as the user sees them; "" means automatic. */
interface Opts {
  contract_type: string;
  jurisdiction: "" | "my" | "sg";
  apply_company_rules: boolean;
  perspective: "" | "business" | "individual";
}

function toApi(o: Opts, hasRules: boolean, docs: CompanyDoc[], picked: Set<string>): ReviewOptions {
  // Every policy ticked: send nothing, so policies added later are also applied.
  const some = hasRules && o.apply_company_rules && picked.size < docs.length ? docs.filter((d) => picked.has(d.id)).map((d) => d.id) : undefined;
  return {
    company_doc_ids: some,
    contract_type: o.contract_type || undefined,
    jurisdiction: o.jurisdiction || undefined,
    apply_company_rules: hasRules ? o.apply_company_rules : undefined,
    perspective: o.perspective || undefined,
  };
}

/** The focused upload screen: several PDFs reviewed one after another. */
export default function Upload() {
  const s = useSession();
  // The read-only demo cannot start paid work; say so instead of offering a picker the API would refuse.
  const readOnly = readOnlyReason(s);
  const inputRef = useRef<HTMLInputElement>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dragging, setDragging] = useState(false);
  const [needSignIn, setNeedSignIn] = useState<boolean | string>(false);
  const [docs, setDocs] = useState<CompanyDoc[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const hasRules = docs.length > 0;
  const [opts, setOpts] = useState<Opts>({ contract_type: "", jurisdiction: "", apply_company_rules: true, perspective: "" });
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => {
    if (!s.user && !s.guest) return setDocs([]);
    listCompanyDocs()
      .then((d) => {
        setDocs(d);
        setPicked(new Set(d.map((x) => x.id)));
      })
      .catch(() => setDocs([]));
  }, [s.user, s.guest, s.workspace?.workspace.id]);

  const patch = (key: string, p: Partial<Job>) => setJobs((js) => js.map((j) => (j.key === key ? { ...j, ...p } : j)));

  // One upload at a time: take the next waiting file whenever nothing is running.
  useEffect(() => {
    if (running.current) return;
    const next = jobs.find((j) => j.status === "waiting");
    if (!next) return;
    running.current = true;
    patch(next.key, { status: "reviewing", stage: undefined, error: undefined });
    uploadReview(next.file, next.opts, (stage) => { if (mounted.current) patch(next.key, { stage }); })
      .then((result) => { if (mounted.current) patch(next.key, { status: "done", result }); })
      .catch((e) => {
        if (!mounted.current) return;
        if (e instanceof ApiError && (e.code === SIGN_IN_CODE || e.code === "guest_read_only")) {
          setNeedSignIn(e.code === SIGN_IN_CODE ? true : e.message);
          // Nothing else can upload while signed out: mark the rest too, each can be retried after sign-in.
          setJobs((js) => js.map((j) => (j.status === "waiting" || j.key === next.key ? { ...j, status: "failed", error: e.message } : j)));
        } else patch(next.key, { status: "failed", error: e instanceof Error ? e.message : "The review did not finish. Try again." });
      })
      .finally(() => {
        running.current = false;
        if (!mounted.current) return;
        setJobs((js) => [...js]); // wake the queue
      });
  }, [jobs]);

  function add(files: FileList | File[] | null | undefined) {
    if (!files) return;
    const list = [...files].map((file, i): Job => {
      const err = check(file);
      return { key: `${Date.now()}-${i}-${file.name}`, file, opts: toApi(optsRef.current, hasRules, docs, picked), status: err ? "failed" : "waiting", error: err ?? undefined };
    });
    setJobs((js) => [...js, ...list]);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    add(e.dataTransfer.files);
  }

  const current = jobs.find((j) => j.status === "reviewing");
  // The active panel disappears when a review finishes; keep the result in the same place so it does not just vanish.
  const lastDone = [...jobs].reverse().find((j) => j.status === "done");
  const done = jobs.filter((j) => j.status === "done").length;
  const active = jobs.some((j) => j.status === "waiting" || j.status === "reviewing");

  return (
    <div className="mx-auto max-w-[960px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="page-eyebrow">{s.workspace?.workspace.name ?? "Sample workspace"}</p>
      <h1 className="display mt-3 text-2xl text-white sm:text-2xl">Review a contract</h1>
      <p className="mt-3 max-w-2xl text-base text-desk-text">
        Add one contract or several. They are reviewed one after another, and each opens with its risky clauses boxed on its own pages.
      </p>

      {current && <ActiveUpload key={current.key} job={current} />}
      {!current && lastDone?.result && <JustFinished job={lastDone} />}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!readOnly) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => (readOnly ? e.preventDefault() : onDrop(e))}
        className={`sheet mt-7 flex flex-col items-center gap-4 border-2 border-dashed px-6 py-10 text-center ${dragging ? "border-desk bg-paper-soft" : "border-transparent"}`}
      >
        <svg width="40" height="40" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="text-desk">
          <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5Z" />
          <path d="M14 2v6h6M12 18v-6M9 15l3-3 3 3" />
        </svg>
        <div>
          <p className="text-lg font-semibold text-ink">Drop contracts here</p>
          <p className="mt-1 text-sm text-ink-soft">PDF, or a photo of each page (JPG or PNG). Each up to 10 MB and 40 pages, in English or Bahasa Malaysia.</p>
        </div>
        {readOnly ? (
          <div className="max-w-md text-ink">
            <p className="font-semibold">{readOnly}</p>
            <div className="mt-3 flex justify-center"><SignInButton /></div>
          </div>
        ) : (
        <button type="button" className="btn-ink" onClick={() => inputRef.current?.click()}>
          Choose files
        </button>
        )}
        {!readOnly && <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf,image/jpeg,.jpg,.jpeg,image/png,.png"
          multiple
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />}
      </div>

      {needSignIn && (
        <div role="alert" className="mt-5 rounded-[3px] bg-white px-5 py-4 text-ink">
          <p className="font-semibold">{typeof needSignIn === "string" ? needSignIn : "Sign in with Google to review your own contract."}</p>
          <p className="mt-1 text-sm text-ink-soft">Your contracts stay in your own workspace. The sample contracts on the home page work without signing in.</p>
          {s.available && (
            <div className="mt-3">
              <SignInButton />
            </div>
          )}
        </div>
      )}

      <details className="sheet group mt-5">
        <summary className="flex min-h-[52px] cursor-pointer list-none items-center justify-between gap-3 px-5 py-3 [&::-webkit-details-marker]:hidden">
          <span>
            <span className="block font-semibold text-ink">Options</span>
            <span className="text-sm text-ink-soft">{summary(opts, hasRules)}</span>
          </span>
          <span aria-hidden="true" className="text-ink-mute group-open:rotate-180">▾</span>
        </summary>
        <div className="grid gap-5 border-t border-paper-line px-5 py-5 sm:grid-cols-2">
          <Choice
            label="Contract type"
            value={opts.contract_type}
            onChange={(v) => setOpts({ ...opts, contract_type: v })}
            options={[["", "Detect automatically"], ["nda", "NDA"], ["service_agreement", "Service agreement"], ["employment", "Employment or freelance"], ["tenancy", "Tenancy"], ["hire_purchase", "Hire purchase"]]}
          />
          <Choice
            label="Governing law"
            value={opts.jurisdiction}
            onChange={(v) => setOpts({ ...opts, jurisdiction: v as Opts["jurisdiction"] })}
            options={[["", "From the contract"], ["my", "Malaysia"], ["sg", "Singapore"]]}
          />
          <Choice
            label="Whose side are you on"
            value={opts.perspective}
            onChange={(v) => setOpts({ ...opts, perspective: v as Opts["perspective"] })}
            options={[["", "Detect automatically"], ["business", "My business"], ["individual", "Me personally"]]}
          />
          {hasRules && (
            <label className="flex min-h-[44px] items-start gap-3 self-end text-ink">
              <input type="checkbox" checked={opts.apply_company_rules} onChange={(e) => setOpts({ ...opts, apply_company_rules: e.target.checked })} className="mt-1 h-5 w-5 accent-[#121A2B]" />
              <span>
                <span className="block font-semibold">Check against our company rules</span>
                <span className="text-sm text-ink-soft">Flags clauses that break your contracting policy.</span>
              </span>
            </label>
          )}
          {hasRules && opts.apply_company_rules && docs.length > 1 && (
            <fieldset className="sm:col-span-2">
              <legend className="label text-ink-soft">Which policies</legend>
              <ul className="mt-1 grid gap-1 sm:grid-cols-2">
                {docs.map((d) => (
                  <li key={d.id}>
                    <label className="flex min-h-[44px] items-center gap-3 text-ink">
                      <input
                        type="checkbox"
                        checked={picked.has(d.id)}
                        onChange={(e) => {
                          const next = new Set(picked);
                          if (e.target.checked) next.add(d.id);
                          else next.delete(d.id);
                          setPicked(next);
                        }}
                        className="h-5 w-5 accent-[#121A2B]"
                      />
                      <span className="min-w-0 break-words">{docTitle(d)}</span>
                    </label>
                  </li>
                ))}
              </ul>
              {picked.size === 0 && <p className="mt-1 text-sm font-medium text-medium">Tick at least one policy, or switch the check off.</p>}
            </fieldset>
          )}
          <p className="text-sm text-ink-soft sm:col-span-2">Options apply to files you add from now on.</p>
        </div>
      </details>

      {jobs.length > 0 && (
        <section aria-labelledby="queue-title" className="mt-6">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 id="queue-title" className="text-xl font-bold text-white">
              {active ? "Reviewing" : "Reviewed"}, {done} of {jobs.length}
            </h2>
            {!active && (
              <button type="button" className="inline-flex min-h-[44px] items-center font-semibold text-white underline underline-offset-4" onClick={() => setJobs([])}>
                Clear the list
              </button>
            )}
          </div>
          <ol className="sheet mt-3 divide-y divide-paper-line" aria-live="polite">
            {jobs.map((j) => (
              <Row
                key={j.key}
                job={j}
                onRetry={() => patch(j.key, { status: "waiting", stage: undefined, error: undefined })}
                onRedo={(o) => setJobs((js) => [...js, { key: `${Date.now()}-redo-${j.file.name}`, file: j.file, opts: { ...j.opts, ...o }, status: "waiting" }])}
              />
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

/** Only the active job gets a local preview; queued/completed files keep no object URL. */
function ActiveUpload({ job }: { job: Job }) {
  const region = useRef<HTMLDivElement>(null);
  const announced = useRef<string | null>(null);
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    const local = URL.createObjectURL(job.file);
    setUrl(local);
    return () => URL.revokeObjectURL(local);
  }, [job.file]);
  // Wait until the real source pane is mounted, then reveal it once for this active job.
  useEffect(() => {
    const el = region.current;
    if (!url || !el || announced.current === job.key) return;
    announced.current = job.key;
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
  }, [job.key, url]);
  const image = /^image\/(jpeg|png)$/i.test(job.file.type) || /\.(jpe?g|png)$/i.test(job.file.name);
  return <div ref={region} role="region" aria-label={`Reviewing ${job.file.name}`} tabIndex={-1} className="scroll-mt-4 rounded outline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-bright">
    <Progress embedded subject={job.file.name} stage={job.stage} source={url ? { url, name: job.file.name, kind: image ? "image" : "pdf" } : undefined} />
  </div>;
}

/** Shown where the active panel was, once the newest review has finished, so the outcome stays in view. */
function JustFinished({ job }: { job: Job }) {
  const r = job.result!;
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.focus({ preventScroll: true });
    box.current?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
  }, [job.key]);
  const counts = (["high", "medium", "low"] as const)
    .map((k) => [k, r.findings.filter((f) => f.risk === k).length] as const)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${k}`)
    .join(", ");
  return (
    <div ref={box} role="status" tabIndex={-1} className="sheet mt-7 flex scroll-mt-4 flex-wrap items-center justify-between gap-4 px-6 py-5 outline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-bright">
      <div className="min-w-0">
        <p className="text-sm text-ink-soft">Review ready</p>
        <p className="mt-0.5 break-words text-lg font-semibold text-ink">{breakable(job.file.name)}</p>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-ink-soft">
          <RiskLabel risk={r.verdict.overall_risk} />
          <span>{counts || "No risky clauses found"}</span>
        </p>
      </div>
      <Link to={`/reviews/${r.id}`} className="btn-go !min-h-[48px] px-5">
        Open the review <span className="sr-only">of {job.file.name}</span>
      </Link>
    </div>
  );
}

function summary(o: Opts, hasRules: boolean): string {
  const auto = !o.contract_type && !o.jurisdiction && !o.perspective && (o.apply_company_rules || !hasRules);
  if (auto) return hasRules ? "All automatic, checked against your company rules" : "All automatic";
  const parts: string[] = [];
  if (o.contract_type) parts.push(contractTypeLabel(o.contract_type as ReviewResult["contract_type"]));
  if (o.jurisdiction) parts.push(LAW[o.jurisdiction]);
  if (o.perspective) parts.push(o.perspective === "business" ? "for my business" : "for me personally");
  if (hasRules && !o.apply_company_rules) parts.push("company rules off");
  return parts.join(", ");
}

function Choice({ label, value, options, onChange }: { label: string; value: string; options: [string, string][]; onChange: (v: string) => void }) {
  const id = `opt-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div>
      <label htmlFor={id} className="label block text-ink-soft">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="mt-1 block min-h-[44px] w-full rounded-[3px] border border-ink/30 bg-white px-2 text-ink">
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  );
}

const TYPES: [string, string][] = [["nda", "NDA"], ["service_agreement", "Service agreement"], ["employment", "Employment or freelance"], ["tenancy", "Tenancy"], ["hire_purchase", "Hire purchase"]];

function Row({ job, onRetry, onRedo }: { job: Job; onRetry: () => void; onRedo: (o: Partial<ReviewOptions>) => void }) {
  const [redo, setRedo] = useState(false);
  const r = job.result;
  const busy = job.status === "reviewing";
  const tone = job.status === "done" ? "bg-low text-white" : job.status === "failed" ? "bg-high text-white" : busy ? "bg-desk text-white" : "bg-paper-soft text-ink border border-paper-line";
  return (
    <li className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-start gap-x-4 gap-y-2 px-5 py-4 sm:grid-cols-[6.5rem_minmax(0,1fr)_auto] sm:items-center">
      <span className={`tab inline-flex h-7 items-center justify-center rounded-[3px] text-xs ${tone}`}>
        {STATUS[job.status]}
        {busy && "…"}
      </span>
      <span className="min-w-0">
        <span className="block break-words font-semibold text-ink">{breakable(job.file.name)}</span>
        {r ? (
          <span className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-soft">
            <RiskLabel risk={r.verdict.overall_risk} />
            <span>{contractTypeLabel(r.contract_type)}</span>
            {r.jurisdiction && LAW[r.jurisdiction] && <span>{LAW[r.jurisdiction]}</span>}
            <span>
              {r.findings.length} {r.findings.length === 1 ? "finding" : "findings"}
            </span>
          </span>
        ) : null}
        {r && (
          <span className="mt-1 block">
            {redo ? (
              <span className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-ink-soft">Review again as</span>
                {TYPES.filter(([v]) => v !== r.contract_type).map(([v, l]) => (
                  <button key={v} type="button" onClick={() => { setRedo(false); onRedo({ contract_type: v }); }} className="inline-flex min-h-[44px] items-center rounded-[3px] border border-paper-line px-2.5 text-ink hover:border-ink">
                    {l}
                  </button>
                ))}
                {(["my", "sg"] as const).filter((x) => x !== r.jurisdiction).map((x) => (
                  <button key={x} type="button" onClick={() => { setRedo(false); onRedo({ jurisdiction: x }); }} className="inline-flex min-h-[44px] items-center rounded-[3px] border border-paper-line px-2.5 text-ink hover:border-ink">
                    under {LAW[x]}
                  </button>
                ))}
                <button type="button" onClick={() => setRedo(false)} className="inline-flex min-h-[44px] items-center px-1 text-ink-soft underline underline-offset-4">Cancel</button>
              </span>
            ) : (
              <button type="button" onClick={() => setRedo(true)} className="inline-flex min-h-[44px] items-center text-sm font-semibold text-desk underline underline-offset-4 hover:text-ink">
                Wrong type or law? Review again as…
              </button>
            )}
          </span>
        )}
        {r ? null : job.error ? (
          <span className="mt-0.5 block text-sm text-high">{job.error}</span>
        ) : (
          <span className="mt-0.5 block text-sm text-ink-soft">
            {job.status === "waiting" ? "Next in line" : job.stage ? PHASE[job.stage] : "Waiting for the completed review. No live phase has been reported."}
          </span>
        )}
      </span>
      <span className="col-start-2 sm:col-start-auto">
        {r ? (
          <Link to={`/reviews/${r.id}`} className="btn-ink !min-h-[44px] px-4">
            Open <span className="sr-only">the review of {job.file.name}</span>
          </Link>
        ) : job.status === "failed" && !check(job.file) ? (
          <button type="button" onClick={onRetry} className="btn-line !min-h-[44px] px-4">
            Try again <span className="sr-only">with {job.file.name}</span>
          </button>
        ) : null}
      </span>
    </li>
  );
}
