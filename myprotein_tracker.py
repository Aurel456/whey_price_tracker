"""
MyProtein Price Tracker
=======================
Variante MyProtein (fr.myprotein.com) du tracker HSN. Réutilise toute la couche
Excel + dashboard + recommandations + sanity de `hsn_tracker` via un `SiteConfig`
dédié (MP_CFG) — seuls les chemins de sortie changent. Le scraping, lui, est
spécifique à MyProtein (plateforme THG/Hut, rien à voir avec le Magento de HSN).

Usage: python myprotein_tracker.py

Sorties (fichiers séparés de HSN) :
    myprotein_prices.xlsx, myprotein_dashboard.html, docs/myprotein-dashboard.html,
    myprotein-recommandations.html, docs/myprotein.html, myprotein_errors.log

Particularités MyProtein (cf. AGENTS.md) :
    - Tailles + prix + stock viennent de la ld+json `ProductGroup.hasVariant[]`
      (pas du DOM). Poids dans le nom de variante ("250G", "1KG", "2.5KG").
    - Pas de profil d'acides aminés publié → colonne €/3g leucine vide.
    - Nutrition dans un accordéon, colonnes "Pour 100 g" / "Par portion" (ordre
      inverse de HSN) → extracteur dédié `_parse_mp_nutrition` (lecture par en-tête).
"""

import asyncio
import json
import random
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

import hsn_tracker as core
from hsn_tracker import (
    SiteConfig, USER_AGENTS,
    append_rows, generate_dashboard, sanity_check_rows, log_error,
    _enrich_row, _parse_num, _parse_size_caps,
)

# ── Configuration ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent

MP_CFG = SiteConfig(
    name="MyProtein",
    brand="MyProtein",
    excel_path=_HERE / "myprotein_prices.xlsx",
    error_log_path=_HERE / "myprotein_errors.log",
    dashboard_local="myprotein_dashboard.html",
    dashboard_docs="myprotein-dashboard.html",  # ≠ index.html (= HSN sur Pages)
    reco_local="myprotein-recommandations.html",
    reco_docs="myprotein.html",
    other_brand="HSN",
    other_dashboard_local="whey_dashboard.html",
    other_dashboard_docs="dashboard.html",
)

# Shortlist de produits clés (url, type). Extensible. Le type est passé explicitement
# à `_enrich_row` car l'URL MyProtein (/p/nutrition-sportive/...) ne le révèle pas.
PRODUCTS = [
    # Whey & protéines
    ("https://fr.myprotein.com/p/nutrition-sportive/impact-whey-protein/10530943/", "whey"),
    ("https://fr.myprotein.com/p/nutrition-sportive/impact-whey-isolate/10530911/", "whey"),
    ("https://fr.myprotein.com/p/nutrition-sportive/clear-whey-isolate/12081395/", "whey"),
    ("https://fr.myprotein.com/p/nutrition-sportive/impact-diet-whey/10530657/", "whey"),
    # Créatine
    ("https://fr.myprotein.com/p/nutrition-sportive/creatine-monohydrate-en-poudre/10530050/", "creatine"),
    ("https://fr.myprotein.com/p/nutrition-sportive/the-creatine-creapure/10529740/", "creatine"),
    ("https://fr.myprotein.com/p/nutrition-sportive/creapure-creatine-micronisee/10574930/", "creatine"),
    # Oméga-3
    ("https://fr.myprotein.com/p/nutrition-sportive/omega-3-en-gelules/10529329/", "omega3"),
    ("https://fr.myprotein.com/p/nutrition-sportive/omegas-3-vegans/13633515/", "omega3"),
]

PAGE_TIMEOUT = 45_000
RETRY_ATTEMPTS = 1
RETRY_DELAY_MS = 2_000
CONCURRENCY = 3

# Poids dans le nom de variante : "250G", "1KG", "2.5KG", "900G", "500g"…
WEIGHT_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(kg|g)\b', re.IGNORECASE)
# Portions dans le nom : "8portions", "33 portions". C'est l'axe de taille FIABLE
# pour les poudres : 33 portions = 1kg quel que soit l'arôme, alors que le POIDS
# net varie par arôme (chocolat plus dense → "930g" pour le même pot "1kg"), ce qui
# génère des dizaines de pseudo-tailles parasites dans la ld+json.
PORTIONS_RE = re.compile(r'(\d+)\s*portions?', re.IGNORECASE)


# ── Helpers variantes (ld+json) ────────────────────────────────────────────────
def _weight_to_kg(name: str):
    """Extrait le poids (kg) depuis un nom de variante MyProtein."""
    if not name:
        return None
    m = WEIGHT_RE.search(name)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    return val if m.group(2).lower() == "kg" else val / 1000.0


