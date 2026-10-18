// Charts in the app's own tokens: ink on paper, risk colours only for risk,
// flat fills, no gradients, no 3D. Every chart has a text summary for screen readers.
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const C = {
  ink: "#121A2B",
  soft: "#44506A",
  mute: "#5B6680",
  line: "#DCE2EC",
  desk: "#1E3A6E",
  high: "#B3261E",
  medium: "#C98C00",
  low: "#1B6B4A",
};

const axis = { stroke: C.line, tick: { fill: C.mute, fontSize: 12, fontFamily: "Archivo Variable" }, tickLine: false };
const tooltip = {
  cursor: { fill: "rgba(30,58,110,.06)" },
  contentStyle: { border: `1px solid ${C.line}`, borderRadius: 3, fontFamily: "Archivo Variable", fontSize: 13, boxShadow: "none" },
  labelStyle: { color: C.ink, fontWeight: 600 },
};

export function Panel({ title, summary, children, className = "", visual = true }: { title: string; summary: string; children: ReactNode; className?: string; visual?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  // Chart SVGs are focusable by default; the text summary stands in for them.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fix = () => el.querySelectorAll('[tabindex]:not([tabindex="-1"])').forEach((n) => n.setAttribute("tabindex", "-1"));
    fix();
    const mo = new MutationObserver(fix);
    mo.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ["tabindex"] });
    return () => mo.disconnect();
  }, []);
  return (
    <figure className={`sheet min-w-0 px-5 py-4 ${className}`}>
      <figcaption>
        <span className="tab block text-ink-mute">{title}</span>
        {visual && <span className="sr-only">{summary}</span>}
      </figcaption>
      <div ref={ref} aria-hidden={visual || undefined} className="chart mt-3">
        {children}
      </div>
    </figure>
  );
}

export function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="sheet min-w-0 px-5 py-4">
      <p className="tab text-ink-mute">{label}</p>
      <p className="mt-1 text-[1.9rem] font-bold leading-none text-ink [font-stretch:110%]">{value}</p>
      {note && <p className="mt-1.5 text-[0.9rem] text-ink-soft">{note}</p>}
    </div>
  );
}

const shortDay = (d: string) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

export function PerDay({ data }: { data: { date: string; count: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart accessibilityLayer={false} data={data} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
        <CartesianGrid vertical={false} stroke={C.line} />
        <XAxis dataKey="date" {...axis} tickFormatter={shortDay} interval="preserveStartEnd" minTickGap={24} />
        <YAxis {...axis} allowDecimals={false} axisLine={false} />
        <Tooltip {...tooltip} labelFormatter={(l) => shortDay(String(l))} formatter={(v) => [v, "reviews"]} />
        <Bar dataKey="count" fill={C.desk} radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Stacked high/medium/low bars, one row per category. */
export function RiskStack({ data, labelKey, height }: { data: Record<string, string | number>[]; labelKey: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(120, data.length * 34 + 24)}>
      <BarChart accessibilityLayer={false} data={data} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 8 }} barCategoryGap={8}>
        <CartesianGrid horizontal={false} stroke={C.line} />
        <XAxis type="number" {...axis} allowDecimals={false} />
        <YAxis type="category" dataKey={labelKey} {...axis} axisLine={false} width={120} tickFormatter={(v: string) => v.replace(/_/g, " ")} />
        <Tooltip {...tooltip} />
        <Bar dataKey="high" stackId="r" fill={C.high} name="High" isAnimationActive={false} />
        <Bar dataKey="medium" stackId="r" fill={C.medium} name="Medium" isAnimationActive={false} />
        <Bar dataKey="low" stackId="r" fill={C.low} name="Low" radius={[0, 2, 2, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Bars({ data, labelKey, valueKey, color = C.ink, width = 160 }: { data: Record<string, string | number>[]; labelKey: string; valueKey: string; color?: string; width?: number }) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(100, data.length * 32 + 24)}>
      <BarChart accessibilityLayer={false} data={data} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 8 }} barCategoryGap={8}>
        <CartesianGrid horizontal={false} stroke={C.line} />
        <XAxis type="number" {...axis} allowDecimals={false} />
        <YAxis type="category" dataKey={labelKey} {...axis} axisLine={false} width={width} tickFormatter={(v: string) => (v.length > 26 ? v.slice(0, 25) + "…" : v)} />
        <Tooltip {...tooltip} />
        <Bar dataKey={valueKey} fill={color} radius={[0, 2, 2, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Trend({ data, keyName }: { data: Record<string, string | number>[]; keyName: string }) {
  return (
    <ResponsiveContainer width="100%" height={140}>
      <LineChart accessibilityLayer={false} data={data} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
        <CartesianGrid vertical={false} stroke={C.line} />
        <XAxis dataKey="date" {...axis} tickFormatter={shortDay} interval="preserveStartEnd" minTickGap={24} />
        <YAxis {...axis} axisLine={false} />
        <Tooltip {...tooltip} />
        <Line dataKey={keyName} stroke={C.ink} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function Legend() {
  return (
    <ul className="mt-2 flex gap-4 text-[0.85rem] text-ink-soft" aria-hidden="true">
      {(["high", "medium", "low"] as const).map((r) => (
        <li key={r} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[1px]" style={{ background: C[r] }} />
          {r[0].toUpperCase() + r.slice(1)}
        </li>
      ))}
    </ul>
  );
}
