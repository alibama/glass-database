"""Generalized intake (rich fields), signed moderation links, and /moderate."""
import os

from fastapi.testclient import TestClient

from central import intake, notify, techniques
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
    appr = {r[0] for r in c.execute("SELECT row_id FROM _approvals WHERE tbl='artist_submissions' AND status='approved'")}
    assert rid not in appr   # this row is pending
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
    appr = {x[0] for x in connect().execute("SELECT row_id FROM _approvals WHERE tbl='artist_submissions' AND status='approved'")}
    assert rid in appr   # this row is now approved


def test_notify_noop_without_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert notify.notify_submission("Artist", "Rae", {"x": "y"}, "t", "r", "") is False


def test_object_submission_one_click_promote(demo_db, monkeypatch, tmp_path):
    import os

    from fastapi.testclient import TestClient
    from PIL import Image

    from api.main import app
    from central import notify
    from central.dbconn import connect
    from glowtbook import contribute
    os.environ["GLASSDB_ADMIN_TOKEN"] = "s3cret"
    monkeypatch.setenv("GLASSDB_MODERATION", "1")
    d = tmp_path / "_objtest"; d.mkdir()
    Image.new("RGB", (400, 300), (120, 60, 30)).save(d / "p.png")
    obj = {"title": "Test goblet", "maker": "AP", "year": "2025", "techniques": "",
           "materials": "cristallo", "dimensions": "", "description": "", "value_amount": "",
           "value_currency": "USD", "insurer": "", "policy_no": "", "status": ""}
    contribute.contribute_object("u", "AP", obj, [],
                                 [{"aip_path": str(d / "p.png"), "role": "primary", "caption": ""}],
                                 False, sign=False, object_id=1, base_url="https://x")
    c = connect()
    sub = c.execute("SELECT id FROM object_submissions WHERE title='Test goblet'").fetchone()
    assert sub is not None            # staged pending, not yet public
    assert c.execute("SELECT COUNT(*) FROM objects WHERE title='Test goblet'").fetchone()[0] == 0
    # one-click approve link promotes it
    sig = notify.sign("object_submissions", str(sub["id"]))
    r = TestClient(app).get(f"/moderate?tbl=object_submissions&row={sub['id']}&action=approve&sig={sig}")
    assert r.status_code == 200 and "Approved" in r.text
    assert connect().execute("SELECT COUNT(*) FROM objects WHERE title='Test goblet'").fetchone()[0] == 1


def test_newsletter_subscribe(demo_db):
    import os
    os.environ["GLASSDB_ADMIN_TOKEN"] = "k"
    from fastapi.testclient import TestClient

    from api.main import app
    cl = TestClient(app)
    assert cl.post("/subscribe", json={"email": "x@y.com"}).json()["new"] is True
    assert cl.post("/subscribe", json={"email": "x@y.com"}).json()["new"] is False   # dedup
    assert cl.post("/subscribe", json={"email": "bad"}).status_code == 400
    assert cl.get("/subscribers.csv", headers={"X-API-Key": "k"}).status_code == 200
    assert cl.get("/subscribers.csv").status_code == 403


def test_season_entry_approve_link_flips_the_entry(demo_db):
    """The Discord link for a season entry must do what Admin → Seasons does: set the entry's
    own status (the season page reads e.status), not the generic approvals gate."""
    from fastapi.testclient import TestClient

    from api.main import app
    from central import seasons as S
    c = connect(); S.ensure(c)
    sid = S.create_season(c, status="open", title="Link Test Season", theme="t", starts="2032-10-01",
                          entries_close="2032-10-20", voting_closes="2032-10-28", ends="2032-10-31")
    c.execute("INSERT INTO season_entries (season_id, object_row_id, artist_uid, artist_name, title, status, created_at) "
              "VALUES (?,?,?,?,?,'pending',datetime('now'))", (sid, "obj-link-test", "u-link", "Link Maker", "Link pumpkin"))
    c.commit()
    eid = c.execute("SELECT id FROM season_entries WHERE object_row_id='obj-link-test'").fetchone()[0]
    cl = TestClient(app)
    sig = notify.sign("season_entries", str(eid))
    assert cl.get(f"/moderate?tbl=season_entries&row={eid}&action=approve&sig=bad").status_code == 403
    r = cl.get(f"/moderate?tbl=season_entries&row={eid}&action=approve&sig={sig}")
    assert r.status_code == 200 and "Approved" in r.text
    assert connect().execute("SELECT status FROM season_entries WHERE id=?", (eid,)).fetchone()[0] == "approved"
    r = cl.get(f"/moderate?tbl=season_entries&row={eid}&action=reject&sig={notify.sign('season_entries', str(eid))}")
    assert r.status_code == 200
    assert connect().execute("SELECT status FROM season_entries WHERE id=?", (eid,)).fetchone()[0] == "rejected"
    assert cl.get(f"/moderate?tbl=season_entries&row=999999&action=approve&sig={notify.sign('season_entries', '999999')}").status_code == 404
    c = connect()
    c.execute("DELETE FROM season_entries WHERE id=?", (eid,)); c.execute("DELETE FROM seasons WHERE id=?", (sid,)); c.commit()