def _kg_label(kg: float) -> str:
    """0.25 → '250g' ; 1.0 → '1kg' ; 2.5 → '2.5kg' (parseable par _parse_size_kg)."""
    if kg < 1:
        return f"{int(round(kg * 1000))}g"
    return (f"{kg:.1f}".rstrip("0").rstrip(".")) + "kg"


def _iter_variants(ld_nodes: list):
    """Aplati les ld+json → liste de variantes {name, sku, price, in_stock}.

    Gère ProductGroup.hasVariant[] (multi-tailles) ET un Product simple (offers).
    """
    def _offer(node):
        off = node.get("offers", {})
        if isinstance(off, list):
            off = off[0] if off else {}
        price = off.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        avail = str(off.get("availability", "")).lower()
        return price, ("outofstock" not in avail and "soldout" not in avail)

    out = []
    for node in ld_nodes:
        if not isinstance(node, dict):
            continue
        t = node.get("@type", "")
        if t == "ProductGroup":
            for v in node.get("hasVariant", []) or []:
                price, in_stock = _offer(v)
                out.append({"name": v.get("name", ""), "sku": str(v.get("sku", "")),
                            "price": price, "in_stock": in_stock})
        elif t == "Product" and node.get("offers"):
            price, in_stock = _offer(node)
            out.append({"name": node.get("name", ""), "sku": str(node.get("sku", "")),
                        "price": price, "in_stock": in_stock})
    return out


def _group_by_size(variants: list, ptype: str) -> list:
    """Regroupe les variantes par taille (une ligne par taille réelle).

    Une déclinaison MyProtein = taille × arôme (jusqu'à 100). On dédoublonne en
    gardant la variante la **moins chère en stock** (sinon la moins chère). La clé
    de regroupement dépend du type, car l'axe stable diffère (observé sur le site) :
      - whey : le POIDS net varie selon l'arôme (chocolat plus dense), mais le nb
        de PORTIONS est fixe par taille nominale → clé = portions.
      - créatine : les arômes ajoutent des charges → les portions varient à poids
        égal, mais le POIDS est fixe → clé = poids.
      - oméga-3 : clé = nb de gélules.
    Le poids net affiché vient de la variante représentative retenue.
    Renvoie [{size_label, size_kg|caps, price, in_stock}].
    """
    buckets = {}
    for v in variants:
        if v["price"] is None:
            continue
        if ptype == "omega3":
            caps = _parse_size_caps(v["name"])
            if not caps:
                continue
            key, extra = ("caps", caps), {"caps": caps}
        else:
            kg = _weight_to_kg(v["name"])
            if not kg:
                continue
            mp = PORTIONS_RE.search(v["name"])
            if ptype == "whey" and mp:
                key = ("port", int(mp.group(1)))
            else:
                key = ("kg", round(kg, 2))
            extra = {"size_kg": kg}
        cur = buckets.get(key)
        better = (
            cur is None
            or (v["in_stock"] and not cur["in_stock"])
            or (v["in_stock"] == cur["in_stock"] and v["price"] < cur["price"])
        )
        if better:
            label = (f"{extra['caps']} gélules" if ptype == "omega3"
                     else _kg_label(extra["size_kg"]))
            buckets[key] = {"size_label": label, "price": v["price"],
                            "in_stock": v["in_stock"], **extra}
    # Tri par taille croissante (gélules ou kg)
    return [buckets[k] for k in sorted(buckets, key=lambda k: buckets[k].get("size_kg") or buckets[k].get("caps") or 0)]


# ── Extraction nutrition (structure MyProtein) ──────────────────────────────────
_MP_EXPAND_JS = r"""() => {
    // Déplie les accordéons Nutrition / Ingrédients (sinon innerText vide car masqué)
    let n = 0;
    for (const el of document.querySelectorAll('button, summary, [role=button], h2, h3, [class*=accordion]')) {
        const t = (el.innerText || '').toLowerCase();
        if (/information nutri|valeurs nutri|déclaration nutri|ingr.dient/.test(t)) {
            try { el.click(); n++; } catch (e) {}
        }
    }
    return n;
}"""

_MP_TABLES_JS = r"""() =>
    Array.from(document.querySelectorAll('table')).map(t =>
        Array.from(t.rows).map(r => Array.from(r.cells).map(c => (c.innerText || '').trim()))
    )
"""

