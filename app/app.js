// Prospect: map of neighbourhood scores plus a due-diligence card per street.
// Basemap and address search use official PDOK services; scores come from app/data (built by pipeline/).
const LOCATIESERVER = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1';
const BRT_GRIJS = 'https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png';
const REPORT_URL = 'https://github.com/bar371/prospector/blob/main/docs/dd/';

// Themes scored as national percentiles (0-100, higher is better).
const THEMES = {
  veiligheid: 'Safety',
  leefbaarheid: 'Liveability',
  onderwijs: 'Education',
  bereikbaarheid: 'Accessibility',
  autoluw: 'Car-light',
  voorzieningen: 'Amenities',
  rust: 'Quiet',
};
// Education is scored per street (schools near the address), so the buurt map leaves it out.
const MAP_THEMES = Object.keys(THEMES).filter((k) => k !== 'onderwijs');
const DEFAULT_WEIGHTS = Object.fromEntries(Object.keys(THEMES).map((k) => [k, 1]));

// Diverging scale, validated for colour-blind separation: red (worse than most of NL) -> grey -> teal-green (better).
const SCORE_COLORS = ['#b8322a', '#ec9474', '#e6e3dc', '#4fb3b0', '#0f6d78'];
const SCORE_BREAKS = [20, 40, 60, 80];
const SCORE_LABELS = ['Bottom 20% of NL', '20–40%', 'Middle', '60–80%', 'Top 20% of NL'];
// Sequential blue for home value (magnitude, not good/bad).
const VALUE_COLORS = ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'];
const VALUE_BREAKS = [250, 350, 450, 600]; // average WOZ in € thousands

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pick = (obj, keys) => Object.fromEntries(keys.filter((k) => obj[k]).map((k) => [k, obj[k]]));
const eur = (v) => (v == null ? 'n/a' : `€${Math.round(v).toLocaleString('nl-NL')}`);
const num = (v, d = 0) => (v == null ? 'n/a' : Number(v).toLocaleString('en-GB', { maximumFractionDigits: d }));
const scoreClass = (s) => (s == null ? -1 : SCORE_BREAKS.filter((b) => s >= b).length);
const scoreColor = (s) => (s == null ? 'transparent' : SCORE_COLORS[scoreClass(s)]);

function storageGet(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: weights just don't persist */ }
}

const state = {
  layer: 'overall',
  weights: { ...DEFAULT_WEIGHTS, ...storageGet('prospect.weights', {}) },
  buurten: null, // GeoJSON with per-buurt scores
  dossiers: {},
  current: null, // what the sheet shows: {kind, ...}
};

function weightedScore(scores, themes = Object.keys(THEMES)) {
  let total = 0;
  let weight = 0;
  for (const k of themes) {
    const w = state.weights[k] ?? 0;
    if (w > 0 && scores[k] != null) { total += w * scores[k]; weight += w; }
  }
  return weight ? Math.round(total / weight) : null;
}

// ---------------------------------------------------------------- map

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      brt: {
        type: 'raster', tiles: [BRT_GRIJS], tileSize: 256, minzoom: 6, maxzoom: 19,
        attribution: 'Map: <a href="https://www.pdok.nl">PDOK</a> / Kadaster · Data: CBS, Politie, BZK, RIVM, DUO',
      },
    },
    layers: [{ id: 'brt', type: 'raster', source: 'brt' }],
  },
  center: [4.56, 52.15],
  zoom: 10.6,
  attributionControl: { compact: true },
});
// Phones pinch to zoom; the buttons would sit under the bottom sheet.
if (window.matchMedia('(min-width: 760px)').matches) {
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
}
window.prospectMap = map; // for tests and debugging

const marker = new maplibregl.Marker({ color: '#0f172a' });
let schoolMarkers = [];

function stepExpression(prop, colors, breaks) {
  const expr = ['step', ['to-number', ['get', prop]], colors[0]];
  breaks.forEach((b, i) => expr.push(b, colors[i + 1]));
  return ['case', ['==', ['get', prop], null], 'rgba(0,0,0,0)', expr];
}

