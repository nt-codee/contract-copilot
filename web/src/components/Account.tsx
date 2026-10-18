import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteMyData } from "../api";
import { signIn, signOut, switchWorkspace } from "../session";
import { useSession } from "../useSession";

function Avatar({ name, photo, size = 32 }: { name?: string | null; photo?: string | null; size?: number }) {
  const initials = (name ?? "?")
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
  return photo ? (
    <img src={photo} alt="" width={size} height={size} referrerPolicy="no-referrer" className="shrink-0 rounded-full" />
  ) : (
    <span aria-hidden="true" style={{ width: size, height: size }} className="grid shrink-0 place-items-center rounded-full bg-white text-[0.8rem] font-bold text-desk">
      {initials}
    </span>
  );
}

export function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path fill="#4285F4" d="M17.6 9.2c0-.6-.1-1.2-.2-1.8H9v3.4h4.8a4.1 4.1 0 0 1-1.8 2.7v2.2h2.9c1.7-1.6 2.7-3.9 2.7-6.5z" />
      <path fill="#34A853" d="M9 18c2.4 0 4.5-.8 6-2.2l-2.9-2.2c-.8.5-1.8.9-3.1.9-2.4 0-4.4-1.6-5.1-3.8H.9v2.3A9 9 0 0 0 9 18z" />
      <path fill="#FBBC05" d="M3.9 10.7a5.4 5.4 0 0 1 0-3.4V5H.9a9 9 0 0 0 0 8z" />
      <path fill="#EA4335" d="M9 3.6c1.3 0 2.5.5 3.5 1.4l2.6-2.6A9 9 0 0 0 .9 5l3 2.3C4.6 5.1 6.6 3.6 9 3.6z" />
    </svg>
  );
}

/** "Sign in with Google", in our button style. */
export function SignInButton({ className = "", label = "Sign in with Google" }: { className?: string; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await signIn();
          } catch (e) {
            const code = (e as { code?: string }).code ?? "";
            if (!/popup-closed|cancelled-popup/.test(code)) setError("Sign-in did not finish. Try again.");
          } finally {
            setBusy(false);
          }
        }}
        aria-label={label ? undefined : "Sign in with Google"}
        title={label ? undefined : "Sign in with Google"}
        className={`btn border border-ink/20 bg-white text-ink hover:bg-paper-soft ${className}`}
      >
        <GoogleMark />
        {label && (busy ? "Signing in…" : label)}
      </button>
      {error && (
        <span role="alert" className="mt-1 text-[0.9rem] font-medium text-[#FFB1A8]">
          {error}
        </span>
      )}
    </span>
  );
}

