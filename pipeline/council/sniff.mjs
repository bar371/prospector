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
    reqs.push({ url: u, status: r.status(), type, ct, size: body.length, body: body.slice(0, 4000) });
  });
  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    await actions(page);
    await page.waitForTimeout(4000);
    await page.screenshot({ path: `${OUT}/${name}.png` });
    writeFileSync(`${OUT}/${name}.html`, await page.content());
  } catch (e) {
    reqs.push({ error: String(e) });
  }
  writeFileSync(`${OUT}/${name}.json`, JSON.stringify(reqs, null, 1));
  console.log(`${name}: ${reqs.length} responses`);
  for (const r of reqs) if (r.url) console.log(`  ${r.status} ${r.type} ${r.ct.slice(0, 30)} ${r.url.slice(0, 180)}`);
  await page.close();
}

// Leiden: Qualigraf public app
await sniff('leiden_home', 'https://leiden.qualigraf.nl/app/public/');
await sniff('leiden_search', 'https://leiden.qualigraf.nl/app/public/', async (page) => {
  const box = page.locator('input[type=search], input[type=text]').first();
  if (await box.count()) { await box.fill('Energiepark'); await box.press('Enter'); await page.waitForTimeout(5000); }
});

// Alphen aan den Rijn: iBabs agenda page with a Company Webcast player
await sniff('alphen_raad', 'https://alphenaandenrijn.bestuurlijkeinformatie.nl/Agenda/Index/25a330d4-a480-483d-8395-41c407f6752f', async (page) => {
  const play = page.locator('button:has-text("Afspelen"), button[aria-label*="lay"], .cwc-play, video').first();
  if (await play.count()) await play.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(8000);
});
await sniff('alphen_search', 'https://alphenaandenrijn.bestuurlijkeinformatie.nl/Search?searchString=Rijndijk', async (page) => {
  await page.waitForTimeout(5000);
});
await browser.close();
