import { lazy, Suspense, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listActivity, listMembers, workspaceOverview } from "../api";
import { canManage } from "../session";
import type { AuditEvent, Overview, ReviewSummary } from "../types";
import { useSession } from "../useSession";
import ActivityList from "./ActivityList";
import YourContracts from "./YourContracts";

const OverviewPanels = lazy(() => import("./OverviewPanels"));

/** Home for a signed-in user: their workspace at a glance, then the company view. */
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
    listActivity(20).then((e) => live && setEvents(e)).catch(() => live && setEvents([]));
    listMembers(ws.id)
      .then((ms) => live && setNames(Object.fromEntries(ms.map((m) => [m.uid, m.uid === s.user?.uid ? "You" : m.name ?? m.email]))))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [ws?.id, s.user?.uid]);

  const first = (s.user?.name ?? "").split(" ")[0];

  return (
    <div>
      <section aria-labelledby="dash-title" className="mx-auto max-w-[1240px] px-5 pb-6 pt-8 sm:px-8 sm:pt-10">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div className="text-white">
            <p className="tab text-desk-text">{ws?.name}</p>
            <h1 id="dash-title" className="display mt-2 text-[2rem] sm:text-[2.6rem]">
              {first ? `Welcome back, ${first}` : "Your workspace"}
            </h1>
          </div>
          <div className="flex flex-wrap gap-3">
            <button type="button" className="btn-go btn-flag !min-h-[52px] !px-6" onClick={onUpload}>
              Review a contract
            </button>
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
            <p className="text-[1.1rem] font-semibold text-ink">No contracts in this workspace yet.</p>
            <p className="mt-1 text-ink-soft">Upload the next vendor contract you are asked to sign. Its deadlines and risks will collect here.</p>
          </div>
        </section>
      )}

      <section aria-labelledby="company-title" className="mx-auto mt-8 max-w-[1240px] px-5 pb-16 sm:px-8">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <h2 id="company-title" className="text-[1.25rem] font-bold text-white">
              {ws?.kind === "company" ? "Across the company · last 30 days" : "Last 30 days"}
            </h2>
            <div className="mt-3">
              <Suspense fallback={<p className="text-desk-text">Loading…</p>}>{o ? <OverviewPanels o={o} days={30} admin={false} /> : <p role="status" className="text-desk-text">Loading…</p>}</Suspense>
            </div>
          </div>
          <aside aria-labelledby="recent-title" className="min-w-0">
            <h2 id="recent-title" className="text-[1.25rem] font-bold text-white">
              Recent activity
            </h2>
            <div className="sheet mt-3 px-5 py-5">
              {events ? <ActivityList events={events.slice(0, 12)} names={names} /> : <p role="status" className="text-ink-soft">Loading…</p>}
              <Link to="/activity" className="mt-4 inline-flex min-h-[44px] items-center font-semibold text-desk underline underline-offset-4 hover:text-ink">
                All activity <span aria-hidden="true">→</span>
              </Link>
              {canManage(s) && (
                <Link to="/workspace" className="ml-5 inline-flex min-h-[44px] items-center font-semibold text-desk underline underline-offset-4 hover:text-ink">
                  Invite people
                </Link>
              )}
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}
