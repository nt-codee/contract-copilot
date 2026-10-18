// Shoot the signed-in screens from the offline-sample build of the web app (invented people and
// data: Kopi Kita Sdn Bhd). It serves web/dist-mock, so build that first with
//   cd web && VITE_MOCK=1 npx vite build --outDir dist-mock
// Usage: node docs/public/shoot_app.mjs [outdir]   (default /tmp/app-shots)
import { createRequire } from "node:module";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const require = createRequire(path.join(root, "web", "package.json"));
const { chromium } = require("playwright-core");
const out = process.argv[2] || "/tmp/app-shots";
fs.mkdirSync(out, { recursive: true });
const dist = path.join(root, "web", "dist-mock");

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const cache = path.join(os.homedir(), "Library/Caches/ms-playwright");
  for (const d of fs.existsSync(cache) ? fs.readdirSync(cache).sort().reverse() : []) {
    for (const rel of [
      "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
      "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    ]) {
      const p = path.join(cache, d, rel);
      if (d.startsWith("chromium-") && fs.existsSync(p)) return p;
    }
  }
  throw new Error("No Chromium found. Set CHROME.");
}

const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
  ".png": "image/png", ".json": "application/json", ".woff2": "font/woff2", ".pdf": "application/pdf", ".mjs": "text/javascript" };
const server = http.createServer((req, res) => {
  let p = path.join(dist, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(dist) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) p = path.join(dist, "index.html");
  res.writeHead(200, { "content-type": types[path.extname(p)] || "application/octet-stream" });
  fs.createReadStream(p).pipe(res);
});
await new Promise((r) => server.listen(4180, r));
const base = "http://localhost:4180";

const browser = await chromium.launch({ executablePath: findChrome() });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await ctx.addInitScript(() => localStorage.setItem("cc.signedIn", "1"));
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("pageerror", e.message));
// fullPage capture resizes the viewport, which makes some pages refetch and caught them mid-load.
// Resize first, let the page settle, then take a plain screenshot.
const tall = async (h) => { await page.setViewportSize({ width: 1440, height: h }); await page.waitForTimeout(1500); };
const shot = async (name, opts = {}) => { await page.screenshot({ path: path.join(out, `${name}-1440.png`), ...opts }); console.log("  " + name); };
const settle = async () => {
  await page.getByText(/^Loading…$|^Loading\.\.\.$/).first().waitFor({ state: "detached", timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1200);
};

// Home: the workspace overview
await page.goto(base + "/");
await page.getByText(/Across the company/).waitFor({ timeout: 30000 });
await settle();
await tall(1700);
await shot("overview");
await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(800);

// Sidebar with the account menu open
await page.getByRole("button", { name: /Kopi Kita Sdn Bhd/ }).first().click().catch((e) => console.log("account menu:", e.message.split("\n")[0]));
await page.waitForTimeout(600);
await shot("account-menu");
await page.keyboard.press("Escape");

// Activity and admin
await page.getByRole("link", { name: "Activity", exact: true }).click();
await settle();
await page.getByText(/Today|Yesterday/).first().waitFor({ timeout: 15000 }).catch(() => console.log("activity: still loading"));
await tall(1000);
await shot("activity");
await page.setViewportSize({ width: 1440, height: 900 });
await page.getByRole("link", { name: "Admin", exact: true }).click();
await settle();
await page.getByRole("cell").first().waitFor({ timeout: 15000 }).catch(() => console.log("admin: still loading"));
await tall(1500);
await shot("admin");

await browser.close();
server.close();
