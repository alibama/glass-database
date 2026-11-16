"""Partner lineage feeds (cvilleglass.org residents → lineage_submissions → graph)."""
import copy
import hashlib

from central import approvals, graph
from central import lineage_feeds as LF
from central.dbconn import connect

FEED_URL = "https://cvilleglass.org/api/residents.php"
FEED = {
    "studio": "Charlottesville Glass",
    "residents": [{
        "name": "Anson Parker", "slug": "anson-parker",
        "url": "https://cvilleglass.org/residency/anson-parker", "year": 2026,
        "project_title": "", "summary": "", "gdb_gid": None, "gdb_url": None,
        "headshot_url": None, "cv_url": None, "updated_at": "2026-11-15T00:00:00Z",
        "lineage": [
            {"student_name": "Anson Parker", "student_gid": None,
             "mentor_name": "Hanna Taubenberger", "mentor_gid": None,
             "relation": "Assisted", "where": "Raging Buffalo Glass Studio", "years": "2026",
             "note": "", "source": None},
            {"student_name": "Anson Parker", "student_gid": None,
             "mentor_name": "Quill Feedmentor", "mentor_gid": None,
             "relation": "took a class / workshop from", "where": "", "years": "2025",
             "note": "Weekend workshop", "source": "javascript:alert(1)"},
            {"student_name": "Same Person", "mentor_name": "same person", "relation": "Assisted"},
            {"student_name": "", "mentor_name": "Nobody", "relation": "Assisted"},
        ]}]}


def _status(c, rid):
    r = c.execute("SELECT status, reviewer FROM _approvals WHERE tbl=? AND row_id=?",
                  (LF.TABLE, rid)).fetchone()
    return tuple(r) if r else None


def _feed_rows(c, url):
    return [dict(r) for r in c.execute(
        f'SELECT * FROM "{LF.TABLE}" WHERE _source_file=? ORDER BY student_name, mentor_name', (url,))]


def test_parse_cleans_and_maps():
    rows = LF.parse(FEED, FEED_URL)
    assert len(rows) == 2                                   # self-link and blank student dropped
    a = next(r for r in rows if r["mentor_name"] == "Hanna Taubenberger")
    assert a["relation"] == "Assisted" and a["where"] == "Raging Buffalo Glass Studio"
    assert a["source_url"] == "https://cvilleglass.org/residency/anson-parker"   # resident page
    assert a["feed_studio"] == "Charlottesville Glass"
    b = next(r for r in rows if r["mentor_name"] == "Quill Feedmentor")
    assert b["relation"] == "Took a class / workshop from"  # canonical label, case-insensitive
    assert b["source_url"].startswith("https://cvilleglass.org/")   # a non-http "source" is ignored
    # the id is stable and doesn't depend on case or spacing
    assert a["_row_id"] == LF.row_id(FEED_URL, " anson  parker", "HANNA TAUBENBERGER", "assisted")


def test_import_is_pending_and_rerunnable(demo_db):
    c = connect()
    url = FEED_URL + "?t=rerun"
    r1 = LF.import_feed(c, url, feed=FEED, notify_reviewers=False)
    assert r1["new"] == 2 and r1["changed"] == 0
    rows = _feed_rows(c, url)
    assert len(rows) == 2 and all(r["_source_sheet"] == "feed" for r in rows)
    assert all(_status(c, r["_row_id"]) is None for r in rows)          # pending: no decision
    assert not any(e.get("meta", {}).get("where") == "Raging Buffalo Glass Studio"
                   for e in graph.build(c)["edges"])                    # not public yet

    r2 = LF.import_feed(c, url, feed=FEED, notify_reviewers=False)
    assert r2["new"] == 0 and r2["unchanged"] == 2
    assert len(_feed_rows(c, url)) == 2                                 # no duplicates


def test_approved_rows_reach_graph_and_changes_go_back_to_pending(demo_db):
    c = connect()
    url = FEED_URL + "?t=graph"
    LF.import_feed(c, url, feed=FEED, notify_reviewers=False)
    ids = [r["_row_id"] for r in _feed_rows(c, url)]
    approvals.set_status(c, LF.TABLE, ids, "approved")
    g = graph.build(c)
    e = next(e for e in g["edges"] if e["type"] == "studied_under"
             and e["source"] == "artist:anson parker" and e["target"] == "artist:hanna taubenberger")
    assert e["relation"] == "Assisted" and e["meta"]["where"] == "Raging Buffalo Glass Studio"
    assert e["meta"]["confidence"] == "structured"
    assert e["meta"]["source"].startswith("https://cvilleglass.org/")

    feed2 = copy.deepcopy(FEED)
    feed2["residents"][0]["lineage"][0]["years"] = "2025–2026"
    r = LF.import_feed(c, url, feed=feed2, notify_reviewers=False)
    assert r["changed"] == 1 and r["unchanged"] == 1
    hanna = LF.row_id(url, "Anson Parker", "Hanna Taubenberger", "Assisted")
    assert _status(c, hanna) is None                                    # re-review the change
    assert len(_feed_rows(c, url)) == 2


