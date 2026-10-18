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
// Value estimate, against a mocked Anthropic API (no key or network call to Anthropic in tests).
const MOCK_RESULT = {
  value_low_eur: 255000, value_estimate_eur: 275000, value_high_eur: 295000, confidence: 'medium',
  summary: 'Small 1985 apartment in Noordvest; WOZ and buurt averages point to the high 200s.',
  comparables: [{ description: 'Zamenhofstraat 25 WOZ', price_eur: 301000, kind: 'WOZ value', date: '2025', relevance: 'Same building' }],
  drivers_up: [{ factor: 'Location', effect: 'Walking distance to Leiden Centraal', source: 'CBS distances' }],
  drivers_down: [{ factor: 'Crime', effect: 'High registered crime in the buurt', source: 'Politie/CBS 2025' }],
  risks_to_check: ['VvE reserves'], missing_information: ['Energy label'],
};
function sse(result) {
  const ev = (type, data) => `event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`;
  return ev('message_start', { message: { id: 'msg_test', type: 'message', role: 'assistant', model: 'claude-opus-5-5', content: [],
    stop_reason: null, stop_sequence: null, usage: { input_tokens: 1200, output_tokens: 1 } } })
    + ev('content_block_start', { index: 0, content_block: { type: 'text', text: '' } })
    + ev('content_block_delta', { index: 0, delta: { type: 'text_delta', text: JSON.stringify(result) } })
    + ev('content_block_stop', { index: 0 })
    + ev('message_delta', { delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 400 } })
    + ev('message_stop', {});
}

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
await test('Search: address on a test street opens its dossier', async () => {
  const { context, page, log } = await open();
  await page.fill('#q', 'Zamenhofstraat 25 Leiden');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  const first = await page.locator('#suggestions li[data-id]').first().innerText();
  check(/Zamenhofstraat 25/.test(first), `first suggestion is the address: "${first.replace(/\s+/g, ' ')}"`);
  await page.locator('#suggestions li[data-id]').first().click();
  await waitForTitle(page, /Zamenhofstraat 25/);
  check(await page.locator('#suggestions').isHidden(), 'suggestion list closes after choosing');
  check(await page.inputValue('#q') === 'Zamenhofstraat 25, 2312NX Leiden', `search box shows the choice ("${await page.inputValue('#q')}")`);
  check(await page.locator('#sheet-body .score-row').count() === 7, 'dossier shows 7 theme scores');
  check(/Noordvest/.test(await page.locator('#sheet-body .muted').first().innerText()), 'dossier names buurt Noordvest');
  await page.waitForTimeout(2500);
  const marker = await page.locator('.maplibregl-marker:not(.school-marker)').first().boundingBox();
  const vp = page.viewportSize();
  check(marker && marker.x > 0 && marker.x < vp.width && marker.y > 0 && marker.y < vp.height * 0.7,
    `marker visible above the bottom sheet (${marker ? `${Math.round(marker.x)},${Math.round(marker.y)}` : 'none'})`);
  await page.screenshot({ path: `${OUT}/search-address.png` });
  check(log.apiFailed.length === 0, `no Locatieserver failures${log.apiFailed.length ? ': ' + log.apiFailed.join(' | ') : ''}`);
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Search: other address in Leiden shows its buurt scores', async () => {
  const { context, page, log } = await open();
  await page.fill('#q', 'Breestraat 70 Leiden');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.locator('#suggestions li[data-id]').first().click();
  await waitForTitle(page, /Breestraat 70/);
  const f = await facts(page);
  check(/^2311/.test(f.Postcode || ''), `postcode shown (${f.Postcode})`);
  check(f.Gemeente === 'Leiden', `gemeente Leiden (${f.Gemeente})`);
  check(!!f.Buurt, `buurt shown (${f.Buurt})`);
  check(await page.locator('#sheet-body .score-row').count() === 6, 'buurt scores shown (6 map themes)');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Search: any address in the Netherlands shows its buurt scores', async () => {
  const { context, page, log } = await open();
  let captured = null;
  await context.route('**/v1/messages**', async (route) => {
    captured = JSON.parse(route.request().postData());
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(MOCK_RESULT) });
  });
  await page.fill('#q', 'Prinsengracht 263 Amsterdam');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.press('#q', 'Enter');
  await waitForTitle(page, /Prinsengracht 263/);
  await page.locator('#sheet-body .score-row').first().waitFor({ timeout: 15000 }).catch(() => {});
  check((await facts(page)).Gemeente === 'Amsterdam', 'shows the location facts');
  check(await page.locator('#sheet-body .score-row').count() === 6, `Amsterdam buurt scores shown (${await page.locator('#sheet-body .score-row').count()} rows)`);
  await page.waitForTimeout(1500);
  const n = await page.evaluate(() => new Set(window.prospectMap.querySourceFeatures('buurten').map((f) => f.properties.gemeentenaam)).size);
  check(n >= 1, `map loaded the gemeenten in view (${n})`);
  await page.screenshot({ path: `${OUT}/amsterdam.png` });
  await page.click('[data-valuation-place]');
  await waitForTitle(page, /Estimate a value/);
  check(/neighbourhood-level data only/.test(await sheetText(page)), 'explains the estimate is less certain outside the test streets');
  await page.waitForTimeout(2500);
  const area = await page.inputValue('input[name=oppervlakte]');
  check(Number(area) > 0, `floor area fetched live from BAG (${area || 'empty'})`);
  await page.fill('input[name=apikey]', 'sk-ant-test-key');
  await page.click('#valuation-form button[type=submit]');
  await page.locator('.estimate').waitFor({ timeout: 15000 }).catch(() => {});
  const text = captured ? captured.messages[0].content.map((c) => c.text).join('\n') : '';
  check(/buurt and gemeente level only/.test(text) && /Prinsengracht 263/.test(text), 'request carries the light dossier and the address');
  check(await page.locator('.estimate').count() === 1, 'estimate shown');
  await page.click('[data-back]');
  await waitForTitle(page, /Prinsengracht 263/);
  check(true, 'back returns to the address card');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Search: street result gets buurt from nearest address', async () => {
  const { context, page } = await open();
  await page.fill('#q', 'Langegracht Leiden');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.locator('#suggestions li', { hasText: 'weg' }).first().click();
  await waitForTitle(page, /^Langegracht, Leiden$/);
  await page.locator('#sheet-body dt', { hasText: 'Buurt' }).waitFor({ timeout: 10000 }).catch(() => {});
  const f = await facts(page);
  check(!!f.Buurt, `buurt filled in (${f.Buurt})`);
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
  check(/Alphen aan den Rijn/.test(await page.locator('#sheet-body .muted').first().innerText()), 'Enter opens the Rijndijk dossier (Alphen aan den Rijn)');
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
  await waitForTitle(page, /Zamenhofstraat/).catch(() => {});
  check(/Zamenhofstraat/.test(await page.locator('#sheet-body h1').innerText()), 'test areas still open (they use local data)');
  const pageErrors = log.errors.filter((e) => !/503|Failed to load resource/.test(e));
  check(pageErrors.length === 0, `app does not crash${pageErrors.length ? ': ' + pageErrors.join(' | ') : ''}`);
  await context.close();
});

