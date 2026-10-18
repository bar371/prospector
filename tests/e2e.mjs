// End-to-end tests for Prospect, run in a real Chromium against a served copy of app/
// (local or the live GitHub Pages site). Needs internet: it uses the real PDOK services.
// Usage: node tests/e2e.mjs <url> [output-dir]
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const BASE = (process.argv[2] || 'http://localhost:8000/').replace(/\/?$/, '/');
const OUT = process.argv[3] || 'test-output';
mkdirSync(OUT, { recursive: true });

const results = [];
let current = '';
const check = (ok, msg) => {
  results.push({ test: current, ok: !!ok, msg });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`);
};

const browser = await chromium.launch();

// Opens the app in a fresh context and collects errors and PDOK traffic.
async function open({ viewport = { width: 390, height: 844 }, colorScheme = 'light', mobile = true } = {}) {
  const context = await browser.newContext({ viewport, colorScheme, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 2 });
  const page = await context.newPage();
  const log = { errors: [], tilesOk: 0, tilesFailed: 0, apiFailed: [] };
  const addError = (msg) => { const line = msg.split('\n')[0]; if (!log.errors.includes(line)) log.errors.push(line); };
  page.on('pageerror', (e) => addError(e.message));
  page.on('console', (m) => m.type() === 'error' && addError(m.text()));
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('service.pdok.nl/brt/achtergrondkaart')) r.ok() ? log.tilesOk++ : log.tilesFailed++;
    if (u.includes('locatieserver') && !r.ok()) log.apiFailed.push(`${r.status()} ${u}`);
  });
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => window.maplibregl && document.querySelector('.maplibregl-canvas'));
  return { context, page, log };
}

const sheetText = async (page) => (await page.locator('#sheet-body').innerText()).replace(/\s+/g, ' ');
const facts = (page) => page.evaluate(() =>
  Object.fromEntries([...document.querySelectorAll('#sheet-body dl.facts dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent])));
const waitForTitle = (page, re) =>
  page.waitForFunction((src) => new RegExp(src).test(document.querySelector('#sheet-body h1')?.textContent || ''), re.source, { timeout: 15000 });
async function test(name, fn) {
  current = name;
  console.log(`\n${name}`);
  try {
    await fn();
  } catch (err) {
    check(false, `threw: ${err.message.split('\n')[0]}`);
  }
}

// 1. Static assets and install metadata.
await test('Assets and install metadata', async () => {
  const { context, page } = await open();
  const req = context.request;
  const manifestRes = await req.get(BASE + 'manifest.webmanifest');
  check(manifestRes.ok(), `manifest served (${manifestRes.status()})`);
  const manifest = await manifestRes.json().catch(() => null);
  check(manifest?.display === 'standalone' && manifest?.start_url, 'manifest has display=standalone and start_url');
  for (const icon of manifest?.icons || []) {
    const r = await req.get(BASE + icon.src);
    check(r.ok(), `manifest icon ${icon.src} (${r.status()})`);
  }
  const touch = await page.getAttribute('link[rel="apple-touch-icon"]', 'href');
  const touchRes = await req.get(BASE + touch);
  check(touchRes.ok(), `apple-touch-icon ${touch} (${touchRes.status()})`);
  const png = await touchRes.body();
  check(png.readUInt32BE(16) === 180 && png.readUInt32BE(20) === 180, 'apple-touch-icon is 180x180');
  check(await page.getAttribute('meta[name="apple-mobile-web-app-capable"]', 'content') === 'yes', 'iOS standalone meta tag present');
  check(/viewport-fit=cover/.test(await page.getAttribute('meta[name="viewport"]', 'content')), 'viewport covers the iPhone notch area');
  for (const path of ['styles.css', 'app.js', 'sw.js', 'vendor/maplibre-gl-4.7.1/maplibre-gl.js', 'vendor/maplibre-gl-4.7.1/maplibre-gl.css']) {
    const r = await req.get(BASE + path);
    check(r.ok(), `${path} (${r.status()})`);
  }
  await context.close();
});

// 2. Map and basemap tiles, including panning and zooming.
await test('Map and PDOK basemap', async () => {
  const { context, page, log } = await open();
  await page.waitForTimeout(3000);
  check(log.tilesOk > 0 && log.tilesFailed === 0, `initial tiles: ${log.tilesOk} ok, ${log.tilesFailed} failed`);
  const before = log.tilesOk;
  const box = await page.locator('#map').boundingBox();
  await page.mouse.move(box.width / 2, box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.width / 2 - 150, box.height / 2 - 100, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(2000);
  check(log.tilesOk > before, `panning loads new tiles (${log.tilesOk - before} more)`);
  check(log.tilesFailed === 0, 'no tile failures after panning');
  check(await page.locator('.maplibregl-ctrl-attrib').count() === 1, 'PDOK attribution shown');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

// 3. Search behaviour.
await test('Search: address result', async () => {
  const { context, page, log } = await open();
  await page.fill('#q', 'Zamenhofstraat 25 Leiden');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  const first = await page.locator('#suggestions li[data-id]').first().innerText();
  check(/Zamenhofstraat 25/.test(first), `first suggestion is the address: "${first.replace(/\s+/g, ' ')}"`);
  await page.locator('#suggestions li[data-id]').first().click();
  await waitForTitle(page, /Zamenhofstraat 25/);
  const f = await facts(page);
  check(f.Type === 'adres', `type adres (${f.Type})`);
  check(f.Postcode === '2312NX', `postcode 2312NX (${f.Postcode})`);
  check(/Noordvest/.test(f.Buurt || ''), `buurt Noordvest (${f.Buurt})`);
  check(/Binnenstad-Noord/.test(f.Wijk || ''), `wijk Binnenstad-Noord (${f.Wijk})`);
  check(f.Gemeente === 'Leiden', `gemeente Leiden (${f.Gemeente})`);
  check(await page.locator('#suggestions').isHidden(), 'suggestion list closes after choosing');
  check(await page.inputValue('#q') === 'Zamenhofstraat 25, 2312NX Leiden', `search box shows the choice ("${await page.inputValue('#q')}")`);
  await page.waitForTimeout(2500);
  const marker = await page.locator('.maplibregl-marker').boundingBox();
  const vp = page.viewportSize();
  check(marker && marker.x > 0 && marker.x < vp.width && marker.y > 0 && marker.y < vp.height * 0.7,
    `marker visible above the bottom sheet (${marker ? `${Math.round(marker.x)},${Math.round(marker.y)}` : 'none'})`);
  await page.screenshot({ path: `${OUT}/search-address.png` });
  check(log.apiFailed.length === 0, `no Locatieserver failures${log.apiFailed.length ? ': ' + log.apiFailed.join(' | ') : ''}`);
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Search: street result gets buurt from nearest address', async () => {
  const { context, page } = await open();
  await page.fill('#q', 'Zamenhofstraat Leiden');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.locator('#suggestions li', { hasText: 'weg' }).first().click();
  await waitForTitle(page, /^Zamenhofstraat, Leiden$/);
  await page.locator('#sheet-body dt', { hasText: 'Buurt' }).waitFor({ timeout: 10000 }).catch(() => {});
  const f = await facts(page);
  check(f.Type === 'weg', `type weg (${f.Type})`);
  check(/Noordvest/.test(f.Buurt || ''), `buurt filled in (${f.Buurt})`);
  await context.close();
});

await test('Search: Enter key, short and unknown queries', async () => {
  const { context, page, log } = await open();
  await page.fill('#q', 'Ri');
  await page.waitForTimeout(800);
  check(await page.locator('#suggestions').isHidden(), 'no suggestions for under 3 characters');
  await page.fill('#q', 'Rijndijk 33 Hazerswoude');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.press('#q', 'Enter');
  await waitForTitle(page, /Rijndijk 33/);
  const f = await facts(page);
  check(f.Postcode === '2394AC', `Enter opens first result: Rijndijk 33, ${f.Postcode}`);
  check(f.Gemeente === 'Alphen aan den Rijn', `gemeente Alphen aan den Rijn (${f.Gemeente})`);
  await page.fill('#q', 'qxqxqxzzzy');
  await page.waitForTimeout(1500);
  check(await page.locator('#suggestions').isHidden(), 'nonsense query shows no suggestions');
  await page.press('#q', 'Enter');
  await page.waitForTimeout(1500);
  check(/Rijndijk 33/.test(await page.locator('#sheet-body h1').innerText()), 'Enter on a nonsense query leaves the current card alone');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Search: input is escaped, never run as HTML', async () => {
  const { context, page, log } = await open();
  await page.evaluate(() => { window.__pwned = false; });
  await page.fill('#q', '<img src=x onerror="window.__pwned=true">leiden');
  await page.waitForTimeout(2000);
  check(await page.evaluate(() => window.__pwned) === false, 'no script injection through the search box');
  check(await page.locator('#suggestions img').count() === 0, 'no HTML injected into the suggestion list');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Search: Locatieserver down', async () => {
  const { context, page, log } = await open();
  await page.route('**/locatieserver/**', (r) => r.fulfill({ status: 503, body: 'down' }));
  await page.fill('#q', 'Zamenhofstraat Leiden');
  await page.waitForTimeout(1500);
  check(await page.locator('#suggestions').isHidden(), 'no broken suggestion list when the service is down');
  await page.click('.preset >> nth=0');
  await page.waitForTimeout(1500);
  const pageErrors = log.errors.filter((e) => !/503|Failed to load resource/.test(e));
  check(pageErrors.length === 0, `app does not crash${pageErrors.length ? ': ' + pageErrors.join(' | ') : ''}`);
  await context.close();
});

// 4. Test-area presets.
for (const [label, title, buurtRe, gemeente] of [
  ['Zamenhofstraat', /^Zamenhofstraat, Leiden$/, /Noordvest/, 'Leiden'],
  ['Rijndijk', /^Rijndijk, Hazerswoude-Rijndijk$/, /./, 'Alphen aan den Rijn'],
]) {
  await test(`Preset: ${label}`, async () => {
    const { context, page, log } = await open();
    await page.click(`.preset[data-q^="${label}"]`);
    await waitForTitle(page, title).catch(() => {});
    const t = await page.locator('#sheet-body h1').innerText();
    check(title.test(t), `opens the street, not the town ("${t}")`);
    await page.locator('#sheet-body dt', { hasText: 'Buurt' }).waitFor({ timeout: 10000 }).catch(() => {});
    const f = await facts(page);
    check(f.Type === 'weg', `type weg (${f.Type})`);
    check(buurtRe.test(f.Buurt || ''), `buurt shown (${f.Buurt})`);
    check(f.Gemeente === gemeente, `gemeente ${gemeente} (${f.Gemeente})`);
    check(await page.locator('#sheet-body .preset').count() === 2, 'both presets still available on the card');
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/preset-${label.toLowerCase()}.png` });
    check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
    await context.close();
  });
}

