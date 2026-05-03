# HSN Whey Price Tracker

Scrape les prix des whey protéines et produits protéinés sur [HSNstore.fr](https://www.hsnstore.fr) et génère un suivi dans un fichier Excel + un dashboard HTML.

Repo : [github.com/Aurel456/whey_price_tracker](https://github.com/Aurel456/whey_price_tracker)

## Fonctionnement

1. Parcourt les catégories de whey et protéines pour collecter les URLs produits
2. Visite chaque produit en parallèle (4 simultanés), récupère :
   - Déclinaisons (tailles), prix, portions, DDM
   - Valeurs nutritionnelles pour 100g (protéines, énergie, glucides, lipides, sel)
   - Profil des 18 acides aminés (mg/100g)
   - Liste d'ingrédients
3. Calcule le **prix par kilo de protéine pure**, le **coût pour 30g de protéine**, et le **coût pour 3g de leucine** (score qualité)
4. Classe chaque produit en **catégorie** selon son taux de protéines : Whey (≥70%), Aliments enrichis (30–70%), Autres (<30%)
5. Ajoute les données du jour dans `whey_prices.xlsx` (feuille "Historique")
6. Génère `whey_dashboard.html` avec graphiques Chart.js, tendances historiques, et badge deal

Les variantes "Monodose" et "Pack" sont automatiquement exclues.

## Utilisation

```bash
# Installation (avec uv)
uv venv
uv pip install -r requirements.txt
playwright install chromium

# Activation puis lancement
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\activate       # Windows
python hsn_tracker.py
```

## Fichiers

| Fichier | Rôle | Versionné |
| ------ | --- | --- |
| `hsn_tracker.py` | Script principal (scraping + Excel + dashboard) | ✓ |
| `requirements.txt` | Dépendances Python | ✓ |
| `whey_prices.xlsx` | Historique des prix (créé automatiquement) | ✓ |
| `whey_dashboard.html` | Dashboard de visualisation (généré automatiquement) | ✓ |
| `descriptions.json` | Descriptions courtes / mots-clés des produits (optionnel) | ✓ |

`whey_prices.xlsx` et `whey_dashboard.html` sont versionnés : chaque mise à jour de prix produit un commit, créant un historique git complet de l'évolution des prix.

## Dashboard

Le dashboard HTML affiche :

- **Cartes récapitulatives** : nombre de produits, meilleur €/kg protéine, meilleur coût pour 30g de protéine, meilleur €/kg produit
- **Filtres** par catégorie (Whey / Aliments enrichis / Autres) et par taille (500g, 750g, 2Kg, etc.)
- **Recherche** par nom de produit et **tri** par prix, protéines, ou score leucine
- **3 graphiques barres** : prix par kg de protéine pure, coût pour 30g de protéine, prix par kg de produit
- **Graphique de tendance** historique par produit (ligne Chart.js)
- **Badge Deal** : signale les produits dont le prix actuel est inférieur à leur moyenne historique (-5%)
- **Tableau détaillé** : %protéine, €/kg prot, €/30g prot, coût/3g leucine, catégorie
- **Déduplication automatique** : si le même produit a été scrapé plusieurs fois, seule la dernière entrée est affichée

## Configuration

Les URLs des catégories et des produits supplémentaires sont définies directement dans le script (`CATEGORY_URLS` et `EXTRA_URLS`). Ajustez-les si la structure du site HSN change.

Paramètres ajustables en haut du script :

| Variable | Défaut | Effet |
| ------ | --- | --- |
| `CONCURRENCY` | `4` | Nombre de produits scrapés en parallèle. Baisser à 2-3 si erreurs/timeouts. |
| `CLICK_WAIT` | `700` | Délai (ms) après sélection d'une taille. À augmenter si certaines tailles renvoient des données vides. |
| `PAGE_TIMEOUT` | `30000` | Timeout (ms) de chargement d'une page. |
| `RETRY_ATTEMPTS` | `1` | Nombre de retentatives si un produit échoue (timeout ou résultat vide). |
| `PROT_MIN_PCT` | `30.0` | Seuil bas du sanity check (%). En dessous : erreur loggée. |
| `PROT_MAX_PCT` | `95.0` | Seuil haut du sanity check (%). Au-dessus : erreur loggée. |

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

## Automatisation cloud (GitHub Actions)

Un workflow [.github/workflows/track-prices.yml](.github/workflows/track-prices.yml) tourne tous les jours à 9h UTC sur les serveurs GitHub. Il :

1. Installe Python + Playwright Chromium (avec cache pour aller plus vite)
2. Lance `hsn_tracker.py`
3. Commit + push les nouveaux prix dans le repo

**Avantages** : tracking continu même PC éteint, et chaque jour de prix devient un commit visible dans l'historique git. Gratuit jusqu'à 2000 minutes/mois.

**Déclenchement manuel** : onglet *Actions* sur GitHub → *Daily price tracking* → *Run workflow*.

## Robustesse

- Retry automatique (`RETRY_ATTEMPTS=1`) sur produit qui timeout ou renvoie un résultat vide
- Sanity check : un produit doit avoir entre 30% et 95% de protéines, sinon l'erreur est loggée dans `errors.log`
- Logs détaillés des échecs dans `errors.log` (timestamp + URL + raison)
- Blocage des ressources inutiles (images, polices, CSS) pour accélérer le scraping
