import { Link } from "react-router-dom";
import { daysUntil, parseDay } from "../dates";
import { formatDate } from "../format";
import type { ReviewSummary, Risk } from "../types";
import { DeadlineLink } from "./KeyDates";
import { reviewTitle } from "../titles";
import { RISK_TEXT, RiskLabel, RiskShape } from "./Risk";

/** For a returning user: what is on file, what is due next, what was reviewed lately. */
export default function YourContracts({ reviews, label }: { reviews: ReviewSummary[]; label?: string }) {
  if (reviews.length === 0) return null;
  const counts: Record<Risk, number> = { high: 0, medium: 0, low: 0 };
  reviews.forEach((r) => (counts[r.overall_risk] += 1));
  const upcoming = reviews
    .filter((r) => r.next_deadline?.date && (daysUntil(r.next_deadline.date) ?? -1) >= 0)
    .sort((a, b) => parseDay(a.next_deadline!.date)!.getTime() - parseDay(b.next_deadline!.date)!.getTime())
    .slice(0, 3);
  const recent = reviews.slice(0, 3);

  return (
    <section aria-labelledby="yours-title" className="border-b border-white/5 bg-desk-deep/70">
      <div className="mx-auto grid max-w-[1240px] gap-6 px-5 py-6 sm:px-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,5fr)_minmax(0,4fr)] lg:gap-8">
        <div className="text-white">
          <h2 id="yours-title" className="label text-desk-text">
            {label ?? "Your contracts"}
          </h2>
          <p className="mt-1 text-xl font-bold leading-tight [font-stretch:110%]">
            {reviews.length} reviewed
          </p>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1" aria-label="By overall risk">
            {(["high", "medium", "low"] as Risk[]).map((r) => (
              <li key={r} className="tab inline-flex items-center gap-1.5 text-sm text-white">
                <span className={r === "high" ? "text-[#FF8A7E]" : r === "medium" ? "text-medium-box" : "text-[#6FD3A4]"}>
                  <RiskShape risk={r} size={10} />
                </span>
                {counts[r]} {r}
              </li>
            ))}
          </ul>
          <Link to="/library" className="mt-2 inline-flex min-h-[44px] items-center gap-1 text-sm font-semibold text-white underline underline-offset-4">
            All contracts
          </Link>
        </div>

        <div className="sheet px-4 py-3">
          <h3 className="label text-ink-soft">Coming up</h3>
          {upcoming.length === 0 ? (
            <p className="mt-2 text-sm text-ink-soft">No deadlines in the contracts you have reviewed.</p>
          ) : (
            <ol className="mt-1 divide-y divide-paper-line">
              {upcoming.map((r) => (
                <li key={r.id} className="py-2">
                  <p className="meta line-clamp-2 text-ink-mute">{reviewTitle(r)}</p>
                  <DeadlineLink d={r.next_deadline!} reviewId={r.id} />
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="sheet px-4 py-3">
          <h3 className="label text-ink-soft">Reviewed lately</h3>
          <ul className="mt-1 divide-y divide-paper-line">
            {recent.map((r) => (
              <li key={r.id}>
                <Link to={`/reviews/${r.id}`} className="flex min-h-[44px] items-center justify-between gap-3 py-2 hover:underline">
                  <span className="min-w-0">
                    <span className="line-clamp-2 text-sm font-semibold leading-snug text-ink">{reviewTitle(r)}</span>
                    <span className="meta block text-ink-mute">{formatDate(r.created_at)}</span>
                  </span>
                  <span className={`shrink-0 whitespace-nowrap ${RISK_TEXT[r.overall_risk]}`}>
                    <RiskLabel risk={r.overall_risk} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
