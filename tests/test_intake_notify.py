"""Generalized intake (rich fields), signed moderation links, and /moderate."""
import os

from fastapi.testclient import TestClient

from central import approvals, intake, notify, techniques
from central.dbconn import connect


def test_token_sign_verify_roundtrip():
    os.environ["GLASSDB_ADMIN_TOKEN"] = "s3cret"
    sig = notify.sign("artist_submissions", "abc123")
    assert notify.verify("artist_submissions", "abc123", sig)
    assert not notify.verify("artist_submissions", "abc123", "wrong")
    assert not notify.verify("other", "abc123", sig)


def test_technique_vocab_is_wikibase_ready():
    r = techniques.resolve("Cane & Murrine")
    assert r["id"] == "cane-murrine" and r["gbo"].endswith("#CaneWork")
    assert techniques.resolve("Furnace Cast")["gbo"] is None   # unmapped, but linkable id present
    assert len(techniques.LABELS) >= 24


def test_sections_are_not_columns(demo_db):
    c = connect()
    intake.ensure(c, "artist")
    cols = {r[1] for r in c.execute('PRAGMA table_info(artist_submissions)')}
    assert "artist_name" in cols and "tech_primary" in cols and "studied_under" in cols
    # section headers ("About you", "Mentorship") never become columns
    assert "about_you" not in cols and "mentorship" not in cols


def test_multiselect_stored_joined_and_pending(demo_db):
    c = connect()
    rid = intake.submit(c, "artist", {
        "artist_name": "Rae Sutter", "email": "r@x.org", "nationality_base": "US",
        "status": "Living / Active", "primary_focus": "Hot Glass / Furnace Work",
        "tech_primary": ["Offhand Blown Glass", "Cane & Murrine"],
        "training_education": "RISD", "studied_under": "Lino Tagliapietra"}, base_url="")
    row = c.execute("SELECT tech_primary FROM artist_submissions WHERE _row_id=?", (rid,)).fetchone()
    assert "Offhand Blown Glass | Cane & Murrine" == row[0]
    assert approvals.counts(c, "artist_submissions")["approved"] == 0   # pending
    # email + submitter are private
    priv = {r[0] for r in c.execute(
        "SELECT column FROM _columns WHERE tbl='artist_submissions' AND is_public=0")}
    assert "email" in priv and "submitted_by" in priv


def test_moderate_endpoint_requires_valid_signature(demo_db):
    os.environ["GLASSDB_ADMIN_TOKEN"] = "s3cret"
    c = connect()
    rid = intake.submit(c, "artist", {"artist_name": "Rae", "email": "r@x", "nationality_base": "US",
                                      "status": "Living / Active",
                                      "primary_focus": "Hot Glass / Furnace Work",
                                      "training_education": "x"}, base_url="")
    from api.main import app
    cl = TestClient(app)
    assert cl.get(f"/moderate?tbl=artist_submissions&row={rid}&action=approve&sig=bad").status_code == 403
    sig = notify.sign("artist_submissions", rid)
    r = cl.get(f"/moderate?tbl=artist_submissions&row={rid}&action=approve&sig={sig}")
    assert r.status_code == 200 and "Approved" in r.text
    assert approvals.counts(connect(), "artist_submissions")["approved"] == 1


def test_notify_noop_without_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert notify.notify_submission("Artist", "Rae", {"x": "y"}, "t", "r", "") is False
