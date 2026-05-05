# AGENTS.md — Notes pour agents IA travaillant sur ce projet

Notes consolidées des erreurs et apprentissages rencontrés lors du dev. À lire avant de toucher au code.

## Environnement Python

- **Toujours** utiliser `C:\Users\Aurel\.conda\envs\hsn_tracker\python.exe` (pas `python` nu, pas un `.venv` racine). Le bare `python` peut résoudre vers une autre install qui n'a pas Playwright.
- Console Windows en `cp1252` par défaut → tout caractère hors latin-1 (flèches `←`, emojis, etc.) cause un `UnicodeEncodeError` au `print`. Utiliser des ascii-fallbacks (`<-`, `->`) dans les scripts ad-hoc, ou wrapper avec `sys.stdout.reconfigure(encoding='utf-8')` si possible.

## Structure Excel & migrations

- Les en-têtes Excel sont la source de vérité. `load_or_create_workbook()` ajoute automatiquement les colonnes manquantes en queue : pas besoin de migration manuelle quand on étend `HEADERS`.
- L'ordre de `HEADERS`, `COL_WIDTHS` et la liste `data` dans `append_rows` doivent rester en miroir. Quand tu ajoutes une colonne, vérifie les **trois** endroits.
- Lecture en `read_only=True` peut renvoyer des en-têtes avec mojibake (`Co�t/portion`) sur Windows. Le contenu fichier est OK ; n'essaie pas de "réparer" l'encodage à l'écriture.

## Scraping HSN — patterns observés

- **Concurrency 4** par défaut. Plus haut → timeouts et 429. Plus bas → lent.
- HSN expose les variants de deux façons :
  - **Legacy** : `input[name*=super_attribute]` + `<label>` adjacent. Le label texte est directement la taille (`30 softgels`, `1Kg`).
  - **Actuel (~2025+)** : `<select id=selectProductSimple>` avec options `"PRODUCT 1Kg ANANAS"`. Il faut parser la taille au regex.
- Toujours essayer la méthode legacy en premier puis fallback sur le select.
- `prix` côté select vient de `spconfig.optionPrices[sku].finalPrice.amount` — pas du DOM. Le DOM affiche le prix de la première option seulement.
- **Cookies / cookie banner** : `dismiss_cookie_popup` doit s'exécuter avant tout extract. Sinon le banner peut intercepter les clics et masquer du contenu.

### Pièges connus

- Le regex `\d+\s*[Kk]?[Gg]` sur du texte multilingue matche faussement la lettre G dans **GÉLULES** / **SOFTGELS** sur les pages oméga. Avant d'élargir le regex aux capsules, il faut une lookahead négative pour ne pas capturer `30 G` dans `30 GÉLULES`.
- Une page sans variant détecté tombe sur `size="Unique"`. C'est OK pour les whey monoformat, mais signal que sur un produit multi-variants, on a raté le sélecteur.
- `_parse_size_kg` retourne `None` pour `120 softgels` → pas de `px_kg` calculé. Conséquence : avant le fix, le filtre `if r.get("Prix/kg (€)") is None: continue` dans `generate_dashboard` excluait silencieusement TOUTES les lignes oméga. **Le filtre doit accepter une ligne dès qu'elle a un prix**, pas exiger px_kg.

## Logging concurrent

- `print()` depuis 4 workers async qui scrapent en parallèle entrelace les lignes dans la console. Une ligne sous un en-tête `[33/38] product-X` peut venir de `[34/38]`.
- **Tous les prints de variants doivent inclure un préfixe URL/idx** pour rester lisibles. Cf. le format `[short:28s]` utilisé dans `scrape_product`.
- Quand un user montre un log "bizarre", **vérifier d'abord l'Excel ou la donnée structurée**, pas l'output stdout. L'output peut être interleavé.

## Détection automatique de tags

- La détection (`_detect_sweeteners`, `_detect_whey_type`, `_detect_omega3_tags`, `_detect_creatine_tags`) lit le **nom du produit + ingrédients**. Le nom seul suffit souvent (`"100% Creapure"` → `creapure`).
- Pour la créatine, l'unité dans la table nutrition est en **mg** mais on stocke en **g** (3000 mg → 3 g). `_parse_nutrition` détecte le suffixe `mg` dans la cellule et convertit. Sans ça, la colonne "Créatine (g/dose)" affichait 3000 (delta x1000).
- Les tags whey type peuvent se cumuler (`isolat` + `cfm`, `hydrolysat` + `concentré`). Les filtres dashboard sont en OU par défaut.

## Dashboard HTML (généré)

- Le dashboard est un **gros fichier HTML statique** (Chart.js CDN, pas de framework). La JS est concaténée en strings dans `generate_dashboard()` — éviter les chaînes trop longues, préférer des chunks logiques.
- Les data structures côté JS :
  - `RAW` : array de tous les produits du dernier snapshot (un objet par variant produit-taille).
  - `HISTORY` : array de courbes `{produit, taille, points: [{date, pxkgProt, ...}]}`.
  - `LOCAL_TAGS` : édits manuels en `localStorage` (jamais écrasé par `RAW`).
