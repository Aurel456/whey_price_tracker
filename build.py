"""
Build du site + contrôles
=========================
Seul module qui connaît TOUS les sites (les trackers ne connaissent que le leur,
et hsn_tracker ne peut pas importer myprotein_tracker sans cycle). Il régénère
docs/ entièrement depuis data/ — aucun scraping.

Usage:
    python build.py            # toutes les pages + l'API JSON de l'extension
    python build.py --health   # bilan des relevés du jour, exit 1 si anomalie
    python build.py --excel    # exporte l'historique de chaque site en .xlsx (exports/)
"""

import os
import sys

import hsn_tracker as core
import myprotein_tracker as mp

SITES = [core.HSN_CFG, mp.MP_CFG]


def build_site() -> None:
    for cfg in SITES:
        core.generate_dashboard(cfg=cfg)   # écrit aussi la page reco du site
    core.generate_comparatif(SITES)
    core.generate_api(SITES)


def health() -> int:
    ok, lines = core.health_report(SITES)
    report = "\n".join(["### Relevés du jour", "", *lines, ""])
    print(report)
    # Résumé affiché en tête du run dans l'onglet Actions.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--health" in sys.argv:
        sys.exit(health())
    if "--excel" in sys.argv:
        for cfg in SITES:
            print(f"Excel : {core.export_excel(cfg)}")
    else:
        build_site()
