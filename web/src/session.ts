// Who is signed in, and which workspace they work in.
// Google sign-in runs on Google Cloud Identity Platform through its client SDK
// (the `firebase/auth` module), loaded only when someone signs in or has signed in
// before. Its config comes from the server at runtime, so nothing is built in.
import type { AuthConfig, User, WorkspaceRef } from "./types";

const MOCK = import.meta.env.VITE_MOCK === "1";
const WS_KEY = "cc.workspace";
const HINT_KEY = "cc.signedIn";

type AuthMod = typeof import("firebase/auth");
interface Client {
  mod: AuthMod;
  auth: import("firebase/auth").Auth;
}

export interface Session {
  /** null: a visitor in the sample workspace */
  user: User | null;
  /** the active workspace for signed-in users */
  workspace: WorkspaceRef | null;
  /** sign-in is available on this server */
  available: boolean;
  /** still finding out who this is */
  loading: boolean;
  /** browsing the read-only demo company */
  guest: boolean;
  /** the server's read-only message for guests */
  guestMessage?: string;
}

let state: Session = { user: null, workspace: null, available: MOCK, loading: true, guest: false };
const GUEST_KEY = "cc.guest";
let client: Promise<Client | null> | null = null;
const listeners = new Set<(s: Session) => void>();

function set(next: Partial<Session>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l(state));
}

