"""
HSN Whey Price Tracker
======================
Lance ce script pour mettre à jour les prix du jour dans whey_prices.xlsx
et régénérer le dashboard HTML whey_dashboard.html.

Usage: python hsn_tracker.py

Dépendances: pip install playwright openpyxl
             python -m playwright install chromium
"""

import asyncio
import json
import re
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Configuration ─────────────────────────────────────────────────────────────
EXCEL_PATH       = Path(__file__).parent / "whey_prices.xlsx"
DESCRIPTIONS_PATH = Path(__file__).parent / "descriptions.json"
ERROR_LOG_PATH   = Path(__file__).parent / "errors.log"

# Sanity checks : un whey doit avoir un % de protéine entre ces bornes
PROT_MIN_PCT = 50.0
PROT_MAX_PCT = 95.0
RETRY_ATTEMPTS = 1     # nb de retentatives en cas d'échec scraping
RETRY_DELAY_MS = 2_000

CATEGORY_URLS = [
    "https://www.hsnstore.fr/nutrition-sportive/proteines/whey",
    "https://www.hsnstore.fr/nutrition-sportive/proteines/whey?p=2",
    "https://www.hsnstore.fr/nutrition-sportive/proteines/whey/isolee-de-lactoserum",
    "https://www.hsnstore.fr/nutrition-sportive/proteines/whey/concentrees-de-lactoserum",
    "https://www.hsnstore.fr/nutrition-sportive/proteines/whey/hydrolysees",
    "https://www.hsnstore.fr/nutrition-sportive/proteines",
]

# URLs supplémentaires à toujours inclure (même si non listées en catégorie)
EXTRA_URLS = [
    "https://www.hsnstore.fr/marques/sport-series/evobasic-whey",
    "https://www.hsnstore.fr/marques/sport-series/evolate-2-0-sans-edulcorants-whey-isolate-cfm",
    "https://www.hsnstore.fr/marques/raw-series/isolat-de-proteine-de-lait",
    "https://www.hsnstore.fr/marques/sport-series/evowhey-protein-sans-edulcorants",
    "https://www.hsnstore.fr/marques/raw-series/isolat-de-proteine-hydrolysee-de-clear-whey",
    "https://www.hsnstore.fr/marques/raw-series/100-isolat-de-proteine-de-lactoserum-hydrolyse",
    "https://www.hsnstore.fr/marques/sport-series/evolate-2-0-whey-isolate-cfm",
    "https://www.hsnstore.fr/marques/raw-series/100-whey-protein-isolate",
]

PAGE_TIMEOUT = 30_000
CLICK_WAIT   = 700
CONCURRENCY  = 4

PORT_RE = re.compile(
    r'(?:DDM:\s*([\d/]+)\s*\|)?\s*PORT\.:\s*(\d+)'
    r'(?:\s*\|\s*COÛT/PORT\.:\s*([\d,\s\xa0]+€))?'
    r'(?:\s*\|\s*PX/KG:\s*([\d,\s\xa0]+€))?'
)

