// Prospect — app shell. Basemap and address search use official PDOK services.
const LOCATIESERVER = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1';
const BRT_GRIJS = 'https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png';

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      brt: {
        type: 'raster',
        tiles: [BRT_GRIJS],
        tileSize: 256,
        minzoom: 6,
        maxzoom: 19,
        attribution: 'Kaart: <a href="https://www.pdok.nl">PDOK</a> / Kadaster',
      },
    },
    layers: [{ id: 'brt', type: 'raster', source: 'brt' }],
  },
  center: [4.55, 52.16],
  zoom: 11.5,
  attributionControl: { compact: true },
});
// Phones pinch to zoom; the buttons would sit under the bottom sheet.
if (window.matchMedia('(min-width: 760px)').matches) {
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
}

const marker = new maplibregl.Marker({ color: '#0f766e' });
const input = document.getElementById('q');
const list = document.getElementById('suggestions');
const sheetBody = document.getElementById('sheet-body');

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function suggest(q) {
  const url = `${LOCATIESERVER}/suggest?rows=7&fq=type:(adres OR weg OR buurt OR wijk OR woonplaats)&q=${encodeURIComponent(q)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Locatieserver ${res.status}`);
  return (await res.json()).response.docs;
}

async function lookup(id) {
  const res = await fetch(`${LOCATIESERVER}/lookup?id=${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Locatieserver ${res.status}`);
  return (await res.json()).response.docs[0];
}

function parsePoint(wkt) {
  const m = /POINT\(([-\d.]+) ([-\d.]+)\)/.exec(wkt || '');
  return m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
}

function renderSuggestions(docs) {
  list.innerHTML = docs
    .map((d) => `<li role="option" data-id="${esc(d.id)}">${esc(d.weergavenaam)}<span class="type">${esc(d.type)}</span></li>`)
    .join('');
  list.hidden = docs.length === 0;
}

function renderPlace(doc) {
  const rows = [
    ['Type', doc.type],
    ['Straat', doc.straatnaam],
    ['Postcode', doc.postcode],
    ['Buurt', doc.buurtnaam && `${doc.buurtnaam} (${doc.buurtcode})`],
    ['Wijk', doc.wijknaam && `${doc.wijknaam} (${doc.wijkcode})`],
    ['Woonplaats', doc.woonplaatsnaam],
    ['Gemeente', doc.gemeentenaam],
    ['Provincie', doc.provincienaam],
  ].filter(([, v]) => v);
  sheetBody.innerHTML = `
    <h1>${esc(doc.weergavenaam)}</h1>
    <dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
    <p class="soon">Onderwijs, veiligheid, leefbaarheid, bereikbaarheid &amp; autoluw, bodem, klimaat, ontwikkeling,
    startersregelingen en "Wat zegt de raad?" volgen in de volgende fases.</p>
    <p class="muted" style="font-size:12px">Bron: PDOK Locatieserver (BAG / CBS wijk- en buurtindeling)</p>`;
}

async function select(id) {
  list.hidden = true;
  try {
    const doc = await lookup(id);
    if (!doc) return;
    input.value = doc.weergavenaam;
    const pt = parsePoint(doc.centroide_ll);
    if (pt) {
      marker.setLngLat(pt).addTo(map);
      const zoom = { adres: 17, weg: 16, buurt: 15, wijk: 14, woonplaats: 13 }[doc.type] ?? 15;
      map.flyTo({ center: pt, zoom, padding: { bottom: window.innerHeight * 0.3 } });
    }
    renderPlace(doc);
  } catch (err) {
    sheetBody.innerHTML = `<h1>Niet gelukt</h1><p class="muted">${esc(err.message)}</p>`;
  }
}

let timer;
input.addEventListener('input', () => {
  clearTimeout(timer);
  const q = input.value.trim();
  if (q.length < 3) { list.hidden = true; return; }
  timer = setTimeout(() => suggest(q).then(renderSuggestions).catch(() => { list.hidden = true; }), 200);
});
list.addEventListener('click', (e) => {
  const li = e.target.closest('li[data-id]');
  if (li) select(li.dataset.id);
});
document.querySelector('.search').addEventListener('submit', async (e) => {
  e.preventDefault();
  const first = list.querySelector('li[data-id]');
  if (first) return select(first.dataset.id);
  const docs = await suggest(input.value.trim()).catch(() => []);
  if (docs[0]) select(docs[0].id);
});
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.preset');
  if (!btn) return;
  const docs = await suggest(btn.dataset.q).catch(() => []);
  if (docs[0]) select(docs[0].id);
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