export const getSession = () => state;
export function subscribe(fn: (s: Session) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function config(): Promise<AuthConfig | null> {
  try {
    const res = await fetch("/api/auth/config");
    if (!res.ok) return null;
    const c = (await res.json()) as Partial<AuthConfig>;
    return c.apiKey && c.authDomain ? (c as AuthConfig) : null;
  } catch {
    return null;
  }
}

function loadClient(): Promise<Client | null> {
  if (!client) {
    client = (async () => {
      const cfg = await config();
      if (!cfg) return null;
      const [{ initializeApp }, mod] = await Promise.all([import("firebase/app"), import("firebase/auth")]);
      const auth = mod.getAuth(initializeApp(cfg, "contract-copilot"));
      return { mod, auth };
    })().catch(() => null);
  }
  return client;
}

/** Headers for every API call: the ID token and the active workspace. */
export async function authHeaders(): Promise<Record<string, string>> {
  const h: Record<string, string> = {};
  let token: string | null = null;
  // Offline mode talks to the real server only for public files (sample PDFs): send nothing.
  if (MOCK) return h;
  if (state.guest && !state.user) {
    h["X-Guest"] = "demo";
    return h;
  }
  if (state.user) {
    const c = await loadClient();
    token = (await c?.auth.currentUser?.getIdToken()) ?? null;
  }
  if (token) h.Authorization = `Bearer ${token}`;
  if (token && state.workspace) h["X-Workspace-Id"] = state.workspace.workspace.id;
  return h;
}

function pickWorkspace(u: User): WorkspaceRef | null {
  const list = u.workspaces ?? [];
  const saved = localStorage.getItem(WS_KEY);
  return list.find((w) => w.workspace.id === saved) ?? list.find((w) => w.workspace.kind === "company") ?? list[0] ?? null;
}

async function loadMe(): Promise<void> {
  const { getMe } = await import("./api");
  const me = await getMe();
  set({ user: me, workspace: pickWorkspace(me), loading: false });
  localStorage.setItem(HINT_KEY, "1");
}

/** On page load: restore a previous sign-in without loading the sign-in client for first-time visitors. */
interface GuestInfo {
  workspace: WorkspaceRef["workspace"];
  role: string;
  read_only: boolean;
  message: string;
}

/** Enter the read-only demo company. Kept for this browser tab only. */
export async function enterGuest(): Promise<void> {
  let info: GuestInfo;
  if (MOCK) {
    info = { workspace: { id: "demo", name: "Kopi Kita Sdn Bhd", kind: "demo", member_count: 4 }, role: "viewer", read_only: true, message: "This is a read-only demo. Sign in with Google to use your own workspace." };
  } else {
    const res = await fetch("/api/guest", { headers: { "X-Guest": "demo" } });
    if (!res.ok) throw new Error("The demo company is not available right now. Try again later.");
    info = (await res.json()) as GuestInfo;
  }
  sessionStorage.setItem(GUEST_KEY, "1");
  // "viewer" is not a member role: treat the guest as a member who cannot write.
  set({ guest: true, guestMessage: info.message, workspace: { workspace: info.workspace, role: "member" }, loading: false });
}

export function exitGuest(): void {
  sessionStorage.removeItem(GUEST_KEY);
  set({ guest: false, guestMessage: undefined, workspace: null });
}

export async function start(): Promise<void> {
  if (sessionStorage.getItem(GUEST_KEY) && !localStorage.getItem(HINT_KEY)) {
    if (!MOCK) {
      const cfgRes = await fetch("/api/auth/config").catch(() => null);
      set({ available: !!cfgRes?.ok });
    }
    await enterGuest().catch(() => {
      sessionStorage.removeItem(GUEST_KEY);
      set({ loading: false });
    });
    return;
  }
  if (MOCK) {
    if (localStorage.getItem(HINT_KEY)) {
      await loadMe().catch(() => set({ loading: false }));
    } else set({ loading: false });
    return;
  }
  // Is sign-in set up here at all? (cheap, and decides whether to show the button)
  const cfgRes = await fetch("/api/auth/config").catch(() => null);
  const available = !!cfgRes?.ok;
  set({ available });
  if (!available || !localStorage.getItem(HINT_KEY)) return set({ loading: false });
  const c = await loadClient();
  if (!c) return set({ loading: false });
  c.mod.onAuthStateChanged(c.auth, (u) => {
    if (u) {
      set({ user: { uid: u.uid, email: u.email ?? "", name: u.displayName, photo_url: u.photoURL } });
      loadMe().catch(() => set({ loading: false }));
    } else {
      localStorage.removeItem(HINT_KEY);
      set({ user: null, workspace: null, loading: false });
    }
  });
}

export async function signIn(): Promise<void> {
  if (MOCK) {
    sessionStorage.removeItem(GUEST_KEY);
    set({ loading: true, guest: false, guestMessage: undefined });
    localStorage.setItem(HINT_KEY, "1");
    return loadMe();
  }
  const c = await loadClient();
  if (!c) throw new Error("Google sign-in is not available right now. Try again later.");
  const provider = new c.mod.GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  const cred = await c.mod.signInWithPopup(c.auth, provider);
  sessionStorage.removeItem(GUEST_KEY);
  set({ guest: false, guestMessage: undefined });
  set({ user: { uid: cred.user.uid, email: cred.user.email ?? "", name: cred.user.displayName, photo_url: cred.user.photoURL }, loading: true });
  await loadMe();
}

export async function signOut(): Promise<void> {
  localStorage.removeItem(HINT_KEY);
  localStorage.removeItem(WS_KEY);
  if (!MOCK) {
    const c = await loadClient();
    if (c) await c.mod.signOut(c.auth);
  }
  set({ user: null, workspace: null, loading: false });
}

export function switchWorkspace(id: string): void {
  const ref = state.user?.workspaces?.find((w) => w.workspace.id === id);
  if (!ref) return;
  localStorage.setItem(WS_KEY, id);
  set({ workspace: ref });
}

/** After a workspace change on the server (rename, accepted invite). */
export async function refresh(): Promise<void> {
  if (state.user) await loadMe();
}

export const canManage = (s: Session) => s.workspace?.role === "owner" || s.workspace?.role === "admin";

/** Writes are closed to guests; this is the reason to show. */
export const readOnlyReason = (s: Session): string | null => (s.guest && !s.user ? s.guestMessage ?? "This is a read-only demo. Sign in with Google to use your own workspace." : null);