/** Header: the active workspace (with a switcher) and the account menu. */
/** The account menu. `slim`: avatar only. `compact`: the phone top bar (menu drops down). */
export default function Account({ slim = false, compact = false }: { slim?: boolean; compact?: boolean }) {
  const s = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [delError, setDelError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  useEffect(() => {
    const d = dialogRef.current;
    if (!d) return;
    if (confirm && !d.open) d.showModal();
    if (!confirm && d.open) d.close();
  }, [confirm]);

  if (s.loading) return <span className="h-8 w-8" aria-hidden="true" />;

  if (!s.user)
    return (
      <>
        {compact && s.available ? <SignInButton className="!min-h-[40px] !px-3 text-[0.92rem]" label="Sign in" /> : compact ? <span className="w-11" /> : null}
        {done && (
          <span role="status" className="sr-only">
            {done}
          </span>
        )}
      </>
    );

  const list = s.user.workspaces ?? [];
  const ws = s.workspace?.workspace;

  async function doDelete() {
    setDeleting(true);
    try {
      const res = await deleteMyData();
      const n = Object.values(res.deleted ?? {}).reduce((a, b) => a + (Number(b) || 0), 0);
      setConfirm(false);
      await signOut();
      setDone(`Your data was deleted (${n} items).`);
      navigate("/");
    } catch (e) {
      setDeleting(false);
      setDelError(e instanceof Error ? e.message : "Your data was not deleted. Try again.");
    }
  }

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((o) => !o)}
        className={`flex min-h-[44px] w-full items-center gap-2.5 rounded-[3px] px-1.5 text-left text-white hover:bg-white/10 ${slim ? "justify-center" : ""}`}
      >
        <Avatar name={s.user.name} photo={s.user.photo_url} />
        <span className={slim ? "hidden" : "min-w-0 flex-1"}>
          <span className="block max-w-[11rem] truncate text-[0.88rem] font-semibold leading-tight">{ws?.name ?? "Your workspace"}</span>
          <span className="tab block !text-[0.68rem] text-desk-text">{s.workspace?.role}</span>
        </span>
        {!slim && <span aria-hidden="true" className="text-desk-text">▴</span>}
        <span className="sr-only">Account menu for {s.user.name ?? s.user.email}, workspace {ws?.name}</span>
      </button>

      {open && (
        <div role="menu" className={`absolute z-40 w-[18rem] ${compact ? "right-0 top-full mt-1" : "bottom-full left-0 mb-2"} rounded-[3px] bg-paper py-2 text-ink shadow-lift`}>
          <div className="border-b border-paper-line px-4 pb-3 pt-1">
            <p className="truncate font-semibold">{s.user.name}</p>
            <p className="truncate text-[0.9rem] text-ink-soft">{s.user.email}</p>
          </div>
          {list.length > 1 && (
            <div className="border-b border-paper-line py-1" role="group" aria-label="Workspaces">
              <p className="tab px-4 pt-2 text-ink-mute">Workspace</p>
              {list.map((w) => (
                <button
                  key={w.workspace.id}
                  role="menuitemradio"
                  aria-checked={w.workspace.id === ws?.id}
                  onClick={() => {
                    switchWorkspace(w.workspace.id);
                    setOpen(false);
                    navigate("/");
                  }}
                  className="flex min-h-[44px] w-full items-center justify-between gap-2 px-4 text-left hover:bg-paper-soft"
                >
                  <span className="truncate">{w.workspace.name}</span>
                  {w.workspace.id === ws?.id && <span aria-hidden="true">✓</span>}
                </button>
              ))}
            </div>
          )}
          <div className="my-1 border-t border-paper-line" />
          <button role="menuitem" onClick={() => { setOpen(false); setDelError(null); setConfirm(true); }} className="flex min-h-[44px] w-full items-center px-4 text-left text-high hover:bg-high-tint">
            Delete my data
          </button>
          <button role="menuitem" onClick={() => { setOpen(false); void signOut(); navigate("/"); }} className="flex min-h-[44px] w-full items-center px-4 text-left hover:bg-paper-soft">
            Sign out
          </button>
        </div>
      )}

      <dialog ref={dialogRef} onClose={() => setConfirm(false)} aria-labelledby="del-title" className="w-[min(30rem,calc(100vw-32px))] rounded-[3px] bg-paper p-0 shadow-lift">
        <div className="px-6 py-6">
          <h2 id="del-title" className="display text-[1.5rem] text-ink">Delete my data?</h2>
          <p className="mt-3 text-ink">This removes everything for your account: your uploads, your reviews, your company documents and your activity. It cannot be undone.</p>
          <p className="mt-2 text-[0.95rem] text-ink-soft">Workspaces you share with others keep the reviews other people ran.</p>
          {delError && <p role="alert" className="mt-4 rounded-[3px] bg-high-tint px-3 py-2 font-medium text-high">{delError}</p>}
          <div className="mt-6 flex flex-wrap gap-3">
            <button type="button" className="btn bg-high text-white hover:bg-[#8f1e18]" onClick={() => void doDelete()} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete everything"}
            </button>
            <button type="button" className="btn-line" onClick={() => setConfirm(false)}>
              Keep my data
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
