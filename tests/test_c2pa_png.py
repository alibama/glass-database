"""C2PA signing/validation covers PNG as well as JPEG (asserted conformance scope)."""
import io

import pytest

from glowtbook import c2pa_sign as C

pytestmark = pytest.mark.skipif(not C.available(), reason="c2pa-python not installed")


def _img(mime, color=(150, 60, 30)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (300, 240), color).save(b, "JPEG" if mime == "image/jpeg" else "PNG")
    return b.getvalue()


@pytest.mark.parametrize("mime,ext", [("image/jpeg", b"\xff\xd8"), ("image/png", b"\x89PNG")])
def test_sign_and_validate(mime, ext):
    orig = _img(mime)
    signed = C.sign_image(orig, "Piece", "A. Parker",
                          {"content_hash": "x", "sourcing": "t", "contributor": "AP"},
                          mime=mime, parent_bytes=orig, parent_format=mime)
    assert signed[:2] == b"\xff\xd8" or signed[:4] == b"\x89PNG"   # right container
    creds = C.read_credentials(signed)
    assert creds and creds["validation_state"] == "Valid"
    assert "cawg.metadata" in creds["assertions"]


def test_sign_jpeg_wrapper_still_works():
    orig = _img("image/jpeg")
    assert C.sign_jpeg(orig, "P", "AP", {"content_hash": "x", "sourcing": "t", "contributor": "AP"})


def test_unsupported_type_rejected():
    with pytest.raises(ValueError):
        C.sign_image(b"x", "P", "AP", {}, mime="image/webp")