def test_withdrawn_rows_are_hidden_then_restored(demo_db):
    c = connect()
    url = FEED_URL + "?t=withdraw"
    LF.import_feed(c, url, feed=FEED, notify_reviewers=False)
    quill = LF.row_id(url, "Anson Parker", "Quill Feedmentor", "Took a class / workshop from")
    approvals.set_status(c, LF.TABLE, [quill], "approved")
    less = copy.deepcopy(FEED)
    less["residents"][0]["lineage"].pop(1)
    r = LF.import_feed(c, url, feed=less, notify_reviewers=False)
    assert r["withdrawn"] == 1
    assert _status(c, quill) == ("rejected", LF.WITHDRAWN)
    assert len(_feed_rows(c, url)) == 2                                 # hidden, not deleted
    assert LF.import_feed(c, url, feed=less, notify_reviewers=False)["withdrawn"] == 0
    r = LF.import_feed(c, url, feed=FEED, notify_reviewers=False)
    assert r["restored"] == 1 and _status(c, quill) is None             # back, pending again


def test_graph_prefers_gid_over_typed_name(demo_db):
    c = connect()
    # a public maker whose gid the feed carries, spelled differently in the feed
    rid = hashlib.sha1(b"lineage-feed-test-profile").hexdigest()[:16]
    c.execute("""CREATE TABLE IF NOT EXISTS profiles (
        _row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, _imported_at TEXT,
        display_name TEXT, website TEXT, nationality_base TEXT, status TEXT,
        techniques TEXT, contributions TEXT, career_highlights TEXT, approved_at TEXT)""")
    c.execute("INSERT OR REPLACE INTO profiles (_row_id, display_name) VALUES (?, ?)",
              (rid, "Ottoline Gidmaker"))
    c.commit()
    approvals.set_status(c, "profiles", [rid], "approved")
    gid = f"gdb:profiles:{rid}"
    feed = {"studio": "Charlottesville Glass", "residents": [{
        "name": "O. Gidmaker", "url": "https://cvilleglass.org/residency/o", "lineage": [
            {"student_name": "O. Gidmaker", "student_gid": gid,
             "mentor_name": "Petra Plainname", "mentor_gid": "gdb:profiles:doesnotexist",
             "relation": "Studied under", "where": "Penland", "years": "2019"}]}]}
    url = FEED_URL + "?t=gid"
    LF.import_feed(c, url, feed=feed, notify_reviewers=False)
    row = _feed_rows(c, url)[0]
    assert row["student_gid"] == gid
    approvals.set_status(c, LF.TABLE, [row["_row_id"]], "approved")
    g = graph.build(c)
    e = next(e for e in g["edges"] if e["type"] == "studied_under" and e["meta"].get("where") == "Penland")
    assert e["source"] == "artist:ottoline gidmaker"                    # the gid's name, not the typed one
    assert e["target"] == "artist:petra plainname"                      # unknown gid → typed name
    assert e["meta"]["matched"] == "gid"
    node = next(n for n in g["nodes"] if n["id"] == "artist:ottoline gidmaker")
    assert node["meta"]["gid"] == gid
    assert "gid" not in next(n for n in g["nodes"] if n["id"] == "artist:petra plainname")["meta"]


def test_fetch_refuses_unsafe_urls():
    for bad in ("http://cvilleglass.org/api/residents.php", "https://localhost/feed"):
        try:
            LF.fetch(bad)
        except ValueError:
            continue
        raise AssertionError(f"fetched {bad}")


def test_import_all_reports_errors_per_feed(demo_db):
    out = LF.import_all(connect(), ["http://insecure.example/feed"])
    assert out[0]["error"]


def test_cli_from_file(demo_db, tmp_path, capsys):
    import json

    from scripts import import_lineage_feeds as cli
    f = tmp_path / "residents.json"
    f.write_text(json.dumps(FEED))
    url = FEED_URL + "?t=cli"
    assert cli.main(["--file", str(f), "--url", url, "--no-notify", "--dry-run"]) == 0
    assert "2 new" in capsys.readouterr().out and not _feed_rows(connect(), url)
    assert cli.main(["--file", str(f), "--url", url, "--no-notify"]) == 0
    assert len(_feed_rows(connect(), url)) == 2


def test_admin_route_is_admin_only_and_feed_listed(demo_db, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "test-admin-key")
    from api.main import app
    if not any(getattr(r, "path", "").startswith("/admin/") for r in app.routes):
        import api.admin as _admin
        app.include_router(_admin.router)
    cl = TestClient(app)
    assert cl.post("/admin/lineage-feeds/import", json={}).status_code == 401
    r = cl.post("/admin/lineage-feeds/import", json={"url": "https://evil.example/x"},
                headers={"X-API-Key": "test-admin-key"})
    assert r.status_code == 400                     # only operator-configured feeds are fetched
