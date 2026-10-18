import { Link } from "react-router-dom";
import { buildIcs, daysUntil, downloadIcs, formatDay, inWords, isDeadline, sortDates, urgency } from "../dates";
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
  const u = urgency(days);
  return (
    <a
      href="#dates-title"
      className={`mt-3 flex items-center gap-3 rounded-[3px] border-l-4 px-3 py-2 hover:bg-paper-soft ${u === "soon" || u === "past" ? "border-high bg-high-tint" : "border-ink bg-paper-soft"}`}
    >
      <CalendarGlyph date={d.date} />
      <span className="min-w-0 text-[0.95rem] leading-snug">
        <span className="block font-semibold text-ink">
          {isDeadline(d) ? "Cancel by" : KIND_LABEL[d.kind]} {formatDay(d.date)}
        </span>
        <span className="block text-ink-soft">
          {d.label}
          {days != null && ` · ${inWords(days)}`}
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
      <span className="tab bg-ink py-0.5 !text-[0.65rem] text-white">{month}</span>
      <span className="py-0.5 text-[1.1rem] font-bold leading-none text-ink">{Number(dd)}</span>
    </span>
  );
}

/** The "Dates that matter" timeline in the rail. */
export default function KeyDates({ review, items, onPick }: { review: ReviewResult; items: Marked[]; onPick: (id: string) => void }) {
  const dates = sortDates(review.key_dates ?? []);
  if (dates.length === 0) return null;
  const main = headlineDate(dates);
  const dated = dates.filter((d) => d.date).length;
  return (
    <section aria-labelledby="dates-title" className="border-t border-paper-line px-5 py-5">
      <div className="flex items-center justify-between gap-3">
        <h2 id="dates-title" className="tab scroll-mt-4 text-ink-mute">
          Dates that matter
        </h2>
        {dated > 0 && (
          <button type="button" onClick={() => addToCalendar(review, dates)} className="btn-line !min-h-[44px] !px-3 text-[0.92rem]">
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
              <rect x="1.5" y="2.5" width="13" height="12" rx="1.5" />
              <path d="M1.5 6h13M5 1v3M11 1v3M8 8.5v4M6 10.5h4" />
            </svg>
            Add to calendar
          </button>
        )}
      </div>
      <ol className="relative mt-4">
        {dates.map((d, i) => {
          const days = daysUntil(d.date);
          const flag = items.find((m) => m.f.id === d.finding_id);
          const isMain = d === main;
          const past = days != null && days < 0;
          return (
            <li key={`${d.kind}-${d.date}-${i}`} className="relative grid grid-cols-[5.5rem_1rem_minmax(0,1fr)] gap-x-2 pb-4 last:pb-0">
              <span className={`tab pt-0.5 text-right ${isMain ? "text-high" : past ? "text-ink-mute" : "text-ink"}`}>
                {d.date ? formatDay(d.date) : "No set date"}
              </span>
              <span aria-hidden="true" className="relative flex justify-center">
                {i < dates.length - 1 && <span className="absolute bottom-[-1rem] top-3 w-px bg-paper-line" />}
                <span className={`relative mt-1 h-3 w-3 rounded-full border-2 ${isMain ? "border-high bg-high" : past ? "border-ink-mute bg-white" : "border-ink bg-white"}`} />
              </span>
              <span className={`min-w-0 ${isMain ? "rounded-[3px] bg-high-tint px-2.5 py-2 -mt-1.5" : ""}`}>
                <span className={`tab block ${isMain ? "text-high" : "text-ink-mute"}`}>
                  {KIND_LABEL[d.kind]}
                  {days != null && <span className="normal-case"> · {inWords(days)}</span>}
                </span>
                <span className={`block leading-snug ${isMain ? "font-semibold text-ink" : "text-ink"}`}>{d.label}</span>
                {d.rule && <span className="mt-0.5 block text-[0.9rem] text-ink-soft">{d.rule}</span>}
                <span className="mt-1 flex flex-wrap items-center gap-2">
                  {flag ? (
                    <button
                      type="button"
                      onClick={() => onPick(flag.f.id)}
                      className="inline-flex min-h-[44px] items-center gap-2 text-[0.9rem] font-semibold text-desk underline underline-offset-4 hover:text-ink"
                    >
                      <FlagTab n={flag.n} risk={flag.f.risk} className="!h-6 !min-w-[2rem] !rounded-[3px]" />
                      See finding {flag.n} · page {d.page}
                    </button>
                  ) : (
                    <span className="tab text-ink-mute">Page {d.page}</span>
                  )}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
      {dated > 0 && <p className="mt-3 text-[0.88rem] text-ink-mute">The calendar file reminds you 30 and 7 days before the deadline.</p>}
    </section>
  );
}

export function DeadlineLink({ d, reviewId }: { d: KeyDate; reviewId: string }) {
  const days = daysUntil(d.date);
  const u = urgency(days);
  return (
    <Link
      to={`/reviews/${reviewId}${d.finding_id ? `?finding=${encodeURIComponent(d.finding_id)}` : ""}`}
      className="group block text-[0.95rem] leading-snug"
    >
      <span className={`tab ${u === "soon" || u === "past" ? "text-high" : "text-ink-mute"}`}>
        {d.date ? formatDay(d.date) : "No set date"}
        {days != null && <span className="normal-case"> · {inWords(days)}</span>}
      </span>
      <span className="block text-ink group-hover:underline">{d.label}</span>
    </Link>
  );
}