// 5. Layout on different screens and colour schemes.
const SCREENS = [
  ['iphone-se', { width: 320, height: 568 }, true],
  ['iphone-15', { width: 393, height: 852 }, true],
  ['iphone-15-landscape', { width: 852, height: 393 }, true],
  ['ipad', { width: 820, height: 1180 }, true],
  ['desktop', { width: 1440, height: 900 }, false],
];
for (const [name, viewport, mobile] of SCREENS) {
  for (const colorScheme of ['light', 'dark']) {
    await test(`Layout: ${name} ${colorScheme}`, async () => {
      const { context, page, log } = await open({ viewport, colorScheme, mobile });
      await page.click('.preset >> nth=0');
      await waitForTitle(page, /Zamenhofstraat/).catch(() => {});
      await page.waitForTimeout(2500);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(overflow <= 0, `no horizontal scroll (${overflow}px)`);
      const search = await page.locator('#q').boundingBox();
      check(search && search.y >= 0 && search.x >= 8 && search.x + search.width <= viewport.width - 8, 'search box fully on screen');
      const sheet = await page.locator('#sheet').boundingBox();
      check(sheet && sheet.y + sheet.height <= viewport.height + 1 && sheet.height <= viewport.height * 0.6, `sheet fits (${Math.round(sheet?.height)}px of ${viewport.height}px)`);
      check(sheet && sheet.y > search.y + search.height, 'sheet does not cover the search box');
      const small = await page.evaluate(() => [...document.querySelectorAll('button, input')]
        .filter((el) => el.offsetParent && el.getBoundingClientRect().height < 30).map((el) => el.textContent || el.id));
      check(small.length === 0, `touch targets at least 30px tall${small.length ? ': ' + small.join(', ') : ''}`);
      const contrast = await page.evaluate(() => {
        const s = getComputedStyle(document.querySelector('#sheet-body h1'));
        return [s.color, getComputedStyle(document.body).getPropertyValue('--panel')];
      });
      check(contrast[0] !== 'rgba(0, 0, 0, 0)', `text colour set (${contrast[0]})`);
      await page.screenshot({ path: `${OUT}/layout-${name}-${colorScheme}.png` });
      check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
      await context.close();
    });
  }
}

// 6. Offline: the service worker keeps the app shell available.
await test('Offline app shell (service worker)', async () => {
  const { context, page } = await open();
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload({ waitUntil: 'load' });
  check(await page.evaluate(() => !!navigator.serviceWorker.controller), 'service worker controls the page');
  await context.setOffline(true);
  await page.reload({ waitUntil: 'load' }).catch(() => {});
  const ok = await page.locator('#q').isVisible().catch(() => false);
  check(ok, 'app shell loads while offline');
  check(await page.evaluate(() => typeof maplibregl === 'object').catch(() => false), 'MapLibre loads offline from cache');
  await page.screenshot({ path: `${OUT}/offline.png` });
  await context.close();
});

await browser.close();

const failed = results.filter((r) => !r.ok);
writeFileSync(`${OUT}/results.json`, JSON.stringify({ base: BASE, passed: results.length - failed.length, failed: failed.length, results }, null, 2));
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
for (const f of failed) console.log(`  FAIL  [${f.test}] ${f.msg}`);
process.exit(failed.length ? 1 : 0);
