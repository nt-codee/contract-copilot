import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listActivity, listMembers, workspaceOverview } from "../api";
import { daysUntil } from "../dates";
import { formatDate } from "../format";
import { canManage, readOnlyReason } from "../session";
import type { AuditEvent, Overview, ReviewSummary } from "../types";
import { useSession } from "../useSession";
import ActivityList from "./ActivityList";
import { Stat } from "./Charts";
import YourContracts from "./YourContracts";


/** Home for a signed-in user: calm. What is on file, what is due, what happened. Charts live on the Workspace page. */
export default function Dashboard({ reviews, onUpload, onSample }: { reviews: ReviewSummary[]; onUpload: () => void; onSample: (() => void) | null }) {
  const s = useSession();
  const ws = s.workspace?.workspace;
  const [o, setO] = useState<Overview | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!ws) return;
    let live = true;
    workspaceOverview(ws.id, 30).then((x) => live && setO(x)).catch(() => undefined);
    listActivity(5).then((e) => live && setEvents(e)).catch(() => live && setEvents([]));
    listMembers(ws.id)
      .then((ms) => live && setNames(Object.fromEntries(ms.map((m) => [m.uid, m.uid === s.user?.uid ? "You" : m.name ?? m.email]))))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [ws?.id, s.user?.uid]);

  const first = (s.user?.name ?? "").split(" ")[0];
  // The same rule as "Coming up": every contract whose next date is today or later.
  const upcoming = reviews
    .filter((r) => (daysUntil(r.next_deadline?.date) ?? -1) >= 0)
    .sort((a, b) => (daysUntil(a.next_deadline!.date) ?? 0) - (daysUntil(b.next_deadline!.date) ?? 0));
  const next = upcoming[0]?.next_deadline;

  return (
    <div className="pb-16">
      <section aria-labelledby="dash-title" className="mx-auto max-w-[1240px] px-5 pb-6 pt-8 sm:px-8 sm:pt-10">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div className="min-w-0 text-white">
            <p className="page-eyebrow">{ws?.name}</p>
            <h1 id="dash-title" className="display mt-2 text-2xl">
              {s.guest && !s.user ? "Demo company (read-only)" : first ? `Welcome back, ${first}` : "Your workspace"}
            </h1>
          </div>
          <div className="flex flex-wrap gap-3">
            {!readOnlyReason(s) && (
              <button type="button" className="btn-go btn-flag !min-h-[52px] !px-6" onClick={onUpload}>
                Review a contract
              </button>
            )}
            {onSample && (
              <button type="button" className="btn-desk !min-h-[52px] !px-5" onClick={onSample}>
                Try a sample
              </button>
            )}
          </div>
        </div>
      </section>

      {reviews.length > 0 ? (
        <YourContracts reviews={reviews} />
      ) : (
        <section className="mx-auto max-w-[1240px] px-5 sm:px-8">
          <div className="sheet px-6 py-6">
            <p className="text-lg font-semibold text-ink">No contracts in this workspace yet.</p>
            <p className="mt-1 text-ink-soft">Upload the next vendor contract you are asked to sign. Its deadlines and risks will collect here.</p>
          </div>
        </section>
      )}

      <section aria-label="This month" className="mx-auto mt-6 max-w-[1240px] px-5 sm:px-8">
        <div className="grid gap-4 sm:grid-cols-2">
          <Stat label="Review runs, last 30 days" value={o ? String(o.reviews) : "…"} note={o ? "by everyone in the workspace, re-reviews and samples included" : undefined} />
          <Stat label="Dates coming up" value={String(upcoming.length)} note={next?.date ? `the next is ${formatDate(next.date)}${upcoming.length > 3 ? ", the 3 soonest are under Coming up" : ""}` : "nothing due"} />
        </div>
      </section>

      <section aria-labelledby="recent-title" className="mx-auto mt-8 max-w-[1240px] px-5 sm:px-8">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="recent-title" className="text-xl font-bold text-white">
            Recent activity
          </h2>
          <span className="flex gap-5">
            {(canManage(s) || s.guest) && (
              <Link to="/workspace" className="inline-flex min-h-[44px] items-center font-semibold text-white underline underline-offset-4">
                Workspace overview
              </Link>
            )}
            <Link to="/activity" className="inline-flex min-h-[44px] items-center font-semibold text-white underline underline-offset-4">
              See all
            </Link>
          </span>
        </div>
        <div className="sheet mt-3 px-5 py-5 sm:px-7">
          {events ? <ActivityList events={events.slice(0, 5)} names={names} /> : <p role="status" className="text-ink-soft">Loading…</p>}
        </div>
      </section>
    </div>
  );
}
