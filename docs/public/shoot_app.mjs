// Optional OFFLINE capture only; never label these images as deployed UI.
// Build with VITE_MOCK=1 first into a separate directory, then:
// node docs/public/shoot_app.mjs OUT --dist DIR --commit SHA --mock
// Requires existing playwright-core/Chromium. No package install or external API calls.
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
const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
if (!args.includes("--mock") || !args.includes("--dist") || !args.includes("--commit") || !args[0]) {
  throw new Error("Supply OUT --dist DIR --commit SHA --mock for explicit offline synthetic captures.");
}
const out = path.resolve(args[0]);
fs.mkdirSync(out, { recursive: true });
const dist = path.resolve(option("--dist"));
const errors = [];
const frames = [];

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
  if (!(p === dist || p.startsWith(dist + path.sep)) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) p = path.join(dist, "index.html");
  res.writeHead(200, { "content-type": types[path.extname(p)] || "application/octet-stream" });
  fs.createReadStream(p).pipe(res);
});
const port = Number(process.env.CAPTURE_PORT || 4180);
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
const base = `http://127.0.0.1:${port}`;

let browser;
try {
browser = await chromium.launch({ executablePath: findChrome() });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await ctx.addInitScript(() => localStorage.setItem("cc.signedIn", "1"));
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(e.message));
await page.route("**/*", (route) => {
  const url = new URL(route.request().url());
  if (url.origin !== base || url.pathname.startsWith("/api/") || !["GET", "HEAD"].includes(route.request().method())) {
    errors.push("Blocked non-offline request: " + url.pathname);
    return route.abort();
  }
  return route.continue();
});
// fullPage capture resizes the viewport, which makes some pages refetch and caught them mid-load.
// Resize first, let the page settle, then take a plain screenshot.
const tall = async (h) => { await page.setViewportSize({ width: 1440, height: h }); await page.waitForTimeout(1500); };
const shot = async (name, opts = {}) => { await page.screenshot({ path: path.join(out, `${name}-1440.png`), ...opts }); frames.push({ input: `${name}-1440.png`, route: new URL(page.url()).pathname }); console.log("  " + name); };
const settle = async () => {
  await page.getByText(/^Loading…$|^Loading\.\.\.$/).first().waitFor({ state: "detached", timeout: 30000 });
  await page.waitForTimeout(1200);
};

// Home: the workspace overview
await page.goto(base + "/");
await page.getByRole("heading", { name: /Welcome back|Your workspace/ }).waitFor({ timeout: 30000 });
await settle();
await tall(1700);
await shot("overview");
await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(800);

// Sidebar with the account menu open
await page.getByRole("button", { name: /Kopi Kita Sdn Bhd/ }).first().click();
await page.waitForTimeout(600);
await shot("account-menu");
await page.keyboard.press("Escape");

// Activity and admin
await page.getByRole("link", { name: "Activity", exact: true }).click();
await settle();
await page.getByText(/Today|Yesterday/).first().waitFor({ timeout: 15000 });
await tall(1000);
await shot("activity");
await page.setViewportSize({ width: 1440, height: 900 });
await page.getByRole("link", { name: "Admin", exact: true }).click();
await settle();
await page.getByRole("cell").first().waitFor({ timeout: 15000 });
await tall(1500);
await shot("admin");

} catch (error) {
  errors.push(error.message);
} finally {
if (browser) await browser.close();
server.close();
fs.writeFileSync(path.join(out, "capture.json"), JSON.stringify({
  capture: { commit: option("--commit"), revision: "local-unpublished", mode: "mock", data_scope: "synthetic-public", base_url: base,
    captured_at: new Date().toISOString(), recipe: "Explicit offline signed-in synthetic workspace; not a live capture.", viewport: { width: 1440, height: 900 } },
  frames, errors,
}, null, 2) + "\n");
}
if (errors.length) throw new Error(`Offline capture had ${errors.length} errors; do not publish its images.`);
