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
HEADERS    = ["Date", "Produit", "URL", "Taille", "Prix (€)",
              "Portions", "Coût/portion (€)", "Prix/kg (€)", "DDM",
              "Description courte", "Mots-clés"]
COL_WIDTHS = [12, 45, 50, 12, 12, 10, 18, 15, 12, 60, 50]


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
    return load_workbook(EXCEL_PATH) if EXCEL_PATH.exists() else init_workbook()


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
        data = [
            today, r.get("name", ""), r.get("url", ""), r.get("size", ""),
            _to_float(r.get("price")),
            _to_float(r.get("portions")) if r.get("portions") else None,
            _to_float(r.get("cout_portion")),
            _to_float(r.get("px_kg")),
            r.get("ddm", ""),
            r.get("desc_short", ""),
            r.get("keywords", ""),
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

        sizes = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('input[name*="super_attribute"]')).map(i => ({
                value: i.value,
                id: i.id,
                label: document.querySelector('label[for="' + i.id + '"]')?.innerText?.trim()
            })).filter(s => s.label)
        """)

        if not sizes:
            text = await page.evaluate("document.body.innerText")
            port_data = parse_port_line(text)
            price_raw = await page.evaluate(
                "document.querySelector('[data-price-type=\"finalPrice\"] .price')?.innerText || ''"
            )
            results.append({
                "name": name, "url": url, "size": "Unique",
                "price": _clean(price_raw), **port_data,
            })
            return results

        for sz in sizes:
            price_amount = option_price_map.get(sz["value"])
            try:
                await page.click(f'label[for="{sz["id"]}"]')
                await page.wait_for_timeout(CLICK_WAIT)
            except Exception:
                pass

            text = await page.evaluate("document.body.innerText")
            port_data = parse_port_line(text)
            price_str = f"{price_amount:.2f}" if price_amount is not None else None

            results.append({
                "name": name, "url": url, "size": sz["label"],
                "price": price_str, **port_data,
            })
            print(
                f"    {sz['label']:12} | {(price_str or '?'):>8} EUR | "
                f"PORT:{port_data.get('portions','?'):>4} | "
                f"{port_data.get('cout_portion','?'):>10} | "
                f"{port_data.get('px_kg','?')}"
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

    chart_rows = [
        {
            "date":     str(r.get("Date", "")),
            "produit":  str(r.get("Produit", "")),
            "taille":   str(r.get("Taille", "")),
            "prix":     r.get("Prix (€)"),
            "pxkg":     r.get("Prix/kg (€)"),
            "cout":     r.get("Cout/portion (€)") or r.get("Coût/portion (€)"),
            "portions": r.get("Portions"),
            "ddm":      str(r.get("DDM") or ""),
            "url":      str(r.get("URL", "")),
        }
        for r in rows
        if r.get("Taille") and "Pack" not in str(r.get("Taille", ""))
        and r.get("Prix/kg (€)") is not None
    ]

    data_json = json.dumps(chart_rows, ensure_ascii=False, default=str)
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
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>HSN Whey — Suivi des prix</h1>\n"
        f"<p class='sub'>Derniere mise a jour : {today_str} &nbsp;|&nbsp; "
        f"{len(all_products)} produits &nbsp;|&nbsp; {len(dates)} jour(s) de donnees</p>\n"
        "<div class='cards' id='metricCards'></div>\n"
        "<div class='filters' id='filters'></div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Prix au kilo (EUR/kg)</div>\n"
        "  <div class='legend' id='legendPxkg'></div>\n"
        "  <div style='position:relative;height:300px;'><canvas id='chartPxkg'></canvas></div>\n"
        "</div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Cout par portion (EUR)</div>\n"
        "  <div class='legend' id='legendCout'></div>\n"
        "  <div style='position:relative;height:300px;'><canvas id='chartCout'></canvas></div>\n"
        "</div>\n"
        "<div class='chart-wrap'>\n"
        "  <div class='section-title'>Donnees completes</div>\n"
        "  <table id='detailTable'><thead><tr>\n"
        "    <th style='width:35%'>Produit</th><th style='width:10%'>Taille</th>\n"
        "    <th style='width:10%;text-align:right'>Prix</th>\n"
        "    <th style='width:10%;text-align:right'>EUR/kg</th>\n"
        "    <th style='width:12%;text-align:right'>EUR/portion</th>\n"
        "    <th style='width:8%;text-align:right'>Portions</th>\n"
        "    <th style='width:15%;text-align:right'>Date</th>\n"
        "  </tr></thead><tbody id='tableBody'></tbody></table>\n"
        "</div>\n"
        f"<script>\nconst RAW = {data_json};\n"
        "const COLORS = {'500g':'#378ADD','750g':'#1D9E75','2Kg':'#7F77DD','Unique':'#D85A30'};\n"
        "const PRODUCTS = [...new Set(RAW.map(r=>r.produit))];\n"
        "const SIZES = [...new Set(RAW.map(r=>r.taille))];\n"
        "let currentSize = 'all';\n"
        "let chartPxkg, chartCout;\n"
        "function fmt(v,d=2){return v!=null?v.toFixed(d)+' EUR':'—';}\n"
        "function getFiltered(){return currentSize==='all'?RAW:RAW.filter(r=>r.taille===currentSize);}\n"
        "function shortName(p){return p.length>32?p.slice(0,30)+'…':p;}\n"
        "function buildDatasets(key){\n"
        "  const sizes=currentSize==='all'?SIZES:[currentSize];\n"
        "  return sizes.map(sz=>({\n"
        "    label:sz,\n"
        "    data:PRODUCTS.map(pr=>{const r=RAW.find(x=>x.produit===pr&&x.taille===sz);return r?r[key]:null;}),\n"
        "    backgroundColor:COLORS[sz]||'#888',borderRadius:4,borderSkipped:false\n"
        "  }));\n"
        "}\n"
        "const COPTS=()=>({\n"
        "  responsive:true,maintainAspectRatio:false,\n"
        "  plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label}: ${ctx.parsed.y!=null?ctx.parsed.y.toFixed(2)+' EUR':'N/A'}`}}},\n"
        "  scales:{x:{grid:{display:false},ticks:{font:{size:11},maxRotation:35}},\n"
        "          y:{grid:{color:'rgba(0,0,0,0.06)'},ticks:{font:{size:11},callback:v=>v.toFixed(2)+' EUR'},beginAtZero:false}}\n"
        "});\n"
        "function buildLegend(id){\n"
        "  const sizes=currentSize==='all'?SIZES:[currentSize];\n"
        "  document.getElementById(id).innerHTML=sizes.map(sz=>`<span><span class='legend-dot' style='background:${COLORS[sz]||\"#888\"}'></span>${sz}</span>`).join('');\n"
        "}\n"
        "function initCharts(){\n"
        "  chartPxkg=new Chart(document.getElementById('chartPxkg'),{type:'bar',data:{labels:PRODUCTS.map(shortName),datasets:buildDatasets('pxkg')},options:COPTS()});\n"
        "  chartCout=new Chart(document.getElementById('chartCout'),{type:'bar',data:{labels:PRODUCTS.map(shortName),datasets:buildDatasets('cout')},options:COPTS()});\n"
        "  buildLegend('legendPxkg');buildLegend('legendCout');\n"
        "}\n"
        "function updateCharts(){\n"
        "  chartPxkg.data.datasets=buildDatasets('pxkg');chartCout.data.datasets=buildDatasets('cout');\n"
        "  chartPxkg.update();chartCout.update();buildLegend('legendPxkg');buildLegend('legendCout');\n"
        "}\n"
        "function updateTable(){\n"
        "  const f=getFiltered();\n"
        "  const mnPx=Math.min(...f.filter(r=>r.pxkg).map(r=>r.pxkg));\n"
        "  const mnCo=Math.min(...f.filter(r=>r.cout).map(r=>r.cout));\n"
        "  document.getElementById('tableBody').innerHTML=f.map((r,i)=>`\n"
        "    <tr><td title='${r.produit}'><a href='${r.url}' target='_blank'>${r.produit}</a></td>\n"
        "    <td>${r.taille}</td>\n"
        "    <td style='text-align:right'>${fmt(r.prix)}</td>\n"
        "    <td style='text-align:right' class='${r.pxkg===mnPx?\"best-cell\":\"\"}'>${fmt(r.pxkg)}</td>\n"
        "    <td style='text-align:right' class='${r.cout===mnCo?\"best-cell\":\"\"}'>${fmt(r.cout)}</td>\n"
        "    <td style='text-align:right'>${r.portions||'—'}</td>\n"
        "    <td style='text-align:right;font-size:11px;color:#888'>${r.date}</td>\n"
        "    </tr>`).join('');\n"
        "}\n"
        "function buildMetrics(){\n"
        "  const wp=RAW.filter(r=>r.pxkg);\n"
        "  const best=wp.reduce((a,b)=>a.pxkg<b.pxkg?a:b,wp[0]);\n"
        "  const bc=RAW.filter(r=>r.cout).reduce((a,b)=>a.cout<b.cout?a:b);\n"
        "  const prods=new Set(RAW.map(r=>r.produit)).size;\n"
        "  document.getElementById('metricCards').innerHTML=`\n"
        "    <div class='card'><div class='card-label'>Produits</div><div class='card-value'>${prods}</div></div>\n"
        "    <div class='card'><div class='card-label'>Meilleur EUR/kg</div>"
        "<div class='card-value best'>${best.pxkg.toFixed(2)} EUR</div>"
        "<div class='card-sub'>${best.produit.slice(0,28)} — ${best.taille}</div></div>\n"
        "    <div class='card'><div class='card-label'>Meilleur EUR/portion</div>"
        "<div class='card-value best'>${bc.cout.toFixed(2)} EUR</div>"
        "<div class='card-sub'>${bc.produit.slice(0,28)} — ${bc.taille}</div></div>\n"
        f"    <div class='card'><div class='card-label'>Mise a jour</div>"
        f"<div class='card-value' style='font-size:16px'>{today_str}</div></div>`;\n"
        "}\n"
        "function buildFilters(){\n"
        "  document.getElementById('filters').innerHTML=['all',...SIZES].map(sz=>`\n"
        "    <button class='filter-btn ${sz===currentSize?\"active\":\"\"}'"
        " onclick='filterSize(\"${sz}\")'>${sz==='all'?'Toutes':sz}</button>`).join('');\n"
        "}\n"
        "function filterSize(sz){currentSize=sz;buildFilters();updateCharts();updateTable();}\n"
        "buildMetrics();buildFilters();initCharts();updateTable();\n"
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
                worker_page = await context.new_page()
                try:
                    return await scrape_product(worker_page, url)
                finally:
                    await worker_page.close()

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