_MP_INGREDIENTS_JS = r"""() => {
    const heads = Array.from(document.querySelectorAll('h2,h3,h4,p,span,strong,button,summary,div'))
        .filter(el => (el.innerText || '').trim().toLowerCase().startsWith('ingrédient'));
    for (const h of heads) {
        // Cherche dans les frères/oncles un bloc de texte conséquent
        let n = h.nextElementSibling, hop = 0;
        while (n && hop < 5) {
            const txt = (n.innerText || '').trim();
            if (txt.length > 25) return txt;
            n = n.nextElementSibling; hop++;
        }
        // Sinon, le corps de l'accordéon parent
        const body = h.closest('[class*=accordion]')?.innerText?.trim();
        if (body && body.length > 40) return body;
    }
    return '';
}"""


def _parse_mp_nutrition(tables: list, ptype: str) -> dict:
    """Parse la table nutrition MyProtein en lisant l'en-tête de colonne.

    Colonnes typiques : [label, 'Pour 100 g', 'Par portion de 30 g']. On localise
    la colonne 100g et la colonne portion par leur intitulé (≠ HSN où c'est figé).
    """
    out = {}
    for rows in tables:
        # Repère la ligne d'en-tête + les indices de colonnes. Deux schémas :
        #   - whey/créatine : "… | Pour 100 g | Par portion de 30 g"
        #   - oméga-3       : "Valeurs nutritionnelles moyennes | Par portion | %Apport"
        #     (PAS de colonne 100 g → on lit la colonne portion/capsule)
        col100 = coldose = None
        for r in rows:
            joined = " ".join(c.lower() for c in r)
            if "valeurs nutri" in joined or "100" in joined or "par portion" in joined:
                for i, cell in enumerate(r):
                    cl = cell.lower()
                    if "100" in cl and col100 is None:
                        col100 = i
                    elif ("portion" in cl or "dose" in cl or "capsule" in cl) and coldose is None:
                        coldose = i
                break
        if col100 is None and coldose is None:
            continue
        for r in rows:
            if not r or not r[0]:
                continue
            label = r[0].lower()
            cell100 = r[col100] if (col100 is not None and len(r) > col100) else ""
            celldose = r[coldose] if (coldose is not None and len(r) > coldose) else ""
            v100 = _parse_num(cell100)
            vdose = _parse_num(celldose)
            # Macros (par 100g)
            if ("énerg" in label or "energ" in label) and "kcal" in cell100.lower():
                out["energie_kcal_100g"] = v100
            elif "protéin" in label or "protein" in label:
                out["proteines_100g"] = v100
            elif "glucides" in label or "hydrates" in label:
                if "dont" not in label:
                    out["glucides_100g"] = v100
            elif ("graisse" in label or "lipide" in label or "matières grasses" in label):
                if "dont" not in label and "satur" not in label:
                    out["lipides_100g"] = v100
            elif "sel" in label or "sodium" in label:
                out["sel_100g"] = v100
            # Oméga-3 (par dose/capsule)
            elif "epa" in label or "eicosapent" in label or "icosapent" in label:
                out["epa_mg_dose"] = vdose if vdose is not None else v100
            elif "dha" in label or "docosahexa" in label:
                out["dha_mg_dose"] = vdose if vdose is not None else v100
            # Créatine (par dose) — convertit mg → g si nécessaire
            elif ("créatine" in label or "creatine" in label) and "kreatin" not in label:
                raw = celldose or cell100
                v = vdose if vdose is not None else v100
                if v is not None:
                    out["creatine_g_dose"] = round(v / 1000.0, 2) if "mg" in raw.lower() else v
        if out:
            break
    return out


async def dismiss_cookie(page) -> None:
    for sel in ("#onetrust-accept-btn-handler", "#truste-consent-button",
                "button[aria-label*='Accept']"):
        try:
            await page.click(sel, timeout=2_500)
            await page.wait_for_timeout(400)
            return
        except Exception:
            continue
    # Fallback : retire l'overlay
    try:
        await page.evaluate("""() => {
            document.querySelector('#onetrust-consent-sdk')?.remove();
            document.querySelector('.onetrust-pc-dark-filter')?.remove();
        }""")
    except Exception:
        pass


