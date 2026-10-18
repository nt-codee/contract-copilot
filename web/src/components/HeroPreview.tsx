import type { CSSProperties, ReactNode } from "react";
import { FlagTab, RiskLabel } from "./Risk";

const d = (s: number) => ({ "--d": `${s}s` }) as CSSProperties;

function Marked({ tone, delay, n, children }: { tone: "high" | "medium"; delay: number; n: number; children: ReactNode }) {
  const hl = tone === "high" ? "rgba(217,58,43,.22)" : "rgba(233,164,0,.30)";
  const edge = tone === "high" ? "#D93A2B" : "#C98C00";
  return (
    <span className="relative block">
      <span className="hl" style={{ ...d(delay), ["--hl" as string]: hl, borderBottom: `2px solid ${edge}` }}>
        {children}
      </span>
      <span className="bracket absolute -right-4 bottom-0 top-0 w-1 rounded-sm sm:-right-5" style={{ ...d(delay + 0.8), background: edge }} />
      <span className="flag absolute -right-[2.7rem] top-0 sm:-right-[4.4rem]" style={d(delay + 1)}>
        <FlagTab n={n} risk={tone} className="shadow-lift" />
      </span>
    </span>
  );
}

/** Home hero: a page of a sample contract being marked up, played once. */
export default function HeroPreview() {
  return (
    <figure className="relative">
      <figcaption className="sr-only">
        Example: clause 4.2 of a sample fleet tracking agreement, boxed as high risk, with a note on what to ask the vendor for.
      </figcaption>
      <div aria-hidden="true" className="hero-play relative">
        {/* The rest of the contract, stacked behind the page being read. */}
        <div className="sheet absolute inset-y-3 left-5 right-14 rotate-[2.2deg] opacity-90 sm:right-28" />
        <div className="sheet absolute inset-y-1.5 left-2.5 right-12 -rotate-[1.2deg] sm:right-[6.5rem]" />
        <div className="sheet relative mr-10 px-5 pb-10 pt-6 font-doc text-[0.82rem] leading-[1.55] text-[#1d1d1d] sm:mr-24 sm:px-8 sm:pt-8 sm:text-[0.92rem]">
          <p className="text-center font-bold tracking-wide">FLEET TRACKING SUBSCRIPTION AGREEMENT</p>
          <p className="mt-4 font-bold">4. TERM AND RENEWAL</p>
          <p className="mt-2">
            4.1 This Agreement commences on the Effective Date and continues for an initial period of twelve (12) months
            from the installation of the first Device (the “Initial Term”).
          </p>
          <div className="mt-2">
            <Marked tone="high" delay={0.5} n={1}>
              4.2 Upon the expiry of the Initial Term, this Agreement shall automatically renew for successive periods of
              twenty-four (24) months each unless either party gives written notice of non-renewal at least ninety (90)
              days before the expiry of the then-current term.
            </Marked>
          </div>
          <div className="mt-2">
            <Marked tone="medium" delay={2.7} n={2}>
              4.3 Notice of non-renewal is effective only if sent by registered post. Notice by email or through the Portal
              shall not be effective.
            </Marked>
          </div>
          <p className="mt-3 font-bold">5. FEES AND PAYMENT</p>
          <div className="mt-2 space-y-[0.6rem] pb-2">
            {[100, 96, 62, 0, 100, 98, 88, 40].map((w, i) =>
              w ? <div key={i} className="h-[0.42rem] rounded-sm bg-paper-line" style={{ width: `${w}%` }} /> : <div key={i} className="h-1" />,
            )}
          </div>
          <span className="tab absolute bottom-3 right-4 !text-[0.7rem] text-ink-mute">2 / 4</span>
        </div>

        <div
          className="note relative -mt-8 ml-6 max-w-[22rem] rounded-[3px] bg-paper px-4 py-3.5 shadow-lift sm:absolute sm:-right-2 sm:bottom-[-2rem] sm:ml-0 sm:mt-0 sm:w-[21rem]"
          style={d(2)}
        >
          <div className="flex items-center gap-2.5">
            <FlagTab n={1} risk="high" className="!rounded-[3px]" />
            <RiskLabel risk="high" />
            <span className="tab text-ink-mute">Page 2</span>
          </div>
          <p className="mt-2 font-semibold leading-snug text-ink">Renews for two years unless you post notice 90 days ahead</p>
          <p className="mt-2 border-l-4 border-ink bg-paper-soft px-2.5 py-1.5 text-[0.9rem] text-ink">
            <span className="tab block">Ask for</span>
            12-month renewals, 30 days’ notice, and email accepted.
          </p>
        </div>
      </div>
    </figure>
  );
}
