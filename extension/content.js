// Encart « vrai prix » affiché sur les fiches produit suivies par le tracker.
// Rendu dans un Shadow DOM : le CSS du site marchand ne peut pas le casser, et
// le nôtre ne déborde pas sur la page.
(() => {
  const ext = globalThis.browser ?? globalThis.chrome; // cf. background.js
  const HOST_ID = 'vrai-prix-ext';
  const VISIBLE_SIZES = 4;
  let lastHref = null;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const num = (v, d = 2) => v == null ? '—'
    : v.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });

  const frDate = (iso) => iso ? iso.split('-').reverse().join('/') : '';

  // Où se situe le prix du jour par rapport à l'historique de CETTE taille.
  function priceStatus(s) {
    if (!s.inStock) return ['Rupture', 'muted'];
    const eps = 1e-9;
    if (s.price <= s.minAll + eps) {
      return s.history.length > 1
        ? ['Plus bas historique', 'good']
        : ['Prix stable', 'neutral'];
    }
    if (s.min90 != null && s.price <= s.min90 + eps) return ['Plus bas sur 90 j', 'good'];
    const pct = Math.round((s.price / s.min90 - 1) * 100);
    return [`+${pct} % vs plus bas`, pct >= 10 ? 'warn' : 'neutral'];
  }

  // Jauge plus bas ↔ plus haut sur 90 jours, avec le prix du jour placé dessus.
  // (Une courbe ne marche pas : HSN alterne promo / prix normal tous les 2-3
  // jours, ça donne un code-barres illisible à cette taille.)
  function rangeBar(s) {
    if (s.min90 == null || s.max90 == null || s.max90 - s.min90 < 0.01) return '';
    const pos = Math.max(0, Math.min(1, (s.price - s.min90) / (s.max90 - s.min90))) * 100;
    return `<div class="range" title="Sur 90 jours : de ${num(s.min90)} € à ${num(s.max90)} €">`
      + `<span class="lo">${num(s.min90)}</span>`
      + `<span class="track"><span class="dot" style="left:${pos.toFixed(0)}%"></span></span>`
      + `<span class="hi">${num(s.max90)}</span></div>`;
  }

  const STYLE = `
    :host { all: initial; }
    .card, .pill { --bg:#fff; --ink:#16181d; --muted:#6b7280; --line:#e5e7eb; --soft:#f6f7f9;
      --accent:#2563eb; --good:#0f7a55; --good-bg:#e7f6ef; --warn:#a15c07; --warn-bg:#fdf3e1;
      font: 13px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink); }
    @media (prefers-color-scheme: dark) {
      .card, .pill { --bg:#1b1d22; --ink:#eceef2; --muted:#9aa1ad; --line:#30333a; --soft:#24272d;
        --accent:#7aa7ff; --good:#5fd3a2; --good-bg:#173a2c; --warn:#f2b45c; --warn-bg:#3d2c12; }
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
    /* Tables : héritage explicite, sinon une page en mode quirks casse police et couleur. */
    table { width: 100%; border-collapse: collapse; font: inherit; color: inherit; }
    td { padding: 7px 12px; border-top: 1px solid var(--line); vertical-align: middle; }
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

  function bestLine(res, ownBest) {
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

  function rowHtml(s, d) {
    const [label, cls] = priceStatus(s);
    // « Top » = n°1, ou podium d'une famille assez grande pour que ça veuille dire quelque chose.
    const top = s.rank === 1 || (s.of >= 10 && s.rank <= 3);
    const rank = s.rank
      ? `<span class="sub rank${top ? ' top' : ''}" title="Classement au coût réel dans sa famille, tous sites confondus">#${s.rank}/${s.of}</span>`
      : '';
    return `<tr>
      <td class="size">${esc(s.size)}<span class="sub">${num(s.price)} €</span></td>
      <td><span class="chip ${cls}">${esc(label)}</span>${rangeBar(s)}</td>
      <td class="num"><span class="metric">${s.metric != null ? num(s.metric, d) + ' €' : '—'}</span>${rank}</td>
    </tr>`;
  }

  function cardHtml(res, expanded) {
    const { product, metric, site, siteUrl, generated } = res;
    const d = metric ? metric.decimals : 2;
    const sizes = [...product.sizes].sort((a, b) =>
      (a.metric ?? Infinity) - (b.metric ?? Infinity) || a.price - b.price);
    const ownBest = sizes.find((s) => s.metric != null && s.inStock)?.metric ?? null;
    const shown = expanded ? sizes : sizes.slice(0, VISIBLE_SIZES);
    const hidden = sizes.length - shown.length;
    return `<div class="card" role="complementary" aria-label="Vrai prix">
      <header><span class="title">💰 Vrai prix</span>
        <button class="icon collapse" title="Réduire" aria-label="Réduire">–</button>
        <button class="icon close" title="Masquer sur cette page" aria-label="Masquer">×</button></header>
      <div class="name" title="${esc(product.name)}">${esc(product.name)}</div>
      <div class="unit">${metric ? 'Coût réel en € ' + esc(metric.unit) + ' · ' : ''}relevé du ${frDate(site?.lastScrape || generated)}</div>
      <table>${shown.map((s) => rowHtml(s, d)).join('')}</table>
      ${hidden > 0 ? `<button class="more">Voir ${hidden} autre${hidden > 1 ? 's' : ''} taille${hidden > 1 ? 's' : ''}</button>` : ''}
      ${bestLine(res, ownBest)}
      <footer><span>Prix indicatifs, vérifie sur le site.</span>
        <a href="${esc(siteUrl + (site?.dashboard || ''))}" target="_blank" rel="noopener">Historique complet →</a></footer>
    </div>`;
  }

  async function render(res) {
    document.getElementById(HOST_ID)?.remove();
    if (!res || !res.product) return;
    const host = document.createElement('div');
    host.id = HOST_ID;
    const root = host.attachShadow({ mode: 'open' });
    const { collapsed } = await ext.storage.local.get('collapsed');
    const metrics = res.product.sizes.filter((s) => s.metric != null).map((s) => s.metric);
    const d = res.metric ? res.metric.decimals : 2;
    const pillText = metrics.length
      ? `💰 dès ${num(Math.min(...metrics), d)} € ${res.metric?.unit || ''}` : '💰 Vrai prix';

    const draw = (isCollapsed, expanded = false) => {
      root.innerHTML = `<style>${STYLE}</style>` + (isCollapsed
        ? `<button class="pill" title="Afficher le vrai prix">${esc(pillText)}</button>`
        : cardHtml(res, expanded));
      root.querySelector('.pill')?.addEventListener('click', () => toggle(false));
      root.querySelector('.collapse')?.addEventListener('click', () => toggle(true));
      root.querySelector('.close')?.addEventListener('click', () => host.remove());
      root.querySelector('.more')?.addEventListener('click', () => draw(false, true));
    };
    const toggle = (value) => { ext.storage.local.set({ collapsed: value }); draw(value); };

    draw(Boolean(collapsed));
    document.documentElement.appendChild(host);
  }

  function check() {
    if (location.href === lastHref) return;
    lastHref = location.href;
    ext.runtime.sendMessage({ type: 'lookup', url: location.href })
      .then(render)
      .catch(() => { /* API injoignable : on n'affiche rien plutôt qu'une erreur */ });
  }

  check();
  // MyProtein navigue parfois sans recharger la page (SPA).
  setInterval(check, 1500);
})();
