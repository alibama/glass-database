"""The last three admin sections as API routes (api/admin_data.py) and the pages that use
them: collecting events (/events-admin.html), the tables (/datasets.html) and usage
(/analytics.html). Every route needs an administrator; every write goes through the
publication gate with the administrator as the reviewer."""
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
PAGES = ("events-admin.html", "datasets.html", "analytics.html")


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_ADMIN_EMAILS", BOSS)
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "test-admin-key")
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _tidy(demo_db):
    """The demo registry is shared by the whole session: take out what these tests put in."""
    yield
    c = connect()
    stmts = [
        ("DELETE FROM _approvals WHERE tbl='studios' AND (row_id LIKE 'studios-pages-%' OR row_id IN "
         "(SELECT _row_id FROM studios WHERE name LIKE 'Pages Test%'))", ()),
        ("DELETE FROM studios WHERE _row_id LIKE 'studios-pages-%' OR name LIKE 'Pages Test%'", ()),
        ("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM studios) WHERE tbl='studios'", ()),
        ("DELETE FROM season_entries WHERE season_id IN (SELECT id FROM seasons WHERE slug LIKE 'pages-test%')", ()),
        ("DELETE FROM season_categories WHERE season_id IN (SELECT id FROM seasons WHERE slug LIKE 'pages-test%')", ()),
        ("DELETE FROM season_prizes WHERE season_id IN (SELECT id FROM seasons WHERE slug LIKE 'pages-test%')", ()),
        ("DELETE FROM season_badges WHERE season_id IN (SELECT id FROM seasons WHERE slug LIKE 'pages-test%')", ()),
        ("DELETE FROM seasons WHERE slug LIKE 'pages-test%'", ()),
        ("DELETE FROM analytics_events WHERE surface='pages-test'", ()),
        ("DELETE FROM newsletter WHERE email='pages.test@example.com'", ()),
    ]
    for sql, args in stmts:
        try:
            c.execute(sql, args)
        except Exception:  # noqa: BLE001 — a table a test never made
            pass
    try:
        from central import snapshots
        for sp in snapshots.list_snapshots(c, "studios"):
            if sp["note"] == "before CSV import":
                c.execute(f'DROP TABLE IF EXISTS "{sp["snap_table"]}"')
                c.execute("DELETE FROM _snapshots WHERE id=?", (sp["id"],))
    except Exception:  # noqa: BLE001
        pass
    c.commit()


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def _boss():
    return _auth("boss", BOSS, "The Boss")


def _key():
    return {"X-API-Key": "test-admin-key"}


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


def _post(client, path, body=None, headers=None, method="POST"):
    return client.request(method, path, json=body if body is not None else {}, headers=headers or _boss())


# --- the gates -------------------------------------------------------------------------
def test_every_route_is_for_administrators_only(client):
    for path in ("/admin/events", "/admin/datasets", "/admin/datasets/studios", "/admin/datasets/studios/export.csv",
                 "/admin/analytics", "/admin/subscribers.csv"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=_auth()).status_code == 403, path
        assert client.get(path, headers=_boss()).status_code == 200, path
        assert client.get(path, headers=_key()).status_code == 200, path
    for method, path in (("POST", "/admin/events"), ("POST", "/admin/events/1/status"), ("DELETE", "/admin/events/1"),
                         ("POST", "/admin/events/entries/1/pull"), ("POST", "/admin/datasets/studios/rows"),
                         ("POST", "/admin/datasets/studios/rows/x"), ("POST", "/admin/datasets/studios/import")):
        assert client.request(method, path, json={}).status_code == 401, path
        assert client.request(method, path, json={}, headers=_auth()).status_code == 403, path
    assert client.get("/admin/events", headers={"X-API-Key": "wrong"}).status_code == 401


