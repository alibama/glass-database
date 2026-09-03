"""
central.glass_traits
====================
A controlled vocabulary of *style traits* for Venetian and façon-de-Venise
drinking glasses — modelled the way museums model vocabularies (SKOS), organised
by facet, with authority-mapping seams to Getty AAT, Wikidata, and the Corning
Museum of Glass Glass Dictionary. Every concept has a stable id so it can carry a
QID/AAT id later without reworking data.

This is a *scholarly scaffold*, not a finished thesaurus: definitions are concise
working definitions, and most authority mappings are deliberately left open —
filling `skos:exactMatch` against Getty AAT and Wikidata is precisely the
alignment work to do with an institutional partner. Attribution in this field is
contested (Venice vs. façon de Venise vs. Altare/Hall/Netherlandish), so traits
describe *observable features*, never a verdict on origin.

Facets (skos:Collection):
  form  bowl  stem  knop  foot  rim  metal  filigrana  decoration  evidence
"""
from __future__ import annotations

VOCAB = "https://glassdatabase.org/vocab/glass-traits#"
AAT = "http://vocab.getty.edu/aat/"
WD = "http://www.wikidata.org/entity/"

# (id, label, definition)
FACETS = [
    ("form", "Vessel form", "Overall type of the drinking vessel."),
    ("bowl", "Bowl form", "Shape of the cup/bowl."),
    ("stem", "Stem type", "Construction and form of the stem — the most diagnostic feature."),
    ("knop", "Knop / collar", "Knops, mereses and collars along the stem."),
    ("foot", "Foot & pontil", "Foot construction and pontil treatment."),
    ("rim", "Rim", "Finishing of the rim."),
    ("metal", "Metal / body glass", "The glass itself — colour and composition family."),
    ("filigrana", "Filigrana decoration", "White/coloured thread (canework) decoration families."),
    ("decoration", "Other decoration", "Surface and applied decoration beyond filigrana."),
    ("evidence", "Making evidence & condition", "Tooling, wear, and condition features (for study/attribution)."),
]

# id, label, facet, definition, altLabels, {aat,wikidata,cmog}
def _c(cid, label, facet, definition, alt=None, aat=None, wikidata=None, cmog=None):
    return {"id": cid, "label": label, "facet": facet, "definition": definition,
            "alt": alt or [], "aat": aat, "wikidata": wikidata, "cmog": cmog}


