import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { openPdf } from "../pdf";

export interface ReviewPreviewSource {
  url: string;
  name: string;
  kind: "pdf" | "image";
}

/** Reads only the existing source; never starts a review or uploads a file. */
export default function ReviewPreview({ source, running }: { source: ReviewPreviewSource; running: boolean }) {
  const [retry, setRetry] = useState(0);
  return <section aria-label="Contract preview" className="min-w-0 overflow-hidden rounded border border-desk-line bg-desk-deep shadow-page">
    <div className="border-b border-desk-line px-4 py-3 text-white">
      <h2 className="font-semibold">Your contract</h2>
      <p className="meta break-words text-desk-text">{source.name}</p>
    </div>
    <SourcePage key={`${source.url}-${retry}`} source={source} running={running} retry={() => setRetry((n) => n + 1)} />
  </section>;
}

function SourcePage({ source, running, retry }: { source: ReviewPreviewSource; running: boolean; retry: () => void }) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(0);
  const [text, setText] = useState("");
  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(1, Math.floor(entry.contentRect.width - 32))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let live = true;
    let loaded: PDFDocumentProxy | null = null;
    if (source.kind === "pdf") {
      openPdf(source.url).then((pdf) => {
        loaded = pdf;
        if (!live) { void pdf.loadingTask.destroy(); return; }
        setDoc(pdf);
      }).catch(() => live && setState("error"));
    }
    return () => { live = false; void loaded?.loadingTask.destroy(); };
  }, [source.url, source.kind]);

  // A slow/unavailable source must not leave a permanent blank loading surface.
  useEffect(() => {
    if (state !== "loading") return;
    const timer = window.setTimeout(() => setState("error"), 15000);
    return () => window.clearTimeout(timer);
  }, [state]);

  useEffect(() => {
    if (!doc || !width || source.kind !== "pdf" || state === "error") return;
    let live = true;
    let render: RenderTask | undefined;
    setState("loading");
    setText("");
    (async () => {
      const pdfPage = await doc.getPage(page);
      const base = pdfPage.getViewport({ scale: 1 });
      const viewport = pdfPage.getViewport({ scale: Math.min(width, 1000) / base.width * zoom });
      const el = canvas.current;
      const context = el?.getContext("2d");
      if (!live || !el || !context) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      el.width = Math.round(viewport.width * ratio);
      el.height = Math.round(viewport.height * ratio);
      el.style.width = `${viewport.width}px`;
      el.style.height = `${viewport.height}px`;
      render = pdfPage.render({ canvas: el, canvasContext: context, viewport, transform: [ratio, 0, 0, ratio, 0, 0] });
      await render.promise;
      const content = await pdfPage.getTextContent();
      if (!live) return;
      setText(content.items.map((item) => "str" in item ? item.str : "").join(" "));
      setState((current) => current === "error" ? "error" : "ready");
    })().catch(() => live && setState("error"));
    return () => { live = false; render?.cancel(); };
    // State is observed only to prevent rendering after failure, not to trigger another render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, page, width, zoom, source.kind]);

  return <>
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-desk-line px-3 py-2 text-white">
      {source.kind === "pdf" && <div className="flex flex-wrap items-center gap-1">
        <button type="button" className="btn-desk !min-h-[44px] !px-3" aria-label="Previous contract page" disabled={!doc || page <= 1 || state === "error"} onClick={() => setPage((n) => n - 1)}>←</button>
        <span className="meta px-1">{doc ? `Page ${page} of ${doc.numPages}` : "Opening pages"}</span>
        <button type="button" className="btn-desk !min-h-[44px] !px-3" aria-label="Next contract page" disabled={!doc || page >= doc.numPages || state === "error"} onClick={() => setPage((n) => n + 1)}>→</button>
      </div>}
      <div className="flex flex-wrap items-center gap-1" aria-label="Preview zoom">
        <button type="button" className="btn-desk !min-h-[44px] !px-3" aria-label="Zoom out of contract" disabled={zoom <= 1 || state === "error"} onClick={() => setZoom((n) => Math.max(1, n - .25))}>−</button>
        <button type="button" className="btn-desk !min-h-[44px] !px-3" onClick={() => setZoom(1)} disabled={state === "error"}>Fit width</button>
        <button type="button" className="btn-desk !min-h-[44px] !px-3" aria-label="Zoom into contract" disabled={zoom >= 2 || state === "error"} onClick={() => setZoom((n) => Math.min(2, n + .25))}>+</button>
      </div>
    </div>
    <div ref={frame} role="region" aria-label="Contract source pages" tabIndex={0} className="relative h-[60vh] min-h-[320px] max-h-[760px] overflow-auto overscroll-contain p-4 outline-offset-[-4px]">
      {state === "loading" && <p role="status" className="mb-3 text-sm text-desk-text">Opening your contract…</p>}
      {state === "error" ? <div role="status" className="mx-auto max-w-sm py-12 text-center text-desk-text">
        <p>The contract preview could not be loaded. {running ? "Your review can still finish; you can read the source with its result." : "Your earlier review is ready; you can read the source with its result."}</p>
        <button type="button" className="btn-desk mt-4" onClick={retry}>Try the preview again</button>
      </div> : <div className="relative w-fit min-w-full">
        {source.kind === "image" ? <img src={source.url} alt={`Contract source: ${source.name}`} style={{ width: `${zoom * 100}%`, maxWidth: "none" }} onLoad={() => setState((current) => current === "error" ? "error" : "ready")} onError={() => setState("error")} /> : <canvas ref={canvas} role="img" aria-label={`Page ${page} of ${source.name}`} className="mx-auto bg-white shadow-page" />}
        {running && state === "ready" && <span aria-hidden="true" className="review-source-scan pointer-events-none absolute inset-x-0 top-0 h-1 bg-accent-bright/60" />}
        {text && <p className="sr-only">{text}</p>}
      </div>}
    </div>
    <p className="meta border-t border-desk-line px-4 py-3 text-desk-text">{running ? "Read the source while the review runs. The review status is shown above." : "Read the source from this earlier review."}</p>
  </>;
}
