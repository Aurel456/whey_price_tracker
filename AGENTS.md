# AGENTS.md — Notes pour agents IA travaillant sur ce projet

Notes consolidées des erreurs et apprentissages rencontrés lors du dev. À lire avant de toucher au code.

## Environnement Python

- **Toujours** utiliser `C:\Users\Aurel\.conda\envs\hsn_tracker\python.exe` (pas `python` nu, pas un `.venv` racine). Le bare `python` peut résoudre vers une autre install qui n'a pas Playwright.
- Console Windows en `cp1252` par défaut → tout caractère hors latin-1 (flèches `←`, emojis, etc.) cause un `UnicodeEncodeError` au `print`. Utiliser des ascii-fallbacks (`<-`, `->`) dans les scripts ad-hoc, ou wrapper avec `sys.stdout.reconfigure(encoding='utf-8')` si possible.

## Anti-bot Cloudflare (2026-05)

HSN est passé derrière Cloudflare avec une détection stricte sur les pages produit (la home et les listes catégories passent toujours). Sans précautions → HTTP 403 `Sorry, you have been blocked`. Combinaison validée qui débloque :

1. **`--headless=new`** : nouveau renderer headless Chrome (passé en arg, avec `headless=False` côté Playwright). L'ancien headless est immédiatement détecté — c'est LE facteur déterminant. Le mode visible (`headless=False` sans `--headless=new`) marche aussi mais ouvre une fenêtre.
2. **`playwright-stealth`** : `await Stealth().apply_stealth_async(context)` patche `navigator.webdriver`, plugins fictifs, etc.
3. **UA rotatif** depuis `USER_AGENTS` (Chrome/Firefox/Safari récents desktop).
4. **`--disable-blink-features=AutomationControlled`** + `locale='fr-FR'`, `timezone_id='Europe/Paris'`, `viewport` réaliste.
5. **Concurrence baissée à 2** (au lieu de 4) — Cloudflare a commencé à bloquer après quelques jours à 4 workers.

Ce qui NE marche PAS (testé) : stealth seul en headless legacy, warmup via la home, baisser la concurrence seule. Le mode headless est le déterminant.

Pour le workflow GitHub Actions : `--headless=new` ne demande pas de display X (c'est un mode interne Chrome), donc pas besoin de xvfb a priori — à confirmer en CI.

## Parseur spconfig (initConfigurableOptions)

Les pages produit injectent un blob JSON via `initConfigurableOptions('ID', {...})`. Ce JSON contient des SVG inline (icônes formats `Monodose`, `Pack`, etc.) avec des `{` dans les `viewBox` / paths. **Compter naïvement les `{`/`}` casse** — utiliser `json.JSONDecoder().raw_decode(source, idx)` qui suit la syntaxe JSON (strings + escapes). Bug observé 2026-05 quand HSN a ajouté les SVG icônes.

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
- **Tabs Whey/Oméga/Créatine/Global** : toujours visibles avec compteur `(n)`. Onglet vide = grisé + message "lance le scraper" dans la table. Ne pas masquer les onglets dynamiquement. L'onglet **Global** agrège tous les types et désactive le bar chart principal.
- **Colonnes du tableau** : pilotées par `TAB_COLS[currentTab]`. Quand tu ajoutes une métrique tab-spécifique, c'est ici, pas dans le HTML statique. La colonne `type` (badge) sur Global utilise `fmt:'typeBadge'` et nécessite `TYPE_BADGE_LABEL` côté JS.
- **Best-cell highlight** : pour qu'une colonne soit marquée comme "meilleure", elle doit avoir `best:true` dans `TAB_COLS`. C'est le min de la colonne sur le filtre courant.
- **Sort key** : `TAB_PRIMARY[tab].sort` définit le tri par défaut quand on switch d'onglet.
- **Tendance par tab** : `TREND_META[tab]` définit `{key, decs, unit, title}`. La clé est lue dans `HISTORY[].points[]` — donc si tu ajoutes une nouvelle métrique de tendance, tu dois aussi la stocker dans `history_by_key.append(...)` côté Python (sinon `dm[p.date]=undefined`).
- **Filtre Catégorie restreint à whey** : `CATEGORIES` filtre `r.type==='whey'` côté JS — Oméga-3 et Créatine ne doivent JAMAIS apparaître comme boutons catégorie (le filtre n'est visible que sur le tab whey de toute façon, mais c'est défensif).

## Pièges JS

- `setupSort` rattache des listeners sur les `<th>`. Comme `renderTableHead` recrée tout le `<thead>` via `innerHTML=`, les anciens listeners sont GC'd. Mais il faut **rappeler `setupSort` après chaque `renderTableHead`** sinon le tri tombe en panne.
- `escapeHtml(JSON.stringify(...))` est utilisé pour passer des strings dans un attribut HTML `onclick='...'`. Ne pas oublier les deux niveaux d'échappement (JSON pour la string JS, HTML pour l'attribut).
- Le `localStorage` n'a pas de quota dur sur les données du dashboard, mais reste prudent : un export via `📥 Exporter tags.json` doit être proposé au user (pas de backend pour persister).

