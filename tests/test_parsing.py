"""Parsing et détection : un test par piège déjà rencontré (cf. AGENTS.md)."""
import json

import hsn_tracker as core
import myprotein_tracker as mp


# ── spconfig HSN ──────────────────────────────────────────────────────────────
def test_spconfig_survives_svg_braces():
    # Bug 2026-05 : des SVG inline avec `{` dans les paths cassaient le comptage
    # naïf des accolades.
    blob = {
        "attributes": {"93": {"options": [{"id": "1", "products": ["111"]}]}},
        "optionPrices": {"111": {"finalPrice": {"amount": 29.9}}},
        "icons": {"Monodose": "<svg viewBox='0 0 24 24'><path d='M1 {2} 3'/></svg>"},
    }
    page = f"<script>initConfigurableOptions('123', {json.dumps(blob)});</script>"
    spc = core.extract_spconfig(page)
    assert spc == blob
    assert core.build_option_price_map(spc) == {"1": 29.9}


def test_spconfig_absent():
    assert core.extract_spconfig("<html>pas de config</html>") is None


# ── Nutrition ─────────────────────────────────────────────────────────────────
def test_parse_num_prefers_kcal():
    assert core._parse_num("1650 kJ / 390 Kcal") == 390
    assert core._parse_num("0,5 g") == 0.5
    assert core._parse_num("") is None


def test_parse_nutrition_whey_reads_per_100g_column():
    tables = [{"heading": "Informations nutritionnelles", "rows": [
        ["", "Par dose (30g)", "Pour 100g"],
        ["Valeur énergétique", "117 Kcal", "390 Kcal"],
        ["Protéines", "24 g", "80 g"],
        ["Sel", "0,1 g", "0,33 g"],
    ]}]
    out = core._parse_nutrition(tables)
    assert out["energie_kcal_100g"] == 390
    assert out["proteines_100g"] == 80
    assert out["sel_100g"] == 0.33


def test_parse_nutrition_creatine_mg_converted_to_g():
    # Bug : 3000 mg stockés tels quels → colonne « Créatine (g/dose) » ×1000.
    tables = [{"heading": "Valeurs nutritionnelles", "rows": [
        ["", "Par dose"], ["Créatine monohydrate", "3000 mg"],
    ]}]
    assert core._parse_nutrition(tables)["creatine_g_dose"] == 3.0


def test_parse_nutrition_omega_epa_dha():
    tables = [{"heading": "Valeurs nutritionnelles", "rows": [
        ["", "Par capsule"], ["EPA", "330 mg"], ["DHA", "220 mg"],
    ]}]
    out = core._parse_nutrition(tables)
    assert (out["epa_mg_dose"], out["dha_mg_dose"]) == (330, 220)


# ── Détection des types ───────────────────────────────────────────────────────
def test_evonative_casein_is_not_native():
    # Signalé 2026-07 : « native » en sous-chaîne → gamme Supérieure à tort.
    tags = core._detect_whey_type("", "EVONATIVE CASEIN (Lacprodan® MicelPure™)")
    assert "native" not in tags
    assert "caseine" in tags
    assert core._whey_tier(tags) == "basique"


def test_native_whey_is_superieure():
    tags = core._detect_whey_type("", "Native Whey Isolate")
    assert "native" in tags
    assert core._whey_tier(tags) == "superieure"


def test_soy_lecithin_is_not_vegetal():
    ingr = "Concentré de protéines de lactosérum, émulsifiant (lécithine de soja), arômes"
    tags = core._detect_whey_type(ingr, "Impact Whey Protein")
    assert "vegetal" not in tags
    assert "concentre" in tags


def test_soy_protein_is_vegan_tier():
    tags = core._detect_whey_type("Isolat de protéine de soja (90%)", "Isolat de soja")
    assert core._whey_tier(tags) == "vegan"


def test_cfm_detection():
    assert "isolat_cfm" in core._detect_whey_type("", "Evolate 2.0 (whey isolate CFM)")


def test_sweeteners():
    assert core._detect_sweeteners("..., édulcorant (sucralose)", "x") == ["sucralose"]
    assert core._detect_sweeteners("Isolat de lactosérum, lécithine", "x") == ["sans_edulcorant"]
    # « édulcorant » mentionné mais non identifié → on s'abstient.
    assert core._detect_sweeteners("édulcorant (E-999)", "x") == []


def test_product_type_from_url():
    base = "https://www.hsnstore.fr/marques/"
    assert core._detect_product_type(base + "essential-series/ultra-omega-3-tg-ifos-1000mg") == "omega3"
    assert core._detect_product_type(base + "raw-series/creatine-excell-100-creapure-en-poudre") == "creatine"
    assert core._detect_product_type(base + "sport-series/evobasic-whey") == "whey"


def test_omega_and_creatine_tags():
    assert set(core._detect_omega3_tags("ULTRA OMEGA-3 TG IFOS 1000mg", "")) == {"form_tg", "ifos"}
    assert set(core._detect_creatine_tags("100% Creapure", "créatine monohydrate")) == {"creapure", "monohydrate"}


def test_category_thresholds():
    assert core._detect_category(80) == "Whey"
    assert core._detect_category(70) == "Whey"
    assert core._detect_category(46) == "Aliments enrichis"
    assert core._detect_category(10) == "Autres"
    assert core._detect_category(None) == ""


def test_protein_costs():
    px_prot, cout30, cout_leu = core._compute_protein_costs(30.0, 80, leucine_mg_per_100g=10000)
    assert px_prot == 37.5
    assert cout30 == 1.125
    assert cout_leu == 0.9  # 30 €/kg ÷ 100 g leucine/kg × 3 g


def test_size_parsers():
    assert core._parse_size_kg("500g") == 0.5
    assert core._parse_size_kg("2 Kg") == 2.0
    assert core._parse_size_kg("Unique") is None
    assert core._parse_size_caps("120 softgels") == 120
    assert core._parse_size_caps("90 gélules") == 90


# ── MyProtein : regroupement des variantes ────────────────────────────────────
def _v(name, price, in_stock=True):
    return {"name": name, "sku": name, "price": price, "in_stock": in_stock}


def test_mp_whey_groups_by_canonical_portions_cheapest_in_stock():
    variants = [
        _v("Impact Whey - Chocolat - 1KG - 33portions", 25.0),
        _v("Impact Whey - Vanille - 1KG - 33portions", 22.0, in_stock=False),
        _v("Impact Whey - Fraise - 1KG - 33portions", 23.5),
        _v("Impact Whey - Miel - 1.9KG - 64portions", 59.99),  # édition limitée
    ]
    out = mp._group_by_size(variants, "whey", canon_portions={33})
    assert len(out) == 1
    assert out[0]["price"] == 23.5  # la moins chère EN STOCK, pas la moins chère tout court
    assert out[0]["size_label"].startswith("33 portions")


def test_mp_plant_protein_falls_back_to_weight():
    # 2026-07 : protéines végétales sans portions → 0 ligne, produit invisible.
    variants = [_v("Isolat de soja - 1KG", 19.0), _v("Isolat de soja - 2.5KG", 40.0)]
    out = mp._group_by_size(variants, "whey")
    assert sorted(o["size_label"] for o in out) == ["1kg", "2.5kg"]
