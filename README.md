# HSN Whey Price Tracker

Scrape les prix des whey protéines sur [HSNstore.fr](https://www.hsnstore.fr) et génère un suivi dans un fichier Excel + un dashboard HTML.

Repo : [github.com/Aurel456/whey_price_tracker](https://github.com/Aurel456/whey_price_tracker)

## Fonctionnement

1. Parcourt les catégories de whey et protéines pour collecter les URLs produits
2. Visite chaque produit en parallèle (4 simultanés), récupère :
   - Déclinaisons (tailles), prix, portions, DDM
   - Valeurs nutritionnelles pour 100g (protéines, énergie, glucides, lipides, sel)
   - Profil des 18 acides aminés (mg/100g)
   - Liste d'ingrédients
3. Calcule le **prix par kilo de protéine pure** et le **coût pour 30g de protéine** (le ratio protéine/produit varie de ~70% à ~90%)
4. Ajoute les données du jour dans `whey_prices.xlsx` (feuille "Historique")
5. Génère `whey_dashboard.html` avec 3 graphiques Chart.js : €/kg protéine, coût/30g protéine, €/kg produit

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
| `whey_prices.xlsx` | Historique des prix (créé automatiquement) | ✗ (gitignore) |
| `whey_dashboard.html` | Dashboard de visualisation (généré automatiquement) | ✗ (gitignore) |
| `descriptions.json` | Descriptions courtes / mots-clés des produits (optionnel) | ✓ |

## Dashboard

Le dashboard HTML affiche :

- Cartes récapitulatives : nombre de produits, **meilleur €/kg protéine**, meilleur coût pour 30g de protéine, meilleur €/kg produit
- Filtre par taille (500g, 750g, 2Kg, etc.)
- 3 graphiques barres : prix par kg de **protéine pure**, coût pour 30g de protéine, prix par kg de produit
- Tableau détaillé avec %protéine, €/kg prot, €/30g prot
- Déduplication automatique : si le même produit a été scrapé plusieurs fois, seule la dernière entrée est affichée

## Configuration

Les URLs des catégories et des produits supplémentaires sont définies directement dans le script (`CATEGORY_URLS` et `EXTRA_URLS`). Ajustez-les si la structure du site HSN change.

Paramètres ajustables en haut du script :

| Variable | Défaut | Effet |
| ------ | --- | --- |
| `CONCURRENCY` | `4` | Nombre de produits scrapés en parallèle. Baisser à 2-3 si erreurs/timeouts. |
| `CLICK_WAIT` | `700` | Délai (ms) après clic sur une taille. À augmenter si certaines tailles renvoient des données vides. |
| `PAGE_TIMEOUT` | `30000` | Timeout (ms) de chargement d'une page. |

## Versioning (git)

Le projet est suivi sur GitHub. Pour publier des modifications :

```bash
git add hsn_tracker.py README.md       # ou les fichiers modifiés
git commit -m "description du changement"
git push
```

`whey_prices.xlsx` et `whey_dashboard.html` sont **versionnés** : chaque mise à jour de prix produit un commit, ce qui crée un historique git complet de l'évolution des prix. Les fichiers ignorés par git : `.venv/`, `errors.log`, `.kilo/`, `__pycache__/`.

## Automatisation cloud (GitHub Actions)

Un workflow [.github/workflows/track-prices.yml](.github/workflows/track-prices.yml) tourne tous les jours à 9h UTC sur les serveurs GitHub. Il :

1. Installe Python + Playwright Chromium (avec cache pour aller plus vite)
2. Lance `hsn_tracker.py`
3. Commit + push les nouveaux prix dans le repo

**Avantages** : tracking continu même PC éteint, et chaque jour de prix devient un commit visible dans l'historique git. Gratuit jusqu'à 2000 minutes/mois.

**Déclenchement manuel** : onglet *Actions* sur GitHub → *Daily price tracking* → *Run workflow*.

## Robustesse

- Retry automatique (1 retentative) sur produit qui timeout ou renvoie un résultat vide
- Sanity check : un whey doit avoir entre 50% et 95% de protéines, sinon l'erreur est loggée dans `errors.log`
- Logs détaillés des échecs dans `errors.log` (timestamp + URL + raison)
