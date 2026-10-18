import type { CSSProperties, ReactNode } from "react";
import type { Risk } from "../types";
import { FlagTab, RISK_BOX, RiskLabel } from "./Risk";

const d = (s: number) => ({ "--d": `${s}s` }) as CSSProperties;

/** One clause with its evidence box drawn on it, and the numbered flag in the margin. */
function Boxed({ risk, n, at, children }: { risk: Risk; n: number; at: number; children: ReactNode }) {
  const box = RISK_BOX[risk];
  return (
    <div className="relative mt-3">
      <span
        className="hv-box pointer-events-none absolute -inset-x-1.5 -inset-y-1 rounded-[2px]"
        style={{ ...d(at), background: box.fill, boxShadow: `inset 0 0 0 2px ${box.edge}` }}
      />
      <p className="relative">{children}</p>
      <span className="hv-flag absolute -right-[3.35rem] top-0 sm:-right-[4.6rem]" style={d(at + 0.45)}>
        <FlagTab n={n} risk={risk} className="shadow-lift" />
      </span>
    </div>
  );
}

/** Grey lines standing in for the rest of the page. */
function Lines({ widths }: { widths: number[] }) {
  return (
    <div className="mt-2 space-y-[0.55rem]">
      {widths.map((w, i) => (
        <div key={i} className="h-[0.4rem] rounded-sm bg-paper-line" style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}

/**
 * Home hero: page 2 of the bundled fleet tracking sample as the reader shows it, with three
 * clauses boxed (numbered by risk, as in the review) and the first finding open beside it.
 * Clause text is the sample's own.
 */
export default function HeroVisual() {
  return (
    <figure className="relative">
      <figcaption className="sr-only">
        Example review of page 2 of a sample fleet tracking agreement. Three clauses are boxed: clause 4.2, a two-year
        auto-renewal, and clause 5.3, a 20% yearly price rise, as high risk; clause 4.3, notice by registered post only,
        as medium risk. A note on clause 4.2 says what to ask the vendor for and quotes the company rule it breaks.
      </figcaption>
      <div aria-hidden="true" className="hero-run relative">
        {/* Lamp light pooled under the page. */}
        <div className="pointer-events-none absolute inset-x-0 -inset-y-12 -z-10 rounded-full bg-[radial-gradient(closest-side,rgba(150,184,240,.28),transparent)] blur-2xl" />
        {/* Page 3 peeking out behind. */}
        <div className="sheet absolute inset-y-4 left-4 right-14 rotate-[1.8deg] opacity-80 sm:right-[6.5rem]" />

        <div className="sheet page-doc relative mr-14 px-5 pb-12 pt-6 font-doc text-[0.8rem] leading-[1.5] text-[#1d1d1d] sm:mr-24 sm:px-8 sm:pt-7 sm:text-[0.9rem]">
          <p className="text-center text-[0.95em] font-bold tracking-wide">FLEET TRACKING SUBSCRIPTION AGREEMENT</p>
          <p className="mt-4 font-bold">4. TERM AND RENEWAL</p>
          <Boxed risk="high" n={1} at={0.6}>
            4.2 Upon the expiry of the Initial Term, this Agreement shall automatically renew for successive periods of
            twenty-four (24) months each unless either party gives written notice of non-renewal at least ninety (90) days
            before the expiry of the then-current term.
          </Boxed>
          <Boxed risk="medium" n={3} at={1.3}>
            4.3 Notice of non-renewal is effective only if sent by registered post. Notice by email or through the Portal
            shall not be effective.
          </Boxed>
          <p className="mt-3 font-bold">5. FEES AND PAYMENT</p>
          <Boxed risk="high" n={2} at={1.0}>
            5.3 With effect from each anniversary of the Effective Date, the Provider shall be entitled to increase the
            Subscription Fees by twenty per cent (20%) without further notice to the Customer.
          </Boxed>
          <Lines widths={[100, 96, 64, 100, 90, 98, 40]} />
          <span className="tab absolute bottom-3 right-4 !text-[0.7rem] text-ink-mute">Page 2 of 4</span>
        </div>

        {/* The open finding, floating over the page edge. */}
        <div
          className="hv-card relative -mt-10 ml-5 mr-2 max-w-[23rem] rounded-[3px] bg-paper px-4 py-3.5 shadow-float sm:absolute sm:-bottom-24 sm:-right-3 sm:m-0 sm:w-[22rem]"
          style={d(2.0)}
        >
          <div className="flex items-center gap-2.5">
            <FlagTab n={1} risk="high" className="!rounded-[3px]" />
            <RiskLabel risk="high" />
            <span className="tab text-ink-mute">Page 2</span>
          </div>
          <p className="mt-2 font-semibold leading-snug text-ink">Renews for two years unless you post notice 90 days ahead</p>
          <p className="mt-2.5 rounded-[2px] bg-ink px-2.5 py-1.5 text-[0.86rem] leading-snug text-white">
            <span className="tab block text-[#C4D2EE]">Breaks your rule 4.2</span>
            Renewal periods of 12 months or less, with 30 days’ notice or less.
          </p>
          <p className="mt-2 border-l-4 border-ink bg-paper-soft px-2.5 py-1.5 text-[0.88rem] leading-snug text-ink">
            <span className="tab block">Ask for</span>
            12-month renewals, 30 days’ notice, and notice by email.
          </p>
        </div>
      </div>
    </figure>
  );
}
