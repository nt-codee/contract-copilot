import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { acceptInvite, createInvite, listMembers, removeMember, renameWorkspace, setMemberRole } from "../api";
import { SignInButton } from "../components/Account";
import { formatDate } from "../format";
import { canManage, refresh, switchWorkspace } from "../session";
import type { Invite, Member, Role } from "../types";
import { useSession } from "../useSession";

const ROLE: Record<Role, string> = { owner: "Owner", admin: "Admin", member: "Member" };

export function WorkspaceSettings() {
  const s = useSession();
  const ws = s.workspace?.workspace;
  const mine = s.workspace?.role;
  const [name, setName] = useState(ws?.name ?? "");
  const [saved, setSaved] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [inviteRole, setInviteRole] = useState<Invite["role"]>("member");
  const [copied, setCopied] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);

  useEffect(() => setName(ws?.name ?? ""), [ws?.name]);
  useEffect(() => {
    if (!ws) return;
    listMembers(ws.id).then(setMembers).catch((e: Error) => setError(e.message));
  }, [ws?.id]);

  if (s.loading) return <p className="px-5 py-16 text-center text-desk-text" role="status">Loading…</p>;
  if (!s.user || !ws || !canManage(s))
    return (
      <div className="mx-auto max-w-xl px-5 py-16 text-white">
        <h1 className="display text-[2rem]">Workspace settings</h1>
        <p className="mt-3 text-desk-text">{s.user ? "Only the workspace owner or an admin can change these settings." : "Sign in to manage your company’s workspace."}</p>
        {!s.user && <div className="mt-5"><SignInButton /></div>}
      </div>
    );

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!ws || !name.trim() || name.trim() === ws.name) return;
    try {
      await renameWorkspace(ws.id, name.trim());
      await refresh();
      setSaved("Name saved.");
    } catch (x) {
      setError((x as Error).message);
    }
  }

  async function makeInvite() {
    if (!ws) return;
    setCopied(false);
    try {
      setInvite(await createInvite(ws.id, inviteRole));
    } catch (x) {
      setError((x as Error).message);
    }
  }

  const link = invite ? `${window.location.origin}/invite/${invite.code}` : "";
  const owners = members?.filter((m) => m.role === "owner").length ?? 0;

  return (
    <div className="mx-auto max-w-[960px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="tab text-desk-text">Workspace settings</p>
      <h1 className="display mt-3 text-[2.1rem] text-white sm:text-[2.6rem]">{ws.name}</h1>
      <p className="mt-3 text-desk-text">
        Everyone here shares the same contracts, company rules and standard positions.{ws.domain ? ` People who sign in with an @${ws.domain} address join automatically.` : ""}
      </p>
      {error && <p role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">{error}</p>}

      <section aria-labelledby="name-title" className="sheet mt-7 px-5 py-6 sm:px-8">
        <h2 id="name-title" className="tab text-ink-mute">Name</h2>
        <form onSubmit={save} className="mt-2 flex flex-col gap-3 sm:flex-row">
          <label htmlFor="ws-name" className="sr-only">Workspace name</label>
          <input id="ws-name" value={name} onChange={(e) => { setName(e.target.value); setSaved(null); }} maxLength={80} className="min-h-[48px] flex-1 rounded-[3px] border border-ink/30 px-3 text-ink" />
          <button type="submit" className="btn-ink" disabled={!name.trim() || name.trim() === ws.name}>Save name</button>
        </form>
        <p role="status" className="mt-2 text-[0.92rem] font-semibold text-low">{saved}</p>
      </section>

      <section aria-labelledby="invite-title" className="sheet mt-5 px-5 py-6 sm:px-8">
        <h2 id="invite-title" className="tab text-ink-mute">Invite people</h2>
        <p className="mt-1 text-ink-soft">Make a link and send it to a colleague. It works once and expires after 7 days.</p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-[0.92rem] text-ink-soft">
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
            <input id="invite-link" readOnly value={link} onFocus={(e) => e.target.select()} className="min-h-[44px] flex-1 rounded-[3px] border border-paper-line bg-white px-3 font-mono text-[0.9rem] text-ink" />
            <button type="button" className="btn-ink" onClick={async () => { await navigator.clipboard.writeText(link).catch(() => undefined); setCopied(true); }}>
              {copied ? "Copied" : "Copy link"}
            </button>
            {invite.expires_at && <span className="tab text-ink-mute">Expires {formatDate(invite.expires_at)}</span>}
          </div>
        )}
      </section>

      <section aria-labelledby="members-title" className="sheet mt-5 overflow-hidden">
        <h2 id="members-title" className="tab border-b border-paper-line px-5 py-4 text-ink-mute sm:px-8">
          Members{members ? ` · ${members.length}` : ""}
        </h2>
        {members === null ? (
          <p className="px-5 py-4 text-ink-soft sm:px-8" role="status">Loading…</p>
        ) : (
          <ul className="divide-y divide-paper-line">
            {members.map((m) => {
              const me = m.uid === s.user!.uid;
              const lastOwner = m.role === "owner" && owners <= 1;
              const canEdit = !me && !(mine === "admin" && m.role === "owner");
              return (
                <li key={m.uid} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-8">
                  <span className="min-w-0">
                    <span className="block font-semibold text-ink">{m.name ?? m.email}{me && " (you)"}</span>
                    <span className="block break-all text-[0.92rem] text-ink-soft">{m.email}</span>
                    {m.joined_at && <span className="tab mt-0.5 block text-ink-mute">Joined {formatDate(m.joined_at)}</span>}
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    {canEdit && !lastOwner ? (
                      <label className="flex items-center gap-2">
                        <span className="sr-only">Role for {m.name ?? m.email}</span>
                        <select
                          value={m.role}
                          onChange={async (e) => {
                            const role = e.target.value as Role;
                            const updated = await setMemberRole(ws.id, m.uid, role).catch((x: Error) => { setError(x.message); return null; });
                            if (updated) setMembers((list) => list!.map((x) => (x.uid === m.uid ? updated : x)));
                          }}
                          className="min-h-[44px] rounded-[3px] border border-ink/30 bg-white px-2 text-ink"
                        >
                          {(mine === "owner" ? (["owner", "admin", "member"] as Role[]) : (["admin", "member"] as Role[])).map((r) => (
                            <option key={r} value={r}>{ROLE[r]}</option>
                          ))}
                        </select>
                      </label>
                    ) : (
                      <span className="tab rounded-[3px] bg-paper-soft px-2 py-1.5 text-ink">{ROLE[m.role]}</span>
                    )}
                    {canEdit && !lastOwner && (confirm === m.uid ? (
                      <>
                        <button type="button" className="btn !min-h-[44px] bg-high px-3 text-white" onClick={async () => {
                          await removeMember(ws.id, m.uid).catch((x: Error) => setError(x.message));
                          setMembers((list) => list!.filter((x) => x.uid !== m.uid));
                          setConfirm(null);
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
        <p className="tab text-ink-mute">Invitation</p>
        <h1 className="display mt-2 text-[1.8rem] text-ink">Join your team’s workspace</h1>
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
        <p className="mt-6 text-[0.92rem] text-ink-soft"><Link to="/" className="underline underline-offset-4">Not now</Link></p>
      </div>
    </div>
  );
}
