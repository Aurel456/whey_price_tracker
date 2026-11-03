# HSN Price Tracker

Relève chaque jour les prix de la nutrition sportive sur [HSNstore.fr](https://www.hsnstore.fr) et [MyProtein](https://fr.myprotein.com), calcule le **coût réel** (€/kg de protéine, €/g d'EPA+DHA, €/kg de créatine) et publie un site comparatif, des dashboards et une API utilisée par une extension navigateur. Couvre **whey & protéines**, **oméga-3** et **créatine**.

- Site : [aurel456.github.io/whey_price_tracker](https://aurel456.github.io/whey_price_tracker/)
- Repo : [github.com/Aurel456/whey_price_tracker](https://github.com/Aurel456/whey_price_tracker)

```text
GitHub Actions (9h UTC)
  hsn_tracker.py ─┐                       ┌─ docs/index.html          accueil comparatif
                  ├─► data/*.csv ─► build.py ─┼─ docs/{hsn,myprotein}.html  recommandations
  myprotein_…py ──┘   (versionné)          ├─ docs/*dashboard.html     dashboards
                                          └─ docs/api/v1/prices.json  ─► extension/
```

## Fonctionnement

1. Parcourt les catégories de whey et protéines pour collecter les URLs produits, plus les listes fixes `OMEGA3_URLS` et `CREATINE_URLS`
2. Visite chaque produit en parallèle (4 simultanés), récupère :
   - Déclinaisons (tailles ou nombre de capsules), prix, portions, DDM
   - Valeurs nutritionnelles (whey : per 100g ; oméga/créatine : per dose ou capsule)
   - Profil des 18 acides aminés (whey)
   - **EPA / DHA en mg par capsule** (oméga-3)
   - **Quantité de créatine par dose** (créatine)
   - Liste d'ingrédients
3. Détecte le **type de produit** depuis l'URL (`whey` / `omega3` / `creatine`) et calcule des métriques de coût adaptées :
   - **Whey** : €/kg de protéine pure, €/30g protéine, €/3g leucine (seuil anabolique)
   - **Oméga-3** : €/g d'EPA+DHA combinés (basé sur capsules × mg/cap)
   - **Créatine** : €/kg de créatine pure (≈ €/kg produit, monohydrate à ~99 %)
4. Classe les whey en **catégorie** selon leur taux de protéines : Whey (≥70%), Aliments enrichis (30–70%), Autres (<30%)
5. Détecte automatiquement depuis le nom + ingrédients :
   - **Édulcorants** : sans édulcorant, stévia, sucralose, acésulfame-K, aspartame
   - **Type whey** : isolat CFM, hydrolysat, concentré, native, caséine, végétal, mix
   - **Type oméga** : forme TG / EE, certification IFOS
   - **Type créatine** : Creapure®, monohydrate, mesh 100/200/500
6. Ajoute les données du jour dans `data/hsn_prices.csv` (+ les fiches produit dans `data/hsn_products.csv`) puis génère `docs/dashboard.html` (onglets Whey / Oméga-3 / Créatine, graphiques Chart.js, tendances, badge deal)

Les variantes "Monodose" et "Pack" sont automatiquement exclues.

## Utilisation

```bash
# Installation (env conda du projet, cf. AGENTS.md)
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium

python hsn_tracker.py         # relevé HSN       → data/hsn_*.csv
python myprotein_tracker.py   # relevé MyProtein → data/myprotein_*.csv
python build.py               # régénère tout docs/ depuis data/ (sans scraper)
python build.py --health      # bilan du relevé du jour (exit 1 si anomalie)
python build.py --excel       # exporte l'historique en .xlsx dans exports/
python -m pytest              # tests (parsing, stockage, API)
```

Ouvrir `docs/index.html` en local fonctionne tel quel (liens relatifs).

## Fichiers

| Fichier | Rôle | Versionné |
| ------ | --- | --- |
| `hsn_tracker.py` | Scraping HSN + couche partagée (stockage, dashboard, reco, comparatif, API) | ✓ |
| `myprotein_tracker.py` | Scraping MyProtein | ✓ |
| `build.py` | Régénère le site complet, bilan de santé, export Excel | ✓ |
| `data/<site>_prices.csv` | Historique des prix : une ligne par (jour, produit, taille) | ✓ |
| `data/<site>_products.csv` | Fiche produit par URL (ingrédients, profil AA…) | ✓ |
| `extension/` | Extension navigateur « Vrai prix » | ✓ |
| `tests/` | Tests pytest | ✓ |
| `docs/` | Site généré (accueil, recos, dashboards, `api/v1/prices.json`) | ✗ (publié par la CI) |
| `exports/` | Exports `.xlsx` à la demande | ✗ |
| `descriptions.json` | Descriptions courtes / mots-clés des produits (optionnel) | ✓ |
| `tags.json` | Labels persos + notes par produit (édité depuis le dashboard) | ✓ |

Seul `data/` est versionné : chaque relevé ajoute quelques Ko de lignes CSV, donc l'historique git reste léger (avant 2026-11, l'Excel binaire et les pages HTML de 2 Mo étaient recommittés chaque jour, soit environ 1 Mo de plus par jour pour le dépôt).

## Automatisation (GitHub Actions)

`.github/workflows/track-prices.yml`, tous les jours à 9h UTC :

1. lance les deux trackers en parallèle ;
2. `python build.py` reconstruit `docs/` ;
3. committe `data/` ;
4. publie `docs/` sur GitHub Pages (source « GitHub Actions » dans Settings → Pages) ;
5. **fait échouer le run, et donc t'envoie un email**, si un tracker a planté, si un site n'a rien relevé ou s'il a relevé moins de 75 % de sa médiane sur 7 jours. Le bilan s'affiche en tête du run.

Un push qui modifie le code de génération republie le site sans re-scraper. `tests.yml` lance pytest à chaque push.

## Extension navigateur « Vrai prix »

Une bulle **déplaçable** (glisser par l'en-tête ou la pastille, position mémorisée) avec trois onglets :

- **Ce produit** (fiche HSN ou MyProtein suivie) :
  - **« Ta sélection »** suit la taille et le prix affichés sur la page, et recalcule en direct le coût réel et le classement ;
  - pour chaque taille : coût réel, **classement** parmi les références comparables tous sites confondus, prix comparé à son **plus bas sur 90 jours** ;
  - la **référence la moins chère de la même famille**. Une whey CFM est comparée aux CFM, jamais à un isolat de soja.
- **Mes produits** : les fiches suivies que tu as ouvertes, d'une page et d'un onglet à l'autre, avec la sélection vue et le meilleur coût réel du jour. Classées au coût réel quand on filtre par type.
- **Conseiller** : les meilleures offres au coût réel selon tes filtres (type, site, gamme, sans édulcorant, en stock, budget par pot), et **« Demander à l'IA »**.

L'extension lit `api/v1/prices.json` sur GitHub Pages (mis en cache 1 h). La mémoire reste dans le stockage local du navigateur. Rien n'est envoyé ailleurs, **sauf** quand tu cliques sur « Demander à l'IA » : la question, les filtres, les offres candidates et les produits vus partent alors au serveur LLM configuré.

**Assistant IA** : clic droit sur l'icône → *Options* (ou `about:addons` → Vrai prix → Options). Colle ta clé Groq (ou celle de tout serveur compatible OpenAI), puis *Tester* pour lister les modèles. La clé reste dans le navigateur. **Ne jamais la mettre dans le code** : `.env` est ignoré par git, et une clé placée dans l'extension ou sur le site serait lisible par tous.

Installation en local :

- **Chrome / Edge / Brave** : `chrome://extensions` → activer le *Mode développeur* → *Charger l'extension non empaquetée* → choisir le dossier `extension/`.
- **Firefox** : `about:debugging#/runtime/this-firefox` → *Charger un module complémentaire temporaire* → choisir `extension/manifest.json`.

Le format de l'API est versionné (`v1`) : ne renommer aucun champ sans publier un `v2/` à côté, car les extensions installées ne se mettent pas à jour en même temps que le site.

## Dashboard

Le dashboard HTML affiche :

- **Onglets en haut** : *Whey & protéines*, *Oméga-3*, *Créatine*, *Global*. Le compteur entre parenthèses indique le nombre de produits chargés ; les onglets vides sont grisés et invitent à lancer le scraper. Chaque onglet a son propre jeu de colonnes, métriques et filtres. L'onglet *Global* agrège les 3 types avec une colonne Type (badge coloré) et ses propres cartes consolidées (nb DEALs / Ruptures / top de chaque catégorie).
- **Cartes récapitulatives** adaptées à l'onglet : meilleur €/kg protéine sur whey, meilleur €/g EPA+DHA sur oméga-3, meilleur €/kg créatine sur créatine.
- **Filtre Catégorie** (whey uniquement) : *Whey ≥70%* / *Aliments enrichis 30–70%* / *Autres <30%*. N'apparaît pas sur les autres onglets.
- **Filtres** : taille, plus :
  - *Whey* : édulcorants (Sans édulcorant, Stévia, Sucralose, Acésulfame-K, Aspartame), type protéine (Isolat CFM, Hydrolysat, Concentré, Native, Caséine, Végétal, Isolat).
  - *Oméga-3* : forme (Triglycéride TG, Ester éthylique EE, IFOS certifié).
  - *Créatine* : type (Creapure®, Monohydrate, 100/200/500 mesh).
- **Bascule ET / OU** par groupe de filtres : chaque rangée à puces a un chip "OU/ET" en tête pour combiner les choix en intersection ou en union (par défaut OU). Les groupes restent en ET entre eux.
- **Filtres labels persos** : chaque label saisi dans le dashboard devient un filtre cliquable, avec le même toggle ET/OU.
- **Recherche** par nom de produit (et note libre) et **tri** par n'importe quelle colonne. La sort key par défaut s'adapte au tab.
- **Graphiques** : 3 barres sur whey (€/kg prot, €/30g prot, €/kg produit), 1 barre sur oméga-3 (€/g EPA+DHA) et créatine (€/kg créatine). Sur *Global*, le bar chart principal est masqué (mélange non pertinent).
- **Tendance historique adaptative** : la métrique du graphique d'évolution s'adapte au tab — €/kg protéine (whey), €/g EPA+DHA (oméga), €/kg créatine (créatine), prix nominal (global). Le bouton **🔥 Tous les DEALs** sélectionne automatiquement toutes les courbes flagées DEAL du tab courant (limité à 8).
- **Colonne `g EPA+DHA / pack`** sur l'onglet oméga-3 : quantité totale d'EPA+DHA fournie par le pack (capsules × (EPA+DHA mg/cap) / 1000).
- **Badge Deal** : signale les produits dont le prix actuel est inférieur à leur moyenne historique (-5%).
- **Édition de tags & note** par produit (✏️ sur chaque ligne) : labels persos + note libre, stockés en `localStorage` puis exportables vers `tags.json` via le bouton 📥.
- **Pill ❓ "à classer"** sur les whey sans ingrédients capturés ou sans tag détecté.

## tags.json (annotations utilisateur)

Le dashboard offre un éditeur (✏️ par ligne) pour ajouter des **labels persos** ("favori", "à tester", …) et une **note libre** (goût, observations).

Les modifs sont conservées en `localStorage` côté navigateur (pas de backend). Le bouton **📥 Exporter tags.json** télécharge le fichier fusionné — copie-le à la racine du projet pour le versionner.

Format :

```json
{
  "EVOLATE 2.0 (WHEY ISOLATE CFM)": {
    "labels": ["favori", "isolat-pur"],
    "note": "Goût neutre, ratio top"
  }
}
```

Au prochain `python build.py`, les valeurs de `tags.json` sont relues et injectées dans le dashboard.

## Configuration

Les URLs sont définies directement en haut du script :

| Liste | Type | Rôle |
| ----- | ---- | ---- |
| `CATEGORY_URLS` | whey | Pages catégories à parcourir pour collecter les URLs produits |
| `EXTRA_URLS` | whey | Produits whey à toujours scraper même si non listés en catégorie |
| `OMEGA3_URLS` | oméga-3 | Produits oméga-3 (capsules) — métriques EPA/DHA |
| `CREATINE_URLS` | créatine | Produits créatine (poudre) — métrique €/kg de créatine |

Ajustez-les si la structure du site HSN change ou pour ajouter de nouveaux suivis.

Paramètres ajustables en haut du script :

| Variable | Défaut | Effet |
| ------ | --- | --- |
| `CONCURRENCY` | `4` | Nombre de produits scrapés en parallèle. Baisser à 2-3 si erreurs/timeouts. |
| `CLICK_WAIT` | `700` | Délai (ms) après sélection d'une taille. À augmenter si certaines tailles renvoient des données vides. |
| `PAGE_TIMEOUT` | `30000` | Timeout (ms) de chargement d'une page. |
| `RETRY_ATTEMPTS` | `1` | Nombre de retentatives si un produit échoue (timeout ou résultat vide). |
| `PROT_MIN_PCT` | `5.0` | Borne basse du sanity check (%). Détecte uniquement les erreurs de parsing (cellule vide, mauvaise unité). NE filtre PAS par catégorie — les Aliments enrichis 30-70% sont légitimes. |
| `PROT_MAX_PCT` | `95.0` | Borne haute du sanity check (%). Au-dessus : erreur loggée. |

## Suivi MyProtein (second site)

En plus de HSN, le projet suit [fr.myprotein.com](https://fr.myprotein.com) via un
script séparé `myprotein_tracker.py`. Il **réutilise toute la couche stockage +
dashboard + recommandations** de `hsn_tracker.py` (via un `SiteConfig` dédié), mais
avec un scraping spécifique à la plateforme THG de MyProtein et des **fichiers de
sortie séparés** :

| Fichier | Rôle |
| ------ | --- |
| `myprotein_tracker.py` | Script MyProtein (scraping + dashboard) |
| `data/myprotein_*.csv` | Historique des prix + fiches produit MyProtein |
| `docs/myprotein-dashboard.html` | Dashboard MyProtein |
| `docs/myprotein.html` | Page recommandations MyProtein |

```bash
python myprotein_tracker.py
```

Particularités :

- **Shortlist de produits clés** définie en haut du script (`PRODUCTS`, avec leur
  type whey/creatine/omega3), extensible. Pas de crawl de catégorie.
- Tailles, prix et **stock** viennent de la `ld+json` `ProductGroup` de la page
  (pas du DOM). Whey : regroupé par **portions** ; créatine : par **poids** ;
  oméga : par **nb de gélules** (cf. AGENTS.md pour le pourquoi).
- MyProtein **ne publie pas de profil d'acides aminés** → la colonne €/3g leucine
  reste vide (les métriques €/kg protéine et €/30g protéine fonctionnent).
- Le workflow GitHub Actions lance les deux trackers en parallèle puis
  `build.py`, qui régénère l'accueil (comparatif multi-sites) et l'API. C'est le
  seul module qui connaît tous les `SiteConfig`, donc après un tracker lancé
  seul en local, il faut relancer `python build.py` pour mettre l'accueil à jour.

## Catégories

Les produits sont automatiquement classés selon leur taux de protéines pour 100g :

| Catégorie | Taux de protéines |
| ------ | --- |
| Whey | ≥ 70% |
| Aliments enrichis | 30% – 70% (crème de riz, flocons d'avoine protéinés, etc.) |
| Autres | < 30% |

## Versioning (git)

Le projet est suivi sur GitHub. Pour publier des modifications :

```bash
git add hsn_tracker.py README.md       # ou les fichiers modifiés
git commit -m "description du changement"
git push
```

### Filtrer les daily commits du `git log`

Le workflow GitHub Actions ajoute un commit `Daily price update — YYYY-MM-DD` chaque jour, ce qui noie les vrais commits "code" dans le log. Un alias git local filtre ces commits automatiques :

```bash
git config --local alias.dev-log "log --invert-grep --grep=^Daily --oneline"
git dev-log -20   # → uniquement les commits "code", sans les daily updates
```

L'alias est local au repo (déjà installé sur ta machine). À refaire après un clone.

## Automatisation cloud (GitHub Actions)

Un workflow [.github/workflows/track-prices.yml](.github/workflows/track-prices.yml) tourne tous les jours à 9h UTC sur les serveurs GitHub. Il :

1. Installe Python + Playwright Chromium (avec cache pour aller plus vite)
2. Lance `hsn_tracker.py`
3. Commit + push les nouveaux prix dans le repo

**Avantages** : tracking continu même PC éteint, et chaque jour de prix devient un commit visible dans l'historique git. Gratuit jusqu'à 2000 minutes/mois.

**Déclenchement manuel** : onglet *Actions* sur GitHub → *Daily price tracking* → *Run workflow*.

## Partage public (GitHub Pages)

Les pages HTML générées sont autonomes (Chart.js via CDN, données injectées, aucun backend). Sur GitHub Pages, **la page d'accueil est le comparatif multi-sites** ; les pages par site sont un cran plus loin, et les dashboards techniques encore après :

- **`docs/index.html`** *(accueil)* — le **comparatif HSN vs MyProtein** : pour chaque catégorie (whey, oméga-3, créatine), la meilleure offre de chaque site face à face sur la même métrique de coût réel, l'écart chiffré entre sites, les constats de l'étude et la méthodologie. C'est la page à partager. Généré par `generate_comparatif()`, appelé en fin de `myprotein_tracker.py` (seul module qui connaît les deux sites).
- **`docs/hsn.html`** / **`docs/myprotein.html`** — la page par site avec le **recommandeur interactif** : le visiteur choisit ses critères (gamme whey *Vegan / Basique / Supérieure*, *sans édulcorant* ; oméga *concentration EPA+DHA ≥ X%* + *IFOS* + *forme TG* ; créatine *Creapure® / Monohydrate*) et obtient en direct le meilleur rapport qualité-prix qui correspond + des alternatives. Plus les bons plans du jour et un guide « comment bien choisir ».
- **`docs/dashboard.html`** / **`docs/myprotein-dashboard.html`** — le dashboard technique complet (tableaux, filtres, graphiques), accessible via le bouton « Explorer toutes les données ».

  > Pour les oméga-3, le critère qualité est **IFOS + concentration ≥ 50 %** (et non un TOTOX chiffré : celui-ci vit dans les rapports IFOS par lot, pas sur les pages produit). IFOS certifie l'oxydation/TOTOX et les contaminants lot par lot — c'est le proxy fiable. Avec ces défauts, la reco sort *ULTRA OMEGA-3 TG (IFOS)* plutôt que l'huile de poisson basique (30 % de concentration).

`generate_dashboard()` (appelé à chaque scrape) régénère dashboard + reco du site ; `generate_comparatif()` régénère l'accueil. **Toutes les pages vivent dans `docs/`** — il n'y a plus de copie à la racine, les liens entre pages étant relatifs, ouvrir `docs/index.html` en local fonctionne tel quel.

**Activation (déjà faite)** : repo GitHub → *Settings* → *Pages* → *Source* = `Deploy from a branch`, *Branch* = `main`, dossier = `/docs` → *Save*. Le site est en ligne à `https://aurel456.github.io/whey_price_tracker/` (tombe sur le comparatif).

**Mise à jour** : le workflow quotidien commit `docs/index.html` en même temps que les prix → le site public se rafraîchit tout seul chaque jour. Pas de manip.

**À savoir** :

- Le repo doit être **public** (GitHub Pages gratuit). Le code du scraper devient donc visible.
- Les **tags/notes persos** sont injectés au moment de la génération (depuis `tags.json`) : les visiteurs voient tes annotations mais leurs propres édits restent dans leur navigateur (`localStorage`), sans impact sur ta version.
- Il n'y a plus de copie HTML à la racine du repo : `docs/` est la seule source, ouvrable aussi bien en local que servie par Pages.

## Robustesse

- Retry automatique (`RETRY_ATTEMPTS=1`) sur produit qui timeout ou renvoie un résultat vide
- Sanity check : un produit whey doit avoir entre 5% et 95% de protéines (bornes larges pour ne détecter que les erreurs de parsing — la catégorisation Whey/Aliments enrichis/Autres est gérée séparément), sinon l'erreur est loggée dans `errors.log`
- Logs détaillés des échecs dans `errors.log` (timestamp + URL + raison)
- Blocage des ressources inutiles (images, polices, CSS) pour accélérer le scraping
