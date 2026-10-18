// Lazy pdf.js loader. The library and its worker are bundled and served by us.
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { locateQuote } from "./locate";
import type { Box, TextRun } from "./locate";

let lib: Promise<typeof import("pdfjs-dist")> | null = null;

function pdfjs() {
  if (!lib) {
    lib = import("pdfjs-dist").then((m) => {
      m.GlobalWorkerOptions.workerSrc = workerUrl;
      return m;
    });
  }
  return lib;
}

export async function openPdf(url: string): Promise<PDFDocumentProxy> {
  const [m, { pdfHeaders }] = await Promise.all([pdfjs(), import("./api")]);
  const httpHeaders = await pdfHeaders();
  return m.getDocument({ url, httpHeaders, standardFontDataUrl: "/pdfjs/standard_fonts/" }).promise;
}

/** Locates quotes on one page from its text layer. */
export async function locateOnPage(doc: PDFDocumentProxy, pageNo: number, quotes: string[]): Promise<Box[][]> {
  const page = await doc.getPage(pageNo);
  const [x0, y0, x1, y1] = page.view;
  const tc = await page.getTextContent();
  const runs: TextRun[] = [];
  for (const it of tc.items) {
    if (!("str" in it)) continue;
    const t = it.transform as number[];
    runs.push({ str: it.str, width: it.width, height: it.height, hasEOL: it.hasEOL, transform: [t[0], t[1], t[2], t[3], t[4] - x0, t[5] - y0] });
  }
  return quotes.map((q) => locateQuote(runs, x1 - x0, y1 - y0, q));
}
