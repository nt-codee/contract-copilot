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

const proxy = { "/api": { target: API, changeOrigin: true } };

export default defineConfig({
  plugins: [react(), pdfjsStandardFonts()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});