function layerPaint() {
  if (state.layer === 'woz') return stepExpression('woz', VALUE_COLORS, VALUE_BREAKS);
  return stepExpression(`score_${state.layer}`, SCORE_COLORS, SCORE_BREAKS);
}

function recomputeOverall() {
  if (!state.buurten) return;
  for (const f of state.buurten.features) {
    const p = f.properties;
    p.score_overall = weightedScore(Object.fromEntries(MAP_THEMES.map((k) => [k, p[`score_${k}`]])), MAP_THEMES);
  }
  map.getSource('buurten')?.setData(state.buurten);
}

async function loadBuurten() {
  const res = await fetch('data/buurten-scores.geojson');
  state.buurten = await res.json();
  recomputeOverall();
  const add = () => {
    map.addSource('buurten', { type: 'geojson', data: state.buurten, promoteId: 'buurtcode' });
    map.addLayer({ id: 'buurten-fill', type: 'fill', source: 'buurten',
      paint: { 'fill-color': layerPaint(), 'fill-opacity': ['case', ['boolean', ['feature-state', 'selected'], false], 0.75, 0.55] } });
    map.addLayer({ id: 'buurten-line', type: 'line', source: 'buurten',
      paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.4, 15, 1.5] } });
    map.addLayer({ id: 'buurten-selected', type: 'line', source: 'buurten',
      paint: { 'line-color': '#0f172a', 'line-width': 2.5, 'line-opacity': ['case', ['boolean', ['feature-state', 'selected'], false], 1, 0] } });
    renderLegend();
  };
  if (map.isStyleLoaded()) add(); else map.once('load', add);
}

let selectedBuurten = [];
function selectBuurten(codes) {
  for (const id of selectedBuurten) map.setFeatureState({ source: 'buurten', id }, { selected: false });
  selectedBuurten = codes.filter((c) => state.buurten?.features.some((f) => f.properties.buurtcode === c));
  for (const id of selectedBuurten) map.setFeatureState({ source: 'buurten', id }, { selected: true });
}

function fitBuurten(codes) {
  const feats = state.buurten?.features.filter((f) => codes.includes(f.properties.buurtcode)) || [];
  if (!feats.length) return;
  const b = new maplibregl.LngLatBounds();
  const walk = (c) => (typeof c[0] === 'number' ? b.extend(c) : c.forEach(walk));
  feats.forEach((f) => walk(f.geometry.coordinates));
  map.fitBounds(b, { padding: { top: 140, bottom: window.innerHeight * 0.45, left: 30, right: 30 }, maxZoom: 15.5, duration: 800 });
}

function setLayer(layer) {
  state.layer = layer;
  document.querySelectorAll('.chip[data-layer]').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.layer === layer)));
  if (map.getLayer('buurten-fill')) map.setPaintProperty('buurten-fill', 'fill-color', layerPaint());
  renderLegend();
}

function renderLegend() {
  const el = document.getElementById('legend');
  const isValue = state.layer === 'woz';
  const title = isValue ? 'Average home value (WOZ)' : state.layer === 'overall' ? 'Prospect score' : THEMES[state.layer];
  const colors = isValue ? VALUE_COLORS : SCORE_COLORS;
  const labels = isValue ? ['< €250k', '€250–350k', '€350–450k', '€450–600k', '> €600k'] : SCORE_LABELS;
  const ends = isValue ? ['Lower', 'Higher'] : ['Worse', 'Better'];
  el.innerHTML = `<div class="legend-title">${esc(title)}</div>
    <ol class="legend-scale">${colors.map((c, i) => `<li><span class="swatch" style="background:${c}"></span>${esc(labels[i])}</li>`).join('')}</ol>
    <div class="legend-compact" aria-hidden="true"><span>${ends[0]}</span>${colors.map((c) => `<span class="swatch" style="background:${c}"></span>`).join('')}<span>${ends[1]}</span></div>
    ${isValue ? '' : '<div class="legend-note">Compared with all Dutch neighbourhoods</div>'}`;
}