# ── Scraping produit ────────────────────────────────────────────────────────────
async def scrape_product(page, url: str, ptype: str) -> list:
    results = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        try:
            await page.wait_for_selector("h1", timeout=10_000)
        except PlaywrightTimeout:
            pass
        await dismiss_cookie(page)

        name = await page.evaluate("document.querySelector('h1')?.innerText?.trim() || ''")

        # Variantes / prix / stock depuis la ld+json
        ld_raw = await page.evaluate(
            r"""() => Array.from(document.querySelectorAll("script[type='application/ld+json']"))
                .map(s => s.textContent)"""
        )
        nodes = []
        for blob in ld_raw:
            try:
                data = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                continue
            graph = data.get("@graph", [data]) if isinstance(data, dict) else data
            nodes.extend(graph if isinstance(graph, list) else [graph])
        variants = _group_by_size(_iter_variants(nodes), ptype)

        # Nutrition / ingrédients : 1× par produit (identiques sur toutes les tailles)
        await page.evaluate(_MP_EXPAND_JS)
        await page.wait_for_timeout(1_000)
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 1200)")
            await page.wait_for_timeout(150)
        tables = await page.evaluate(_MP_TABLES_JS)
        ingredients = await page.evaluate(_MP_INGREDIENTS_JS) or ""
        nutri = {
            "nutrition": _parse_mp_nutrition(tables, ptype),
            "amino_acids": {},  # MyProtein ne publie pas de profil AA
            "ingredients": ingredients,
        }

        if not variants:
            log_error(url, "Aucune variante extraite de la ld+json", MP_CFG)
            return results

        for v in variants:
            price = v["price"]
            row = {
                "name": name, "url": url, "size": v["size_label"],
                "price": f"{price:.2f}", "en_stock": v["in_stock"],
            }
            # px_kg pour whey/créatine (le prix/poids) ; oméga recalcule via capsules
            if ptype in ("whey", "creatine") and v.get("size_kg"):
                row["px_kg"] = f"{price / v['size_kg']:.2f} €"
            results.append(_enrich_row(row, nutri, ptype=ptype))
            short = url.rsplit("/", 2)[-2][:24]
            flag = "" if v["in_stock"] else " [OOS]"
            print(
                f"    [{short:24s}] {v['size_label']:12} | {price:>7.2f} EUR | "
                f"PROT:{nutri['nutrition'].get('proteines_100g','-')}g | "
                f"PXKG-PROT:{row.get('px_kg_proteine','-')}{flag}"
            )

    except PlaywrightTimeout:
        print(f"  Timeout : {url}")
    except Exception as e:
        print(f"  Erreur : {url} — {e}")

    return results


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"\n{'='*62}")
    print(f"  MyProtein Tracker — {core.datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*62}")

    async with async_playwright() as p:
        # Même combinaison anti-bot que HSN (--headless=new + stealth + UA rotatif).
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--headless=new", "--disable-blink-features=AutomationControlled",
            ],
        )
        ua = random.choice(USER_AGENTS)
        print(f"  user-agent: {ua[:60]}...")
        context = await browser.new_context(
            user_agent=ua, locale="fr-FR", timezone_id="Europe/Paris",
            viewport={"width": 1366, "height": 900},
        )
        await Stealth().apply_stealth_async(context)
        await context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "font", "media"}
            else route.continue_(),
        )

        sem = asyncio.Semaphore(CONCURRENCY)
        total = len(PRODUCTS)

        async def worker(idx: int, url: str, ptype: str):
            async with sem:
                print(f"[{idx:02d}/{total}] {url.rsplit('/', 2)[-2]} ({ptype})")
                last_err = None
                for attempt in range(RETRY_ATTEMPTS + 1):
                    page = await context.new_page()
                    try:
                        rows = await scrape_product(page, url, ptype)
                        if rows:
                            return rows
                        last_err = "résultat vide"
                    except Exception as e:
                        last_err = repr(e)
                    finally:
                        await page.close()
                    if attempt < RETRY_ATTEMPTS:
                        print(f"   ↻ retry ({last_err})")
                        await asyncio.sleep(RETRY_DELAY_MS / 1000)
                log_error(url, f"Échec après {RETRY_ATTEMPTS + 1} tentative(s) : {last_err}", MP_CFG)
                return []

        batches = await asyncio.gather(
            *(worker(i, u, t) for i, (u, t) in enumerate(PRODUCTS, 1))
        )
        all_rows = [r for batch in batches for r in batch]
        await browser.close()

    clean_rows, ok, reason = sanity_check_rows(all_rows, MP_CFG)
    print(f"\n[sanity] {reason}")
    if not ok:
        if core.os.environ.get("SANITY_SKIP") == "1":
            print("  [sanity] SANITY_SKIP=1 → on écrit quand même")
        else:
            log_error("(sanity)", f"REFUS commit : {reason}", MP_CFG)
            print("  Refus d'append/commit. Override : SANITY_SKIP=1")
            sys.exit(1)

    if clean_rows:
        print(f"\nEnregistrement de {len(clean_rows)} lignes dans Excel...")
        append_rows(clean_rows, MP_CFG)
        print(f"Excel OK : {MP_CFG.excel_path}")
        print("Génération du dashboard HTML...")
        generate_dashboard(cfg=MP_CFG)
        print(f"Terminé ! {len(clean_rows)} entrées MyProtein")
    else:
        print("Aucune donnée collectée.")

    return clean_rows


if __name__ == "__main__":
    asyncio.run(main())
