# HSN Price Tracker

Scrape les prix des produits de nutrition sportive sur [HSNstore.fr](https://www.hsnstore.fr) et génère un suivi dans un fichier Excel + un dashboard HTML. Couvre **whey & protéines**, **oméga-3** et **créatine** avec des métriques de coût adaptées à chaque type.

Repo : [github.com/Aurel456/whey_price_tracker](https://github.com/Aurel456/whey_price_tracker)

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
6. Ajoute les données du jour dans `whey_prices.xlsx` (feuille "Historique") puis génère `whey_dashboard.html` (onglets Whey / Oméga-3 / Créatine, graphiques Chart.js, tendances, badge deal)

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
| `tags.json` | Labels persos + notes par produit (édité depuis le dashboard) | ✓ |

`whey_prices.xlsx` et `whey_dashboard.html` sont versionnés : chaque mise à jour de prix produit un commit, créant un historique git complet de l'évolution des prix.

## Dashboard

Le dashboard HTML affiche :

- **Onglets en haut** : *Whey & protéines*, *Oméga-3*, *Créatine*. Le compteur entre parenthèses indique le nombre de produits chargés ; les onglets vides sont grisés et invitent à lancer le scraper. Chaque onglet a son propre jeu de colonnes, métriques et filtres.
- **Cartes récapitulatives** adaptées à l'onglet : meilleur €/kg protéine sur whey, meilleur €/g EPA+DHA sur oméga-3, meilleur €/kg créatine sur créatine.
- **Filtres** : taille, plus :
  - *Whey* : édulcorants (Sans édulcorant, Stévia, Sucralose, Acésulfame-K, Aspartame), type protéine (Isolat CFM, Hydrolysat, Concentré, Native, Caséine, Végétal, Isolat).
  - *Oméga-3* : forme (Triglycéride TG, Ester éthylique EE, IFOS certifié).
  - *Créatine* : type (Creapure®, Monohydrate, 100/200/500 mesh).
- **Bascule ET / OU** par groupe de filtres : chaque rangée à puces a un chip "OU/ET" en tête pour combiner les choix en intersection ou en union (par défaut OU). Les groupes restent en ET entre eux.
- **Filtres labels persos** : chaque label saisi dans le dashboard devient un filtre cliquable, avec le même toggle ET/OU.
- **Recherche** par nom de produit (et note libre) et **tri** par n'importe quelle colonne. La sort key par défaut s'adapte au tab.
- **Graphiques** : 3 barres sur whey (€/kg prot, €/30g prot, €/kg produit), 1 barre sur oméga-3 (€/g EPA+DHA) et créatine (€/kg créatine). Tendance historique sur whey uniquement.
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

Au prochain `python hsn_tracker.py`, les valeurs de `tags.json` sont relues et injectées dans le dashboard.

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
