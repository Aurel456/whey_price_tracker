---
name: fix-ci
description: Diagnostique le dernier run en échec du workflow « Daily price tracking » (GitHub Actions) et le répare si la cause est dans le code. À utiliser quand un run est rouge, qu'un email d'échec GitHub arrive, ou qu'on demande « pourquoi la CI a planté ».
---

# Réparer un run « Daily price tracking » en échec

Lis d'abord `AGENTS.md` (sections « Santé du relevé & CI », « Anti-bot Cloudflare »,
« Scraping HSN » / « Scraping MyProtein »). Python local :
`C:\Users\Aurel\.conda\envs\hsn_tracker\python.exe` (en cloud : `python`).

## 1. Récupérer le diagnostic (sans authentification)

Les logs de job exigent un jeton, **pas les annotations**. Le workflow y publie
la fin du log de chaque tracker en échec (`::warning` / `::error`), la fin de
`errors.log` / `myprotein_errors.log`, et les échecs Pages.

```bash
REPO=Aurel456/whey_price_tracker
# Derniers runs
curl -s "https://api.github.com/repos/$REPO/actions/workflows/track-prices.yml/runs?per_page=5" \
  | python -c "import sys,json; [print(r['id'], r['conclusion'], r['created_at'], r['head_sha'][:7]) for r in json.load(sys.stdin)['workflow_runs']]"
# Étapes en échec + annotations d'un run
RUN=<id>
JOB=$(curl -s https://api.github.com/repos/$REPO/actions/runs/$RUN/jobs | python -c "import sys,json; j=json.load(sys.stdin)['jobs'][0]; print(j['id']); [print('FAILED STEP:', s['name']) for s in j['steps'] if s['conclusion']=='failure']" | tee /dev/stderr | head -1)
curl -s https://api.github.com/repos/$REPO/check-runs/$JOB/annotations \
  | python -c "import sys,json; [print('---', a['annotation_level'], a.get('title'), '\n', a['message']) for a in json.load(sys.stdin)]"
```

Puis regarde la donnée réelle, pas seulement les logs (cf. AGENTS.md) :

```bash
for f in data/hsn_prices.csv data/myprotein_prices.csv; do echo $f; cut -d, -f1 $f | sort | uniq -c | tail -5; done
```

## 2. Classer la cause

| Symptôme | Cause | Action |
|---|---|---|
| Seule l'étape **Deploy to GitHub Pages (2e tentative)** échoue : `Deployment failed, try again later`, `No artifacts named "github-pages"` | Incident GitHub | **Rien à corriger dans le code.** Les données sont déjà committées, le run suivant republie. Vérifier https://www.githubstatus.com. Si ça dure plus de 2 jours : Settings → Pages → Source = GitHub Actions. |
| Tracker en `warning` (tentative 1) puis OK | Blocage ponctuel | Rien à faire. Noter si ça devient quotidien. |
| `[sanity] chute brutale` / `aucune row valide` / `Sorry, you have been blocked` / 403 | Cloudflare (HSN) | 1 jour isolé : rien. Plusieurs jours de suite : reproduire en local (c'est la combinaison `--headless=new` + stealth qui débloque, cf. AGENTS.md), ne pas réécrire l'historique. |
| Traceback Python dans l'annotation `error` | Bug ou changement du site | Reproduire en local sur 1-2 URLs, corriger, **ajouter un test** dans `tests/`. |
| `Échec après N tentative(s)` sur une URL précise (annotation `errors.log`) | Fiche retirée ou URL changée | Vérifier l'URL ; la retirer ou la mettre à jour dans `*_URLS` / `PRODUCTS`. |
| **Health check** seul en échec (trackers à 0) | Relevé partiel (< 75 % de la médiane 7 j) | Lister les produits manquants (ci-dessous), puis traiter comme une URL cassée ou un problème de clé de regroupement (MyProtein). |

Produits présents hier mais absents du dernier relevé :

```bash
python - <<'EOF'
import csv, collections
for f in ["data/hsn_prices.csv", "data/myprotein_prices.csv"]:
    by = collections.defaultdict(set)
    for r in csv.DictReader(open(f, encoding="utf-8")): by[r["Date"]].add(r["Produit"])
    d = sorted(by)[-2:]
    print(f, d, "manquants:", sorted(by[d[0]] - by[d[1]]))
EOF
```

## 3. Corriger

- Ne jamais éditer à la main l'historique `data/*.csv` pour « réparer » un jour raté.
- `python -m pytest -q` doit passer avant de committer.
- Commit court en français. **Ne pas pousser sur `main` sans accord** de l'utilisateur.
  En routine cloud : pousser sur une branche `fix-ci/<date>` et ouvrir une PR.
- Si la cause est un incident GitHub ou un blocage isolé, ne rien modifier et
  le dire clairement : un run rouge isolé n'est pas toujours un bug.
- Ajouter à `AGENTS.md` tout nouveau piège découvert.
