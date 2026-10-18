import { useEffect, useState } from "react";
import { listActivity, listMembers } from "../api";
import ActivityList from "../components/ActivityList";
import type { Names } from "../components/ActivityList";
import { SignInButton } from "../components/Account";
import { canManage } from "../session";
import type { AuditEvent } from "../types";
import { useSession } from "../useSession";

export default function Activity() {
  const s = useSession();
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [names, setNames] = useState<Names>({});
  const [error, setError] = useState<string | null>(null);
  const ws = s.workspace?.workspace;

  useEffect(() => {
    if (s.loading) return;
    let live = true;
    setEvents(null);
    listActivity(200)
      .then((e) => live && setEvents(e))
      .catch((e: Error) => live && setError(e.message));
    if (ws && s.user)
      listMembers(ws.id)
        .then((ms) => live && setNames(Object.fromEntries(ms.map((m) => [m.uid, m.uid === s.user!.uid ? "You" : m.name ?? m.email]))))
        .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [s.loading, ws?.id, s.user]);

  return (
    <div className="mx-auto max-w-[860px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="tab text-desk-text">{ws?.name ?? "Sample workspace"}</p>
      <h1 className="display mt-3 text-[2.1rem] text-white sm:text-[2.6rem]">Activity</h1>
      <p className="mt-3 max-w-2xl text-[1.05rem] text-desk-text">
        {s.user
          ? canManage(s)
            ? "Everything that happened in this workspace: who reviewed what, with which AI version and knowledge library. Nothing here can be edited."
            : "What you have done in this workspace. Nothing here can be edited."
          : "What has happened in the sample workspace. Sign in to keep a trail of your own reviews."}
      </p>
      {!s.user && !s.loading && s.available && (
        <div className="mt-5">
          <SignInButton />
        </div>
      )}
      <div className="sheet mt-7 px-5 py-6 sm:px-8">
        {error ? (
          <p role="alert" className="font-medium text-high">The activity could not be loaded. {error}</p>
        ) : events === null ? (
          <p className="text-ink-soft" role="status">Loading…</p>
        ) : (
          <ActivityList events={events} names={names} />
        )}
      </div>
    </div>
  );
}
