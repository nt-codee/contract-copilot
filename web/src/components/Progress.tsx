import { useEffect, useState } from "react";

/** Generic stages and when each usually starts (seconds). Labels only; the server reports the real trail. */
const STEPS: [string, number][] = [
  ["Reading every page", 0],
  ["Checking Malaysian and Singapore law", 4],
  ["Checking your company rules", 10],
  ["Comparing each clause with usual terms", 16],
  ["Matching every quote to the contract’s own words", 26],
];

/** Illustrative marks that appear on the page as the review goes on. */
const MARKS: { top: number; h: number; at: number; tone: string }[] = [
  { top: 22, h: 9, at: 6, tone: "rgba(217,58,43,.28)" },
  { top: 47, h: 6, at: 13, tone: "rgba(233,164,0,.36)" },
  { top: 66, h: 9, at: 19, tone: "rgba(217,58,43,.28)" },
];

export default function Progress({ subject }: { subject: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const step = STEPS.reduce((cur, [, at], i) => (secs >= at ? i : cur), 0);

  return (
    <div className="mx-auto grid max-w-5xl items-center gap-10 px-5 py-12 sm:px-8 md:grid-cols-[1fr_300px] md:py-20">
      <div className="text-white">
        <p className="tab text-desk-text">Reviewing</p>
        <h1 className="display mt-2 break-words text-[1.9rem] sm:text-[2.6rem]">{subject}</h1>
        <p className="sr-only" role="status">
          {STEPS[step][0]}
        </p>
        <ol className="mt-8 space-y-1">
          {STEPS.map(([label, at], i) => {
            const state = i < step ? "done" : i === step ? "now" : "next";
            const took = i < step ? STEPS[i + 1][1] - at : null;
            return (
              <li
                key={label}
                aria-current={state === "now" ? "step" : undefined}
                className={`flex min-h-[44px] items-center gap-3 rounded-[3px] px-2 transition-colors ${
                  state === "now" ? "bg-white/10 text-white" : state === "done" ? "text-white" : "text-desk-text"
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`relative grid h-7 w-7 shrink-0 place-items-center rounded-[3px] text-[0.85rem] font-bold ${
                    state === "done" ? "bg-white text-desk" : state === "now" ? "bg-high-box text-white" : "border border-desk-line"
                  }`}
                >
                  {state === "done" ? "✓" : i + 1}
                  {state === "now" && <span className="ping absolute inset-0 rounded-[3px] border-2 border-high-box" />}
                </span>
                <span className={`flex-1 ${state === "now" ? "font-semibold" : ""}`}>
                  {label}
                  <span className="sr-only">{state === "done" ? " (done)" : state === "now" ? " (in progress)" : " (next)"}</span>
                </span>
                <span aria-hidden="true" className="tab normal-case text-desk-text">
                  {took != null ? `${took} s` : state === "now" ? `${secs - at} s` : ""}
                </span>
              </li>
            );
          })}
        </ol>
        <p className="mt-6 text-[0.95rem] text-desk-text">Usually 20 to 60 seconds · {secs} s so far.</p>
      </div>

      <div aria-hidden="true" className="sheet relative mx-auto aspect-[1/1.414] w-full max-w-[300px] overflow-hidden p-7">
        {Array.from({ length: 16 }).map((_, i) => (
          <div key={i} className="mb-3 h-[6px] rounded-sm bg-paper-line" style={{ width: `${[92, 100, 96, 70][i % 4]}%`, marginTop: i % 5 === 0 && i ? 18 : 0 }} />
        ))}
        {MARKS.map((m) =>
          secs >= m.at ? (
            <div key={m.top} className="rise absolute left-5 right-5" style={{ top: `${m.top}%`, height: `${m.h}%`, background: m.tone }} />
          ) : null,
        )}
        <div className="reading absolute inset-x-0 top-0 h-[12%] border-b-2 border-high-box bg-gradient-to-b from-transparent to-high-box/20" />
      </div>
    </div>
  );
}
