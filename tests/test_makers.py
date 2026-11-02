"""Maker cards: finding a maker by key, account or name; the traits, bio completeness and
knowledge score; the career across collecting events with the prizes settled to them; the
boards and the site's numbers; the API and the pages that show them."""
from pathlib import Path

from fastapi.testclient import TestClient
from test_seasons import _auth, _conn, _entry, _season

from central import makers, seasons, sessions, stats

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def test_find_card_and_scores(demo_db):
    c = _conn()
    makers.forget()
    m = makers.find(c, name="Rae Sutter")
    assert m and m["gid"].startswith("gdb:artists:") and m["table"] == "artists" and m["row"]["artist_name"] == "Rae Sutter"
    assert makers.find(c, gid=m["gid"])["name"] == "Rae Sutter"
    assert makers.find(c, name="Nobody Here At All") is None
    assert makers.find(c, gid="gdb:artists:nope") is None
    card = makers.card(c, m)
    t, k, comp = card["traits"], card["knowledge"], card["completeness"]
    assert t["pieces"] >= 1 and t["registered"] >= 1 and t["events"] >= 1 and t["best_rank"] == 1
    assert card["studio"] == "Blue Ridge Hot Glass" and card["techniques"] and card["since"] == "1984"
    assert 0 <= comp["percent"] <= 100 and "Name" in comp["filled"]
    assert all(isinstance(x, str) for x in comp["missing"])                          # the public card names, no where-to-fill
    assert k["score"] == min(100, sum(k["parts"].values())) and k["level"] in dict((w, w) for _, w in makers.LEVELS)
    assert set(k["parts"]) == set(k["max"]) and k["parts"]["profile"] == round(0.4 * comp["percent"])
    assert card["links"]["website"].startswith("http") and card["links"]["record"] == f"/record/{m['gid']}"
    assert card["url"].startswith("/maker.html?m=") and card["pieces"][0]["gid"].startswith("gdb:object:")
    # by account: the published pieces and the where-to-fill list
    um = makers.find(c, uid="demo-maker-rae", name="Rae Sutter", email="rae@example.com")
    pc = makers.card(c, um, private=True)
    assert pc["has_account"] and pc["name"] == "Rae Sutter" and pc["traits"]["pieces"] >= 1
    assert pc["next_steps"] and all(s["where"]["url"] for s in pc["next_steps"])
    assert all(isinstance(x, dict) and x["where"]["label"] for x in pc["completeness"]["missing"])
    # the formula on its own
    low = makers.score_parts(completeness_pct=0, pieces_n=0, registered=0, events=0, votes=0, collected=0,
                             awards=0, prizes=0, badges=0, external=0)
    high = makers.score_parts(completeness_pct=100, pieces_n=40, registered=40, events=3, votes=60, collected=5,
                              awards=2, prizes=2, badges=4, external=10)
    assert low["score"] == 0 and low["level"] == "barely known" and high["score"] == 100 and high["level"] == "on the record"
    mid = makers.score_parts(completeness_pct=50, pieces_n=1, registered=1, events=1, votes=3, collected=1,
                             awards=0, prizes=0, badges=0, external=2)
    assert mid["parts"] == {"profile": 20, "pieces": 4, "registered": 10, "events": 4, "recognition": 3, "external": 2}


def test_career_follows_a_maker_across_events(demo_db):
    c = _conn()
    uid = "career-maker"
    s1 = _season(c, theme="pumpkins")
    e1, r1 = _entry(c, s1, uid, studio="Career Shop", title="Season one, a", fingerprint=True)
    e2, r2 = _entry(c, s1, uid, studio="Career Shop", title="Season one, b", fingerprint=True)
    _entry(c, s1, "career-other", studio="Other Shop", title="Rival")
    seasons.vote(c, s1, e1, "cv1"); seasons.vote(c, s1, e1, "cv2"); seasons.vote(c, s1, e2, "cv3")
    seasons.claim(c, object_row_id=r1, collector_uid="cc1", collector_name="Col", confidence=92)
    stem = seasons.get_category(c, s1, "best-stem")
    seasons.award_vote(c, s1, stem["id"], e1, "cv1")                                  # an award led at the close
    res = seasons.close_season(c, s1)
    refs = {p["title"]: p["ref"] for p in res["prizes"]}
    assert refs["Most registered pumpkins"]["artist_uid"] == uid and refs["Most registered pumpkins"]["who"] == "maker"
    assert refs["Crowd favourite"]["entry_id"] == e1 and refs["Crowd favourite"]["artist_uid"] == uid
    assert refs["Most makers from one shop"]["studio"] in ("Career Shop", "Other Shop")
    won = seasons.prizes_won(c, uid=uid)
    assert {p["title"] for p in won} >= {"Most registered pumpkins", "Crowd favourite"}
    assert seasons.prizes_won(c, name="Maker career-maker")                      # by name too, for makers without an account
    assert seasons.prizes_won(c, studio="career shop") or seasons.prizes_won(c, studio="Other Shop")
    # a second event: the career has two lines, newest first
    s2 = _season(c, theme="ornaments", starts="2032-12-01", entries_close="2032-12-20", voting_closes="2032-12-28", ends="2032-12-31")
    try:
        e3, _ = _entry(c, s2, uid, studio="Career Shop", title="Season two")
        seasons.vote(c, s2, e3, "cv9")
        m = makers.find(c, uid=uid, name="Maker career-maker")
        card = makers.card(c, m, private=True)
        car = card["career"]
        assert [s["title"] for s in car["seasons"]] == [seasons.get_season(c, s2)["title"], seasons.get_season(c, s1)["title"]]
        assert car["totals"]["events"] == 2 and car["totals"]["entries"] == 3 and car["totals"]["votes"] == 4
        assert car["totals"]["collected"] == 1 and car["totals"]["prizes"] >= 2 and car["totals"]["best_rank"] == 1
        first = car["seasons"][1]
        assert first["entries"] == 2 and first["best_rank"] == 1 and "Crowd favourite" in first["prizes"]
        assert first["awards"] == ["Best stem"] and "crowd-favourite" in {b["badge"] for b in car["badges"]}
        assert card["traits"]["prizes"] >= 2 and card["traits"]["awards"] == 1 and card["traits"]["crowd_favourites"] == 1
        assert card["studio"] == "Career Shop"
    finally:
        seasons.set_status(c, s2, "closed")                                           # leave no open season behind


