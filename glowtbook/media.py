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
from pathlib import Path

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


def condense_image(data: bytes, max_px: int = 1200, quality: int = 80) -> tuple[bytes, str]:
    """Downscale to fit max_px on the long edge, honor EXIF orientation, and
    **preserve the source format**: PNG stays PNG, everything else becomes JPEG.
    Returns (bytes, mime). This is the DIP rendition; the original stays untouched
    as the AIP."""
    im = Image.open(io.BytesIO(data))
    is_png = (im.format or "").upper() == "PNG"
    im = ImageOps.exif_transpose(im)            # bake in rotation, then drop EXIF
    buf = io.BytesIO()
    if is_png:
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            im = im.convert("RGBA")
        im.thumbnail((max_px, max_px))
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((max_px, max_px))
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "image/jpeg"


THUMB_WIDTHS = (160, 320, 640, 960)


def thumb_width(w) -> int:
    """Snap a requested ``?w=`` to one of a few sizes so the caches stay small. 0 = the original."""
    try:
        w = int(w or 0)
    except (TypeError, ValueError):
        return 0
    if w <= 0:
        return 0
    for cand in THUMB_WIDTHS:
        if w <= cand:
            return cand
    return THUMB_WIDTHS[-1]


def thumbnail(data: bytes, width: int, quality: int = 76) -> tuple[bytes, str]:
    """A card-sized JPEG of an image: EXIF orientation baked in, long edge at most ``width``,
    never upscaled, transparency flattened onto paper. A display derivative only: it carries
    no Content Credential; the signed rendition is the full image."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (244, 238, 228))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((width, width))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue(), "image/jpeg"


def cached_thumbnail(cache_path: Path, source_mtime: float | None, loader, width: int) -> bytes:
    """``thumbnail`` with a file cache: ``cache_path`` is reused while it is at least as new as
    ``source_mtime`` (None = the source never changes under this key). ``loader()`` returns the
    source bytes and is only called on a miss."""
    try:
        if cache_path.exists() and (source_mtime is None or cache_path.stat().st_mtime >= source_mtime):
            return cache_path.read_bytes()
    except OSError:
        pass
    out, _ = thumbnail(loader(), width)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(out)
    except OSError:
        pass
    return out


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
            {"label": "org.glassdatabase.object", "data": meta},
            {"label": "org.glassdatabase.provenance.events", "data": ev},
            {"label": "org.glassdatabase.technique", "data": technique_links(techniques)},
        ],
        "ingredients": ingredients,                 # the condensed images, by hash
        "signature": None,                          # reserved for C2PA signing (roadmap)
    }
    manifest["content_hash"] = sha256_hex(
        json.dumps({k: v for k, v in manifest.items() if k != "content_hash"},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    )[:16]
    return manifest
