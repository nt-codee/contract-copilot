import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Finding, RemovedClaim, ReviewResult, TraceAction, TraceDetail, TraceStep } from "../types";

/** What each kind of step looked at, in the reader's terms. */
export const ACTION_LABEL: Record<TraceAction, string> = {
  read_contract: "Read",
  plan_research: "Plan research",
  search_law: "Law",
  search_playbook: "Usual terms",
  search_company: "Your rules",
  read_page: "Re-read",
  draft_findings: "Wrote",
  verify: "Checked",
};

const PLACE: Record<string, string> = { my: "Malaysian", sg: "Singapore" };
const TYPE: Record<string, string> = { nda: "NDAs", service_agreement: "service agreements" };

function parse(d?: string | null): TraceDetail | null {
  if (!d) return null;
  try {
    const x = JSON.parse(d) as Partial<TraceDetail>;
    if (!x || typeof x !== "object" || typeof x.tool !== "string") return null;
    return {
      tool: x.tool,
      args: x.args && typeof x.args === "object" && !Array.isArray(x.args) ? x.args : {},
      results: Array.isArray(x.results) ? x.results.filter((r) => r && typeof r.ref === "string" && typeof r.title === "string" && typeof r.kind === "string") : [],
      cited: Array.isArray(x.cited) ? x.cited.filter((r) => typeof r === "string") : [],
    };
  } catch {
    return null;
  }
}

/** The tool's arguments, said in plain words. */
function asked(a: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof a.query === "string" && a.query) parts.push(`“${a.query}”`);
  if (typeof a.jurisdiction === "string" && PLACE[a.jurisdiction]) parts.push(`in ${PLACE[a.jurisdiction]} law`);
  if (typeof a.clause_type === "string") parts.push(`for ${a.clause_type.replace(/_/g, " ")} clauses`);
  if (typeof a.contract_type === "string" && TYPE[a.contract_type]) parts.push(`in ${TYPE[a.contract_type]}`);
  if (typeof a.page === "number") parts.push(`page ${a.page}`);
  return parts.join(" ");
}

/** Company results point at a policy, not a library entry. */
function link(ref: string, kind: string): string | null {
  if (kind === "company" || ref.startsWith("company:")) return "/rules";
  return `/knowledge/${encodeURIComponent(kind === "playbook" && !ref.startsWith("playbook:") ? `playbook:${ref}` : ref)}`;
}

/** One fact about how the review was made: model, knowledge and instruction versions, cost. */
export interface TraceFact {
  term: string;
  value: string;
  /** shown as a tooltip, e.g. the full model id */
  title?: string;
}

const WHY: Record<RemovedClaim["reason"], string> = {
  quote_not_found: "Quote not found in the contract",
  source_not_retrieved: "Source was not retrieved",
  no_company_citation: "No company rule cited",
  wrong_jurisdiction: "Law from another jurisdiction",
  ungrounded: "Not supported by the contract",
  citation_scope: "Source does not support this point",
  remedy_scope: "Remedy not supported by governing law",
  unsupported_severity: "Risk reduced to medium: no concrete high-risk consequence",
};

interface Props {
  steps: TraceStep[];
  seconds: string;
  facts?: TraceFact[];
  findings?: Finding[];
  removed?: RemovedClaim[];
  metrics?: ReviewResult["metrics"];
  onPick?: (id: string) => void;
}

const LANES: TraceAction[] = ["read_contract", "plan_research", "search_law", "search_playbook", "search_company", "read_page", "draft_findings", "verify"];
const nonnegative = (n: number | null | undefined): n is number => typeof n === "number" && Number.isFinite(n) && n >= 0;
const identifier = (n: number | null | undefined): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0;
function recordedSpan(s: TraceStep): s is TraceStep & { start_ms: number; ms: number } {
  return nonnegative(s.start_ms) && nonnegative(s.ms) && Number.isFinite(s.start_ms + s.ms);
}
const time = (ms: number) => `${(ms / 1000).toFixed(2)} s`;
function turnBatch(s: TraceStep): string {
  return [identifier(s.turn) ? `turn ${s.turn}` : "", identifier(s.batch) ? `batch ${s.batch}` : ""].filter(Boolean).join(", ");
}

