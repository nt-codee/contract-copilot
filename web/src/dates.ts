// Dates in contracts: parsing, wording, sorting, and calendar files.
import type { KeyDate } from "./types";

/** Parses a YYYY-MM-DD date as a local calendar day. */
export function parseDay(iso?: string | null): Date | null {
  if (!iso) return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

export function formatDay(iso?: string | null): string {
  const d = parseDay(iso);
  return d ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
}

/** Whole days from today to the date; negative when past. */
export function daysUntil(iso?: string | null, today = new Date()): number | null {
  const d = parseDay(iso);
  if (!d) return null;
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((d.getTime() - t.getTime()) / 86400000);
}

export function inWords(days: number | null): string {
  if (days == null) return "";
  if (days < 0) return days === -1 ? "yesterday" : `${-days} days ago`;
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 60) return `in ${days} days`;
  const months = Math.round(days / 30.4);
  return `in about ${months} months`;
}

/** How urgent a deadline is, for colour plus words. */
export function urgency(days: number | null): "past" | "soon" | "later" | "none" {
  if (days == null) return "none";
  if (days < 0) return "past";
  if (days <= 60) return "soon";
  return "later";
}

export const isDeadline = (k: KeyDate) => k.kind === "notice_deadline";

/** Dated items first in date order, undated last; deadlines win ties. */
export function sortDates(ds: KeyDate[]): KeyDate[] {
  return [...ds].sort((a, b) => {
    const x = parseDay(a.date)?.getTime() ?? Infinity;
    const y = parseDay(b.date)?.getTime() ?? Infinity;
    if (x !== y) return x - y;
    return Number(isDeadline(b)) - Number(isDeadline(a));
  });
}

// --- .ics (RFC 5545), built in the browser ---

function esc(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

/** Folds lines longer than 75 octets, as the format requires. */
function fold(line: string): string {
  const out: string[] = [];
  let cur = "";
  for (const ch of line) {
    if (new TextEncoder().encode(cur + ch).length > 74) {
      out.push(cur);
      cur = " " + ch;
    } else cur += ch;
  }
  out.push(cur);
  return out.join("\r\n");
}

const ymd = (d: Date) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;

export interface CalendarItem {
  uid: string;
  date: string;
  title: string;
  description: string;
  url?: string;
  /** days before the date to remind, e.g. [14, 1] */
  alarms?: number[];
}

export function buildIcs(items: CalendarItem[]): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Contract Co-pilot//Key dates//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"];
  for (const it of items) {
    const d = parseDay(it.date);
    if (!d) continue;
    const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
    lines.push(
      "BEGIN:VEVENT",
      `UID:${esc(it.uid)}@contract-copilot`,
      `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${ymd(d)}`,
      `DTEND;VALUE=DATE:${ymd(next)}`,
      `SUMMARY:${esc(it.title)}`,
      `DESCRIPTION:${esc(it.description)}`,
      ...(it.url ? [`URL:${esc(it.url)}`] : []),
      "TRANSP:TRANSPARENT",
    );
    for (const n of it.alarms ?? []) {
      lines.push("BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${esc(it.title)}`, `TRIGGER:-P${n}D`, "END:VALARM");
    }
    lines.push("END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.map(fold).join("\r\n") + "\r\n";
}

export function downloadIcs(filename: string, ics: string): void {
  const url = URL.createObjectURL(new Blob([ics], { type: "text/calendar;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
