"""Consent-gated harvest: refusal, ownership assertion, per-item approval."""
import io

from PIL import Image

from central import approvals, harvest, intake
from central.dbconn import connect


def _img(color=(50, 150, 80)):
    b = io.BytesIO(); Image.new("RGB", (160, 160), color).save(b, "JPEG"); return b.getvalue()


def _artist(c, name, consent):
    rid = intake.submit(c, "artist", {"artist_name": name, "nationality_base": "US",
                                      "status": "Living / Active",
                                      "primary_focus": "Hot Glass / Furnace Work",
                                      "training_education": "x", "harvest_consent": consent}, base_url="")
    approvals.set_status(c, "artist_submissions", [rid], "approved")


def test_consent_required(demo_db):
    c = connect()
    _artist(c, "No Consent Person", False)
    assert harvest.consent_ok(c, "No Consent Person") is False
    assert harvest.record_candidate(c, "No Consent Person", "website", "http://x/1", _img((200, 40, 40))) is None


def test_consented_item_pending_with_ownership(demo_db):
    c = connect()
    _artist(c, "Consented Person", True)
    rid = harvest.record_candidate(c, "Consented Person", "website", "http://x/2", _img(), caption="vase")
    assert rid
    items = harvest.list_items(c, "pending", "Consented Person")
    assert len(items) == 1
    assert "retains ownership" in items[0]["manifest_json"]        # owner asserted
    # not public until approved
    assert harvest.counts(c).get("approved", 0) == 0
    harvest.set_status(c, rid, "approved")
    assert harvest.list_items(c, "approved", "Consented Person")


def test_dedup(demo_db):
    c = connect()
    _artist(c, "Dedup Person", True)
    img = _img((10, 90, 200))
    a = harvest.record_candidate(c, "Dedup Person", "website", "http://x/3", img)
    b = harvest.record_candidate(c, "Dedup Person", "website", "http://x/3", img)
    assert a == b   # same content -> same item
