// Encart « vrai prix » affiché sur les fiches produit suivies par le tracker.
// Rendu dans un Shadow DOM : le CSS du site marchand ne peut pas le casser, et
// le nôtre ne déborde pas sur la page.
//
// Deux sources se complètent :
//   - l'API (relevé quotidien) : historique, plus bas sur 90 j, classement ;
//   - la page elle-même (« sélection ») : taille choisie et prix affiché à
//     l'instant, avec lesquels on recalcule le coût réel et le rang en direct.
(() => {
  const ext = globalThis.browser ?? globalThis.chrome; // cf. background.js
  const HOST_ID = 'vrai-prix-ext';
  const VISIBLE_SIZES = 4;
  const POLL_MS = 800;
  const SEEN_MAX = 100;
  const RECO_SHOWN = 6;
  let lastHref = null;
  let res = null;          // réponse de l'API pour la page courante (product: null hors fiche suivie)
  let live = null;         // {size, price} lus sur la page
  let liveSig = '';
  let ui = { collapsed: false, expanded: false, closed: false, tab: 'product', seenType: 'all' };
  let host = null, root = null;
  // Persistés dans ext.storage.local, donc partagés entre pages et onglets :
  let seen = {};           // produits suivis consultés {key: {name, url, site, type, lastSeen, views, sel…}}
  let pos = { right: 16, bottom: 16 };   // position de la bulle (coin bas-droit)
  let filters = { type: 'whey', site: 'all', family: 'all', noSweet: false, inStock: true, budget: '' };
  let data = null;         // API complète, chargée à la demande (onglets Mes produits / Conseiller)
  let ai = { question: '', loading: false, answer: '', error: '', model: '' };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const num = (v, d = 2) => v == null ? '—'
    : v.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });

  const frDate = (iso) => iso ? iso.split('-').reverse().join('/') : '';

  const parsePrice = (t) => {
    const m = String(t || '').replace(/\s/g, '').match(/(\d+(?:[.,]\d{1,2})?)/);
    return m ? parseFloat(m[1].replace(',', '.')) : null;
  };

  // ── Lecture des tailles (miroir de _parse_size_kg / _parse_size_caps) ─────
  // Lookahead sur les lettres : sinon « 30 GÉLULES » se lit « 30 g ».
  function sizeKg(label) {
    const m = String(label || '').match(/(\d+(?:[.,]\d+)?)\s*(kg|g)(?![A-Za-zÀ-ÿ])/i);
    if (!m) return null;
    const v = parseFloat(m[1].replace(',', '.'));
    return m[2].toLowerCase() === 'kg' ? v : v / 1000;
  }
  const sizeCaps = (label) => {
    const m = String(label || '').match(/(\d+)\s*(?:caps|capsules?|g[ée]lules?|softgels?|comprim)/i);
    return m ? parseInt(m[1], 10) : null;
  };
  const sizePortions = (label) => {
    const m = String(label || '').match(/(\d+)\s*(?:portions?|servings?)/i);
    return m ? parseInt(m[1], 10) : null;
  };

  // Même taille ? Portions d'abord (MyProtein : le poids varie selon l'arôme),
  // puis gélules, puis poids à 3 % près.
  function sameSize(a, b) {
    const pa = sizePortions(a), pb = sizePortions(b);
    if (pa && pb) return pa === pb;
    const ca = sizeCaps(a), cb = sizeCaps(b);
    if (ca && cb) return ca === cb;
    const ka = sizeKg(a), kb = sizeKg(b);
    return Boolean(ka && kb && Math.abs(ka - kb) / kb < 0.03);
  }

  // Coût réel de la sélection : celui du relevé si le prix n'a pas bougé (le
  // scraper arrondit le prix au kilo avant de diviser, recalculer donnerait un
  // centime d'écart avec la ligne du tableau), sinon recalculé.
  function selectionMetric(sel, tracked) {
    if (!sel) return null;
    if (tracked && tracked.metric != null && Math.abs(sel.price - tracked.price) < 0.005) return tracked.metric;
    return liveMetric(res.product, sel.size || tracked?.size, sel.price);
  }

  // Coût réel recalculé — mêmes formules que _enrich_row côté Python.
  function liveMetric(product, size, price) {
    const n = product.nutri || {};
    if (product.type === 'whey') {
      const kg = sizeKg(size);
      return kg && n.prot ? price / (kg * n.prot / 100) : null;
    }
    if (product.type === 'creatine') {
      const kg = sizeKg(size);
      return kg ? price / kg : null;
    }
    if (product.type === 'omega3') {
      const caps = sizeCaps(size);
      const mg = (n.epa || 0) + (n.dha || 0);
      return caps && mg ? price / (caps * mg / 1000) : null;
    }
    return null;
  }

  // ── Lecture de la sélection sur la page, par site ──────────────────────────
  let mpVariantsCache = null;
  function mpVariants() {
    if (mpVariantsCache) return mpVariantsCache;
    const out = {};
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try { data = JSON.parse(s.textContent); } catch { continue; }
      for (const node of [].concat(data['@graph'] || data)) {
        for (const v of node.hasVariant || (node['@type'] === 'Product' ? [node] : [])) {
          const offer = [].concat(v.offers || [])[0] || {};
          if (v.sku) out[String(v.sku)] = { name: v.name || '', price: parseFloat(offer.price) };
        }
      }
    }
    return (mpVariantsCache = out);
  }

  const READERS = {
    'www.hsnstore.fr': () => {
      // Formulaire du produit principal uniquement : les produits associés
      // (`product_related_addtocart_form_*`) ont leurs propres prix et tailles.
      const form = document.querySelector('form[id^="product_addtocart_form"]');
      if (!form) return null;
      const input = form.querySelector('input[name*="super_attribute"]:checked');
      const label = input
        ? (document.querySelector(`label[for="${input.id}"]`) || input.closest('label'))?.innerText?.trim()
        : null;
      const pid = form.querySelector('input[name="product"]')?.value;
      const el = (pid && document.getElementById('product-price-' + pid)?.querySelector('.price'))
        || document.querySelector('#sticky-add-to-cart .final-price .price');
      const price = parsePrice(el?.innerText);
      return price ? { size: label, price } : null;
    },
    'fr.myprotein.com': () => {
      // Le bouton « Ajouter au panier » porte le SKU de la variante choisie ;
      // le JSON-LD donne son nom (poids, portions) et son prix.
      const sku = document.querySelector('button[data-e2e="product_add-to-basket"]')?.dataset.sku;
      const v = sku && mpVariants()[sku];
      if (!v || !Number.isFinite(v.price)) return null;
      const btn = document.querySelector(`button.elements-variations-button[data-sku="${sku}"]`);
      const portions = sizePortions(v.name) || sizePortions(btn?.dataset.key);
      const weight = (v.name.match(/(\d+(?:[.,]\d+)?\s*(?:kg|g))(?![A-Za-zÀ-ÿ])/i) || [])[1];
      const size = portions && weight ? `${portions} portions (${weight})` : (btn?.dataset.key || v.name);
      return { size, price: v.price };
    },
  };

  function readLive() {
    const reader = READERS[location.hostname];
    try { return reader ? reader() : null; } catch { return null; }
  }

  // Taille du relevé correspondant à la sélection (null si absente des relevés).
  function trackedFor(sel) {
    if (!sel || !res) return null;
    const sizes = res.product.sizes;
    if (!sel.size) return sizes.length === 1 ? sizes[0] : null; // produit mono-taille
    // Plusieurs candidats possibles (MyProtein : « 30 portions » en 750 g et en
    // 870 g selon l'arôme) → le plus proche en poids.
    const kg = sizeKg(sel.size);
    const dist = (s) => (kg && sizeKg(s.size) ? Math.abs(sizeKg(s.size) - kg) : 0);
    return sizes.filter((s) => sameSize(s.size, sel.size)).sort((a, b) => dist(a) - dist(b))[0] || null;
  }

  // Rang « en direct » : la sélection remplace sa propre ligne du classement.
  function liveRank(metric, tracked) {
    if (metric == null || !res.product.ranked) return null;
    const others = res.ranking.filter(([, key, size]) =>
      !(key === res.product.key && tracked && size === tracked.size));
    return { rank: 1 + others.filter(([m]) => m < metric).length, of: others.length + 1 };
  }

  // Où se situe un prix par rapport à l'historique de CETTE taille.
  function priceStatus(s, price = s.price) {
    if (!s.inStock && price === s.price) return ['Rupture', 'muted'];
    const eps = 1e-9;
    if (price <= s.minAll + eps) {
      return s.history.length > 1 || price < s.minAll - eps
        ? ['Plus bas historique', 'good']
        : ['Prix stable', 'neutral'];
    }
    if (s.min90 != null && price <= s.min90 + eps) return ['Plus bas sur 90 j', 'good'];
    const pct = Math.round((price / s.min90 - 1) * 100);
    return [`+${pct} % vs plus bas`, pct >= 10 ? 'warn' : 'neutral'];
  }

  // Jauge plus bas ↔ plus haut sur 90 jours, avec le prix placé dessus.
  // (Une courbe ne marche pas : HSN alterne promo / prix normal tous les 2-3
  // jours, ça donne un code-barres illisible à cette taille.)
  function rangeBar(s, price = s.price) {
    if (s.min90 == null || s.max90 == null || s.max90 - s.min90 < 0.01) return '';
    const pos = Math.max(0, Math.min(1, (price - s.min90) / (s.max90 - s.min90))) * 100;
    return `<div class="range" title="Sur 90 jours : de ${num(s.min90)} € à ${num(s.max90)} €">`
      + `<span class="lo">${num(s.min90)}</span>`
      + `<span class="track"><span class="dot" style="left:${pos.toFixed(0)}%"></span></span>`
      + `<span class="hi">${num(s.max90)}</span></div>`;
  }

  const rankHtml = (r) => {
    if (!r || !r.rank) return '';
    // « Top » = n°1, ou podium d'une famille assez grande pour que ça veuille dire quelque chose.
    const top = r.rank === 1 || (r.of >= 10 && r.rank <= 3);
    return `<span class="sub rank${top ? ' top' : ''}" title="Classement au coût réel dans sa famille, tous sites confondus">#${r.rank}/${r.of}</span>`;
  };

  const STYLE = `
    :host { all: initial; }
    .card, .pill { --bg:#fff; --ink:#16181d; --muted:#6b7280; --line:#e5e7eb; --soft:#f6f7f9;
      --accent:#2563eb; --accent-bg:#eef4ff; --good:#0f7a55; --good-bg:#e7f6ef; --warn:#a15c07; --warn-bg:#fdf3e1;
      font: 13px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink); }
    @media (prefers-color-scheme: dark) {
      .card, .pill { --bg:#1b1d22; --ink:#eceef2; --muted:#9aa1ad; --line:#30333a; --soft:#24272d;
        --accent:#7aa7ff; --accent-bg:#1f2a40; --good:#5fd3a2; --good-bg:#173a2c; --warn:#f2b45c; --warn-bg:#3d2c12; }
    }
    /* right / bottom posés en ligne par applyPos() : la bulle se déplace. */
    .card { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; width: 350px;
      max-width: calc(100vw - 32px); max-height: calc(100vh - 32px); overflow: auto;
      background: var(--bg); border: 1px solid var(--line); border-radius: 12px;
      box-shadow: 0 10px 30px rgba(0,0,0,.18); }
    .pill { all: unset; position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; cursor: grab;
      background: var(--bg); color: var(--ink); border: 1px solid var(--line); border-radius: 999px;
      padding: 7px 12px; box-shadow: 0 6px 18px rgba(0,0,0,.15); font: 600 13px system-ui, sans-serif;
      touch-action: none; user-select: none; }
    header { display: flex; align-items: center; gap: 8px; padding: 10px 12px 6px; cursor: grab;
      touch-action: none; user-select: none; position: sticky; top: 0; background: var(--bg); z-index: 1; }
    header .title { font-weight: 700; flex: 1; }
    header .grip { color: var(--muted); font-size: 11px; letter-spacing: -2px; }
    .tabs { display: flex; gap: 2px; padding: 0 8px; border-bottom: 1px solid var(--line); margin-bottom: 8px; }
    .tab { all: unset; cursor: pointer; padding: 6px 8px; font-size: 12px; font-weight: 600; color: var(--muted);
      border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .tab:hover, .tab:focus-visible { color: var(--ink); }
    .tab.on { color: var(--accent); border-bottom-color: var(--accent); }
    .empty { padding: 8px 12px 14px; color: var(--muted); font-size: 12px; margin: 0; }
    .seg { display: flex; gap: 4px; padding: 0 12px 8px; flex-wrap: wrap; }
    .seg button { all: unset; cursor: pointer; font-size: 12px; padding: 3px 10px; border-radius: 999px;
      border: 1px solid var(--line); color: var(--muted); }
    .seg button.on { border-color: var(--accent); color: var(--accent); background: var(--accent-bg); font-weight: 600; }
    .frow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 0 12px 8px; font-size: 12px; }
    .frow select, .frow input[type=number] { font: inherit; color: inherit; background: var(--soft);
      border: 1px solid var(--line); border-radius: 6px; padding: 3px 6px; }
    .frow input[type=number] { width: 64px; }
    .frow label { display: inline-flex; gap: 4px; align-items: center; cursor: pointer; }
    ul.list, ol.list { list-style: none; margin: 0; padding: 0; }
    .list li { padding: 8px 12px; border-top: 1px solid var(--line); }
    .list .top { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .list a { color: var(--ink); font-weight: 600; text-decoration: none; }
    .list a:hover { color: var(--accent); text-decoration: underline; }
    .list .bot { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; margin-top: 2px; }
    .tag { display: inline-block; font-size: 10px; padding: 0 6px; border-radius: 999px; background: var(--soft);
      color: var(--muted); margin-left: 4px; vertical-align: 1px; font-weight: 600; }
    .tag.seen { background: var(--accent-bg); color: var(--accent); }
    .good { color: var(--good); font-weight: 600; }
    .linkbtn { all: unset; cursor: pointer; color: var(--accent); font-size: 12px; font-weight: 600; }
    .linkbtn:hover { text-decoration: underline; }
    .ai { padding: 10px 12px; border-top: 1px solid var(--line); background: var(--soft); }
    .ai textarea { width: 100%; box-sizing: border-box; min-height: 52px; resize: vertical; font: inherit; font-size: 12px;
      color: inherit; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; }
    .ai .act { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; gap: 8px; }
    .ai .ask { all: unset; cursor: pointer; background: var(--accent); color: #fff; font-size: 12px; font-weight: 700;
      padding: 5px 12px; border-radius: 8px; }
    .ai .ask[disabled] { opacity: .6; cursor: progress; }
    .answer { margin-top: 8px; font-size: 12px; line-height: 1.5; background: var(--bg); border: 1px solid var(--line);
      border-radius: 8px; padding: 8px 10px; }
    .answer.err { color: var(--warn); }
    .answer ul { margin: 4px 0; padding-left: 16px; }
    button.icon { all: unset; cursor: pointer; color: var(--muted); padding: 2px 6px; border-radius: 6px; font-size: 15px; }
    button.icon:hover, button.icon:focus-visible { background: var(--soft); color: var(--ink); }
    .name { padding: 0 12px; color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .unit { padding: 2px 12px 8px; font-size: 11px; color: var(--muted); }
    .live { margin: 0 12px 10px; padding: 10px 12px; border-radius: 10px; background: var(--accent-bg);
      border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); }
    .live .lbl { font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--accent); }
    .live .row1 { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-top: 2px; }
    .live .sel { font-weight: 700; }
    .live .big { font-size: 20px; font-weight: 800; font-variant-numeric: tabular-nums; }
    .live .row2 { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 4px; }
    .live .note { font-size: 11px; color: var(--muted); margin-top: 4px; }
    /* Tables : héritage explicite, sinon une page en mode quirks casse police et couleur. */
    table { width: 100%; border-collapse: collapse; font: inherit; color: inherit; }
    td { padding: 7px 12px; border-top: 1px solid var(--line); vertical-align: middle; }
    tr.on td { background: var(--accent-bg); }
    tr.on td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
    td.size { font-weight: 600; white-space: nowrap; }
    td.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .metric { font-weight: 700; }
    .sub { display: block; font-size: 11px; color: var(--muted); font-weight: 400; }
    .chip { display: inline-block; white-space: nowrap; font-size: 11px; padding: 1px 7px; border-radius: 999px;
      background: var(--soft); color: var(--muted); }
    .chip.good { background: var(--good-bg); color: var(--good); font-weight: 600; }
    .chip.warn { background: var(--warn-bg); color: var(--warn); }
    .rank { font-size: 11px; color: var(--muted); }
    .rank.top { color: var(--good); font-weight: 700; }
    .range { display: flex; align-items: center; gap: 5px; margin-top: 5px; font-size: 10px;
      color: var(--muted); font-variant-numeric: tabular-nums; }
    .track { position: relative; flex: 1; min-width: 40px; height: 4px; border-radius: 2px;
      background: linear-gradient(90deg, var(--good-bg), var(--warn-bg)); }
    .dot { position: absolute; top: 50%; width: 9px; height: 9px; border-radius: 50%;
      background: var(--accent); border: 2px solid var(--bg); transform: translate(-50%, -50%); }
    .more { all: unset; display: block; width: 100%; box-sizing: border-box; padding: 6px 12px; cursor: pointer;
      text-align: center; font-size: 12px; color: var(--accent); border-top: 1px solid var(--line); }
    .more:hover, .more:focus-visible { background: var(--soft); }
    .best { margin: 0; padding: 10px 12px; background: var(--soft); border-top: 1px solid var(--line); font-size: 12px; }
    .best a, footer a { color: var(--accent); text-decoration: none; font-weight: 600; }
    .best a:hover, footer a:hover { text-decoration: underline; }
    footer { display: flex; justify-content: space-between; gap: 8px; padding: 8px 12px 10px;
      font-size: 11px; color: var(--muted); border-top: 1px solid var(--line); }
  `;

  function bestLine(ownBest) {
    const top = res.best[0];
    if (!top || !res.metric) return '';
    const family = res.familyLabel ? ` en ${esc(res.familyLabel)}` : '';
    if (top.key === res.product.key) {
      const of = res.product.sizes.find((s) => s.rank === 1)?.of;
      return `<p class="best">🏆 Meilleur rapport au coût réel${family}${of > 1 ? ` (${of} références suivies)` : ''}.</p>`;
    }
    const gap = ownBest ? Math.round((1 - top.metric / ownBest) * 100) : null;
    const siteName = top.site === res.product.site ? '' : ` chez ${esc(res.sites[top.site]?.name || top.site)}`;
    return `<p class="best">Moins cher${family}${siteName} : `
      + `<a href="${esc(top.url)}" target="_blank" rel="noopener">${esc(top.name)}</a> (${esc(top.size)}) — `
      + `<b>${num(top.metric, res.metric.decimals)} €</b> ${esc(res.metric.unit)}${gap > 0 ? ` · ${gap} % de moins` : ''}</p>`;
  }

  // Bloc « Ta sélection » : ce que la page affiche maintenant, recalculé.
  function liveHtml(sel, tracked, metric) {
    const d = res.metric ? res.metric.decimals : 2;
    const unit = res.metric ? esc(res.metric.unit) : '';
    const r = liveRank(metric, tracked);
    const [label, cls] = tracked ? priceStatus(tracked, sel.price) : [null, null];
    let note;
    if (!tracked) {
      note = 'Taille absente de nos relevés : coût calculé avec le prix affiché.';
    } else {
      const diff = Math.round((sel.price / tracked.price - 1) * 100);
      note = `Relevé du ${frDate(tracked.date)} : ${num(tracked.price)} €`
        + (diff === 0 ? ' (identique)' : diff < 0 ? ` · ${-diff} % moins cher aujourd'hui` : ` · ${diff} % plus cher aujourd'hui`);
    }
    return `<section class="live" aria-live="polite">
      <div class="lbl">Ta sélection</div>
      <div class="row1"><span class="sel">${esc(sel.size || tracked?.size || '')}</span><span>${num(sel.price)} €</span></div>
      <div class="row2">
        <span>${metric != null ? `<span class="big">${num(metric, d)} €</span> ${unit}` : '<span class="sub">coût réel indisponible</span>'}</span>
        ${r ? rankHtml(r).replace('class="sub ', 'class="') : ''}
      </div>
      ${label ? `<div class="row2"><span class="chip ${cls}">${esc(label)}</span>${rangeBar(tracked, sel.price)}</div>` : ''}
      <div class="note">${note}</div>
    </section>`;
  }

  function rowHtml(s, d, on) {
    const [label, cls] = priceStatus(s);
    return `<tr${on ? ' class="on"' : ''}>
      <td class="size">${esc(s.size)}<span class="sub">${num(s.price)} €</span></td>
      <td><span class="chip ${cls}">${esc(label)}</span>${rangeBar(s)}</td>
      <td class="num"><span class="metric">${s.metric != null ? num(s.metric, d) + ' €' : '—'}</span>${rankHtml(s)}</td>
    </tr>`;
  }

  // ── Onglet « Ce produit » ──────────────────────────────────────────────────
  function productBody() {
    const { product, metric, site, siteUrl, generated } = res;
    const d = metric ? metric.decimals : 2;
    const sizes = [...product.sizes].sort((a, b) =>
      (a.metric ?? Infinity) - (b.metric ?? Infinity) || a.price - b.price);
    const ownBest = sizes.find((s) => s.metric != null && s.inStock)?.metric ?? null;
    const tracked = trackedFor(live);
    const lm = selectionMetric(live, tracked);
    const shown = ui.expanded ? sizes : sizes.slice(0, VISIBLE_SIZES);
    if (tracked && !shown.includes(tracked)) shown.push(tracked); // la taille choisie reste visible
    const hidden = sizes.length - shown.length;
    return `<div class="name" title="${esc(product.name)}">${esc(product.name)}</div>
      <div class="unit">${metric ? 'Coût réel en € ' + esc(metric.unit) + ' · ' : ''}relevé du ${frDate(site?.lastScrape || generated)}</div>
      ${live ? liveHtml(live, tracked, lm) : ''}
      <table>${shown.map((s) => rowHtml(s, d, s === tracked)).join('')}</table>
      ${hidden > 0 ? `<button class="more">Voir ${hidden} autre${hidden > 1 ? 's' : ''} taille${hidden > 1 ? 's' : ''}</button>` : ''}
      ${bestLine(ownBest)}
      <footer><span>Prix indicatifs, vérifie sur le site.</span>
        <a href="${esc(siteUrl + (site?.dashboard || ''))}" target="_blank" rel="noopener">Historique complet →</a></footer>`;
  }

  // ── Mémoire « Mes produits » ───────────────────────────────────────────────
  // Stockage local de l'extension : partagé entre toutes les pages et tous les
  // onglets, jamais envoyé nulle part (sauf « Demander à l'IA », cf. options).
  async function remember(sel) {
    if (!res?.product) return;
    const p = res.product;
    const prev = seen[p.key] || {};
    const now = Date.now();
    const visit = sel === undefined;  // appel au chargement de la fiche
    const pick = visit ? prev.sel : sel && {
      size: sel.size || trackedFor(sel)?.size || '', price: sel.price, metric: selectionMetric(sel, trackedFor(sel)),
    };
    seen[p.key] = {
      key: p.key, site: p.site, url: p.url, name: p.name, type: p.type, family: p.family,
      firstSeen: prev.firstSeen || now, lastSeen: now, views: (prev.views || 0) + (visit ? 1 : 0),
      sel: pick || prev.sel || null,
    };
    const old = Object.values(seen).sort((a, b) => b.lastSeen - a.lastSeen).slice(SEEN_MAX);
    for (const o of old) delete seen[o.key];
    await ext.storage.local.set({ seen });
  }

  async function loadData() {
    if (data || loadData.pending) return;
    loadData.pending = true;
    try {
      data = await ext.runtime.sendMessage({ type: 'data' });
      if (data?.error) data = null;
    } finally {
      loadData.pending = false;
    }
    draw();
  }

  const bestOffer = (p) => p.sizes.filter((s) => s.metric != null && s.inStock)
    .sort((a, b) => a.metric - b.metric)[0] || null;

  const TYPE_LABEL = { whey: 'Whey', omega3: 'Oméga-3', creatine: 'Créatine' };

  function seenBody() {
    const items = Object.values(seen);
    if (!items.length) {
      return '<p class="empty">Les fiches HSN et MyProtein suivies que tu ouvres s\'ajoutent ici automatiquement, avec la taille et le prix que tu as regardés.</p>';
    }
    if (!data) { loadData(); return '<p class="empty">Chargement des prix…</p>'; }
    const byKey = Object.fromEntries(data.products.map((p) => [p.key, p]));
    const types = Object.keys(TYPE_LABEL).filter((t) => items.some((i) => i.type === t));
    const t = types.includes(ui.seenType) ? ui.seenType : 'all';
    const rows = items.filter((i) => t === 'all' || i.type === t)
      .map((i) => ({ i, p: byKey[i.key], b: byKey[i.key] && bestOffer(byKey[i.key]) }))
      // Un type choisi → classement au coût réel (même unité), sinon les plus récents.
      .sort((x, y) => (t === 'all' ? y.i.lastSeen - x.i.lastSeen
        : (x.b?.metric ?? Infinity) - (y.b?.metric ?? Infinity)));
    const seg = `<div class="seg">${[['all', 'Tout'], ...types.map((k) => [k, TYPE_LABEL[k]])]
      .map(([k, l]) => `<button data-seen-type="${k}" class="${k === t ? 'on' : ''}">${l}</button>`).join('')}</div>`;
    const li = rows.map(({ i, p, b }) => {
      const m = data.metrics[i.type] || { unit: '', decimals: 2 };
      const site = data.sites[i.site]?.name || i.site;
      const when = new Date(i.lastSeen).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });
      const sel = i.sel ? `Ta sélection : ${esc(i.sel.size)} · ${num(i.sel.price)} €` : '';
      const best = b ? `dès <b>${num(b.metric, m.decimals)} €</b>${b.rank ? ` · #${b.rank}/${b.of}` : ''}`
        : (p ? 'pas de prix en stock' : 'plus suivi');
      return `<li><div class="top"><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.name)}</a>
          <button class="icon forget" data-key="${esc(i.key)}" title="Retirer" aria-label="Retirer">×</button></div>
        <div class="bot"><span class="sub">${esc(site)} · vu le ${when}${i.views > 1 ? ` · ${i.views}×` : ''}</span><span>${best}</span></div>
        ${sel ? `<div class="sub">${sel}</div>` : ''}</li>`;
    }).join('');
    return `${seg}<ul class="list">${li}</ul>
      <footer><span>${t === 'all' ? 'Du plus récent au plus ancien' : 'Classés au coût réel ' + esc(data.metrics[t]?.unit || '')}</span>
        <button class="linkbtn clear-seen">Tout effacer</button></footer>`;
  }

  // ── Onglet « Conseiller » ──────────────────────────────────────────────────
  function candidates() {
    const out = [];
    const budget = parseFloat(filters.budget);
    for (const p of data.products) {
      // `ranked` exclut les « aliments enrichis » (crème de riz protéinée…).
      if (p.type !== filters.type || !p.ranked) continue;
      if (filters.site !== 'all' && p.site !== filters.site) continue;
      if (filters.type === 'whey' && filters.family !== 'all' && p.family !== filters.family) continue;
      if (filters.type === 'whey' && filters.noSweet && !p.tags.includes('sans_edulcorant')) continue;
      for (const s of p.sizes) {
        if (s.metric == null || (filters.inStock && !s.inStock)) continue;
        if (budget > 0 && s.price > budget) continue;
        out.push({ p, s });
      }
    }
    return out.sort((a, b) => a.s.metric - b.s.metric);
  }

  function aiContext(list) {
    const m = data.metrics[filters.type];
    return {
      date_releve: data.generated,
      categorie: m.label,
      unite_cout_reel: '€ ' + m.unit,
      filtres: {
        site: filters.site === 'all' ? 'tous' : data.sites[filters.site]?.name,
        gamme: filters.family === 'all' ? 'toutes' : data.families[filters.family],
        sans_edulcorant: filters.noSweet, en_stock: filters.inStock,
        budget_max_par_pot_eur: parseFloat(filters.budget) || null,
      },
      offres: list.slice(0, 12).map(({ p, s }) => ({
        nom: p.name, site: data.sites[p.site]?.name, taille: s.size, prix_eur: s.price,
        cout_reel: Number(s.metric.toFixed(m.decimals)), rang: s.rank ? `${s.rank}/${s.of}` : null,
        plus_bas_90j_eur: s.min90, au_plus_bas: s.price <= s.min90 + 1e-9, tags: p.tags,
        deja_consulte: Boolean(seen[p.key]), url: p.url,
      })),
      produits_consultes: Object.values(seen).sort((a, b) => b.lastSeen - a.lastSeen).slice(0, 10).map((i) => ({
        nom: i.name, site: data.sites[i.site]?.name, type: i.type,
        selection: i.sel ? `${i.sel.size} à ${i.sel.price} €` : null,
      })),
    };
  }

  // Markdown minimal de la réponse : gras, listes, retours à la ligne (après échappement).
  function mdLite(text) {
    const lines = esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split('\n');
    let html = '', inList = false;
    for (const line of lines) {
      const item = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (item) { html += (inList ? '' : '<ul>') + `<li>${item[1]}</li>`; inList = true; continue; }
      if (inList) { html += '</ul>'; inList = false; }
      if (line.trim()) html += line + '<br>';
    }
    return html + (inList ? '</ul>' : '');
  }

  function recoBody() {
    if (!data) { loadData(); return '<p class="empty">Chargement des offres…</p>'; }
    const list = candidates();
    const m = data.metrics[filters.type];
    const seg = `<div class="seg">${Object.entries(TYPE_LABEL).map(([k, l]) =>
      `<button data-reco-type="${k}" class="${k === filters.type ? 'on' : ''}">${l}</button>`).join('')}</div>`;
    const siteSel = `<select data-f="site" aria-label="Site"><option value="all">Tous les sites</option>${
      Object.entries(data.sites).map(([k, s]) => `<option value="${k}"${filters.site === k ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select>`;
    const famSel = filters.type !== 'whey' ? '' : `<select data-f="family" aria-label="Gamme"><option value="all">Toutes gammes</option>${
      Object.entries(data.families).filter(([k]) => k.startsWith('whey:'))
        .map(([k, l]) => `<option value="${k}"${filters.family === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    const checks = `${filters.type === 'whey' ? `<label><input type="checkbox" data-f="noSweet"${filters.noSweet ? ' checked' : ''}> Sans édulcorant</label>` : ''}
      <label><input type="checkbox" data-f="inStock"${filters.inStock ? ' checked' : ''}> En stock</label>
      <label>Pot ≤ <input type="number" min="0" step="1" data-f="budget" value="${esc(filters.budget)}" placeholder="—"> €</label>`;
    const li = list.slice(0, RECO_SHOWN).map(({ p, s }, idx) => {
      const low = s.min90 != null && s.price <= s.min90 + 1e-9;
      return `<li><div class="top"><span>${idx + 1}. <a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>${
        seen[p.key] ? '<span class="tag seen">déjà vu</span>' : ''}</span>
          <b>${num(s.metric, m.decimals)} €</b></div>
        <div class="bot"><span class="sub">${esc(data.sites[p.site]?.name || p.site)} · ${esc(s.size)} · ${num(s.price)} €${
          low ? ' · <span class="good">plus bas 90 j</span>' : ''}</span><span class="sub">${s.rank ? `#${s.rank}/${s.of}` : ''}</span></div></li>`;
    }).join('');
    const results = list.length
      ? `<ol class="list">${li}</ol>`
      : '<p class="empty">Aucune offre ne correspond à ces filtres.</p>';
    let answer = '';
    if (ai.error === 'NO_KEY') {
      answer = '<div class="answer err">Ajoute ta clé API dans les options de l\'extension. <button class="linkbtn open-options">Ouvrir les options</button></div>';
    } else if (ai.error) {
      answer = `<div class="answer err">${esc(ai.error)}</div>`;
    } else if (ai.answer) {
      answer = `<div class="answer">${mdLite(ai.answer)}<div class="sub">Réponse générée par ${esc(ai.model)} à partir des offres ci-dessus — vérifie avant d'acheter.</div></div>`;
    }
    return `${seg}<div class="frow">${siteSel}${famSel}</div><div class="frow">${checks}</div>
      <div class="unit">${list.length} offre${list.length > 1 ? 's' : ''} · coût réel en € ${esc(m.unit)}</div>
      ${results}
      <div class="ai">
        <textarea class="q" placeholder="Ex. : whey pour 2 mois sans sucralose, la moins chère au coût réel ?">${esc(ai.question)}</textarea>
        <div class="act"><span class="sub">L'IA ne choisit que parmi ces offres.</span>
          <button class="ask"${ai.loading ? ' disabled' : ''}>${ai.loading ? 'Réflexion…' : 'Demander à l\'IA'}</button></div>
        ${answer}
      </div>`;
  }

  async function askAI() {
    if (!data || ai.loading) return;
    ai = { ...ai, loading: true, answer: '', error: '' };
    draw();
    const question = ai.question.trim() || 'Quelle est la meilleure option pour moi avec ces filtres ?';
    try {
      const r = await ext.runtime.sendMessage({ type: 'ask', question, context: aiContext(candidates()) });
      ai = { ...ai, loading: false, answer: r.answer || '', error: r.error || '', model: r.model || '' };
    } catch (err) {
      ai = { ...ai, loading: false, error: String(err) };
    }
    draw();
  }

  // ── Bulle : rendu ──────────────────────────────────────────────────────────
  function cardHtml() {
    const n = Object.keys(seen).length;
    const tabs = [res?.product && ['product', 'Ce produit'], ['seen', `Mes produits${n ? ` (${n})` : ''}`],
      ['reco', 'Conseiller']].filter(Boolean);
    const body = ui.tab === 'product' && res?.product ? productBody()
      : ui.tab === 'reco' ? recoBody() : seenBody();
    return `<div class="card" role="complementary" aria-label="Vrai prix">
      <header title="Glisser pour déplacer"><span class="grip" aria-hidden="true">⋮⋮</span><span class="title">💰 Vrai prix</span>
        <button class="icon collapse" title="Réduire" aria-label="Réduire">–</button>
        <button class="icon close" title="Masquer sur cette page" aria-label="Masquer">×</button></header>
      <nav class="tabs" role="tablist">${tabs.map(([k, l]) =>
        `<button role="tab" class="tab${ui.tab === k ? ' on' : ''}" data-tab="${k}" aria-selected="${ui.tab === k}">${l}</button>`).join('')}</nav>
      ${body}
    </div>`;
  }

  function pillText() {
    if (!res?.product) return `💰 Mes produits (${Object.keys(seen).length})`;
    const d = res.metric ? res.metric.decimals : 2;
    const unit = res.metric?.unit || '';
    const tracked = trackedFor(live);
    const lm = selectionMetric(live, tracked);
    if (lm != null) return `💰 ${num(lm, d)} € ${unit}`;
    const metrics = res.product.sizes.filter((s) => s.metric != null).map((s) => s.metric);
    return metrics.length ? `💰 dès ${num(Math.min(...metrics), d)} € ${unit}` : '💰 Vrai prix';
  }

  function draw() {
    if (!root) return;
    const scroll = root.querySelector('.card')?.scrollTop || 0;
    root.innerHTML = `<style>${STYLE}</style>` + (ui.collapsed
      ? `<button class="pill" title="Afficher (glisser pour déplacer)">${esc(pillText())}</button>`
      : cardHtml());
    const card = root.querySelector('.card');
    if (card) card.scrollTop = scroll;
    applyPos();
  }

  // ── Bulle : déplacement ────────────────────────────────────────────────────
  // Ancrée par son coin bas-droit (right / bottom) : réduite ou dépliée, elle
  // reste au même endroit. Poignée = en-tête de la carte, ou la pastille.
  let drag = null, suppressClick = false;

  const clampPos = (p) => ({
    right: Math.round(Math.min(Math.max(0, p.right), innerWidth - 80)),
    bottom: Math.round(Math.min(Math.max(0, p.bottom), innerHeight - 40)),
  });

  function applyPos() {
    const el = root?.querySelector('.card, .pill');
    if (!el) return;
    el.style.right = pos.right + 'px';
    el.style.bottom = pos.bottom + 'px';
    if (el.classList.contains('card')) el.style.maxHeight = `calc(100vh - ${pos.bottom + 16}px)`;
  }

  function onPointerDown(e) {
    if (e.button !== 0 || !e.target.closest('header, .pill') || e.target.closest('header button')) return;
    drag = { x: e.clientX, y: e.clientY, start: { ...pos }, moved: false };
    addEventListener('pointermove', onPointerMove);
    addEventListener('pointerup', onPointerUp, { once: true });
  }

  function onPointerMove(e) {
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;  // simple clic, pas un glisser
    drag.moved = true;
    pos = clampPos({ right: drag.start.right - dx, bottom: drag.start.bottom - dy });
    applyPos();
  }

  function onPointerUp() {
    removeEventListener('pointermove', onPointerMove);
    if (drag?.moved) {
      ext.storage.local.set({ pos });
      suppressClick = true;  // le clic qui suit un glisser ne doit pas déplier la pastille
      setTimeout(() => { suppressClick = false; }, 50);
    }
    drag = null;
  }

  // ── Bulle : interactions (délégation sur le shadow root, posée une fois) ───
  function onClick(e) {
    if (suppressClick) return;
    const t = e.target;
    const tab = t.closest('[data-tab]');
    if (tab) { ui.tab = tab.dataset.tab; draw(); return; }
    if (t.closest('.pill')) return setCollapsed(false);
    if (t.closest('.collapse')) return setCollapsed(true);
    if (t.closest('.close')) { ui.closed = true; host.remove(); host = root = null; return; }
    if (t.closest('.more')) { ui.expanded = true; draw(); return; }
    const st = t.closest('[data-seen-type]');
    if (st) { ui.seenType = st.dataset.seenType; draw(); return; }
    const rt = t.closest('[data-reco-type]');
    if (rt) { setFilter('type', rt.dataset.recoType); return; }
    const fg = t.closest('.forget');
    if (fg) { delete seen[fg.dataset.key]; ext.storage.local.set({ seen }); draw(); return; }
    if (t.closest('.clear-seen')) { seen = {}; ext.storage.local.set({ seen }); draw(); return; }
    if (t.closest('.ask')) { askAI(); return; }
    if (t.closest('.open-options')) ext.runtime.sendMessage({ type: 'openOptions' });
  }

  function setFilter(key, value) {
    filters = { ...filters, [key]: value };
    if (key === 'type') filters.family = 'all';
    ext.storage.local.set({ filters });
    draw();
  }

  function onChange(e) {
    const f = e.target.dataset?.f;
    if (f) setFilter(f, e.target.type === 'checkbox' ? e.target.checked : e.target.value);
  }

  function onInput(e) {
    if (e.target.classList?.contains('q')) ai.question = e.target.value;  // pas de redessin pendant la saisie
  }

  function onKeyDown(e) {
    // Les raccourcis clavier du site (recherche, etc.) ne doivent pas voler la frappe.
    e.stopPropagation();
    if (e.target.classList?.contains('q') && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) askAI();
  }

  function setCollapsed(value) {
    ui.collapsed = value;
    ext.storage.local.set({ collapsed: value });
    draw();
  }

  // ── Cycle de vie ───────────────────────────────────────────────────────────
  async function mount(response) {
    host?.remove();
    host = root = null;
    res = response && !response.error ? response : null;
    const stored = await ext.storage.local.get(['collapsed', 'seen', 'pos', 'filters']);
    seen = stored.seen || {};
    pos = clampPos(stored.pos || pos);
    filters = { ...filters, ...(stored.filters || {}) };
    // Hors fiche suivie : la bulle n'apparaît (réduite) que s'il y a un historique.
    if (!res?.product && !Object.keys(seen).length) return;
    ui = {
      collapsed: res?.product ? Boolean(stored.collapsed) : true,
      expanded: false, closed: false, tab: res?.product ? 'product' : 'seen', seenType: 'all',
    };
    host = document.createElement('div');
    host.id = HOST_ID;
    root = host.attachShadow({ mode: 'open' });
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
    root.addEventListener('input', onInput);
    root.addEventListener('keydown', onKeyDown);
    root.addEventListener('pointerdown', onPointerDown);
    live = res?.product ? readLive() : null;
    liveSig = JSON.stringify(live);
    if (res?.product) await remember();
    if (live) await remember(live);
    draw();
    document.documentElement.appendChild(host);
  }

  function tick() {
    if (location.href !== lastHref) {
      lastHref = location.href;
      mpVariantsCache = null;
      ext.runtime.sendMessage({ type: 'lookup', url: location.href })
        .then(mount)
        .catch(() => { /* API injoignable : on n'affiche rien plutôt qu'une erreur */ });
      return;
    }
    if (!res?.product || !root || ui.closed) return;
    // Sélection modifiée sur la page (taille, arôme, promo) → mémorisée, et
    // redessinée si elle est visible (pas pendant qu'on tape dans le conseiller).
    const now = readLive();
    const sig = JSON.stringify(now);
    if (sig !== liveSig) {
      live = now;
      liveSig = sig;
      if (now) remember(now);
      if (ui.collapsed || ui.tab === 'product') draw();
    }
  }

  // Les autres onglets du navigateur peuvent ajouter des produits vus.
  ext.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes.seen || !root) return;
    seen = changes.seen.newValue || {};
    if (ui.tab === 'seen' && !ui.collapsed) draw();
  });
  addEventListener('resize', () => { pos = clampPos(pos); applyPos(); });

  tick();
  // Lecture régulière : URL (MyProtein navigue parfois sans recharger) et
  // sélection courante. Deux querySelector par tour, coût négligeable.
  setInterval(tick, POLL_MS);
})();
