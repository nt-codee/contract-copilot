import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { acceptInvite, createInvite, listMembers, removeMember, renameWorkspace, setMemberRole, workspaceOverview } from "../api";
import { SignInButton } from "../components/Account";
import { formatDate } from "../format";
import { canManage, enterGuest, readOnlyReason, refresh, switchWorkspace } from "../session";
import type { Invite, Member, Overview, Role } from "../types";

const OverviewPanels = lazy(() => import("../components/OverviewPanels"));
import { useSession } from "../useSession";

interface Loaded<T> { key: string; data?: T; error?: string }

const ROLE: Record<Role, string> = { owner: "Owner", admin: "Admin", member: "Member" };

export function WorkspaceSettings() {
  const s = useSession();
  const ws = s.workspace?.workspace;
  const mine = s.workspace?.role;
  const [name, setName] = useState(ws?.name ?? "");
  const [saved, setSaved] = useState<string | null>(null);
  const [memberState, setMemberState] = useState<Loaded<Member[]>>();
  const [memberRetry, setMemberRetry] = useState(0);
  const [overviewRetry, setOverviewRetry] = useState(0);
  const [enteringDemo, setEnteringDemo] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [inviteRole, setInviteRole] = useState<Invite["role"]>("member");
  const [copied, setCopied] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "people">("overview");
  const [days, setDays] = useState(30);
  const [overviewState, setOverviewState] = useState<Loaded<Overview>>();
  const readOnly = readOnlyReason(s);
  const canView = !s.loading && !!ws && ((!s.user && s.guest) || (!!s.user && canManage(s)));
  const accessKey = canView ? `${ws!.id}|${s.user?.uid ?? "guest"}|${mine}` : "denied";
  const overviewKey = `${accessKey}|${days}|${overviewRetry}`;
  const memberKey = `${accessKey}|${memberRetry}`;
  const actionScope = `${accessKey}|${tab}`;
  const activeScope = useRef(actionScope);
  activeScope.current = actionScope;
  const o = overviewState?.key === overviewKey ? overviewState.data : undefined;
  const overviewError = overviewState?.key === overviewKey ? overviewState.error : undefined;
  const members = memberState?.key === memberKey ? memberState.data : undefined;
  const memberError = memberState?.key === memberKey ? memberState.error : undefined;

  useEffect(() => {
    setName(ws?.name ?? "");
  }, [ws?.name]);
  useEffect(() => {
    setError(null);
    setSaved(null);
    setInvite(null);
    setConfirm(null);
    setDemoError(null);
  }, [accessKey, tab]);
  useEffect(() => {
    if (!canView || !ws || tab !== "overview") return;
    let live = true;
    setOverviewState({ key: overviewKey });
    workspaceOverview(ws.id, days)
      .then((data) => { if (live) setOverviewState({ key: overviewKey, data }); })
      .catch((e: Error) => { if (live) setOverviewState({ key: overviewKey, error: e.message || "The overview could not be loaded." }); });
    return () => { live = false; };
  }, [canView, ws?.id, overviewKey, tab, days]);
  useEffect(() => {
    if (!canView || !ws || tab !== "people") return;
    let live = true;
    setMemberState({ key: memberKey });
    listMembers(ws.id)
      .then((data) => { if (live) setMemberState({ key: memberKey, data }); })
      .catch((e: Error) => { if (live) setMemberState({ key: memberKey, error: e.message || "The members could not be loaded." }); });
    return () => { live = false; };
  }, [canView, ws?.id, memberKey, tab]);

  async function viewDemo() {
    if (enteringDemo || s.user || s.guest) return;
    setEnteringDemo(true);
    setDemoError(null);
    try {
      await enterGuest(); // explicit action only; remain on /workspace
    } catch (e) {
      setDemoError(e instanceof Error ? e.message : "The demo company could not be opened. Please try again.");
    } finally {
      setEnteringDemo(false);
    }
  }

  if (s.loading) return <p className="px-5 py-16 text-center text-desk-text" role="status">Loading…</p>;
  if (!canView || !ws)
    return (
      <div className="mx-auto max-w-xl px-5 py-16 text-white">
        <h1 className="display text-2xl">Workspace settings</h1>
        {s.user ? (
          <p className="mt-3 text-desk-text">Only the workspace owner or an admin can view these settings. Your contracts are available in the <Link to="/library" className="underline underline-offset-4">Library</Link>.</p>
        ) : (
          <>
            <p className="mt-3 text-desk-text">Sign in to manage your own workspace, with your company’s contracts, rules and people.</p>
            {s.available && <div className="mt-5"><SignInButton /></div>}
            <section aria-labelledby="demo-workspace-title" className="mt-8 rounded-[3px] border border-desk-line px-5 py-5">
              <h2 id="demo-workspace-title" className="text-lg font-semibold">Explore the demo company</h2>
              <p className="mt-2 text-desk-text">Kopi Kita Sdn Bhd is a made-up company. View its workspace overview and sample team without signing in. The demo is read-only; your own workspace stays separate.</p>
              <button type="button" className="btn-go mt-4 !min-h-[44px]" disabled={enteringDemo} onClick={() => void viewDemo()}>
                {enteringDemo ? "Opening the demo…" : "View the read-only demo"}
              </button>
              {enteringDemo && <p role="status" className="mt-2 text-sm text-desk-text">Opening the demo company’s workspace…</p>}
              {demoError && <p role="alert" className="mt-3 rounded-[3px] bg-high-tint px-3 py-2 text-high">{demoError}</p>}
            </section>
          </>
        )}
      </div>
    );

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!ws || !name.trim() || name.trim() === ws.name) return;
    setError(null);
    try {
      await renameWorkspace(ws.id, name.trim());
      if (activeScope.current !== actionScope) return;
      await refresh();
      if (activeScope.current === actionScope) setSaved("Name saved.");
    } catch (x) {
      if (activeScope.current === actionScope) setError((x as Error).message);
    }
  }

  async function makeInvite() {
    if (!ws) return;
    setCopied(false);
    setError(null);
    try {
      const created = await createInvite(ws.id, inviteRole);
      if (activeScope.current === actionScope) setInvite(created);
    } catch (x) {
      if (activeScope.current === actionScope) setError((x as Error).message);
    }
  }

  const link = invite ? `${window.location.origin}/invite/${invite.code}` : "";
  const owners = members?.filter((m) => m.role === "owner").length ?? 0;

  return (
    <div className="mx-auto max-w-[1240px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="page-eyebrow">{ws.name}</p>
      <h1 className="display mt-3 text-2xl text-white sm:text-2xl">Workspace settings</h1>
      <p className="mt-3 text-desk-text">
        Everyone here shares the same contracts, company rules and standard positions.{ws.domain ? ` People who sign in with an @${ws.domain} address join automatically.` : ""}
      </p>
      {error && <p role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">{error}</p>}
      {readOnly && <p className="mt-5 rounded-[3px] border border-desk-line px-4 py-3 text-desk-text">{readOnly}</p>}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Workspace" className="flex gap-1.5">
          {([["overview", "Overview"], ["people", "People and settings"]] as const).map(([v, l]) => (
            <button key={v} type="button" role="tab" id={`tab-${v}`} aria-selected={tab === v} aria-controls={`panel-${v}`} onClick={() => { if (v !== tab) { setTab(v); setOverviewState(undefined); setMemberState(undefined); } }} className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-4 text-sm ${tab === v ? "border-white bg-white font-semibold text-ink" : "border-desk-line text-white hover:border-desk-text"}`}>
              {l}
            </button>
          ))}
        </div>
        {tab === "overview" && (
          <div role="group" aria-label="Period" className="flex gap-1.5">
            {[7, 30, 90].map((d) => (
              <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)} className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-3 text-sm ${days === d ? "border-white bg-white font-semibold text-ink" : "border-desk-line text-white hover:border-desk-text"}`}>
                {d} days
              </button>
            ))}
          </div>
        )}
      </div>

      {tab === "overview" ? (
        <div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" className="mt-5">
          {overviewError ? (
            <div className="sheet px-5 py-5">
              <p role="alert" className="text-high">The workspace overview could not be loaded. {overviewError}</p>
              <button type="button" className="btn-line mt-3 !min-h-[44px]" onClick={() => setOverviewRetry((n) => n + 1)}>Try the overview again</button>
            </div>
          ) : (
            <Suspense fallback={<OverviewSkeleton />}>
              {o ? <OverviewPanels o={o} days={days} admin={false} /> : <OverviewSkeleton />}
            </Suspense>
          )}
        </div>
      ) : (
      <div id="panel-people" role="tabpanel" aria-labelledby="tab-people">
      {!readOnly && <section aria-labelledby="name-title" className="sheet mt-5 px-5 py-6 sm:px-8">
        <h2 id="name-title" className="label text-ink-soft">Name</h2>
        <form onSubmit={save} className="mt-2 flex flex-col gap-3 sm:flex-row">
          <label htmlFor="ws-name" className="sr-only">Workspace name</label>
          <input id="ws-name" value={name} onChange={(e) => { setName(e.target.value); setSaved(null); }} maxLength={80} className="min-h-[48px] flex-1 rounded-[3px] border border-ink/30 px-3 text-ink" />
          <button type="submit" className="btn-ink" disabled={!name.trim() || name.trim() === ws.name}>Save name</button>
        </form>
        <p role="status" className="mt-2 text-sm font-semibold text-low">{saved}</p>
      </section>}

      {!readOnly && <section aria-labelledby="invite-title" className="sheet mt-5 px-5 py-6 sm:px-8">
        <h2 id="invite-title" className="label text-ink-soft">Invite people</h2>
        <p className="mt-1 text-ink-soft">Make a link and send it to a colleague. It works once and expires after 7 days.</p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-sm text-ink-soft">
            They join as
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Invite["role"])} className="mt-1 min-h-[44px] rounded-[3px] border border-ink/30 bg-white px-2 text-ink">
              <option value="member">Member: reviews contracts</option>
              <option value="admin">Admin: also manages people and rules</option>
            </select>
          </label>
          <button type="button" onClick={() => void makeInvite()} className="btn-go btn-flag border border-ink/20">Create invite link</button>
        </div>
        {invite && (
          <div className="mt-4 flex flex-col gap-2 rounded-[3px] border border-paper-line bg-paper-soft p-3 sm:flex-row sm:items-center">
            <label htmlFor="invite-link" className="sr-only">Invite link</label>
            <input id="invite-link" readOnly value={link} onFocus={(e) => e.target.select()} className="min-h-[44px] flex-1 rounded-[3px] border border-paper-line bg-white px-3 font-mono text-sm text-ink" />
            <button type="button" className="btn-ink" onClick={async () => { await navigator.clipboard.writeText(link).catch(() => undefined); setCopied(true); }}>
              {copied ? "Copied" : "Copy link"}
            </button>
            {invite.expires_at && <span className="meta text-ink-mute">Expires {formatDate(invite.expires_at)}</span>}
          </div>
        )}
      </section>}

      <section aria-labelledby="members-title" className="sheet mt-5 overflow-hidden">
        <h2 id="members-title" className="label border-b border-paper-line px-5 py-4 text-ink-soft sm:px-8">
          Members{members ? `, ${members.length}` : ""}
        </h2>
        {memberError ? (
          <div className="px-5 py-4 sm:px-8">
            <p role="alert" className="text-high">The workspace members could not be loaded. {memberError}</p>
            <button type="button" className="btn-line mt-3 !min-h-[44px]" onClick={() => setMemberRetry((n) => n + 1)}>Try loading members again</button>
          </div>
        ) : !members ? (
          <p className="px-5 py-4 text-ink-soft sm:px-8" role="status">Loading…</p>
        ) : members.length === 0 ? (
          <p className="px-5 py-4 text-ink-soft sm:px-8">No members to show in this workspace.</p>
        ) : (
          <ul className="divide-y divide-paper-line">
            {members.map((m) => {
              const me = m.uid === s.user?.uid;
              const lastOwner = m.role === "owner" && owners <= 1;
              const canEdit = !readOnly && !me && !(mine === "admin" && m.role === "owner");
              return (
                <li key={m.uid} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-8">
                  <span className="min-w-0">
                    <span className="block font-semibold text-ink">{m.name ?? m.email}{me && " (you)"}</span>
                    <span className="block break-words text-sm text-ink-soft">{m.email}</span>
                    {m.joined_at && <span className="meta mt-0.5 block text-ink-mute">Joined {formatDate(m.joined_at)}</span>}
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    {canEdit && !lastOwner ? (
                      <label className="flex items-center gap-2">
                        <span className="sr-only">Role for {m.name ?? m.email}</span>
                        <select
                          value={m.role}
                          onChange={async (e) => {
                            const role = e.target.value as Role;
                            setError(null);
                            const updated = await setMemberRole(ws.id, m.uid, role).catch((x: Error) => { if (activeScope.current === actionScope) setError(x.message); return null; });
                            if (updated && activeScope.current === actionScope) setMemberState((state) => state?.key === memberKey ? { ...state, data: state.data?.map((x) => x.uid === m.uid ? updated : x) } : state);
                          }}
                          className="min-h-[44px] rounded-[3px] border border-ink/30 bg-white px-2 text-ink"
                        >
                          {(mine === "owner" ? (["owner", "admin", "member"] as Role[]) : (["admin", "member"] as Role[])).map((r) => (
                            <option key={r} value={r}>{ROLE[r]}</option>
                          ))}
                        </select>
                      </label>
                    ) : (
                      <span className="meta rounded-[3px] bg-paper-soft px-2 py-1.5 text-ink">{ROLE[m.role]}</span>
                    )}
                    {canEdit && !lastOwner && (confirm === m.uid ? (
                      <>
                        <button type="button" className="btn !min-h-[44px] bg-high px-3 text-white" onClick={async () => {
                          setError(null);
                          try {
                            await removeMember(ws.id, m.uid);
                            if (activeScope.current !== actionScope) return;
                            setMemberState((state) => state?.key === memberKey ? { ...state, data: state.data?.filter((x) => x.uid !== m.uid) } : state);
                            setConfirm(null);
                          } catch (x) {
                            if (activeScope.current === actionScope) setError((x as Error).message);
                          }
                        }}>Remove</button>
                        <button type="button" className="btn-line !min-h-[44px] px-3" onClick={() => setConfirm(null)}>Keep</button>
                      </>
                    ) : (
                      <button type="button" className="btn-line !min-h-[44px] px-3" onClick={() => setConfirm(m.uid)} aria-label={`Remove ${m.name ?? m.email}`}>Remove</button>
                    ))}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      </div>
      )}
    </div>
  );
}

export function AcceptInvite() {
  const { code = "" } = useParams();
  const s = useSession();
  const navigate = useNavigate();
  const [state, setState] = useState<"idle" | "joining" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function join() {
    setState("joining");
    try {
      const ref = await acceptInvite(code);
      await refresh();
      switchWorkspace(ref.workspace.id);
      navigate("/");
    } catch (e) {
      setError((e as Error).message);
      setState("error");
    }
  }

  return (
    <div className="mx-auto max-w-[640px] px-5 py-16">
      <div className="sheet px-6 py-8 sm:px-10">
        <p className="meta text-ink-mute">Invitation</p>
        <h1 className="display mt-2 text-2xl text-ink">Join your team’s workspace</h1>
        <p className="mt-3 text-ink-soft">You will share contracts, company rules and standard positions with the people already there.</p>
        <div className="mt-6">
          {s.loading ? (
            <p role="status" className="text-ink-soft">Checking who you are…</p>
          ) : !s.user ? (
            <SignInButton label="Sign in with Google to join" />
          ) : (
            <button type="button" className="btn-ink" onClick={() => void join()} disabled={state === "joining"}>
              {state === "joining" ? "Joining…" : `Join as ${s.user.email}`}
            </button>
          )}
        </div>
        {error && <p role="alert" className="mt-4 font-medium text-high">{error}</p>}
        <p className="mt-6 text-sm text-ink-soft"><Link to="/" className="underline underline-offset-4">Not now</Link></p>
      </div>
    </div>
  );
}

/** Placeholder sheets at the size of the real panels, so the page does not jump when the numbers arrive. */
function OverviewSkeleton() {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">Loading the overview…</span>
      <div aria-hidden="true" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-[106px] rounded-[3px] bg-white/10" />
        ))}
      </div>
      <div aria-hidden="true" className="mt-6 grid gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="h-[280px] rounded-[3px] bg-white/10" />
        ))}
      </div>
    </div>
  );
}
