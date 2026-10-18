import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { locateOnPage, openPdf } from "../pdf";
import type { Box } from "../locate";
import type { Finding } from "../types";
import { RISK_LABEL } from "../format";
import { FlagTab, RISK_BOX, RISK_TEXT, RiskShape } from "./Risk";

export interface Marked {
  f: Finding;
  /** 1-based position in risk order; the number on the flag. */
  n: number;
  boxes: Box[];
}

export type ReaderState = "loading" | "ready" | "error";

interface Props {
  url: string;
  items: Marked[];
  selected: string | null;
  /** Bump `k` to scroll to and pulse the finding `id`. */
  focus: { id: string; k: number } | null;
  onSelect: (id: string) => void;
  onLocated: (found: Record<string, Box[]>) => void;
  onState: (s: ReaderState) => void;
  /** Narrow screens: no page strip, flags sit inside the page edge. */
  compact?: boolean;
}

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function Reader({ url, items, selected, focus, onSelect, onLocated, onState, compact }: Props) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<{ w: number; h: number }[]>([]);
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);
  const [current, setCurrent] = useState(1);
  const colRef = useRef<HTMLDivElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);

  // Load the document and every page size, so the layout is stable before drawing.
  useEffect(() => {
    let live = true;
    let loaded: PDFDocumentProxy | null = null;
    onState("loading");
    openPdf(url)
      .then(async (d) => {
        loaded = d;
        const s: { w: number; h: number }[] = [];
        for (let i = 1; i <= d.numPages; i++) {
          const v = (await d.getPage(i)).getViewport({ scale: 1 });
          s.push({ w: v.width, h: v.height });
        }
        if (!live) return;
        setSizes(s);
        setDoc(d);
        onState("ready");
      })
      .catch(() => {
        if (!live) return;
        setFailed(true);
        onState("error");
      });
    return () => {
      live = false;
      void loaded?.loadingTask.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // Findings that arrived without boxes: find their quotes in the text layer.
  useEffect(() => {
    if (!doc) return;
    const missing = items.filter((m) => m.boxes.length === 0 && m.f.page <= doc.numPages);
    if (missing.length === 0) return;
    let live = true;
    const byPage = new Map<number, Marked[]>();
    missing.forEach((m) => byPage.set(m.f.page, [...(byPage.get(m.f.page) ?? []), m]));
    (async () => {
      const found: Record<string, Box[]> = {};
      for (const [p, ms] of byPage) {
        try {
          const res = await locateOnPage(doc, p, ms.map((m) => m.f.quote));
          ms.forEach((m, i) => (found[m.f.id] = res[i]));
        } catch {
          ms.forEach((m) => (found[m.f.id] = []));
        }
      }
      if (live) onLocated(found);
    })();
    return () => {
      live = false;
    };
    // Only when the document changes; `items` boxes are filled by this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc]);

  useEffect(() => {
    const el = colRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [doc]);

  // Track which page is in view for the page strip.
  useEffect(() => {
    const pane = paneRef.current;
    if (!pane || !doc) return;
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (vis) setCurrent(Number((vis.target as HTMLElement).dataset.page));
      },
      { root: compact ? null : pane, threshold: [0.25, 0.5, 0.75] },
    );
    pane.querySelectorAll("[data-page]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [doc, sizes, compact]);

  // Scroll to a finding's box when asked (again once boxes first arrive).
  const anyBoxes = items.some((m) => m.boxes.length > 0);
  useEffect(() => {
    if (!focus || !doc) return;
    const t = window.setTimeout(() => {
      const el = paneRef.current?.querySelector(`[data-box="${focus.id}"]`);
      el?.scrollIntoView({ block: compact ? "start" : "center", behavior: reduced() ? "auto" : "smooth" });
    }, 30);
    return () => window.clearTimeout(t);
  }, [focus, doc, width, compact, anyBoxes]);

  function goToPage(p: number) {
    paneRef.current
      ?.querySelector(`[data-page="${p}"]`)
      ?.scrollIntoView({ block: "start", behavior: reduced() ? "auto" : "smooth" });
  }

  if (failed)
    return (
      <div className="grid h-full place-items-center p-8 text-center text-desk-text">
        <p className="max-w-sm">
          The pages of this contract could not be shown here. Each finding still quotes its clause and page.
        </p>
      </div>
    );

  const marksOn = (p: number) => items.filter((m) => m.f.page === p);

  return (
    <div ref={paneRef} className={`relative flex gap-5 ${compact ? "" : "h-full overflow-y-auto px-5 py-6"}`}>
      {!compact && (
        <nav aria-label="Pages" className="sticky top-0 hidden w-14 shrink-0 self-start lg:block">
          <p className="tab mb-2 text-center !text-[0.75rem] text-desk-text">Page</p>
          <ol className="space-y-1.5">
            {sizes.map((_, i) => {
              const p = i + 1;
              const marks = marksOn(p);
              return (
                <li key={p}>
                  <button
                    type="button"
                    onClick={() => goToPage(p)}
                    aria-current={current === p ? "page" : undefined}
                    aria-label={`Page ${p}${marks.length ? `, ${marks.length} ${marks.length === 1 ? "finding" : "findings"}` : ""}`}
                    className={`flex min-h-[52px] w-full flex-col items-center justify-center gap-1 rounded-[3px] border py-1.5 transition-colors ${
                      current === p ? "border-white bg-white text-ink" : "border-desk-line text-white hover:border-desk-text"
                    }`}
                  >
                    <span className="tab !text-[0.95rem]">{p}</span>
                    {marks.length > 0 && (
                      <span className="flex flex-wrap justify-center gap-[3px] px-1">
                        {marks.slice(0, 4).map((m) => (
                          <span key={m.f.id} className={current === p ? RISK_TEXT[m.f.risk] : "text-white"}>
                            <RiskShape risk={m.f.risk} size={10} />
                          </span>
                        ))}
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>
      )}

      <div ref={colRef} className={`min-w-0 flex-1 ${compact ? "pr-10" : "pr-12"}`}>
        {!doc && (
          <div className="sheet mx-auto aspect-[1/1.414] w-full max-w-[760px] overflow-hidden" role="status">
            <span className="sr-only">Opening the contract</span>
            <div className="reading h-[12%] w-full bg-gradient-to-b from-transparent via-desk/10 to-transparent" />
          </div>
        )}
        {doc && width > 0 && (
          <ol className="mx-auto max-w-[760px] space-y-6" aria-label="Contract pages">
            {sizes.map((s, i) => (
              <li key={i} data-page={i + 1}>
                <Page
                  doc={doc}
                  pageNo={i + 1}
                  total={sizes.length}
                  size={s}
                  width={Math.min(width, 760)}
                  marks={marksOn(i + 1)}
                  selected={selected}
                  focus={focus}
                  onSelect={onSelect}
                  compact={!!compact}
                />
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function Page(props: {
  doc: PDFDocumentProxy;
  pageNo: number;
  total: number;
  size: { w: number; h: number };
  width: number;
  marks: Marked[];
  selected: string | null;
  focus: { id: string; k: number } | null;
  onSelect: (id: string) => void;
  compact: boolean;
}) {
  const { doc, pageNo, total, size, width, marks, selected, focus, onSelect } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(pageNo <= 2);
  const height = Math.round((width * size.h) / size.w);

  useEffect(() => {
    if (near || !boxRef.current) return;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setNear(true), { rootMargin: "1200px 0px" });
    io.observe(boxRef.current);
    return () => io.disconnect();
  }, [near]);

  useEffect(() => {
    if (!near || !canvasRef.current) return;
    let task: RenderTask | null = null;
    let live = true;
    (async () => {
      const page = await doc.getPage(pageNo);
      if (!live || !canvasRef.current) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const vp = page.getViewport({ scale: (width / size.w) * dpr });
      const c = canvasRef.current;
      c.width = Math.floor(vp.width);
      c.height = Math.floor(vp.height);
      task = page.render({ canvas: c, viewport: vp });
      await task.promise.catch(() => undefined);
    })();
    return () => {
      live = false;
      task?.cancel();
    };
  }, [doc, pageNo, width, size.w, near]);

  return (
    <div ref={boxRef} className="relative" style={{ width, height }}>
      <div className="sheet absolute inset-0 overflow-hidden">
        <canvas ref={canvasRef} className="block h-full w-full" aria-label={`Page ${pageNo} of ${total}`} role="img" />
      </div>
      {marks.map((m) => {
        if (m.boxes.length === 0) return null;
        const c = RISK_BOX[m.f.risk];
        const on = selected === m.f.id;
        const top = Math.min(...m.boxes.map((b) => b[1]));
        const bottom = Math.max(...m.boxes.map((b) => b[3]));
        const left = Math.min(...m.boxes.map((b) => b[0]));
        const right = Math.max(...m.boxes.map((b) => b[2]));
        const pad = 0.004;
        return (
          <div key={m.f.id}>
            {/* The highlighted lines. Clicking them selects the finding. */}
            {m.boxes.map((b, i) => (
              <div
                key={i}
                onClick={() => onSelect(m.f.id)}
                className="absolute cursor-pointer mix-blend-multiply"
                style={{
                  left: `${(b[0] - pad) * 100}%`,
                  top: `${(b[1] - pad) * 100}%`,
                  width: `${(b[2] - b[0] + pad * 2) * 100}%`,
                  height: `${(b[3] - b[1] + pad * 2) * 100}%`,
                  background: c.fill,
                  borderBottom: `2px solid ${c.edge}`,
                }}
              />
            ))}
            {/* Outline of the whole quote; pulses when picked from the list. */}
            <div
              key={focus?.id === m.f.id ? focus.k : 0}
              data-box={m.f.id}
              aria-hidden="true"
              className={`pointer-events-none absolute rounded-[2px] ${focus?.id === m.f.id ? "pulse" : ""}`}
              style={{
                left: `${(left - pad * 2) * 100}%`,
                top: `${(top - pad * 2) * 100}%`,
                width: `${(right - left + pad * 4) * 100}%`,
                height: `${(bottom - top + pad * 4) * 100}%`,
                outline: on ? `2px solid ${c.edge}` : "none",
                outlineOffset: 2,
                scrollMarginTop: 84,
                ["--ring" as string]: c.ring,
              }}
            />
            {/* Margin bracket spanning the quoted lines. */}
            <div
              aria-hidden="true"
              className="absolute"
              style={{
                left: `${Math.min(right + 0.025, 0.955) * 100}%`,
                top: `${top * 100}%`,
                height: `${(bottom - top) * 100}%`,
                width: 4,
                background: c.edge,
                borderRadius: 2,
              }}
            />
            {/* The numbered flag, sticking out of the page edge. */}
            <button
              type="button"
              onClick={() => onSelect(m.f.id)}
              aria-pressed={on}
              aria-label={`Finding ${m.n}: ${m.f.title}. ${RISK_LABEL[m.f.risk]}, page ${m.f.page}`}
              className="absolute left-full flex min-h-[44px] items-center"
              style={{ top: `calc(${top * 100}% - 8px)` }}
            >
              <FlagTab
                n={m.n}
                risk={m.f.risk}
                className={`shadow-lift transition-transform ${on ? "translate-x-1 scale-110" : ""}`}
              />
            </button>
          </div>
        );
      })}
      <span aria-hidden="true" className="tab absolute bottom-2 right-3 !text-[0.7rem] text-ink-mute">
        {pageNo} / {total}
      </span>
    </div>
  );
}
