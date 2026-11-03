"""A page for every Glass Database ID: the resolver behind /record/<gid>, the search by name,
the wiki items and their references pointing at those pages, the API and the page's wiring."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from central import records
from central.dbconn import connect
from central.wikibase import model, stage
from central.wikibase import store as wstore

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def _conn():
    c = connect()
    wstore.ensure(c)
    return c


def _staged(c):
    if not c.execute("SELECT 1 FROM wb_items WHERE kind='person' LIMIT 1").fetchone():
        stage.run(c, lambda *_: None, {"public_base": "https://glassdatabase.test"})
    return c


def test_parse_and_resolve_rows(demo_db):
    c = _conn()
    assert records.parse_gid("gdb:artists:artists-0000") == ("artists", "artists-0000")
    assert records.parse_gid("gdb:object:abc:def") == ("object", "abc:def")
    assert records.parse_gid("artists-0000") is None and records.parse_gid("") is None
    rid, name = c.execute("SELECT _row_id, artist_name FROM artists WHERE artist_name='Rae Sutter'").fetchone()
    rec = records.resolve(c, f"gdb:artists:{rid}", base="https://x.test")
    assert rec and rec["gid"] == f"gdb:artists:{rid}" and rec["kind"] == "person" and rec["label"] == name
    assert rec["url"] == f"https://x.test/record/gdb:artists:{rid}"
    cols = {f["column"] for f in rec["source"]["fields"]}
    assert "artist_name" in cols and "phone" not in cols                   # public columns only
    assert "glass artist" in rec["description"]                            # composed from the row
    assert rec["links"]["maker"].startswith("/maker.html?m=") and "explore" in rec["links"]["directory"]
    assert rec["related"]["objects"] and rec["related"]["objects"][0]["gid"].startswith("gdb:object:")
    assert rec["related"]["entries"][0]["season_slug"] == "pumpkin-patch-2026"
    assert records.resolve(c, f"gdb:artists:{rid}-nope") is None
    assert records.resolve(c, "junk") is None
    assert records.resolve(c, "gdb:studio_intake:x") is None              # a restricted table never resolves
    orid, chash, maker = c.execute("SELECT _row_id, content_hash, maker FROM objects WHERE _row_id='obj-demo-0001'").fetchone()
    o = records.resolve(c, f"gdb:object:{chash}")
    assert o["kind"] == "object" and o["links"]["verify"].endswith(orid) and o["related"]["maker"]["name"] == maker
    assert o["related"]["object"]["fingerprinted"] is True and "collected" in o["related"]
    assert records.resolve(c, f"gdb:object:{orid}")["gid"] == f"gdb:object:{chash}"   # a row id resolves to the hash key


def test_wiki_items_and_their_references_point_at_record_pages(demo_db):
    c = _staged(_conn())
    key, item_json = c.execute("SELECT key, item_json FROM wb_items WHERE kind='person' AND key LIKE 'gdb:artists:%' LIMIT 1").fetchone()
    assert json.loads(item_json)["url"] == f"https://glassdatabase.test/record/{key}"
    okey, oitem = c.execute("SELECT key, item_json FROM wb_items WHERE kind='object' LIMIT 1").fetchone()
    described = [cl for cl in json.loads(oitem)["claims"] if cl["p"] == "described at URL"]
    assert described and described[0]["v"]["v"] == f"https://glassdatabase.test/record/{okey}"
    key_prop = next(p for p in model.PROPERTIES if p.label == model.KEY_PROP)
    assert key_prop.formatter.endswith("/record/$1")
    rec = records.resolve(c, key)
    assert rec["wiki"]["key"] == key and rec["wiki"]["status"]
    props = {f["prop"] for f in rec["facts"]}
    assert "Glass Database ID" not in props and "described at URL" not in props
    assert any(f["kind"] == "item" and f.get("key") for f in rec["facts"])   # item values carry their labels and keys
    place = c.execute("SELECT key FROM wb_items WHERE kind='place' LIMIT 1").fetchone()
    if place:
        prec = records.resolve(c, place[0])
        assert prec and prec["kind"] == "place" and prec["source"]["fields"] == []


def test_search_by_name(demo_db):
    c = _staged(_conn())
    hits = records.search(c, "sutter")
    assert hits and hits[0]["label"] == "Rae Sutter" and hits[0]["gid"].startswith("gdb:")
    assert records.search(c, "r") == []
    assert {h["kind"] for h in records.search(c, "glass", limit=50)} & {"studio", "person", "technique", "object"}


def test_api_and_go_redirect(demo_db):
    c = _staged(_conn())
    from api.main import app
    client = TestClient(app)
    rid = c.execute("SELECT _row_id FROM artists WHERE artist_name='Rae Sutter'").fetchone()[0]
    gid = f"gdb:artists:{rid}"
    r = client.get(f"/records/{gid}")
    assert r.status_code == 200 and r.json()["gid"] == gid and r.json()["url"].endswith(f"/record/{gid}")
    assert client.get("/records/gdb:artists:nope").status_code == 404
    assert client.get("/records/junk").status_code == 404
    r = client.get("/records", params={"q": "sutter"})
    assert r.status_code == 200 and r.json()["records"][0]["gid"].startswith("gdb:")
    go = client.get(f"/wikibase/go/{gid}", follow_redirects=False)
    assert go.status_code == 302 and go.headers["location"] == f"/record/{gid}"


def test_the_record_page_is_wired_everywhere():
    page = (DEPLOY / "record.html").read_text(encoding="utf-8")
    assert 'id="recordBody"' in page and '"/records/"' in page and "application/ld+json" in page
    assert re.search(r"/record/\(gdb:\.\+\)", page) or "/record/" in page
    dev = (ROOT / "scripts" / "dev_site.py").read_text(encoding="utf-8")
    assert '"/record.html": "record.html"' in dev and '"/record/{gid:path}"' in dev
    proxy = (DEPLOY / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8")
    assert "^/record/(gdb:.+)$ /record.html?gid=$1" in proxy and "|record|maker|stats)" in proxy
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    for p in ("record.html", "maker.html", "stats.html"):
        assert f'cp "$SRC/deploy/{p}"' in install, p


def test_record_fields_are_nouns_without_dates_of_birth_and_links_name_the_row(demo_db):
    c = _conn()
    rid = c.execute("SELECT _row_id FROM artists WHERE artist_name='Rae Sutter'").fetchone()[0]
    rec = records.resolve(c, f"gdb:artists:{rid}")
    cols = {f["column"]: f["label"] for f in rec["source"]["fields"]}
    assert "birth_year" not in cols and cols["based_in"] == "Based In"       # per-person, never a date of birth
    assert rec["links"]["directory"] == "/explore/?view=datasets&q=Rae%20Sutter&table=artists"
    orec = records.resolve(c, "gdb:object:obj-demo-0001")
    assert orec and orec["links"]["piece"] == "/explore/?view=objects&piece=obj-demo-0001"


def test_record_fields_fold_a_grid_question_into_one_line(demo_db):
    c = _conn()
    rows = [("gridtest", "artist_name", "Artist Name", 0, 1),
            ("gridtest", "hot_offhand", "Hot Glass & Flameworking Techniques [Offhand Blown Glass]", 1, 1),
            ("gridtest", "hot_cane", "Hot Glass & Flameworking Techniques [Cane & Murrine]", 2, 1),
            ("gridtest", "kiln_fusing", "Kiln, Cold Work & Architectural Techniques [Fusing & Slumping]", 3, 1)]
    c.executemany("INSERT OR REPLACE INTO _columns (tbl, column, label, ordinal, is_public) VALUES (?,?,?,?,?)", rows)
    try:
        fields = records._fields(c, "gridtest", {"artist_name": "Rae", "hot_offhand": "Primary Focus",  # noqa: SLF001
                                                 "hot_cane": "", "kiln_fusing": "Occasional Use"})
    finally:
        c.execute("DELETE FROM _columns WHERE tbl='gridtest'")
        c.commit()
    assert fields == [{"column": "artist_name", "label": "Artist Name", "value": "Rae"},
                      {"column": "group:Techniques", "label": "Techniques",
                       "value": "Offhand Blown Glass (primary), Fusing & Slumping (occasional)"}]
