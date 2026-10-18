// Finds where a quote sits on a PDF page, from the page's text layer.
// Used when a finding arrives without `boxes` (older reviews), and by
// tools/boxes.mjs to build the offline sample data. Plain, erasable TS so
// Node can import it directly.

export type Box = [number, number, number, number];

/** The subset of a pdf.js TextItem this needs. */
export interface TextRun {
  str: string;
  transform: number[]; // [a, b, c, d, e, f]
  width: number;
  height: number;
  hasEOL?: boolean;
}

/** One character of the normalised page text and where it came from. */
interface CharRef {
  run: number;
  offset: number;
}

function normChar(ch: string): string {
  if (/\s/.test(ch)) return " ";
  if ("‘’‚′`".includes(ch)) return "'";
  if ("“”„″".includes(ch)) return '"';
  if ("–—‐-".includes(ch)) return "-";
  return ch.toLowerCase();
}

function normalise(s: string): string {
  let out = "";
  for (const ch of s.replace(/\u00ad/g, "")) {
    const c = normChar(ch);
    if (c === " " && (out === "" || out.endsWith(" "))) continue;
    out += c;
  }
  return out.trim();
}

/** Returns one normalised [x0,y0,x1,y1] rect per line the quote covers, or []. */
export function locateQuote(
  runs: TextRun[],
  pageWidth: number,
  pageHeight: number,
  quote: string,
): Box[] {
  let text = "";
  const refs: CharRef[] = [];
  runs.forEach((r, ri) => {
    const s = r.str.replace(/\u00ad/g, "");
    for (let i = 0; i < s.length; i++) {
      const c = normChar(s[i]);
      if (c === " " && (text === "" || text.endsWith(" "))) continue;
      text += c;
      refs.push({ run: ri, offset: i });
    }
    if (!text.endsWith(" ") && text !== "") {
      text += " ";
      refs.push({ run: ri, offset: s.length });
    }
  });

  const q = normalise(quote);
  if (q.length < 8) return [];
  let start = text.indexOf(q);
  let end = start + q.length;
  if (start < 0) {
    // Tolerate small differences in the middle: anchor on both ends.
    const n = Math.min(48, Math.floor(q.length / 3));
    const head = text.indexOf(q.slice(0, n));
    const tail = head < 0 ? -1 : text.indexOf(q.slice(-n), head);
    if (head < 0 || tail < 0 || tail - head > q.length * 1.5) return [];
    start = head;
    end = tail + n;
  }

  // Character span per run.
  const spans = new Map<number, [number, number]>();
  for (let i = start; i < end; i++) {
    const { run, offset } = refs[i];
    const cur = spans.get(run);
    spans.set(run, cur ? [Math.min(cur[0], offset), Math.max(cur[1], offset + 1)] : [offset, offset + 1]);
  }

  // One rect per run, then merge runs that share a baseline.
  const lines: { base: number; x0: number; x1: number; top: number; bottom: number }[] = [];
  for (const [ri, [a, b]] of spans) {
    const r = runs[ri];
    const len = Math.max(1, r.str.length);
    const fontH = r.height || Math.hypot(r.transform[2], r.transform[3]) || 10;
    const x = r.transform[4];
    const base = r.transform[5];
    const x0 = x + (r.width * a) / len;
    const x1 = x + (r.width * Math.min(b, len)) / len;
    if (x1 - x0 < 0.5) continue;
    const line = lines.find((l) => Math.abs(l.base - base) < fontH * 0.4);
    const top = base + fontH * 0.88;
    const bottom = base - fontH * 0.26;
    if (line) {
      line.x0 = Math.min(line.x0, x0);
      line.x1 = Math.max(line.x1, x1);
      line.top = Math.max(line.top, top);
      line.bottom = Math.min(line.bottom, bottom);
    } else lines.push({ base, x0, x1, top, bottom });
  }

  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  const round = (v: number) => Math.round(clamp(v) * 10000) / 10000;
  return lines
    .sort((l, m) => m.base - l.base)
    .map((l): Box => [
      round(l.x0 / pageWidth),
      round((pageHeight - l.top) / pageHeight),
      round(l.x1 / pageWidth),
      round((pageHeight - l.bottom) / pageHeight),
    ]);
}
