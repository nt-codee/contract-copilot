import type { ContractType, FindingCounts, Risk } from "./types";

export const RISK_LABEL: Record<Risk, string> = {
  high: "High risk",
  medium: "Medium risk",
  low: "Low risk",
};

export const RISK_ORDER: Record<Risk, number> = { high: 0, medium: 1, low: 2 };

export function contractTypeLabel(t: ContractType): string {
  switch (t) {
    case "nda":
      return "NDA";
    case "service_agreement":
      return "Service agreement";
    case "employment":
      return "Employment or freelance";
    case "tenancy":
      return "Tenancy";
    case "hire_purchase":
      return "Hire purchase";
    default:
      return "Other agreement";
  }
}

const LANGS: Record<string, string> = {
  en: "English",
  ms: "Bahasa Malaysia",
  zh: "Chinese",
  ta: "Tamil",
};
export function languageLabel(code: string): string {
  return LANGS[code.toLowerCase()] ?? code.toUpperCase();
}

export function formatSeconds(ms: number): string {
  return `${Math.max(1, Math.round(ms / 1000))} s`;
}

export function formatCost(usd?: number | null): string | null {
  if (usd == null) return null;
  return `US$${usd < 0.01 ? usd.toFixed(3) : usd.toFixed(2)}`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function countText(c: FindingCounts): string {
  const parts: string[] = [];
  if (c.high) parts.push(`${c.high} high`);
  if (c.medium) parts.push(`${c.medium} medium`);
  if (c.low) parts.push(`${c.low} low`);
  return parts.length ? parts.join(", ") : "No issues found";
}

/** The AI's version for people, e.g. "gemini-3.8-flash" -> "3.8 Flash". The full id goes in a tooltip. */
export function aiVersion(model?: string | null): string {
  if (!model) return "";
  const v = model.replace(/^[a-z]+-/i, "").replace(/-/g, " ");
  return v.replace(/\b([a-z])/g, (c) => c.toUpperCase());
}

/** A filename with soft break points after - _ . so it wraps between words, never mid-word. */
export function breakable(name: string): string {
  return name.replace(/([-_.])/g, "$1\u200B");
}