# --- collecting events -------------------------------------------------------------------
def test_create_open_vote_close_and_delete_a_collecting_event(client):
    from central import seasons
    c = connect()
    j = client.get("/admin/events", headers=_boss()).json()
    assert {"summary", "events", "presets", "defaults", "review_mode", "statuses"} <= set(j)
    assert [p["label"] for p in j["presets"]] == ["Pumpkins", "Ornaments", "General"]
    assert "Best stem | vote" in j["presets"][0]["categories_text"]
    assert "Biggest pumpkin, solo | measured | solo" in j["presets"][0]["categories_text"]
    assert j["defaults"]["votes_per_voter"] == 3 and j["defaults"]["rules"]
    demo = next(e for e in j["events"] if e["slug"] == "pumpkin-patch-2026")
    assert demo["status"] == "open" and demo["entry_count"] == 3 and len(demo["entries"]) == 3
    assert demo["page"] == "/games.html?season=pumpkin-patch-2026"
    assert {cat["slug"] for cat in demo["categories"]} >= {"best-stem", "best-colour"}

    # a draft, with award categories from the textarea
    r = _post(client, "/admin/events", {
        "title": "Pages Test Gourds", "theme": "Pumpkins for the test", "description": "A test event.",
        "starts": "2031-10-01", "entries_close": "2031-10-20", "voting_closes": "2031-10-28", "ends": "2031-10-31",
        "rules": "Be kind.", "prize_note": "A nod.", "votes_per_voter": 2,
        "categories": "Best handle | vote\nTallest | measured | solo\n"})
    assert r.status_code == 200, r.text
    ev = r.json()["event"]
    sid = ev["id"]
    assert ev["slug"] == "pages-test-gourds" and ev["status"] == "draft" and ev["votes_per_voter"] == 2
    assert [(cat["title"], cat["kind"], cat["solo_only"]) for cat in ev["categories"]] == \
        [("Best handle", "vote", 0), ("Tallest", "measured", 1)]
    assert ev["entries"] == [] and ev["entry_count"] == 0
    assert "pages-test-gourds" not in {s["slug"] for s in seasons.list_seasons(c)}          # drafts stay off the page

    # bad requests
    assert _post(client, "/admin/events", {"title": ""}).status_code == 400
    assert _post(client, "/admin/events", {"title": "x", "starts": "soon"}).status_code == 400
    assert _post(client, "/admin/events", {"title": "x", "votes_per_voter": "many"}).status_code == 400
    assert _post(client, f"/admin/events/{sid}/status", {"status": "paused"}).status_code == 400
    assert _post(client, "/admin/events/999999/status", {"status": "open"}).status_code == 404

    # edit the words and dates
    r = _post(client, f"/admin/events/{sid}", {"title": "Pages Test Gourds II", "theme": "Pumpkins", "starts": "2031-10-02",
                                               "entries_close": "2031-10-21", "voting_closes": "2031-10-29",
                                               "ends": "2031-11-01", "rules": "Be kinder.", "prize_note": "A wave.",
                                               "votes_per_voter": 4})
    assert r.status_code == 200, r.text
    assert r.json()["event"]["title"] == "Pages Test Gourds II" and r.json()["event"]["votes_per_voter"] == 4
    assert seasons.get_season(c, sid)["slug"] == "pages-test-gourds"                        # the slug never changes
    assert _post(client, f"/admin/events/{sid}", {"title": ""}).status_code == 400

    # open → voting → closed
    r = _post(client, f"/admin/events/{sid}/status", {"status": "open"})
    assert r.status_code == 200 and r.json()["event"]["status"] == "open"
    assert "pages-test-gourds" in {s["slug"] for s in seasons.list_seasons(c)}
    # an approved entry on it (a demo piece; the review queue's entry id is what Pull takes)
    c.execute("""INSERT INTO season_entries (season_id, object_row_id, artist_uid, artist_name, title, edition_size,
                 status, created_at) VALUES (?,?,?,?,?,?,'approved',?)""",
              (sid, "obj-demo-0002", "demo-maker-theo", "Theo Park", "Blue Heirloom Ornament", 20, _now()))
    c.commit()
    eid = c.execute("SELECT id FROM season_entries WHERE season_id=? AND object_row_id='obj-demo-0002'", (sid,)).fetchone()[0]
    ev = next(e for e in client.get("/admin/events", headers=_boss()).json()["events"] if e["id"] == sid)
    assert [e["id"] for e in ev["entries"]] == [eid] and ev["entry_count"] == 1
    assert ev["entries"][0]["title"] == "Blue Heirloom Ornament" and ev["entries"][0]["rarity"] in ("common", "uncommon", "rare")
    r = _post(client, f"/admin/events/{sid}/status", {"status": "voting"})
    assert r.status_code == 200 and r.json()["event"]["status"] == "voting"
    # pull the entry off the page
    r = _post(client, f"/admin/events/entries/{eid}/pull")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert seasons.get_entry(c, eid)["status"] == "rejected"
    assert _post(client, "/admin/events/entries/999999/pull").status_code == 404
    ev = next(e for e in client.get("/admin/events", headers=_boss()).json()["events"] if e["id"] == sid)
    assert ev["entries"] == [] and ev["entry_count"] == 0
    r = _post(client, f"/admin/events/{sid}/status", {"status": "closed"})
    assert r.status_code == 200, r.text
    assert r.json()["event"]["status"] == "closed" and r.json()["crowd_favourite"] is None    # no votes were cast
    assert seasons.get_season(c, sid)["status"] == "closed"
    # a closed event cannot be deleted; a draft can
    assert _post(client, f"/admin/events/{sid}", method="DELETE").status_code == 409
    r = _post(client, "/admin/events", {"title": "Pages Test Draft", "theme": "ornaments"})
    draft = r.json()["id"]
    assert [cat["title"] for cat in r.json()["event"]["categories"]][0] == "Best hook"      # the theme's defaults
    assert _post(client, f"/admin/events/{draft}", method="DELETE").status_code == 200
    assert seasons.get_season(c, draft) is None
    assert not c.execute("SELECT 1 FROM season_categories WHERE season_id=?", (draft,)).fetchone()
    assert _post(client, f"/admin/events/{draft}", method="DELETE").status_code == 404
    # created open: on the page at once
    r = _post(client, "/admin/events", {"title": "Pages Test Open", "open": True})
    assert r.json()["event"]["status"] == "open"
    _post(client, f"/admin/events/{r.json()['id']}/status", {"status": "closed"})


