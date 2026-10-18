import { defineConfig } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const API = process.env.VITE_API_PROXY || "https://contract-copilot-ordg3tjh7a-as.a.run.app";

// pdf.js needs its standard font files for PDFs that use the base-14 fonts
// (Times, Helvetica) without embedding them. Serve and emit them ourselves.
function pdfjsStandardFonts(): Plugin {
  const dir = join(process.cwd(), "node_modules/pdfjs-dist/standard_fonts");
  const prefix = "/pdfjs/standard_fonts/";
  return {
    name: "pdfjs-standard-fonts",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith(prefix)) return next();
        try {
          res.setHeader("Content-Type", "application/octet-stream");
          res.end(readFileSync(join(dir, req.url.slice(prefix.length).split("?")[0])));
        } catch {
          res.statusCode = 404;
          res.end();
        }
      });
    },
    generateBundle() {
      for (const f of readdirSync(dir)) {
        this.emitFile({ type: "asset", fileName: `pdfjs/standard_fonts/${f}`, source: readFileSync(join(dir, f)) });
      }
    },
  };
}

// Explicit opt-in (VITE_SAMPLE_PDF_STANDIN=1) local stand-in for GET /api/samples/{id}/pdf (served by the API in production), so
// the sample preview works under `vite` and `vite preview`. Never part of the built bundle.
function samplePdfs(): Plugin {
  const dir = join(process.cwd(), "../samples/contracts");
  const use = (mw: { use: (fn: (req: any, res: any, next: () => void) => void) => void }) =>
    mw.use((req, res, next) => {
      const m = req.url?.match(/^\/api\/samples\/([A-Za-z0-9_-]{1,64})\/pdf(?:\?|$)/);
      if (!m) return next();
      try {
        const body = readFileSync(join(dir, `${m[1]}.pdf`));
        res.setHeader("Content-Type", "application/pdf");
        res.end(body);
      } catch {
        res.statusCode = 404;
        res.end();
      }
    });
  return { name: "sample-pdfs", configureServer(s) { use(s.middlewares); }, configurePreviewServer(s) { use(s.middlewares); } };
}

const proxy = { "/api": { target: API, changeOrigin: true } };

export default defineConfig({
  plugins: [react(), pdfjsStandardFonts(), ...(process.env.VITE_SAMPLE_PDF_STANDIN === "1" ? [samplePdfs()] : [])],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});
