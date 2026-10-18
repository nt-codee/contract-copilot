import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { deleteReview, draftNegotiation, exportReview, getReview, pdfUrl, reviewSample } from "../api";
import { canManage } from "../session";
import { useSession } from "../useSession";
import type { ReviewStage } from "../api";
import Progress from "../components/Progress";
import { SignInButton } from "../components/Account";
import DraftDialog from "../components/DraftDialog";
import FindingItem from "../components/FindingItem";
import Reader from "../components/Reader";
import Trace from "../components/Trace";
import type { TraceFact } from "../components/Trace";
import Evidence from "../components/Evidence";
import type { EvidenceTarget } from "../components/Evidence";
import { History, LeftOut } from "../components/Checks";
import KeyDates, { DeadlineLine } from "../components/KeyDates";
import type { Marked, ReaderState } from "../components/Reader";
import { RISK_BG, RISK_TEXT, RiskShape } from "../components/Risk";
import { RISK_LABEL, RISK_ORDER, aiVersion, breakable, contractTypeLabel, formatCost, formatDate, formatSeconds, languageLabel } from "../format";
import type { Box } from "../locate";
import type { NegotiationDraft, ReviewResult, Risk } from "../types";
import { useMedia } from "../useMedia";
import { counterparty } from "../counterparty";
import { reviewTitle } from "../titles";

const PANEL_KEY = "cc.panelWidth";
const PANEL_MIN = 360;
const panelMax = () => Math.max(PANEL_MIN, Math.round(window.innerWidth * 0.6));
/** Panel width in px, kept between 360 px and 60% of the window. 0 means "not set": 32vw, at least 400. */
function clampPanel(w: number): number {
  const v = w > 0 ? w : Math.max(400, window.innerWidth * 0.32);
  return Math.min(panelMax(), Math.max(PANEL_MIN, v));
}

/** Drag handle between the contract and the findings. Arrow keys move it 16 px (Shift: 64), Home/End jump. */
function Resizer({ width, onChange }: { width: number; onChange: (w: number) => void }) {
  const drag = useRef<{ x: number; w: number } | null>(null);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the findings panel"
      aria-valuemin={PANEL_MIN}
      aria-valuemax={panelMax()}
      aria-valuenow={Math.round(width)}
      aria-valuetext={`${Math.round(width)} pixels wide`}
      tabIndex={0}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 64 : 16;
        // The panel is on the right: moving the handle left widens it.
        const next = e.key === "ArrowLeft" ? width + step : e.key === "ArrowRight" ? width - step : e.key === "Home" ? panelMax() : e.key === "End" ? PANEL_MIN : null;
        if (next == null) return;
        e.preventDefault();
        onChange(clampPanel(next));
      }}
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, w: width };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (drag.current) onChange(clampPanel(drag.current.w + drag.current.x - e.clientX));
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerCancel={() => (drag.current = null)}
      className="group relative z-10 w-2 shrink-0 cursor-col-resize touch-none bg-desk-deep outline-offset-0 hover:bg-accent/40 focus-visible:bg-accent/40"
    >
      <span aria-hidden="true" className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-desk-text/70 group-hover:bg-white" />
    </div>
  );
}