# --- the tables ---------------------------------------------------------------------------
def test_tables_list_rows_and_search(client):
    j = client.get("/admin/datasets", headers=_boss()).json()
    tables = {t["tbl"]: t for t in j["tables"]}
    assert {"studios", "artists", "objects", "studio_intake"} <= set(tables)       # the restricted one too
    studios = tables["studios"]
    assert set(studios["counts"]) == {"total", "approved", "rejected", "pending"}
    cols = {c["column"]: c for c in studios["columns"]}
    assert cols["name"]["is_public"] == 1 and cols["email_address"]["is_public"] == 0 and cols["name"]["label"] == "Studio Name"
    assert not any(c["column"].startswith("_") for c in studios["columns"])

    r = client.get("/admin/datasets/studios?limit=2", headers=_boss())
    assert r.status_code == 200
    j = r.json()
    assert j["tbl"] == "studios" and j["limit"] == 2 and len(j["rows"]) == 2 and j["total"] >= 5
    assert {"_row_id", "_status", "name", "email_address"} <= set(j["rows"][0])
    assert j["rows"][0]["_status"] == "approved"
    r = client.get("/admin/datasets/studios?q=Blue%20Ridge", headers=_boss()).json()
    assert [x["name"] for x in r["rows"]] == ["Blue Ridge Hot Glass"] and r["total"] == 1
    assert client.get("/admin/datasets/sqlite_master", headers=_boss()).status_code == 404
    assert client.get("/admin/datasets/studios?limit=9999", headers=_boss()).status_code == 422


def test_a_cell_edit_publishes_the_row(client):
    c = connect()
    _pending_row(c, "studios", "studios-pages-edit", name="Pages Test Studio", city="Nowhere", country="USA")
    assert _approval(c, "studios", "studios-pages-edit") == (None, None)
    row = next(x for x in client.get("/admin/datasets/studios?q=Pages%20Test", headers=_boss()).json()["rows"]
               if x["_row_id"] == "studios-pages-edit")
    assert row["_status"] == "pending"
    r = _post(client, "/admin/datasets/studios/rows/studios-pages-edit", {"column": "city", "value": "Elsewhere"})
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"city": "Elsewhere"} and r.json()["status"] == "approved"
    assert c.execute("SELECT city FROM studios WHERE _row_id='studios-pages-edit'").fetchone()[0] == "Elsewhere"
    assert _approval(c, "studios", "studios-pages-edit") == ("approved", BOSS)
    # several cells at once, with the API key as the reviewer
    r = _post(client, "/admin/datasets/studios/rows/studios-pages-edit", {"values": {"city": "Here", "region": "VA"}},
              headers=_key())
    assert r.status_code == 200
    assert c.execute("SELECT city, region FROM studios WHERE _row_id='studios-pages-edit'").fetchone()[:2] == ("Here", "VA")
    assert _approval(c, "studios", "studios-pages-edit") == ("approved", "API key")
    # what cannot be done
    assert _post(client, "/admin/datasets/studios/rows/studios-pages-edit", {"column": "_row_id", "value": "x"}).status_code == 400
    assert _post(client, "/admin/datasets/studios/rows/studios-pages-edit", {"column": "nope", "value": "x"}).status_code == 400
    assert _post(client, "/admin/datasets/studios/rows/studios-pages-edit", {}).status_code == 400
    assert _post(client, "/admin/datasets/studios/rows/no-such-row", {"column": "city", "value": "x"}).status_code == 404
    assert _post(client, "/admin/datasets/nope/rows/x", {"column": "city", "value": "x"}).status_code == 404


