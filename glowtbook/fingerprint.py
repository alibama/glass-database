"""
glowtbook.fingerprint
=====================
Adapter for the object-fingerprint capture apps
(github.com/alibama/object-fingerprint). The browser apps now compute the
descriptors (colour histogram + dHash, optional DINOv2 embedding) and do the
matching client-side, so this module's job is just to move the exported
fingerprint in and out of the registry, format-agnostically:

  * import an enroll export (.zip) → the raw fingerprint JSON + rating/tier,
  * summarise it for display,
  * build a compact, signable C2PA attestation (rating/tier/colour + a hash of
    the fingerprint — not the bulky per-frame vectors),

Verification happens in verify.html, which loads the reference straight from
/api/objects/<id>/fingerprint. No server-side matching needed.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

FP_LABEL = "io.github.object_fingerprint.fingerprint"


def available() -> bool:
    return True  # pure zip/json handling; no heavy dependency required


def load_enrollment(zip_bytes: bytes) -> dict:
    """Read an enroll export. Accepts a .zip (containing fingerprint.json) or raw
    JSON bytes. Returns rating/tier/frame-count, the raw fingerprint, and a
    preview thumbnail — or {'ok': False, 'error': ...}."""
    try:
        fp, preview = _read_export(zip_bytes)
        frames = fp.get("frames") or []
        if not frames:
            return {"ok": False, "error": "no frames in the fingerprint"}
        dims = None
        try:
            from glowtbook import matdetect
            m = matdetect.measure_zip(zip_bytes)
            if m.get("ok"):
                dims = m
                fp.setdefault("metadata", {})["dimensions_mm"] = {
                    "width": m["width_mm"], "depth": m["depth_mm"], "height": m["height_mm"]}
        except Exception:
            dims = None
        return {"ok": True, "rating": fp.get("rating"), "tier": fp.get("tier"),
                "n_frames": len(frames), "fingerprint": _strip_images(fp), "sheet": preview,
                "dimensions": dims}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)}


def summary(reference) -> dict:
    fp = _as_obj(reference)
    md = fp.get("metadata") or {}
    return {"rating": fp.get("rating"), "tier": fp.get("tier"),
            "created": fp.get("created"),
            "has_embeddings": bool(md.get("hasEmbeddings")),
            "dominant_color": (md.get("dominantColor") or {}).get("name")}


def fingerprint_hash(reference) -> str:
    canonical = json.dumps(_as_obj(reference), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def assertion(reference) -> dict | None:
    """A compact C2PA assertion: what was enrolled + a hash to bind it, without
    embedding the full per-frame vectors in the credential."""
    fp = _as_obj(reference)
    md = fp.get("metadata") or {}
    return {"label": FP_LABEL, "data": {
        "rating": fp.get("rating"), "tier": fp.get("tier"),
        "views": len(fp.get("frames") or []),
        "algorithm": ((fp.get("params") or {}).get("descriptor")
                      or ("dinov2+color+dhash" if md.get("hasEmbeddings") else "color+dhash")),
        "dominant_color": (md.get("dominantColor") or {}).get("name"),
        "fingerprint_sha256": fingerprint_hash(fp),
    }}


def verify_binding(image_bytes: bytes, reference) -> dict:
    """Prove the fingerprint served for an object is the exact one signed into its
    C2PA credential: recompute the SHA-256 over the whole fingerprint (all views)
    and compare it to the fingerprint_sha256 in the signed `glassdb.fingerprint`
    assertion. Also surfaces the signer and validation state."""
    out = {"bound": False, "computed_sha256": None, "signed_sha256": None,
           "views": None, "validation_state": None, "signer": None, "reason": ""}
    try:
        out["computed_sha256"] = fingerprint_hash(reference)
        out["views"] = len(_as_obj(reference).get("frames") or [])
    except Exception as ex:  # noqa: BLE001
        out["reason"] = f"bad fingerprint: {ex}"; return out
    try:
        from glowtbook import c2pa_sign
        data = c2pa_sign.read_assertion(image_bytes, FP_LABEL)
        creds = c2pa_sign.read_credentials(image_bytes) or {}
        out["validation_state"] = creds.get("validation_state")
        out["signer"] = creds.get("issuer")
        if not data:
            out["reason"] = "no fingerprint assertion in the credential"; return out
        out["signed_sha256"] = data.get("fingerprint_sha256")
        out["bound"] = bool(out["signed_sha256"]) and out["signed_sha256"] == out["computed_sha256"]
        if not out["bound"] and out["signed_sha256"]:
            out["reason"] = "hash mismatch — the served fingerprint is not the one that was signed"
    except Exception as ex:  # noqa: BLE001
        out["reason"] = f"couldn't read credential: {ex}"
    return out


# --- helpers --------------------------------------------------------------
def _read_export(data: bytes):
    if data[:2] == b"PK":  # a zip
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith("fingerprint.json")), None)
            if not name:
                raise ValueError("no fingerprint.json inside the export")
            fp = json.loads(z.read(name).decode())
            preview = None
            frames = fp.get("frames") or []
            if frames:
                first = frames[0].get("file")
                if first and first in z.namelist():
                    preview = z.read(first)
            return fp, preview
    return json.loads(data.decode()), None  # raw JSON


def _strip_images(fp: dict) -> dict:
    """The registry stores descriptors only — the frame thumbnails live in the
    export/AIP, not the public record. Descriptors (chist/dhash/emb) stay."""
    out = dict(fp)
    out["frames"] = [{k: v for k, v in fr.items() if k != "file"} | {"file": fr.get("file")}
                     for fr in (fp.get("frames") or [])]
    return out


def _as_obj(reference) -> dict:
    if isinstance(reference, dict):
        return reference
    return json.loads(reference)
