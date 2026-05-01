# HSN Whey Price Tracker

Scrape les prix des whey protéines sur [HSNstore.fr](https://www.hsnstore.fr) et génère un suivi dans un fichier Excel + un dashboard HTML.

## Fonctionnement

1. Parcourt les catégories de whey et protéines pour collecter les URLs produits
2. Visite chaque produit, récupère les déclinaisons (tailles), prix, portions, DDM
3. Ajoute les données du jour dans `whey_prices.xlsx` (feuille "Historique")
4. Génère `whey_dashboard.html` avec graphiques Chart.js (prix/kg, coût/portion)

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

| Fichier | Rôle |
| ------ | --- |
| `hsn_tracker.py` | Script principal (scraping + Excel + dashboard) |
| `whey_prices.xlsx` | Historique des prix (créé automatiquement) |
| `whey_dashboard.html` | Dashboard de visualisation (généré automatiquement) |
| `descriptions.json` | Descriptions courtes / mots-clés des produits (optionnel) |

## Dashboard

Le dashboard HTML affiche :

- Cartes récapitulatives (nombre de produits, meilleur prix/kg, meilleur coût/portion)
- Filtre par taille (500g, 750g, 2Kg, etc.)
- Graphiques barres : prix au kilo et coût par portion
- Tableau détaillé de toutes les entrées

## Configuration

Les URLs des catégories et des produits supplémentaires sont définies directement dans le script (`CATEGORY_URLS` et `EXTRA_URLS`). Ajustez-les si la structure du site HSN change.
