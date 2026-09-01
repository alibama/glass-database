"""
glowtbook.fingerprint
=====================
A thin adapter over `object-fingerprint`
(github.com/alibama/object-fingerprint): capture a re-identification fingerprint
of a physical piece from many angles, match a later capture against it, and bind
the result to the same C2PA provenance we already sign. Everything degrades
gracefully if the package isn't installed.

The camera capture happens in the package's standalone browser apps (enroll /
verify) — they can't run inside the Streamlit iframe — which export a zip; this
module handles those zips through the package API.
"""
from __future__ import annotations

import json


def available() -> bool:
    try:
        import object_fingerprint  # noqa: F401
        return True
    except Exception:
        return False


def load_enrollment(zip_bytes: bytes) -> dict:
    """Import an enroll export (.zip). Returns rating, tier, the fingerprint dict,
    a reference-sheet JPEG, and frame count — or {'ok': False, 'error': ...}."""
    try:
        import object_fingerprint as ofp
        fp, frames = ofp.load_enrollment_zip(zip_bytes)
        sheet = ofp.reference_sheet([b for _, b in frames]) if frames else None
        return {"ok": True, "rating": fp.rating, "tier": fp.tier,
                "n_frames": len(frames), "fingerprint": fp.to_dict(), "sheet": sheet}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)}


def verify_zip(reference, candidate_zip_bytes: bytes) -> dict:
    """Match a candidate capture (.zip of frames) against a stored reference.
    Returns the match result dict (confidence, verdict, …) + a human label."""
    import object_fingerprint as ofp
    ref = ofp.load_fingerprint(_as_obj(reference))
    candidates = ofp.candidate_images_from_zip(candidate_zip_bytes)
    result = ofp.match_images(ref, candidates)
    out = result.to_dict()
    out["label"] = result.label
    out["n_candidates"] = len(candidates)
    return out


def summary(reference) -> dict:
    """rating / tier / created for display, without touching images."""
    fp = _as_obj(reference)
    return {"rating": fp.get("rating"), "tier": fp.get("tier"), "created": fp.get("created")}


def assertion(reference) -> dict | None:
    """The C2PA fingerprint assertion to embed alongside our provenance manifest."""
    try:
        from object_fingerprint import c2pa
        return c2pa.fingerprint_assertion(_as_obj(reference))
    except Exception:
        return None


def capture_app(kind: str = "enroll") -> str | None:
    """Filesystem path to the packaged capture app (enroll|verify), if installed."""
    try:
        import object_fingerprint as ofp
        return ofp.capture_app(kind)
    except Exception:
        return None


def _as_obj(reference) -> dict:
    if isinstance(reference, dict):
        return reference
    return json.loads(reference)