## Workflow de modif

1. Si tu touches au schéma Excel → vérifie `HEADERS`, `COL_WIDTHS`, `append_rows` (3 endroits).
2. Si tu touches au dashboard → regen via `from hsn_tracker import generate_dashboard; generate_dashboard()` à chaque itération. Pas besoin de rescrap.
3. Si tu touches au scraping → test ciblé sur 1-2 URLs avant de lancer le full scrape (cf. les helpers ponctuels supprimés `_test_new_urls.py`).
4. **Toujours regen le dashboard** après modif data ou JS, sinon le HTML qui est versionné reflète l'ancien état. `generate_dashboard(cfg)` écrit `docs/<dashboard_docs>` puis appelle `generate_recommendations()` qui écrit `docs/<reco_docs>`. L'accueil (`docs/index.html`) vient de `generate_comparatif()`, à relancer à part. Le workflow committe tout `docs/`.
   - **Landing page** : cf. la section « Page d'accueil = comparatif multi-sites » plus bas pour la liste exacte des fichiers générés.
5. **À la fin de chaque phase d'implémentation, proposer un `git commit -m "..."` rapide** avec un message court qui résume les changements. Ne pas commit soi-même sans validation — juste afficher la commande au user pour qu'il valide / ajuste.

## Page recommandations (recommandeur interactif)

