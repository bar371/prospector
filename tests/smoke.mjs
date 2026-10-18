// Smoke test against a deployed (or local) Prospect site.
// Usage: node tests/smoke.mjs <url> [screenshot-dir]
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const url = process.argv[2] || 'http://localhost:8000/';
const outDir = process.argv[3] || 'smoke-output';
mkdirSync(outDir, { recursive: true });

const failures = [];
const check = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`);
  if (!ok) failures.push(msg);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
let tilesOk = 0;
let tilesFailed = 0;
page.on('response', (r) => {
  if (r.url().includes('service.pdok.nl/brt/achtergrondkaart')) r.ok() ? tilesOk++ : tilesFailed++;
});

const res = await page.goto(url, { waitUntil: 'load' });
check(res?.ok(), `page loads (${res?.status()}) at ${url}`);
check(await page.evaluate(() => typeof maplibregl === 'object'), 'MapLibre loaded');
await page.waitForTimeout(4000);
check(tilesOk > 0, `PDOK basemap tiles loaded (${tilesOk} ok, ${tilesFailed} failed)`);
await page.screenshot({ path: `${outDir}/1-start.png` });

// Address search via PDOK Locatieserver.
await page.fill('#q', 'Zamenhofstraat Leiden');
const suggestion = page.locator('#suggestions li[data-id]').first();
await suggestion.waitFor({ timeout: 10000 }).catch(() => {});
const suggestions = await page.locator('#suggestions li[data-id]').allTextContents();
check(suggestions.length > 0, `search suggestions: ${suggestions.slice(0, 3).join(' | ') || 'none'}`);

if (suggestions.length > 0) {
  await suggestion.click();
  await page.locator('#sheet-body dl.facts').waitFor({ timeout: 10000 }).catch(() => {});
  const sheet = (await page.locator('#sheet-body').innerText()).replace(/\s+/g, ' ');
  check(/Leiden/.test(sheet), `place card shows Leiden: "${sheet.slice(0, 160)}"`);
  check(/Buurt/.test(sheet), 'place card shows buurt');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${outDir}/2-zamenhofstraat.png` });
}

// Second test area preset.
await page.click('.preset[data-q^="Rijndijk"]').catch(() => {});
await page.waitForFunction(() => /Rijndijk/.test(document.querySelector('#sheet-body h1')?.textContent), null, { timeout: 10000 }).catch(() => {});
const title2 = await page.locator('#sheet-body h1').innerText();
check(/Rijndijk/.test(title2), `preset Rijndijk opens a place card: "${title2}"`);
await page.waitForTimeout(3000);
await page.screenshot({ path: `${outDir}/3-rijndijk.png` });

check(pageErrors.length === 0, `no page errors${pageErrors.length ? ': ' + pageErrors.join('; ') : ''}`);

await browser.close();
if (failures.length) {
  console.log(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log('\nAll checks passed');
