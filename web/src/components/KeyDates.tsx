import { Link } from "react-router-dom";
import { buildIcs, daysUntil, downloadIcs, formatDay, inWords, isDeadline, sortDates } from "../dates";
import type { KeyDate, ReviewResult } from "../types";
import type { Marked } from "./Reader";
import { FlagTab } from "./Risk";

const KIND_LABEL: Record<KeyDate["kind"], string> = {
  start: "Starts",
  end: "Ends",
  renewal: "Renews",
  notice_deadline: "Last day to cancel",
  payment: "Payment",
  price_change: "Price change",
  other: "Date",
};

function calendarItems(review: ReviewResult, dates: KeyDate[]) {
  const link = `${window.location.origin}/reviews/${review.id}`;
  return dates
    .filter((d) => d.date)
    .map((d, i) => ({
      uid: `${review.id}-${d.kind}-${d.date}-${i}`,
      date: d.date!,
      title: `${isDeadline(d) ? "Deadline: " : ""}${d.label} (${review.filename})`,
      description: [d.rule, `Page ${d.page} of ${review.filename}.`, link].filter(Boolean).join("\n"),
      url: link,
      alarms: isDeadline(d) ? [30, 7] : [7],
    }));
}

export function addToCalendar(review: ReviewResult, dates: KeyDate[]) {
  const name = review.filename.replace(/\.pdf$/i, "").replace(/[^\w.-]+/g, "-");
  downloadIcs(`${name}-dates.ics`, buildIcs(calendarItems(review, dates)));
}

/** The deadline that matters most: the next notice deadline, else the next dated item. */
export function headlineDate(dates: KeyDate[]): KeyDate | null {
  const future = sortDates(dates).filter((d) => (daysUntil(d.date) ?? -1) >= 0);
  return future.find(isDeadline) ?? dates.find(isDeadline) ?? future[0] ?? null;
}

/** One line under the verdict: the date you must act by. */
export function DeadlineLine({ review, items }: { review: ReviewResult; items: Marked[] }) {
  const d = headlineDate(review.key_dates ?? []);
  if (!d || !d.date) return null;
  const days = daysUntil(d.date);
  const flag = items.find((m) => m.f.id === d.finding_id);
  return (
    <a
      href="#dates-title"
      className={`mt-inner flex items-center gap-3 rounded-[3px] border-l-4 px-3 py-2 hover:brightness-[0.98] due-${tone(days)}`}
    >
      <CalendarGlyph date={d.date} />
      <span className="min-w-0 text-sm leading-snug">
        <span className="block font-semibold text-ink">
          {isDeadline(d) ? "Cancel by" : KIND_LABEL[d.kind]} {formatDay(d.date)}
        </span>
        <span className="block text-ink-soft">
          {d.label}
          {days != null && `, ${inWords(days)}`}
        </span>
      </span>
      {flag && <FlagTab n={flag.n} risk={flag.f.risk} className="ml-auto !rounded-[3px]" />}
    </a>
  );
}

function CalendarGlyph({ date }: { date: string }) {
  const [y, m, dd] = date.split("-");
  const month = new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-GB", { month: "short" });
  return (
    <span aria-hidden="true" className="flex w-11 shrink-0 flex-col overflow-hidden rounded-[3px] border border-ink/30 bg-white text-center">
      <span className="tab bg-ink py-0.5 text-xs text-white">{month}</span>
      <span className="py-0.5 text-lg font-bold leading-none text-ink">{Number(dd)}</span>
    </span>
  );
}

/** Urgency tone for a date: amber within 30 days, quiet once passed, ink otherwise. Never red:
 *  red is reserved for high-risk findings. */
function tone(days: number | null): "soon" | "past" | "later" {
  if (days != null && days < 0) return "past";
  if (days != null && days <= 30) return "soon";
  return "later";
}