- `generate_recommendations(rows, cfg)` est appelée en fin de `generate_dashboard()`. Elle écrit `docs/<cfg.reco_docs>` (`hsn.html` / `myprotein.html`). Les liens vers le dashboard et l'accueil passent par les tokens `__DASHBOARD_HREF__` et `__COMPARATIF_HREF__`.
- `_recommendation_data(rows)` calcule, par item du dernier snapshot : `concentration` (oméga = (EPA+DHA mg/cap)/poids capsule, poids lu dans le nom via `_omega_cap_mg`), `ifos`/`tg` (oméga), `creapure`/`monohydrate` (créatine), `wheyTier` (`_whey_tier`), `sansEdulcorant`, et `badges` (`_reco_badges`).
- **Gammes whey** (`_whey_tier`) : Vegan (vegetal) / Supérieure (isolat_cfm ou native) / Basique (isolat, concentre, hydrolysat, **caseine**) / Autre. Hydrolysat ET caséine = Basique (choix produit) : « Supérieure » est réservé à un procédé réellement premium (CFM à froid, protéine native), une caséine micellaire reste une caséine standard.
- **Détection `native` : frontière de mot obligatoire.** `"native" in combined` matchait la sous-chaîne dans des noms de gamme (`EVONATIVE CASEIN Lacprodan® MicelPure™` → tagué `native` → gamme Supérieure à tort, signalé par l'utilisateur 2026-07). Utiliser `re.search(r"\bnati(?:ve|fs?)\b", …)`. Se méfier de la même classe de bug sur les autres mots-clés courts noyés dans des noms de marque.
- **Caséine : détecter `casein` (sans e).** Les noms HSN sont parfois en anglais (`EVONATIVE CASEIN`) — `"caseine"`/`"caséine"` seuls rataient le produit entièrement.
- **Critère qualité oméga = IFOS + concentration**, PAS de TOTOX chiffré (il est dans les rapports IFOS PDF par lot, pas sur les pages produit — vérifié 2026-06). Ne pas tenter de scraper le TOTOX.
- Le recommandeur est du **JS embarqué** : `ITEMS` (JSON des items en stock avec métrique), filtré par `matchItem()` selon `crit[cat]`, trié par métrique croissante. Les whey embarquées sont restreintes à `categorie=="Whey"` (≥70 %) pour exclure les aliments enrichis.
- Après modif de la logique de critères, **toujours re-tester dans Playwright** que les défauts oméga (≥50 % + IFOS) sortent bien *ULTRA OMEGA-3 TG* et pas l'huile de poisson basique.

## Détection rupture de stock

- **HSN n'utilise PAS les sélecteurs Magento standards** : pas de `#product-addtocart-button`, pas de `button.tocart`, et `.stock.unavailable` seul n'est pas fiable. Le thème HSN est un Tailwind custom — détection à faire via leurs propres conteneurs.
- **Signaux fiables observés (`_STOCK_CHECK_JS`, 2026-05)** :
  1. **`#addtocart-wrapper`** : sur la variante OOS, le bouton "Ajouter maintenant" est remplacé par `Rupture de stock, Préviens-moi!` + `Prévenez-moi lorsque le produit sera disponible`. Signal le plus net.
  2. **`.product-info-main .stock-info-container`** : `En stock. Expédition immédiate.` (in-stock) vs `Temporairement en rupture de stock` (OOS).
- **Toujours scoper à `.product-info-main`** (ou `#addtocart-wrapper` qui est unique). La page contient des produits cross-sell ("Vous aimerez aussi") qui ont **chacun** un `.stock-info-container` et des boutons "Ajouter maintenant" — un check global produit des faux résultats.
- Le check (`_STOCK_CHECK_JS`) est **par variante** : il s'exécute après le click legacy ou le `select_option` qui sélectionne la variante. Sinon on lit l'état de la variante par défaut pour toutes les tailles.
- Pour la méthode SELECT (HSN ~2025+), il faut programmatiquement `page.select_option(...)` avant le check stock, sinon le DOM reste figé sur la variante par défaut. Coût ≈ CLICK_WAIT × N variantes (acceptable).
- Le `<select id=selectProductSimple>` capture aussi des options de cross-sell (sur evoexcel : 152 options vs ~6 pour le produit). La dédup par taille dans `scrape_product` masque le problème en pratique, mais `select_option` peut cibler le mauvais select sur ce genre de page — à surveiller.
- La colonne `En stock` dans Excel vaut `True` par défaut (conservateur : mieux manquer une rupture que de faux-positifs).
- Les tailles `Pack` / `Monodose` sont déjà exclues via `SIZE_EXCLUDE_RE` avant la boucle — leur état stock n'a pas à être vérifié.

## Graphique de tendance multi-produits

- `selectedTrendIndices` = array d'indices dans `HISTORY`. Limité à 8 séries (lisibilité).
- `renderTrendChips()` doit être appelé à chaque modification de `selectedTrendIndices` avant `buildTrendChart()`.
- `buildTrendSelect()` remplace `buildTrendOptions()` — peuple uniquement le `<select>` d'ajout.
- Lors d'un changement de catégorie ou d'onglet, vider `selectedTrendIndices` + appeler `renderTrendChips()` pour réinitialiser l'UI.
- Les dates sont agrégées sur l'union de tous les points (`flatMap` + `Set` + `sort`). Les trous sont comblés par `spanGaps:true`.

### Deux charts d'évolution (tendance + prix nominal)

- Le dashboard a 2 charts de tendance empilés : `chartTrend` (métrique du tab : EUR/kg prot, EUR/g EPA+DHA, EUR/kg créatine) et `chartTrendPrice` (prix nominal EUR). Sur le tab Global, `chartTrendPrice` est caché (`trendPriceWrap` → `tab-hidden`) car le chart principal montre déjà le prix nominal.
- **Les 2 charts partagent `selectedTrendIndices` et `getTrendDateWindow()`** — la synchro vient de `buildTrendChart()` qui appelle `buildTrendPriceChart()` à la fin. **Penser à l'appeler aussi dans la branche early-return** (sélection vide) sinon "Tout effacer" laisse l'autre chart figé.
- **Alignement visuel des x-axes** : forcer la largeur du y-axis identique sur les 2 charts via `afterFit:(scale)=>{scale.width=78}`. Sans ça, les labels y de largeurs différentes (`30.00 EUR` vs `0.025 EUR`) décalent les x-axes et les dates ne tombent plus l'une sous l'autre.
- **Filtres applicables aux selects/boutons trend** : `buildTrendSelect()`, `addAllDeals()` et `addAllFiltered()` doivent tous respecter `currentTab` + `currentCategory` (whey) + `currentSize`. `addAllFiltered()` utilise `getFiltered()` (filtre complet incluant édulcorants/types/labels/recherche) — pratique pour tracer "tous les produits visibles dans le tableau".
- Le filtre Taille rebuild le select via `filterSize → buildTrendSelect()`. Les courbes déjà sélectionnées AVANT le changement de taille restent affichées (pas auto-nettoyées — l'utilisateur peut retirer via × ou "Tout effacer").

## Multi-sites (HSN + MyProtein)

- Architecture : `hsn_tracker.py` contient toute la couche **partagée** (Excel,
  dashboard, recommandations, sanity). Elle est paramétrée par un `SiteConfig`
  (dataclass en haut du fichier) passé en argument optionnel `cfg=HSN_CFG`. Les
  défauts reproduisent **exactement** le comportement HSN historique — ne pas
  changer les défauts.
- `myprotein_tracker.py` = module mince : `import hsn_tracker as core`, son propre
  `MP_CFG`, et **uniquement** le scraping spécifique MyProtein. Il appelle
  `append_rows/generate_dashboard/sanity_check_rows(..., cfg=MP_CFG)`.
- Quand tu touches à une fonction liée aux chemins (`append_rows`,
  `generate_dashboard`, `generate_recommendations`, `sanity_check_rows`,
  `_last_date_product_count`, `log_error`, `load_or_create_workbook`), garde le
  paramètre `cfg` et n'utilise plus `EXCEL_PATH`/`ERROR_LOG_PATH` en dur dedans.
- **Fichiers séparés** par site (choix utilisateur) : pas de colonne « Site », pas
  de schéma Excel modifié. `myprotein.html` (docs/) est la landing Pages MyProtein,
  **distincte** de `index.html` (= HSN).

### Scraping MyProtein (plateforme THG/Hut)

- **Rien à voir avec le Magento de HSN** : pas de `initConfigurableOptions`, pas de
  Cloudflare. Bannière cookies **OneTrust** (`#onetrust-accept-btn-handler`).
- **Source des variantes = `ld+json` `ProductGroup.hasVariant[]`** (pas le DOM, où
  `data-sku` est vide). Chaque variante donne `name` (poids « 250G »/« 1KG »),
  `sku`, `offers.price` (EUR) et `offers.availability` → prix + **stock** sans
  interaction DOM. Gérer aussi le cas d'un `Product` simple (mono-taille).
- **Clé de regroupement par taille = dépend du type** (observé sur le site) :
  - **whey** : clé = **nb de portions**, MAIS restreint aux **portions canoniques**
    lues sur les boutons DOM (`button.elements-variations-button`, texte
    « 15 PORTIONS »…, ex. {15,30,90,150}). La ld+json contient ~107 variantes
    (taille × arôme) dont des **éditions limitées mono-taille** (8/20/21/32/64/83
    portions) qui ne sont PAS dans le sélecteur du site → sans restriction, le
    dashboard affichait p.ex. « 1.9kg / 59.99 » (64 portions, édition Miel vanille).
    On lit les boutons **sans cliquer** (présents dans le HTML statique) ; pas de
    pilotage SPA (les clics fragiles n'étaient pas fiables — overlay `listrak-popup`,
    swatches invisibles). Attention : le **poids n'est PAS un axe stable**, même à
    portions égales : le Sans arôme fait 23 g/portion (345 g pour 15 portions) vs
    ~30 g/portion pour un arôme (450 g) → on garde la variante la moins chère en
    stock par bucket (choix « best deal »), donc le poids affiché peut varier d'un
    arôme à l'autre. Libellé whey = « N portions (poids) ».
  - **créatine** : les arômes ajoutent des charges → portions variables à poids
    égal, mais le **poids est stable** → clé = poids. Déjà propre (4 buckets), pas
    de restriction canonique nécessaire (les boutons créa sont en portions, qui ne
    mappent pas sur les poids ld+json — ne pas tenter de les croiser).
  - **oméga** : clé = nb de gélules.
  On garde la variante la moins chère en stock par bucket.
- **Fallback poids sur les protéines végétales** (2026-07) : `isolat-de-proteine-de-soja`,
  `proteine-vegan-impact` (et d'autres) n'exposent **que des poids** dans la
  ld+json — aucun nom de variante ne porte de portions. Le regroupement whey par
  portions les jetait toutes (`continue`) → **0 ligne, produit totalement
  invisible et silencieux**. `_group_by_size` calcule donc `whey_by_weight` en
  amont de la boucle : si AUCUNE variante du produit n'expose de portions, on
  bascule sur le poids comme clé (comme la créatine), libellé compris. Décision
  **par produit**, jamais par variante : si le produit expose des portions, une
  variante isolée sans portions reste une variante non standard à écarter.
- Symptôme à connaître : un produit qui renvoie 0 ligne sans erreur bruyante est
  presque toujours un problème de **clé de regroupement**, pas de scraping. Le
  réflexe est de dumper `_iter_variants(nodes)` et de regarder si les noms
  portent des portions, un poids, ou des gélules.
- Les libellés de taille MyProtein **changent côté site sans préavis** : le
  2026-07-27, les noms de variantes Impact Whey ont commencé à inclure
  « - 30portions », ce qui a fait basculer le produit du poids vers les portions
  du jour au lendemain. Ça crée une génération de lignes fantômes (ancien
  libellé), résorbée automatiquement par `_activity_status` en 3 jours. Ne pas
  s'en alarmer, ne pas « réparer » l'historique.
- **Nutrition** : dans un accordéon (à **déplier** avant lecture, sinon `innerText`
  vide). Table à colonnes `Pour 100 g` / `Par portion` — ordre **inverse** de HSN
  (où col[2]=100g) → extracteur dédié `_parse_mp_nutrition` qui lit par **en-tête
  de colonne**. Les oméga n'ont **pas** de colonne 100g (seulement « Par portion »)
  → le parser doit accepter ce schéma (EPA/DHA lus en colonne portion).
- **Pas de profil d'acides aminés** publié → €/3g leucine vide pour MyProtein.
- Edge-case oméga vegan (algues) : parfois DHA seul sans EPA → `_enrich_row`
  n'établit pas €/g EPA+DHA (best-effort, non bloquant).

## Page d'accueil = comparatif multi-sites

- **5 fichiers HTML générés, tous dans `docs/`** : `index.html` (**accueil** =
  comparatif HSN vs MyProtein), `hsn.html` et `myprotein.html` (reco par site),
  `dashboard.html` et `myprotein-dashboard.html`.
- **Plus aucun HTML à la racine** (2026-07) : les copies `whey_dashboard.html`,
  `recommandations.html`, `myprotein_dashboard.html`,
  `myprotein-recommandations.html`, `comparatif.html` étaient des doublons
  octet-pour-octet à régénérer et committer chaque jour. Les liens entre pages
  sont relatifs → ouvrir `docs/index.html` en local fonctionne tel quel. Les
  champs `*_local` de `SiteConfig` et les tokens `__DASHBOARD_HREF__` par
  destination ont disparu avec eux. **Ne pas les réintroduire.**
- `generate_comparatif(sites)` prend une **liste de SiteConfig** et relit chaque
  Excel via `_site_snapshot`. Elle est appelée depuis **`myprotein_tracker.py`**
  (fin de `main`) et pas depuis `hsn_tracker.py` : c'est le seul module qui
  connaît les deux configs sans créer d'import circulaire. Conséquence : lancer
  `hsn_tracker.py` seul ne régénère PAS l'accueil.
- **Seuil d'égalité (`TIE_PCT`, 3 %)** : sous cet écart, aucune catégorie n'a de
  gagnant (badge « ≈ Prix équivalent »). Sans ça, un écart de **1 centime** sur la
  créatine (25,49 € vs 25,50 €) suffisait à faire écrire « aucun site n'est le
  moins cher partout » alors qu'un site était devant partout où l'écart était
  réel — signalé par l'utilisateur, à juste titre.
- **Le constat n°1 de la section étude est calculé, jamais écrit d'avance** :
  trois formulations selon les données (un seul site devant / plusieurs sites se
  partagent / tout est à égalité). Ne pas re-hardcoder une conclusion : les prix
  bougent énormément (le 2026-07-27, la créatine HSN 1 kg est passée de 25,50 € à
  13,43 €, faisant basculer le classement sur les 3 catégories en un jour).
- **Blurbs marchands** (`SITE_BLURBS`, keyés par `SiteConfig.name`) : factuel et
  vérifiable uniquement (pays, positionnement, ce qui est publié sur les fiches).
  Pas de superlatif commercial — la page est un comparatif indépendant.
- **Honnêteté du match whey** : le meilleur rapport d'un site peut être une
  protéine végétale (soja/pois, structurellement moins chère au kg de protéine)
  face à une whey laitière chez l'autre. Le flag `mixed` détecte ce cas, affiche
  un avertissement sous le bloc, et **exclut cet écart** du chiffre « jusqu'à
  N % » de la section étude (sinon on annonce 241 % pour un écart qui n'en est
  pas un). Ne pas retirer ce garde-fou — c'est ce qui rend le comparatif
  défendable publiquement.

## Statut actif / inactif (distinct de la rupture de stock)

- Trois états distincts, à ne pas confondre :
  - **En stock / rupture** (colonne Excel `En stock`) : le produit est au
    catalogue mais indisponible. Il est toujours scrapé chaque jour → **actif**.
  - **Inactif** : plus vu au scrape depuis > `STALE_SIZE_DAYS` (3) jours. Retiré
    du catalogue, ou taille/nom qui a changé. Gardé et affiché grisé (badge
    💤 Inactif) sur le dashboard, **masqué par défaut** (case « Afficher les
    inactifs »), et **exclu du recommandeur** — on ne conseille pas une réf
    qu'on ne sait plus trouver.
  - **Supprimé** : absent depuis > `INACTIVE_DROP_DAYS` (30) jours →
    `_activity_status` le retire de `latest` pour de bon.
- `_activity_status(latest)` remplace l'ancien `_prune_stale_sizes` : elle
  **renvoie** `{key: {actif, joursAbsent}}` et ne supprime que les > 30 jours.
  Les deux appelants (`generate_dashboard`, `_recommendation_data`) décident
  ensuite quoi faire des inactifs — c'est volontairement leur choix, pas celui
  de la fonction.

## Lignes fantômes dans `latest` (dashboard + recommandeur)

- `generate_dashboard` et `_recommendation_data` gardent chacun un dict `latest`
  keyé par `(produit, taille)` = dernière ligne vue. Un `(produit, taille)` qui
  disparaît du scrape (au lieu d'être mis à jour) **reste affiché pour toujours**
  avec un prix vieux de plusieurs semaines si rien ne le purge.
- Deux causes observées en 2026-07 :
  - **MyProtein** : le regroupement whey/oméga garde la variante (arôme) la moins
    chère par bucket (portions/gélules) — le POIDS affiché dans `Taille` vient de
    cette variante et peut changer de jour en jour (ex. "625g" → "600g" si un
    autre arôme devient moins cher), sans que l'ancienne taille ne revienne
    jamais. Repéré via Impact Whey Protein qui affichait un "625g / 22,19€" vieux
    d'un mois à côté des tailles du jour.
  - **HSN** : le nom produit change de casse/symbole ® côté site (ex.
    `...DIGEZYME®)` → `...DigeZyme®)` le 2026-07-17, `MicelPure®`, `IFOS®`…) — la
    clé `(produit, taille)` traite ça comme un **produit entièrement différent**,
    donc l'ancien nom ne se met plus jamais à jour et pollue le dashboard/reco en
    doublon.
- **Fix** : `_activity_status(latest)` (partagé par les deux fonctions) compare la
  date de chaque ligne à la date la plus récente de **TOUT le snapshot** — pas à
  la dernière date du même nom de produit (un nom qui change de casse n'a par
  définition aucune ligne "plus récente" sous son propre nom à comparer). Cf. la
  section « Statut actif / inactif » ci-dessus pour les seuils.

## Recommandeur (`generate_recommendations`) — pas de textes hardcodés par site

- La page reco est générée pour HSN **et** MyProtein via le même `cfg: SiteConfig`.
  Ne jamais écrire "HSN"/"HSNstore" en dur dans le HTML/JS de
  `generate_recommendations` — toujours passer par `cfg.brand` /
  `cfg.site_domain` (champ ajouté pour l'eyebrow + le texte "mis à jour chaque
  jour"). Bug réel trouvé 2026-07 : la page MyProtein affichait "Guide
  indépendant · HSNstore.fr" et "les whey... de HSN au vrai coût" mot pour mot.

## Ne pas faire

- Ne pas filtrer silencieusement des rows dans `generate_dashboard` sans un commentaire expliquant pourquoi (cf. l'incident px_kg/oméga).
- Ne pas hardcoder de colonnes "whey" dans le rendu du tableau — passer par `TAB_COLS`.
- Ne pas appeler des modules externes pour des transformations triviales sur Excel (openpyxl suffit, pas besoin de pandas).
- Ne pas écrire de fichier JSON / Markdown auxiliaire sans demande explicite — `tags.json` et `descriptions.json` sont les seuls JSON métier autorisés.