def test_add_a_row(client):
    c = connect()
    before = c.execute("SELECT row_count FROM _datasets WHERE tbl='studios'").fetchone()[0]
    r = _post(client, "/admin/datasets/studios/rows", {"values": {"name": "Pages Test Added", "city": "Crozet", "country": "USA"}})
    assert r.status_code == 200, r.text
    rid = r.json()["row_id"]
    assert len(rid) == 16 and r.json()["status"] == "approved"
    row = c.execute("SELECT name, city, _source_file FROM studios WHERE _row_id=?", (rid,)).fetchone()
    assert tuple(row) == ("Pages Test Added", "Crozet", "admin-ui")
    assert _approval(c, "studios", rid) == ("approved", BOSS)
    assert c.execute("SELECT row_count FROM _datasets WHERE tbl='studios'").fetchone()[0] == before + 1
    # it is public at once
    assert "Pages Test Added" in [x["name"] for x in client.get("/datasets/studios?q=Pages%20Test%20Added").json()["rows"]]
    assert _post(client, "/admin/datasets/studios/rows", {"values": {"name": " ", "city": ""}}).status_code == 400
    assert _post(client, "/admin/datasets/studios/rows", {"values": {"_imported_at": "x"}}).status_code == 400
    assert _post(client, "/admin/datasets/studios/rows", {"values": "name=x"}).status_code == 400


