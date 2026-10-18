import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

const navClass = ({ isActive }: { isActive: boolean }) =>
  `inline-flex min-h-[44px] items-center px-2 sm:px-3 text-[0.95rem] font-semibold transition-colors border-b-2 ${
    isActive ? "border-white text-white" : "border-transparent text-desk-text hover:text-white"
  }`;

/** The wordmark's mark: a page with a flag tab on its edge. */
function Mark() {
  return (
    <svg width="26" height="30" viewBox="0 0 26 30" aria-hidden="true">
      <rect x="1" y="1" width="18" height="28" rx="1.5" fill="#fff" />
      <rect x="4.5" y="7" width="11" height="2" fill="#C4D2EE" />
      <rect x="4.5" y="11.5" width="11" height="3.2" fill="#D93A2B" opacity=".35" />
      <rect x="4.5" y="17.5" width="8" height="2" fill="#C4D2EE" />
      <path d="M19 10h5.5a1 1 0 0 1 1 1v4.5a1 1 0 0 1-1 1H19z" fill="#D93A2B" />
    </svg>
  );
}

export default function Layout() {
  const { pathname } = useLocation();
  const isReview = pathname.startsWith("/reviews/");
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:bg-white focus:px-4 focus:py-2 focus:font-semibold"
      >
        Skip to the content
      </a>
      <header className="relative z-20 bg-desk-deep">
        <div className="flex h-[60px] items-center justify-between gap-2 px-3 sm:gap-4 sm:px-6">
          <NavLink to="/" className="flex min-h-[44px] items-center gap-2.5 text-white" aria-label="Contract Co-pilot, home">
            <Mark />
            <span className="hidden whitespace-nowrap text-[1rem] font-bold min-[480px]:inline tracking-tight [font-stretch:105%] sm:text-[1.1rem] sm:[font-stretch:112%]">Contract Co-pilot</span>
          </NavLink>
          <nav aria-label="Main" className="flex min-w-0 items-center sm:gap-1">
            <NavLink to="/" end className={navClass}>
              <span className="hidden sm:inline">Review a contract</span>
              <span className="sm:hidden">Review</span>
            </NavLink>
            <NavLink to="/library" className={navClass}>
              Library
            </NavLink>
            <NavLink to="/knowledge" className={navClass}>
              <span className="hidden sm:inline">Knowledge</span>
              <span className="sm:hidden">Law</span>
            </NavLink>
            <NavLink to="/rules" className={navClass}>
              <span className="hidden sm:inline">Your company rules</span>
              <span className="sm:hidden">Rules</span>
            </NavLink>
          </nav>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="outline-none">
        <Outlet />
      </main>
      {!isReview && (
        <footer className="border-t border-desk-line">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-5 py-4 text-[0.9rem] text-desk-text sm:px-8">
            <p>Contract Co-pilot · Uploaded files are deleted after 7 days.</p>
            <nav aria-label="Legal" className="flex gap-4">
              <NavLink to="/privacy" className="inline-flex min-h-[44px] items-center text-white underline underline-offset-4 hover:text-desk-text">
                Privacy
              </NavLink>
              <NavLink to="/terms" className="inline-flex min-h-[44px] items-center text-white underline underline-offset-4 hover:text-desk-text">
                Terms
              </NavLink>
            </nav>
          </div>
        </footer>
      )}
    </>
  );
}