export default function Review() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const session = useSession();
  const location = useLocation();
  const opening = location.state as { earlier?: string; sampleId?: string } | null;
  const navigate = useNavigate();
  const refreshRequest = useRef(0);
  const refreshActive = useRef(false);
  const latestParams = useRef(params);
  latestParams.current = params;
  useEffect(() => {
    refreshRequest.current += 1;
    refreshActive.current = false;
    setRefreshing(false);
    setRefreshStage(undefined);
    return () => { refreshRequest.current += 1; };
  }, [id]);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStage, setRefreshStage] = useState<ReviewStage | undefined>();
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const wide = useMedia("(min-width: 1024px)");

  const [review, setReview] = useState<ReviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [found, setFound] = useState<Record<string, Box[]>>({});
  const [readerState, setReaderState] = useState<ReaderState>("loading");
  const [located, setLocated] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ id: string; k: number } | null>(null);
  const [pageView, setPageView] = useState<string | null>(null); // narrow screens

  const [draft, setDraft] = useState<NegotiationDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);

  const itemRefs = useRef(new Map<string, HTMLButtonElement>());
  const [panelW, setPanelW] = useState(() => clampPanel(Number(localStorage.getItem(PANEL_KEY)) || 0));
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    localStorage.setItem(PANEL_KEY, String(Math.round(panelW)));
  }, [panelW]);
  useEffect(() => {
    const onResize = () => setPanelW((w) => clampPanel(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    let live = true;
    setReview(null);
    setDraft(null);
    setError(null);
    setFound({});
    setLocated(false);
    getReview(id)
      .then((r) => {
        if (!live) return;
        setReview(r);
        const want = params.get("finding");
        const first = [...r.findings].sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk])[0];
        // A finding named in the link opens; otherwise show the worst one on the page, list closed.
        const named = r.findings.find((f) => f.id === want)?.id ?? null;
        setSelected(named);
        const show = named ?? first?.id;
        if (show) setFocus({ id: show, k: 0 });
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
    // `params` is read once per review.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (review) document.title = `${reviewTitle(review)} · Contract Co-pilot`;
    return () => {
      document.title = "Contract Co-pilot";
    };
  }, [review]);

  const items: Marked[] = useMemo(() => {
    if (!review) return [];
    return [...review.findings]
      .sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk])
      .map((f, i) => ({ f, n: i + 1, boxes: found[f.id] ?? f.boxes ?? [] }));
  }, [review, found]);

  const onLocated = useCallback((more: Record<string, Box[]>) => {
    setFound((cur) => ({ ...cur, ...more }));
    setLocated(true);
  }, []);
  const onState = useCallback((s: ReaderState) => setReaderState(s), []);
  const locating = readerState === "loading" || (readerState === "ready" && !located && items.some((m) => m.boxes.length === 0));

  // Merge into the current query so question and other deep-link state survive.
  function rememberFinding(fid: string | null) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (fid) next.set("finding", fid);
      else next.delete("finding");
      return next;
    }, { replace: true });
  }
  useEffect(() => {
    const fid = params.get("finding");
    if (!review) return;
    const named = review.findings.find((f) => f.id === fid)?.id ?? null;
    setSelected(named);
    setPageView(named);
    if (named) setFocus((cur) => ({ id: named, k: (cur?.k ?? 0) + 1 }));
  }, [params, review]);

  async function refreshSample() {
    if (!(review?.sample_id ?? opening?.sampleId) || !session.user || session.guest || session.loading || refreshing || refreshActive.current) return;
    refreshActive.current = true;
    const ticket = ++refreshRequest.current;
    setRefreshStage(undefined);
    setRefreshing(true);
    setRefreshError(null);
    try {
      const fresh = await reviewSample((review?.sample_id ?? opening?.sampleId)!, true, (next) => { if (refreshRequest.current === ticket) setRefreshStage(next); });
      if (refreshRequest.current !== ticket) return;
      const query = new URLSearchParams(latestParams.current);
      // Finding IDs can change between runs; keep only a still-valid selection.
      if (!fresh.findings.some((f) => f.id === query.get("finding"))) query.delete("finding");
      navigate(`/reviews/${fresh.id}${query.size ? `?${query}` : ""}`);
      setReview(fresh);
    } catch (e) {
      if (refreshRequest.current !== ticket) return;
      setRefreshError(e instanceof Error ? e.message : "The fresh review could not be completed.");
    } finally {
      if (refreshRequest.current !== ticket) return;
      refreshActive.current = false;
      setRefreshing(false);
      setRefreshStage(undefined);
    }
  }

  /** From the list: select and show it on the page. */
  function pick(fid: string) {
    rememberFinding(fid);
    setSelected(fid);
    setFocus((cur) => ({ id: fid, k: (cur?.k ?? 0) + 1 }));
    if (!wide) {
      setPageView(fid);
      window.scrollTo(0, 0);
    } else {
      // Keep the opened finding's heading in view in the rail.
      window.setTimeout(() => itemRefs.current.get(fid)?.scrollIntoView({ block: "start", behavior: "auto" }), 0);
    }
  }

  /** From a finding's "Where" chip: scroll the page to its box and pulse it; the list stays put. */
  function show(fid: string) {
    setFocus((cur) => ({ id: fid, k: (cur?.k ?? 0) + 1 }));
  }

  /** From the page: select it in the list. */
  function selectFromPage(fid: string) {
    rememberFinding(fid);
    setSelected(fid);
    if (wide) {
      const el = itemRefs.current.get(fid);
      el?.scrollIntoView({ block: "nearest" });
      el?.focus({ preventScroll: true });
    } else {
      setPageView(fid);
    }
  }

  async function openDraft() {
    if (draft) {
      setDialogOpen(true);
      return;
    }
    setDraftError(null);
    setDrafting(true);
    try {
      setDraft(await draftNegotiation(id));
      setDialogOpen(true);
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : "The reply could not be written. Try again.");
    } finally {
      setDrafting(false);
    }
  }

  if (error)
    return (
      <div className="mx-auto max-w-xl px-5 py-16 text-white" role="alert">
        <h1 className="display text-2xl">Review not available</h1>
        <p className="mt-3 text-desk-text">{error}</p>
        <Link to="/" className="btn-go mt-6">
          Review a contract
        </Link>
      </div>
    );

  if (!review)
    return (
      <p className="px-5 py-16 text-center text-desk-text" role="status">
        Opening your review…
      </p>
    );

  if (refreshing) return <Progress subject={reviewTitle(review)} stage={refreshStage} source={{ url: pdfUrl(review.id), name: reviewTitle(review), kind: "pdf" }} />;

  const url = pdfUrl(review.id);
  const reader = (compact: boolean) => (
    <Reader
      url={url}
      items={items}
      selected={selected}
      focus={focus}
      onSelect={selectFromPage}
      onLocated={onLocated}
      onState={onState}
      compact={compact}
    />
  );

  const list = (
    <section aria-labelledby="findings-title">
      <h2 id="findings-title" className="label px-5 pb-inner pt-section text-ink-soft">
        {items.length === 0 ? "Nothing flagged" : `What to look at, worst first`}
      </h2>
      {items.length === 0 ? (
        <p className="px-5 py-4 text-ink-soft">No clause in this contract needs pushing back on.</p>
      ) : (
        <ol className="pb-section">
          {items.map((m) => (
            <FindingItem
              key={m.f.id}
              ref={(el) => {
                if (el) itemRefs.current.set(m.f.id, el);
                else itemRefs.current.delete(m.f.id);
              }}
              m={m}
              reviewId={review.id}
              open={wide && selected === m.f.id}
              locating={locating}
              onPick={pick}
              onShow={show}
              opensPage={!wide}
              onEvidence={(c) => setEvidence({ m, c })}
            />
          ))}
        </ol>
      )}
    </section>
  );

  const other = counterparty(review);
  const verdict = (
    <Verdict review={review} items={items}>
      {(review.sample_id || opening?.sampleId) && <div className="mb-inner rounded border border-paper-line bg-white p-3 text-sm text-ink-soft">
        <p>{opening?.earlier ? "Earlier sample review" : "Sample review"} from {formatDate(opening?.earlier ?? review.created_at)}. Opening this result does not run a new review.</p>
        {session.user && !session.guest ? <button type="button" className="mt-2 min-h-[44px] font-semibold text-accent underline" disabled={session.loading} onClick={refreshSample}>Run a fresh review</button> : <div className="mt-2"><p>Sign in to run a fresh review in your workspace.</p><SignInButton /></div>}
        {refreshError && <p role="alert" className="mt-2 text-high">{refreshError}</p>}
      </div>}

      <button type="button" className="btn-primary w-full !min-h-[52px] text-lg" onClick={openDraft} disabled={drafting} aria-describedby={draftError ? "draft-error" : undefined}>
        {drafting ? "Writing your reply…" : draft ? `Open my reply to the ${other}` : `Draft my reply to the ${other}`}
      </button>
      {draftError && (
        <p id="draft-error" role="alert" className="mt-2 font-medium text-high">
          {draftError}
        </p>
      )}
    </Verdict>
  );

  const tail = (
    <>
      <KeyDates review={review} items={items} onPick={pick} />
      <Missing review={review} />
      <Provenance review={review} onPick={pick} />
      <Manage review={review} />
    </>
  );
  const dialog = (
    <>
    <Evidence target={evidence} reviewId={review.id} onClose={() => setEvidence(null)} />
    <DraftDialog
      other={other}
      draft={draft}
      open={dialogOpen}
      items={items}
      onClose={() => setDialogOpen(false)}
      onShow={(fid) => {
        setDialogOpen(false);
        window.setTimeout(() => pick(fid), 0);
      }}
    />
    </>
  );

  if (wide)
    return (
      <div className="desk-calm flex h-screen">
        <h1 className="sr-only">Review of {reviewTitle(review)}</h1>
        <section aria-label="The contract" className={`min-h-0 ${expanded ? "w-[200px] shrink-0 overflow-y-auto bg-desk-deep px-2 pt-3" : "min-w-0 flex-1"}`}>
          {reader(expanded)}
        </section>
        {!expanded && <Resizer width={panelW} onChange={setPanelW} />}
        <aside
          id="rail"
          aria-label="Findings"
          style={expanded ? undefined : { width: panelW }}
          className={`relative min-h-0 overflow-y-auto bg-paper-panel shadow-page ${expanded ? "min-w-0 flex-1" : "shrink-0"}`}
        >
          <div className={expanded ? "mx-auto max-w-[760px]" : ""}>
            <div className="flex justify-end px-5 pt-3">
              <button
                type="button"
                aria-pressed={expanded}
                onClick={() => setExpanded((v) => !v)}
                className="meta inline-flex min-h-[36px] items-center gap-1.5 rounded-[3px] px-2 font-semibold text-accent hover:bg-accent-tint"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  {expanded ? <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" /> : <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />}
                </svg>
                {expanded ? "Show the contract" : "Expand the findings"}
              </button>
            </div>
            {verdict}
            {list}
            {tail}
          </div>
        </aside>
        {dialog}
      </div>
    );

  // Narrow screens: the list first; a finding opens its page.
  const open = items.find((m) => m.f.id === pageView);
  return (
    <div className="desk-calm min-h-screen">
      <h1 className="sr-only">Review of {reviewTitle(review)}</h1>
      {open ? (
        <div>
          <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-desk-deep px-3 py-2">
            <button
              type="button"
              className="btn-desk !min-h-[44px] !px-3"
              onClick={() => {
                const back = pageView;
                setPageView(null);
                rememberFinding(null);
                window.setTimeout(() => back && itemRefs.current.get(back)?.focus(), 0);
              }}
            >
              <span aria-hidden="true">←</span> Findings
            </button>
            <span className="flex items-center gap-1">
              {(() => {
                const i = items.indexOf(open);
                return (
                  <>
                    <button type="button" className="btn-desk !min-h-[44px] min-w-[44px] !px-3" disabled={i === 0} aria-label="Previous finding" onClick={() => pick(items[i - 1].f.id)}>
                      <span aria-hidden="true">↑</span>
                    </button>
                    <span className="meta px-1 text-desk-text">
                      {open.n} of {items.length}
                    </span>
                    <button type="button" className="btn-desk !min-h-[44px] min-w-[44px] !px-3" disabled={i === items.length - 1} aria-label="Next finding" onClick={() => pick(items[i + 1].f.id)}>
                      <span aria-hidden="true">↓</span>
                    </button>
                  </>
                );
              })()}
            </span>
          </div>
          <div className="px-3 pb-[50vh] pt-4">{reader(true)}</div>
          <section aria-label={`Finding ${open.n}`} className="fixed inset-x-0 bottom-0 z-10 max-h-[46vh] overflow-y-auto overscroll-contain pb-[env(safe-area-inset-bottom)] bg-paper shadow-lift">
            <ol>
              <FindingItem m={open} reviewId={review.id} open locating={locating} onPick={() => undefined} onShow={show} onEvidence={(c) => setEvidence({ m: open, c })} />
            </ol>
          </section>
        </div>
      ) : (
        <div className="bg-paper-panel">
          {verdict}
          {list}
          {tail}
        </div>
      )}
      {dialog}
    </div>
  );
}