CONCEPTS = [
    # --- vessel form ---
    _c("goblet", "Goblet", "form", "Stemmed drinking vessel with a bowl on a footed stem.", ["calice"]),
    _c("tazza", "Tazza", "form", "Shallow wide bowl on a stemmed foot; a display/drinking form.", []),
    _c("flute", "Flute", "form", "Tall narrow bowl for sparkling or ceremonial drinking.", []),
    _c("beaker", "Beaker", "form", "Footless or low-footed tumbler form.", ["bicchiere"]),
    _c("tumbler", "Tumbler", "form", "Flat-based drinking vessel without a stem.", []),
    _c("ewer", "Ewer / jug", "form", "Pouring vessel with handle and spout.", []),

    # --- bowl form ---
    _c("bowl-round-funnel", "Round-funnel bowl", "bowl", "Rounded base opening into a funnel.", []),
    _c("bowl-conical", "Conical bowl", "bowl", "Straight-sided cone.", []),
    _c("bowl-bucket", "Bucket bowl", "bowl", "Cylindrical, flat-based bowl.", []),
    _c("bowl-bell", "Bell bowl", "bowl", "Bell-shaped, flaring to the rim.", []),
    _c("bowl-ovoid", "Ovoid bowl", "bowl", "Egg-shaped bowl.", []),
    _c("bowl-thistle", "Thistle bowl", "bowl", "Bulbous base with a flaring upper section.", []),
    _c("bowl-cup", "Cup-shaped bowl", "bowl", "Shallow rounded cup.", []),

    # --- stem type (diagnostic) ---
    _c("stem-hollow-blown", "Hollow blown stem", "stem", "Stem blown as a hollow form, often moulded.", []),
    _c("stem-solid-drawn", "Solid drawn stem", "stem", "Solid stem drawn from the bowl or foot gather.", []),
    _c("stem-baluster", "Baluster stem", "stem", "Vase-profile swelling stem.", []),
    _c("stem-inverted-baluster", "Inverted baluster stem", "stem", "Baluster with the swelling uppermost.", []),
    _c("stem-knopped", "Knopped stem", "stem", "Stem articulated by one or more knops.", []),
    _c("stem-ribbed-knop", "Mould-blown ribbed knop stem", "stem",
       "Hollow knop blown in a ribbed mould (the 'flattened pumpkin/melon' knop).", ["melon knop", "pumpkin knop"]),
    _c("stem-figured", "Figured / pincered stem", "stem",
       "Stem worked with pincers into figural or moulded forms (lion masks, etc.).", ["mereses stem"]),
    _c("stem-serpent", "Serpent stem", "stem",
       "Elaborate pincered/trailed stem of coiled 'serpent' form, often with coloured trails.", ["à serpent", "vetro a serpenti"]),
    _c("stem-winged", "Winged stem", "stem",
       "Stem with applied pincered 'wings', often blue trails (Flügelglas / verre à ailettes).", ["flügelglas", "à ailettes", "winged glass"]),
    _c("stem-hollow-quatrefoil", "Hollow quatrefoil stem", "stem", "Hollow stem pinched into a lobed section.", []),
    _c("stem-cigar", "Cylindrical / cigar stem", "stem", "Plain straight cylindrical stem.", []),

    # --- knop / collar ---
    _c("merese", "Merese", "knop", "Flat glass wafer/collar joining bowl, stem, and foot.", ["wafer", "collar"]),
    _c("knop-hollow", "Hollow knop", "knop", "Blown hollow swelling in the stem.", []),
    _c("knop-solid", "Solid knop", "knop", "Solid swelling in the stem.", []),
    _c("knop-cushion", "Cushion knop", "knop", "Flattened cushion-shaped knop.", []),
    _c("knop-acorn", "Acorn knop", "knop", "Acorn-shaped knop.", []),

    # --- foot & pontil ---
    _c("foot-folded", "Folded foot", "foot", "Foot rim folded under for strength.", ["folded conical foot"]),
    _c("foot-conical", "Conical foot", "foot", "Cone-shaped spreading foot.", []),
    _c("foot-domed", "Domed foot", "foot", "Raised domed foot.", []),
    _c("foot-spreading", "Spreading foot", "foot", "Wide low spreading foot.", []),
    _c("pontil-rough", "Rough pontil scar", "foot", "Unfinished pontil scar on the foot underside.", ["pontil mark"]),
    _c("pontil-ground", "Ground/polished pontil", "foot", "Pontil scar ground and polished smooth.", []),

    # --- rim ---
    _c("rim-folded-in", "Inward-folded rim", "rim", "Rim folded inward.", []),
    _c("rim-folded-out", "Outward-folded rim", "rim", "Rim folded outward.", []),
    _c("rim-plain", "Plain fire-polished rim", "rim", "Simple rounded fire-polished rim.", []),
    _c("rim-everted", "Everted / flared rim", "rim", "Rim turned outward.", []),
    _c("rim-tooled", "Tooled / pinched rim", "rim", "Rim worked with tools or pincers.", []),

    # --- metal / body glass ---
    _c("cristallo", "Cristallo", "metal",
       "Very pure near-colourless soda glass developed on Murano, imitating rock crystal.",
       ["colourless"], cmog="https://www.cmog.org/glass-dictionary/cristallo"),
    _c("vitrum-blancum", "Tinged 'green' metal", "metal",
       "Slightly greenish/greyish or smoky metal (e.g. fern-ash verre de fougère).",
       ["verre de fougère", "fern glass"]),
    _c("calcedonio", "Calcedonio", "metal",
       "Marbled opaque glass imitating chalcedony/agate.", ["chalcedony glass"]),
    _c("lattimo", "Lattimo (opaque white)", "metal",
       "Opaque white glass, used as a body or as threads.", ["milk glass"]),
    _c("coloured-cobalt", "Cobalt-blue metal", "metal", "Deep blue body or trails from cobalt.", ["blue"]),
    _c("coloured-manganese", "Manganese-purple metal", "metal", "Purple/amethyst body from manganese.", []),
    _c("aventurine-metal", "Aventurine metal", "metal", "Glass with sparkling copper crystals.", ["avventurina"]),

    # --- filigrana (canework) ---
    _c("filigrana", "Filigrana (canework)", "filigrana",
       "Decoration using white or coloured glass canes/threads embedded in clear glass.",
       ["filigree glass", "latticino", "latticinio"]),
    _c("vetro-a-fili", "Vetro a fili", "filigrana",
       "Parallel straight threads/canes.", ["a fili", "thread glass"]),
    _c("vetro-a-retortoli", "Vetro a retortoli", "filigrana",
       "Twisted cable canes (spiralled threads).", ["a retortoli", "latticino twist"]),
    _c("vetro-a-reticello", "Vetro a reticello", "filigrana",
       "Crosshatched 'little net' of canes with a regular grid of trapped air bubbles.",
       ["reticello", "net glass"], cmog="https://www.cmog.org/glass-dictionary/reticello"),
    _c("zanfirico", "Zanfirico", "filigrana",
       "Later term for twisted-cane (a retortoli–type) filigrana rods.", ["zanfirico canes"]),

    # --- other decoration ---
    _c("mould-ribbing", "Mould-blown ribbing", "decoration", "Vertical ribs from an optic/dip mould.", ["optic ribbing"]),
    _c("wrythen", "Wrythen / twisted ribbing", "decoration", "Ribs twisted into a spiral.", ["wrything"]),
    _c("ice-glass", "Ice glass (ghiaccio)", "decoration",
       "Crackled frosted surface from thermal shock/marvering in fragments.", ["ghiaccio", "craquelle", "crackle glass"],
       cmog="https://www.cmog.org/glass-dictionary/ice-glass"),
    _c("diamond-point", "Diamond-point engraving", "decoration", "Surface scratched with a diamond point.", ["diamond engraving"]),
    _c("gilding", "Gilding", "decoration", "Applied gold decoration.", []),
    _c("cold-enamel", "Enamel / cold painting", "decoration", "Painted enamels (smalti), fired or cold.", ["smalti", "enamelled"]),
    _c("applied-trails", "Applied trails", "decoration", "Threads of glass trailed onto the surface.", ["trailing"]),
    _c("prunts", "Prunts / raspberry prunts", "decoration", "Applied blobs, often stamped (raspberry).", ["raspberry prunt"]),
    _c("pincered-work", "Pincered work", "decoration", "Surface pinched into ridges/'waffles' with pincers.", ["waffles", "pincered trails"]),
    _c("aventurine-dec", "Aventurine inclusions", "decoration", "Sparkling aventurine used decoratively.", []),

    # --- making evidence & condition ---
    _c("tool-marks", "Tooling marks", "evidence", "Marks from jacks/pincers, visible in raking light.", []),
    _c("straw-marks", "Straw marks", "evidence", "Fine surface striations from the annealing tray.", []),
    _c("wear-ring", "Wear ring", "evidence", "Ring of fine scratches on the foot from use.", ["base wear"]),
    _c("mould-seams", "Mould seams", "evidence", "Seam lines from a blowing mould.", []),
    _c("internal-bubbles", "Internal bubbles / seed", "evidence", "Trapped bubbles characteristic of period metal.", ["seed"]),
    _c("weathering", "Weathering / iridescence", "evidence", "Surface alteration or iridescent film from burial/age.", ["iridescence"]),
    _c("devitrification", "Devitrification", "evidence", "Crystalline cloudiness in the glass.", []),
    _c("repair", "Repair / restoration", "evidence", "Evidence of repair, fill, or replacement.", []),
]