def test_boards_and_the_site_numbers(demo_db):
    c = _conn()
    makers.forget(); stats.forget()
    b = makers.leaderboards(c, limit=5, force=True)
    assert b["counts"]["makers"] >= 5 and b["counts"]["with_pieces"] >= 3 and 0 <= b["counts"]["average_knowledge"] <= 100
    ks = [m["knowledge"] for m in b["knowledge"]]
    assert ks == sorted(ks, reverse=True) and b["knowledge"][0]["url"].startswith("/maker.html")
    assert b["pieces"][0]["pieces"] >= 1 and all(m["completeness"] <= 100 for m in b["completeness"])
    again = makers.leaderboards(c, limit=2)
    assert again["computed_at"] == b["computed_at"] and len(again["knowledge"]) == 2      # cached, sliced per call
    assert b["knowledge"][0]["studio"] == "Blue Ridge Hot Glass"                    # the shop they entered from, not the town
    st = stats.site_stats(c, live=False, force=True)
    assert st["pieces"]["total"] >= 3 and st["pieces"]["registered"] >= 1 and st["people"]["studios"] >= 5
    assert st["events"]["seasons"] >= 1 and "prizes_settled" in st["events"] and "makers_entering" in st["events"]
    assert "items_staged" in st["wikibase"] and "by_kind" in st["wikibase"] and st["wikibase"]["live"] is None
    assert st["makers"]["makers"] == b["counts"]["makers"] and st["directory"]["rows"] >= 10
    assert stats.wiki_live("") is None


def test_api_cards_boards_stats_and_my_card(demo_db):
    _conn()
    from api.main import app
    client = TestClient(app)
    r = client.get("/makers", params={"q": "sutter"})
    assert r.status_code == 200 and r.json()["makers"][0]["name"] == "Rae Sutter"
    gid = r.json()["makers"][0]["gid"]
    r = client.get(f"/makers/{gid}")
    assert r.status_code == 200 and r.json()["name"] == "Rae Sutter" and "next_steps" not in r.json()
    body = r.text
    assert "demo-maker-rae" not in body and "artist_uid" not in body and "collector_uid" not in body
    assert client.get("/makers/name:Rae%20Sutter").json()["gid"] == gid
    assert client.get("/makers/name:Nobody%20Here").status_code == 404
    assert client.get("/makers/gdb:artists:nope").status_code == 404
    r = client.get("/stats", params={"live": "false"})
    assert r.status_code == 200 and r.json()["pieces"]["total"] >= 3 and "wikidata" in r.json()
    r = client.get("/stats/makers", params={"limit": 3})
    assert r.status_code == 200 and len(r.json()["knowledge"]) <= 3
    assert client.get("/me/card").status_code == 401
    r = client.get("/me/card", headers=_auth("demo-maker-rae", "rae@example.com", "Rae Sutter"))
    assert r.status_code == 200 and r.json()["has_account"] and r.json()["next_steps"] and r.json()["name"] == "Rae Sutter"
    r = client.get("/me/card", headers={"Authorization": f"Bearer {sessions.issue('brand-new', 'new@example.com', 'New Maker')}"})
    assert r.status_code == 200 and r.json()["traits"]["pieces"] == 0 and r.json()["knowledge"]["score"] < 20
    assert r.json()["next_steps"][0]["text"].startswith("Publish a piece")


def test_the_pages_show_the_cards():
    maker = (DEPLOY / "maker.html").read_text(encoding="utf-8")
    assert 'id="cardSlot"' in maker and "GDB.makerCard(" in maker and '"/makers/"' in maker and 'id="careerBody"' in maker
    st = (DEPLOY / "stats.html").read_text(encoding="utf-8")
    assert '"/stats"' in st and '"/stats/makers"' in st and 'id="wikiTiles"' in st and 'id="bKnowledge"' in st
    account = (DEPLOY / "account.html").read_text(encoding="utf-8")
    assert 'id="cardSlot"' in account and '"/me/card"' in account and "GDB.makerCard(" in account and "Your maker card" in account
    js = (DEPLOY / "site.js").read_text(encoding="utf-8")
    assert "function makerCard(" in js and "makerCard: makerCard" in js and "pc-bar" in js
    css = (DEPLOY / "paper.css").read_text(encoding="utf-8")
    assert ".pc-bar" in css and ".pc-art.pc-mono" in css
    for page in ("record.html", "maker.html", "stats.html"):
        s = (DEPLOY / page).read_text(encoding="utf-8")
        assert '<ul id="menu" data-nav data-active=' in s and "— Glass Database</title>" in s and "GDB.chip" in s, page
