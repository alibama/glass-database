"""Image condensing + provenance manifest shape."""
from glowtbook import media


def test_condense_shrinks_image(sample_image_bytes):
    dip = media.condense_image(sample_image_bytes)
    assert isinstance(dip, bytes) and 0 < len(dip) < len(sample_image_bytes)


def test_sha256_is_stable(sample_image_bytes):
    a = media.sha256_hex(sample_image_bytes)
    b = media.sha256_hex(sample_image_bytes)
    assert a == b and len(a) == 64


def test_manifest_has_c2pa_shape():
    obj = {"title": "Cane vase", "maker": "AP", "year": "2025",
           "materials": "soda-lime", "dimensions": "", "description": "", "status": ""}
    events = [{"event_type": "created", "event_date": "2025", "actor": "AP",
               "location": "Crozet", "note": ""}]
    ingredients = [{"title": "front.jpg", "hash": "a" * 64, "role": "primary"}]
    m = media.build_manifest(obj, events, ingredients, ["Cane / murrine"],
                             include_value=False, contributor="AP")
    labels = {a["label"] for a in m["assertions"]}
    assert "glassdb.object.metadata" in labels
    assert "glassdb.provenance.events" in labels
    assert m["content_hash"] and m["ingredients"]
