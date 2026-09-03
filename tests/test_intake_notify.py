"""Generalized intake, signed moderation links, and the /moderate endpoint."""
import os

from fastapi.testclient import TestClient

from central import approvals, intake, notify
from central.dbconn import connect


def test_token_sign_verify_roundtrip():
    os.environ["GLASSDB_ADMIN_TOKEN"] = "s3cret"
    sig = notify.sign("artist_submissions", "abc123")
    assert notify.verify("artist_submissions", "abc123", sig)
    assert not notify.verify("artist_submissions", "abc123", "wrong")
    assert not notify.verify("other", "abc123", sig)


def test_intake_writes_pending(demo_db):
    c = connect()
    rid = intake.submit(c, "studio", {"name": "New Hot Shop", "city": "Richmond",
                                      "email": "p@x.org"}, base_url="")
    # pending -> not approved yet
    assert approvals.counts(c, "studio_submissions")["approved"] == 0
    approvals.set_status(c, "studio_submissions", [rid], "approved")
    assert approvals.counts(c, "studio_submissions")["approved"] == 1
    # private column registered as non-public
    priv = {r[0] for r in c.execute(
        "SELECT column FROM _columns WHERE tbl='studio_submissions' AND is_public=0")}
    assert "email" in priv and "submitted_by" in priv


def test_moderate_endpoint_requires_valid_signature(demo_db):
    os.environ["GLASSDB_ADMIN_TOKEN"] = "s3cret"
    c = connect()
    rid = intake.submit(c, "artist", {"name": "Rae"}, base_url="")
    from api.main import app
    cl = TestClient(app)
    # bad signature -> 403, no change
    assert cl.get(f"/moderate?tbl=artist_submissions&row={rid}&action=approve&sig=bad").status_code == 403
    assert approvals.counts(connect(), "artist_submissions")["approved"] == 0
    # good signature -> approves
    sig = notify.sign("artist_submissions", rid)
    r = cl.get(f"/moderate?tbl=artist_submissions&row={rid}&action=approve&sig={sig}")
    assert r.status_code == 200 and "Approved" in r.text
    assert approvals.counts(connect(), "artist_submissions")["approved"] == 1


def test_moderation_links_shape():
    a, r = notify.moderation_links("https://glassdatabase.org", "event_submissions", "xyz")
    assert a.startswith("https://glassdatabase.org/api/moderate?") and "action=approve" in a
    assert "action=reject" in r and "sig=" in a


def test_notify_noop_without_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert notify.notify_submission("Artist", "Rae", {"x": "y"}, "t", "r", "") is False
