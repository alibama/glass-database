"""
scripts/build_shop_build.py
===========================
The equipment build documents come from ONE source, ``deploy/shop-build/bom.json``: the
parts, the options (service phase, fuel, how the furnace is heated) and the sizing
parameters. This script renders from it

* ``deploy/SHOP-BUILD.md``: the long-form document with every part, the sizing tables and
  the build notes, for reading and printing;
* ``deploy/shop-diagrams/furnace-section.svg``, ``annealer-section.svg`` and
  ``gloryhole-section.svg``: dimensioned section drawings (schematic, not to scale).

``deploy/shop-build.html`` reads the same JSON in the browser and does the same arithmetic
in JavaScript, so a change to a part or a size is made once. ``sizing`` below is the
arithmetic, kept small enough to read: branch amperage, breaker and copper wire size,
burner orifice drills, gas pipe size, furnace element strings and annealer coils.

Run:  python -m scripts.build_shop_build            (stdlib only)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOM = ROOT / "deploy" / "shop-build" / "bom.json"
MD = ROOT / "deploy" / "SHOP-BUILD.md"
DIAGRAMS = ROOT / "deploy" / "shop-diagrams"

WIRE_ORDER = ["14", "12", "10", "8", "6", "4", "3", "2", "1", "1/0", "2/0", "3/0", "4/0"]
WIRE_DIA_IN = {"10": 0.1019, "12": 0.0808, "13": 0.072, "14": 0.0641, "16": 0.0508}   # resistance wire


def load(path: Path = BOM) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ sizing
def amps(kw: float, volts: float, phases: int) -> float:
    """Line current for a resistive load: P/V single-phase, P/(√3·V) three-phase."""
    return kw * 1000 / volts if phases == 1 else kw * 1000 / (volts * math.sqrt(3))


def breaker(continuous_amps: float, sizes) -> int:
    """The next standard breaker at or above 125% of a continuous load."""
    need = continuous_amps * 1.25
    for b in sizes:
        if b >= need:
            return b
    return sizes[-1]


def wire(breaker_amps: int, ampacity: dict, small_cap: dict) -> str:
    """Smallest copper THHN (75 C terminals) whose ampacity covers the breaker, honouring
    the small-conductor caps (14 AWG 15 A, 12 AWG 20 A, 10 AWG 30 A)."""
    for g in WIRE_ORDER:
        cap = min(ampacity[g], small_cap.get(g, 10 ** 9))
        if cap >= breaker_amps:
            return g
    return WIRE_ORDER[-1]


def orifice(btuh: float, fuel: dict, constants: dict) -> dict:
    """Orifice for a burner: Q (CFH) = 1658.5 · A (in²) · Cd · √(h / SG). Returns the
    diameter and the number drill at or just above it, plus the one below, to tune with."""
    cfh = btuh / fuel["btu_per_cuft"]
    area = cfh / (constants["orifice_coefficient"] * constants["orifice_discharge"]
                  * math.sqrt(fuel["manifold_inwc"] / fuel["sg"]))
    dia = math.sqrt(4 * area / math.pi)
    drills = constants["drills"]
    above = next(((n, d) for n, d in drills if d >= dia), drills[-1])
    below = next(((n, d) for n, d in reversed(drills) if d < dia), drills[0])
    return {"cfh": cfh, "area_in2": area, "dia_in": dia, "drill": above, "drill_below": below}


def pipe_size(cfh: float, length_ft: float, table: dict) -> str:
    """Smallest Schedule 40 size that carries `cfh` at the next tabulated length ≥ length_ft."""
    lengths = table["lengths_ft"]
    col = next((i for i, L in enumerate(lengths) if L >= length_ft), len(lengths) - 1)
    for size in ("3/4", "1", "1-1/4", "1-1/2", "2"):
        if table["sizes"][size][col] >= cfh:
            return size
    return "over 2"


def furnace_strings(sz: dict, phase: dict) -> dict:
    """Silicon-carbide element strings: the voltage and current that give the design
    power with new elements, the power left at full voltage at end of life, and the
    feeder two ways (sized for new elements, or held by the SCR's current limit)."""
    cfg = sz["configs"][phase["id"]]
    r_string = cfg["per_string"] * sz["element_r_new_ohm"]
    p_string = sz["design_kw"] * 1000 / cfg["strings"]
    v_new = math.sqrt(p_string * r_string)
    i_string = v_new / r_string
    line_new = i_string * cfg["strings"] if phase["phases"] == 1 else i_string * math.sqrt(3)
    eol = sz["aging_factor_end_of_life"]
    p_eol = cfg["strings"] * phase["volts"] ** 2 / (r_string * eol) / 1000
    return {"config": cfg["label"], "elements": cfg["strings"] * cfg["per_string"], "r_string_new": r_string,
            "v_new": v_new, "i_string_new": i_string, "line_amps_new": line_new,
            "pct_voltage_new": v_new / phase["volts"] * 100, "kw_end_of_life_full_voltage": p_eol,
            "scr": cfg["scr"]}


def annealer_coils(sz: dict, phase: dict) -> dict:
    """Coil length from resistance and the alloy's ohms per foot, and the watt loading on
    the wire surface (the number that decides element life)."""
    cfg = sz["configs"][phase["id"]]
    ohm_ft = sz["wire_ohm_per_ft"][str(cfg["wire_awg"])]
    ft = cfg["coil_ohm"] / ohm_ft
    if phase["id"] == "1ph240":
        v_coil = phase["volts"] / 2            # two coils in series across 240 V
    elif phase["id"] == "3ph208":
        v_coil = phase["volts"] / math.sqrt(3)  # wye: line to neutral
    else:
        v_coil = phase["volts"]                # delta: line to line
    w_coil = v_coil ** 2 / cfg["coil_ohm"]
    area = ft * 12 * math.pi * WIRE_DIA_IN[str(cfg["wire_awg"])]
    total_kw = w_coil * cfg["coils"] / 1000
    return {"config": cfg["label"], "coils": cfg["coils"], "coil_ohm": cfg["coil_ohm"], "wire_awg": cfg["wire_awg"],
            "ft_per_coil": ft, "ft_total": ft * cfg["coils"], "v_coil": v_coil, "w_coil": w_coil,
            "w_per_in2": w_coil / area, "total_kw": total_kw}


def electrical_table(bom: dict) -> list[dict]:
    """One row per (equipment, phase option): continuous amps, breaker and wire."""
    c = bom["constants"]
    rows = []
    for ph in bom["options"]["phase"]:
        f = next(e for e in bom["equipment"] if e["id"] == "furnace")
        st = furnace_strings(f["sizing"]["electric"], ph)
        a_new = st["line_amps_new"]
        b_new = breaker(a_new, c["breaker_sizes"])
        rows.append({"equipment": "Furnace, electric, feeder sized for new elements at 12 kW", "phase": ph["label"],
                     "amps": a_new, "breaker": b_new, "wire": wire(b_new, c["copper_ampacity_75c"], c["small_conductor_cap"])})
        lim = 80.0
        if a_new > lim:        # the limit only matters where new elements would draw more than the feeder
            b_lim = breaker(lim, c["breaker_sizes"])
            rows.append({"equipment": "Furnace, electric, SCR current limit set to 80 A", "phase": ph["label"],
                         "amps": lim, "breaker": b_lim, "wire": wire(b_lim, c["copper_ampacity_75c"], c["small_conductor_cap"])})
        an = next(e for e in bom["equipment"] if e["id"] == "annealer")
        co = annealer_coils(an["sizing"], ph)
        a = amps(co["total_kw"], ph["volts"], ph["phases"])
        b = breaker(a, c["breaker_sizes"])
        rows.append({"equipment": f"Annealer, {co['total_kw']:.1f} kW", "phase": ph["label"], "amps": a, "breaker": b,
                     "wire": wire(b, c["copper_ampacity_75c"], c["small_conductor_cap"])})
    return rows


def gas_table(bom: dict) -> list[dict]:
    c = bom["constants"]
    out = []
    for fuel in bom["options"]["fuel"]:
        for eq, key, label in (("gloryhole", "max_btuh", "Glory hole at full fire"), ("gloryhole", "idle_btuh", "Glory hole at idle"),
                               ("furnace", "max_btuh", "Gas furnace at full fire"), ("furnace", "idle_btuh", "Gas furnace at idle")):
            e = next(x for x in bom["equipment"] if x["id"] == eq)
            sz = e["sizing"]["gas"] if eq == "furnace" else e["sizing"]
            o = orifice(sz[key], fuel, c)
            out.append({"fuel": fuel["label"], "what": label, "btuh": sz[key], "cfh": o["cfh"], "dia_in": o["dia_in"],
                        "drill": o["drill"][0], "drill_dia": o["drill"][1], "below": o["drill_below"][0], "inwc": fuel["manifold_inwc"]})
    return out


# ------------------------------------------------------------------ markdown
def _opt_tags(part: dict, bom: dict) -> str:
    opts = part.get("options") or {}
    if not opts:
        return "all"
    labels = []
    for key, ids in opts.items():
        names = {o["id"]: o["label"] for o in bom["options"][key]}
        labels.append(", ".join(names.get(i, i) for i in ids))
    return "; ".join(labels)


def _usd(p: dict) -> str:
    lo, hi = p.get("usd", [0, 0])
    if lo == 0 and hi == 0:
        return "made or on hand"
    return f"{lo:,} to {hi:,}"


def _parts_table(section: dict, bom: dict) -> list[str]:
    lines = ["| Item | Qty | Spec | Applies to | Where | USD, planning |", "|---|---|---|---|---|---|"]
    for p in section["parts"]:
        spec = p.get("spec", "")
        if p.get("note"):
            spec = (spec + ". " if spec else "") + p["note"]
        lines.append(f"| {p['item']} | {p['qty']} {p['unit']} | {spec} | {_opt_tags(p, bom)} | {p.get('where', '')} | {_usd(p)} |")
    return lines


def _section_tags(section: dict, bom: dict) -> str:
    opts = section.get("options") or {}
    if not opts:
        return ""
    names = []
    for key, ids in opts.items():
        lab = {o["id"]: o["label"] for o in bom["options"][key]}
        names.append(", ".join(lab.get(i, i) for i in ids))
    return " (" + "; ".join(names) + " only)"


def _totals(equipment: dict, bom: dict, choice: dict) -> tuple[int, int]:
    lo = hi = 0
    for s in equipment["sections"]:
        if not _applies(s.get("options"), choice):
            continue
        for p in s["parts"]:
            if _applies(p.get("options"), choice):
                a, b = p.get("usd", [0, 0])
                lo += a
                hi += b
    return lo, hi


def _applies(opts: dict | None, choice: dict) -> bool:
    if not opts:
        return True
    return all(choice.get(k) in v for k, v in opts.items())


def render_md(bom: dict) -> str:
    L: list[str] = []
    L.append(f"# {bom['title']}")
    L.append("")
    L.append(f"*Version {bom['version']}. Generated from `deploy/shop-build/bom.json` by "
             "`scripts/build_shop_build.py`; the interactive version with option toggles is "
             "`/shop-build.html`. Edit the JSON, not this file.*")
    L.append("")
    L.append(bom["preface"])
    L.append("")
    L.append("**Safety and code.** Nothing here is an engineering drawing or a permit set. Gas trains follow the "
             "National Fuel Gas Code (NFPA 54) and the furnace and oven standard (NFPA 86); electrical feeders and "
             "controls follow the National Electrical Code; both are installed and inspected by licensed people to "
             "the edition in force where you build. Where this document gives a number (an amperage, an orifice drill, "
             "a pipe size) it is the design figure for the professional to check, with the formula beside it.")
    L.append("")
    L.append("## The options")
    L.append("")
    for key, title in (("phase", "Electrical service"), ("fuel", "Fuel for the burners"), ("furnace_heat", "How the furnace is heated")):
        L.append(f"**{title}.**")
        L.append("")
        for o in bom["options"][key]:
            extra = f": {o['note']}" if o.get("note") else ""
            L.append(f"- {o['label']}{extra}")
        L.append("")
    L.append("## Sizing: the arithmetic the parts lists rest on")
    L.append("")
    L.append("### Branch circuits")
    L.append("")
    L.append("Current is P/V on single-phase and P/(√3·V) on three-phase; a furnace or annealer is a continuous load, "
             "so the breaker is the next standard size at or above 125% of it, and the copper conductor (THHN, 75 °C "
             "terminals) is the smallest whose ampacity covers the breaker, with 14, 12 and 10 AWG capped at 15, 20 "
             "and 30 A. The electric furnace is shown two ways: fed for the current new elements draw at the design "
             "power, or held to 80 A by the SCR's current limit (new elements then give about 9 kW until they age).")
    L.append("")
    L.append("| Equipment | Service | Continuous amps | Breaker | Copper THHN |")
    L.append("|---|---|---|---|---|")
    for r in electrical_table(bom):
        L.append(f"| {r['equipment']} | {r['phase']} | {r['amps']:.1f} | {r['breaker']} A | {r['wire']} AWG |")
    L.append("")
    L.append("### Furnace elements")
    L.append("")
    f = next(e for e in bom["equipment"] if e["id"] == "furnace")
    sz = f["sizing"]["electric"]
    L.append(f"Silicon-carbide elements rise in resistance as they age, to about {sz['aging_factor_end_of_life']}× when "
             "they are replaced. The strings are chosen so new elements reach the design power well below line "
             "voltage (the SCR holds them there) and old ones still reach it at full voltage. Example element "
             f"resistance {sz['element_r_new_ohm']} Ω new ({sz['element_r_new_note']}).")
    L.append("")
    L.append("| Service | Strings | Elements | String Ω new | Volts for 12 kW, new | % of line | Line amps, new | kW at full voltage, end of life | SCR |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for ph in bom["options"]["phase"]:
        s = furnace_strings(sz, ph)
        L.append(f"| {ph['label']} | {s['config']} | {s['elements']} | {s['r_string_new']:.2f} | {s['v_new']:.0f} V | "
                 f"{s['pct_voltage_new']:.0f}% | {s['line_amps_new']:.0f} A | {s['kw_end_of_life_full_voltage']:.1f} kW | {s['scr']} |")
    L.append("")
    L.append("### Annealer coils")
    L.append("")
    an = next(e for e in bom["equipment"] if e["id"] == "annealer")
    L.append(f"Coil length is resistance divided by the alloy's ohms per foot ({an['sizing']['wire_note']}); the watt "
             f"loading on the wire surface is kept at or under {an['sizing']['surface_load_w_per_in2_max']} W/in² so "
             "the coils last years at annealing temperature.")
    L.append("")
    L.append("| Service | Coils | Ω each | Wire | Feet per coil | Feet total | Volts per coil | Watts per coil | W/in² | Total kW |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for ph in bom["options"]["phase"]:
        c = annealer_coils(an["sizing"], ph)
        L.append(f"| {ph['label']} | {c['config']} | {c['coil_ohm']} | {c['wire_awg']} AWG | {c['ft_per_coil']:.0f} | "
                 f"{c['ft_total']:.0f} | {c['v_coil']:.0f} | {c['w_coil']:.0f} | {c['w_per_in2']:.1f} | {c['total_kw']:.1f} |")
    L.append("")
    L.append("### Burner orifices")
    L.append("")
    c = bom["constants"]
    L.append(f"Q (CFH) = {c['orifice_coefficient']} · A (in²) · {c['orifice_discharge']} · √(h ÷ SG), with h the manifold "
             "pressure in inches of water column and SG the gas's specific gravity (natural gas 0.60 at 1,000 BTU/ft³, "
             "propane 1.52 at 2,500 BTU/ft³). Drill the spud to the size shown, keep the one below in the drawer, and "
             "set the final size by the flame with a manometer on the test tee.")
    L.append("")
    L.append("| Fuel | Burner | BTU/h | CFH | Manifold | Orifice diameter | Drill | One below |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in gas_table(bom):
        L.append(f"| {r['fuel']} | {r['what']} | {r['btuh']:,} | {r['cfh']:.0f} | {r['inwc']} in WC | {r['dia_in']:.3f} in | "
                 f"{r['drill']} ({r['drill_dia']:.4f} in) | {r['below']} |")
    L.append("")
    L.append("### Gas pipe")
    L.append("")
    t = c["ng_pipe_cfh"]
    L.append(t["note"] + ". Capacity in cubic feet per hour (1,000 BTU/ft³ for natural gas); propane at 11 in WC "
             "uses the code's propane table and carries more BTU per pipe size.")
    L.append("")
    L.append("| Pipe size | " + " | ".join(f"{n} ft" for n in t["lengths_ft"]) + " |")
    L.append("|---|" + "---|" * len(t["lengths_ft"]))
    for size, caps in t["sizes"].items():
        L.append(f"| {size} in | " + " | ".join(f"{x:,}" for x in caps) + " |")
    L.append("")
    L.append(f"Examples: a glory hole alone (250 CFH) is a {pipe_size(250, 20, t)} in line to 20 ft and "
             f"{pipe_size(250, 40, t)} in to 40 ft; a glory hole and a gas furnace (430 CFH) is "
             f"{pipe_size(430, 40, t)} in to 40 ft and {pipe_size(430, 60, t)} in to 60 ft; the whole shop at "
             f"500 CFH is {pipe_size(500, 40, t)} in to 40 ft and {pipe_size(500, 100, t)} in to 100 ft.")
    L.append("")
    L.append("### Propane supply")
    L.append("")
    L.append(c["propane_vaporization_note"])
    L.append("")
    # equipment
    for e in bom["equipment"]:
        L.append(f"## {e['name']}")
        L.append("")
        L.append(e["summary"])
        L.append("")
        L.append(f"![Section drawing of the {e['name'].split(',')[0].lower()}](shop-diagrams/{_svg_name(e['id'])})")
        L.append("")
        L.append("**Design.**")
        L.append("")
        L.append("| Part of the design | As drawn |")
        L.append("|---|---|")
        for k, v in e["design"].items():
            L.append(f"| {k.replace('_', ' ')} | {v} |")
        L.append("")
        for s in e["sections"]:
            L.append(f"### {s['name']}{_section_tags(s, bom)}")
            L.append("")
            L.extend(_parts_table(s, bom))
            L.append("")
        L.append("**Planning totals for the parts above** (the fabrication labour is yours; prices are 2026 ranges from "
                 "the kinds of suppliers named):")
        L.append("")
        combos = _combos(bom, e["id"])
        for label, choice in combos:
            lo, hi = _totals(e, bom, choice)
            L.append(f"- {label}: {lo:,} to {hi:,} USD")
        L.append("")
        L.append("**Build notes.**")
        L.append("")
        for i, n in enumerate(e["build_notes"], 1):
            L.append(f"{i}. {n}")
        L.append("")
    sh = bom["shared"]
    L.append(f"## {sh['name']}")
    L.append("")
    for s in sh["sections"]:
        L.append(f"### {s['name']}")
        L.append("")
        L.extend(_parts_table(s, bom))
        L.append("")
    L.append("## Where these designs come from")
    L.append("")
    L.append("The shapes are the ones studio glass has built for itself since the 1960s and published: Henry Halem's "
             "*Glass Notes* (the do-it-yourself reference for furnaces, glory holes and annealers), Dudley Giberson's "
             "*A Glassblower's Companion* (cast-refractory glory holes and burners), Ed Schmid's books on beginning and "
             "advanced glassblowing (a built glory hole among them), Charles Correll's recuperative furnace work, and "
             "the kit designs sold to small studios. The example shop this site documents (an electric furnace on "
             "silicon-carbide elements and an SCR, a forced-air natural-gas glory hole, electric annealers on a "
             "ramp-soak controller, pneumatic doors and a pneumatic crucible lift) is the configuration the parts lists "
             "follow; `SHOP-PARTS.md` names the manufacturers to look at for the controls and elements, and "
             "`shop-prompts.md` is how to document the shop you end up with.")
    L.append("")
    L.append("The same three builds are described in Brick Schema, the open building ontology, so studios, controllers "
             "and archives can read one another's equipment models: see `docs/BRICK-FOR-HOT-SHOPS.md` and the Turtle files "
             "in `docs/brick/` in the repository (https://github.com/alibama/glass-database/tree/main/docs).")
    L.append("")
    return "\n".join(L) + "\n"


def _combos(bom: dict, eq: str) -> list[tuple[str, dict]]:
    out = []
    for ph in bom["options"]["phase"]:
        if eq == "furnace":
            out.append((f"electric, {ph['label']}", {"phase": ph["id"], "fuel": "ng", "furnace_heat": "electric"}))
            if ph["id"] == "1ph240":
                for fu in bom["options"]["fuel"]:
                    out.append((f"gas, {fu['label']} (any service)", {"phase": ph["id"], "fuel": fu["id"], "furnace_heat": "gas"}))
        elif eq == "annealer":
            out.append((ph["label"], {"phase": ph["id"], "fuel": "ng", "furnace_heat": "electric"}))
        else:
            if ph["id"] == "1ph240":
                for fu in bom["options"]["fuel"]:
                    out.append((fu["label"], {"phase": ph["id"], "fuel": fu["id"], "furnace_heat": "gas"}))
    return out


# ------------------------------------------------------------------ drawings
def _svg_name(eq_id: str) -> str:
    return {"furnace": "furnace-section.svg", "annealer": "annealer-section.svg", "gloryhole": "gloryhole-section.svg"}[eq_id]


class _Draw:
    def __init__(self, w: int, h: int, title: str):
        self.w, self.h, self.e = w, h, []
        self.e.append(f'<rect width="{w}" height="{h}" fill="#ffffff"/>')
        self.text(24, 30, title, 17, weight="bold")

    def text(self, x, y, s, size=12, anchor="start", color="#1a1a1a", weight="normal"):
        s = s.replace("&", "&amp;").replace("<", "&lt;")
        self.e.append(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{color}" font-weight="{weight}" '
                      f'font-family="Helvetica,Arial,sans-serif">{s}</text>')

    def rect(self, x, y, w, h, fill="none", stroke="#1a1a1a", sw=1.2, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.e.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def circle(self, cx, cy, r, fill="none", stroke="#1a1a1a", sw=1.2):
        self.e.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def line(self, x1, y1, x2, y2, stroke="#1a1a1a", sw=1.2, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.e.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def dim(self, x1, y1, x2, y2, label, offset=14):
        """A dimension line with ticks and a label, horizontal or vertical."""
        self.line(x1, y1, x2, y2, "#555", 1)
        if y1 == y2:
            self.line(x1, y1 - 5, x1, y1 + 5, "#555", 1); self.line(x2, y1 - 5, x2, y1 + 5, "#555", 1)
            self.text((x1 + x2) / 2, y1 - 6 if offset >= 0 else y1 + 16, label, 11, "middle", "#333")
        else:
            self.line(x1 - 5, y1, x1 + 5, y1, "#555", 1); self.line(x1 - 5, y2, x1 + 5, y2, "#555", 1)
            self.e.append(f'<text x="{x1 + offset}" y="{(y1 + y2) / 2 + 4}" font-size="11" fill="#333" '
                          f'font-family="Helvetica,Arial,sans-serif" transform="rotate(-90 {x1 + offset} {(y1 + y2) / 2 + 4})" text-anchor="middle">{label}</text>')

    def key(self, x, y, items):
        for i, (label, fill) in enumerate(items):
            yy = y + i * 18
            self.rect(x, yy - 10, 14, 12, fill, "#1a1a1a", 0.8)
            self.text(x + 20, yy, label, 11, color="#333")

    def render(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
                + "".join(self.e) + "</svg>")


STEEL, BLANKET, BOARD, BRICK, CAST, GLASS, FIBER26 = "#9aa0a6", "#f3e5ab", "#e8d7a8", "#e6b8a2", "#c9c3b8", "#9fd3e6", "#f9e7b0"


def draw_furnace() -> str:
    d = _Draw(900, 820, "Furnace, 100 lb invested crucible: front section (schematic, not to scale)")
    d.text(24, 50, "Cut through the centre, seen from the front. The gathering port is in the front wall, which the cut removes; it is shown dashed where it sits.", 11, color="#555")
    s = 11                      # 1 in = 11 px
    x0, ytop = 200, 190         # the skin's top edge, under the lid
    outer_w = 36 * s
    cw, ch = 22 * s, 30 * s     # chamber
    wall = 5.5 * s              # blanket 1 + board 2 + brick 2.5
    floor = 6.5 * s             # slab 3 + brick 2.5 + board 1
    outer_h = ch + floor
    legs = 8 * s
    # skin, blanket, board, brick
    d.rect(x0, ytop, outer_w, outer_h, STEEL)
    d.rect(x0 + 1 * s, ytop, outer_w - 2 * s, outer_h - 1 * s, BLANKET)
    d.rect(x0 + 3 * s, ytop, outer_w - 6 * s, outer_h - 3 * s, BOARD)
    d.rect(x0 + wall, ytop, outer_w - 2 * wall, outer_h - 3.5 * s, BRICK)
    cx0, cy0 = x0 + wall + 2.5 * s, ytop
    d.rect(cx0, cy0, cw, ch, "#ffffff")
    # floor: cast slab with dam over brick and board
    fy = cy0 + ch
    d.rect(cx0 - 2.5 * s, fy, cw + 5 * s, 3 * s, CAST)
    d.rect(cx0, fy - 1 * s, 1 * s, 1 * s, CAST); d.rect(cx0 + cw - 1 * s, fy - 1 * s, 1 * s, 1 * s, CAST)
    d.text(cx0 + cw / 2, fy + 1.2 * s, "3 in cast slab with a 1 in dam;", 10, "middle", "#333")
    d.text(cx0 + cw / 2, fy + 2.4 * s, "drain notch to a catch tray at the front corner", 10, "middle", "#333")
    # plinth and crucible
    px = cx0 + cw / 2
    d.rect(px - 7.5 * s, fy - 4 * s, 15 * s, 4 * s, CAST)
    d.text(px, fy - 1.5 * s, "cast plinth 15 x 4 in", 10, "middle", "#333")
    rim_y = fy - 4 * s - 14 * s
    d.rect(px - 7.25 * s, rim_y, 14.5 * s, 14 * s, "#ffffff")
    d.rect(px - 6 * s, rim_y + 3 * s, 12 * s, 11 * s, GLASS)
    d.text(px, rim_y + 8.5 * s, "glass, 100 lb", 11, "middle", "#246")
    d.text(px, rim_y + 1.8 * s, "invested crucible 12 x 14 in", 10, "middle", "#333")
    # lid: cast slab in a pan with board above, sitting on the walls; flue for the gas option
    lid_y = ytop - 5.5 * s
    d.rect(x0 + 1 * s, lid_y, outer_w - 2 * s, 5.5 * s, BOARD)
    d.rect(x0 + 3 * s, lid_y + 2 * s, outer_w - 6 * s, 3.5 * s, CAST)
    d.rect(x0 + outer_w / 2 - 1.5 * s, lid_y - 2 * s, 3 * s, 7.5 * s, "#d9b38c")
    d.text(x0 + outer_w / 2, lid_y - 2.8 * s, "removable lid: 3 in cast slab in an angle pan, 2 in board above; a 3 in flue with a damper on the gas option", 10, "middle", "#333")
    # elements: two per side, 2 and 5 in above the rim; label at the right margin with a leader
    for ex in (cx0 + 3 * s, cx0 + cw - 3 * s):
        for ey in (rim_y - 2 * s, rim_y - 5 * s):
            d.circle(ex, ey, 0.5 * s, "#333")
    lx = x0 + outer_w + 70
    d.line(cx0 + cw - 3 * s + 0.5 * s, rim_y - 3.5 * s, lx - 6, rim_y - 3.5 * s, "#555", 1, "3,3")
    d.text(lx, rim_y - 3.5 * s + 4, "SiC elements, 1 in, through both", 10, color="#333")
    d.text(lx, rim_y - 3.5 * s + 17, "side walls, 2 and 5 in above the rim", 10, color="#333")
    d.text(lx, rim_y - 3.5 * s + 30, "(electric option)", 10, color="#333")
    # port in the front wall, shown dashed; sill 2 in above the rim, 8 in tall, 6 in wide
    port_top, port_sill = rim_y - 10 * s, rim_y - 2 * s
    d.rect(px - 3 * s, port_top, 6 * s, port_sill - port_top, "none", "#1a1a1a", 1.2, "5,3")
    d.line(px + 3 * s, port_top + 1.5 * s, lx - 6, port_top + 1.5 * s, "#555", 1, "3,3")
    d.text(lx, port_top + 1.5 * s + 4, "gathering port 6 x 8 in in the", 10, color="#333")
    d.text(lx, port_top + 1.5 * s + 17, "front wall; sill 2 in above the rim;", 10, color="#333")
    d.text(lx, port_top + 1.5 * s + 30, "3 in cast door, counterweighted", 10, color="#333")
    # burner block for the gas option, back corner at floor level; label at the left margin
    d.rect(cx0 - 2.5 * s, fy - 6 * s, 2.5 * s, 3 * s, "#d9b38c")
    d.line(cx0 - 2.5 * s, fy - 4.5 * s, x0 - 30, fy - 4.5 * s, "#555", 1, "3,3")
    d.text(x0 - 36, fy - 4.5 * s - 6, "burner block, tangential,", 10, "end", "#333")
    d.text(x0 - 36, fy - 4.5 * s + 7, "3 in above the floor", 10, "end", "#333")
    d.text(x0 - 36, fy - 4.5 * s + 20, "(gas option)", 10, "end", "#333")
    # legs and floor line
    for lgx in (x0 + 1 * s, x0 + outer_w - 2.5 * s):
        d.rect(lgx, ytop + outer_h, 1.5 * s, legs, STEEL)
    d.line(x0 - 70, ytop + outer_h + legs, x0 + outer_w + 70, ytop + outer_h + legs, "#333", 2)
    # dimensions
    d.dim(x0, ytop + outer_h + legs + 26, x0 + outer_w, ytop + outer_h + legs + 26, "36 in outside", -1)
    d.dim(cx0, cy0 + 16, cx0 + cw, cy0 + 16, "22 in chamber")
    d.dim(x0 + outer_w + 30, lid_y, x0 + outer_w + 30, ytop + outer_h, "42 in, lid top to floor skin", 16)
    d.dim(x0 - 30, cy0, x0 - 30, fy, "30 in chamber", -16)
    d.key(620, 640, [("12 ga steel skin", STEEL), ("1 in 8 lb fiber blanket", BLANKET), ("2 in 2300 F fiber board", BOARD),
                     ("2.5 in K-28 insulating firebrick", BRICK), ("3000 F dense castable", CAST), ("burner block / flue (gas option)", "#d9b38c")])
    d.text(24, 800, "Walls from the outside: skin, 1 in blanket, 2 in board, 2.5 in brick. Chamber 22 x 22 in plan, 30 in tall. Legs 8 in; lifting eyes at the top corners.", 11, color="#333")
    return d.render()


def draw_annealer() -> str:
    d = _Draw(900, 560, "Annealer, 30 x 24 x 24 in: side section through the door (schematic, not to scale)")
    s = 11
    x0, ytop = 250, 70
    # outer: depth 24 + 2*(2.5+1) = 31 in; height 24 + 2*3.5 = 31 in
    ow, oh = 31 * s, 31 * s
    d.rect(x0, ytop, ow, oh, STEEL)
    d.rect(x0 + 0.3 * s, ytop + 0.3 * s, ow - 0.6 * s, oh - 0.6 * s, BOARD)
    d.rect(x0 + 1.3 * s, ytop + 1.3 * s, ow - 2.6 * s, oh - 2.6 * s, BRICK)
    cx0, cy0 = x0 + 3.5 * s, ytop + 3.5 * s
    cw, ch = 24 * s, 24 * s
    d.rect(cx0, cy0, cw, ch, "#ffffff")
    # shelf on posts
    d.rect(cx0 + 1 * s, cy0 + ch - 1.5 * s, cw - 2 * s, 0.5 * s, "#bbb")
    for px in (cx0 + 2 * s, cx0 + cw - 2.5 * s):
        d.rect(px, cy0 + ch - 1 * s, 0.6 * s, 1 * s, "#bbb")
    d.text(cx0 + cw / 2, cy0 + ch - 2 * s, "1/2 in kiln shelf on 1 in posts", 10, "middle", "#333")
    # coils in grooves on the back wall (right side in this section) at 6 and 14 in
    for gy in (cy0 + ch - 6 * s, cy0 + ch - 14 * s):
        d.rect(cx0 + cw - 0.6 * s, gy - 0.3 * s, 0.6 * s, 0.6 * s, "#fff", "#1a1a1a", 1)
        d.circle(cx0 + cw - 0.3 * s, gy, 0.25 * s, "#333")
    d.text(cx0 + cw / 2, cy0 + 1.6 * s, "coils in 1/2 x 5/8 in grooves at 6 and 14 in", 10, "middle", "#333")
    d.text(cx0 + cw / 2, cy0 + 1.6 * s + 13, "(both side walls and the back)", 10, "middle", "#333")
    # door (left): brick in angle frame, hinge, rope seal
    d.rect(x0 - 3.5 * s, ytop + 1.3 * s, 3.5 * s, oh - 2.6 * s, BRICK)
    d.rect(x0 - 3.8 * s, ytop + 1.0 * s, 0.3 * s, oh - 2.0 * s, STEEL)
    d.line(x0, ytop + 1.3 * s, x0, ytop + oh - 1.3 * s, "#c33", 3, "4,3")
    d.text(x0 - 4.6 * s, ytop + oh / 2 - 14, "door: 2.5 in brick, 1 in board,", 10, "end", "#333")
    d.text(x0 - 4.6 * s, ytop + oh / 2, "angle frame on two 1/2 in pins,", 10, "end", "#333")
    d.text(x0 - 4.6 * s, ytop + oh / 2 + 14, "3/4 in rope seal, limit switch", 10, "end", "#333")
    # thermocouples through the back wall at mid height
    tcy = cy0 + ch / 2
    d.line(cx0 + cw, tcy, cx0 + cw + 4 * s, tcy, "#333", 2)
    d.line(cx0 + cw, tcy + 1.5 * s, cx0 + cw + 4 * s, tcy + 1.5 * s, "#333", 2)
    d.text(cx0 + cw + 4.3 * s, tcy + 4, "TC control", 10, color="#333")
    d.text(cx0 + cw + 4.3 * s, tcy + 1.5 * s + 4, "TC high limit", 10, color="#333")
    # legs
    for lx in (x0 + 0.5 * s, x0 + ow - 2 * s):
        d.rect(lx, ytop + oh, 1.5 * s, 10 * s, STEEL)
    d.line(x0 - 70, ytop + oh + 10 * s, x0 + ow + 120, ytop + oh + 10 * s, "#333", 2)
    d.text(x0 + ow / 2, ytop + oh + 6 * s, "24 in legs (drawn short)", 10, "middle", "#333")
    d.dim(x0, ytop - 16, x0 + ow, ytop - 16, "31 in outside, 24 in chamber (depth)")
    d.dim(x0 + ow + 100, ytop, x0 + ow + 100, ytop + oh, "31 in outside, 24 in chamber", 16)
    d.key(640, 440, [("16 ga steel skin", STEEL), ("1 in 2300 F fiber board", BOARD), ("2.5 in K-23 insulating firebrick", BRICK)])
    d.text(24, 540, "Width (into the page) 30 in; the control enclosure hangs on the cool side; the element terminal box is on the back wall behind the coils' tails.", 11, color="#333")
    return d.render()


def draw_gloryhole() -> str:
    d = _Draw(900, 600, "Glory hole, 16 in bore: longitudinal section (schematic, not to scale)")
    s = 11
    x0, cy = 160, 260
    length = 30 * s
    # shell 24 OD
    d.rect(x0, cy - 12 * s, length, 24 * s, STEEL)
    # blanket 3 in
    d.rect(x0, cy - 11.6 * s, length, 23.2 * s, FIBER26)
    # hot face 1 in, bore 16
    d.rect(x0, cy - 9 * s, length, 18 * s, CAST)
    d.rect(x0 + 4 * s, cy - 8 * s, length - 4 * s, 16 * s, "#ffffff")
    # back wall 4 in cast
    d.rect(x0, cy - 9 * s, 4 * s, 18 * s, CAST)
    # burner tangential near the back (drawn entering from the top at an angle)
    d.line(x0 + 7 * s, cy - 12 * s - 40, x0 + 7 * s, cy - 8 * s, "#b36b00", 6)
    d.text(x0 + 7 * s + 8, cy - 12 * s - 24, "forced-air burner, tangential, 4 in from the back wall", 10, color="#333")
    d.text(x0 + 7 * s + 8, cy - 12 * s - 10, "2 in mixer body, 2.5 in burner block", 10, color="#333")
    # door ring and doors at the right (front)
    fx = x0 + length
    d.rect(fx, cy - 12 * s, 1.5 * s, 24 * s, STEEL)
    d.rect(fx + 1.5 * s, cy - 10 * s, 2.5 * s, 7 * s, CAST)
    d.rect(fx + 1.5 * s, cy + 3 * s, 2.5 * s, 7 * s, CAST)
    d.circle(fx + 1.5 * s, cy - 10 * s, 4, "#333")
    d.circle(fx + 1.5 * s, cy + 10 * s, 4, "#333")
    d.text(fx + 4.5 * s, cy - 6 * s, "half-moon doors, 2.5 in cast,", 10, color="#333")
    d.text(fx + 4.5 * s, cy - 6 * s + 14, "on 3/4 in pivots; foot pedal", 10, color="#333")
    d.text(fx + 4.5 * s, cy - 6 * s + 28, "or two air cylinders", 10, color="#333")
    d.line(fx + 1.5 * s, cy - 3 * s, fx + 1.5 * s, cy + 3 * s, "#c33", 2, "4,3")
    d.text(fx + 4.5 * s, cy + 2, "6 in half-open stop for the pipe", 10, color="#333")
    # stand
    for lx in (x0 + 2 * s, x0 + length - 4 * s):
        d.rect(lx, cy + 12 * s, 2 * s, 14 * s, STEEL)
    d.rect(x0 + 1 * s, cy + 12 * s, length - 2 * s, 1 * s, STEEL)
    d.line(x0 - 60, cy + 26 * s, x0 + length + 140, cy + 26 * s, "#333", 2)
    mid = (x0 + 4 * s + x0 + length - 4 * s) / 2          # between the legs
    d.text(mid, cy + 21.5 * s, "2 x 2 in angle stand, 6 in adjustment;", 10, "middle", "#333")
    d.text(mid, cy + 21.5 * s + 13, "bore centre 40 to 44 in off the floor", 10, "middle", "#333")
    # blower shelf
    d.rect(x0 + 8 * s, cy + 14 * s, 8 * s, 5 * s, "#ddd")
    d.text(x0 + 12 * s, cy + 17 * s, "blower 250 CFM", 10, "middle", "#333")
    # dims
    d.dim(x0, cy - 12 * s - 60, x0 + length, cy - 12 * s - 60, "30 in shell; 26 in bore depth")
    d.dim(x0 - 30, cy - 12 * s, x0 - 30, cy + 12 * s, "24 in shell OD", -16)
    d.dim(x0 - 70, cy - 8 * s, x0 - 70, cy + 8 * s, "16 in bore", -16)
    d.key(560, 420, [("14 ga rolled steel shell", STEEL), ("3 in 2600 F fiber blanket, two layers", FIBER26), ("1 in 3000 F cast hot face; 4 in cast back wall", CAST), ("burner", "#b36b00")])
    d.text(24, 580, "The flame enters on a tangent, wraps the bore and leaves through the door opening; the damperless chamber runs slightly positive, so the hood above it matters.", 11, color="#333")
    return d.render()


def main(argv: list[str]) -> int:
    bom = load()
    MD.write_text(render_md(bom), encoding="utf-8", newline="\n")
    DIAGRAMS.mkdir(exist_ok=True)
    for name, fn in (("furnace-section.svg", draw_furnace), ("annealer-section.svg", draw_annealer), ("gloryhole-section.svg", draw_gloryhole)):
        (DIAGRAMS / name).write_text(fn(), encoding="utf-8", newline="\n")
    print(f"wrote {MD.relative_to(ROOT)} and three drawings in {DIAGRAMS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