# ── Logging d'erreurs ─────────────────────────────────────────────────────────
def log_error(url: str, reason: str) -> None:
    """Append une ligne dans errors.log avec timestamp + url + raison."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {url}\n   {reason}\n")


# ── Excel styles ──────────────────────────────────────────────────────────────
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF", name="Arial", size=10)
DATE_FILL   = PatternFill("solid", fgColor="D9E1F2")
ALT_FILL    = PatternFill("solid", fgColor="F2F2F2")
CENT_ALIGN  = Alignment(horizontal="center", vertical="center")
LEFT_ALIGN  = Alignment(horizontal="left",   vertical="center", wrap_text=True)
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"),  bottom=Side(style="thin"),
)
HEADERS    = [
    "Date", "Produit", "URL", "Taille", "Prix (€)",
    "Portions", "Coût/portion (€)", "Prix/kg (€)", "DDM",
    "Description courte", "Mots-clés",
    # Colonnes nutrition / coût protéine
    "Prix/kg protéine (€)", "Coût/30g protéine (€)",
    "Protéines (g/100g)", "Énergie (kcal/100g)",
    "Glucides (g/100g)", "Lipides (g/100g)", "Sel (g/100g)",
    "Leucine (mg/100g)", "Isoleucine (mg/100g)", "Valine (mg/100g)",
    "Ingrédients", "Profil AA (JSON)",
    # Score qualitatif : seuil anabolique = 3g leucine
    "Coût/3g leucine (€)",
    # Catégorie : Whey / Aliments enrichis / Autres (déduite du % de protéine)
    "Catégorie",
]
COL_WIDTHS = [12, 45, 50, 12, 12, 10, 18, 15, 12, 60, 50,
              18, 18, 16, 18, 16, 16, 14, 16, 16, 14, 80, 60,
              18, 18]


# ── Excel helpers ─────────────────────────────────────────────────────────────
def _style(cell, fill=None, font=None, alignment=None, border=THIN_BORDER):
    if fill:      cell.fill = fill
    if font:      cell.font = font
    if alignment: cell.alignment = alignment
    if border:    cell.border = border


def init_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "Historique"
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22
    for col, (h, w) in enumerate(zip(HEADERS, COL_WIDTHS), start=1):
        c = ws.cell(row=1, column=col, value=h)
        _style(c, fill=HEADER_FILL, font=HEADER_FONT, alignment=CENT_ALIGN)
        ws.column_dimensions[get_column_letter(col)].width = w
    wb.save(EXCEL_PATH)
    return wb


def load_or_create_workbook():
    if not EXCEL_PATH.exists():
        return init_workbook()
    wb = load_workbook(EXCEL_PATH)
    ws = wb["Historique"]
    existing = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    if len(existing) < len(HEADERS):
        for col, h in enumerate(HEADERS[len(existing):], start=len(existing) + 1):
            c = ws.cell(row=1, column=col, value=h)
            _style(c, fill=HEADER_FILL, font=HEADER_FONT, alignment=CENT_ALIGN)
            ws.column_dimensions[get_column_letter(col)].width = COL_WIDTHS[col - 1]
        wb.save(EXCEL_PATH)
    return wb


def _clean(val):
    if val is None:
        return None
    return str(val).replace("\xa0", " ").replace(",", ".").replace("€", "").strip()


def _to_float(val):
    c = _clean(val)
    if not c:
        return None
    try:
        return float(c)
    except ValueError:
        return None


def append_rows(rows: list):
    wb = load_or_create_workbook()
    ws = wb["Historique"]
    today = date.today().isoformat()
    next_row = ws.max_row + 1
    for i, r in enumerate(rows):
        aa = r.get("amino_acids") or {}
        data = [
            today, r.get("name", ""), r.get("url", ""), r.get("size", ""),
            _to_float(r.get("price")),
            _to_float(r.get("portions")) if r.get("portions") else None,
            _to_float(r.get("cout_portion")),
            _to_float(r.get("px_kg")),
            r.get("ddm", ""),
            r.get("desc_short", ""),
            r.get("keywords", ""),
            # Coût protéine + nutrition
            r.get("px_kg_proteine"),
            r.get("cout_30g_proteine"),
            r.get("proteines_100g"),
            r.get("energie_kcal_100g"),
            r.get("glucides_100g"),
            r.get("lipides_100g"),
            r.get("sel_100g"),
            aa.get("L-Leucine") or aa.get("Leucine"),
            aa.get("L-Isoleucine") or aa.get("Isoleucine"),
            aa.get("L-Valine") or aa.get("Valine"),
            r.get("ingredients", ""),
            json.dumps(aa, ensure_ascii=False) if aa else "",
            r.get("cout_3g_leucine"),
            r.get("categorie", ""),
        ]
        row_fill = ALT_FILL if i % 2 == 0 else None
        for col, val in enumerate(data, start=1):
            c = ws.cell(row=next_row, column=col, value=val)
            align = LEFT_ALIGN if col in (2, 3) else CENT_ALIGN
            _style(c, fill=row_fill, alignment=align)
            if col == 1:
                c.fill = DATE_FILL
                c.font = Font(bold=True, name="Arial", size=9)
            else:
                c.font = Font(name="Arial", size=9)
        next_row += 1
    wb.save(EXCEL_PATH)


# ── Scraping helpers ──────────────────────────────────────────────────────────
def parse_port_line(text: str) -> dict:
    for line in text.split("\n"):
        m = PORT_RE.search(line)
        if m:
            return {
                "ddm":          m.group(1),
                "portions":     m.group(2),
                "cout_portion": m.group(3),
                "px_kg":        m.group(4),
            }
    return {}


def extract_spconfig(page_source: str):
    m = re.search(
        r'initConfigurableOptions\s*\(\s*[\'"][\d]+[\'"]\s*,\s*(\{)', page_source
    )
    if not m:
        return None
    start = m.start(1)
    depth, end = 0, start
    for i in range(start, min(len(page_source), start + 300_000)):
        if page_source[i] == '{':
            depth += 1
        elif page_source[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    try:
        return json.loads(page_source[start:end + 1])
    except json.JSONDecodeError:
        return None


def build_option_price_map(spconfig: dict) -> dict:
    op = spconfig.get("optionPrices", {})
    mapping = {}
    for attr in spconfig.get("attributes", {}).values():
        for opt in attr.get("options", []):
            opt_id = str(opt.get("id", ""))
            for pid in opt.get("products", []):
                pid_s = str(pid)
                if pid_s in op:
                    fp = op[pid_s].get("finalPrice", {}).get("amount")
                    if fp is not None:
                        mapping[opt_id] = fp
    return mapping


# ── Extraction nutrition / AA / ingrédients ───────────────────────────────────
_NUM_RE = re.compile(r'([\d,\.]+)')
_KCAL_RE = re.compile(r'([\d,\.]+)\s*Kcal', re.IGNORECASE)


def _parse_num(text):
    if not text:
        return None
    t = str(text).strip()
    m = _KCAL_RE.search(t)  # priorité aux Kcal pour la valeur énergétique
    if m:
        return float(m.group(1).replace(",", "."))
    m = _NUM_RE.search(t)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


_TABLES_JS = r"""() => {
    const findHeading = (t) => {
        let p = t.parentElement;
        while (p) {
            const hs = p.querySelectorAll(':scope h1, :scope h2, :scope h3, :scope h4');
            let last = null;
            for (const h of hs) {
                if (t.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_PRECEDING) last = h;
            }
            if (last) return last.innerText.trim();
            p = p.parentElement;
        }
        return '';
    };
    return Array.from(document.querySelectorAll('table')).map(t => ({
        heading: findHeading(t),
        rows: Array.from(t.rows).map(r =>
            Array.from(r.cells).map(c => c.innerText.trim())
        ),
    }));
}"""

_INGREDIENTS_JS = r"""() => {
    const headers = Array.from(document.querySelectorAll('p, h2, h3, h4, span, strong'))
        .filter(el => el.textContent.trim() === 'Ingrédients');
    for (const h of headers) {
        let next = h.nextElementSibling;
        while (next && !next.innerText?.trim()) next = next.nextElementSibling;
        if (next && next.innerText.trim().length > 20) {
            return next.innerText.trim();
        }
    }
    return '';
}"""


def _parse_nutrition(tables):
    """Extrait les valeurs nutritionnelles pour 100g depuis les tables HTML."""
    out = {}
    for tbl in tables:
        if "nutritionnel" not in tbl["heading"].lower():
            continue
        for row in tbl["rows"][1:]:
            if len(row) < 3:
                continue
            label = row[0].lower()
            val = _parse_num(row[2])
            if val is None:
                continue
            if "valeur" in label or "énerg" in label or "energ" in label:
                out["energie_kcal_100g"] = val
            elif "protéin" in label or "protein" in label:
                out["proteines_100g"] = val
            elif "sucre" in label:
                out["sucres_100g"] = val
            elif "glucides" in label or "hydrates" in label or "carbon" in label:
                out["glucides_100g"] = val
            elif "satur" in label:
                out["lipides_satures_100g"] = val
            elif "graisse" in label or "lipide" in label:
                out["lipides_100g"] = val
            elif "sel" in label or "sodium" in label:
                out["sel_100g"] = val
        break
    return out


def _parse_amino_acids(tables):
    """Extrait le profil AA (mg/100g) depuis les tables HTML."""
    out = {}
    for tbl in tables:
        h = tbl["heading"].lower()
        if "acide" not in h or "amin" not in h:
            continue
        for row in tbl["rows"][1:]:
            if len(row) == 1:
                m = re.match(r'(.+?)([\d\.,]+)\s*mg', row[0])
                if m:
                    out[m.group(1).strip()] = float(m.group(2).replace(",", "."))
            elif len(row) >= 2:
                m = re.search(r'([\d\.,]+)\s*mg', row[1])
                if m:
                    out[row[0].strip()] = float(m.group(1).replace(",", "."))
        break
    return out


async def extract_nutrition_data(page) -> dict:
    """Récupère nutrition /100g + acides aminés + ingrédients (1× par produit)."""
    # Scroll pour déclencher le lazy-load des tables
    for _ in range(6):
        await page.evaluate("window.scrollBy(0, 1200)")
        await page.wait_for_timeout(150)

    tables = await page.evaluate(_TABLES_JS)
    ingredients = await page.evaluate(_INGREDIENTS_JS) or ""
    return {
        "nutrition": _parse_nutrition(tables),
        "amino_acids": _parse_amino_acids(tables),
        "ingredients": ingredients,
    }


def _detect_category(prot_per_100g) -> str:
    """Catégorise un produit selon sa teneur en protéines pour 100g.

    - Whey : >= 70% (whey isolat / hydrolysat / concentré pur)
    - Aliments enrichis : 30-70% (mix avec glucides/fibres : oats, smoothies, riz)
    - Autres : < 30% ou inconnu (à investiguer)
    """
    if prot_per_100g is None:
        return ""
    if prot_per_100g >= 70:
        return "Whey"
    if prot_per_100g >= 30:
        return "Aliments enrichis"
    return "Autres"


def _compute_protein_costs(px_kg, prot_per_100g, leucine_mg_per_100g=None):
    """Calcule prix/kg protéine, coût/30g protéine, et coût/3g leucine (seuil anabolique)."""
    if px_kg is None or not prot_per_100g:
        return None, None, None
    ratio = prot_per_100g / 100.0
    if ratio <= 0:
        return None, None, None
    px_kg_prot = round(px_kg / ratio, 2)
    cout_30g = round(px_kg_prot * 0.030, 3)
    # Coût pour atteindre 3g de leucine (seuil de stimulation MPS)
    cout_3g_leu = None
    if leucine_mg_per_100g and leucine_mg_per_100g > 0:
        # px_kg = €/kg produit ; leucine_mg/100g → leucine_g/kg = leucine_mg / 100
        leucine_g_per_kg = leucine_mg_per_100g / 100.0
        cout_3g_leu = round(px_kg / leucine_g_per_kg * 3.0, 3)
    return px_kg_prot, cout_30g, cout_3g_leu


async def dismiss_cookie_popup(page) -> None:
    try:
        await page.click('#didomi-notice-agree-button', timeout=3_000)
        await page.wait_for_timeout(600)
    except Exception:
        await page.evaluate("""() => {
            document.getElementById('didomi-host')?.remove();
            document.querySelector('.didomi-popup-backdrop')?.remove();
        }""")


async def get_product_urls(page, category_urls: list) -> list:
    seen, urls = set(), []
    for cat_url in category_urls:
        try:
            await page.goto(cat_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            try:
                await page.wait_for_selector('a.product-item-link, .product-item-info a', timeout=8_000)
            except PlaywrightTimeout:
                pass
            await dismiss_cookie_popup(page)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)
            links = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('a.product-item-link, .product-item-info a'))
                    .map(a => a.href)
                    .filter(h => h.includes('/marques/') ||
                                 (h.includes('/proteines/') && h.split('/').length > 5))
            """)
            added = 0
            for link in links:
                clean = link.split("?")[0].rstrip("/")
                if clean not in seen:
                    seen.add(clean)
                    urls.append(link.split("?")[0])
                    added += 1
            print(f"  {cat_url.split('fr/')[-1]:50} +{added}")
        except Exception as e:
            print(f"  Erreur {cat_url}: {e}")
    return urls


