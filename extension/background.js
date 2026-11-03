// Récupère l'API statique publiée par le workflow quotidien (GitHub Pages) et
// répond aux fiches produit qui demandent « que sait-on de cette URL ? ».
// La recherche se fait en local : on télécharge le fichier entier, aucune
// donnée de navigation n'est envoyée au site.
//
// Seule exception, à la demande explicite de l'utilisateur : « Demander à
// l'IA » envoie la question, les filtres, les offres candidates et la liste
// des produits consultés au serveur LLM configuré dans les options.
// Firefox expose `browser` (promesses garanties) ; Chrome seulement `chrome`.
const ext = globalThis.browser ?? globalThis.chrome;
const SITE_URL = 'https://aurel456.github.io/whey_price_tracker/';
const API_URL = SITE_URL + 'api/v1/prices.json';
const TTL_MS = 60 * 60 * 1000; // les prix ne bougent qu'une fois par jour

// Valeurs par défaut du LLM (API compatible OpenAI). La clé n'a PAS de valeur
// par défaut : l'utilisateur la saisit dans les options, elle reste dans le
// stockage local du navigateur.
const LLM_DEFAULTS = { url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' };

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
  if (!product) return { product: null, siteUrl: SITE_URL };
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

async function llmSettings() {
  const { llm } = await ext.storage.local.get('llm');
  return { ...LLM_DEFAULTS, ...(llm || {}) };
}

async function llmFetch(path, init = {}) {
  const cfg = await llmSettings();
  if (!cfg.key) return { error: 'NO_KEY' };
  const res = await fetch(cfg.url.replace(/\/+$/, '') + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + cfg.key },
  });
  if (!res.ok) {
    const body = (await res.text()).slice(0, 300);
    return { error: `HTTP ${res.status} — ${body}` };
  }
  return { json: await res.json(), cfg };
}

const SYSTEM_PROMPT = [
  'Tu es le conseiller de « Vrai prix », un comparatif indépendant de nutrition sportive.',
  'Tu reçois des offres RÉELLES relevées aujourd\'hui (prix, taille, coût réel par kg de protéine,',
  'par g d\'EPA+DHA ou par kg de créatine, classement, plus bas sur 90 jours) et la liste des',
  'produits que l\'utilisateur a consultés.',
  'Règles : ne recommande QUE des offres présentes dans les données, n\'invente aucun prix ni',
  'produit. Compare au coût réel, pas au prix du pot. Cite le nom, le site, la taille, le prix',
  'et le coût réel. Signale si un prix est au plus bas. Si rien ne convient, dis-le.',
  'Pas de conseil médical. Réponds en français, en 150 mots maximum, avec une liste courte.',
].join(' ');

async function ask({ question, context }) {
  const r = await llmFetch('/chat/completions', {
    method: 'POST',
    body: JSON.stringify({
      model: (await llmSettings()).model,
      temperature: 0.3,
      max_tokens: 700,
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: `Données (JSON) :\n${JSON.stringify(context)}\n\nQuestion : ${question}` },
      ],
    }),
  });
  if (r.error) return r;
  return { answer: r.json.choices?.[0]?.message?.content?.trim() || '(réponse vide)', model: r.cfg.model };
}

async function listModels() {
  const r = await llmFetch('/models');
  if (r.error) return r;
  return { models: (r.json.data || []).map((m) => m.id).sort() };
}

const HANDLERS = {
  lookup: (msg) => lookup(msg.url),
  data: () => getData(),
  ask: (msg) => ask(msg),
  models: () => listModels(),
  openOptions: () => ext.runtime.openOptionsPage(),
};

ext.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handler = msg && HANDLERS[msg.type];
  if (!handler) return false;
  Promise.resolve(handler(msg)).then(sendResponse, (err) => sendResponse({ error: String(err) }));
  return true; // réponse asynchrone
});

ext.action.onClicked.addListener(() => ext.tabs.create({ url: SITE_URL }));
