"""Prizes on a collecting event: the makers and studios boards behind them, the defaults a
theme gets, the textarea round trip, the admin's edits, the winners written at close-out,
and the HTTP surface (public reads with no account ids in them; admin-only writes)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_seasons import _auth, _client, _conn, _entry, _object, _season

from central import seasons as S
from central import sessions
from central.dbconn import connect

ROOT = Path(__file__).resolve().parent.parent
BOSS = "prizes.boss@example.com"


@pytest.fixture(autouse=True)
def _review_mode(monkeypatch):
    """Entries wait for an admin here, as in test_seasons; the helpers approve them."""
    monkeypatch.setenv("GLASSDB_SEASON_ENTRY_REVIEW", "1")


@pytest.fixture
def admin(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_ADMIN_EMAILS", BOSS)
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "test-admin-key")
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


def _boss():
    return {"Authorization": f"Bearer {sessions.issue('prizes-boss', BOSS, 'The Boss')}"}


# --- the boards the prizes read -------------------------------------------------------------
def test_studios_and_makers_boards(demo_db):
    c = _conn()
    sid = _season(c)
    # Shop A: two makers, the shop spelled two ways · Shop B: one maker, two registered pieces
    # · one entry with no shop at all
    e1, r1 = _entry(c, sid, "pz-a1", studio="Shop A", title="A one", fingerprint=True)
    e2, r2 = _entry(c, sid, "pz-a2", studio="shop  a", title="A two")
    e3, r3 = _entry(c, sid, "pz-b", studio="Shop B", title="B one", fingerprint=True)
    e4, r4 = _entry(c, sid, "pz-b", studio="Shop B", title="B two", fingerprint=True)
    e5, r5 = _entry(c, sid, "pz-solo", studio="", title="Loner")
    S.vote(c, sid, e2, "pz-v1"); S.vote(c, sid, e2, "pz-v2")
    S.claim(c, object_row_id=r3, collector_uid="pz-c1", collector_name="Col One", confidence=90)
    S.claim(c, object_row_id=r1, collector_uid="pz-c1", collector_name="Col One", confidence=90)
    lb = S.leaderboard(c, sid)
    studios = lb["studios"]
    assert [s["studio"] for s in studios] == ["Shop A", "Shop B", "Independent"]   # makers first, Independent last
    a, b, ind = studios
    assert a["makers"] == 2 and a["entries"] == 2 and a["registered"] == 1 and a["votes"] == 2
    assert not a["independent"] and a["claims"] == 1
    assert b["makers"] == 1 and b["entries"] == 2 and b["registered"] == 2 and b["claims"] == 1
    assert ind["independent"] and ind["makers"] == 1 and [s["rank"] for s in studios] == [1, 2, 3]
    makers = lb["makers"]
    assert [m["artist_uid"] for m in makers][:2] == ["pz-b", "pz-a1"]            # two registered beats one
    assert makers[0]["registered"] == 2 and makers[0]["studio"] == "Shop B" and makers[0]["rank"] == 1
    assert [m["rank"] for m in makers] == [1, 2, 3, 4]
    pts = {h["collector_name"]: h for h in lb["points"]}
    assert pts["Col One"]["pieces"] == 2 and pts["Col One"]["studios"] == 2
    names = {s["name"] for s in S.known_studios(c)}
    assert {"Shop A", "Shop B", "Blue Ridge Hot Glass"} <= names and "shop  a" not in names
    listed = {s["name"]: s for s in S.known_studios(c)}
    assert listed["Blue Ridge Hot Glass"]["listed"] and listed["Blue Ridge Hot Glass"]["city"] == "Crozet"
    assert not listed["Shop A"]["listed"]
    # an admin sets an entry's shop right; blank means Independent
    assert S.set_entry_studio(c, e2, "  Shop   A ")["studio"] == "Shop A"
    assert S.leaderboard(c, sid)["studios"][0]["studio"] == "Shop A"
    assert S.set_entry_studio(c, e5, "")["studio"] == ""
    with pytest.raises(ValueError):
        S.set_entry_studio(c, 999999, "x")
    S.set_status(c, sid, "closed")                 # leave no open season behind for the other modules


# --- prizes --------------------------------------------------------------------------------
def test_prizes_defaults_edits_standings_and_close(demo_db):
    c = _conn()
    sid = _season(c, theme="Pumpkins again")
    rows = S.prizes(c, sid)
    assert [p["title"] for p in rows] == ["Most makers from one shop", "Most registered pumpkins",
                                          "Crowd favourite", "Collector of the event"]
    assert rows[0]["awarded_for"] == "studio_most_makers" and rows[0]["for_label"] == "Studio with the most makers"
    assert rows[0]["who"] == "studio" and rows[0]["unit"] == "makers" and rows[0]["slug"] == "most-makers-from-one-shop"
    empty = _season(c, theme="Things", prizes=[])
    assert S.prizes(c, empty) == []                                              # an empty list means none
    S.set_status(c, empty, "closed")
    assert S.default_prizes("ornaments")[1]["title"] == "Most registered ornaments"
    assert S.default_prizes("")[1]["title"] == "Most registered pieces"

    # the textarea round trip: a kind's label, an award's title, a judges' pick
    parsed = S.parse_prizes("Biggest shelf | Collector with the most pieces | A shelf | Rod Co | https://rods.example\n"
                            "Stem of the year | best stem\n\n | ignored\nJudged | judges")
    assert [r["awarded_for"] for r in parsed] == ["Collector with the most pieces", "best stem", "judges"]
    assert parsed[0]["sponsor_url"] == "https://rods.example" and parsed[0]["sponsor"] == "Rod Co"
    psid = _season(c, theme="pumpkins", prizes=parsed)
    got = S.prizes(c, psid)
    assert [p["awarded_for"] for p in got] == ["collector_most_pieces", "award:best-stem", "judges"]
    assert got[1]["for_label"] == "Best stem" and got[1]["unit"] == "picks" and got[2]["who"] == "hand"
    again = S.parse_prizes(S.prizes_text(got))
    assert [r["awarded_for"] for r in again] == ["Collector with the most pieces", "award:best-stem", "Judges' pick"]
    assert again[0]["detail"] == "A shelf" and again[0]["sponsor_url"] == "https://rods.example"
    with pytest.raises(ValueError):
        S.add_prize(c, psid, title="Nope", awarded_for="the vibes")
    with pytest.raises(ValueError):
        S.add_prize(c, psid, title="Nope", awarded_for="judges", sponsor_url="rods.example")
    with pytest.raises(ValueError):
        S.add_prize(c, psid, title="   ", awarded_for="judges")
    n = c.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
    with pytest.raises(ValueError):                                  # a bad line leaves no half-made season
        _season(c, theme="x", prizes=[{"title": "Bad", "awarded_for": "nothing at all"}])
    assert c.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == n

    # standings come straight off the boards
    e1, r1 = _entry(c, sid, "pz-m1", studio="Hot Shop", title="One", fingerprint=True)
    e2, r2 = _entry(c, sid, "pz-m2", studio="Hot Shop", title="Two")
    e3, r3 = _entry(c, sid, "pz-m3", studio="Cold Shop", title="Three", fingerprint=True)
    e4, r4 = _entry(c, sid, "pz-m3", studio="Cold Shop", title="Four", fingerprint=True)
    S.vote(c, sid, e2, "pz-v9")
    S.claim(c, object_row_id=r1, collector_uid="pz-c9", collector_name="Nine", confidence=95)
    st = {p["awarded_for"]: p for p in S.prize_standings(c, sid)}
    assert st["studio_most_makers"]["leader"]["name"] == "Hot Shop" and st["studio_most_makers"]["leader"]["value"] == 2
    assert st["studio_most_makers"]["standings"][1]["name"] == "Cold Shop"
    assert st["maker_most_registered"]["leader"]["name"] == "Maker pz-m3"
    assert st["maker_most_registered"]["leader"]["value"] == 2 and st["maker_most_registered"]["leader"]["sub"] == "Cold Shop"
    assert st["peoples_choice"]["leader"]["entry_id"] == e2 and st["peoples_choice"]["leader"]["unit"] == "votes"
    assert st["collector_most_points"]["leader"]["name"] == "Nine" and st["collector_most_points"]["winner"] == ""
    assert not st["collector_most_points"]["settled"]
    for p in st.values():
        for s in p["standings"]:
            assert "artist_uid" not in s and "collector_uid" not in s
    assert S.prize_board(c, sid, "first_in")[0]["entry_id"] == e1
    assert S.prize_board(c, sid, "most_collected")[0]["entry_id"] == e1
    pieces = S.prize_board(c, sid, "studio_most_pieces")
    assert pieces[0]["value"] == 2 and pieces[0]["name"] == "Hot Shop"       # two pieces each; Hot Shop's collection breaks the tie
    assert S.prize_board(c, sid, "collector_most_studios")[0]["value"] == 1
    assert S.prize_board(c, sid, "rookie")[0]["name"] == "Maker pz-m2"          # the one voted maker new to the events
    assert S.prize_board(c, sid, "judges") == [] and S.prize_board(c, sid, "award:no-such") == []
    assert S.prize_board(c, sid, "maker_most_votes")[0]["name"] == "Maker pz-m2"

    # a judges' pick has no standings until an admin names someone
    pid = S.add_prize(c, sid, title="Judged", awarded_for="judges")
    jp = next(p for p in S.prize_standings(c, sid) if p["id"] == pid)
    assert jp["leader"] is None and jp["settled"] is False and jp["sort"] == 5
    S.update_prize(c, pid, winner_text="Two, by the panel", sponsor="Rod Co", sponsor_url="https://rods.example")
    jp = S.get_prize(c, pid)
    assert jp["winner_text"] == "Two, by the panel" and jp["sponsor"] == "Rod Co" and jp["for_label"] == "Judges' pick"
    assert S.update_prize(c, pid, awarded_for="Best stem")["awarded_for"] == "award:best-stem"
    with pytest.raises(ValueError):
        S.update_prize(c, pid, title="")
    with pytest.raises(ValueError):
        S.update_prize(c, 999999, title="x")

    # closing writes the leaders in, and leaves a hand-named winner alone
    res = S.close_season(c, sid)
    assert {w["title"] for w in res["prizes"]} == {"Most makers from one shop", "Most registered pumpkins",
                                                    "Crowd favourite", "Collector of the event"}
    after = {p["id"]: p for p in S.prize_standings(c, sid)}
    byfor = {p["awarded_for"]: p for p in after.values()}
    assert byfor["studio_most_makers"]["winner"] == "Hot Shop · 2 makers" and byfor["studio_most_makers"]["settled"]
    assert byfor["peoples_choice"]["winner"] == "Two · 1 vote"
    assert byfor["maker_most_registered"]["winner"] == "Maker pz-m3 · 2 registered pieces"
    assert after[pid]["winner"] == "Two, by the panel"
    assert S.close_season(c, sid)["prizes"] == []                              # idempotent
    S.delete_prize(c, pid)
    assert S.get_prize(c, pid) is None
    with pytest.raises(ValueError):
        S.delete_prize(c, pid)
    assert S.summary(c)["prizes"] >= 4
    S.set_status(c, psid, "closed")


# --- over HTTP ------------------------------------------------------------------------------
def test_api_prizes_boards_and_studios(demo_db):
    c = _conn()
    sid = _season(c, theme="pumpkins")
    slug = S.get_season(c, sid)["slug"]
    eid, rid = _entry(c, sid, "pz-api", studio="API Shop", fingerprint=True)
    cl = _client()
    d = cl.get(f"/seasons/{slug}").json()
    assert [p["awarded_for"] for p in d["prizes"]][:2] == ["studio_most_makers", "maker_most_registered"]
    assert d["prizes"][0]["leader"]["name"] == "API Shop" and d["prizes"][0]["who"] == "studio"
    assert d["leaderboard"]["makers"][0]["artist_name"] == "Maker pz-api"
    assert "artist_uid" not in d["leaderboard"]["makers"][0]
    assert d["leaderboard"]["studios"][0]["makers"] == 1 and d["leaderboard"]["studios"][0]["registered"] == 1
    r = cl.get(f"/seasons/{slug}/prizes")
    assert r.status_code == 200
    assert r.json()["prizes"][1]["leader"]["value"] == 1 and "judges" in r.json()["kinds"]
    assert r.json()["season"]["slug"] == slug
    assert cl.get("/seasons/no-such-season/prizes").status_code == 404
    r = cl.get("/seasons/studios")
    assert r.status_code == 200
    assert {"API Shop", "Blue Ridge Hot Glass"} <= {s["name"] for s in r.json()["studios"]}
    assert cl.get(f"/seasons/{slug}/leaderboard").json()["makers"][0]["registered"] == 1
    S.set_status(c, sid, "closed")


def test_admin_edits_prizes_and_studios(admin):
    c = connect()
    for method, path in (("POST", "/admin/events/1/prizes"), ("POST", "/admin/events/1/prizes/1"),
                         ("DELETE", "/admin/events/1/prizes/1"), ("POST", "/admin/events/entries/1")):
        assert admin.request(method, path, json={}).status_code == 401, path
        assert admin.request(method, path, json={}, headers=_auth()).status_code == 403, path
    ov = admin.get("/admin/events", headers=_boss()).json()
    assert {k["key"] for k in ov["prize_kinds"]} >= {"studio_most_makers", "maker_most_registered", "judges"}
    assert "Most registered pumpkins | Maker with the most registered pieces" in ov["presets"][0]["prizes_text"]
    assert {s["name"] for s in ov["studios"]} >= {"Blue Ridge Hot Glass"}
    demo = next(e for e in ov["events"] if e["slug"] == "pumpkin-patch-2026")
    assert [p["awarded_for"] for p in demo["prizes"]][:2] == ["studio_most_makers", "maker_most_registered"]

    # create with a prizes textarea; a bad kind is a 400 that leaves nothing behind
    n = c.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
    r = admin.post("/admin/events", json={"title": "Prize Test Gourds", "theme": "pumpkins", "prizes": "Odd | the vibes"},
                   headers=_boss())
    assert r.status_code == 400 and c.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == n
    r = admin.post("/admin/events", json={
        "title": "Prize Test Gourds", "theme": "pumpkins", "open": True,
        "prizes": "Shop of the year | Studio with the most makers | A feature | Rod Co | https://rods.example"},
        headers=_boss())
    assert r.status_code == 200, r.text
    ev = r.json()["event"]
    sid = ev["id"]
    try:
        assert [p["title"] for p in ev["prizes"]] == ["Shop of the year"] and ev["prizes"][0]["sponsor"] == "Rod Co"
        assert ev["prizes_text"] == "Shop of the year | Studio with the most makers | A feature | Rod Co | https://rods.example"
        # add one by hand, add the defaults, edit, name a winner, remove
        r = admin.post(f"/admin/events/{sid}/prizes", json={"title": "Best stem, sponsored", "awarded_for": "Best stem"},
                       headers=_boss())
        assert r.status_code == 200 and r.json()["event"]["prizes"][1]["awarded_for"] == "award:best-stem"
        pid = r.json()["ids"][0]
        assert admin.post(f"/admin/events/{sid}/prizes", json={"title": "x", "awarded_for": "nope"},
                          headers=_boss()).status_code == 400
        r = admin.post(f"/admin/events/{sid}/prizes", json={"defaults": True}, headers=_boss())
        assert r.status_code == 200 and len(r.json()["event"]["prizes"]) == 6
        r = admin.post(f"/admin/events/{sid}/prizes/{pid}", json={"winner_text": "Rae, by the panel", "detail": "A rod"},
                       headers=_boss())
        assert r.status_code == 200
        p = next(p for p in r.json()["event"]["prizes"] if p["id"] == pid)
        assert p["winner"] == "Rae, by the panel" and p["detail"] == "A rod" and p["settled"]
        assert admin.post(f"/admin/events/{sid}/prizes/{pid}", json={"sponsor_url": "rods"}, headers=_boss()).status_code == 400
        assert admin.post(f"/admin/events/{sid}/prizes/999999", json={"title": "x"}, headers=_boss()).status_code == 404
        assert admin.post("/admin/events/999999/prizes", json={"title": "x"}, headers=_boss()).status_code == 404
        r = admin.request("DELETE", f"/admin/events/{sid}/prizes/{pid}", headers=_boss())
        assert r.status_code == 200 and len(r.json()["event"]["prizes"]) == 5
        assert admin.request("DELETE", f"/admin/events/{sid}/prizes/{pid}", headers=_boss()).status_code == 404
        # the admin key works too
        assert admin.get("/admin/events", headers={"X-API-Key": "test-admin-key"}).status_code == 200
        # an entry's studio can be set right
        rid, _ = _object(c, uid="pz-adm", title="Admin's pumpkin")
        eid = S.enter(c, sid, object_row_id=rid, artist_uid="pz-adm", artist_name="Adm", studio="blue ridge hot glass")
        S.approve_entry(c, eid, True)
        assert admin.post(f"/admin/events/entries/{eid}", json={}, headers=_boss()).status_code == 400
        r = admin.post(f"/admin/events/entries/{eid}", json={"studio": " Blue Ridge  Hot Glass "}, headers=_boss())
        assert r.status_code == 200 and r.json()["entry"]["studio"] == "Blue Ridge Hot Glass"
        assert S.get_entry(c, eid)["studio"] == "Blue Ridge Hot Glass"
        assert admin.post("/admin/events/entries/999999", json={"studio": "x"}, headers=_boss()).status_code == 404
        ev = next(e for e in admin.get("/admin/events", headers=_boss()).json()["events"] if e["id"] == sid)
        assert ev["entries"][0]["studio"] == "Blue Ridge Hot Glass" and ev["prizes"][0]["leader"]["name"] == "Blue Ridge Hot Glass"
    finally:
        for t in ("season_prizes", "season_entries", "season_categories"):
            c.execute(f"DELETE FROM {t} WHERE season_id=?", (sid,))
        c.execute("DELETE FROM seasons WHERE id=?", (sid,))
        c.commit()


def test_the_pages_carry_the_prizes():
    ev = (ROOT / "deploy" / "events-admin.html").read_text(encoding="utf-8")
    assert "/prizes" in ev and "data-prize-defaults" in ev and "data-studio-save" in ev
    assert '"prizes"' in ev and '"winner_text"' in ev and 'name="awarded_for"' in ev and 'id="studioNames"' in ev
    games = (ROOT / "deploy" / "games.html").read_text(encoding="utf-8")
    assert 'id="prizesBody"' in games and 'data-tab="makers"' in games and "/prizes" in games
    assert 'id="prizes"' in games and "Studio" in games
    account = (ROOT / "deploy" / "account.html").read_text(encoding="utf-8")
    assert 'id="goStudio"' in account and "/seasons/studios" in account and "#e-" not in account
    assert "studio: studio" in account