SIZE_EXCLUDE_RE = re.compile(r'monodose|pack', re.IGNORECASE)
SIZE_KG_RE = re.compile(r'(\d+(?:[\.,]\d+)?)\s*([Kk]?[Gg])')


def _parse_size_kg(label: str):
    """Parse '500g' / '1Kg' / '2Kg' → poids en kg (float)."""
    if not label:
        return None
    m = SIZE_KG_RE.match(label.strip())
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    return val if unit.startswith("k") else val / 1000


def _enrich_row(row: dict, nutri: dict) -> dict:
    """Ajoute nutrition + acides aminés + coût protéine à une ligne."""
    n = nutri.get("nutrition", {})
    aa = nutri.get("amino_acids", {})
    row["amino_acids"] = aa
    row["ingredients"] = nutri.get("ingredients", "")
    row.update(n)
    px_kg = _to_float(row.get("px_kg"))
    leucine = aa.get("L-Leucine") or aa.get("Leucine")
    px_kg_prot, cout_30g, cout_3g_leu = _compute_protein_costs(
        px_kg, n.get("proteines_100g"), leucine
    )
    row["px_kg_proteine"] = px_kg_prot
    row["cout_30g_proteine"] = cout_30g
    row["cout_3g_leucine"] = cout_3g_leu
    row["categorie"] = _detect_category(n.get("proteines_100g"))

    # Sanity check : un whey doit avoir entre PROT_MIN_PCT et PROT_MAX_PCT de protéines
    prot = n.get("proteines_100g")
    if prot is not None and not (PROT_MIN_PCT <= prot <= PROT_MAX_PCT):
        log_error(
            row.get("url", "?"),
            f"Protéine suspecte : {prot}g/100g hors [{PROT_MIN_PCT};{PROT_MAX_PCT}] "
            f"(taille={row.get('size','?')}) — possible erreur de parsing"
        )
    return row


