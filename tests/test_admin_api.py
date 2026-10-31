"""The review queue through the API (what /review.html uses) and the page itself."""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central import sessions
from central.dbconn import connect

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
BOSS = "boss@example.com"


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_ADMIN_EMAILS", BOSS)
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "test-admin-key")
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    if not any(getattr(r, "path", "").startswith("/admin/") for r in app.routes):
        import api.admin as _admin
        app.include_router(_admin.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _tidy(demo_db):
    """The demo registry is shared by the whole session: take out what these tests put in."""
    yield
    import hashlib
    c = connect()
    promoted = hashlib.sha1(b"obj|test-queue-hash").hexdigest()[:16]
    profile = hashlib.sha1(b"profiles|prof-test-1").hexdigest()[:16]
    stmts = [
        ("DELETE FROM _approvals WHERE (tbl='studios' AND row_id LIKE 'studios-test-%') OR "
         "(tbl='artists' AND row_id LIKE 'artists-test-%') OR (tbl='objects' AND (row_id LIKE 'obj-test-queue-%' OR row_id=?)) "
         "OR (tbl='profiles' AND row_id=?)", (promoted, profile)),
        ("DELETE FROM studios WHERE _row_id LIKE 'studios-test-%'", ()),
        ("DELETE FROM artists WHERE _row_id LIKE 'artists-test-%'", ()),
        ("DELETE FROM object_submission_images WHERE submission_id IN "
         "(SELECT id FROM object_submissions WHERE content_hash='test-queue-hash')", ()),
        ("DELETE FROM object_submissions WHERE content_hash='test-queue-hash'", ()),
        ("DELETE FROM object_images WHERE object_row_id=? OR object_row_id LIKE 'obj-test-queue-%'", (promoted,)),
        ("DELETE FROM objects WHERE _row_id=? OR _row_id LIKE 'obj-test-queue-%'", (promoted,)),
        ("DELETE FROM submissions WHERE content_hash LIKE 'prof-test-%'", ()),
        ("DELETE FROM profiles WHERE _row_id=?", (profile,)),
        ("DELETE FROM season_claims WHERE collector_uid='q-collector'", ()),
        ("DELETE FROM season_entries WHERE object_row_id LIKE 'obj-test-queue-%'", ()),
        ("DELETE FROM season_badges WHERE uid LIKE 'q-maker-%' OR uid='q-collector'", ()),
        ("DELETE FROM feedback WHERE name='Tester' AND page='/review.html'", ()),
        ("DELETE FROM studio_sources WHERE name='Test Kiln Co'", ()),
        ("DELETE FROM harvested_items WHERE content_hash='hv-test-1'", ()),
        ("DELETE FROM users WHERE email='new.person@example.com'", ()),
        ("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM objects) WHERE tbl='objects'", ()),
    ]
    for sql, args in stmts:
        try:
            c.execute(sql, args)
        except Exception:  # noqa: BLE001 — a table a test never made
            pass
    c.commit()


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def _boss():
    return _auth("boss", BOSS, "The Boss")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _pending_row(c, tbl, rid, **cols):
    names = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *cols]
    c.execute(f'INSERT OR REPLACE INTO "{tbl}" ({", ".join(chr(34) + n + chr(34) for n in names)}) '
              f'VALUES ({", ".join("?" for _ in names)})', [rid, "test", "test", _now(), *cols.values()])
    c.execute("DELETE FROM _approvals WHERE tbl=? AND row_id=?", (tbl, rid))
    c.commit()