// 4. Map layers, legend and tapping a neighbourhood.
await test('Map: buurt layer, legend and layer chips', async () => {
  const { context, page, log } = await open();
  await page.waitForFunction(() => window.prospectMap.getLayer('buurten-fill') && window.prospectMap.querySourceFeatures('buurten').length > 0, null, { timeout: 15000 });
  const n = await page.evaluate(() => new Set(window.prospectMap.querySourceFeatures('buurten').map((f) => f.properties.buurtcode)).size);
  check(n >= 100, `buurt polygons loaded (${n})`);
  check(/Prospect score/.test(await page.locator('#legend').innerText()), 'legend shows the Prospect score');
  check(await page.locator('#legend .legend-scale .swatch').count() === 5, 'legend has 5 classes');
  const before = await page.evaluate(() => JSON.stringify(window.prospectMap.getPaintProperty('buurten-fill', 'fill-color')));
  await page.click('.chip[data-layer="veiligheid"]');
  const after = await page.evaluate(() => JSON.stringify(window.prospectMap.getPaintProperty('buurten-fill', 'fill-color')));
  check(before !== after && after.includes('score_veiligheid'), 'Safety chip recolours the map by safety');
  check(await page.getAttribute('.chip[data-layer="veiligheid"]', 'aria-pressed') === 'true', 'Safety chip is marked as pressed');
  check(/Safety/.test(await page.locator('#legend').innerText()), 'legend follows the layer');
  await page.click('.chip[data-layer="woz"]');
  check(/home value/i.test(await page.locator('#legend').innerText()), 'Home value layer uses its own legend');
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/map-home-value.png` });
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Map: tapping a neighbourhood opens its scores', async () => {
  const { context, page, log } = await open();
  await page.waitForFunction(() => window.prospectMap.getLayer('buurten-fill') && window.prospectMap.loaded(), null, { timeout: 15000 });
  await page.evaluate(() => window.prospectMap.jumpTo({ center: [4.490699, 52.163989], zoom: 16 }));
  await page.waitForTimeout(1500);
  const pt = await page.evaluate(() => window.prospectMap.project([4.490699, 52.163989]));
  await page.mouse.click(pt.x, pt.y);
  await waitForTitle(page, /Noordvest/).catch(() => {});
  check(/Noordvest/.test(await page.locator('#sheet-body h1').innerText()), 'tap opens buurt Noordvest');
  check(await page.locator('#sheet-body .score-row').count() === 6, 'buurt card shows 6 theme scores');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

// 5. Test-area dossiers.
for (const [key, title, buurten, councilRe] of [
  ['zamenhofstraat-leiden', /^Zamenhofstraat, Leiden$/, 1, /Energiepark/],
  ['rijndijk-hazerswoude-rijndijk', /^Rijndijk, Hazerswoude-Rijndijk$/, 4, /not in Open Raadsinformatie/],
]) {
  await test(`Dossier: ${key}`, async () => {
    const { context, page, log } = await open();
    await page.click(`.preset[data-area="${key}"]`);
    await waitForTitle(page, title).catch(() => {});
    const t = await page.locator('#sheet-body h1').innerText();
    check(title.test(t), `title "${t}"`);
    check(await page.locator('#sheet-body .score-row').count() === 7, 'shows 7 theme scores');
    const overall = await page.locator('.overall-value').innerText();
    check(/^\d+$/.test(overall), `overall Prospect score shown (${overall})`);
    for (const id of ['s-safety', 's-liveability', 's-schools', 's-buildings', 's-market', 's-rules', 's-development']) {
      await page.click(`#${id} summary`);
      const txt = await page.locator(`#${id} .section-body`).innerText();
      check(txt.length > 80, `section ${id} has content (${txt.length} chars)`);
    }
    check(await page.locator('#s-safety h3').count() === buurten, `safety covers ${buurten} buurt(en)`);
    check(await page.locator('.school-marker').count() > 0, `school markers on the map (${await page.locator('.school-marker').count()})`);
    await page.screenshot({ path: `${OUT}/dossier-${key}.png`, fullPage: false });
    await page.click('[data-council]');
    await waitForTitle(page, /What does the council say/);
    check(councilRe.test(await sheetText(page)), `council view content matches ${councilRe}`);
    await page.screenshot({ path: `${OUT}/council-${key}.png` });
    await page.click('[data-back]');
    await waitForTitle(page, title);
    check(true, 'back returns to the dossier');
    check(await page.locator('#sheet-body .preset').count() === 2, 'both test areas still available');
    check(await page.locator('[data-valuation]').count() === 1, 'value estimate button present');
    check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
    await context.close();
  });
}