/** Optional monotonic offsets place only genuinely recorded spans on the elapsed axis. */
export default function Trace({ steps, seconds, facts = [], findings = [], removed = [], metrics, onPick }: Props) {
  const [expanded, setExpanded] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (expanded) dialog.current?.showModal();
    else dialog.current?.close();
  }, [expanded]);
  if (steps.length === 0 && facts.length === 0 && findings.length === 0) return null;
  const used = new Set(steps.flatMap((s) => parse(s.detail)?.cited ?? [])).size;
  const maximum = Math.max(1, ...steps.map((s) => nonnegative(s.ms) ? s.ms : 0));
  const timed = steps.filter(recordedSpan);
  const elapsed = Math.max(1, ...timed.map((s) => s.start_ms + s.ms));
  const turns = new Set(steps.filter((s) => identifier(s.turn)).map((s) => s.turn)).size;
  const missingTurns = steps.some((s) => (s.action === "plan_research" || s.action === "draft_findings") && !identifier(s.turn));
  const turnCount = turns ? `${turns} recorded model ${turns === 1 ? "turn" : "turns"}${missingTurns ? "; some model actions have no turn ID" : ""}` : "Model turn count not recorded";
  const candidates = steps.find((s) => s.action === "draft_findings")?.detail?.match(/^(\d+) candidate findings$/)?.[1];
  function choose(id: string) {
    setExpanded(false);
    onPick?.(id);
  }
  function content() {
    return <div className="px-5 pb-5">
      <p className="meta mb-3 text-ink-mute">{turnCount}.</p>
      {timed.length > 0 ? <section aria-label="Recorded elapsed timeline" className="my-inner min-w-0 rounded border border-paper-line bg-white p-3">
        <h3 className="label">Recorded elapsed timeline</h3>
        <p className="mt-1 text-sm text-ink-soft">Bars share the same elapsed axis from the start of this review. Overlap shows recorded concurrent work.</p>
        {timed.length < steps.length && <p className="meta mt-2 text-ink-mute">{steps.length - timed.length} actions lack a valid start or duration; their available details remain below.</p>}
        <div className="mt-3 grid grid-cols-[5rem_minmax(0,1fr)] gap-2">
          <span className="meta text-ink-mute">Elapsed</span>
          <div className="meta flex min-w-0 justify-between gap-1 text-ink-mute"><span>0 s</span><span>{time(elapsed)}</span></div>
          {LANES.map((action) => {
            const lane = timed.filter((s) => s.action === action);
            if (!lane.length) return null;
            const band = action === "plan_research" || action === "draft_findings" || action === "verify";
            return <div key={action} className="contents">
              <span className="meta min-w-0 break-words text-ink-soft">{ACTION_LABEL[action]}</span>
              <div className="min-w-0 space-y-2">{lane.map((s) => <div key={s.step}>
                <p className="meta break-words text-ink-mute">Step {s.step}{turnBatch(s) && `, ${turnBatch(s)}`}: {time(s.start_ms)}–{time(s.start_ms + s.ms)}</p>
                <div aria-hidden="true" className="relative mt-1 h-3 overflow-hidden rounded-sm bg-paper-soft">
                  <div className={`absolute inset-y-0 ${band ? "bg-ink-soft" : "bg-accent"}`} style={{ left: `${s.start_ms / elapsed * 100}%`, width: `${s.ms / elapsed * 100}%` }} />
                </div>
              </div>)}</div>
            </div>;
          })}
        </div>
      </section> : <>
      <p className="text-sm text-ink-soft">Recorded actions and returned sources. Timing bars compare durations from 0 to {(maximum / 1000).toFixed(1)} s; they do not show start times or overlap. {steps.some((s) => s.action === "plan_research") ? "Planning actions are recorded below without elapsed timing." : "Planning turns were not recorded."}</p>
      {steps.some((s) => ["search_law", "search_playbook", "search_company", "read_page"].includes(s.action)) && <section aria-label="Lookup duration lanes" className="my-inner rounded border border-paper-line bg-white p-3">
        <h3 className="label">Lookup lanes</h3>
        <p className="meta text-ink-mute">Each bar starts at zero duration; concurrent starts are not recorded.</p>
        <div className="mt-3 space-y-3">
          {(["search_law", "search_playbook", "search_company", "read_page"] as TraceAction[]).map((action) => {
            const lane = steps.filter((s) => s.action === action);
            if (!lane.length) return null;
            return <div key={action} className="grid grid-cols-[5rem_minmax(0,1fr)] gap-2">
              <span className="meta text-ink-soft">{ACTION_LABEL[action]}</span>
              <div className="min-w-0 space-y-2">{lane.map((s) => <div key={s.step}>
                <span className="meta block text-ink-mute">Step {s.step}: {nonnegative(s.ms) ? `${(s.ms / 1000).toFixed(2)} s` : "duration unavailable"}</span>
                {timed.length === 0 && nonnegative(s.ms) && <div aria-hidden="true" className="h-2 bg-paper-soft"><div className="h-full bg-accent" style={{ width: `${Math.max(0, s.ms) / maximum * 100}%` }} /></div>}
              </div>)}</div>
            </div>;
          })}
        </div>
      </section>}
      </>}
      <ol className="mt-inner space-y-4" aria-label="Recorded review timeline">
        {steps.map((s) => {
          const d = parse(s.detail);
          const cited = new Set(d?.cited ?? []);
          return <li key={s.step} className="min-w-0 rounded border border-paper-line bg-white p-3">
            <div className="flex flex-wrap justify-between gap-2">
              <span className="meta text-accent">{s.step}. {ACTION_LABEL[s.action] ?? s.action}</span>
              <span className="meta text-ink-mute">{recordedSpan(s) ? `${time(s.start_ms)}–${time(s.start_ms + s.ms)} elapsed, ${time(s.ms)} duration` : nonnegative(s.ms) ? `${time(s.ms)} duration; start not recorded` : "Duration not recorded"}</span>
            </div>
            {timed.length === 0 && nonnegative(s.ms) && <div aria-hidden="true" className="my-2 h-1.5 bg-paper-soft"><div className="h-full bg-accent" style={{ width: `${Math.max(0, s.ms) / maximum * 100}%` }} /></div>}
            {turnBatch(s) && <p className="meta text-ink-mute">{turnBatch(s)}</p>}
            <p className="break-words text-sm text-ink">{s.summary}</p>
            {!d && s.detail && <p className="mt-1 break-words text-sm text-ink-soft">{s.detail}</p>}
            {d && asked(d.args) && <p className="mt-1 break-words text-sm text-ink-soft">Looked up {asked(d.args)}</p>}
            {d && <ul className="mt-2 flex flex-wrap gap-2" aria-label="Returned hits">
              {d.results.map((r, i) => {
                const targets = findings.filter((f) => (f.citations ?? []).some((c) => c.ref === r.ref) || f.baseline_ref === r.ref);
                const to = link(r.ref, r.kind);
                return <li key={`${r.ref}-${i}`} className="min-w-0 max-w-full rounded border border-paper-line bg-paper-panel px-2 py-1 text-xs text-ink-soft">
                  <span className="block break-words">{r.title}</span>
                  <span className="meta block">{cited.has(r.ref) ? "Used in findings" : "Retrieved, not cited"}</span>
                  {onPick && targets.map((f) => <button key={f.id} type="button" className="block min-h-[44px] max-w-full break-words text-left font-semibold text-accent underline" onClick={() => choose(f.id)}>Show: {f.title}</button>)}
                  {to && <Link to={to} className="inline-flex min-h-[44px] items-center text-accent underline">Open source<span className="sr-only">: {r.title}</span></Link>}
                </li>;
              })}
            </ul>}
          </li>;
        })}
      </ol>
      <section aria-label="Verification outcome" className="mt-section border-t border-paper-line pt-inner">
        <h3 className="label text-ink">Verification</h3>
        <p className="mt-2 text-sm text-ink-soft">{candidates ? `${candidates} candidate findings → ` : "Candidate finding count not recorded. "}{findings.length} findings kept. {metrics?.dropped_unverified != null && `${metrics.dropped_unverified} findings removed. `}{metrics?.dropped_unverified_citations != null && `${metrics.dropped_unverified_citations} citations removed.`}</p>
        <ul className="mt-inner space-y-2">
          {findings.map((f) => <li key={f.id} className="text-sm">
            <button type="button" disabled={!onPick} className="min-h-[44px] max-w-full break-words text-left font-semibold text-accent underline" onClick={() => choose(f.id)}>Kept: {f.title}, page {f.page}</button>
            <span className="block text-ink-soft">{f.verification ? `Quote checked: ${f.verification.quote_match} match` : "Detailed quote verification not recorded"}</span>
          </li>)}
          {removed.map((r, i) => <li key={i} className="border-l-2 border-ink-mute pl-3 text-sm text-ink-soft">
            <span className="meta block">{r.reason === "unsupported_severity" ? "Adjusted" : "Removed"}: {r.kind.replace(/_/g, " ")}{r.page ? `, page ${r.page}` : ""}</span>
            <p className="break-words">{r.text}</p><p className="font-medium text-ink">{WHY[r.reason] ?? r.reason}</p>
          </li>)}
        </ul>
      </section>
      {facts.length > 0 && <dl className="mt-section grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-3 border-t border-paper-line pt-inner text-sm">
        {facts.map((f) => <div key={f.term} className="contents"><dt className="text-ink-mute">{f.term}</dt><dd className="min-w-0 break-words text-ink" title={f.title}><span className="sr-only">: </span>{f.value}</dd></div>)}
      </dl>}
    </div>;
  }
  return <>
    <details className="group border-t border-paper-line">
      <summary className="flex min-h-[52px] cursor-pointer items-center justify-between gap-3 px-5 py-3 hover:bg-paper-soft">
        <span><span className="block font-semibold text-ink">How this was checked</span><span className="meta block text-ink-mute">{steps.length} recorded steps, {used} sources marked used, {seconds}</span></span>
      </summary>
      <div className="px-5 pb-3"><button type="button" className="btn-line" onClick={() => setExpanded(true)}>Expand X-ray view</button></div>
      {!expanded && content()}
    </details>
    <dialog ref={dialog} onCancel={() => setExpanded(false)} onClose={() => setExpanded(false)} aria-label="Review X-ray" className="max-h-[90dvh] w-[min(1100px,95vw)] max-w-none overflow-y-auto rounded bg-paper-panel p-0 text-ink shadow-float backdrop:bg-desk-deep/80">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-paper-line bg-paper-panel px-5 py-3"><h2 className="text-lg font-bold">Review X-ray</h2><button type="button" className="btn-line" onClick={() => setExpanded(false)}>Close X-ray</button></div>
      {expanded && <div className="pt-4">{content()}</div>}
    </dialog>
  </>;
}
