import { useEffect, useState } from "react";
import { adminActivity, adminOverview, adminUsers } from "../api";
import ActivityList from "../components/ActivityList";
import OverviewPanels from "../components/OverviewPanels";
import { formatDate } from "../format";
import type { AdminUserRow, AuditEvent, Overview } from "../types";
import { useSession } from "../useSession";

/** Platform admin: aggregates and metadata across every workspace, never contract text. */
export default function Admin() {
  const s = useSession();
  const [days, setDays] = useState(30);
  const [o, setO] = useState<Overview | null>(null);
  const [users, setUsers] = useState<AdminUserRow[] | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = !!s.user?.platform_admin;

  useEffect(() => {
    if (!allowed) return;
    setO(null);
    adminOverview(days).then(setO).catch((e: Error) => setError(e.message));
  }, [allowed, days]);
  useEffect(() => {
    if (!allowed) return;
    adminUsers().then(setUsers).catch(() => setUsers([]));
    adminActivity(100).then(setEvents).catch(() => setEvents([]));
  }, [allowed]);

  if (s.loading) return <p className="px-5 py-16 text-center text-desk-text" role="status">Loading…</p>;
  if (!allowed)
    return (
      <div className="mx-auto max-w-xl px-5 py-16 text-white">
        <h1 className="display text-2xl">Admin</h1>
        <p className="mt-3 text-desk-text">This page is for the people who run Contract Co-pilot.</p>
      </div>
    );

  const names = Object.fromEntries((users ?? []).map((u) => [u.uid, u.name ?? u.email]));

  return (
    <div className="mx-auto max-w-[1240px] px-5 pb-20 pt-10 sm:px-8 sm:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="display mt-2 text-2xl text-white sm:text-2xl">Usage across workspaces</h1>
          <p className="mt-2 max-w-2xl text-desk-text">Counts and timings only. Contract text stays inside each workspace.</p>
        </div>
        <div role="group" aria-label="Period" className="flex gap-1.5">
          {[7, 30, 90].map((d) => (
            <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)} className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-3 text-sm ${days === d ? "border-white bg-white font-semibold text-ink" : "border-desk-line text-white hover:border-desk-text"}`}>
              {d} days
            </button>
          ))}
        </div>
      </div>

      {error && <p role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">{error}</p>}
      <div className="mt-7">{o ? <OverviewPanels o={o} days={days} admin /> : <p role="status" className="text-desk-text">Loading…</p>}</div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[3fr_2fr]">
        <section aria-labelledby="users-title" className="sheet min-w-0 overflow-hidden">
          <h2 id="users-title" className="label border-b border-paper-line px-5 py-3 text-ink-soft">People{users ? `, ${users.length}` : ""}</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead className="label text-ink-soft">
                <tr className="border-b border-paper-line">
                  <th scope="col" className="px-5 py-2 font-[inherit]">Person</th>
                  <th scope="col" className="px-3 py-2 font-[inherit]">Workspace</th>
                  <th scope="col" className="px-3 py-2 text-right font-[inherit]">Reviews</th>
                  <th scope="col" className="px-5 py-2 font-[inherit]">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-paper-line">
                {(users ?? []).map((u) => (
                  <tr key={u.uid}>
                    <td className="px-5 py-2.5">
                      <span className="block font-semibold text-ink">{u.name ?? "—"}</span>
                      <span className="block break-words text-ink-soft">{u.email}</span>
                    </td>
                    <td className="px-3 py-2.5 text-ink">{u.workspaces.map((w) => `${w.name} (${w.role})`).join(", ")}</td>
                    <td className="px-3 py-2.5 text-right font-semibold text-ink">{u.review_count}</td>
                    <td className="px-5 py-2.5 text-ink-soft">{u.last_seen_at ? formatDate(u.last_seen_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section aria-labelledby="all-activity" className="sheet min-w-0 px-5 py-4">
          <h2 id="all-activity" className="label text-ink-soft">Activity, all workspaces</h2>
          <div className="mt-3 max-h-[560px] overflow-y-auto pr-1">
            {events ? <ActivityList events={events.slice(0, 40)} names={names} showWorkspace technical /> : <p role="status" className="text-ink-soft">Loading…</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