def _approval(c, tbl, rid):
    r = c.execute("SELECT status, reviewer FROM _approvals WHERE tbl=? AND row_id=?", (tbl, rid)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def test_the_queue_is_for_administrators_only(client):
    assert client.get("/admin/queue").status_code == 401
    assert client.get("/admin/queue", headers=_auth()).status_code == 403
    assert client.post("/admin/decide", json={"queue": "studios", "id": "x", "action": "approve"},
                       headers=_auth()).status_code == 403
    r = client.get("/admin/queue", headers={"X-API-Key": "test-admin-key"})
    assert r.status_code == 200 and r.json()["reviewer"] == "API key"
    assert client.get("/admin/queue", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/admin/queue", headers=_boss()).status_code == 200


def test_queue_shape_and_counts(client):
    j = client.get("/admin/queue", headers=_boss()).json()
    for k in ("counts", "object_submissions", "submissions", "datasets", "listings", "registrations",
              "entries", "claims", "harvest", "studio_sources", "feedback"):
        assert k in j, k
    assert j["reviewer"] == BOSS
    c = j["counts"]
    assert set(c) >= {"object_submissions", "submissions", "datasets", "listings", "registrations", "entries",
                      "claims", "harvest", "studio_sources", "feedback", "total"}
    assert c["total"] == sum(v for k, v in c.items() if k != "total")
    tables = {d["tbl"] for d in j["datasets"]}
    assert {"studios", "artists", "objects"} <= tables
    studios = next(d for d in j["datasets"] if d["tbl"] == "studios")
    assert set(studios["counts"]) == {"total", "approved", "rejected", "pending"}
    assert [col["column"] for col in studios["columns"]] and "email_address" not in [col["column"] for col in studios["columns"]]


def test_dataset_rows_approve_reject_and_approve_all(client):
    c = connect()
    _pending_row(c, "studios", "studios-test-pending", name="Pending Test Studio", city="Nowhere", country="USA")
    _pending_row(c, "studios", "studios-test-reject", name="Rejected Test Studio", city="Nowhere", country="USA")
    j = client.get("/admin/queue", headers=_boss()).json()
    studios = next(d for d in j["datasets"] if d["tbl"] == "studios")
    assert studios["counts"]["pending"] >= 2
    assert "Pending Test Studio" in [r.get("name") for r in studios["rows"]]
    assert j["counts"]["datasets"] >= 2

    r = client.post("/admin/decide", json={"queue": "studios", "id": "studios-test-pending", "action": "approve"},
                    headers=_boss())
    assert r.status_code == 200, r.text
    assert r.json()["result"] == {"table": "studios", "row": "studios-test-pending", "status": "approved"}
    assert _approval(c, "studios", "studios-test-pending") == ("approved", BOSS)
    r = client.post("/admin/decide", json={"queue": "studios", "id": "studios-test-reject", "action": "reject"},
                    headers=_boss())
    assert r.status_code == 200 and _approval(c, "studios", "studios-test-reject") == ("rejected", BOSS)
    assert "datasets" in r.json()["counts"]
    # bad requests
    assert client.post("/admin/decide", json={"queue": "studios", "id": "nope", "action": "approve"},
                       headers=_boss()).status_code == 404
    assert client.post("/admin/decide", json={"queue": "not_a_table", "id": "1", "action": "approve"},
                       headers=_boss()).status_code == 400
    assert client.post("/admin/decide", json={"queue": "studios", "id": "x", "action": "maybe"},
                       headers=_boss()).status_code == 400
    assert client.post("/admin/decide", json={"queue": "studios", "action": "approve"},
                       headers=_boss()).status_code == 400

    _pending_row(c, "artists", "artists-test-a", artist_name="Test Artist A")
    _pending_row(c, "artists", "artists-test-b", artist_name="Test Artist B")
    r = client.post("/admin/approve-all", json={"tbl": "artists"}, headers=_boss())
    assert r.status_code == 200, r.text
    assert r.json()["approved"] >= 2 and r.json()["counts"]["pending"] == 0
    assert _approval(c, "artists", "artists-test-a") == ("approved", BOSS)
    assert client.post("/admin/approve-all", json={"tbl": "sqlite_master"}, headers=_boss()).status_code == 400


def test_object_submission_image_and_promotion(client):
    from scripts.seed_demo import _demo_png_b64
    c = connect()
    c.execute("DELETE FROM object_submissions WHERE content_hash='test-queue-hash'")
    cur = c.execute("""INSERT INTO object_submissions (content_hash, submitted_uid, submitted_by, title, maker,
                       year, techniques, materials, description, sourcing, status)
                       VALUES ('test-queue-hash','u1','U One','Queue Vase','U One','2026','Offhand Blown Glass',
                               'glass','A vase for the queue test.','studio','pending')""")
    sid = cur.lastrowid
    c.execute("INSERT INTO object_submission_images (submission_id, role, caption, image_b64) VALUES (?,?,?,?)",
              (sid, "primary", "front", _demo_png_b64("#e8a44a")))
    c.commit()
    j = client.get("/admin/queue", headers=_boss()).json()
    s = next(x for x in j["object_submissions"] if x["id"] == sid)
    assert s["images"] == 1 and s["image_url"] == f"/api/admin/submissions/{sid}/image"
    assert j["counts"]["object_submissions"] >= 1
    img = client.get(f"/admin/submissions/{sid}/image", headers=_boss())
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert client.get(f"/admin/submissions/{sid}/image?i=3", headers=_boss()).status_code == 404
    assert client.get(f"/admin/submissions/{sid}/image").status_code == 401

    r = client.post("/admin/decide", json={"queue": "object_submissions", "id": sid, "action": "approve"},
                    headers=_boss())
    assert r.status_code == 200, r.text
    rid = r.json()["result"]["published"]
    assert c.execute("SELECT title FROM objects WHERE _row_id=?", (rid,)).fetchone()[0] == "Queue Vase"
    assert _approval(c, "objects", rid)[0] == "approved"
    assert c.execute("SELECT status FROM object_submissions WHERE id=?", (sid,)).fetchone()[0] == "approved"
    assert sid not in [x["id"] for x in client.get("/admin/queue", headers=_boss()).json()["object_submissions"]]


def test_artist_profile_promotion(client):
    import json as _json
    c = connect()
    c.execute("""CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT UNIQUE,
        display_name TEXT, payload_json TEXT NOT NULL, status TEXT DEFAULT 'pending_review',
        sourcing TEXT DEFAULT 'self-submitted', received_at TEXT DEFAULT (datetime('now')))""")
    c.execute("DELETE FROM submissions WHERE content_hash IN ('prof-test-1','prof-test-2')")
    payload = {"display_name": "Profile Tester", "website": "https://example.com/p", "nationality_base": "Tacoma, WA",
               "status": "Living / Active", "techniques": ["Offhand Blown Glass"], "contributions": [],
               "career_highlights": "Tested the queue."}
    c.execute("INSERT INTO submissions (content_hash, display_name, payload_json) VALUES (?,?,?)",
              ("prof-test-1", "Profile Tester", _json.dumps(payload)))
    c.execute("INSERT INTO submissions (content_hash, display_name, payload_json) VALUES (?,?,?)",
              ("prof-test-2", "Profile Reject", _json.dumps(dict(payload, display_name="Profile Reject"))))
    c.commit()
    ids = {r[0]: r[1] for r in c.execute("SELECT content_hash, id FROM submissions WHERE content_hash LIKE 'prof-test-%'")}
    j = client.get("/admin/queue", headers=_boss()).json()
    s = next(x for x in j["submissions"] if x["id"] == ids["prof-test-1"])
    assert s["payload"]["display_name"] == "Profile Tester" and "payload_json" not in s
    r = client.post("/admin/decide", json={"queue": "submissions", "id": ids["prof-test-1"], "action": "approve"},
                    headers=_boss())
    assert r.status_code == 200, r.text
    rid = r.json()["result"]["published"]
    assert c.execute("SELECT display_name FROM profiles WHERE _row_id=?", (rid,)).fetchone()[0] == "Profile Tester"
    assert _approval(c, "profiles", rid) == ("approved", BOSS)
    assert c.execute("SELECT 1 FROM _datasets WHERE tbl='profiles'").fetchone()
    r = client.post("/admin/decide", json={"queue": "submissions", "id": ids["prof-test-2"], "action": "reject"},
                    headers=_boss())
    assert r.status_code == 200
    assert c.execute("SELECT status FROM submissions WHERE id=?", (ids["prof-test-2"],)).fetchone()[0] == "rejected"


def test_season_entries_and_claims(client):
    from central import seasons
    c = connect()
    seasons.ensure(c)
    sid = c.execute("SELECT id FROM seasons WHERE slug='pumpkin-patch-2026'").fetchone()[0]
    for n in ("1", "2"):
        rid = f"obj-test-queue-{n}"
        c.execute("DELETE FROM season_entries WHERE object_row_id=?", (rid,))
        c.execute("""INSERT OR REPLACE INTO objects (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,
                     techniques,materials,content_hash,published_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (rid, "test", "test", _now(), f"Queued Gourd {n}", "Q Maker", "2026", "Offhand Blown Glass",
                   "glass", f"queue-hash-{n}", _now()))
        c.execute("INSERT OR REPLACE INTO _approvals (tbl,row_id,status,reviewed_at,reviewer) VALUES "
                  "('objects',?,'approved',?,'test')", (rid, _now()))
        c.execute("""INSERT INTO season_entries (season_id, object_row_id, artist_uid, artist_name, title,
                     edition_size, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)""",
                  (sid, rid, f"q-maker-{n}", "Q Maker", f"Queued Gourd {n}", 3, _now()))
    c.commit()
    eids = {r[0]: r[1] for r in c.execute("SELECT object_row_id, id FROM season_entries WHERE object_row_id LIKE 'obj-test-queue-%'")}
    j = client.get("/admin/queue", headers=_boss()).json()
    assert {e["id"] for e in j["entries"]} >= set(eids.values())
    assert j["counts"]["entries"] >= 2
    e1, e2 = eids["obj-test-queue-1"], eids["obj-test-queue-2"]
    r = client.post("/admin/decide", json={"queue": "entry", "id": e1, "action": "approve"}, headers=_boss())
    assert r.status_code == 200 and seasons.get_entry(c, e1)["status"] == "approved"
    r = client.post("/admin/decide", json={"queue": "entry", "id": e2, "action": "reject"}, headers=_boss())
    assert r.status_code == 200 and seasons.get_entry(c, e2)["status"] == "rejected"
    assert client.post("/admin/decide", json={"queue": "entry", "id": 999999, "action": "approve"},
                       headers=_boss()).status_code == 404
    # a low-confidence collection request waits, then is approved
    c.execute("DELETE FROM season_claims WHERE collector_uid='q-collector'")
    c.execute("""INSERT INTO season_claims (season_id, entry_id, object_row_id, collector_uid, collector_name,
                 confidence, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)""",
              (sid, e1, "obj-test-queue-1", "q-collector", "Q Collector", 40.0, _now()))
    c.commit()
    cid = c.execute("SELECT id FROM season_claims WHERE collector_uid='q-collector'").fetchone()[0]
    j = client.get("/admin/queue", headers=_boss()).json()
    cl = next(x for x in j["claims"] if x["id"] == cid)
    assert cl["title"] == "Queued Gourd 1" and "report_json" not in cl
    r = client.post("/admin/decide", json={"queue": "claim", "id": cid, "action": "approve"}, headers=_boss())
    assert r.status_code == 200
    assert c.execute("SELECT status FROM season_claims WHERE id=?", (cid,)).fetchone()[0] == "approved"


def test_feedback_studio_sources_and_harvest_queues(client):
    from central import feedback, harvest, studio_sources
    c = connect()
    fid = feedback.submit(c, "The queue page is handy.", email="", page="/review.html", name="Tester")
    src_id, err = studio_sources.add(c, "Test Kiln Co", "https://example.com/kiln.json", city="Tacoma",
                                     country="USA", submitted_by="tester")
    assert src_id and not err
    harvest.ensure_harvest(c)
    c.execute("INSERT INTO harvested_items (artist_name, source, source_url, content_hash, caption, image_b64, "
              "harvested_at, status) VALUES ('Test Artist','instagram','https://example.com/p/1','hv-test-1',"
              "'a bowl','aGk=',?, 'pending')", (_now(),))
    c.commit()
    hid = c.execute("SELECT id FROM harvested_items WHERE content_hash='hv-test-1'").fetchone()[0]
    j = client.get("/admin/queue", headers=_boss()).json()
    assert fid in [f["id"] for f in j["feedback"]]
    s = next(x for x in j["studio_sources"] if x["id"] == src_id)
    assert "url_ok" in s and s["api_url"].startswith("https://")
    h = next(x for x in j["harvest"] if x["id"] == hid)
    assert h["image_url"] == f"/api/admin/harvest/{hid}/image" and "manifest_json" not in h
    assert client.get(f"/admin/harvest/{hid}/image", headers=_boss()).status_code == 200
    for queue, item in (("feedback", fid), ("studio_source", src_id), ("harvest", hid)):
        r = client.post("/admin/decide", json={"queue": queue, "id": item, "action": "approve"}, headers=_boss())
        assert r.status_code == 200, r.text
    assert c.execute("SELECT resolved FROM feedback WHERE id=?", (fid,)).fetchone()[0] == 1
    assert c.execute("SELECT status FROM studio_sources WHERE id=?", (src_id,)).fetchone()[0] == "approved"
    assert c.execute("SELECT status FROM harvested_items WHERE id=?", (hid,)).fetchone()[0] == "approved"


def test_users_promote_and_revoke(client):
    r = client.post("/admin/users", json={"email": "New.Person@example.com", "admin": True}, headers=_boss())
    assert r.status_code == 200 and r.json()["email"] == "new.person@example.com"
    ppl = {u["email"]: u for u in client.get("/admin/users", headers=_boss()).json()["users"]}
    assert ppl["new.person@example.com"]["is_admin"] == 1
    # the new admin can read the queue
    assert client.get("/admin/queue", headers=_auth("np", "new.person@example.com", "New")).status_code == 200
    r = client.post("/admin/users", json={"email": "new.person@example.com", "admin": False}, headers=_boss())
    assert r.status_code == 200
    ppl = {u["email"]: u for u in client.get("/admin/users", headers=_boss()).json()["users"]}
    assert ppl["new.person@example.com"]["is_admin"] == 0
    assert client.get("/admin/queue", headers=_auth("np", "new.person@example.com", "New")).status_code == 403
    # a bootstrap admin cannot be revoked here
    assert client.post("/admin/users", json={"email": BOSS, "admin": False}, headers=_boss()).status_code == 400
    assert client.post("/admin/users", json={"email": "nonsense"}, headers=_boss()).status_code == 400


def test_discord_settings_never_echo_the_webhook(client):
    from central import settings
    c = connect()
    before = settings.get(c, "discord_webhook_url", "")
    try:
        r = client.post("/admin/discord", json={"webhook": "https://discord.com/api/webhooks/123/abcdefg",
                                                "enabled": False}, headers=_boss())
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["configured"] is True and j["enabled"] is False and j["webhook_tail"] == "bcdefg"
        assert "discord.com/api" not in r.text
        g = client.get("/admin/discord", headers=_boss())
        assert g.json()["configured"] is True and "discord.com/api" not in g.text and "123" not in g.text
        assert client.post("/admin/discord", json={"webhook": "http://not-https"}, headers=_boss()).status_code == 400
        assert client.get("/admin/discord").status_code == 401
    finally:
        settings.set(c, "discord_webhook_url", before)
        settings.set(c, "discord_enabled", "1")
    assert client.get("/admin/discord", headers=_boss()).json()["configured"] is bool(before)


def test_review_page_markup():
    page = (DEPLOY / "review.html").read_text(encoding="utf-8")
    assert re.search(r'<ul id="menu" data-nav data-active=""></ul>', page)
    assert '<ul id="menu">' not in page and '<small class="tag">' not in page
    assert "Review queue — Glass Database" in page and 'name="robots" content="noindex"' in page
    assert '<span class="crumb">Admin</span>' in page and 'href="/wikibase.html"' in page
    assert "/admin/queue" in page and "/admin/decide" in page and "/admin/approve-all" in page
    assert "GDB.loginUrl" in page and "is_admin" in page
