// Récupère l'API statique publiée par le workflow quotidien (GitHub Pages) et
// répond aux fiches produit qui demandent « que sait-on de cette URL ? ».
// Aucune donnée de navigation ne sort du navigateur : on télécharge le fichier
// entier et la recherche se fait en local.
// Firefox expose `browser` (promesses garanties) ; Chrome seulement `chrome`.
const ext = globalThis.browser ?? globalThis.chrome;
const SITE_URL = 'https://aurel456.github.io/whey_price_tracker/';
const API_URL = SITE_URL + 'api/v1/prices.json';
const TTL_MS = 60 * 60 * 1000; // les prix ne bougent qu'une fois par jour

// Miroir exact de `_url_key()` dans hsn_tracker.py.
function urlKey(url) {
  let u = (url || '').trim().toLowerCase().split('#')[0].split('?')[0];
  u = u.replace(/^https?:\/\//, '').replace(/^www\./, '');
  return u.replace(/\/+$/, '');
}

async function getData() {
  const { cache } = await ext.storage.local.get('cache');
  if (cache && Date.now() - cache.at < TTL_MS) return cache.data;
  try {
    const res = await fetch(API_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.version !== 1) throw new Error('Version d\'API inattendue : ' + data.version);
    await ext.storage.local.set({ cache: { at: Date.now(), data } });
    return data;
  } catch (err) {
    if (cache) return cache.data; // hors ligne : dernières données connues
    throw err;
  }
}

async function lookup(url) {
  const data = await getData();
  const key = urlKey(url);
  const product = data.products.find((p) => p.key === key);
  if (!product) return { product: null };
  return {
    product,
    // Alternatives et classement au sein de la même famille (type + gamme pour
    // la whey) : une whey CFM n'est jamais comparée à un isolat de soja.
    best: data.best[product.family] || [],
    familyLabel: data.families[product.family] || '',
    ranking: (data.ranking || {})[product.family] || [],
    metric: data.metrics[product.type] || null,
    site: data.sites[product.site],
    sites: data.sites,
    siteUrl: SITE_URL,
    generated: data.generated,
  };
}

ext.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== 'lookup') return false;
  lookup(msg.url).then(sendResponse, (err) => sendResponse({ error: String(err) }));
  return true; // réponse asynchrone
});

ext.action.onClicked.addListener(() => ext.tabs.create({ url: SITE_URL }));
