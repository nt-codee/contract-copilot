import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { draftNegotiation, getReview, pdfUrl } from "../api";
import DraftDialog from "../components/DraftDialog";
import FindingItem from "../components/FindingItem";
import Reader from "../components/Reader";
import Trace from "../components/Trace";
import type { Marked, ReaderState } from "../components/Reader";
import { RISK_BG, RISK_TEXT, RiskShape } from "../components/Risk";
import { RISK_LABEL, RISK_ORDER, contractTypeLabel, formatCost, formatSeconds, languageLabel } from "../format";
import type { Box } from "../locate";
import type { NegotiationDraft, ReviewResult, Risk } from "../types";
import { useMedia } from "../useMedia";

export default function Review() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
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

  const itemRefs = useRef(new Map<string, HTMLButtonElement>());

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
    if (review) document.title = `${review.filename} · Contract Co-pilot`;
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

  /** From the list: select and show it on the page. */
  function pick(fid: string) {
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

  /** From the page: select it in the list. */
  function selectFromPage(fid: string) {
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
        <h1 className="display text-[2rem]">This review could not be opened</h1>
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
      <h2 id="findings-title" className="tab border-b border-paper-line px-5 pb-2 pt-5 text-ink-mute">
        {items.length === 0 ? "Nothing flagged" : `What to look at, worst first`}
      </h2>
      {items.length === 0 ? (
        <p className="px-5 py-4 text-ink-soft">No clause in this contract needs pushing back on.</p>
      ) : (
        <ol>
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
              opensPage={!wide}
            />
          ))}
        </ol>
      )}
    </section>
  );

  const verdict = (
    <Verdict review={review} items={items}>
      <button type="button" className="btn-ink w-full !min-h-[52px] text-[1.05rem]" onClick={openDraft} disabled={drafting} aria-describedby={draftError ? "draft-error" : undefined}>
        {drafting ? "Writing your reply…" : draft ? "Open my reply to the vendor" : "Draft my reply to the vendor"}
        {!drafting && <span aria-hidden="true">→</span>}
      </button>
      {draftError && (
        <p id="draft-error" role="alert" className="mt-2 font-medium text-high">
          {draftError}
        </p>
      )}
    </Verdict>
  );

  const tail = <Tail review={review} />;
  const dialog = <DraftDialog draft={draft} open={dialogOpen} items={items} onClose={() => setDialogOpen(false)} />;

  if (wide)
    return (
      <div className="grid h-[calc(100vh-60px)] grid-cols-[minmax(0,1fr)_minmax(400px,32vw)]">
        <h1 className="sr-only">Review of {review.filename}</h1>
        <section aria-label="The contract" className="min-h-0">
          {reader(false)}
        </section>
        <aside aria-label="Findings" className="min-h-0 overflow-y-auto bg-paper shadow-page">
          {verdict}
          {list}
          {tail}
        </aside>
        {dialog}
      </div>
    );

  // Narrow screens: the list first; a finding opens its page.
  const open = items.find((m) => m.f.id === pageView);
  return (
    <div>
      <h1 className="sr-only">Review of {review.filename}</h1>
      {open ? (
        <div>
          <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-desk-deep px-3 py-2">
            <button
              type="button"
              className="btn-desk !min-h-[44px] !px-3"
              onClick={() => {
                const back = pageView;
                setPageView(null);
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
                    <span className="tab px-1 text-desk-text">
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
          <section aria-label={`Finding ${open.n}`} className="fixed inset-x-0 bottom-0 z-10 max-h-[46vh] overflow-y-auto bg-paper shadow-lift">
            <ol>
              <FindingItem m={open} reviewId={review.id} open locating={locating} onPick={() => undefined} />
            </ol>
          </section>
        </div>
      ) : (
        <div className="bg-paper">
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

function Verdict({ review, items, children }: { review: ReviewResult; items: Marked[]; children: ReactNode }) {
  const v = review.verdict;
  const counts: Record<Risk, number> = { high: 0, medium: 0, low: 0 };
  items.forEach((m) => (counts[m.f.risk] += 1));
  return (
    <section aria-labelledby="verdict-title" className="border-b border-paper-line px-5 pb-5 pt-5">
      <p className="tab break-all text-ink-mute">
        <Link to="/library" className="underline underline-offset-4 hover:text-ink">
          Library
        </Link>{" "}
        / {review.filename}
      </p>
      <p className={`tab mt-3 inline-flex items-center gap-2 rounded-[3px] px-2.5 py-1.5 !text-[0.95rem] ${RISK_BG[v.overall_risk]} ${RISK_TEXT[v.overall_risk]}`}>
        <RiskShape risk={v.overall_risk} size={13} />
        Overall: {RISK_LABEL[v.overall_risk]}
      </p>
      <h2 id="verdict-title" className="display mt-3 text-[1.4rem] !leading-[1.12] text-ink sm:text-[1.5rem]">
        {v.headline}
      </h2>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1" aria-label="Findings by risk">
        {(["high", "medium", "low"] as Risk[]).map((r) => (
          <li key={r} className={`tab inline-flex items-center gap-1.5 !text-[0.9rem] ${counts[r] ? RISK_TEXT[r] : "text-ink-mute"}`}>
            <RiskShape risk={r} size={10} />
            {counts[r]} {r}
          </li>
        ))}
        <li className="tab !text-[0.9rem] text-ink-mute">
          {contractTypeLabel(review.contract_type)} · {languageLabel(review.language)} · {review.pages} {review.pages === 1 ? "page" : "pages"}
          {lawLabel(review.jurisdiction) && ` · ${lawLabel(review.jurisdiction)}`}
        </li>
      </ul>
      <div className="mt-3.5">{children}</div>
    </section>
  );
}

function Tail({ review }: { review: ReviewResult }) {
  const cost = formatCost(review.metrics.cost_usd_est);
  const dropped = review.metrics.dropped_unverified ?? 0;
  return (
    <>
      <Trace steps={review.trace ?? []} seconds={formatSeconds(review.metrics.latency_ms)} />
      <section aria-labelledby="short-title" className="border-t border-paper-line px-5 py-5">
        <h2 id="short-title" className="tab text-ink-mute">
          In short
        </h2>
        <ul className="mt-2 space-y-1.5 text-[0.95rem] text-ink-soft">
          {review.verdict.summary.map((line) => (
            <li key={line} className="flex gap-2.5">
              <span aria-hidden="true" className="mt-[0.6em] h-[2px] w-2.5 shrink-0 bg-ink-mute" />
              <span>{line}</span>
            </li>
          ))}
        </ul>
        {review.parties.length > 0 && <p className="mt-3 text-[0.9rem] text-ink-mute">Between {review.parties.join(" and ")}</p>}
      </section>
      {review.missing_clauses.length > 0 && (
        <section aria-labelledby="missing-title" className="border-t border-paper-line px-5 py-5">
          <h2 id="missing-title" className="tab text-ink-mute">
            Not in this contract
          </h2>
          <ul className="mt-3 space-y-4">
            {review.missing_clauses.map((m) => (
              <li key={m.clause_type + m.title} className="flex gap-3">
                <span aria-hidden="true" className="w-[2.6rem] shrink-0 text-center font-doc text-[1.6rem] font-bold leading-none text-desk">
                  ‸
                </span>
                <div>
                  <h3 className="font-semibold leading-snug">{m.title}</h3>
                  <p className="mt-1 text-[0.95rem] text-ink-soft">{m.why_it_matters}</p>
                  <p className="mt-2 border-l-4 border-ink bg-paper-soft px-3 py-2 text-[0.95rem] font-medium">
                    <span className="tab block text-ink">Ask for</span>
                    {m.suggested_ask}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      <footer className="border-t border-paper-line px-5 py-5 text-[0.9rem] text-ink-mute">
        <p>
          Reviewed in {formatSeconds(review.metrics.latency_ms)}
          {cost && <> · est. cost {cost}</>} · every quote checked against the contract’s own words
          {dropped > 0 && <> · {dropped} unconfirmed {dropped === 1 ? "point" : "points"} left out</>}
        </p>
        <p className="mt-2 text-[0.95rem] text-ink-soft">{review.disclaimer}</p>
      </footer>
    </>
  );
}
