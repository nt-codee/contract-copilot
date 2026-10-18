import { Link } from "react-router-dom";
import { aiVersion } from "../format";
import { reviewTitle } from "../titles";
import type { AuditAction, AuditEvent } from "../types";

const VERB: Record<AuditAction, string> = {
  signed_in: "Signed in",
  uploaded_contract: "Uploaded a contract",
  reviewed: "Reviewed",
  re_reviewed: "Reviewed again",
  drafted_reply: "Drafted a reply to the vendor",
  viewed_review: "Opened",
  deleted_review: "Deleted a review",
  uploaded_policy: "Added a company policy",
  deleted_policy: "Removed a company policy",
  exported: "Exported",
  deleted_my_data: "Deleted their data",
};

/** Margin tab per kind of action, so the trail scans like a marked-up file. */
function tone(a: AuditAction): string {
  if (a === "reviewed" || a === "re_reviewed") return "bg-desk text-white";
  if (a === "drafted_reply") return "bg-ink text-white";
  if (a.startsWith("deleted")) return "bg-high text-white";
  if (a.includes("policy")) return "bg-medium-box text-ink";
  return "bg-paper-soft text-ink border border-paper-line";
}
const TAB: Record<AuditAction, string> = {
  signed_in: "In",
  uploaded_contract: "Up",
  reviewed: "Rev",
  re_reviewed: "Rev",
  drafted_reply: "Reply",
  viewed_review: "View",
  deleted_review: "Del",
  uploaded_policy: "Rule",
  deleted_policy: "Rule",
  exported: "Out",
  deleted_my_data: "Del",
};

function when(iso: string): string {
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function day(iso: string): string {
  const d = new Date(iso);
  const t = new Date();
  const y = new Date(t.getFullYear(), t.getMonth(), t.getDate() - 1);
  if (d.toDateString() === t.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

function facts(e: AuditEvent, technical: boolean): string[] {
  const d = e.detail ?? {};
  const out: string[] = [];
  if (typeof d.findings === "number") out.push(`${d.findings} findings`);
  if (typeof d.added === "number" && d.added) out.push(`${d.added} new`);
  if (typeof d.removed === "number" && d.removed) out.push(`${d.removed} gone`);
  if (typeof d.asks === "number") out.push(`${d.asks} asks`);
  if (typeof d.latency_ms === "number") out.push(`${Math.round(d.latency_ms / 1000)} s`);
  if (technical && typeof d.model === "string") out.push(`AI ${aiVersion(String(d.model))}`);
  if (technical && typeof d.kb_version === "string") out.push(`knowledge ${String(d.kb_version).slice(0, 10)}`);
  return out;
}

export interface Names {
  [uid: string]: string;
}

/** The audit trail, grouped by day. */
/** `technical`: also show the AI version and knowledge library id (Admin only; elsewhere they are system words). */
export default function ActivityList({ events, names, showWorkspace = false, technical = false }: { events: AuditEvent[]; names: Names; showWorkspace?: boolean; technical?: boolean }) {
  if (events.length === 0) return <p className="text-ink-soft">Nothing has happened in this workspace yet.</p>;
  const groups: [string, AuditEvent[]][] = [];
  for (const e of events) {
    const k = day(e.at);
    const g = groups.find((x) => x[0] === k);
    if (g) g[1].push(e);
    else groups.push([k, [e]]);
  }
  return (
    <div className="space-y-6">
      {groups.map(([label, list]) => (
        <section key={label} aria-label={label}>
          <h3 className="label text-ink-soft">{label}</h3>
          <ol className="mt-2">
            {list.map((e, i) => {
              const who = e.actor === "demo" ? "Sample workspace" : names[e.actor] ?? "Someone";
              const linkable = e.target_id && ["reviewed", "re_reviewed", "drafted_reply", "viewed_review", "exported", "uploaded_contract"].includes(e.action);
              return (
                <li key={e.id} className="relative grid grid-cols-[3.4rem_minmax(0,1fr)] gap-x-3 pb-4 last:pb-0">
                  {i < list.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[1.7rem] top-8 w-px bg-paper-line" />}
                  <span aria-hidden="true" className={`tab relative inline-flex h-7 items-center justify-center rounded-[3px] text-xs ${tone(e.action)}`}>
                    {TAB[e.action]}
                  </span>
                  <span className="min-w-0 pt-0.5 leading-snug">
                    <span className="text-ink">
                      <span className="font-semibold">{who}</span> {VERB[e.action].toLowerCase()}
                      {e.target_label && (
                        <>
                          {" "}
                          {linkable ? (
                            <Link to={`/reviews/${e.target_id}`} className="break-words font-semibold text-desk underline underline-offset-4 hover:text-ink">
                              {reviewTitle({ filename: e.target_label })}
                            </Link>
                          ) : (
                            <span className="break-words font-semibold">{reviewTitle({ filename: e.target_label })}</span>
                          )}
                        </>
                      )}
                    </span>
                    <span className="meta mt-0.5 block text-ink-mute" data-system={technical ? "" : undefined}>
                      <span>{when(e.at)}</span>
                      {facts(e, technical).map((f) => (
                        <span key={f}>, {f}</span>
                      ))}
                      {showWorkspace && e.workspace_id && <span>, {e.workspace_id}</span>}
                    </span>
                  </span>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
