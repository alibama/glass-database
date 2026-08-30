"""
glowtbook.media
===============
The AIP -> DIP engine. Turns full-fidelity local originals (the OAIS Archival
Information Package the artist keeps) into a condensed, shareable Dissemination
Information Package: downscaled images plus a provenance MANIFEST.

The manifest is deliberately shaped like a C2PA manifest — `assertions`,
`ingredients`, and a reserved `signature` slot — so real Content Credentials
(c2patool / py-c2pa) can wrap it later without reshaping the data. Technique
fields carry their glass-ontology IRIs, so provenance is ontologically aligned.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone

from PIL import Image, ImageOps

MANIFEST_VERSION = "glowtbook-0.1"
GBO = "http://example.org/glassblowing#"   # replace with the published ontology base

# minimal label -> ontology class map (extend as the ontology grows)
TECHNIQUE_GBO = {
    "Offhand blown glass": "FreeBlowing",
    "Hot-sculpted solid glass": "SolidSculpting",
    "Cane & murrine": "CaneWork",
    "Borosilicate flameworking": "Flameworking",
    "Soft glass / beadmaking": "Flameworking",
    "Scientific apparatus": "ScientificGlassblowing",
    "Neon / plasma": "ScientificGlassblowing",
    "Fusing & slumping": "Fusing",
    "Kiln casting": "KilnCasting",
    "Pâte de verre": "PateDeVerre",
    "Cold working": "ColdWorkingProcess",
    "Engraving / sandblasting": "Engraving",
}


def technique_links(labels: list[str]) -> list[dict]:
    out = []
    for lab in labels:
        cls = TECHNIQUE_GBO.get(lab)
        out.append({"label": lab, "gbo": (GBO + cls) if cls else None})
    return out


def condense_image(data: bytes, max_px: int = 1200, quality: int = 80) -> bytes:
    """Downscale to fit max_px on the long edge, honor EXIF orientation, emit JPEG.
    This is the DIP rendition; the original stays untouched as the AIP."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)            # bake in rotation, then drop EXIF
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(obj: dict, events: list[dict], ingredients: list[dict],
                   techniques: list[str], include_value: bool,
                   contributor: str, sourcing: str = "self-reported") -> dict:
    """Assemble a C2PA-shaped provenance manifest for one object (the DIP core)."""
    meta = {k: obj.get(k, "") for k in
            ("title", "maker", "year", "materials", "dimensions", "description",
             "status")}
    if include_value:
        meta["value"] = {"amount": obj.get("value_amount", ""),
                         "currency": obj.get("value_currency", ""),
                         "insured": bool(obj.get("insured"))}

    ev = []
    for e in events:
        row = {k: e.get(k, "") for k in
               ("event_type", "event_date", "actor", "location", "note")}
        if include_value and (e.get("value_amount") or e.get("value_currency")):
            row["value"] = {"amount": e.get("value_amount", ""),
                            "currency": e.get("value_currency", "")}
        ev.append(row)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generator": "Glowtbook",
        "created": datetime.now(timezone.utc).isoformat(),
        "contributor": contributor,
        "sourcing": sourcing,                       # provenance is self-reported until verified
        "assertions": [
            {"label": "glassdb.object.metadata", "data": meta},
            {"label": "glassdb.provenance.events", "data": ev},
            {"label": "glassdb.technique.ontology", "data": technique_links(techniques)},
        ],
        "ingredients": ingredients,                 # the condensed images, by hash
        "signature": None,                          # reserved for C2PA signing (roadmap)
    }
    manifest["content_hash"] = sha256_hex(
        json.dumps({k: v for k, v in manifest.items() if k != "content_hash"},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    )[:16]
    return manifest
