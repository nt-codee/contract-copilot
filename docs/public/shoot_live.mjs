// Shoot the README and deck screens from the LIVE site, signed out, at 1440x900.
//
//   node docs/public/shoot_live.mjs [outdir] [base-url]     defaults: /tmp/live-shots, the public demo
//
// Then: uv run docs/public/make_assets.py --shots <outdir>   (crops and resizes them into docs/public/screens)
// Makes one sample review and one reply draft on the live service. Uses playwright-core and the Chromium
// already cached for the web app; installs nothing.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs'; import path from 'node:path'; import os from 'node:os';
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, '..', '..', 'web', 'package.json'));
const { chromium } = require('playwright-core');
const out = path.resolve(process.argv[2] || '/tmp/live-shots');
const base = process.argv[3] || 'https://contract-copilot-ordg3tjh7a-as.a.run.app';
fs.mkdirSync(out, { recursive: true });
const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright');
const dir = fs.readdirSync(cache).filter(d => /^chromium-\d+$/.test(d)).sort((a,b)=>b.split('-')[1]-a.split('-')[1])[0];
const exe = path.join(cache, dir, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const b = await chromium.launch({ executablePath: exe });
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push('pageerror ' + e.message));
page.on('response', r => { if (r.status() >= 400) errs.push(r.status() + ' ' + r.url().replace(base, '')); });
const log = (m) => console.log(m);
const shot = async (name, opts = {}) => { await page.screenshot({ path: `${out}/${name}-1440.png`, ...opts }); log('shot ' + name); };
async function step(name, fn) { try { await fn(); } catch (e) { log(`FAILED ${name}: ${e.message.split('\n')[0]}`); } }

// keep the sample review/draft calls to one each
const calls = new Map();
await page.route(/\/api\/reviews\/(sample\/[^/]+|[^/]+\/negotiation)$/, async (route) => {
  if (route.request().method() !== 'POST') return route.continue();
  const key = route.request().url();
  if (!calls.has(key)) { const r = await route.fetch({ timeout: 120000 }); calls.set(key, { status: r.status(), headers: r.headers(), body: await r.body() }); }
  if (key.includes('/sample/')) await new Promise(r => setTimeout(r, 3000));
  await route.fulfill(calls.get(key));
});

await page.goto(base + '/');
await step('home', async () => {
  await page.getByRole('button', { name: /Try a sample contract/ }).waitFor({ timeout: 30000 });
  await page.waitForTimeout(5200);
  await shot('home');
});
let reviewUrl;
await step('review', async () => {
  await page.getByRole('button', { name: /Try a sample contract/ }).click();
  await page.getByText('Reviewing', { exact: true }).waitFor({ timeout: 30000 });
  await page.waitForTimeout(1200); await shot('progress');
  await page.waitForURL('**/reviews/**', { timeout: 150000 });
  reviewUrl = page.url().split('?')[0];
  await page.getByRole('button', { name: /Draft my reply to the vendor/ }).waitFor({ timeout: 60000 });
  await page.locator('[data-box]').first().waitFor({ timeout: 60000 });
  await page.waitForTimeout(2400);
  await shot('review');
  await page.locator('aside [aria-labelledby="findings-title"] li h3 button').nth(1).click();
  await page.waitForTimeout(1400);
  const chip = page.locator('aside button[aria-controls$="-src-panel"]').first();
  if (await chip.count()) await chip.click();
  await page.waitForTimeout(300);
  await shot('review-selected');
});
await step('draft', async () => {
  await page.getByRole('button', { name: /Draft my reply to the vendor/ }).click();
  await page.getByRole('heading', { name: 'Your reply to the vendor' }).waitFor({ timeout: 120000 });
  await page.waitForTimeout(4200);
  await shot('review-draft');
});
await step('library', async () => {
  await page.goto(base + '/library');
  await page.getByText('Past reviews').waitFor({ timeout: 30000 });
  await page.getByLabel('Search your contracts').fill('cancel early');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByText(/Best match|Nothing matches/).waitFor({ timeout: 30000 });
  await page.waitForTimeout(500);
  await shot('library', { fullPage: true });
});
await step('knowledge', async () => {
  await page.goto(base + '/knowledge');
  await page.getByRole('heading', { name: 'What every flag is checked against' }).waitFor({ timeout: 30000 });
  await page.getByText(/\d+ entr(y|ies)/).waitFor({ timeout: 30000 });
  await page.waitForTimeout(400); await shot('knowledge');
  await page.locator('a[href^="/knowledge/"]').first().click();
  await page.getByText('Open the official source').waitFor({ timeout: 30000 });
  await page.waitForTimeout(600); await shot('knowledge-entry');
});
await step('rules', async () => {
  await page.goto(base + '/rules');
  await page.getByText('Rules your reviews check').waitFor({ timeout: 30000 });
  await page.getByText(/Loading your documents/).waitFor({ state: 'detached', timeout: 30000 }).catch(() => {});
  await page.getByText(/Reading your reviews/).waitFor({ state: 'detached', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(500); await shot('rules', { fullPage: true });
});
await step('home-after', async () => {
  await page.goto(base + '/');
  await page.getByRole('button', { name: /Try a sample contract/ }).waitFor({ timeout: 30000 });
  await page.waitForTimeout(5200); await shot('home-after');
});
log('errors: ' + JSON.stringify(errs.slice(0, 10)));
await b.close();
