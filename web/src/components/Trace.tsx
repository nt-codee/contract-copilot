import type { TraceAction, TraceStep } from "../types";

/** What each kind of step looked at, in the reader's terms. */
export const ACTION_LABEL: Record<TraceAction, string> = {
  read_contract: "Read",
  search_law: "Law",
  search_playbook: "Usual terms",
  search_company: "Your rules",
  read_page: "Re-read",
  draft_findings: "Wrote",
  verify: "Checked",
};

function Glyph({ a }: { a: TraceAction }) {
  const p = { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": true, fill: "none", stroke: "currentColor", strokeWidth: 1.6 } as const;
  switch (a) {
    case "read_contract":
    case "read_page":
      return (
        <svg {...p}>
          <rect x="2.5" y="1.5" width="9" height="11" rx="1" />
          <path d="M4.5 5h5M4.5 7.5h5M4.5 10h3" />
        </svg>
      );
    case "search_law":
      return (
        <svg {...p}>
          <path d="M7 1.5v11M3 12.5h8M2 4.5h10M4 4.5 2.2 8.5h3.6zM10 4.5l-1.8 4h3.6z" />
        </svg>
      );
    case "search_company":
      return (
        <svg {...p}>
          <rect x="2" y="2" width="10" height="10" rx="1" />
          <path d="M4.5 7l1.8 1.8L9.5 5.5" />
        </svg>
      );
    case "verify":
      return (
        <svg {...p}>
          <path d="M2.5 7.5 5.5 10.5 11.5 3.5" />
        </svg>
      );
    default:
      return (
        <svg {...p}>
          <circle cx="7" cy="7" r="4.5" />
        </svg>
      );
  }
}

/** The check trail, as a short timeline inside a disclosure. */
export default function Trace({ steps, seconds }: { steps: TraceStep[]; seconds: string }) {
  if (steps.length === 0) return null;
  return (
    <details className="group border-t border-paper-line">
      <summary className="flex min-h-[52px] cursor-pointer list-none items-center justify-between gap-3 px-5 py-3 hover:bg-paper-soft [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block font-semibold text-ink">How this was checked</span>
          <span className="tab text-ink-mute">
            {steps.length} steps · {seconds}
          </span>
        </span>
        <span aria-hidden="true" className="text-ink-mute transition-transform group-open:rotate-180">
          ▾
        </span>
      </summary>
      <ol className="relative px-5 pb-5">
        {steps.map((s, i) => (
          <li key={s.step} className="trace-step relative flex gap-3 pb-3 last:pb-0" style={{ ["--i" as string]: i }}>
            {i < steps.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[13px] top-7 w-px bg-paper-line" />}
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-paper-line bg-white text-desk">
              <Glyph a={s.action} />
            </span>
            <span className="min-w-0 pt-0.5">
              <span className="tab text-ink-mute">
                {ACTION_LABEL[s.action]}
                {s.ms != null && <span className="normal-case"> · {(s.ms / 1000).toFixed(1)} s</span>}
              </span>
              <span className="block text-[0.95rem] leading-snug text-ink">{s.summary}</span>
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}