async def scrape_product(page, url: str) -> list:
    results = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        try:
            await page.wait_for_selector('h1', timeout=10_000)
        except PlaywrightTimeout:
            pass
        await dismiss_cookie_popup(page)

        name = await page.evaluate(
            "document.querySelector('h1.page-title span, h1[itemprop=\"name\"]')"
            "?.innerText?.trim() || document.querySelector('h1')?.innerText?.trim() || ''"
        )

        html = await page.content()
        spconfig = extract_spconfig(html)
        option_price_map = build_option_price_map(spconfig) if spconfig else {}

        # Nutrition / AA / ingrédients : 1× par produit (identique sur toutes les tailles)
        nutri = await extract_nutrition_data(page)

        # Méthode legacy : inputs super_attribute (radio/checkbox)
        sizes = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('input[name*="super_attribute"]')).map(i => ({
                kind: 'input',
                value: i.value,
                id: i.id,
                label: document.querySelector('label[for="' + i.id + '"]')?.innerText?.trim()
            })).filter(s => s.label)
        """)

        # Méthode actuelle (HSN ~2025+) : <select id="selectProductSimple"> avec
        # options du type "EVOCLEAR HYDRO 1Kg ANANAS" — on groupe par taille
        if not sizes:
            variants = await page.evaluate(r"""() =>
                Array.from(document.querySelectorAll('#selectProductSimple option, select.select-product option'))
                    .map(o => ({sku: o.value, text: (o.textContent || '').trim()}))
                    .filter(o => o.sku && o.text && !/Sélectionnez/i.test(o.text))
            """)
            size_re = re.compile(r'(\d+(?:[\.,]\d+)?\s*[Kk]?[Gg])')
            by_size = {}
            for v in variants:
                # Exclure les variantes Monodose / Pack au niveau du texte complet
                if SIZE_EXCLUDE_RE.search(v["text"]):
                    continue
                m = size_re.search(v["text"])
                if not m:
                    continue
                sz_label = m.group(1).strip().replace(" ", "")  # "1 Kg" → "1Kg"
                if sz_label not in by_size:
                    by_size[sz_label] = v["sku"]
            sizes = [
                {"kind": "select", "sku": sku, "label": sz}
                for sz, sku in by_size.items()
            ]

        # Exclure les tailles Monodose / Pack
        sizes = [s for s in sizes if not SIZE_EXCLUDE_RE.search(s["label"])]

        if not sizes:
            text = await page.evaluate("document.body.innerText")
            port_data = parse_port_line(text)
            price_raw = await page.evaluate(
                "document.querySelector('[data-price-type=\"finalPrice\"] .price')?.innerText || ''"
            )
            row = {
                "name": name, "url": url, "size": "Unique",
                "price": _clean(price_raw), **port_data,
            }
            results.append(_enrich_row(row, nutri))
            return results

        for sz in sizes:
            price_str = None
            if sz.get("kind") == "input":
                # Legacy : click sur label
                price_amount = option_price_map.get(sz["value"])
                try:
                    await page.click(f'label[for="{sz["id"]}"]')
                    await page.wait_for_timeout(CLICK_WAIT)
                except Exception:
                    pass
                if price_amount is not None:
                    price_str = f"{price_amount:.2f}"
            else:
                # Select : prix depuis spconfig.optionPrices (keyed by SKU)
                if spconfig:
                    op = spconfig.get("optionPrices", {})
                    fp = op.get(str(sz["sku"]), {}).get("finalPrice", {}).get("amount")
                    if fp is not None:
                        price_str = f"{fp:.2f}"

            # Port data : seulement fiable après un click (legacy).
            # Pour les selects, on calcule px_kg nous-mêmes depuis prix/poids.
            if sz.get("kind") == "select":
                size_kg = _parse_size_kg(sz["label"])
                px_val = _to_float(price_str)
                port_data = {}
                if px_val is not None and size_kg:
                    px_kg_val = px_val / size_kg
                    port_data["px_kg"] = f"{px_kg_val:.2f} €"
            else:
                text = await page.evaluate("document.body.innerText")
                port_data = parse_port_line(text)

            row = {
                "name": name, "url": url, "size": sz["label"],
                "price": price_str, **port_data,
            }
            results.append(_enrich_row(row, nutri))
            print(
                f"    {sz['label']:12} | {(price_str or '?'):>8} EUR | "
                f"PORT:{port_data.get('portions','?'):>4} | "
                f"PROT:{nutri['nutrition'].get('proteines_100g','?')}g | "
                f"PX/KG-PROT:{row.get('px_kg_proteine','?')}"
            )

    except PlaywrightTimeout:
        print(f"  Timeout : {url}")
    except Exception as e:
        print(f"  Erreur : {url} — {e}")

    return results


# ── Dashboard HTML ────────────────────────────────────────────────────────────
def generate_dashboard(rows=None):
    """Regenerate whey_dashboard.html from current Excel data (or provided rows)."""
    if rows is None:
        wb = load_or_create_workbook()
        ws = wb["Historique"]
        hdrs = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        rows = [
            dict(zip(hdrs, r))
            for r in ws.iter_rows(min_row=2, values_only=True)
            if any(v is not None for v in r)
        ]

    # Construit l'historique complet par (produit, taille) pour les tendances
    from collections import defaultdict
    history_by_key = defaultdict(list)
    latest = {}
    for r in rows:
        size = str(r.get("Taille", ""))
        if not size or "Pack" in size or "Monodose" in size:
            continue
        if r.get("Prix/kg (€)") is None:
            continue
        key = (str(r.get("Produit", "")), size)
        rdate = str(r.get("Date", ""))
        history_by_key[key].append({
            "date": rdate,
            "pxkg": r.get("Prix/kg (€)"),
            "pxkgProt": r.get("Prix/kg protéine (€)"),
            "cout3leu": r.get("Cout/3g leucine (€)") or r.get("Coût/3g leucine (€)"),
        })
        if key not in latest or rdate >= str(latest[key].get("Date", "")):
            latest[key] = r

    # Calcule moyenne historique (hors dernière date) + flag deal
    deal_meta = {}
    for key, hist in history_by_key.items():
        hist.sort(key=lambda x: x["date"])
        latest_date = hist[-1]["date"] if hist else None
        previous_pxp = [h["pxkgProt"] for h in hist if h["date"] != latest_date and h["pxkgProt"] is not None]
        avg = (sum(previous_pxp) / len(previous_pxp)) if previous_pxp else None
        cur = hist[-1]["pxkgProt"] if hist and hist[-1]["pxkgProt"] is not None else None
        is_deal = bool(avg and cur and cur < avg * 0.95)  # -5% sous la moyenne
        deal_meta[key] = {"avg": avg, "isDeal": is_deal, "histLen": len(hist)}

    chart_rows = []
    for r in latest.values():
        key = (str(r.get("Produit", "")), str(r.get("Taille", "")))
        meta = deal_meta.get(key, {})
        chart_rows.append({
            "date":     str(r.get("Date", "")),
            "produit":  str(r.get("Produit", "")),
            "taille":   str(r.get("Taille", "")),
            "prix":     r.get("Prix (€)"),
            "pxkg":     r.get("Prix/kg (€)"),
            "cout":     r.get("Cout/portion (€)") or r.get("Coût/portion (€)"),
            "portions": r.get("Portions"),
            "ddm":      str(r.get("DDM") or ""),
            "url":      str(r.get("URL", "")),
            "pxkgProt": r.get("Prix/kg protéine (€)"),
            "cout30":   r.get("Cout/30g protéine (€)") or r.get("Coût/30g protéine (€)"),
            "prot":     r.get("Protéines (g/100g)"),
            "cout3leu": r.get("Cout/3g leucine (€)") or r.get("Coût/3g leucine (€)"),
            "avgPxkgProt": meta.get("avg"),
            "isDeal":      meta.get("isDeal", False),
            "histLen":     meta.get("histLen", 0),
            "categorie":   str(r.get("Catégorie") or _detect_category(r.get("Protéines (g/100g)"))),
        })

    # Sérialise l'historique pour les courbes de tendance
    # Catégorie reprise depuis le snapshot le plus récent (chart_rows / latest)
    cat_by_key = {(c["produit"], c["taille"]): c["categorie"] for c in chart_rows}
    history_json_data = [
        {
            "produit":   k[0],
            "taille":    k[1],
            "categorie": cat_by_key.get(k, ""),
            "points":    v,
        }
        for k, v in history_by_key.items()
        if len(v) >= 2  # courbe seulement si au moins 2 points
    ]

    data_json = json.dumps(chart_rows, ensure_ascii=False, default=str)
    history_json = json.dumps(history_json_data, ensure_ascii=False, default=str)
    today_str = date.today().strftime("%d/%m/%Y")

    # Collect unique dates for trend data
    dates = sorted(set(r["date"] for r in chart_rows))
    all_products = list(dict.fromkeys(r["produit"] for r in chart_rows))

    html = (
        "<!DOCTYPE html>\n"
        "<html lang='fr'>\n"
        "<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
        f"<title>HSN Whey Tracker — {today_str}</title>\n"
        "<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js'></script>\n"
        "<style>\n"
        "*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }\n"
        "body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5;"
        " color: #222; padding: 24px; }\n"
        "h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }\n"
        ".sub { font-size: 12px; color: #888; margin-bottom: 20px; }\n"
        ".cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }\n"
        ".card { background: #fff; border-radius: 10px; padding: 16px 20px; flex: 1;"
        " min-width: 150px; border: 0.5px solid #e0e0e0; }\n"
        ".card-label { font-size: 12px; color: #888; margin-bottom: 4px; }\n"
        ".card-value { font-size: 22px; font-weight: 600; }\n"
        ".card-sub { font-size: 11px; color: #888; margin-top: 2px; }\n"
        ".best { color: #0F6E56; }\n"
        ".section-title { font-size: 13px; font-weight: 600; color: #555; margin-bottom: 8px; }\n"
        ".chart-wrap { background: #fff; border-radius: 10px; padding: 20px;"
        " margin-bottom: 20px; border: 0.5px solid #e0e0e0; }\n"
        ".filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }\n"
        ".filter-btn { font-size: 12px; padding: 5px 16px; border-radius: 20px;"
        " border: 1.5px solid #ccc; background: #fff; color: #555; cursor: pointer; }\n"
        ".filter-btn.active { border-color: #378ADD; background: #E6F1FB; color: #0C447C; }\n"
        "table { width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed; }\n"
        "th { background: #f0f0f0; padding: 8px 12px; text-align: left; font-weight: 500;"
        " color: #555; border-bottom: 1px solid #e0e0e0; }\n"
        "td { padding: 7px 12px; border-bottom: 0.5px solid #eee;"
        " overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n"
        "tr:nth-child(even) { background: #fafafa; }\n"
        ".best-cell { color: #0F6E56; font-weight: 600; }\n"
        "a { color: #185FA5; text-decoration: none; }\n"
        ".legend { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 10px;"
        " font-size: 12px; color: #666; }\n"
        ".legend-dot { width: 10px; height: 10px; border-radius: 2px;"
        " display: inline-block; margin-right: 4px; vertical-align: middle; }\n"
        ".table-toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }\n"
        ".search-input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px;"
        " font-size: 12px; width: 240px; }\n"
        "th.sortable { cursor: pointer; user-select: none; }\n"
        "th.sortable:hover { background: #e8e8e8; }\n"
        "th.sortable .sort-arrow { font-size: 10px; opacity: 0.4; margin-left: 3px; }\n"
        "th.sortable.sort-asc .sort-arrow, th.sortable.sort-desc .sort-arrow { opacity: 1; color: #378ADD; }\n"
        ".deal-badge { display: inline-block; background: #FF6B35; color: #fff;"
        " font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 3px;"
        " margin-left: 6px; vertical-align: middle; letter-spacing: 0.3px; }\n"
        ".trend-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;"
        " margin-bottom: 12px; }\n"
        ".trend-controls select { padding: 5px 8px; border: 1px solid #ccc; border-radius: 6px;"
        " font-size: 12px; max-width: 360px; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>HSN Whey — Suivi des prix</h1>\n"
        f"<p class='sub'>Derniere mise a jour : {today_str} &nbsp;|&nbsp; "
        f"{len(all_products)} produits &nbsp;|&nbsp; {len(dates)} jour(s) de donnees</p>\n"
        "<div class='cards' id='metricCards'></div>\n"
        "<div class='filters' id='categoryFilters'></div>\n"
        "<div class='filters' id='filters'></div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Prix par kilo de protéine pure (EUR/kg)</div>\n"
        "  <div class='legend' id='legendPxkgProt'></div>\n"
        "  <div style='position:relative;height:300px;'><canvas id='chartPxkgProt'></canvas></div>\n"
        "</div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Coût pour 30g de protéine (EUR)</div>\n"
        "  <div class='legend' id='legendCout30'></div>\n"
        "  <div style='position:relative;height:300px;'><canvas id='chartCout30'></canvas></div>\n"
        "</div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Prix au kilo de produit (EUR/kg)</div>\n"
        "  <div class='legend' id='legendPxkg'></div>\n"
        "  <div style='position:relative;height:300px;'><canvas id='chartPxkg'></canvas></div>\n"
        "</div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Évolution dans le temps (EUR/kg protéine)</div>\n"
        "  <div class='trend-controls'>\n"
        "    <label style='font-size:12px;color:#555'>Produit&nbsp;:</label>\n"
        "    <select id='trendSelect'></select>\n"
        "    <span id='trendInfo' style='font-size:11px;color:#888'></span>\n"
        "  </div>\n"
        "  <div style='position:relative;height:280px;'><canvas id='chartTrend'></canvas></div>\n"
        "</div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Données complètes</div>\n"
        "  <div class='table-toolbar'>\n"
        "    <input id='searchInput' class='search-input' type='text' placeholder='Rechercher un produit...'>\n"
        "    <span id='rowCount' style='font-size:11px;color:#888'></span>\n"
        "  </div>\n"
        "  <table id='detailTable'><thead><tr>\n"
        "    <th class='sortable' data-key='produit' style='width:26%'>Produit<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='taille' style='width:7%'>Taille<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='prix' style='width:7%;text-align:right'>Prix<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='prot' style='width:6%;text-align:right'>%Prot<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='pxkgProt' style='width:11%;text-align:right'>EUR/kg prot<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='cout30' style='width:11%;text-align:right'>EUR/30g prot<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='cout3leu' style='width:11%;text-align:right' title='Coût pour 3g de leucine (seuil anabolique)'>EUR/3g leu<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='pxkg' style='width:8%;text-align:right'>EUR/kg<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='cout' style='width:8%;text-align:right'>EUR/portion<span class='sort-arrow'>↕</span></th>\n"
        "    <th class='sortable' data-key='date' style='width:5%;text-align:right'>Date<span class='sort-arrow'>↕</span></th>\n"
        "  </tr></thead><tbody id='tableBody'></tbody></table>\n"
        "</div>\n"
        f"<script>\nconst RAW = {data_json};\n"
        f"const HISTORY = {history_json};\n"
        "const COLORS = {'500g':'#378ADD','750g':'#1D9E75','2Kg':'#7F77DD','Unique':'#D85A30'};\n"
        "const CAT_ORDER = ['Whey','Aliments enrichis','Autres'];\n"
        "const CATEGORIES = CAT_ORDER.filter(c=>RAW.some(r=>r.categorie===c)).concat([...new Set(RAW.map(r=>r.categorie).filter(c=>c && !CAT_ORDER.includes(c)))]);\n"
        "const PRODUCTS = [...new Set(RAW.map(r=>r.produit))];\n"
        "const SIZES = [...new Set(RAW.map(r=>r.taille))];\n"
        "let currentSize = 'all';\n"
        "let currentCategory = 'Whey';\n"  # default: Whey only (purist view)
        "let searchQuery = '';\n"
        "let sortKey = 'pxkgProt', sortAsc = true;\n"
        "let chartPxkgProt, chartCout30, chartPxkg, chartTrend;\n"
        "function fmt(v,d=2,suf=' EUR'){return v!=null?Number(v).toFixed(d)+suf:'—';}\n"
        "function getFiltered(){\n"
        "  let f = RAW;\n"
        "  if(currentCategory!=='all'){f=f.filter(r=>r.categorie===currentCategory);}\n"
        "  if(currentSize!=='all'){f=f.filter(r=>r.taille===currentSize);}\n"
        "  if(searchQuery){const q=searchQuery.toLowerCase();f=f.filter(r=>r.produit.toLowerCase().includes(q));}\n"
        "  return f;\n"
        "}\n"
        "function shortName(p){return p.length>32?p.slice(0,30)+'…':p;}\n"
        "function getFilteredProducts(){return [...new Set(getFiltered().map(r=>r.produit))];}\n"
        "function buildDatasets(key){\n"
        "  const products=getFilteredProducts();\n"
        "  const sizes=currentSize==='all'?SIZES:[currentSize];\n"
        "  return sizes.map(sz=>({\n"
        "    label:sz,\n"
        "    data:products.map(pr=>{const r=RAW.find(x=>x.produit===pr&&x.taille===sz&&(currentCategory==='all'||x.categorie===currentCategory));return r?r[key]:null;}),\n"
        "    backgroundColor:COLORS[sz]||'#888',borderRadius:4,borderSkipped:false\n"
        "  }));\n"
        "}\n"
        "const COPTS=(unit=' EUR',d=2)=>({\n"
        "  responsive:true,maintainAspectRatio:false,\n"
        "  plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label}: ${ctx.parsed.y!=null?Number(ctx.parsed.y).toFixed(d)+unit:'N/A'}`}}},\n"
        "  scales:{x:{grid:{display:false},ticks:{font:{size:11},maxRotation:35}},\n"
        "          y:{grid:{color:'rgba(0,0,0,0.06)'},ticks:{font:{size:11},callback:v=>Number(v).toFixed(d)+unit},beginAtZero:false}}\n"
        "});\n"
        "function buildLegend(id){\n"
        "  const sizes=currentSize==='all'?SIZES:[currentSize];\n"
        "  document.getElementById(id).innerHTML=sizes.map(sz=>`<span><span class='legend-dot' style='background:${COLORS[sz]||\"#888\"}'></span>${sz}</span>`).join('');\n"
        "}\n"
        "function initCharts(){\n"
        "  const labels=getFilteredProducts().map(shortName);\n"
        "  chartPxkgProt=new Chart(document.getElementById('chartPxkgProt'),{type:'bar',data:{labels,datasets:buildDatasets('pxkgProt')},options:COPTS()});\n"
        "  chartCout30=new Chart(document.getElementById('chartCout30'),{type:'bar',data:{labels,datasets:buildDatasets('cout30')},options:COPTS(' EUR',3)});\n"
        "  chartPxkg=new Chart(document.getElementById('chartPxkg'),{type:'bar',data:{labels,datasets:buildDatasets('pxkg')},options:COPTS()});\n"
        "  buildLegend('legendPxkgProt');buildLegend('legendCout30');buildLegend('legendPxkg');\n"
        "}\n"
        "function updateCharts(){\n"
        "  const labels=getFilteredProducts().map(shortName);\n"
        "  [chartPxkgProt,chartCout30,chartPxkg].forEach((c,i)=>{\n"
        "    c.data.labels=labels;\n"
        "    c.data.datasets=buildDatasets(['pxkgProt','cout30','pxkg'][i]);\n"
        "    c.update();\n"
        "  });\n"
        "  buildLegend('legendPxkgProt');buildLegend('legendCout30');buildLegend('legendPxkg');\n"
        "}\n"
        "function buildCategoryFilters(){\n"
        "  const cats=['all',...CATEGORIES];\n"
        "  document.getElementById('categoryFilters').innerHTML=cats.map(c=>`<button class='filter-btn ${c===currentCategory?\"active\":\"\"}' onclick='filterCategory(\"${c}\")'>${c==='all'?'Toutes catégories':c}</button>`).join('');\n"
        "}\n"
        "function filterCategory(c){currentCategory=c;buildCategoryFilters();updateCharts();updateTable();buildTrendOptions();buildTrendChart();}\n"
        "function compareValues(a,b,k){\n"
        "  const av=a[k], bv=b[k];\n"
        "  if(av==null && bv==null) return 0;\n"
        "  if(av==null) return 1;\n"
        "  if(bv==null) return -1;\n"
        "  if(typeof av === 'number' && typeof bv === 'number') return av-bv;\n"
        "  return String(av).localeCompare(String(bv));\n"
        "}\n"
        "function updateTable(){\n"
        "  let f=getFiltered();\n"
        "  f=[...f].sort((a,b)=>{const c=compareValues(a,b,sortKey);return sortAsc?c:-c;});\n"
        "  const mnPxP=Math.min(...f.filter(r=>r.pxkgProt).map(r=>r.pxkgProt));\n"
        "  const mnC30=Math.min(...f.filter(r=>r.cout30).map(r=>r.cout30));\n"
        "  const mnC3L=Math.min(...f.filter(r=>r.cout3leu).map(r=>r.cout3leu));\n"
        "  document.getElementById('rowCount').textContent=`${f.length} ligne(s)`;\n"
        "  document.getElementById('tableBody').innerHTML=f.map((r,i)=>{\n"
        "    const dealBadge = r.isDeal ? `<span class='deal-badge' title='Sous la moyenne historique de plus de 5%'>🔥 DEAL</span>` : '';\n"
        "    return `<tr>\n"
        "    <td title='${r.produit}'><a href='${r.url}' target='_blank'>${r.produit}</a>${dealBadge}</td>\n"
        "    <td>${r.taille}</td>\n"
        "    <td style='text-align:right'>${fmt(r.prix)}</td>\n"
        "    <td style='text-align:right'>${r.prot!=null?Number(r.prot).toFixed(0)+'%':'—'}</td>\n"
        "    <td style='text-align:right' class='${r.pxkgProt===mnPxP?\"best-cell\":\"\"}'>${fmt(r.pxkgProt)}</td>\n"
        "    <td style='text-align:right' class='${r.cout30===mnC30?\"best-cell\":\"\"}'>${fmt(r.cout30,3)}</td>\n"
        "    <td style='text-align:right' class='${r.cout3leu===mnC3L?\"best-cell\":\"\"}'>${fmt(r.cout3leu,3)}</td>\n"
        "    <td style='text-align:right'>${fmt(r.pxkg)}</td>\n"
        "    <td style='text-align:right'>${fmt(r.cout)}</td>\n"
        "    <td style='text-align:right;font-size:11px;color:#888'>${r.date}</td>\n"
        "    </tr>`;\n"
        "  }).join('');\n"
        "}\n"
        "function updateSortHeaders(){\n"
        "  document.querySelectorAll('th.sortable').forEach(th=>{\n"
        "    th.classList.remove('sort-asc','sort-desc');\n"
        "    if(th.dataset.key===sortKey){th.classList.add(sortAsc?'sort-asc':'sort-desc');\n"
        "      th.querySelector('.sort-arrow').textContent=sortAsc?'↑':'↓';}\n"
        "    else{th.querySelector('.sort-arrow').textContent='↕';}\n"
        "  });\n"
        "}\n"
        "function setupSort(){\n"
        "  document.querySelectorAll('th.sortable').forEach(th=>{\n"
        "    th.addEventListener('click',()=>{\n"
        "      const k=th.dataset.key;\n"
        "      if(sortKey===k){sortAsc=!sortAsc;}else{sortKey=k;sortAsc=true;}\n"
        "      updateSortHeaders();updateTable();\n"
        "    });\n"
        "  });\n"
        "}\n"
        "function setupSearch(){\n"
        "  document.getElementById('searchInput').addEventListener('input',e=>{\n"
        "    searchQuery=e.target.value.trim();updateTable();\n"
        "  });\n"
        "}\n"
        "function buildTrendOptions(){\n"
        "  const sel=document.getElementById('trendSelect');\n"
        "  const filtered=HISTORY.map((h,i)=>({h,i})).filter(x=>currentCategory==='all'||x.h.categorie===currentCategory);\n"
        "  sel.innerHTML=filtered.map(({h,i})=>`<option value='${i}'>${h.produit} — ${h.taille} (${h.points.length} pts)</option>`).join('');\n"
        "  if(filtered.length===0){sel.innerHTML='<option>Pas assez de données (besoin de 2+ jours)</option>';}\n"
        "}\n"
        "function buildTrendChart(){\n"
        "  if(HISTORY.length===0){return;}\n"
        "  const idx=parseInt(document.getElementById('trendSelect').value)||0;\n"
        "  const h=HISTORY[idx];\n"
        "  if(!h){return;}\n"
        "  const labels=h.points.map(p=>p.date);\n"
        "  const data=h.points.map(p=>p.pxkgProt);\n"
        "  document.getElementById('trendInfo').textContent=`${h.produit} ${h.taille} — ${h.points.length} relevés`;\n"
        "  if(chartTrend){chartTrend.destroy();}\n"
        "  chartTrend=new Chart(document.getElementById('chartTrend'),{\n"
        "    type:'line',\n"
        "    data:{labels,datasets:[{label:'EUR/kg protéine',data,borderColor:'#378ADD',backgroundColor:'rgba(55,138,221,0.1)',tension:0.2,pointRadius:4,fill:true}]},\n"
        "    options:COPTS()\n"
        "  });\n"
        "}\n"
        "function setupTrend(){\n"
        "  document.getElementById('trendSelect').addEventListener('change',buildTrendChart);\n"
        "}\n"
        "function buildMetrics(){\n"
        "  const wpp=RAW.filter(r=>r.pxkgProt);\n"
        "  const wc30=RAW.filter(r=>r.cout30);\n"
        "  const w3l=RAW.filter(r=>r.cout3leu);\n"
        "  const wpx=RAW.filter(r=>r.pxkg);\n"
        "  const bestProt=wpp.length?wpp.reduce((a,b)=>a.pxkgProt<b.pxkgProt?a:b):null;\n"
        "  const bestC30=wc30.length?wc30.reduce((a,b)=>a.cout30<b.cout30?a:b):null;\n"
        "  const best3L=w3l.length?w3l.reduce((a,b)=>a.cout3leu<b.cout3leu?a:b):null;\n"
        "  const bestPx=wpx.length?wpx.reduce((a,b)=>a.pxkg<b.pxkg?a:b):null;\n"
        "  const prods=new Set(RAW.map(r=>r.produit)).size;\n"
        "  const cards=[];\n"
        "  cards.push(`<div class='card'><div class='card-label'>Produits</div><div class='card-value'>${prods}</div></div>`);\n"
        "  if(bestProt){cards.push(`<div class='card'><div class='card-label'>Meilleur EUR/kg protéine</div><div class='card-value best'>${bestProt.pxkgProt.toFixed(2)} EUR</div><div class='card-sub'>${bestProt.produit.slice(0,28)} — ${bestProt.taille}</div></div>`);}\n"
        "  if(bestC30){cards.push(`<div class='card'><div class='card-label'>Meilleur 30g protéine</div><div class='card-value best'>${bestC30.cout30.toFixed(3)} EUR</div><div class='card-sub'>${bestC30.produit.slice(0,28)} — ${bestC30.taille}</div></div>`);}\n"
        "  if(best3L){cards.push(`<div class='card'><div class='card-label' title='Coût pour 3g de leucine (seuil anabolique)'>Meilleur 3g leucine</div><div class='card-value best'>${best3L.cout3leu.toFixed(3)} EUR</div><div class='card-sub'>${best3L.produit.slice(0,28)} — ${best3L.taille}</div></div>`);}\n"
        "  if(bestPx){cards.push(`<div class='card'><div class='card-label'>Meilleur EUR/kg produit</div><div class='card-value' style='font-size:18px'>${bestPx.pxkg.toFixed(2)} EUR</div><div class='card-sub'>${bestPx.produit.slice(0,28)} — ${bestPx.taille}</div></div>`);}\n"
        f"  cards.push(`<div class='card'><div class='card-label'>Mise à jour</div><div class='card-value' style='font-size:16px'>{today_str}</div></div>`);\n"
        "  document.getElementById('metricCards').innerHTML=cards.join('');\n"
        "}\n"
        "function buildFilters(){\n"
        "  document.getElementById('filters').innerHTML=['all',...SIZES].map(sz=>`\n"
        "    <button class='filter-btn ${sz===currentSize?\"active\":\"\"}'"
        " onclick='filterSize(\"${sz}\")'>${sz==='all'?'Toutes':sz}</button>`).join('');\n"
        "}\n"
        "function filterSize(sz){currentSize=sz;buildFilters();updateCharts();updateTable();buildTrendOptions();buildTrendChart();}\n"
        "buildMetrics();buildCategoryFilters();buildFilters();initCharts();\n"
        "buildTrendOptions();buildTrendChart();setupTrend();\n"
        "setupSort();setupSearch();updateSortHeaders();updateTable();\n"
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )

    dash_path = EXCEL_PATH.parent / "whey_dashboard.html"
    dash_path.write_text(html, encoding="utf-8")
    print(f"Dashboard : {dash_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print(f"\n{'='*62}")
    print(f"  HSN Whey Tracker — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*62}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        # Bloque les ressources inutiles pour aller plus vite
        await context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "font", "media"}
            else route.continue_(),
        )
        page = await context.new_page()

        print("\nCollecte des URLs produits...")
        product_urls = await get_product_urls(page, CATEGORY_URLS)
        # Inclure les URLs supplémentaires (déduplication)
        seen = {u.split("?")[0].rstrip("/") for u in product_urls}
        for u in EXTRA_URLS:
            if u.split("?")[0].rstrip("/") not in seen:
                product_urls.append(u)
        print(f"\n  {len(product_urls)} produits uniques\n")
        await page.close()

        sem = asyncio.Semaphore(CONCURRENCY)
        total = len(product_urls)

        async def worker(idx: int, url: str):
            async with sem:
                short = url.split("/")[-1]
                print(f"[{idx:02d}/{total}] {short}")
                last_err = None
                for attempt in range(RETRY_ATTEMPTS + 1):
                    worker_page = await context.new_page()
                    try:
                        rows = await scrape_product(worker_page, url)
                        if rows:
                            return rows
                        last_err = "résultat vide"
                    except Exception as e:
                        last_err = repr(e)
                    finally:
                        await worker_page.close()
                    if attempt < RETRY_ATTEMPTS:
                        print(f"   ↻ retry {short} ({last_err})")
                        await asyncio.sleep(RETRY_DELAY_MS / 1000)
                log_error(url, f"Échec après {RETRY_ATTEMPTS + 1} tentative(s) : {last_err}")
                return []

        batches = await asyncio.gather(
            *(worker(i, u) for i, u in enumerate(product_urls, 1))
        )
        all_rows = [r for batch in batches for r in batch]

        await browser.close()

    if all_rows:
        print(f"\nEnregistrement de {len(all_rows)} lignes dans Excel...")
        append_rows(all_rows)
        print(f"Excel OK : {EXCEL_PATH}")
        print("Generation du dashboard HTML...")
        generate_dashboard()
        print(f"Termine ! {len(all_rows)} entrees pour le {date.today().isoformat()}")
    else:
        print("Aucune donnee collectee.")

    return all_rows


if __name__ == "__main__":
    asyncio.run(main())
