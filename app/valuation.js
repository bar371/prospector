// Prospect value estimate: sends the street dossier plus the property's details to Claude and shows a
// reasoned market-value range. Runs in the browser with the user's own API key, which is kept only on
// this device. Loaded on demand by app.js; the official Anthropic SDK is vendored in vendor/.
(() => {
  const SDK_SRC = 'vendor/anthropic-sdk-0.131.0.js';
  const MODEL = 'claude-opus-5-5';
  const KEY_STORAGE = 'prospect.anthropicKey';

  const SYSTEM = `You are a residential property valuation analyst for the Netherlands. You estimate the current
market value (marktwaarde, kosten koper) of one home, using only the data you are given: a due-diligence dossier
for the street (official open data: CBS, Politie, Leefbaarometer, RIVM, DUO, BAG, BRO Bodemkaart, Kadaster/CBS
price statistics, Gemeenteblad publications, council documents) and the details the user entered.

How to work:
- Anchor on comparable evidence first: asking prices and sale dates of homes on the same street, the
  gemeente's average sale price trend, the buurt's average WOZ value. Asking prices are not sale prices;
  WOZ values lag the market (reference date 1 January of the previous year) and usually sit below market
  value; bring older evidence to today with the CBS price trend in the dossier.
- Then adjust for this home: floor area, type, building year, energy label, condition, plot, and the risks
  in the dossier (foundation risk on soft soil for pre-1970 buildings, noise, crime, liveability) and buyer
  rules (opkoopbescherming and zelfbewoningsplicht narrow the buyer pool).
- When evidence is thin or conflicting, widen the range and lower the confidence rather than guessing.
- Never invent facts, addresses or prices. Every comparable and driver must come from the dossier or the
  user's input, and its "source" names where it came from.
- Write all text in English, concise and specific. Amounts in whole euros.
This is an indicative estimate, not a formal taxatie.`;

  const SCHEMA = {
    type: 'object',
    additionalProperties: false,
    required: ['value_low_eur', 'value_estimate_eur', 'value_high_eur', 'confidence', 'summary', 'comparables',
      'drivers_up', 'drivers_down', 'risks_to_check', 'missing_information'],
    properties: {
      value_low_eur: { type: 'integer' },
      value_estimate_eur: { type: 'integer' },
      value_high_eur: { type: 'integer' },
      confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
      summary: { type: 'string' },
      comparables: {
        type: 'array',
        items: {
          type: 'object', additionalProperties: false, required: ['description', 'price_eur', 'kind', 'date', 'relevance'],
          properties: {
            description: { type: 'string' },
            price_eur: { type: 'integer' },
            kind: { type: 'string', enum: ['asking price', 'WOZ value', 'third-party estimate', 'area average'] },
            date: { type: 'string' },
            relevance: { type: 'string' },
          },
        },
      },
      drivers_up: { type: 'array', items: { $ref: '#/$defs/driver' } },
      drivers_down: { type: 'array', items: { $ref: '#/$defs/driver' } },
      risks_to_check: { type: 'array', items: { type: 'string' } },
      missing_information: { type: 'array', items: { type: 'string' } },
    },
    $defs: {
      driver: {
        type: 'object', additionalProperties: false, required: ['factor', 'effect', 'source'],
        properties: { factor: { type: 'string' }, effect: { type: 'string' }, source: { type: 'string' } },
      },
    },
  };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const eur = (v) => (v == null ? 'n/a' : `€${Math.round(v).toLocaleString('nl-NL')}`);

  function storedKey() {
    try { return localStorage.getItem(KEY_STORAGE) || ''; } catch { return ''; }
  }
  function storeKey(key, remember) {
    try { if (remember) localStorage.setItem(KEY_STORAGE, key); else localStorage.removeItem(KEY_STORAGE); } catch { /* private mode */ }
  }

  let sdkPromise;
  function loadSdk() {
    sdkPromise ??= new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = SDK_SRC;
      s.onload = () => resolve(window.AnthropicSDK.Anthropic);
      s.onerror = () => reject(new Error('Could not load the Anthropic SDK'));
      document.head.append(s);
    });
    return sdkPromise;
  }

  // The parts of the dossier that matter for value, without the bulky raw series.
  function compactDossier(d) {
    return {
      street: `${d.straat}, ${d.woonplaats}`, gemeente: d.gemeente, addresses_on_street: d.adressen,
      scores_national_percentile: d.scores,
      buurten: d.buurten.map((b) => ({
        name: b.buurtnaam, code: b.buurtcode, addresses_on_street: b.adressen_op_straat, residents: b.inwoners,
        key_figures: b.kerncijfers,
        crime: { year: b.criminaliteit.jaar, per_1000_residents: b.criminaliteit.per_1000_inwoners,
          national_median_per_1000: b.criminaliteit.landelijke_mediaan_per_1000, percentile: b.criminaliteit.percentiel,
          per_year: b.criminaliteit.per_jaar, most_common: b.criminaliteit.meest_voorkomend },
        leefbaarometer: { year: b.leefbaarometer.jaar, score: b.leefbaarometer.score, percentile: b.leefbaarometer.percentiel,
          dimensions: b.leefbaarometer.dimensies, trend: b.leefbaarometer.trend },
        health_monitor: b.gezondheidsmonitor,
      })),
      gemeente_comparison: d.gemeente_vergelijking,
      schools_nearby: d.scholen.slice(0, 12).map((s) => ({ name: s.naam, sector: s.sector, distance_km: s.afstand_km,
        test_result_percentile: s.doorstroomtoets?.percentiel ?? null })),
      buildings_on_street: d.bebouwing,
      soil: d.bodem,
      listings_and_values: d.aanbod,
      buyer_rules: d.regelingen,
      recent_publications: d.bekendmakingen.recent.slice(0, 12).map((r) => `${r.available}: ${r.title}`),
      council_documents: (d.raad || []).slice(0, 8).map((h) => `${(h.date || '').slice(0, 10)}: ${h.name}`),
    };
  }

  function buildRequest(dossier, property) {
    return {
      model: MODEL,
      max_tokens: 16000,
      betas: ['server-side-fallback-2026-07-01'],
      fallbacks: 'default',
      thinking: { type: 'adaptive' },
      output_config: { effort: 'high', format: { type: 'json_schema', schema: SCHEMA } },
      system: [{ type: 'text', text: SYSTEM }],
      messages: [{
        role: 'user',
        content: [
          // The dossier is the same for every home on the street, so it is cached across estimates.
          { type: 'text', text: `Street dossier (JSON):\n${JSON.stringify(compactDossier(dossier))}`, cache_control: { type: 'ephemeral' } },
          { type: 'text', text: `Today is ${new Date().toISOString().slice(0, 10)}. Estimate the market value of this home:\n${JSON.stringify(property, null, 1)}` },
        ],
      }],
    };
  }

  async function estimate(apiKey, dossier, property) {
    const Anthropic = await loadSdk();
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true, maxRetries: 2 });
    const stream = client.beta.messages.stream(buildRequest(dossier, property));
    const message = await stream.finalMessage();
    if (message.stop_reason === 'refusal') throw new Error('The model declined this request.');
    if (message.stop_reason === 'max_tokens') throw new Error('The answer was cut off; please try again.');
    const text = message.content.filter((b) => b.type === 'text').map((b) => b.text).join('');
    return { result: JSON.parse(text), model: message.model, usage: message.usage };
  }

  function errorText(err) {
    const A = window.AnthropicSDK?.Anthropic;
    if (A && err instanceof A.AuthenticationError) return 'Anthropic rejected the API key. Check it and try again.';
    if (A && err instanceof A.PermissionDeniedError) return 'This API key is not allowed to use this model.';
    if (A && err instanceof A.RateLimitError) return 'Rate limit reached. Wait a minute and try again.';
    if (A && err instanceof A.APIConnectionError) return 'Could not reach Anthropic. Check your connection.';
    if (A && err instanceof A.APIError) return `Anthropic returned an error (${err.status ?? 'unknown'}): ${err.message}`;
    return err.message || String(err);
  }

  const TYPES = ['Apartment', 'Terraced house (tussenwoning)', 'Corner house (hoekwoning)', 'Semi-detached (2-onder-1-kap)',
    'Detached house (vrijstaand)', 'Houseboat (woonark)', 'Other'];
  const LABELS = ['Unknown', 'A++++', 'A+++', 'A++', 'A+', 'A', 'B', 'C', 'D', 'E', 'F', 'G'];
  const CONDITIONS = ['Unknown', 'Needs renovation (kluswoning)', 'Average', 'Good', 'Excellent / recently renovated'];

  function formHtml(d, addresses, preset) {
    const key = storedKey();
    const homes = addresses.filter((a) => (a.gebruiksdoel || '').includes('woon'));
    const presetAddr = homes.find((a) => preset && a.adres === preset) || null;
    return `<button type="button" class="link-button back" data-back>← Back to ${esc(d.straat)}</button>
      <h1>Estimate a value</h1>
      <p class="muted">Claude estimates the market value from this street's dossier and the details below. It is an
        indication, not a formal valuation (taxatie).</p>
      <form id="valuation-form" class="valuation-form">
        <label>Address
          <select name="adres" required>
            <option value="">Choose an address on ${esc(d.straat)}</option>
            ${homes.map((a) => `<option value="${esc(a.adres)}"${presetAddr === a ? ' selected' : ''}>${esc(a.adres)}</option>`).join('')}
          </select></label>
        <p class="muted" id="bag-facts"></p>
        <div class="grid2">
          <label>Type<select name="type">${TYPES.map((t) => `<option>${esc(t)}</option>`).join('')}</select></label>
          <label>Floor area (m²)<input name="oppervlakte" type="number" min="10" max="2000" inputmode="numeric"></label>
          <label>Building year<input name="bouwjaar" type="number" min="1200" max="2030" inputmode="numeric"></label>
          <label>Plot size (m², optional)<input name="perceel" type="number" min="0" max="100000" inputmode="numeric"></label>
          <label>Energy label<select name="energielabel">${LABELS.map((t) => `<option>${esc(t)}</option>`).join('')}</select></label>
          <label>Condition<select name="staat">${CONDITIONS.map((t) => `<option>${esc(t)}</option>`).join('')}</select></label>
          <label>Asking price (optional)<input name="vraagprijs" type="number" min="0" step="1000" inputmode="numeric"></label>
          <label>Ground lease<select name="erfpacht"><option>Unknown</option><option>No (eigen grond)</option><option>Yes (erfpacht)</option></select></label>
        </div>
        <label>Anything else (garden, parking, extensions, listing text)<textarea name="notes" rows="3"></textarea></label>
        <fieldset class="key-box">
          <legend>Your Anthropic API key</legend>
          <input name="apikey" type="password" autocomplete="off" placeholder="sk-ant-..." value="${esc(key)}" required>
          <label class="check"><input type="checkbox" name="remember"${key ? ' checked' : ''}> Remember on this device</label>
          <p class="source">The key goes straight from this browser to Anthropic and is never stored anywhere else.
            One estimate uses Claude Opus 5.5 and costs about €0.10–0.20 in API usage.</p>
        </fieldset>
        <button type="submit" class="primary wide">Estimate value</button>
      </form>
      <div id="valuation-result" aria-live="polite"></div>`;
  }

  function resultHtml({ result: r, model }) {
    const list = (items) => (items.length ? `<ul class="plain">${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted">None.</p>');
    const drivers = (items) => (items.length ? `<ul class="plain">${items.map((x) =>
      `<li><strong>${esc(x.factor)}</strong>: ${esc(x.effect)} <span class="muted">(${esc(x.source)})</span></li>`).join('')}</ul>` : '<p class="muted">None.</p>');
    return `<div class="estimate">
        <div class="estimate-range"><span>${eur(r.value_low_eur)}</span><strong>${eur(r.value_estimate_eur)}</strong><span>${eur(r.value_high_eur)}</span></div>
        <div class="estimate-labels"><span>low</span><span>estimate</span><span>high</span></div>
        <p><span class="status">confidence: ${esc(r.confidence)}</span></p>
        <p>${esc(r.summary)}</p>
      </div>
      <h3>Evidence used</h3>
      <table class="table"><thead><tr><th>Evidence</th><th>Price</th><th>Why it matters</th></tr></thead><tbody>
        ${r.comparables.map((c) => `<tr><td>${esc(c.description)}<br><span class="muted">${esc(c.kind)} · ${esc(c.date)}</span></td>
          <td>${eur(c.price_eur)}</td><td>${esc(c.relevance)}</td></tr>`).join('')}</tbody></table>
      <h3>Pushing the value up</h3>${drivers(r.drivers_up)}
      <h3>Pushing the value down</h3>${drivers(r.drivers_down)}
      <h3>Check before you buy</h3>${list(r.risks_to_check)}
      <h3>Would sharpen the estimate</h3>${list(r.missing_information)}
      <p class="source">Estimated by ${esc(model)} on ${new Date().toISOString().slice(0, 10)} from official open data and your input.
        Indicative only, not a taxatie.</p>`;
  }

  async function open(container, dossier, { presetAddress = null } = {}) {
    let addresses = [];
    try { addresses = await (await fetch(`data/${dossier.key}-addresses.json`)).json(); } catch { /* form still works without BAG prefill */ }
    container.innerHTML = formHtml(dossier, addresses, presetAddress);
    const form = container.querySelector('#valuation-form');
    const out = container.querySelector('#valuation-result');
    const facts = container.querySelector('#bag-facts');
    const fill = () => {
      const a = addresses.find((x) => x.adres === form.adres.value);
      if (!a) { facts.textContent = ''; return; }
      if (a.oppervlakte) form.oppervlakte.value = a.oppervlakte;
      if (a.bouwjaar) form.bouwjaar.value = a.bouwjaar;
      facts.textContent = `BAG: ${a.oppervlakte ?? '?'} m², built ${a.bouwjaar ?? '?'}, use: ${a.gebruiksdoel ?? '?'}`;
    };
    form.adres.addEventListener('change', fill);
    fill();
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(form));
      const key = (f.apikey || '').trim();
      storeKey(key, !!f.remember);
      const bag = addresses.find((x) => x.adres === f.adres) || {};
      const property = {
        address: f.adres, type: f.type, floor_area_m2: Number(f.oppervlakte) || bag.oppervlakte || null,
        building_year: Number(f.bouwjaar) || bag.bouwjaar || null, plot_m2: Number(f.perceel) || null,
        energy_label: f.energielabel, condition: f.staat, ground_lease: f.erfpacht,
        asking_price_eur: Number(f.vraagprijs) || null, notes: f.notes || null,
        bag: { floor_area_m2: bag.oppervlakte ?? null, building_year: bag.bouwjaar ?? null, use: bag.gebruiksdoel ?? null },
      };
      const button = form.querySelector('button[type=submit]');
      button.disabled = true;
      out.innerHTML = '<p class="working">Claude is weighing the evidence. This takes about a minute.</p>';
      try {
        const res = await estimate(key, dossier, property);
        out.innerHTML = resultHtml(res);
        out.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (err) {
        out.innerHTML = `<p class="notice">${esc(errorText(err))}</p>`;
      } finally {
        button.disabled = false;
      }
    });
  }

  window.prospectValuation = { open, buildRequest, compactDossier, SCHEMA };
})();
