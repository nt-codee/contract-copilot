import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { canManage } from "../session";
import { useMedia } from "../useMedia";
import { useSession } from "../useSession";
import Account, { GuestButton, SignInButton } from "./Account";

/** The wordmark's mark: a page with a flag tab on its edge. */
function Mark() {
  return (
    <svg width="26" height="30" viewBox="0 0 26 30" aria-hidden="true" className="shrink-0">
      <rect x="1" y="1" width="18" height="28" rx="1.5" fill="#fff" />
      <rect x="4.5" y="7" width="11" height="2" fill="#C4D2EE" />
      <rect x="4.5" y="11.5" width="11" height="3.2" fill="#D93A2B" opacity=".35" />
      <rect x="4.5" y="17.5" width="8" height="2" fill="#C4D2EE" />
      <path d="M19 10h5.5a1 1 0 0 1 1 1v4.5a1 1 0 0 1-1 1H19z" fill="#D93A2B" />
    </svg>
  );
}

// Icons: Lucide (ISC licence), inlined so no icon package is needed.
const I = (d: ReactNode) => (
  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
    {d}
  </svg>
);
const ICON = {
  home: I(<><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /><path d="M10 21v-6h4v6" /></>),
  review: I(<><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="M10 9H8M16 13H8M16 17H8" /></>),
  library: I(<><path d="m16 6 4 14M12 6v14M8 8v12M4 4v16" /></>),
  knowledge: I(<><path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" /><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" /><path d="M7 21h10M12 3v18M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2" /></>),
  rules: I(<><rect x="8" y="2" width="8" height="4" rx="1" /><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><path d="m9 14 2 2 4-4" /></>),
  positions: I(<><path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4" /></>),
  workspace: I(<><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>),
  activity: I(<><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5M12 7v5l4 2" /></>),
  admin: I(<><path d="M3 3v18h18M18 17V9M13 17V5M8 17v-3" /></>),
  collapse: I(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M9 3v18M16 15l-3-3 3-3" /></>),
  expand: I(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M9 3v18M14 9l3 3-3 3" /></>),
  menu: I(<><path d="M4 6h16M4 12h16M4 18h16" /></>),
  close: I(<><path d="M18 6 6 18M6 6l12 12" /></>),
};

interface Item {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
}

function useItems(): { main: Item[]; team: Item[] } {
  const s = useSession();
  const main: Item[] = [
    { to: "/", label: "Home", icon: ICON.home, end: true },
    { to: "/review", label: "Review a contract", icon: ICON.review },
    { to: "/library", label: "Library", icon: ICON.library },
    { to: "/knowledge", label: "Knowledge", icon: ICON.knowledge },
    { to: "/rules", label: "Company rules", icon: ICON.rules },
    { to: "/positions", label: "Standard positions", icon: ICON.positions },
  ];
  const team: Item[] = [];
  if ((s.user && canManage(s)) || s.guest) team.push({ to: "/workspace", label: "Workspace", icon: ICON.workspace });
  team.push({ to: "/activity", label: "Activity", icon: ICON.activity });
  if (s.user?.platform_admin) team.push({ to: "/admin", label: "Admin", icon: ICON.admin });
  return { main, team };
}

function NavList({ items, slim, onGo }: { items: Item[]; slim: boolean; onGo?: () => void }) {
  return (
    <ul className="space-y-0.5">
      {items.map((it) => (
        <li key={it.to}>
          <NavLink
            to={it.to}
            end={it.end}
            onClick={onGo}
            title={slim ? it.label : undefined}
            className={({ isActive }) =>
              `relative flex min-h-[44px] items-center gap-3 rounded-[3px] px-3 text-sm transition-colors ${
                isActive ? "bg-white font-semibold text-ink" : "text-desk-text hover:bg-white/10 hover:text-white"
              } ${slim ? "justify-center px-0" : ""}`
            }
          >
            {({ isActive }) => (
              <>
                {it.icon}
                <span className={slim ? "sr-only" : ""}>{it.label}</span>
                {isActive && <span aria-hidden="true" className="absolute -right-[3px] top-2 h-[calc(100%-1rem)] w-[5px] rounded-r-[2px] nav-mark" />}
              </>
            )}
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

/** Shown to visitors: what signing in unlocks. The samples keep working without it. */
function SignInPanel({ slim }: { slim: boolean }) {
  const s = useSession();
  if (s.loading || s.user) return null;
  if (s.guest)
    return slim ? null : (
      <div className="rounded-[3px] border border-desk-line bg-desk px-3 py-3">
        <p className="text-sm leading-snug text-white">You are looking at a made-up company. Sign in to use your own workspace.</p>
        {s.available && <div className="mt-3"><SignInButton className="w-full !min-h-[44px] !px-3 text-sm" /></div>}
      </div>
    );
  if (!s.available) return null;
  if (slim)
    return (
      <div className="flex justify-center">
        <SignInButton className="!min-h-[44px] !w-11 !px-0" label="" />
      </div>
    );
  return (
    <div className="rounded-[3px] border border-desk-line bg-desk px-3 py-3">
      <p className="meta text-desk-text">Sample workspace</p>
      <p className="mt-1 text-sm leading-snug text-white">Sign in to review your own contracts, add your company rules and work with your team.</p>
      <div className="mt-3 space-y-2">
        <SignInButton className="w-full !min-h-[44px] !px-3 text-sm" />
        <GuestButton className="w-full !min-h-[44px] !px-3 text-sm" />
      </div>
    </div>
  );
}

function NavBody({ slim, onGo }: { slim: boolean; onGo?: () => void }) {
  const { main, team } = useItems();
  return (
    <>
      <nav aria-label="Main" className="px-3">
        <NavList items={main} slim={slim} onGo={onGo} />
        <p className={`meta mb-1 mt-6 px-3 text-desk-text ${slim ? "sr-only" : ""}`}>Team</p>
        {slim && <div aria-hidden="true" className="mx-3 my-4 border-t border-desk-line" />}
        <NavList items={team} slim={slim} onGo={onGo} />
      </nav>
    </>
  );
}

function Legal({ slim }: { slim: boolean }) {
  if (slim) return null;
  return (
    <p className="flex gap-4 px-6 text-xs text-desk-text">
      <NavLink to="/privacy" className="inline-flex min-h-[44px] items-center underline underline-offset-4 hover:text-white">Privacy</NavLink>
      <NavLink to="/terms" className="inline-flex min-h-[44px] items-center underline underline-offset-4 hover:text-white">Terms</NavLink>
    </p>
  );
}

const SLIM_KEY = "cc.navSlim";

export default function Layout() {
  const { pathname } = useLocation();
  const isReview = pathname.startsWith("/reviews/");
  const wide = useMedia("(min-width: 1024px)");
  const [pref, setPref] = useState<boolean | null>(() => {
    const v = localStorage.getItem(SLIM_KEY);
    return v == null ? null : v === "1";
  });
  // The review screen needs the width: the sidebar starts collapsed there.
  const [reviewSlim, setReviewSlim] = useState(true);
  const slim = isReview ? reviewSlim : pref ?? false;
  const [drawer, setDrawer] = useState(false);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const menuBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    window.scrollTo(0, 0);
    setDrawer(false);
  }, [pathname]);
  useEffect(() => {
    if (isReview) setReviewSlim(true);
  }, [isReview]);

  useEffect(() => {
    const d = drawerRef.current;
    if (!d) return;
    if (drawer && !d.open) d.showModal(); // modal dialog: focus stays inside, Esc closes
    if (!drawer && d.open) {
      d.close();
      menuBtn.current?.focus();
    }
  }, [drawer]);

  function toggle() {
    if (isReview) return setReviewSlim((v) => !v);
    const next = !slim;
    setPref(next);
    localStorage.setItem(SLIM_KEY, next ? "1" : "0");
  }

  const skip = (
    <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:bg-white focus:px-4 focus:py-2 focus:font-semibold focus:text-ink">
      Skip to the content
    </a>
  );

  const footer = !isReview && (
    <footer className="border-t border-desk-line">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-5 py-4 text-sm text-desk-text sm:px-8">
        <p>Contract Co-pilot. Uploaded files are deleted after 7 days.</p>
        <nav aria-label="Legal" className="flex gap-4">
          <NavLink to="/privacy" className="inline-flex min-h-[44px] items-center text-white underline underline-offset-4 hover:text-desk-text">Privacy</NavLink>
          <NavLink to="/terms" className="inline-flex min-h-[44px] items-center text-white underline underline-offset-4 hover:text-desk-text">Terms</NavLink>
        </nav>
      </div>
    </footer>
  );

  if (wide)
    return (
      <>
        {skip}
        <div className="flex min-h-screen">
          <aside
            aria-label="Contract Co-pilot"
            className={`sticky top-0 z-20 flex h-screen shrink-0 flex-col bg-desk-deep transition-[width] duration-200 ${slim ? "w-[68px]" : "w-[248px]"}`}
          >
            <div className={`flex h-[64px] items-center ${slim ? "justify-center" : "justify-between pl-5 pr-2"}`}>
              <NavLink to="/" className="flex min-h-[44px] items-center gap-2.5 text-white" aria-label="Contract Co-pilot, home">
                <Mark />
                {!slim && <span className="whitespace-nowrap text-base font-bold tracking-tight [font-stretch:110%]">Contract Co-pilot</span>}
              </NavLink>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden pt-2">
              <NavBody slim={slim} />
            </div>
            <div className="space-y-3 border-t border-desk-line px-3 py-3">
              <SignInPanel slim={slim} />
              <Account slim={slim} />
              <button
                type="button"
                onClick={toggle}
                aria-expanded={!slim}
                className={`flex min-h-[44px] w-full items-center gap-3 rounded-[3px] px-3 text-sm text-desk-text hover:bg-white/10 hover:text-white ${slim ? "justify-center px-0" : ""}`}
              >
                {slim ? ICON.expand : ICON.collapse}
                <span className={slim ? "sr-only" : ""}>{slim ? "Show the menu" : "Collapse the menu"}</span>
              </button>
              <Legal slim={slim} />
            </div>
          </aside>
          <div className={`min-w-0 flex-1 ${isReview ? "" : "desk-bg"}`}>
            <main id="main" tabIndex={-1} className="outline-none">
              <Outlet />
            </main>
            {footer}
          </div>
        </div>
      </>
    );

  return (
    <>
      {skip}
      <header className="sticky top-0 z-20 flex h-[56px] items-center justify-between gap-3 bg-desk-deep px-2">
        <button ref={menuBtn} type="button" onClick={() => setDrawer(true)} aria-expanded={drawer} aria-controls="nav-drawer" className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-[3px] text-white hover:bg-white/10">
          {ICON.menu}
          <span className="sr-only">Menu</span>
        </button>
        <NavLink to="/" className="flex min-h-[44px] items-center gap-2 text-white" aria-label="Contract Co-pilot, home">
          <Mark />
          <span className="whitespace-nowrap text-base font-bold tracking-tight [font-stretch:108%]">Contract Co-pilot</span>
        </NavLink>
        <GuestBadge />
        <Account slim compact />
      </header>
      <dialog
        id="nav-drawer"
        ref={drawerRef}
        onClose={() => setDrawer(false)}
        onClick={(e) => e.target === e.currentTarget && setDrawer(false)}
        aria-label="Menu"
        className="m-0 h-[100dvh] max-h-none w-[min(320px,86vw)] max-w-none bg-desk-deep p-0 text-white"
      >
        <div className="flex h-full flex-col">
          <div className="flex h-[56px] items-center justify-between pl-4 pr-2">
            <span className="flex items-center gap-2.5">
              <Mark />
              <span className="font-bold [font-stretch:110%]">Contract Co-pilot</span>
            </span>
            <button type="button" onClick={() => setDrawer(false)} className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-[3px] hover:bg-white/10">
              {ICON.close}
              <span className="sr-only">Close the menu</span>
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pt-2">
            <NavBody slim={false} onGo={() => setDrawer(false)} />
          </div>
          <div className="space-y-3 border-t border-desk-line px-3 py-3">
            <SignInPanel slim={false} />
            <Legal slim={false} />
          </div>
        </div>
      </dialog>
      <div className={isReview ? "" : "desk-bg"}>
        <main id="main" tabIndex={-1} className="outline-none">
          <Outlet />
        </main>
        {footer}
      </div>
    </>
  );
}

/** On phones the sidebar is hidden, so the read-only state shows in the top bar. */
function GuestBadge() {
  const s = useSession();
  if (!s.guest || s.user) return null;
  return <span className="meta hidden rounded-[3px] bg-medium-box px-1.5 py-0.5 text-xs text-ink min-[400px]:inline">Demo</span>;
}