_BY_ID = {c["id"]: c for c in CONCEPTS}
_BY_LABEL = {c["label"]: c for c in CONCEPTS}
_FACET_LABEL = {f[0]: f[1] for f in FACETS}


def facet_label(facet: str) -> str:
    return _FACET_LABEL.get(facet, facet)


def labels_by_facet(facet: str) -> list[str]:
    return [c["label"] for c in CONCEPTS if c["facet"] == facet]


def resolve(label_or_id: str) -> dict | None:
    return _BY_LABEL.get(label_or_id) or _BY_ID.get(label_or_id)


def resolve_many(labels) -> list[dict]:
    out = []
    for x in labels:
        c = resolve(x)
        if c:
            out.append({"id": c["id"], "label": c["label"], "facet": c["facet"],
                        "uri": VOCAB + c["id"]})
    return out


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def to_skos() -> str:
    """Emit the vocabulary as a SKOS concept scheme in Turtle."""
    L = ['@prefix skos: <http://www.w3.org/2004/02/skos/core#> .',
         '@prefix dct:  <http://purl.org/dc/terms/> .',
         '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
         f'@prefix gdt:  <{VOCAB}> .',
         f'@prefix aat:  <{AAT}> .',
         f'@prefix wd:   <{WD}> .', '',
         '<https://glassdatabase.org/vocab/glass-traits> a skos:ConceptScheme ;',
         '  dct:title "Glass Database — Venetian & façon-de-Venise trait thesaurus" ;',
         '  dct:description "Observable style traits of Renaissance Venetian-style drinking glasses. Traits describe features, not attributions." ;',
         '  dct:license <https://creativecommons.org/licenses/by/4.0/> .', '']
    for fid, flabel, fdef in FACETS:
        L.append(f'gdt:facet-{fid} a skos:Collection ;')
        L.append(f'  skos:prefLabel "{_esc(flabel)}" ;')
        L.append(f'  skos:definition "{_esc(fdef)}" ;')
        members = ", ".join(f'gdt:{c["id"]}' for c in CONCEPTS if c["facet"] == fid)
        L.append(f'  skos:member {members} .' if members else '  .')
        L.append('')
    for c in CONCEPTS:
        L.append(f'gdt:{c["id"]} a skos:Concept ;')
        L.append('  skos:inScheme <https://glassdatabase.org/vocab/glass-traits> ;')
        L.append(f'  skos:prefLabel "{_esc(c["label"])}"@en ;')
        for a in c["alt"]:
            L.append(f'  skos:altLabel "{_esc(a)}"@en ;')
        L.append(f'  skos:definition "{_esc(c["definition"])}"@en ;')
        if c["aat"]:
            L.append(f'  skos:exactMatch aat:{c["aat"]} ;')
        if c["wikidata"]:
            L.append(f'  skos:exactMatch wd:{c["wikidata"]} ;')
        if c["cmog"]:
            L.append(f'  rdfs:seeAlso <{c["cmog"]}> ;')
        L.append('  skos:note "authority mapping pending alignment" ;')
        L[-1] = L[-1][:-2] + ' .'  # close statement
        L.append('')
    return "\n".join(L) + "\n"
