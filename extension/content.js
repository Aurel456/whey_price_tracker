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
  let lastHref = null;
  let res = null;          // réponse de l'API pour la page courante
  let live = null;         // {size, price} lus sur la page
  let liveSig = '';
  let ui = { collapsed: false, expanded: false, closed: false };
  let host = null, root = null;

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
    .card { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; width: 340px;
      max-width: calc(100vw - 32px); max-height: calc(100vh - 32px); overflow: auto;
      background: var(--bg); border: 1px solid var(--line); border-radius: 12px;
      box-shadow: 0 10px 30px rgba(0,0,0,.18); }
    .pill { all: unset; position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; cursor: pointer;
      background: var(--bg); color: var(--ink); border: 1px solid var(--line); border-radius: 999px;
      padding: 7px 12px; box-shadow: 0 6px 18px rgba(0,0,0,.15); font: 600 13px system-ui, sans-serif; }
    header { display: flex; align-items: center; gap: 8px; padding: 10px 12px 6px; }
    header .title { font-weight: 700; flex: 1; }
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

  function cardHtml() {
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
    return `<div class="card" role="complementary" aria-label="Vrai prix">
      <header><span class="title">💰 Vrai prix</span>
        <button class="icon collapse" title="Réduire" aria-label="Réduire">–</button>
        <button class="icon close" title="Masquer sur cette page" aria-label="Masquer">×</button></header>
      <div class="name" title="${esc(product.name)}">${esc(product.name)}</div>
      <div class="unit">${metric ? 'Coût réel en € ' + esc(metric.unit) + ' · ' : ''}relevé du ${frDate(site?.lastScrape || generated)}</div>
      ${live ? liveHtml(live, tracked, lm) : ''}
      <table>${shown.map((s) => rowHtml(s, d, s === tracked)).join('')}</table>
      ${hidden > 0 ? `<button class="more">Voir ${hidden} autre${hidden > 1 ? 's' : ''} taille${hidden > 1 ? 's' : ''}</button>` : ''}
      ${bestLine(ownBest)}
      <footer><span>Prix indicatifs, vérifie sur le site.</span>
        <a href="${esc(siteUrl + (site?.dashboard || ''))}" target="_blank" rel="noopener">Historique complet →</a></footer>
    </div>`;
  }

  function pillText() {
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
      ? `<button class="pill" title="Afficher le vrai prix">${esc(pillText())}</button>`
      : cardHtml());
    const card = root.querySelector('.card');
    if (card) card.scrollTop = scroll;
    root.querySelector('.pill')?.addEventListener('click', () => setCollapsed(false));
    root.querySelector('.collapse')?.addEventListener('click', () => setCollapsed(true));
    root.querySelector('.close')?.addEventListener('click', () => { ui.closed = true; host.remove(); host = null; });
    root.querySelector('.more')?.addEventListener('click', () => { ui.expanded = true; draw(); });
  }

  function setCollapsed(value) {
    ui.collapsed = value;
    ext.storage.local.set({ collapsed: value });
    draw();
  }

  async function mount(response) {
    host?.remove();
    host = root = null;
    res = response && response.product ? response : null;
    if (!res) return;
    const { collapsed } = await ext.storage.local.get('collapsed');
    ui = { collapsed: Boolean(collapsed), expanded: false, closed: false };
    host = document.createElement('div');
    host.id = HOST_ID;
    root = host.attachShadow({ mode: 'open' });
    live = readLive();
    liveSig = JSON.stringify(live);
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
    if (!res || ui.closed) return;
    // Sélection modifiée sur la page (taille, arôme, promo) → on redessine.
    const now = readLive();
    const sig = JSON.stringify(now);
    if (sig !== liveSig) {
      live = now;
      liveSig = sig;
      draw();
    }
  }

  tick();
  // Lecture régulière : URL (MyProtein navigue parfois sans recharger) et
  // sélection courante. Deux querySelector par tour, coût négligeable.
  setInterval(tick, POLL_MS);
})();
