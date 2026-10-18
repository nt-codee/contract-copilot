import type { Risk } from "../types";
import { RISK_LABEL } from "../format";

/** Shape per risk, so risk never depends on colour alone. */
export function RiskShape({ risk, size = 12 }: { risk: Risk; size?: number }) {
  const p = { width: size, height: size, viewBox: "0 0 12 12", "aria-hidden": true, className: "shrink-0" } as const;
  if (risk === "high")
    return (
      <svg {...p}>
        <path d="M6 .6 11.6 11H.4z" fill="currentColor" />
      </svg>
    );
  if (risk === "medium")
    return (
      <svg {...p}>
        <path d="M6 .4 11.6 6 6 11.6.4 6z" fill="currentColor" />
      </svg>
    );
  return (
    <svg {...p}>
      <circle cx="6" cy="6" r="5.4" fill="currentColor" />
    </svg>
  );
}

export const RISK_TEXT: Record<Risk, string> = {
  high: "text-high",
  medium: "text-medium",
  low: "text-low",
};

export const RISK_BG: Record<Risk, string> = {
  high: "bg-high-tint",
  medium: "bg-medium-tint",
  low: "bg-low-tint",
};

/** Box colours used on the PDF page. */
export const RISK_BOX: Record<Risk, { fill: string; edge: string; ring: string }> = {
  high: { fill: "rgba(217,58,43,.20)", edge: "#D93A2B", ring: "rgba(217,58,43,.45)" },
  medium: { fill: "rgba(233,164,0,.26)", edge: "#C98C00", ring: "rgba(233,164,0,.5)" },
  low: { fill: "rgba(47,158,110,.20)", edge: "#2F9E6E", ring: "rgba(47,158,110,.45)" },
};

/** The numbered flag tab: number, shape, colour. Matches box and rail. */
export function FlagTab({ n, risk, className = "" }: { n: number; risk: Risk; className?: string }) {
  const tone =
    risk === "high" ? "bg-high text-white" : risk === "medium" ? "bg-medium-box text-ink" : "bg-low text-white";
  return (
    <span
      aria-hidden="true"
      className={`tab inline-flex h-7 min-w-[2.6rem] items-center justify-center gap-1 rounded-r-[3px] pl-1.5 pr-2 text-sm ${tone} ${className}`}
    >
      <RiskShape risk={risk} size={10} />
      {n}
    </span>
  );
}

/** Risk word with its shape, for text on paper. */
export function RiskLabel({ risk, label }: { risk: Risk; label?: string }) {
  return (
    <span className={`tab inline-flex items-center gap-1.5 ${RISK_TEXT[risk]}`}>
      <RiskShape risk={risk} size={11} />
      {label ?? RISK_LABEL[risk]}
    </span>
  );
}