await test('Weights change the Prospect score and are remembered', async () => {
  const { context, page, log } = await open();
  await page.click('.preset[data-area="zamenhofstraat-leiden"]');
  await waitForTitle(page, /Zamenhofstraat/);
  const before = Number(await page.locator('.overall-value').innerText());
  await page.click('#open-weights');
  check(await page.locator('#weights').isVisible(), 'weights dialog opens');
  check(await page.locator('#weights input[type=range]').count() === 7, 'a slider per theme');
  await page.locator('input[data-weight="veiligheid"]').fill('0');
  await page.locator('input[data-weight="voorzieningen"]').fill('3');
  await page.click('#weights button[value=close]');
  const after = Number(await page.locator('.overall-value').innerText());
  check(after > before, `ignoring safety and stressing amenities raises Zamenhofstraat's score (${before} -> ${after})`);
  await page.reload({ waitUntil: 'load' });
  await page.click('.preset[data-area="zamenhofstraat-leiden"]');
  await waitForTitle(page, /Zamenhofstraat/);
  check(Number(await page.locator('.overall-value').innerText()) === after, 'weights are remembered after a reload');
  await page.click('#open-weights');
  await page.click('#reset-weights');
  await page.click('#weights button[value=close]');
  check(Number(await page.locator('.overall-value').innerText()) === before, 'reset restores the default score');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Value estimate: request, result and remembered key', async () => {
  const { context, page, log } = await open();
  let captured = null;
  await context.route('**/v1/messages**', async (route) => {
    captured = { url: route.request().url(), headers: route.request().headers(), body: JSON.parse(route.request().postData()) };
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(MOCK_RESULT) });
  });
  await page.fill('#q', 'Zamenhofstraat 25 Leiden');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.locator('#suggestions li[data-id]').first().click();
  await waitForTitle(page, /Zamenhofstraat 25/);
  await page.click('[data-valuation]');
  await waitForTitle(page, /Estimate a value/);
  check(await page.inputValue('select[name=adres]') === 'Zamenhofstraat 25, 2312NX Leiden', 'the searched address is preselected');
  check(/BAG: \d+ m²/.test(await page.locator('#bag-facts').innerText()), `BAG facts shown (${await page.locator('#bag-facts').innerText()})`);
  check(Number(await page.inputValue('input[name=oppervlakte]')) > 0, 'floor area prefilled from BAG');
  check(await page.inputValue('input[name=bouwjaar]') === '1985' || await page.inputValue('input[name=bouwjaar]') === '1986' || await page.inputValue('input[name=bouwjaar]') === '1987',
    `building year prefilled (${await page.inputValue('input[name=bouwjaar]')})`);
  await page.selectOption('select[name=type]', { index: 0 });
  await page.fill('input[name=apikey]', 'sk-ant-test-key');
  await page.check('input[name=remember]');
  await page.click('#valuation-form button[type=submit]');
  await page.locator('.estimate').waitFor({ timeout: 15000 }).catch(() => {});
  check(captured !== null, 'a request went to the Anthropic Messages API');
  if (captured) {
    const b = captured.body;
    check(b.model === 'claude-opus-5-5', `model ${b.model}`);
    check(b.thinking?.type === 'adaptive', 'adaptive thinking');
    check(b.output_config?.effort === 'high' && b.output_config?.format?.type === 'json_schema', 'effort high and a JSON schema for the answer');
    check(b.fallbacks === 'default' && /server-side-fallback-2026-07-01/.test(captured.headers['anthropic-beta'] || ''), 'server-side refusal fallback enabled');
    check(captured.headers['x-api-key'] === 'sk-ant-test-key', 'the typed key is sent as x-api-key');
    check(captured.headers['anthropic-dangerous-direct-browser-access'] === 'true', 'browser access header set by the SDK');
    const text = b.messages[0].content.map((c) => c.text).join('\n');
    check(/Noordvest/.test(text) && /Zamenhofstraat 25, 2312NX Leiden/.test(text), 'request carries the dossier and the property');
    check(b.messages[0].content[0].cache_control?.type === 'ephemeral', 'dossier block is marked for prompt caching');
  }
  const est = await page.locator('.estimate-range strong').innerText().catch(() => '');
  check(/275\.000/.test(est), `estimate shown (${est})`);
  check(/Zamenhofstraat 25 WOZ/.test(await sheetText(page)), 'evidence table shown');
  await page.screenshot({ path: `${OUT}/valuation-result.png` });
  await page.reload({ waitUntil: 'load' });
  await page.click('.preset[data-area="zamenhofstraat-leiden"]');
  await waitForTitle(page, /Zamenhofstraat/);
  await page.click('[data-valuation]');
  await waitForTitle(page, /Estimate a value/);
  check(await page.inputValue('input[name=apikey]') === 'sk-ant-test-key', 'remembered key is filled in after a reload');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

