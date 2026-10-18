import { useEffect, useRef, useState } from "react";
import { settle } from "../motion";

/**
 * What the review does, in order, and when each usually starts (seconds).
 * The server answers in one go, so these follow typical timing; the
 * review's own record of its steps is shown on the result.
 */
const STEPS: [string, string, number][] = [
  ["Read", "Reading every page", 0],
  ["Law", "Checking Malaysian and Singapore law", 4],
  ["Your rules", "Checking your company rules", 9],
  ["Usual terms", "Comparing each clause with usual terms", 14],
  ["Checked", "Matching every quote to the contract’s own words", 24],
];
const PAGES = 4;

/** Moment 2: steps join the list one at a time; the page being read carries an edge mark. */
export default function Progress({ subject }: { subject: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const shown = STEPS.filter(([, , at]) => secs >= at).length;
  const step = shown - 1;
  // While reading, the mark moves page to page; after that it rests on the last one read.
  const reading = step === 0 ? Math.min(PAGES, secs + 1) : PAGES;

  return (
    <div className="mx-auto grid max-w-5xl items-start gap-10 px-5 py-12 sm:px-8 md:grid-cols-[minmax(0,1fr)_260px] md:py-20">
      <div className="text-white">
        <p className="tab text-desk-text">Reviewing</p>
        <h1 className="display mt-2 break-words text-[1.9rem] sm:text-[2.6rem]">{subject}</h1>
        <p className="sr-only" role="status">
          {STEPS[step][1]}
        </p>
        <ol className="mt-8">
          {STEPS.slice(0, shown).map(([tag, label, at], i) => (
            <Step key={label} tag={tag} label={label} last={i === shown - 1} done={i < step} took={i < step ? STEPS[i + 1][2] - at : secs - at} />
          ))}
        </ol>
        <p className="mt-6 text-[0.95rem] text-desk-text">Usually 20 to 60 seconds · {secs} s so far.</p>
      </div>

      <ol aria-hidden="true" className="grid grid-cols-4 gap-3 md:grid-cols-2 md:pt-10">
        {Array.from({ length: PAGES }).map((_, i) => {
          const p = i + 1;
          const now = step === 0 && p === reading;
          const read = p < reading || step > 0;
          return (
            <li key={p} className="relative">
              <div className={`aspect-[1/1.414] rounded-[2px] bg-paper p-[12%] shadow-page ${read || now ? "" : "opacity-60"}`}>
                {[100, 92, 96, 64, 0, 100, 88, 70].map((w, j) =>
                  w ? <div key={j} className="mb-[9%] h-[3px] rounded-sm bg-paper-line" style={{ width: `${w}%` }} /> : <div key={j} className="h-[6%]" />,
                )}
              </div>
              {/* The edge mark: a tab on the page being read. */}
              {now && <span className="absolute -right-1.5 top-[18%] h-[22%] w-1.5 rounded-r-[2px] bg-high-box" />}
              <span className="tab mt-1 block text-center !text-[0.7rem] text-desk-text">{p}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Step({ tag, label, last, done, took }: { tag: string; label: string; last: boolean; done: boolean; took: number }) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (ref.current) settle(ref.current, 0, 8);
  }, []);
  return (
    <li ref={ref} className="relative flex gap-3 pb-4 last:pb-0" aria-current={last ? "step" : undefined}>
      {!last && <span aria-hidden="true" className="absolute bottom-0 left-[13px] top-7 w-px bg-desk-line" />}
      <span
        aria-hidden="true"
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border text-[0.8rem] font-bold ${
          done ? "border-white bg-white text-desk" : "border-high-box bg-high-box text-white"
        }`}
      >
        {done ? "✓" : "•"}
      </span>
      <span className="min-w-0 pt-0.5">
        <span className="tab text-desk-text">
          {tag}
          <span className="normal-case"> · {took} s</span>
        </span>
        <span className={`block leading-snug ${last ? "font-semibold text-white" : "text-white"}`}>
          {label}
          {last && "…"}
          <span className="sr-only">{done ? " (done)" : " (in progress)"}</span>
        </span>
      </span>
    </li>
  );
}