def test_csv_import_upserts_by_row_id(client):
    from central import snapshots
    c = connect()
    _pending_row(c, "studios", "studios-pages-csv", name="Pages Test CSV", city="Old Town", country="USA")
    csv_text = ("_row_id,name,city,bogus_column\r\n"
                "studios-pages-csv,Pages Test CSV,New Town,ignored\r\n"
                ",Pages Test CSV Two,Somewhere,ignored\r\n"
                "studios-pages-csv-given,Pages Test CSV Three,Elsewhere,\r\n"
                ",,,\r\n")
    r = client.post("/admin/datasets/studios/import", content=csv_text.encode("utf-8-sig"),
                    headers={**_boss(), "Content-Type": "text/csv"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["updated"], j["added"], j["skipped"]) == (1, 2, 1) and j["columns"] == ["name", "city"]
    assert c.execute("SELECT city FROM studios WHERE _row_id='studios-pages-csv'").fetchone()[0] == "New Town"
    assert _approval(c, "studios", "studios-pages-csv") == ("approved", BOSS)
    two = c.execute("SELECT _row_id, _source_file FROM studios WHERE name='Pages Test CSV Two'").fetchone()
    assert two and two[1] == "csv-import" and _approval(c, "studios", two[0]) == ("approved", BOSS)
    assert c.execute("SELECT city FROM studios WHERE _row_id='studios-pages-csv-given'").fetchone()[0] == "Elsewhere"
    assert c.execute("SELECT COUNT(*) FROM studios WHERE name LIKE 'Pages Test CSV%'").fetchone()[0] == 3
    assert snapshots.list_snapshots(c, "studios")[0]["note"] == "before CSV import"
    # a sheet with none of the table's columns, and an empty one
    r = client.post("/admin/datasets/studios/import", content=b"a,b\r\n1,2\r\n", headers={**_boss(), "Content-Type": "text/csv"})
    assert r.status_code == 400
    r = client.post("/admin/datasets/studios/import", content=b"", headers={**_boss(), "Content-Type": "text/csv"})
    assert r.status_code == 400


def test_export_csv_has_every_column(client):
    r = client.get("/admin/datasets/studios/export.csv", headers=_boss())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert lines[0].startswith("_row_id,") and "email_address" in lines[0] and "name" in lines[0]
    assert any("Blue Ridge Hot Glass" in line for line in lines[1:])


# --- usage ---------------------------------------------------------------------------------
def test_analytics_summary_and_the_newsletter_export(client):
    from central import analytics
    c = connect()
    for _ in range(3):
        analytics.log(c, "pages-test", "home", "view", ip="10.0.0.1")
    analytics.log(c, "pages-test", "home", "view", ip="10.0.0.2")
    analytics.log(c, "pages-test", "", "submit:test")
    c.execute("CREATE TABLE IF NOT EXISTS newsletter (email TEXT PRIMARY KEY, name TEXT, created_at TEXT, source TEXT)")
    c.execute("INSERT OR REPLACE INTO newsletter (email, name, created_at, source) VALUES (?,?,?,?)",
              ("pages.test@example.com", "Pages, Tester", _now(), "test"))
    c.commit()
    for days in (7, 30, 90):
        r = client.get(f"/admin/analytics?days={days}", headers=_boss())
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["days"] == days
        assert {"views", "visitors", "submissions", "by_day", "by_surface", "by_view", "by_event", "by_country", "newsletter"} <= set(j)
        assert j["views"] >= 4 and j["visitors"] >= 2 and j["submissions"] >= 1
        surf = next(x for x in j["by_surface"] if x["surface"] == "pages-test")
        assert surf["views"] == 4 and surf["visitors"] == 2
        assert any(x["view"] == "pages-test · home" for x in j["by_view"])
        assert any(x["event"] == "submit:test" for x in j["by_event"])
        assert j["by_day"] and set(j["by_day"][0]) == {"day", "views", "visitors"}
        assert j["newsletter"]["count"] >= 1
    assert client.get("/admin/analytics?days=5", headers=_boss()).status_code == 400
    # the newsletter list, for a signed-in administrator and for the key
    for headers in (_boss(), _key()):
        r = client.get("/admin/subscribers.csv", headers=headers)
        assert r.status_code == 200 and r.text.startswith("email,name,created_at,source")
        assert 'pages.test@example.com,"Pages, Tester"' in r.text
    r = client.get("/subscribers.csv", headers=_boss())              # the old key-only route takes the session too
    assert r.status_code == 200 and "pages.test@example.com" in r.text
    assert client.get("/subscribers.csv", headers=_key()).status_code == 200
    assert client.get("/subscribers.csv").status_code == 403
    assert client.get("/subscribers.csv", headers=_auth()).status_code == 403


# --- the pages ------------------------------------------------------------------------------
@pytest.mark.parametrize("page", PAGES)
def test_admin_page_markup(page):
    s = (DEPLOY / page).read_text(encoding="utf-8")
    assert re.search(r'<ul id="menu" data-nav data-active=""></ul>', s)
    assert '<ul id="menu">' not in s and '<small class="tag">' not in s
    assert 'name="robots" content="noindex"' in s and "— Glass Database</title>" in s
    assert '<span class="crumb">Admin</span>' in s
    for other in ("/review.html", "/events-admin.html", "/datasets.html", "/analytics.html", "/wikibase.html"):
        assert f'href="{other}"' in s, (page, other)
    assert f'href="/{page}" class="on"' in s
    assert "GDB.loginUrl" in s and "is_admin" in s and "GDB.chip" in s
    assert "/admin/" not in s.replace("/admin/events", "").replace("/admin/datasets", "").replace("/admin/analytics", "") \
        .replace("/admin/subscribers.csv", "").replace("/admin/decide", ""), "the page still links to the old console"


def test_pages_use_their_routes():
    ev = (DEPLOY / "events-admin.html").read_text(encoding="utf-8")
    assert '"/admin/events"' in ev and "/status" in ev and "/pull" in ev and "DELETE" in ev and "/prizes" in ev
    ds = (DEPLOY / "datasets.html").read_text(encoding="utf-8")
    assert '"/admin/datasets"' in ds and "/rows" in ds and "/import" in ds and "/export.csv" in ds and '"/admin/decide"' in ds
    an = (DEPLOY / "analytics.html").read_text(encoding="utf-8")
    assert "/admin/analytics?days=" in an and "/admin/subscribers.csv" in an
    assert "<svg" in an and "viewBox" in an and "<script src=" not in an.replace('<script src="/site.js">', "")


def test_the_pages_are_registered_everywhere():
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    dev = (ROOT / "scripts" / "dev_site.py").read_text(encoding="utf-8")
    proxy = (DEPLOY / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8")
    m = re.search(r"RedirectMatch 301 \^/\((.*?)\)/\?\$ /\$1\.html", proxy)
    assert m
    pretty = set(m.group(1).split("|"))
    for page in PAGES:
        assert f'cp "$SRC/deploy/{page}"' in install, page
        assert f'"/{page}": "{page}"' in dev, page
        assert page[:-5] in pretty, page
    for page in ("review.html", "wikibase.html", "account.html"):
        s = (DEPLOY / page).read_text(encoding="utf-8")
        for other in PAGES:
            assert f'href="/{other}"' in s, (page, other)
        assert 'href="/admin/"' not in s, f"{page} still links to the old console"
    console = (ROOT / "admin" / "app.py").read_text(encoding="utf-8")
    for other in PAGES:
        assert f"](/{other})" in console, other