await test('Value estimate: a rejected key gives a clear message', async () => {
  const { context, page } = await open();
  await context.route('**/v1/messages**', (route) => route.fulfill({ status: 401, headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: 'invalid x-api-key' } }) }));
  await page.click('.preset[data-area="rijndijk-hazerswoude-rijndijk"]');
  await waitForTitle(page, /Rijndijk/);
  await page.click('[data-valuation]');
  await waitForTitle(page, /Estimate a value/);
  await page.selectOption('select[name=adres]', { index: 1 });
  await page.fill('input[name=apikey]', 'sk-ant-wrong');
  await page.click('#valuation-form button[type=submit]');
  await page.locator('#valuation-result .notice').waitFor({ timeout: 15000 }).catch(() => {});
  const msg = await page.locator('#valuation-result').innerText();
  check(/rejected the API key/.test(msg), `clear error (${msg.slice(0, 120)})`);
  check(await page.evaluate(() => localStorage.getItem('prospect.anthropicKey')) === null, 'key not stored when "remember" is off');
  await context.close();
});

// 6. Layout on different screens and colour schemes.
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
      const legend = await page.locator('#legend').boundingBox();
      const overlap = legend && sheet && !(legend.y + legend.height <= sheet.y || legend.x >= sheet.x + sheet.width || legend.x + legend.width <= sheet.x);
      check(legend && !overlap, `legend visible and clear of the sheet (${legend ? `${Math.round(legend.x)},${Math.round(legend.y)} ${Math.round(legend.width)}x${Math.round(legend.height)}` : 'none'})`);
      check(search.width <= 520, `search box keeps a readable width (${Math.round(search.width)}px)`);
      const brandBg = await page.evaluate(() => getComputedStyle(document.querySelector('.brand')).backgroundColor);
      check(brandBg !== 'rgba(0, 0, 0, 0)', `title has its own background, readable over the map (${brandBg})`);
      const small = await page.evaluate(() => [...document.querySelectorAll('button, input')]
        .filter((el) => el.offsetParent && el.getBoundingClientRect().height < 30)
        .map((el) => `${el.getAttribute('aria-label') || el.title || el.textContent || el.id} (${Math.round(el.getBoundingClientRect().height)}px)`));
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

