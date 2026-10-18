import { useEffect, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import { getReview, listReviews, searchContracts } from "../api";
import { FlagTab, RISK_BOX, RISK_TEXT, RiskLabel, RiskShape } from "../components/Risk";
import { excerpt, groupHits } from "../group";
import { DeadlineLink } from "../components/KeyDates";
import { parseDay } from "../dates";
import type { ContractMatches, Match } from "../group";
import { RISK_ORDER } from "../format";
import { breakable, contractTypeLabel, formatDate } from "../format";
import type { ReviewSummary, Risk, SearchHit } from "../types";
import { counterpartyName, reviewTitle } from "../titles";

type Flags = Record<string, Record<string, { n: number; risk: Risk }>>;

const EXAMPLES = ["cancel early", "price increase", "who owns the work", "renews automatically"];

function highlight(text: string, query: string): ReactNode {
  const terms = Array.from(
    new Set(
      query
        .split(/\s+/)
        .map((t) => t.replace(/[^\p{L}\p{N}]/gu, ""))
        .filter((t) => t.length >= 3),
    ),
  );
  if (terms.length === 0) return text;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "giu");
  return text.split(re).map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="bg-transparent px-0 font-bold text-inherit underline decoration-ink decoration-2 underline-offset-2">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function Counts({ c }: { c: ReviewSummary["finding_counts"] }) {
  const rs = (["high", "medium", "low"] as Risk[]).filter((r) => c[r]);
  if (rs.length === 0) return <span className="meta text-ink-mute">Nothing flagged</span>;
  return (
    <span className="flex flex-wrap gap-x-3">
      {rs.map((r) => (
        <span key={r} className={`tab inline-flex items-center gap-1 ${RISK_TEXT[r]}`}>
          <RiskShape risk={r} size={9} />
          {c[r]} {r}
        </span>
      ))}
    </span>
  );
}

export default function Library() {
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [reviews, setReviews] = useState<ReviewSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [flags, setFlags] = useState<Flags>({});
  const [sort, setSort] = useState<"deadline" | "recent" | "risk">("deadline");

  useEffect(() => {
    let live = true;
    listReviews()
      .then((r) => live && setReviews(r))
      .catch((e: Error) => live && setListError(e.message));
    return () => {
      live = false;
    };
  }, []);

  // Only contracts that can still be opened; the search index can outlive a deleted review.
  const known = reviews ? new Set(reviews.map((r) => r.id)) : null;
  const usable = (hits ?? []).filter((h) => !known || known.has(h.review_id));
  const groups: ContractMatches[] = hits ? groupHits(usable) : [];

  // Look up each matched finding's number and risk, so its flag matches the review screen.
  useEffect(() => {
    if (!reviews) return;
    const ids = [...new Set(usable.filter((h) => h.finding_id).map((h) => h.review_id))].filter((id) => !flags[id]);
    if (ids.length === 0) return;
    let live = true;
    Promise.all(ids.map((id) => getReview(id).then((r) => [id, r] as const).catch(() => null))).then((rs) => {
      if (!live) return;
      const next: Flags = {};
      for (const x of rs) {
        if (!x) continue;
        const [id, r] = x;
        next[id] = {};
        [...r.findings]
          .sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk])
          .forEach((f, i) => (next[id][f.id] = { n: i + 1, risk: f.risk }));
      }
      setFlags((cur) => ({ ...cur, ...next }));
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hits, reviews]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    void run(q);
  }

  async function run(raw: string) {
    const term = raw.trim();
    if (!term) return;
    setQ(term);
    setSearching(true);
    setSearchError(null);
    try {
      setHits(await searchContracts(term));
      setAsked(term);
    } catch (err) {
      setHits(null);
      setSearchError(err instanceof Error ? err.message : "Search did not finish. Try again.");
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1040px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <section aria-labelledby="search-title">
        <h1 id="search-title" className="display mt-3 text-2xl text-white sm:text-2xl">
          Search your contracts
        </h1>
        <p className="mt-3 max-w-2xl text-base text-desk-text">
          Describe the situation in your own words. You get the clause that answers it in each contract, with its page.
        </p>
        <form onSubmit={onSearch} role="search" className="mt-7 flex flex-col gap-3 sm:flex-row">
          <label htmlFor="q" className="sr-only">
            Words to search for
          </label>
          <input
            id="q"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="For example: what happens if we cancel early"
            autoComplete="off"
            className="min-h-[52px] flex-1 rounded-[3px] border-0 bg-white px-4 text-base text-ink placeholder:text-ink-mute"
          />
          <button type="submit" className="btn-go !min-h-[52px] sm:px-8" disabled={searching || !q.trim()}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-desk-text">Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => void run(ex)}
              className="inline-flex min-h-[44px] items-center rounded-[3px] border border-desk-line px-3 text-sm text-white hover:border-desk-text hover:bg-white/5"
            >
              {ex}
            </button>
          ))}
        </div>

        <div aria-live="polite" className="mt-8">
          {searchError && (
            <p role="alert" className="rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
              {searchError}
            </p>
          )}
          {hits && !searching && (
            <>
              <h2 className="text-lg font-bold text-white">
                {groups.length === 0
                  ? `Nothing matches “${asked}”`
                  : `Best ${groups.length === 1 ? "match" : "matches"} for “${asked}” in ${groups.length} ${groups.length === 1 ? "contract" : "contracts"}`}
              </h2>
              {groups.length === 0 && (
                <p className="mt-1 text-desk-text">Try other words, or describe the situation instead of the clause name.</p>
              )}
              <ul className="mt-4 space-y-5">
                {groups.map((g) => (
                  <li key={g.review_id}>
                    <ContractResult g={g} asked={asked} flags={flags[g.review_id] ?? {}} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </section>

      <section aria-labelledby="past-title" className="mt-14">
        <h2 id="past-title" className="text-xl font-bold text-white">
          Past reviews
        </h2>
        {listError ? (
          <p role="alert" className="mt-4 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
            {listError}
          </p>
        ) : reviews === null ? (
          <p className="mt-4 text-desk-text" role="status">
            Loading your reviews…
          </p>
        ) : reviews.length === 0 ? (
          <div className="sheet mt-4 p-6">
            <p className="text-ink-soft">No contracts reviewed yet. Reviews you run show up here.</p>
            <Link to="/" className="btn-ink mt-4">
              Review a contract
            </Link>
          </div>
        ) : (
          <>
            <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Sort contracts">
              <span className="text-sm text-desk-text">Sort by</span>
              {([["deadline", "Next deadline"], ["recent", "Most recent"], ["risk", "Highest risk"]] as const).map(([v, l]) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={sort === v}
                  onClick={() => setSort(v)}
                  className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-3 text-sm ${sort === v ? "border-white bg-white font-semibold text-ink" : "border-desk-line text-white hover:border-desk-text"}`}
                >
                  {l}
                </button>
              ))}
            </div>
            <ul className="sheet mt-3 divide-y divide-paper-line">
              {sorted(reviews, sort).map((r) => (
                <li key={r.id} className="grid gap-3 px-5 py-4 sm:grid-cols-[minmax(0,1fr)_13rem_auto] sm:items-center sm:gap-6">
                  <Link to={`/reviews/${r.id}`} className="min-w-0 hover:underline">
                    <span className="block break-words font-semibold text-ink">{reviewTitle(r)}</span>
                    <span className="mt-0.5 block text-sm text-ink-soft">{r.headline}</span>
                    <span className="meta mt-1.5 block break-words text-ink-mute">
                      {[contractTypeLabel(r.contract_type), counterpartyName(r) && `with ${counterpartyName(r)}`, formatDate(r.created_at), breakable(r.filename)].filter(Boolean).join(", ")}
                    </span>
                  </Link>
                  <div className="min-w-0 border-l-2 border-paper-line pl-3">
                    {r.next_deadline ? (
                      <DeadlineLink d={r.next_deadline} reviewId={r.id} />
                    ) : (
                      <span className="meta text-ink-mute">No deadline found</span>
                    )}
                  </div>
                  <span className="flex flex-col gap-1 sm:items-end">
                    <RiskLabel risk={r.overall_risk} />
                    <Counts c={r.finding_counts} />
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  );
}

function sorted(rs: ReviewSummary[], by: "deadline" | "recent" | "risk"): ReviewSummary[] {
  const now = Date.now();
  // Upcoming deadlines first (soonest first), then passed ones, then contracts without one.
  const t = (r: ReviewSummary) => {
    const d = parseDay(r.next_deadline?.date)?.getTime();
    if (d == null) return Infinity;
    return d >= now - 86400000 ? d : 1e15 + (now - d);
  };
  const order = { high: 0, medium: 1, low: 2 } as const;
  return [...rs].sort((a, b) =>
    by === "deadline" ? t(a) - t(b) || b.created_at.localeCompare(a.created_at)
    : by === "risk" ? order[a.overall_risk] - order[b.overall_risk] || t(a) - t(b)
    : b.created_at.localeCompare(a.created_at),
  );
}

function Excerpt({ m, asked, flag, reviewId }: { m: Match; asked: string; flag?: { n: number; risk: Risk }; reviewId: string }) {
  const box = flag ? RISK_BOX[flag.risk] : null;
  const to = `/reviews/${reviewId}${m.hit.finding_id ? `?finding=${encodeURIComponent(m.hit.finding_id)}` : ""}`;
  return (
    <div className="relative grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-3 pr-11 sm:grid-cols-[4.5rem_minmax(0,1fr)] sm:pr-14">
      <p className="meta pt-1 text-right text-ink-mute">p. {m.hit.page}</p>
      <div className="min-w-0">
        {m.title && <p className="mb-1 font-semibold leading-snug text-ink">{m.title}</p>}
        <p className="font-doc text-base leading-[1.55] text-ink">
          <span
            className="hl"
            style={box ? ({ ["--hl" as string]: box.fill, borderBottom: `2px solid ${box.edge}` } as CSSProperties) : undefined}
          >
            {highlight(excerpt(m.text, asked), asked)}
          </span>
        </p>
        <Link to={to} className="mt-1 inline-flex min-h-[44px] items-center gap-1 text-sm font-semibold text-desk underline underline-offset-4 hover:text-ink">
          {flag ? `Open finding ${flag.n} on page ${m.hit.page}` : `Open page ${m.hit.page}`}
        </Link>
      </div>
      {flag && (
        <span className="absolute right-0 top-0">
          <FlagTab n={flag.n} risk={flag.risk} className="shadow-lift" />
        </span>
      )}
    </div>
  );
}

function ContractResult({ g, asked, flags }: { g: ContractMatches; asked: string; flags: Record<string, { n: number; risk: Risk }> }) {
  const flagFor = (m: Match) => (m.hit.finding_id ? flags[m.hit.finding_id] : undefined);
  return (
    <article className="sheet overflow-hidden" aria-label={reviewTitle(g)}>
      <header className="flex items-center justify-between gap-3 border-b border-paper-line bg-paper-soft px-4 py-2.5 sm:px-5">
        <h3 className="min-w-0 break-words text-sm font-semibold text-ink">
          {reviewTitle(g)} <span className="meta font-normal text-ink-mute">{breakable(g.filename)}</span>
        </h3>
        {g.more.length > 0 && <span className="meta shrink-0 text-ink-mute">{g.more.length + 1} clauses</span>}
      </header>
      <div className="space-y-4 px-4 py-4 sm:px-5">
        <Excerpt m={g.best} asked={asked} flag={flagFor(g.best)} reviewId={g.review_id} />
        {g.more.map((m) => (
          <div key={m.hit.page + m.text.slice(0, 30)} className="border-t border-dashed border-paper-line pt-3">
            <Excerpt m={m} asked={asked} flag={flagFor(m)} reviewId={g.review_id} />
          </div>
        ))}
      </div>
    </article>
  );
}