function DateRow({ d, last, items, onPick, isMain }: { d: KeyDate; last: boolean; items: Marked[]; onPick: (id: string) => void; isMain: boolean }) {
  const days = daysUntil(d.date);
  const flag = items.find((m) => m.f.id === d.finding_id);
  const t = tone(days);
  return (
    <li className="relative grid grid-cols-[5.5rem_1rem_minmax(0,1fr)] gap-x-2 pb-4 last:pb-0">
      <span className={`meta pt-0.5 text-right ${t === "past" ? "text-ink-mute" : t === "soon" ? "font-semibold text-medium" : "text-ink"}`}>
        {d.date ? formatDay(d.date) : "No set date"}
      </span>
      <span aria-hidden="true" className="relative flex justify-center">
        {!last && <span className="absolute bottom-[-1rem] top-3 w-px bg-paper-line" />}
        <span className={`relative mt-1 h-3 w-3 rounded-full border-2 ${isMain ? (t === "soon" ? "border-medium-box bg-medium-box" : "border-ink bg-ink") : t === "past" ? "border-ink-mute bg-white" : "border-ink bg-white"}`} />
      </span>
      <span className={`min-w-0 ${isMain ? `-mt-1.5 rounded-[3px] border-l-4 px-2.5 py-2 due-${t}` : ""}`}>
        <span className="meta block text-ink-mute">
          {KIND_LABEL[d.kind]}
          {days != null && `, ${inWords(days)}`}
        </span>
        <span className={`block leading-snug ${isMain ? "font-semibold text-ink" : "text-ink"}`}>{d.label}</span>
        {d.rule && <span className="mt-0.5 block text-sm text-ink-soft">{d.rule}</span>}
        <span className="mt-1 flex flex-wrap items-center gap-2">
          {flag ? (
            <button
              type="button"
              onClick={() => onPick(flag.f.id)}
              className="inline-flex min-h-[44px] items-center gap-2 text-sm font-semibold text-accent underline underline-offset-4 hover:text-accent-deep"
            >
              <FlagTab n={flag.n} risk={flag.f.risk} className="!h-6 !min-w-[2rem] !rounded-[3px]" />
              See finding {flag.n}, page {d.page}
            </button>
          ) : (
            <span className="meta text-ink-mute">Page {d.page}</span>
          )}
        </span>
      </span>
    </li>
  );
}

/** "Dates that matter": upcoming (and undated) first; passed dates folded under "Past dates". */
export default function KeyDates({ review, items, onPick }: { review: ReviewResult; items: Marked[]; onPick: (id: string) => void }) {
  const dates = sortDates(review.key_dates ?? []);
  if (dates.length === 0) return null;
  const main = headlineDate(dates);
  const isPast = (d: KeyDate) => (daysUntil(d.date) ?? 0) < 0;
  const upcoming = dates.filter((d) => !isPast(d));
  const past = dates.filter(isPast);
  const upcomingDated = upcoming.filter((d) => d.date);
  return (
    <section aria-labelledby="dates-title" className="border-t border-paper-line px-5 py-section">
      <div className="flex items-center justify-between gap-3">
        <h2 id="dates-title" className="label scroll-mt-4 text-ink-soft">
          Dates that matter
        </h2>
        {upcomingDated.length > 0 && (
          <button type="button" onClick={() => addToCalendar(review, upcomingDated)} className="btn-line !min-h-[44px] !px-3 text-sm">
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
              <rect x="1.5" y="2.5" width="13" height="12" rx="1.5" />
              <path d="M1.5 6h13M5 1v3M11 1v3M8 8.5v4M6 10.5h4" />
            </svg>
            Add to calendar
          </button>
        )}
      </div>
      {upcoming.length > 0 ? (
        <ol className="relative mt-inner">
          {upcoming.map((d, i) => (
            <DateRow key={`${d.kind}-${d.date}-${i}`} d={d} last={i === upcoming.length - 1} items={items} onPick={onPick} isMain={d === main} />
          ))}
        </ol>
      ) : (
        <p className="mt-inner text-sm text-ink-soft">No upcoming dates in this contract.</p>
      )}
      {upcomingDated.length > 0 && <p className="meta mt-inner text-ink-mute">The calendar file reminds you 30 and 7 days before each deadline.</p>}
      {past.length > 0 && (
        <details className="group mt-inner">
          <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 text-sm font-semibold text-ink hover:text-accent [&::-webkit-details-marker]:hidden">
            Past dates ({past.length})
            <span aria-hidden="true" className="text-ink-mute group-open:rotate-180">▾</span>
          </summary>
          <ol className="relative mt-2">
            {past.map((d, i) => (
              <DateRow key={`${d.kind}-${d.date}-${i}`} d={d} last={i === past.length - 1} items={items} onPick={onPick} isMain={false} />
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

export function DeadlineLink({ d, reviewId }: { d: KeyDate; reviewId: string }) {
  const days = daysUntil(d.date);
  return (
    <Link
      to={`/reviews/${reviewId}${d.finding_id ? `?finding=${encodeURIComponent(d.finding_id)}` : ""}`}
      className="group block text-sm leading-snug"
    >
      <span className={`meta ${tone(days) === "soon" ? "font-semibold text-medium" : "text-ink-mute"}`}>
        {d.date ? formatDay(d.date) : "No set date"}
        {days != null && `, ${inWords(days)}`}
      </span>
      <span className="block text-ink group-hover:underline">{d.label}</span>
    </Link>
  );
}
