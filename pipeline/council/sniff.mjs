// Open the public council pages in a real browser and record the data and media requests they make,
// to find meeting lists, agenda timestamps, video streams and any transcripts.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.argv[2] || 'out/sniff';
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();

async function sniff(name, url, actions = async () => {}) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const reqs = [];
  page.on('response', async (r) => {
    const u = r.url();
    const type = r.request().resourceType();
    if (!['xhr', 'fetch', 'media', 'other', 'document'].includes(type) && !/m3u8|\.vtt|\.mp4|json/.test(u)) return;
    let body = '';
    const ct = r.headers()['content-type'] || '';
    if (/json|text|vtt|mpegurl|xml/.test(ct) || /m3u8|\.vtt/.test(u)) {
      body = await r.text().catch(() => '');
    }
    reqs.push({ url: u, method: r.request().method(), post: (r.request().postData() || '').slice(0, 2000),
      status: r.status(), type, ct, size: body.length, body: body.slice(0, 6000) });
  });
  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    await actions(page);
    await page.waitForTimeout(4000);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
    reqs.push({ final_url: page.url() });
    writeFileSync(`${OUT}/${name}.html`, await page.content());
  } catch (e) {
    reqs.push({ error: String(e) });
  }
  writeFileSync(`${OUT}/${name}.json`, JSON.stringify(reqs, null, 1));
  console.log(`${name}: ${reqs.length} responses`);
  for (const r of reqs) if (r.url) console.log(`  ${r.status} ${r.type} ${r.ct.slice(0, 30)} ${r.url.slice(0, 180)}`);
  await page.close();
}

// Leiden: Qualigraf public app. Search, then open the latest council meeting that has video.
await sniff('leiden_search', 'https://leiden.qualigraf.nl/app/public/', async (page) => {
  const box = page.locator('input[type=search], input[type=text]').first();
  if (await box.count()) { await box.fill('Energiepark'); await box.press('Enter'); await page.waitForTimeout(5000); }
});
await sniff('leiden_meeting', 'https://leiden.qualigraf.nl/app/public/', async (page) => {
  await page.getByText('Laatste vergaderingen').first().click().catch(() => {});
  await page.waitForTimeout(1500);
  await page.getByText('Gemeenteraad', { exact: true }).first().click().catch(() => {});
  await page.waitForTimeout(6000);
  const video = page.getByText(/video/i).first();
  if (await video.count()) await video.click().catch(() => {});
  await page.waitForTimeout(8000);
});

// Alphen aan den Rijn: iBabs agenda page; the Company Webcast player sits under "Uitzending".
await sniff('alphen_raad', 'https://alphenaandenrijn.bestuurlijkeinformatie.nl/Agenda/Index/25a330d4-a480-483d-8395-41c407f6752f', async (page) => {
  await page.getByText('Uitzending').first().scrollIntoViewIfNeeded().catch(() => {});
  await page.waitForTimeout(12000);
  const frames = page.frames().map((f) => f.url());
  writeFileSync(`${OUT}/alphen_frames.json`, JSON.stringify(frames, null, 1));
  const play = page.locator('button[aria-label*="lay" i], button[title*="lay" i], .vjs-big-play-button').first();
  if (await play.count()) await play.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(10000);
});
await browser.close();