- **Filtres en cascade** : `getFiltered()` filtre RAW d'abord par tab, puis catégorie, puis taille, puis tags multi-select avec logique ET/OU configurable.
- **Tabs Whey/Oméga/Créatine** : toujours visibles avec compteur `(n)`. Onglet vide = grisé + message "lance le scraper" dans la table. Ne pas masquer les onglets dynamiquement.
- **Colonnes du tableau** : pilotées par `TAB_COLS[currentTab]`. Quand tu ajoutes une métrique tab-spécifique, c'est ici, pas dans le HTML statique.
- **Best-cell highlight** : pour qu'une colonne soit marquée comme "meilleure", elle doit avoir `best:true` dans `TAB_COLS`. C'est le min de la colonne sur le filtre courant.
- **Sort key** : `TAB_PRIMARY[tab].sort` définit le tri par défaut quand on switch d'onglet.

## Pièges JS

- `setupSort` rattache des listeners sur les `<th>`. Comme `renderTableHead` recrée tout le `<thead>` via `innerHTML=`, les anciens listeners sont GC'd. Mais il faut **rappeler `setupSort` après chaque `renderTableHead`** sinon le tri tombe en panne.
- `escapeHtml(JSON.stringify(...))` est utilisé pour passer des strings dans un attribut HTML `onclick='...'`. Ne pas oublier les deux niveaux d'échappement (JSON pour la string JS, HTML pour l'attribut).
- Le `localStorage` n'a pas de quota dur sur les données du dashboard, mais reste prudent : un export via `📥 Exporter tags.json` doit être proposé au user (pas de backend pour persister).

## Workflow de modif

1. Si tu touches au schéma Excel → vérifie `HEADERS`, `COL_WIDTHS`, `append_rows` (3 endroits).
2. Si tu touches au dashboard → regen via `from hsn_tracker import generate_dashboard; generate_dashboard()` à chaque itération. Pas besoin de rescrap.
3. Si tu touches au scraping → test ciblé sur 1-2 URLs avant de lancer le full scrape (cf. les helpers ponctuels supprimés `_test_new_urls.py`).
4. **Toujours regen le dashboard** après modif data ou JS, sinon le HTML qui est versionné reflète l'ancien état.

## Détection rupture de stock

- HSN n'utilise pas `.stock.unavailable` seul — selon les variantes le DOM expose plutôt un bouton "Prévenez-moi lorsque le produit sera disponible" ou désactive l'add-to-cart.
- Le check (`_STOCK_CHECK_JS`) est **par variante** : il s'exécute après le click legacy ou le `select_option` qui sélectionne la variante. Sinon on lit l'état de la variante par défaut pour toutes les tailles.
- Le check est **scopé à `.product-info-main`** (et fallbacks), pas à `document.body`. Sinon une variante OOS qu'on exclut (ex: `Pack (5x500g)`) contamine la détection des tailles 500g/2kg en stock — incident résolu 2026-05.
- Pour la méthode SELECT (HSN ~2025+), il faut programmatiquement `page.select_option(...)` avant le check stock, sinon le DOM reste figé sur la variante par défaut. Coût ≈ CLICK_WAIT × N variantes (acceptable).
- La colonne `En stock` dans Excel vaut `True` par défaut (conservateur : mieux manquer une rupture que de faux-positifs).
- Les tailles `Pack` / `Monodose` sont déjà exclues via `SIZE_EXCLUDE_RE` avant la boucle — leur état stock n'a pas à être vérifié.

## Graphique de tendance multi-produits

- `selectedTrendIndices` = array d'indices dans `HISTORY`. Limité à 8 séries (lisibilité).
- `renderTrendChips()` doit être appelé à chaque modification de `selectedTrendIndices` avant `buildTrendChart()`.
- `buildTrendSelect()` remplace `buildTrendOptions()` — peuple uniquement le `<select>` d'ajout.
- Lors d'un changement de catégorie ou d'onglet, vider `selectedTrendIndices` + appeler `renderTrendChips()` pour réinitialiser l'UI.
- Les dates sont agrégées sur l'union de tous les points (`flatMap` + `Set` + `sort`). Les trous sont comblés par `spanGaps:true`.

## Ne pas faire

- Ne pas filtrer silencieusement des rows dans `generate_dashboard` sans un commentaire expliquant pourquoi (cf. l'incident px_kg/oméga).
- Ne pas hardcoder de colonnes "whey" dans le rendu du tableau — passer par `TAB_COLS`.
- Ne pas appeler des modules externes pour des transformations triviales sur Excel (openpyxl suffit, pas besoin de pandas).
- Ne pas écrire de fichier JSON / Markdown auxiliaire sans demande explicite — `tags.json` et `descriptions.json` sont les seuls JSON métier autorisés.