// 7. Bottom sheet can be collapsed to give the map the screen.
await test('Bottom sheet collapse', async () => {
  const { context, page, log } = await open();
  await page.click('.preset >> nth=0');
  await waitForTitle(page, /Zamenhofstraat/);
  const open1 = (await page.locator('#sheet').boundingBox()).height;
  await page.click('.handle');
  await page.waitForTimeout(300);
  const closed = (await page.locator('#sheet').boundingBox()).height;
  check(closed < open1 / 2, `collapsing shrinks the sheet (${Math.round(open1)}px -> ${Math.round(closed)}px)`);
  check(await page.locator('#sheet-body h1').isVisible(), 'title stays visible when collapsed');
  check(await page.getAttribute('.handle', 'aria-expanded') === 'false', 'handle reports collapsed state');
  await page.screenshot({ path: `${OUT}/sheet-collapsed.png` });
  await page.click('.handle');
  await page.waitForTimeout(300);
  check(Math.abs((await page.locator('#sheet').boundingBox()).height - open1) < 2, 'expanding restores the sheet');
  await page.click('.handle');
  await page.fill('#q', 'Rijndijk 33 Hazerswoude');
  await page.locator('#suggestions li[data-id]').first().waitFor({ timeout: 10000 });
  await page.press('#q', 'Enter');
  await waitForTitle(page, /Rijndijk 33/);
  check(await page.getAttribute('.handle', 'aria-expanded') === 'true', 'choosing a new place expands the sheet again');
  check(log.errors.length === 0, `no errors${log.errors.length ? ': ' + log.errors.join(' | ') : ''}`);
  await context.close();
});

// 8. Offline: the service worker keeps the app shell available.
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
