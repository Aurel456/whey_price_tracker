"""Stockage CSV, sanity, statut actif/inactif, santé CI et API extension."""
from datetime import date, timedelta

import pytest

import hsn_tracker as core


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    return core.SiteConfig(
        name="Test", brand="Test", site_domain="test.fr", slug="test",
        error_log_path=tmp_path / "errors.log",
        dashboard_docs="d.html", reco_docs="r.html",
    )


def _scraped(name="Whey X", size="1Kg", price="29,90 €", ingredients="lactosérum", **kw):
    return {"name": name, "url": "https://www.test.fr/whey-x", "size": size,
            "price": price, "px_kg": "29,90 €", "ingredients": ingredients,
            "proteines_100g": 80, "px_kg_proteine": 37.38, "type_produit": "whey",
            "categorie": "Whey", "en_stock": True, **kw}


def test_roundtrip_keeps_types(cfg):
    core.append_rows([_scraped(), _scraped(size="2Kg", price="50 €", en_stock=False)], cfg)
    rows = core.load_rows(cfg)
    assert [r["Taille"] for r in rows] == ["1Kg", "2Kg"]
    r = rows[0]
    assert r["Date"] == date.today().isoformat()
    assert r["Prix (€)"] == 29.9 and isinstance(r["Prix (€)"], float)
    assert rows[1]["Prix (€)"] == 50
    assert r["Protéines (g/100g)"] == 80 and isinstance(r["Protéines (g/100g)"], int)
    assert r["En stock"] is True and rows[1]["En stock"] is False
    assert r["Portions"] is None
    assert r["Ingrédients"] == "lactosérum"
    assert list(r) == core.HEADERS


def test_new_column_migrates_header(cfg, monkeypatch):
    core.append_rows([_scraped()], cfg)
    monkeypatch.setattr(core, "HEADERS", core.HEADERS + ["Nouvelle (€)"])
    monkeypatch.setattr(core, "PRICE_COLS", core.PRICE_COLS + ["Nouvelle (€)"])
    core.append_rows([_scraped(size="2Kg")], cfg)
    rows = core.load_rows(cfg)
    assert [r["Taille"] for r in rows] == ["1Kg", "2Kg"]
    assert rows[1]["Prix (€)"] == 29.9  # pas de décalage de colonnes


def test_empty_ingredients_never_erase_known_ones(cfg):
    core.append_rows([_scraped(ingredients="lactosérum, sucralose")], cfg)
    core.append_rows([_scraped(ingredients="")], cfg)  # échec ponctuel du parsing
    assert {r["Ingrédients"] for r in core.load_rows(cfg)} == {"lactosérum, sucralose"}


def test_ingredients_with_newlines_and_commas(cfg):
    ingr = 'Lactosérum (lait), cacao "maigre";\nédulcorant'
    core.append_rows([_scraped(ingredients=ingr)], cfg)
    assert core.load_rows(cfg)[0]["Ingrédients"] == ingr


def test_sanity_filters_cloudflare_rows(cfg):
    rows = [_scraped(), _scraped(name="Sorry, you have been blocked", price=None)]
    clean, ok, _ = core.sanity_check_rows(rows, cfg)
    assert ok and len(clean) == 1


def test_activity_status():
    today = date(2026, 11, 2)
    latest = {
        ("A", "1kg"): {"Date": today.isoformat()},
        ("B", "1kg"): {"Date": (today - timedelta(days=3)).isoformat()},
        ("C", "1kg"): {"Date": (today - timedelta(days=4)).isoformat()},
        ("D", "1kg"): {"Date": (today - timedelta(days=31)).isoformat()},
    }
    st = core._activity_status(latest)
    assert st[("A", "1kg")]["actif"] and st[("B", "1kg")]["actif"]
    assert not st[("C", "1kg")]["actif"]
    assert ("D", "1kg") not in latest  # supprimé au-delà de 30 jours


def _write_counts(cfg, counts: dict):
    """Écrit un historique avec `n` produits distincts par date."""
    cfg.prices_path.parent.mkdir(exist_ok=True)
    lines = [",".join(core.PRICE_COLS)]
    for d, n in counts.items():
        for i in range(n):
            lines.append(f"{d},P{i},https://x/{i},1kg" + "," * (len(core.PRICE_COLS) - 4))
    cfg.prices_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_health_ok_and_failures(cfg):
    days = {f"2026-10-{d:02d}": 40 for d in range(20, 27)}
    _write_counts(cfg, {**days, "2026-10-27": 39})
    ok, _ = core.health_report([cfg], today="2026-10-27")
    assert ok
    _write_counts(cfg, {**days, "2026-10-27": 20})
    ok, lines = core.health_report([cfg], today="2026-10-27")
    assert not ok and "chute" in lines[-1]
    ok, lines = core.health_report([cfg], today="2026-10-28")
    assert not ok and "aucun relevé" in lines[-1]


def test_url_key_matches_page_variants():
    k = core._url_key("https://www.hsnstore.fr/marques/sport-series/evobasic-whey")
    assert k == "hsnstore.fr/marques/sport-series/evobasic-whey"
    assert core._url_key("https://hsnstore.fr/marques/sport-series/evobasic-whey/?utm=x#top") == k
    assert core._url_key("https://fr.myprotein.com/p/x/impact/10530943/") == "fr.myprotein.com/p/x/impact/10530943"


def test_price_stats():
    pts = [("2026-07-01", 30.0), ("2026-09-01", 25.0), ("2026-10-20", 28.0),
           ("2026-10-21", 28.0), ("2026-11-02", 29.0)]
    st = core._price_stats(pts, "2026-11-02")
    assert st["minAll"] == 25.0 and st["minAllDate"] == "2026-09-01"
    assert st["min30"] == 28.0
    assert st["min90"] == 25.0 and st["max90"] == 29.0
    assert st["since"] == "2026-07-01"
    # Historique en escalier : un point par changement de prix + le dernier jour.
    assert st["history"] == [["2026-09-01", 25.0], ["2026-10-20", 28.0], ["2026-11-02", 29.0]]
