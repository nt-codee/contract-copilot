// Capture the deployed synthetic demo. No new reviews, drafts, uploads or search calls.
// node docs/public/shoot_live.mjs OUT BASE --review-id ID --commit SHA --revision REV --synthetic-public
// Optional: --cached-sample-id ID permits ONLY that explicit guest cached-opening POST.
// Confirm that sample is cached before using the option. No response delay or phase simulation.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const args = process.argv.slice(2);
const option = (name) => { const i = args.indexOf(name); return i < 0 ? undefined : args[i + 1]; };
const [outArg, baseArg] = args;
const reviewId = option("--review-id"), commit = option("--commit"), revision = option("--revision");
const sampleId = option("--cached-sample-id");
if (!outArg || !baseArg || !reviewId || !commit || !revision || !args.includes("--synthetic-public")) {
  throw new Error("Specify OUT BASE --review-id ID --commit SHA --revision REV --synthetic-public; use only verified synthetic public data.");
}
if (![reviewId, ...(sampleId ? [sampleId] : [])].every((id) => /^[A-Za-z0-9_-]+$/.test(id))) throw new Error("Invalid review/sample id.");
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
  capture: { commit, revision, base_url: base, captured_at: new Date().toISOString(), mode: "live", data_scope: "synthetic-public", viewport: { width: 1440, height: 900 }, review_id: reviewId,
    recipe: "Explicit read-only demo entry; cached review GET; select finding; expand recorded trace; read rules/workspace. Optional guest cached-sample opening only, never refresh or draft.",
    timing: "Screens held for readability; interaction media is not a review-duration benchmark." },
  frames: [], screens: [], animations: [], requests: [], errors: [],
};
let browser;
try {
  browser = await chromium.launch({ executablePath: chrome() });
  const context = await browser.newContext({ viewport: manifest.capture.viewport, deviceScaleFactor: 1, reducedMotion: "reduce" });
  const page = await context.newPage();
  const samplePath = sampleId ? `/api/reviews/sample/${sampleId}` : null;
  await page.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    const allowedPost = samplePath && request.method() === "POST" && url.origin === base && url.pathname === samplePath && !url.search && request.headers()["x-guest"] === "demo";
    if (!["GET", "HEAD"].includes(request.method()) && !allowedPost) {
      manifest.errors.push(`Blocked unexpected ${request.method()} ${url.pathname}`);
      return route.abort();
    }
    if (url.origin === base && url.pathname.startsWith("/api/")) manifest.requests.push({ method: request.method(), path: url.pathname });
    return route.continue();
  });
  page.on("pageerror", (e) => manifest.errors.push(e.message));
  page.on("response", (r) => { if (new URL(r.url()).origin === base && r.status() >= 400) manifest.errors.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  const shot = async (name) => {
    await page.screenshot({ path: path.join(out, `${name}.png`) });
    manifest.frames.push({ input: `${name}.png`, route: new URL(page.url()).pathname, captured_at: new Date().toISOString() });
    console.log(`captured ${name}`);
  };
  await page.goto(base + "/workspace", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "View the read-only demo", exact: true }).click();
  await page.getByRole("tab", { name: "People and settings", exact: true }).waitFor();
  await page.getByText("Reviews per day", { exact: true }).waitFor();
  await shot("workspace");
  await page.getByRole("tab", { name: "People and settings", exact: true }).click();
  await page.getByRole("heading", { name: /^Members, \d+/ }).waitFor();
  await shot("people");
  if (sampleId) {
    await page.goto(base + `/sample/${sampleId}`, { waitUntil: "domcontentloaded" });
    await page.locator("canvas").first().waitFor();
    await shot("sample");
    const opened = page.waitForResponse((r) => new URL(r.url()).pathname === samplePath && r.request().method() === "POST");
    await page.getByRole("button", { name: "Review this contract", exact: true }).click();
    const response = await opened;
    if (!response.ok() || !["cached", "copied"].includes(response.headers()["x-review-source"])) throw new Error("Sample opening did not confirm a cached/copied result.");
    const earlier = page.getByText("Earlier review", { exact: true });
    await earlier.waitFor();
    // The existing earlier-result screen is brief. Wait only within that real transition;
    // never hold the response or lengthen phases to manufacture a source-preview capture.
    await page.getByText("Opening your contract…", { exact: true }).waitFor({ state: "detached", timeout: 1800 });
    await page.getByRole("region", { name: "Contract source pages", exact: true }).locator("canvas").waitFor({ timeout: 500 });
    if (!await earlier.isVisible()) throw new Error("Cached earlier screen ended before its source preview settled; recapture instead of relabelling a result frame.");
    await shot("cached-opening");
    await page.waitForURL("**/reviews/**");
  }
  await page.goto(base + `/reviews/${reviewId}`, { waitUntil: "domcontentloaded" });
  await page.locator("canvas").first().waitFor();
  await page.locator("[data-box]").first().waitFor();
  await shot("reader");
  const findings = page.locator('#rail [aria-labelledby="findings-title"] h3 button');
  const count = await findings.count();
  if (count < 1) throw new Error("The selected synthetic review has no finding to capture.");
  const selected = findings.nth(Math.min(1, count - 1));
  if (await selected.getAttribute("aria-expanded") !== "true") await selected.click();
  // Expanding a finding does not navigate the PDF. Use its explicit source action and wait for
  // the page navigation's observed current-page state before claiming a matching source frame.
  const panelId = await selected.getAttribute("aria-controls");
  if (!panelId) throw new Error("Selected finding has no details panel.");
  const where = page.locator(`[id=${JSON.stringify(panelId)}]`).getByRole("button", { name: /show it on the contract$/ });
  const targetPage = (await where.innerText()).match(/page\s+(\d+)/i)?.[1];
  if (!targetPage) throw new Error("Selected finding has no source page label.");
  await where.click();
  const sourcePage = page.getByRole("navigation", { name: "Pages", exact: true }).getByRole("button", { name: new RegExp(`^Page ${targetPage}(?:,|$)`) });
  await sourcePage.and(page.locator('[aria-current="page"]')).waitFor();
  await shot("finding");
  await page.locator("summary").filter({ hasText: "How this was checked" }).click();
  await page.getByRole("button", { name: "Expand X-ray view", exact: true }).click();
  await page.getByRole("dialog", { name: "Review X-ray", exact: true }).waitFor();
  await shot("trace");
  await page.getByRole("button", { name: "Close X-ray", exact: true }).click();
  await page.goto(base + "/rules", { waitUntil: "domcontentloaded" });
  await page.getByText("Rules your reviews check", { exact: true }).waitFor();
  await page.getByText(/Loading your documents/).waitFor({ state: "detached", timeout: 30000 });
  await page.getByText(/Reading your reviews/).waitFor({ state: "detached", timeout: 60000 });
  await shot("rules");
  manifest.screens = ["reader", "trace", "rules", "workspace"].map((name) => ({ input: `${name}.png`, output: `screens/${name}.png`, max_width: 1280 }));
  if (sampleId) manifest.screens.push({ input: "cached-opening.png", output: "screens/cached-opening.png", max_width: 1280 });
  manifest.animations = [{ output: "demo-review.gif", max_width: 1080, caption: "Saved synthetic review; actual page and recorded trace interactions, held for readability.", frames: ["reader", "finding", "trace"].map((name) => ({ input: `${name}.png`, duration_ms: 2000 })) }];
  if (sampleId) manifest.animations.push({ output: "demo-cached-opening.gif", max_width: 1080, caption: "Synthetic sample preview and opening an earlier result; no fresh review or replayed stages.", frames: ["sample", "cached-opening", "reader"].map((name) => ({ input: `${name}.png`, duration_ms: 1800 })) });
  if (manifest.errors.length) throw new Error(`Capture had ${manifest.errors.length} errors; inspect capture.json before using any image.`);
} finally {
  if (browser) await browser.close();
  fs.writeFileSync(path.join(out, "capture.json"), JSON.stringify(manifest, null, 2) + "\n");
}
