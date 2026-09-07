"""C2PA Conformance v0.2 fields + format preservation (Spec 2.2)."""
import io
import json

import pytest

from glowtbook import c2pa_sign as C

pytestmark = pytest.mark.skipif(not C.available(), reason="c2pa-python not installed")


def _img(mime, color=(150, 60, 30)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (300, 240), color).save(b, "JPEG" if mime == "image/jpeg" else "PNG")
    return b.getvalue()


def _actions(signed, mime):
    import c2pa
    m = json.loads(c2pa.Reader(mime, io.BytesIO(signed)).json())
    am = m["manifests"][m["active_manifest"]]
    acts = next(a for a in am["assertions"] if a["label"].startswith("c2pa.action"))["data"]
    return m["validation_state"], acts, am["claim_generator_info"][0]


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
def test_format_preserved_and_valid(mime):
    signed = C.sign_image(_img(mime), "P", "AP",
                          {"content_hash": "x", "sourcing": "t", "contributor": "AP"}, mime=mime)
    assert (signed[:4] == b"\x89PNG") == (mime == "image/png")   # PNG stays PNG, JPEG stays JPEG
    vs, _, _ = _actions(signed, mime)
    assert vs == "Valid"


def test_created_has_digitalsourcetype_and_allactionsincluded_and_specversion():
    signed = C.sign_image(_img("image/jpeg"), "P", "AP",
                          {"content_hash": "x", "sourcing": "t", "contributor": "AP"}, mime="image/jpeg")
    vs, acts, cg = _actions(signed, "image/jpeg")
    assert acts.get("allActionsIncluded") is True                 # §2.2
    assert cg.get("specVersion") == C.SPEC_VERSION                # §2.1
    created = next(a for a in acts["actions"] if a["action"] == "c2pa.created")
    assert "digitalSourceType" in created                         # §2.4


def test_opened_has_no_digitalsourcetype():
    parent = _img("image/jpeg", (40, 120, 90))
    signed = C.sign_image(_img("image/jpeg"), "P", "AP",
                          {"content_hash": "x", "sourcing": "t", "contributor": "AP"},
                          mime="image/jpeg", parent_bytes=parent, parent_format="image/jpeg")
    vs, acts, _ = _actions(signed, "image/jpeg")
    assert acts.get("allActionsIncluded") is True
    opened = next(a for a in acts["actions"] if a["action"] == "c2pa.opened")
    assert "digitalSourceType" not in opened                      # §2.4/§2.5
    # the proportional-resize action is an excepted action -> also no DST
    resized = next(a for a in acts["actions"] if a["action"] == "c2pa.resized.proportional")
    assert "digitalSourceType" not in resized
