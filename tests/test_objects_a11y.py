"""mDLAUG accessibility features in the objects collection view."""
import base64

from explore.objects_a11y import build_objects_html

_IMG = base64.b64encode(b"\xff\xd8\xff\xe0jpegbytesjpegbytes").decode()


def _sample(n=2):
    objs = []
    for i in range(n):
        objs.append({
            "id": f"row{i}", "title": f"Reticello vase {i}", "maker": "A. Parker",
            "year": "2025", "techniques": "Cane / murrine", "materials": "soda-lime",
            "dimensions": "20cm", "description": "A blown vessel.", "contributor": "Anson",
            "sourcing": "self-reported", "value_display": "", "content_hash": f"hash{i}",
            "has_credentials": True, "manifest_json": '{"content_hash":"x"}',
            "images": [("primary", "front", _IMG)],
            "events": [{"event_type": "created", "event_date": "2025", "actor": "AP", "location": "Crozet"}],
            "creds": {"issuer": "Glassdatabase", "creator": ["AP"],
                      "actions": ["c2pa.opened", "c2pa.resized"], "validation_state": "Valid"},
            "verify_url": "https://contentcredentials.org/verify?source=x",
            "video_url": None,
            "fingerprint": {"rating": 84, "tier": "Strong"},
        })
    return objs


def test_empty_state_has_live_region():
    html = build_objects_html([])
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert "No objects" in html


def test_named_list_and_count():
    html = build_objects_html(_sample(3))
    assert 'role="list"' in html                         # ACC4 collection items
    assert "3 objects found" in html                     # RED1 result count
    assert 'role="status"' in html


def test_images_have_descriptive_alt_not_filename():
    html = build_objects_html(_sample(1))
    assert 'alt="Reticello vase 0, by A. Parker, 2025' in html   # ACC2 real alt text
    assert 'alt=""' not in html and "front.jpg" not in html


def test_per_item_position_and_headings():
    html = build_objects_html(_sample(2))
    assert "Item 1 of 2" in html and "Item 2 of 2" in html        # NAV3 position
    assert '<h3 id="obj-row0-h">' in html                         # labelled item


def test_events_are_a_real_table_with_header_scope():
    html = build_objects_html(_sample(1))
    assert "<table" in html and 'scope="col"' in html and "<caption>" in html  # ACC3


def test_accessible_file_links_with_format_size_and_newtab_warning():
    html = build_objects_html(_sample(1))
    assert "download=" in html and "(JSON," in html and "(JPEG," in html        # ACC1
    assert 'target="_blank" rel="noopener"' in html
    assert "opens in a new browser tab" in html


def test_user_content_is_escaped():
    objs = _sample(1)
    objs[0]["title"] = '<script>alert(1)</script>'
    html = build_objects_html(objs)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_contents_jumplinks_present():
    html = build_objects_html(_sample(2))
    assert 'aria-label="Objects on this page"' in html and 'href="#obj-row0"' in html  # NAV4


def test_fingerprint_line_present():
    html = build_objects_html(_sample(1))
    assert "Physical fingerprint:" in html and "84/100 (Strong)" in html
    assert "/fingerprint/verify.html" in html
