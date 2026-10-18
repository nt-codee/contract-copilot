import { Link } from "react-router-dom";
import type { TraceAction, TraceDetail, TraceStep } from "../types";

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

const PLACE: Record<string, string> = { my: "Malaysian", sg: "Singapore" };
const TYPE: Record<string, string> = { nda: "NDAs", service_agreement: "service agreements" };

function parse(d?: string | null): TraceDetail | null {
  if (!d) return null;
  try {
    const x = JSON.parse(d) as Partial<TraceDetail>;
    return x && typeof x === "object" && "tool" in x ? { tool: String(x.tool), args: x.args ?? {}, results: x.results ?? [], cited: x.cited ?? [] } : null;
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

function Glyph({ a }: { a: TraceAction }) {
  const p = { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": true, fill: "none", stroke: "currentColor", strokeWidth: 1.6 } as const;
  if (a === "read_contract" || a === "read_page")
    return (
      <svg {...p}>
        <rect x="2.5" y="1.5" width="9" height="11" rx="1" />
        <path d="M4.5 5h5M4.5 7.5h5M4.5 10h3" />
      </svg>
    );
  if (a === "search_law" || a === "search_playbook" || a === "search_company")
    return (
      <svg {...p}>
        <circle cx="6" cy="6" r="4" />
        <path d="M9 9l3.5 3.5" />
      </svg>
    );
  if (a === "verify")
    return (
      <svg {...p}>
        <path d="M2.5 7.5 5.5 10.5 11.5 3.5" />
      </svg>
    );
  return (
    <svg {...p}>
      <path d="M3 11l1-3 6-6 2 2-6 6z" />
    </svg>
  );
}

/** The agent's work: what it looked up, what it found, which of those it used. */
export default function Trace({ steps, seconds }: { steps: TraceStep[]; seconds: string }) {
  if (steps.length === 0) return null;
  const lookups = steps.filter((s) => parse(s.detail)?.results.length).length;
  const used = new Set(steps.flatMap((s) => parse(s.detail)?.cited ?? [])).size;
  return (
    <details className="group border-t border-paper-line">
      <summary className="flex min-h-[52px] cursor-pointer list-none items-center justify-between gap-3 px-5 py-3 hover:bg-paper-soft [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block font-semibold text-ink">How this was checked</span>
          <span className="tab text-ink-mute">
            {steps.length} steps{lookups ? ` · ${lookups} lookups · ${used} sources used` : ""} · {seconds}
          </span>
        </span>
        <span aria-hidden="true" className="text-ink-mute group-open:rotate-180">
          ▾
        </span>
      </summary>
      <ol className="relative px-5 pb-5">
        {steps.map((s, i) => {
          const d = parse(s.detail);
          const cited = new Set(d?.cited ?? []);
          return (
            <li key={s.step} className="relative flex gap-3 pb-4 last:pb-0">
              {i < steps.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[13px] top-7 w-px bg-paper-line" />}
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-paper-line bg-white text-desk">
                <Glyph a={s.action} />
              </span>
              <span className="min-w-0 flex-1 pt-0.5">
                <span className="tab text-ink-mute">
                  {ACTION_LABEL[s.action]}
                  {s.ms != null && <span className="normal-case"> · {(s.ms / 1000).toFixed(1)} s</span>}
                </span>
                <span className="block text-[0.95rem] leading-snug text-ink">{s.summary}</span>
                {d && asked(d.args) && (
                  <span className="mt-1 block text-[0.88rem] text-ink-soft">
                    Looked up {asked(d.args)}
                  </span>
                )}
                {d && d.results.length > 0 && (
                  <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="What it found">
                    {d.results.map((r) => {
                      const on = cited.has(r.ref);
                      const to = link(r.ref, r.kind);
                      const chip = (
                        <>
                          {on && (
                            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" className="shrink-0">
                              <path d="M2 6.5 4.8 9.2 10 3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                            </svg>
                          )}
                          <span className="line-clamp-2">{r.title}</span>
                          <span className="sr-only">{on ? " (used in a finding)" : " (found, not used)"}</span>
                        </>
                      );
                      const cls = `inline-flex min-h-[44px] max-w-full items-center gap-1.5 rounded-[3px] border px-2 py-1 text-left text-[0.82rem] leading-tight ${
                        on ? "border-ink bg-ink text-white" : "border-dashed border-ink-mute bg-white text-ink-soft"
                      }`;
                      return (
                        <li key={r.ref} className="max-w-full">
                          {to ? (
                            <Link to={to} className={`${cls} hover:underline`}>
                              {chip}
                            </Link>
                          ) : (
                            <span className={cls}>{chip}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="px-5 pb-5 text-[0.85rem] text-ink-mute">
        <span className="mr-1.5 inline-block rounded-[2px] bg-ink px-1 text-white">✓</span>used in a finding
        <span className="ml-4 mr-1.5 inline-block rounded-[2px] border border-dashed border-ink-mute px-1">&nbsp;&nbsp;</span>found, not used
      </p>
    </details>
  );
}
