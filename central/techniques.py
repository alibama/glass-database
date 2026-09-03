"""
central.techniques
=================
Controlled vocabularies for the artist directory. The technique list is the one
from the intake form, but modelled as **linkable entities** (a stable slug id +
a `gbo:` ontology class) rather than free text — so the upcoming Wikibase section
can attach a QID to each technique without reworking the data. Keep that in the
background: today we store technique *labels* from this list; the id/gbo/QID
columns are the seam for later.
"""
from __future__ import annotations

GBO = "http://example.org/glassblowing#"

PRIMARY_FOCUS = [
    "Hot Glass / Furnace Work", "Flameworking / Lampworking", "Kilnformed & Cast Glass",
    "Cold Working & Surface Manipulation", "Architectural & Flat Glass",
    "Surface Painting, Printing & Image Processes", "Light, Gas & Electro-Glass",
    "Digital, Hybrid & Interdisciplinary", "Other",
]

STATUS = ["Living / Active", "Deceased"]

ETHNICITY = [
    "Hispanic or Latino", "American Indian or Alaska Native", "Asian",
    "Black or African American", "Native Hawaiian or Other Pacific Islander", "White",
    "Two or More Races / Multiracial", "Prefer not to answer", "Unknown", "Other",
]

# (id, label, group, gbo_class | None) — gbo_class is the seam to the ontology /
# future Wikibase item; None means "not yet mapped".
TECHNIQUES = [
    # Hot Glass & Flameworking
    ("offhand-blown", "Offhand Blown Glass", "Hot Glass & Flameworking", "FreeBlowing"),
    ("hot-sculpted", "Hot-Sculpted Solid Glass (Massiccio)", "Hot Glass & Flameworking", "SolidSculpting"),
    ("cane-murrine", "Cane & Murrine", "Hot Glass & Flameworking", "CaneWork"),
    ("furnace-cast", "Furnace Cast", "Hot Glass & Flameworking", None),
    ("borosilicate-flameworking", "Borosilicate Flameworking", "Hot Glass & Flameworking", "Flameworking"),
    ("functional-pipe", "Functional / Pipe Art", "Hot Glass & Flameworking", None),
    ("soft-glass-beadmaking", "Soft Glass / Beadmaking", "Hot Glass & Flameworking", "Flameworking"),
    ("scientific-apparatus", "Scientific Apparatus Fabrication", "Hot Glass & Flameworking", "ScientificGlassblowing"),
    # Kiln, Cold Work & Architectural
    ("fusing-slumping", "Fusing & Slumping", "Kiln, Cold Work & Architectural", "Fusing"),
    ("kiln-casting", "Kiln / Open-Mold Casting", "Kiln, Cold Work & Architectural", "KilnCasting"),
    ("lost-wax-casting", "Lost-Wax Casting (Cire Perdue)", "Kiln, Cold Work & Architectural", None),
    ("pate-de-verre", "Pâte de Verre", "Kiln, Cold Work & Architectural", "PateDeVerre"),
    ("cold-working", "Cold Working (Grinding / Polishing)", "Kiln, Cold Work & Architectural", "ColdWorkingProcess"),
    ("engraving-sandblasting", "Engraving, Sandblasting / Acid Etching", "Kiln, Cold Work & Architectural", "Engraving"),
    ("stained-glass", "Stained Glass", "Kiln, Cold Work & Architectural", None),
    ("dalle-de-verre", "Dalle de Verre", "Kiln, Cold Work & Architectural", None),
    # Specialized, Surface & Digital
    ("enamel-grisaille", "Enamel Painting & Grisaille", "Specialized, Surface & Digital", None),
    ("verre-eglomise", "Reverse Painting (Verre Églomisé)", "Specialized, Surface & Digital", None),
    ("screen-printing-decals", "Screen Printing / Decals", "Specialized, Surface & Digital", None),
    ("vitreography", "Vitreography (Glass Plate Printing)", "Specialized, Surface & Digital", None),
    ("neon-plasma", "Neon & Plasma Glass", "Specialized, Surface & Digital", "ScientificGlassblowing"),
    ("digital-fabrication", "Digital Fabrication (CNC, Waterjet, 3D-Print Molds)", "Specialized, Surface & Digital", None),
    ("mixed-media", "Mixed Media Sculptural Assembly", "Specialized, Surface & Digital", None),
    ("installation-performance", "Installation & Performance", "Specialized, Surface & Digital", None),
    ("restoration-conservation", "Historic Restoration & Conservation", "Specialized, Surface & Digital", None),
]

LABELS = [t[1] for t in TECHNIQUES]
_BY_LABEL = {t[1]: t for t in TECHNIQUES}
GROUPS = ["Hot Glass & Flameworking", "Kiln, Cold Work & Architectural", "Specialized, Surface & Digital"]


def labels_in_group(group: str) -> list[str]:
    return [t[1] for t in TECHNIQUES if t[2] == group]


def resolve(label: str) -> dict | None:
    """Turn a technique label into its linkable form (id + gbo IRI) — the hook the
    Wikibase section will extend with a QID."""
    t = _BY_LABEL.get(label)
    if not t:
        return None
    return {"id": t[0], "label": t[1], "group": t[2],
            "gbo": (GBO + t[3]) if t[3] else None}


def resolve_many(labels) -> list[dict]:
    return [r for r in (resolve(x) for x in labels) if r]
