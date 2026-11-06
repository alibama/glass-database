"""The equipment builds: one JSON of parts and sizes (deploy/shop-build/bom.json), the arithmetic
in scripts/build_shop_build.py, the generated SHOP-BUILD.md and drawings, and the page that
reads the same JSON in the browser."""
import json
import re
from pathlib import Path

from scripts import build_shop_build as sb

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
BOM = json.loads((DEPLOY / "shop-build" / "bom.json").read_text(encoding="utf-8"))
CONST = BOM["constants"]


def _opt_ids(key):
    return {o["id"] for o in BOM["options"][key]}


def test_bom_is_complete_and_its_options_are_known():
    assert {e["id"] for e in BOM["equipment"]} == {"furnace", "annealer", "gloryhole"}
    for e in BOM["equipment"] + [BOM["shared"]]:
        assert e["sections"], e.get("id", "shared")
        for s in e["sections"]:
            for k, ids in (s.get("options") or {}).items():
                assert set(ids) <= _opt_ids(k), (s["name"], k)
            assert s["parts"]
            for p in s["parts"]:
                assert p["item"] and p["qty"] > 0 and p["unit"], p
                lo, hi = p["usd"]
                assert 0 <= lo <= hi, p["item"]
                for k, ids in (p.get("options") or {}).items():
                    assert set(ids) <= _opt_ids(k), p["item"]
    for e in BOM["equipment"]:
        assert e["design"] and e["summary"] and len(e["build_notes"]) >= 5


def test_branch_circuit_arithmetic():
    sizes, amp, cap = CONST["breaker_sizes"], CONST["copper_ampacity_75c"], CONST["small_conductor_cap"]
    assert round(sb.amps(7.2, 240, 1), 1) == 30.0
    assert round(sb.amps(7.2, 208, 3), 1) == 20.0
    assert sb.breaker(30, sizes) == 40 and sb.breaker(20, sizes) == 25 and sb.breaker(80, sizes) == 100
    assert sb.wire(40, amp, cap) == "8" and sb.wire(25, amp, cap) == "10" and sb.wire(100, amp, cap) == "3"
    assert sb.wire(15, amp, cap) == "14" and sb.wire(30, amp, cap) == "10"       # the small-conductor caps hold


def test_furnace_strings_reach_design_power_new_and_old():
    f = next(e for e in BOM["equipment"] if e["id"] == "furnace")["sizing"]["electric"]
    for ph in BOM["options"]["phase"]:
        s = sb.furnace_strings(f, ph)
        assert 40 <= s["pct_voltage_new"] <= 75, (ph["id"], s)              # SCR headroom with new elements
        assert s["kw_end_of_life_full_voltage"] >= f["design_kw"], (ph["id"], s)   # still full power when aged 3x
        assert s["elements"] in (4, 6)


def test_annealer_coils_make_the_rated_power_at_a_safe_loading():
    a = next(e for e in BOM["equipment"] if e["id"] == "annealer")["sizing"]
    for ph in BOM["options"]["phase"]:
        c = sb.annealer_coils(a, ph)
        assert abs(c["total_kw"] - a["design_kw"]) < 0.05, (ph["id"], c)
        assert c["w_per_in2"] <= a["surface_load_w_per_in2_max"], (ph["id"], c)
        assert 20 < c["ft_per_coil"] < 120


def test_orifice_and_pipe_sizing():
    ng = next(f for f in BOM["options"]["fuel"] if f["id"] == "ng")
    lp = next(f for f in BOM["options"]["fuel"] if f["id"] == "lp")
    o = sb.orifice(250000, ng, CONST)
    assert round(o["cfh"]) == 250 and 0.22 < o["dia_in"] < 0.27 and o["drill"][1] >= o["dia_in"] > o["drill_below"][1]
    p = sb.orifice(250000, lp, CONST)
    assert round(p["cfh"]) == 100 and 0.15 < p["dia_in"] < 0.19 and p["dia_in"] < o["dia_in"]   # propane: smaller hole
    t = CONST["ng_pipe_cfh"]
    assert sb.pipe_size(250, 20, t) == "1" and sb.pipe_size(250, 40, t) == "1-1/4"      # 1 in carries 246 CFH at 40 ft
    assert sb.pipe_size(430, 60, t) == "1-1/2" and sb.pipe_size(430, 20, t) == "1-1/4" and sb.pipe_size(3000, 100, t) == "over 2"


def test_generated_document_and_drawings_are_current():
    """SHOP-BUILD.md and the three section drawings are regenerated from the JSON; a stale
    commit of either fails here (run python -m scripts.build_shop_build)."""
    assert (DEPLOY / "SHOP-BUILD.md").read_text(encoding="utf-8") == sb.render_md(BOM)
    for name, fn in (("furnace-section.svg", sb.draw_furnace), ("annealer-section.svg", sb.draw_annealer),
                     ("gloryhole-section.svg", sb.draw_gloryhole)):
        assert (DEPLOY / "shop-diagrams" / name).read_text(encoding="utf-8") == fn(), name
    md = sb.render_md(BOM)
    for e in BOM["equipment"]:
        assert e["name"] in md
    assert "| Fuel | Burner | BTU/h |" in md and "Propane supply" in md and "Where these designs come from" in md


def test_the_page_and_the_site_wire_the_builds_in():
    page = (DEPLOY / "shop-build.html").read_text(encoding="utf-8")
    assert "/shop-build/bom.json" in page and "1658.5" in page and 'data-nav data-active=' in page
    for key in ("furnace-section.svg", "annealer-section.svg", "gloryhole-section.svg"):
        assert key in page
    for name in ("shop.html", "shop-costs.html"):
        assert 'href="/shop-build.html"' in (DEPLOY / name).read_text(encoding="utf-8"), name
    assert "/shop-build.html" in (DEPLOY / "landing-index.html").read_text(encoding="utf-8")
    inst = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "shop-build.html" in inst and "shop-build/" in inst and "SHOP-BUILD.md" in inst
    dev = (ROOT / "scripts" / "dev_site.py").read_text(encoding="utf-8")
    assert "shop-build.html" in dev and '"shop-build"' in dev
    conf = (DEPLOY / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8")
    assert re.search(r"RedirectMatch 301 \^/\([^)]*\|shop-build\|", conf)