// ---------------------------------------------------------------- search (PDOK Locatieserver)

const input = document.getElementById('q');
const list = document.getElementById('suggestions');
const sheet = document.getElementById('sheet');
const sheetBody = document.getElementById('sheet-body');
const handle = sheet.querySelector('.handle');
const PRESETS = document.querySelector('.presets').outerHTML;

async function suggest(q, types = 'adres OR weg OR buurt OR wijk OR woonplaats') {
  const url = `${LOCATIESERVER}/suggest?rows=7&fq=${encodeURIComponent(`type:(${types})`)}&q=${encodeURIComponent(q)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Locatieserver ${res.status}`);
  return (await res.json()).response.docs;
}

async function lookup(id) {
  const res = await fetch(`${LOCATIESERVER}/lookup?id=${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Locatieserver ${res.status}`);
  return (await res.json()).response.docs[0];
}

// Streets and towns carry no buurt in the Locatieserver; take it from the nearest address.
async function nearestAddress([lon, lat]) {
  const res = await fetch(`${LOCATIESERVER}/reverse?lat=${lat}&lon=${lon}&type=adres&rows=1`);
  if (!res.ok) return null;
  const hit = (await res.json()).response.docs[0];
  return hit ? lookup(hit.id) : null;
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

function setCollapsed(collapsed) {
  sheet.classList.toggle('collapsed', collapsed);
  handle.setAttribute('aria-expanded', String(!collapsed));
  handle.setAttribute('aria-label', collapsed ? 'Show details' : 'Show more map');
}
handle.addEventListener('click', () => setCollapsed(!sheet.classList.contains('collapsed')));

const AREA_INDEX = [
  { key: 'zamenhofstraat-leiden', straat: 'zamenhofstraat', woonplaats: 'leiden' },
  { key: 'rijndijk-hazerswoude-rijndijk', straat: 'rijndijk', woonplaats: 'hazerswoude-rijndijk' },
];

async function select(id) {
  list.hidden = true;
  try {
    const doc = await lookup(id);
    if (!doc) return;
    input.value = doc.weergavenaam;
    const area = AREA_INDEX.find((a) => (doc.straatnaam || '').toLowerCase() === a.straat
      && (doc.woonplaatsnaam || '').toLowerCase() === a.woonplaats);
    const pt = parsePoint(doc.centroide_ll);
    if (area) {
      await openArea(area.key, { point: doc.type === 'adres' ? pt : null, title: doc.type === 'adres' ? doc.weergavenaam : null });
      return;
    }
    if (pt) {
      marker.setLngLat(pt).addTo(map);
      const zoom = { adres: 17, weg: 16, buurt: 15, wijk: 14, woonplaats: 13 }[doc.type] ?? 15;
      map.flyTo({ center: pt, zoom, padding: { bottom: window.innerHeight * 0.3 } });
    }
    if (pt && !doc.buurtcode) {
      const near = await nearestAddress(pt).catch(() => null);
      if (near) Object.assign(doc, pick(near, ['buurtnaam', 'buurtcode', 'wijknaam', 'wijkcode', 'postcode', 'provincienaam']));
    }
    clearSchools();
    selectBuurten(doc.buurtcode ? [doc.buurtcode] : []);
    renderPlace(doc);
    setCollapsed(false);
  } catch (err) {
    sheetBody.innerHTML = `<h1>Something went wrong</h1><p class="muted">${esc(err.message)}</p>${PRESETS}`;
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

// ---------------------------------------------------------------- cards

function scoreBars(scores, { withOverall = true } = {}) {
  const overall = weightedScore(scores);
  const rows = Object.entries(THEMES).filter(([k]) => scores[k] !== undefined).map(([k, label]) => {
    const s = scores[k];
    return `<li class="score-row" data-theme="${k}">
      <span class="score-label">${esc(label)}</span>
      <span class="score-track"><span class="score-fill" style="width:${s ?? 0}%;background:${scoreColor(s)}"></span></span>
      <span class="score-value">${s ?? '–'}</span></li>`;
  }).join('');
  return `${withOverall ? `<div class="overall"><span class="overall-value" style="--c:${scoreColor(overall)}">${overall ?? '–'}</span>
      <span class="overall-text"><strong>Prospect score</strong><br>Better than ${overall ?? '–'}% of Dutch neighbourhoods, with your weights</span></div>` : ''}
    <ul class="scores">${rows}</ul>`;
}

function renderPlace(doc) {
  const feat = state.buurten?.features.find((f) => f.properties.buurtcode === doc.buurtcode);
  const rows = [
    ['Postcode', doc.postcode],
    ['Buurt', doc.buurtnaam && `${doc.buurtnaam} (${doc.buurtcode})`],
    ['Wijk', doc.wijknaam],
    ['Gemeente', doc.gemeentenaam],
  ].filter(([, v]) => v);
  state.current = { kind: 'place', doc };
  sheetBody.innerHTML = `
    <h1>${esc(doc.weergavenaam)}</h1>
    <dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
    ${feat ? buurtSummary(feat.properties) : `<p class="notice">No Prospect data for this area yet. The first version covers
      the gemeenten Leiden and Alphen aan den Rijn.</p>`}
    <p class="source">Location: PDOK Locatieserver (BAG, CBS buurt boundaries)</p>
    ${PRESETS}`;
}

function buurtSummary(p) {
  const scores = Object.fromEntries(MAP_THEMES.map((k) => [k, p[`score_${k}`] ?? null]));
  return `<h2 class="section-title">Neighbourhood ${esc(p.buurtnaam)}</h2>
    ${scoreBars(scores)}
    <dl class="facts compact">
      <dt>Residents</dt><dd>${num(p.inwoners)}</dd>
      <dt>Average WOZ</dt><dd>${p.woz ? eur(p.woz * 1000) : 'n/a'}</dd>
      <dt>Crimes / 1,000 residents</dt><dd>${num(p.misdrijven_per_1000, 1)} <span class="muted">(2025)</span></dd>
      <dt>Leefbaarometer</dt><dd>${num(p.leefbaarometer, 2)} <span class="muted">(2024)</span></dd>
    </dl>
    <p class="source">Scores are percentiles among all Dutch buurten. Sources: CBS, Politie, Leefbaarometer (BZK), RIVM.</p>`;
}

function renderBuurt(p) {
  state.current = { kind: 'buurt', props: p };
  clearSchools();
  marker.remove();
  selectBuurten([p.buurtcode]);
  sheetBody.innerHTML = `<h1>${esc(p.buurtnaam)}</h1>
    <p class="muted">${esc(p.gemeentenaam)} · ${esc(p.buurtcode)}</p>
    ${buurtSummary(p).replace(/<h2[^>]*>.*?<\/h2>/, '')}
    ${PRESETS}`;
  setCollapsed(false);
}

map.on('click', 'buurten-fill', (e) => {
  const f = e.features?.[0];
  if (f) renderBuurt(f.properties);
});
map.on('mouseenter', 'buurten-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', 'buurten-fill', () => { map.getCanvas().style.cursor = ''; });

// ---------------------------------------------------------------- street dossier

async function loadDossier(key) {
  if (!state.dossiers[key]) {
    const res = await fetch(`data/${key}.json`);
    if (!res.ok) throw new Error(`Could not load data for ${key}`);
    state.dossiers[key] = await res.json();
  }
  return state.dossiers[key];
}

function clearSchools() {
  schoolMarkers.forEach((m) => m.remove());
  schoolMarkers = [];
}

function showSchools(d) {
  clearSchools();
  for (const s of d.scholen.filter((x) => x.sector === 'po' && x.afstand_km <= 1.5)) {
    const el = document.createElement('div');
    el.className = 'school-marker';
    const pct = s.doorstroomtoets?.percentiel;
    el.style.background = scoreColor(pct ?? null) === 'transparent' ? '#94a3b8' : scoreColor(pct);
    el.title = `${s.naam}${pct != null ? ` · test score better than ${pct}% of schools` : ''}`;
    schoolMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([s.lon, s.lat]).addTo(map));
  }
}

function details(id, title, body, open = false) {
  return `<details class="section" id="${id}"${open ? ' open' : ''}><summary>${title}</summary><div class="section-body">${body}</div></details>`;
}

function sparkBars(series, { max } = {}) {
  const vals = series.map(([, v]) => v ?? 0);
  const top = max ?? Math.max(1, ...vals);
  return `<div class="spark" role="img" aria-label="${esc(series.map(([k, v]) => `${k}: ${v}`).join(', '))}">${series.map(([k, v]) =>
    `<span class="spark-bar" style="height:${Math.max(2, (100 * (v ?? 0)) / top)}%" title="${esc(k)}: ${esc(v)}"></span>`).join('')}</div>
    <div class="spark-axis"><span>${esc(series[0]?.[0])}</span><span>${esc(series.at(-1)?.[0])}</span></div>`;
}

function safetySection(d) {
  return d.buurten.map((b) => {
    const c = b.criminaliteit;
    const central = c.percentiel != null && c.percentiel < 25 && (b.kerncijfers.groteSupermarktGemiddeldeAfstandInKm ?? 9) <= 0.5;
    return `<h3>${esc(b.buurtnaam)} <span class="pill" style="--c:${scoreColor(c.percentiel)}">${c.percentiel ?? '–'}</span></h3>
      <p>${num(c.totaal)} registered crimes in ${esc(c.jaar)}: <strong>${num(c.per_1000_inwoners, 1)}</strong> per 1,000 residents
      (Dutch median ${num(c.landelijke_mediaan_per_1000, 1)}).</p>
      ${sparkBars(Object.entries(c.per_jaar))}
      ${c.meest_voorkomend.length ? `<p class="muted">Most common: ${c.meest_voorkomend.map((t) => `${esc(t.soort)} (${t.aantal})`).join(', ')}</p>` : ''}
      ${central ? '<p class="notice">Police register crimes where they happen. Central neighbourhoods with a station, shops or nightlife count every visitor\'s incident against few residents, so the rate per resident overstates the risk for people living there.</p>' : ''}`;
  }).join('') + '<p class="source">Source: Politie via CBS (47018NED), 2021–2025.</p>';
}

function liveabilitySection(d) {
  return d.buurten.map((b) => {
    const l = b.leefbaarometer;
    const g = b.gezondheidsmonitor;
    return `<h3>${esc(b.buurtnaam)} <span class="pill" style="--c:${scoreColor(l.percentiel)}">${l.percentiel ?? '–'}</span></h3>
      <p>Leefbaarometer ${esc(l.jaar)}: ${num(l.score, 2)}.</p>
      <ul class="mini-scores">${Object.entries(l.dimensies).map(([name, v]) =>
        `<li><span>${esc({ 'fysieke omgeving': 'Physical surroundings', 'overlast en onveiligheid': 'Nuisance & insecurity', 'sociale samenhang': 'Social cohesion', voorzieningen: 'Facilities', woningvoorraad: 'Housing stock' }[name] || name)}</span>
         <span class="pill" style="--c:${scoreColor(v.percentiel)}">${v.percentiel ?? '–'}</span></li>`).join('')}</ul>
      <p>${num(g.b_tevr_wo?.waarde, 1)}% of residents are satisfied with their surroundings; ${num(g.b_gel_weg?.waarde, 1)}% are severely bothered by road noise, ${num(g.b_gel_bu?.waarde, 1)}% by neighbours (RIVM ${esc(g.jaar)}).</p>`;
  }).join('') + '<p class="source">Sources: Leefbaarometer 3.0 (BZK), RIVM Gezondheidsmonitor 2024.</p>';
}

function schoolsSection(d) {
  const po = d.scholen.filter((s) => s.sector === 'po' && s.afstand_km <= 2);
  const vo = d.scholen.filter((s) => s.sector === 'vo').slice(0, 6);
  const row = (s) => {
    const t = s.doorstroomtoets;
    const res = t && t.gemiddelde != null ? `<span class="pill" style="--c:${scoreColor(t.percentiel)}">${t.percentiel ?? '–'}</span>` : '<span class="muted">–</span>';
    return `<li><span><strong>${esc(s.naam)}</strong><br><span class="muted">${num(s.afstand_km, 1)} km · ${esc(s.denominatie || '')}${s.onderwijsstructuur ? ` · ${esc(s.onderwijsstructuur)}` : ''}</span></span>${s.sector === 'po' ? res : ''}</li>`;
  };
  return `<p>Primary schools within 2 km. The number is the school's end-of-primary test result (doorstroomtoets 2024),
    as a percentile among schools that took the same test.</p>
    <ul class="list">${po.map(row).join('')}</ul>
    <h3>Secondary schools nearby</h3><ul class="list">${vo.map(row).join('')}</ul>
    <p class="source">Source: DUO open onderwijsdata. Inspection verdicts are not yet included.</p>`;
}

function buildingsSection(d) {
  const b = d.bebouwing;
  const so = d.bodem;
  const old = ['voor 1900', '1900-1944', '1945-1969'].reduce((n, k) => n + (b.bouwjaar_klassen[k] || 0), 0);
  const soft = so.bodemtypen.some((t) => /klei|veen|zavel/i.test(t.naam));
  const periods = Object.entries(b.bouwjaar_klassen);
  let foundation;
  if (old && soft) {
    foundation = `<p class="notice">${old} of ${b.met_bouwjaar} addresses are in buildings from before 1970, on clay or peat. Buildings of that age on soft soil often stand on wooden piles or shallow foundations, which can deteriorate (pile rot, subsidence). Ask for a foundation survey (funderingsonderzoek) for any pre-1970 property here.</p>`;
  } else if (old) {
    foundation = `<p class="notice">${old} addresses are in buildings from before 1970 and the soil here is not mapped. Check the foundation type for older properties.</p>`;
  } else {
    foundation = `<p>All buildings date from ${b.bouwjaar_min} or later; buildings of this age normally stand on concrete piles. Foundation risk is low.</p>`;
  }
  return `<p>${b.met_bouwjaar} of ${b.adressen} addresses have a building year: median <strong>${b.bouwjaar_mediaan}</strong>, range ${b.bouwjaar_min}–${b.bouwjaar_max}. Median floor area ${num(b.oppervlakte_mediaan_m2)} m².</p>
    ${sparkBars(periods)}
    <h3>Soil</h3>
    ${so.bodemtypen.length ? `<ul class="plain">${so.bodemtypen.map((t) => `<li>${esc(t.naam)} <span class="muted">(${t.monsters} of ${so.monsters} points)</span></li>`).join('')}</ul>` : ''}
    ${so.niet_gekarteerd ? `<p class="muted">${so.niet_gekarteerd} of ${so.monsters} sample points are in built-up area, which the Bodemkaart does not cover.</p>` : ''}
    ${foundation}
    <p class="source">Sources: BAG (Kadaster), BRO Bodemkaart.</p>`;
}

function marketSection(d) {
  const a = d.aanbod;
  const g = d.gemeente_vergelijking;
  const trend = g.gemiddelde_verkoopprijs.slice(-8);
  let listings = '';
  if (a?.listings?.length) {
    listings = `<table class="table"><thead><tr><th>Address</th><th>Asking</th><th>Sold</th></tr></thead><tbody>
      ${a.listings.map((i) => `<tr><td><a href="${esc(i.source)}" target="_blank" rel="noopener">${esc(i.address)}</a><br><span class="muted">${esc(i.type)}</span></td>
        <td>${eur(i.asking_price_eur)}</td><td>${esc(i.sold_on || i.status)}</td></tr>`).join('')}</tbody></table>
      <p class="muted">Asking prices (k.k.) found online, not final sale prices.</p>`;
  } else if (a) {
    listings = `<p class="muted">${esc(a.price_note)}</p><ul class="plain">${(a.other_values || []).map((v) =>
      `<li><a href="${esc(v.source)}" target="_blank" rel="noopener">${esc(v.address)}</a>: ${esc(v.kind)} ${v.value_eur ? eur(v.value_eur) : (v.value_eur_range || []).map(eur).join(' – ')}</li>`).join('')}</ul>`;
  }
  return `${listings}
    <h3>Average sale price in ${esc(d.gemeente)}</h3>
    ${sparkBars(trend)}
    <p>${esc(trend.at(-1)?.[0])}: <strong>${eur(trend.at(-1)?.[1])}</strong> (Netherlands ${eur(g.gemiddelde_verkoopprijs_nl.at(-1)?.[1])})</p>
    <p class="source">Source: CBS/Kadaster (83625NED); listings via online search.</p>`;
}

function rulesSection(d) {
  return d.regelingen.flatMap((doc) => doc.regelingen).map((r) => `
    <div class="rule"><h3>${esc(r.naam)}${r.status ? ` <span class="status">${esc(r.status)}</span>` : ''}</h3>
    <p>${esc(r.samenvatting || (r.bedrag_eur ? `${eur(r.bedrag_eur)} (${r.jaar})` : ''))}</p>
    <p class="source">${(r.bronnen || []).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.titel)}</a>${s.officieel ? '' : ' (unofficial)'}`).join(' · ')}</p></div>`).join('');
}

function developmentSection(d) {
  const p = d.bekendmakingen;
  const labels = { omgevingsvergunning: 'permits', verkeersbesluit: 'traffic decisions', bestemmingsplan: 'zoning plans', evenement: 'events', overig: 'other' };
  return `<p>${p.aantal} official publications mention the street: ${Object.entries(p.per_categorie).map(([k, v]) => `${v} ${labels[k] || k}`).join(', ')}.</p>
    <ul class="plain dated">${p.recent.slice(0, 12).map((r) => `<li><span class="muted">${esc(r.available)}</span>
      <a href="${esc(r.preferredUrl)}" target="_blank" rel="noopener">${esc(r.title)}</a></li>`).join('')}</ul>
    <p class="source">Source: Gemeenteblad and Staatscourant (officielebekendmakingen.nl).</p>`;
}

function councilView(d) {
  const items = d.raad || [];
  const body = items.length ? `<ul class="plain council">${items.slice(0, 20).map((h) => {
    const quote = (h.highlight?.[0] || '').replace(/<\/?em>/g, (t) => (t === '<em>' ? '\u0001' : '\u0002'));
    return `<li><span class="muted">${esc((h.date || '').slice(0, 10))}</span>
      <a href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.name)}</a>
      ${quote ? `<blockquote>${esc(quote).replace(/\u0001/g, '<mark>').replace(/\u0002/g, '</mark>').slice(0, 400)}</blockquote>` : ''}</li>`;
  }).join('')}</ul>` : `<p class="notice">Council documents for ${esc(d.gemeente)} are not in Open Raadsinformatie yet.
     Its own council system is the next source to add, together with meeting transcripts.</p>`;
  return `<button type="button" class="link-button back" data-back>← Back to ${esc(d.straat)}</button>
    <h1>What does the council say?</h1>
    <p class="muted">Gemeenteraad documents that mention ${esc(d.straat)} or its neighbourhood, newest first. Transcripts of council meetings come in a later version.</p>
    ${body}
    <p class="source">Source: Open Raadsinformatie (Open State Foundation).</p>`;
}

async function openArea(key, { point = null, title = null } = {}) {
  const d = await loadDossier(key);
  state.current = { kind: 'area', key, title };
  const codes = d.buurten.map((b) => b.buurtcode);
  selectBuurten(codes);
  marker.setLngLat(point || [d.centrum.lon, d.centrum.lat]).addTo(map);
  if (point) map.flyTo({ center: point, zoom: 16, padding: { bottom: window.innerHeight * 0.4 } });
  else fitBuurten(codes);
  showSchools(d);
  renderArea(d, title);
  setCollapsed(false);
  input.value = title || `${d.straat}, ${d.woonplaats}`;
}

function renderArea(d, title) {
  sheetBody.innerHTML = `
    <h1>${esc(title || `${d.straat}, ${d.woonplaats}`)}</h1>
    <p class="muted">${esc(d.gemeente)} · ${d.adressen} addresses · ${d.buurten.map((b) => esc(b.buurtnaam)).join(', ')}</p>
    ${scoreBars(d.scores)}
    <button type="button" class="council-button" data-council>What does the council say?</button>
    ${details('s-safety', 'Safety', safetySection(d))}
    ${details('s-liveability', 'Liveability & noise', liveabilitySection(d))}
    ${details('s-schools', 'Schools', schoolsSection(d))}
    ${details('s-buildings', 'Buildings, soil & foundations', buildingsSection(d))}
    ${details('s-market', 'Prices & market', marketSection(d))}
    ${details('s-rules', 'Buying rules & starter loans', rulesSection(d))}
    ${details('s-development', 'Permits & development', developmentSection(d))}
    <p class="source">Full report with all sources: <a href="${REPORT_URL}${esc(d.key)}.md" target="_blank" rel="noopener">due-diligence report</a></p>
    ${PRESETS}`;
}

sheetBody.addEventListener('click', (e) => {
  const d = state.current?.kind === 'area' ? state.dossiers[state.current.key] : null;
  if (e.target.closest('[data-council]') && d) {
    sheetBody.innerHTML = councilView(d);
    sheet.scrollTop = 0;
  } else if (e.target.closest('[data-back]') && d) {
    renderArea(d, state.current.title);
  }
});

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.preset');
  if (!btn) return;
  try {
    await openArea(btn.dataset.area);
  } catch (err) {
    sheetBody.innerHTML = `<h1>Something went wrong</h1><p class="muted">${esc(err.message)}</p>${PRESETS}`;
  }
});

// ---------------------------------------------------------------- layers and weights

document.querySelectorAll('.chip[data-layer]').forEach((c) => c.addEventListener('click', () => setLayer(c.dataset.layer)));

const dialog = document.getElementById('weights');
function renderSliders() {
  document.getElementById('weight-sliders').innerHTML = Object.entries(THEMES).map(([k, label]) => `
    <label class="slider"><span>${esc(label)}${k === 'onderwijs' ? ' <span class="muted">(streets only)</span>' : ''}</span>
      <input type="range" min="0" max="3" step="1" value="${state.weights[k]}" data-weight="${k}" aria-label="${esc(label)} weight">
      <output>${['Off', 'Low', 'Normal', 'High'][state.weights[k]]}</output></label>`).join('');
}
document.getElementById('open-weights').addEventListener('click', () => { renderSliders(); dialog.showModal(); });
document.getElementById('weight-sliders').addEventListener('input', (e) => {
  const k = e.target.dataset.weight;
  if (!k) return;
  state.weights[k] = Number(e.target.value);
  e.target.nextElementSibling.textContent = ['Off', 'Low', 'Normal', 'High'][state.weights[k]];
  applyWeights();
});
document.getElementById('reset-weights').addEventListener('click', () => {
  state.weights = { ...DEFAULT_WEIGHTS };
  renderSliders();
  applyWeights();
});

function applyWeights() {
  storageSet('prospect.weights', state.weights);
  recomputeOverall();
  const c = state.current;
  if (c?.kind === 'area' && sheetBody.querySelector('.overall')) renderArea(state.dossiers[c.key], c.title);
  else if (c?.kind === 'buurt') renderBuurt(state.buurten.features.find((f) => f.properties.buurtcode === c.props.buurtcode).properties);
  else if (c?.kind === 'place') renderPlace(c.doc);
}

loadBuurten().catch(() => {});
renderLegend();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
