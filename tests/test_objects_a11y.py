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
    assert "download" in html and "/api/objects/" in html and "(JPEG," in html    # ACC1 (API URLs)
    assert 'target="_blank" rel="noopener"' in html
    assert "opens in a new browser tab" in html


def test_verify_links_are_chooseable_tools():
    html = build_objects_html(_sample(1))
    assert "verify.contentauthenticity.org" in html and "c2paviewer.com" in html
    assert "contentcredentials.org/verify?source=" not in html   # the broken one is gone


def test_multiple_images_stay_on_one_card():
    objs = _sample(1)
    objs[0]["images"] = [("primary", "front", _IMG), ("detail", "signature", _IMG),
                         ("detail", "base", _IMG)]
    html = build_objects_html(objs)
    assert html.count("<article") == 1                 # one object -> one card
    assert "gdb-more" in html and "more views of" in html.lower()


def test_user_content_is_escaped():
    objs = _sample(1)
    objs[0]["title"] = '<script>alert(1)</script>'
    html = build_objects_html(objs)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_grid_layout_and_verify_callout():
    html = build_objects_html(_sample(2))
    assert "gdb-grid" in html                                  # grid, not a long list
    assert 'aria-label="Verify a physical piece"' in html      # verify surfaced up top
    assert "Have a physical piece?" in html
    assert "Objects on this page" not in html                  # old Contents list is gone


def test_details_disclosure_holds_the_heavy_content():
    html = build_objects_html(_sample(1))
    assert "<details>" in html and "<summary>" in html         # scannable cards + disclosure


def test_fingerprint_line_present():
    html = build_objects_html(_sample(1))
    assert "Physical fingerprint:" in html and "84/100 (Strong)" in html
    assert "/fingerprint/verify.html" in html
