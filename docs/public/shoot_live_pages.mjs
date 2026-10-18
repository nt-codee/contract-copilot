// Capture more read-only pages of the deployed synthetic demo: key dates, keyword search, a law entry.
// GET/HEAD only. Guest search is keyword-only, so it starts no model or embedding call.
// node docs/public/shoot_live_pages.mjs OUT BASE --commit SHA --revision REV --synthetic-public [--query TEXT] [--entry ID]
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const args = process.argv.slice(2);
const option = (name, fallback) => { const i = args.indexOf(name); return i < 0 ? fallback : args[i + 1]; };
const [outArg, baseArg] = args;
const commit = option("--commit"), revision = option("--revision");
const query = option("--query", "cancel early"), entry = option("--entry", "sg-ucta1977-s11");
if (!outArg || !baseArg || !commit || !revision || !args.includes("--synthetic-public")) {
  throw new Error("Specify OUT BASE --commit SHA --revision REV --synthetic-public; use only verified synthetic public data.");
}
if (!/^[A-Za-z0-9_-]+$/.test(entry)) throw new Error("Invalid knowledge entry id.");
const base = new URL(baseArg).origin;
const out = path.resolve(outArg);
fs.mkdirSync(out, { recursive: true });
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../web/package.json"));
const { chromium } = require("playwright-core");
function chrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const cache = path.join(os.homedir(), "Library/Caches/ms-playwright");
  const dirs = fs.readdirSync(cache).filter((d) => /^chromium-\d+$/.test(d)).sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
  for (const d of dirs) for (const rel of ["chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", "chrome-linux/chrome"]) {
    const p = path.join(cache, d, rel);
    if (fs.existsSync(p)) return p;
  }
  throw new Error("Set CHROME to an installed Chromium executable.");
}
const manifest = {
  capture: { commit, revision, base_url: base, captured_at: new Date().toISOString(), mode: "live", data_scope: "synthetic-public", viewport: { width: 1440, height: 900 },
    recipe: `Explicit read-only demo entry; home key dates; library keyword search for "${query}"; knowledge entry ${entry}. GET/HEAD only.`,
    timing: "Screens held for readability; not a review-duration benchmark." },
  frames: [], screens: [], animations: [], requests: [], errors: [],
};
let browser;
try {
  browser = await chromium.launch({ executablePath: chrome() });
  const context = await browser.newContext({ viewport: manifest.capture.viewport, deviceScaleFactor: 1, reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (!["GET", "HEAD"].includes(request.method())) {
      manifest.errors.push(`Blocked unexpected ${request.method()} ${url.pathname}`);
      return route.abort();
    }
    if (url.origin === base && url.pathname.startsWith("/api/")) manifest.requests.push({ method: request.method(), path: url.pathname });
    return route.continue();
  });
  page.on("pageerror", (e) => manifest.errors.push(e.message));
  page.on("response", (r) => { if (new URL(r.url()).origin === base && r.status() >= 400) manifest.errors.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  const shot = async (name, opts = {}) => {
    await page.screenshot({ path: path.join(out, `${name}.png`), ...opts });
    manifest.frames.push({ input: `${name}.png`, route: new URL(page.url()).pathname, captured_at: new Date().toISOString() });
    console.log(`captured ${name}`);
  };
  await page.goto(base + "/workspace", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "View the read-only demo", exact: true }).click();
  await page.getByText("Reviews per day", { exact: true }).waitFor();
  await page.goto(base + "/", { waitUntil: "domcontentloaded" });
  await page.getByText("Coming up", { exact: true }).first().waitFor();
  await page.getByText("Reviewed lately", { exact: true }).first().waitFor();
  await page.waitForTimeout(2500);
  await shot("home");
  await page.goto(base + "/library", { waitUntil: "domcontentloaded" });
  await page.getByText("Past reviews", { exact: true }).waitFor();
  await page.waitForTimeout(2500);
  await shot("library-list");
  await page.getByRole("searchbox").or(page.getByRole("textbox")).first().fill(query);
  const searched = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/search");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await searched;
  await page.waitForTimeout(2500);
  await shot("library");
  await page.goto(base + `/knowledge/${entry}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("link", { name: /Open the official source/ }).waitFor();
  await page.waitForTimeout(2500);
  await shot("citation");
  if (manifest.errors.length) throw new Error(`Capture had ${manifest.errors.length} errors; inspect capture.json before using any image.`);
} finally {
  if (browser) await browser.close();
  fs.writeFileSync(path.join(out, "capture.json"), JSON.stringify(manifest, null, 2) + "\n");
}
