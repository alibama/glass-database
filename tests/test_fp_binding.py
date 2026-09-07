"""The multi-view fingerprint is bound to the C2PA credential and verifiable."""
import copy
import io

import pytest

from glowtbook import c2pa_sign as C
from glowtbook import fingerprint as F

pytestmark = pytest.mark.skipif(not C.available(), reason="c2pa-python not installed")


def _fp(n=16):
    return {"tool": "gd", "rating": 84, "tier": "Strong", "created": "2026",
            "metadata": {"dominantColor": {"name": "blue"}, "hasEmbeddings": False},
            "frames": [{"file": f"f/{i}.jpg", "dhash": [i, i + 1], "chist": [0.1] * 64, "sector": i}
                       for i in range(n)]}


def _signed(fp):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (200, 160), (30, 60, 160)).save(b, "JPEG")
    return C.sign_image(b.getvalue(), "Piece", "AP",
                        {"content_hash": "x", "sourcing": "t", "contributor": "AP"},
                        mime="image/jpeg", extra_assertions=[F.assertion(fp)])


def test_binding_holds_for_original():
    fp = _fp()
    r = F.verify_binding(_signed(fp), fp)
    assert r["bound"] is True and r["views"] == 16
    assert r["signed_sha256"] == r["computed_sha256"]
    assert r["validation_state"] == "Valid"


def test_binding_breaks_on_any_frame_change():
    fp = _fp()
    signed = _signed(fp)
    tampered = copy.deepcopy(fp); tampered["frames"][3]["dhash"] = [999, 999]
    r = F.verify_binding(signed, tampered)
    assert r["bound"] is False and "mismatch" in r["reason"]


def test_binding_reports_missing_assertion():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (120, 120), (10, 10, 10)).save(b, "JPEG")
    unsigned = C.sign_image(b.getvalue(), "P", "AP",
                            {"content_hash": "x", "sourcing": "t", "contributor": "AP"},
                            mime="image/jpeg")  # signed, but no fingerprint assertion
    r = F.verify_binding(unsigned, _fp())
    assert r["bound"] is False and "no fingerprint assertion" in r["reason"]