function lawLabel(j?: string | null): string | null {
  if (j === "my") return "Malaysian law";
  if (j === "sg") return "Singapore law";
  return null;
}

/** "3 high and 1 medium" in sentence case, zero counts left out. */
function countWords(c: Record<Risk, number>): string {
  const parts = (["high", "medium", "low"] as Risk[]).filter((r) => c[r]).map((r) => `${c[r]} ${r}`);
  if (parts.length === 0) return "Nothing flagged";
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/**
 * Review header, three bands:
 * 1. the verdict chip and the contract's short title, on one line;
 * 2. the verdict sentence as body text, and one sentence-case meta line;
 * 3. the next deadline (if any) and the reply action.
 */
function Verdict({ review, items, children }: { review: ReviewResult; items: Marked[]; children: ReactNode }) {
  const v = review.verdict;
  const counts: Record<Risk, number> = { high: 0, medium: 0, low: 0 };
  items.forEach((m) => (counts[m.f.risk] += 1));
  const law = lawLabel(review.jurisdiction);
  const pages = `${review.pages} ${review.pages === 1 ? "page" : "pages"}`;
  return (
    <section aria-labelledby="verdict-title" className="border-b border-paper-line px-5 pb-section pt-inner">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className={`tab inline-flex shrink-0 items-center gap-1.5 rounded-[3px] px-2 py-1 ${RISK_BG[v.overall_risk]} ${RISK_TEXT[v.overall_risk]}`}>
          <RiskShape risk={v.overall_risk} size={12} />
          {RISK_LABEL[v.overall_risk]}
        </p>
        <h2 id="verdict-title" className="min-w-0 break-words text-lg font-bold leading-snug text-ink [font-stretch:105%]">
          {breakable(reviewTitle(review))}
        </h2>
      </div>

      <p className="mt-inner text-base font-medium leading-relaxed text-ink">{v.headline}</p>
      {v.summary.length > 0 && (
        <>
          <h3 className="sr-only">In short</h3>
          <ul className="mt-2 space-y-1 text-sm text-ink-soft">
            {v.summary.map((line) => (
              <li key={line} className="flex gap-2.5">
                <span aria-hidden="true" className="mt-[0.65em] h-[2px] w-2.5 shrink-0 bg-ink-mute" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="meta mt-inner text-ink-mute">
        <span className="text-ink-soft">{countWords(counts)}.</span> {contractTypeLabel(review.contract_type)}, {languageLabel(review.language)}, {pages}
        {law && `, ${law}`}.{review.parties.length > 0 && ` Between ${review.parties.join(" and ").replace(/\.$/, "")}.`}{" "}
        <Link to="/library" className="whitespace-nowrap text-accent underline underline-offset-2 hover:text-accent-deep">
          All contracts
        </Link>
      </p>

      <DeadlineLine review={review} items={items} />
      <div className="mt-inner">{children}</div>
    </section>
  );
}

/** Clauses a fair contract of this type would have, and this one does not. */
function Missing({ review }: { review: ReviewResult }) {
  if (review.missing_clauses.length === 0) return null;
  return (
    <section aria-labelledby="missing-title" className="border-t border-paper-line px-5 py-section">
      <h2 id="missing-title" className="label text-ink-soft">
        Not in this contract
      </h2>
      <ul className="mt-inner space-y-5">
        {review.missing_clauses.map((m) => (
          <li key={m.clause_type + m.title} className="flex gap-3">
            {/* Lucide "file-question" (ISC): a page with something missing. */}
            <svg role="img" aria-label="Missing from the contract" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mt-0.5 shrink-0 text-desk">
              <path d="M12 17h.01" />
              <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z" />
              <path d="M9.1 9a3 3 0 0 1 5.82 1c0 2-3 3-3 3" />
            </svg>
            <div className="min-w-0">
              <h3 className="font-semibold leading-snug">{m.title}</h3>
              <p className="mt-1 text-sm text-ink-soft">{m.why_it_matters}</p>
              <p className="mt-2 border-l-4 border-ink bg-paper-soft px-3 py-2 text-sm font-medium">
                <span className="label block text-ink">
                  Ask for<span className="sr-only">:</span>
                </span>
                {m.suggested_ask}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The technical facts, shown only inside "How this was checked". */
function facts(review: ReviewResult): TraceFact[] {
  const p = review.provenance;
  const cost = formatCost(review.metrics.cost_usd_est);
  return [
    p?.model ? { term: "AI model", value: aiVersion(p.model), title: p.model } : null,
    p?.kb_version ? { term: "Knowledge library", value: p.kb_version } : null,
    p?.prompt_version ? { term: "Instructions", value: p.prompt_version } : null,
    p?.app_version ? { term: "App version", value: p.app_version } : null,
    cost ? { term: "Estimated cost", value: cost } : null,
  ].filter((f): f is TraceFact => f !== null);
}

/** How the review was made, in plain words. Versions and steps sit inside "How this was checked". */
function Provenance({ review, onPick }: { review: ReviewResult; onPick: (id: string) => void }) {
  const dropped = review.metrics.dropped_unverified ?? 0;
  return (
    <>
      <Trace steps={review.trace ?? []} seconds={formatSeconds(review.metrics.latency_ms)} facts={facts(review)} findings={review.findings} removed={review.removed} metrics={review.metrics} onPick={onPick} />
      <LeftOut review={review} />
      <History review={review} />
      <footer className="border-t border-paper-line px-5 py-section">
        <p className="meta text-ink-mute">
          Checked with AI on {formatDate(review.created_at)} · {formatSeconds(review.metrics.latency_ms)}. Every quote is checked against the
          contract’s own words{dropped > 0 && `; ${dropped} unconfirmed ${dropped === 1 ? "point was" : "points were"} left out`}.
        </p>
        <p className="mt-2 text-sm text-ink-soft">{review.disclaimer}</p>
      </footer>
    </>
  );
}

/** Export, and delete for the person who ran it or a workspace owner/admin. Signed-in only. */
function Manage({ review }: { review: ReviewResult }) {
  const s = useSession();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<"" | "export" | "delete">("");
  const [error, setError] = useState<string | null>(null);
  if (!s.user) return null;
  const mayDelete = canManage(s) || review.created_by === s.user.uid;
  return (
    <section aria-label="This review" className="border-t border-paper-line px-5 py-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn-line !min-h-[44px] px-4"
          disabled={busy !== ""}
          onClick={async () => {
            setBusy("export");
            setError(null);
            await exportReview(review.id, review.filename).catch((e: Error) => setError(e.message));
            setBusy("");
          }}
        >
          {busy === "export" ? "Exporting…" : "Export the review"}
        </button>
        {mayDelete &&
          (confirm ? (
            <>
              <button
                type="button"
                className="btn !min-h-[44px] bg-high px-4 text-white hover:bg-[#8f1e18]"
                disabled={busy !== ""}
                onClick={async () => {
                  setBusy("delete");
                  setError(null);
                  try {
                    await deleteReview(review.id);
                    navigate("/library");
                  } catch (e) {
                    setError((e as Error).message);
                    setBusy("");
                  }
                }}
              >
                {busy === "delete" ? "Deleting…" : "Delete this review"}
              </button>
              <button type="button" className="btn-line !min-h-[44px] px-4" onClick={() => setConfirm(false)}>
                Keep it
              </button>
            </>
          ) : (
            <button type="button" className="inline-flex min-h-[44px] items-center px-2 font-semibold text-high underline underline-offset-4" onClick={() => setConfirm(true)}>
              Delete…
            </button>
          ))}
      </div>
      {confirm && <p className="mt-2 text-sm text-ink-soft">The review, its uploaded file and its search entries are removed. This cannot be undone.</p>}
      {error && <p role="alert" className="mt-2 font-medium text-high">{error}</p>}
    </section>
  );
}
