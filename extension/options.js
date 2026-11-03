const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const DEFAULT_URL = 'https://api.groq.com/openai/v1';

function status(text, cls = '') {
  $('status').textContent = text;
  $('status').className = cls;
}

async function load() {
  const { llm } = await ext.storage.local.get('llm');
  $('url').value = llm?.url || DEFAULT_URL;
  $('model').value = llm?.model || '';
  // La clé n'est jamais réaffichée en clair : on indique seulement sa présence.
  $('key').placeholder = llm?.key ? 'Clé enregistrée — laisser vide pour la conserver' : 'gsk_…';
}

// Un serveur autre que Groq demande une permission d'hôte supplémentaire
// (déclarée en optionnelle dans le manifeste), accordée au clic.
async function ensureHostPermission(url) {
  const origin = new URL(url).origin + '/*';
  if (origin === 'https://api.groq.com/*') return true;
  return ext.permissions.request({ origins: [origin] });
}

async function save() {
  const url = $('url').value.trim() || DEFAULT_URL;
  try { new URL(url); } catch { return status('URL invalide.', 'err'); }
  if (!(await ensureHostPermission(url))) return status('Permission refusée pour ce serveur.', 'err');
  const { llm } = await ext.storage.local.get('llm');
  const next = { url, model: $('model').value.trim(), key: $('key').value.trim() || llm?.key || '' };
  await ext.storage.local.set({ llm: next });
  $('key').value = '';
  await load();
  status(next.key ? 'Enregistré.' : 'Enregistré, mais aucune clé : l\'assistant IA restera désactivé.', next.key ? 'ok' : 'err');
}

async function test() {
  await save();
  status('Test en cours…');
  const r = await ext.runtime.sendMessage({ type: 'models' });
  if (r.error === 'NO_KEY') return status('Aucune clé enregistrée.', 'err');
  if (r.error) return status('Échec : ' + r.error, 'err');
  $('models').innerHTML = r.models.map((m) => `<option value="${m.replace(/"/g, '&quot;')}">`).join('');
  status(`Connexion OK — ${r.models.length} modèles disponibles (liste proposée dans le champ Modèle).`, 'ok');
}

async function forget() {
  const { llm } = await ext.storage.local.get('llm');
  await ext.storage.local.set({ llm: { ...(llm || {}), key: '' } });
  await load();
  status('Clé effacée.', 'ok');
}

$('save').addEventListener('click', save);
$('test').addEventListener('click', test);
$('forget').addEventListener('click', forget);
load();
