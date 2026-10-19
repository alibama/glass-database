"""Seasons: schema + seasons, who may enter what, votes, claims + badges, the maker's
side of a claim (owner-only confirm / reject, the inbox), Discord nudges, boards,
close-out, and the HTTP surface (anonymous reads, signed-in writes, share card)."""
import io
import itertools

import pytest
from fastapi.testclient import TestClient

from central import approvals
from central import seasons as S
from central.dbconn import connect

_N = itertools.count(1)


def _conn():
    from glowtbook.contribute import ensure_central_objects, ensure_object_staging
    c = connect()
    S.ensure(c); ensure_central_objects(c); ensure_object_staging(c)
    return c


def _object(c, uid=None, title="Test Piece", maker="Tess Maker", approve=True, image=False):
    """A public object. With `uid`, an object_submissions row says who published it."""
    n = next(_N)
    rid, chash = f"obj-t-{n:04d}", f"hash-t-{n:04d}"
    c.execute("INSERT OR REPLACE INTO objects (_row_id,_source_file,_source_sheet,_imported_at,"
              "title,maker,year,techniques,content_hash,manifest_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (rid, "test", "test", "2026", title, maker, "2026", "Blown", chash, "{}"))
    if uid:
        c.execute("INSERT OR REPLACE INTO object_submissions (content_hash, submitted_uid, "
                  "submitted_by, title, status) VALUES (?,?,?,?,?)",
                  (chash, uid, maker, title, "approved"))
    if image:
        import base64

        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (320, 240), (232, 164, 74)).save(buf, "PNG")
        c.execute("INSERT INTO object_images (object_row_id, role, caption, image_b64) "
                  "VALUES (?,?,?,?)", (rid, "primary", title, base64.b64encode(buf.getvalue()).decode()))
    c.commit()
    if approve:
        approvals.set_status(c, "objects", [rid], "approved")
    return rid, chash


def _season(c, status="open", **kw):
    n = next(_N)
    base = dict(title=f"Test Season {n}", theme="Things", starts="2031-01-01",
                entries_close="2031-01-20", voting_closes="2031-01-28", ends="2031-01-31",
                votes_per_voter=3)
    base.update(kw)
    return S.create_season(c, status=status, **base)


def _entry(c, sid, uid, studio="Studio A", approve=True, **kw):
    rid, _ = _object(c, uid=uid, **kw)
    eid = S.enter(c, sid, object_row_id=rid, artist_uid=uid, artist_name=f"Maker {uid}",
                  studio=studio, edition_size=5, price_note="$50")
    if approve:
        S.approve_entry(c, eid, True)
    return eid, rid


# --- seasons ----------------------------------------------------------------------
def test_ensure_create_list_current(demo_db):
    c = _conn()
    for t in ("seasons", "season_entries", "season_votes", "season_claims", "season_badges"):
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
    draft = S.create_season(c, title="Draft Only!", starts="2040-01-01")
    sid = S.create_season(c, title="Future Pumpkins 2040", theme="t", starts="2040-10-01",
                          status="open", votes_per_voter=2)
    s = S.get_season(c, sid)
    assert s["slug"] == "future-pumpkins-2040" and s["status"] == "open" and s["votes_per_voter"] == 2
    assert S.get_season(c, "future-pumpkins-2040")["id"] == sid
    assert S.get_season(c, "draft-only")["status"] == "draft"
    # a second season with the same title gets a distinct slug
    sid2 = S.create_season(c, title="Future Pumpkins 2040")
    assert S.get_season(c, sid2)["slug"] == "future-pumpkins-2040-2"
    slugs = {x["slug"] for x in S.list_seasons(c)}
    assert "future-pumpkins-2040" in slugs and "draft-only" not in slugs
    assert "draft-only" in {x["slug"] for x in S.list_seasons(c, include_draft=True)}
    assert S.current_season(c)["id"] == sid            # open beats voting; latest start wins
    S.set_status(c, sid, "voting")
    assert S.current_season(c)["slug"] == "pumpkin-patch-2026"   # the seeded open season
    with pytest.raises(ValueError):
        S.set_status(c, sid, "bogus")
    for x in (draft, sid, sid2):
        S.set_status(c, x, "closed")


def test_season_object_rows_via_submission_and_store(demo_db, monkeypatch, tmp_path):
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    c = _conn()
    uid = "owner-rows"
    a, _ = _object(c, uid=uid, title="Via submission")
    b, bhash = _object(c, title="Via glowtbook")              # no submission row
    unapproved, _ = _object(c, uid=uid, title="Not public", approve=False)
    from glowtbook import store as gstore
    st = gstore.open_store(tmp_path / "gb.db")
    st.execute("INSERT INTO contribution (user_id, object_id, content_hash) VALUES (?,?,?)",
               (uid, 1, bhash))
    st.commit(); st.close()
    rows = S.season_object_rows(c, uid)
    ids = {r["row_id"] for r in rows}
    assert a in ids and b in ids and unapproved not in ids
    r = next(r for r in rows if r["row_id"] == a)
    assert r["image_url"] == f"/api/objects/{a}/image" and r["title"] == "Via submission"
    assert S.season_object_rows(c, "nobody") == []


# --- entries ----------------------------------------------------------------------
def test_enter_rules(demo_db):
    c = _conn()
    sid = _season(c)
    rid, _ = _object(c, uid="own2")
    with pytest.raises(ValueError):                           # not the publisher
        S.enter(c, sid, object_row_id=rid, artist_uid="stranger", artist_name="X")
    eid = S.enter(c, sid, object_row_id=rid, artist_uid="own2", artist_name="Own Two",
                  studio="Studio Z", edition_size=3)
    with pytest.raises(ValueError):                           # duplicate
        S.enter(c, sid, object_row_id=rid, artist_uid="own2", artist_name="Own Two")
    hidden, _ = _object(c, uid="own2", approve=False)
    with pytest.raises(ValueError):                           # not public
        S.enter(c, sid, object_row_id=hidden, artist_uid="own2", artist_name="Own Two")
    assert S.entries(c, sid) == [] and S.get_entry(c, eid)["status"] == "pending"
    assert [e["id"] for e in S.my_entries(c, "own2")] == [eid]
    S.approve_entry(c, eid, True)
    es = S.entries(c, sid)
    assert len(es) == 1 and es[0]["vote_count"] == 0 and es[0]["claim_count"] == 0
    assert es[0]["image_url"] == f"/api/objects/{rid}/image" and es[0]["title"] == "Test Piece"
    assert "maker-debut" in {b["badge"] for b in S.badges_for(c, "own2")}
    closed = _season(c, status="closed")
    other, _ = _object(c, uid="own2")
    with pytest.raises(ValueError):                           # season not open
        S.enter(c, closed, object_row_id=other, artist_uid="own2", artist_name="Own Two")


# --- votes ------------------------------------------------------------------------
def test_vote_toggle_cap_and_closed(demo_db):
    c = _conn()
    sid = _season(c, votes_per_voter=2)
    e1, _ = _entry(c, sid, "mk1"); e2, _ = _entry(c, sid, "mk2"); e3, _ = _entry(c, sid, "mk3")
    pend, _ = _entry(c, sid, "mk4", approve=False)
    r = S.vote(c, sid, e1, "v1")
    assert r == {"voted": True, "votes": 1, "remaining": 1}
    assert S.vote(c, sid, e2, "v1")["remaining"] == 0
    with pytest.raises(ValueError):                           # cap reached
        S.vote(c, sid, e3, "v1")
    with pytest.raises(ValueError):                           # pending entry
        S.vote(c, sid, pend, "v2")
    r = S.vote(c, sid, e1, "v1")                              # toggle off
    assert r == {"voted": False, "votes": 0, "remaining": 1}
    assert S.my_votes(c, sid, "v1") == [e2]
    S.vote(c, sid, e1, "v2")
    assert S.vote(c, sid, e1, "v3")["votes"] == 2
    S.set_status(c, sid, "voting")
    assert S.vote(c, sid, e3, "v1")["voted"] is True          # still allowed in voting
    S.set_status(c, sid, "closed")
    with pytest.raises(ValueError):
        S.vote(c, sid, e3, "v4")


# --- claims + badges ----------------------------------------------------------------
def test_claim_verified_pending_and_badges(demo_db):
    c = _conn()
    sid = _season(c)
    eid, rid = _entry(c, sid, "mk-claim", studio="Claim Studio")
    out = S.claim(c, object_row_id=rid, collector_uid="col1", collector_name="Col One",
                  confidence=91, report={"verdict": "Match likely"}, location="Richmond")
    assert out["status"] == "verified" and out["verified"] is True
    assert out["season_id"] == sid and out["entry_id"] == eid
    assert out["badges"] == ["first-scan"] and out["studio"] == "Claim Studio"
    with pytest.raises(ValueError):                           # one claim per piece per person
        S.claim(c, object_row_id=rid, collector_uid="col1", collector_name="Col One", confidence=95)
    low = S.claim(c, object_row_id=rid, collector_uid="col2", collector_name="Col Two", confidence=50)
    assert low["status"] == "pending" and low["badges"] == []
    assert S.badges_for(c, "col2") == []
    assert S.entries(c, sid)[0]["claim_count"] == 1          # pending doesn't count
    S.set_claim_status(c, low["id"], "approved")
    assert {b["badge"] for b in S.badges_for(c, "col2")} == {"first-scan"}
    assert S.entries(c, sid)[0]["claim_count"] == 2
    coll = S.collection(c, "col1")
    assert len(coll) == 1 and coll[0]["image_url"] == f"/api/objects/{rid}/image"
    assert coll[0]["season_title"] == S.get_season(c, sid)["title"]
    hidden, _ = _object(c, uid="mk-claim", approve=False)
    with pytest.raises(ValueError):
        S.claim(c, object_row_id=hidden, collector_uid="col1", collector_name="Col One", confidence=99)
    with pytest.raises(ValueError):
        S.claim(c, object_row_id=rid, collector_uid="", collector_name="", confidence=99)


def test_low_scan_then_better_scan_upgrades(demo_db):
    c = _conn()
    sid = _season(c)
    _, rid = _entry(c, sid, "mk-up")
    first = S.claim(c, object_row_id=rid, collector_uid="col-up", collector_name="Up", confidence=40)
    assert first["status"] == "pending"
    second = S.claim(c, object_row_id=rid, collector_uid="col-up", collector_name="Up", confidence=88)
    assert second["id"] == first["id"] and second["status"] == "verified"
    assert "first-scan" in second["badges"]


# --- the maker's side: owner-only confirmation + the inbox ---------------------------
def test_maker_confirms_or_rejects_pending_claims(demo_db):
    c = _conn()
    sid = _season(c)
    _, rid = _entry(c, sid, "mk-conf", studio="Confirm Studio")
    low = S.claim(c, object_row_id=rid, collector_uid="col-a", collector_name="Col A", confidence=42)
    other = S.claim(c, object_row_id=rid, collector_uid="col-b", collector_name="Col B", confidence=30)
    sure = S.claim(c, object_row_id=rid, collector_uid="col-c", collector_name="Col C", confidence=90)
    on = S.claims_on(c, rid)
    assert len(on) == 3 and {x["id"] for x in on[:2]} == {low["id"], other["id"]}   # pending first
    assert on[-1]["id"] == sure["id"] and on[0]["title"] == "Test Piece"
    assert S.claims_on(c, "no-such-row") == []

    assert S.owns_object(c, "mk-conf", rid) and not S.owns_object(c, "stranger", rid)
    assert not S.owns_object(c, "", rid) and not S.owns_object(c, "mk-conf", "no-such-row")
    with pytest.raises(ValueError):                           # not the maker
        S.confirm_claim(c, low["id"], "stranger")
    with pytest.raises(ValueError):                           # a collector can't self-approve
        S.confirm_claim(c, low["id"], "col-a")
    assert S.get_claim(c, low["id"])["status"] == "pending"
    assert S.badges_for(c, "col-a") == []

    out = S.confirm_claim(c, low["id"], "mk-conf")
    assert out["status"] == "approved" and out["verified"] is True
    assert {b["badge"] for b in S.badges_for(c, "col-a")} == {"first-scan"}
    assert S.entries(c, sid)[0]["claim_count"] == 2          # the verified one + the confirmed one
    with pytest.raises(ValueError):                           # already decided
        S.confirm_claim(c, low["id"], "mk-conf")
    with pytest.raises(ValueError):                           # a scan-verified claim isn't pending
        S.confirm_claim(c, sure["id"], "mk-conf", False)
    rej = S.confirm_claim(c, other["id"], "mk-conf", False)
    assert rej["status"] == "rejected" and S.badges_for(c, "col-b") == []
    assert S.collection(c, "col-b") == [] and S.entries(c, sid)[0]["claim_count"] == 2
    with pytest.raises(ValueError):
        S.confirm_claim(c, 999999, "mk-conf")

    counts = S.claim_counts(c, [rid, "nope", None, rid])
    assert counts[rid] == {"ok": 2, "pending": 0} and counts["nope"] == {"ok": 0, "pending": 0}
    assert None not in counts and S.claim_counts(c, []) == {}
    inbox = S.claims_on_my_pieces(c, "mk-conf")
    assert {x["id"] for x in inbox} == {low["id"], other["id"], sure["id"]}
    assert S.claims_on_my_pieces(c, "stranger") == [] and S.claims_on_my_pieces(c, "") == []


def test_maker_known_only_to_glowtbook_store_can_confirm(demo_db, monkeypatch, tmp_path):
    """Ownership resolves through the Glowtbook contribution table too (no
    object_submissions row), like season_object_rows does."""
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    c = _conn()
    rid, chash = _object(c, title="Store-owned")               # no submission row
    from glowtbook import store as gstore
    st = gstore.open_store(tmp_path / "gb.db")
    st.execute("INSERT INTO contribution (user_id, object_id, content_hash) VALUES (?,?,?)",
               ("gb-maker", 7, chash))
    st.commit(); st.close()
    cl = S.claim(c, object_row_id=rid, collector_uid="col-gb", collector_name="GB", confidence=55)
    assert cl["status"] == "pending"
    assert [x["id"] for x in S.claims_on_my_pieces(c, "gb-maker")] == [cl["id"]]
    assert S.claim_counts(c, [rid])[rid] == {"ok": 0, "pending": 1}
    with pytest.raises(ValueError):
        S.confirm_claim(c, cl["id"], "gb-other")
    assert S.confirm_claim(c, cl["id"], "gb-maker")["status"] == "approved"
    assert S.claim_counts(c, [rid])[rid] == {"ok": 1, "pending": 0}


# --- Discord nudges: posted on claims and season openings, never fatal -----------------
def test_nudges_are_best_effort(demo_db, monkeypatch):
    from central import notify
    sent = []
    monkeypatch.setattr(notify, "notify_message",
                        lambda title, fields, color=0: sent.append((title, dict(fields))) or True)
    c = _conn()
    sid = _season(c, status="draft", theme="Nudge things", prize_note="A feature")
    S.set_status(c, sid, "open")
    S.set_status(c, sid, "open")                              # no change → no second post
    S.set_status(c, sid, "voting")
    S.set_status(c, sid, "closed")                            # closing is quiet
    assert [("Season open" in t, "Voting" in t) for t, _ in sent] == [(True, False), (False, True)]
    opened = sent[0][1]
    assert opened["Theme"] == "Nudge things" and opened["Prize"] == "A feature"
    assert opened["Enter a piece"].startswith("Glowtbook") and "games.html?season=" in opened["Page"]
    assert "games.html?season=" in sent[1][1]["Vote"] and sent[1][1]["Votes per person"] == 3

    S.set_status(c, sid, "open")
    _, rid = _entry(c, sid, "mk-nudge", studio="Nudge Studio")
    sent.clear()
    S.claim(c, object_row_id=rid, collector_uid="col-n1", collector_name="Nudge One", confidence=88)
    assert len(sent) == 1 and "verified by scan" in sent[0][0] and "Nudge One" in sent[0][0]
    assert sent[0][1]["Studio"] == "Nudge Studio" and sent[0][1].get("Confirm") is None
    S.claim(c, object_row_id=rid, collector_uid="col-n2", collector_name="Nudge Two", confidence=20)
    assert len(sent) == 2 and "needs a look" in sent[1][0]
    assert "Glowtbook" in sent[1][1]["Confirm"] and sent[1][1]["Confidence"] == "20/100"

    def boom(*_a, **_k):                                      # Discord is down
        raise RuntimeError("webhook exploded")
    monkeypatch.setattr(notify, "notify_message", boom)
    out = S.claim(c, object_row_id=rid, collector_uid="col-n3", collector_name="Nudge Three",
                  confidence=95)
    assert out["status"] == "verified"
    S.set_status(c, sid, "voting")
    assert S.get_season(c, sid)["status"] == "voting"
    S.set_status(c, sid, "closed")


# --- boards + close ------------------------------------------------------------------
def test_leaderboard_close_and_collectors(demo_db):
    c = _conn()
    sid = _season(c)
    e1, r1 = _entry(c, sid, "artist-a", studio="Shop A", title="Piece A")
    e2, r2 = _entry(c, sid, "artist-b", studio="Shop B", title="Piece B")
    e3, r3 = _entry(c, sid, "artist-c", studio="Shop A", title="Piece C")
    S.vote(c, sid, e2, "lv1"); S.vote(c, sid, e2, "lv2"); S.vote(c, sid, e1, "lv1")
    S.claim(c, object_row_id=r1, collector_uid="lc1", collector_name="Lead Collector", confidence=90)
    S.claim(c, object_row_id=r2, collector_uid="lc1", collector_name="Lead Collector", confidence=85)
    S.claim(c, object_row_id=r1, collector_uid="lc2", collector_name="Other", confidence=75)
    lb = S.leaderboard(c, sid)
    assert [e["id"] for e in lb["peoples_choice"]] == [e2, e1, e3]
    assert [e["rank"] for e in lb["peoples_choice"]] == [1, 2, 3]
    assert [e["id"] for e in lb["most_collected"]][:2] == [e1, e2]
    assert lb["most_collected"][0]["claim_count"] == 2
    studios = {s["studio"]: s for s in lb["studios"]}
    assert studios["Shop A"]["entries"] == 2 and studios["Shop A"]["claims"] == 2
    assert studios["Shop B"]["votes"] == 2 and studios["Shop B"]["claims"] == 1
    cols = {x["collector_name"]: x for x in S.collectors(c)}
    assert cols["Lead Collector"]["pieces"] == 2 and cols["Lead Collector"]["studios"] == 2
    assert cols["Lead Collector"]["rank"] < cols["Other"]["rank"]
    res = S.close_season(c, sid)
    assert S.get_season(c, sid)["status"] == "closed"
    assert res["crowd_favourite"]["entry_id"] == e2
    assert "crowd-favourite" in {b["badge"] for b in S.badges_for(c, "artist-b")}
    assert "crowd-favourite" not in {b["badge"] for b in S.badges_for(c, "artist-a")}
    lc1 = {b["badge"]: b for b in S.badges_for(c, "lc1")}
    assert "set-complete" in lc1 and lc1["set-complete"]["season_id"] == sid
    assert "set-complete" not in {b["badge"] for b in S.badges_for(c, "lc2")}
    assert S.close_season(c, sid)["badges"] == {}            # idempotent: nothing new
    sm = S.summary(c)
    assert sm["seasons"] >= 1 and sm["claims"] >= 3 and "pending_entries" in sm


# --- API ----------------------------------------------------------------------------------
def _client():
    from api.main import app
    return TestClient(app)


def _auth(uid="u1", email="u1@example.com", name="U One"):
    from central import sessions
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def test_api_reads_votes_claims_collection_and_card(demo_db):
    c = _conn()
    sid = _season(c)
    slug = S.get_season(c, sid)["slug"]
    eid, rid = _entry(c, sid, "api-maker", studio="API Shop", image=True)
    cl = _client()

    r = cl.get("/seasons")
    assert r.status_code == 200
    body = r.json()
    assert "current" in body and slug in {s["slug"] for s in body["seasons"]}
    assert "first-scan" in body["badges"]

    r = cl.get(f"/seasons/{slug}")
    assert r.status_code == 200
    d = r.json()
    assert d["entries"][0]["id"] == eid and d["entries"][0]["mine"] is False
    assert "artist_uid" not in d["entries"][0] and d["remaining_votes"] == 3
    assert d["leaderboard"]["peoples_choice"][0]["id"] == eid and "collectors" in d["leaderboard"]
    assert cl.get("/seasons/no-such-season").status_code == 404

    assert cl.post(f"/seasons/{slug}/vote", json={"entry_id": eid}).status_code == 401
    r = cl.post(f"/seasons/{slug}/vote", json={"entry_id": eid}, headers=_auth())
    assert r.status_code == 200 and r.json() == {"voted": True, "votes": 1, "remaining": 2}
    assert cl.get(f"/seasons/{slug}", headers=_auth()).json()["my_votes"] == [eid]
    assert cl.post(f"/seasons/{slug}/vote", json={"entry_id": 999999}, headers=_auth()).status_code == 400
    r = cl.get(f"/seasons/{slug}/leaderboard")
    assert r.status_code == 200 and r.json()["peoples_choice"][0]["vote_count"] == 1
    r = cl.get(f"/seasons/{slug}/entries/{eid}", headers=_auth())
    assert r.status_code == 200 and r.json()["voted"] is True

    assert cl.post("/claims", json={"object_row_id": rid, "confidence": 88}).status_code == 401
    r = cl.post("/claims", json={"object_row_id": rid, "confidence": 88,
                                 "report": {"verdict": "Match likely"}, "location": "Norfolk"},
                headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert "first-scan" in r.json()["badges"] and "collector_uid" not in r.json()
    assert cl.post("/claims", json={"object_row_id": "nope", "confidence": 99},
                   headers=_auth()).status_code == 400

    assert cl.get("/me/collection").status_code == 401
    r = cl.get("/me/collection", headers=_auth())
    assert r.status_code == 200
    me = r.json()
    assert me["collection"][0]["object_row_id"] == rid and "first-scan" in me["all_badges"]
    assert {b["badge"] for b in me["badges"]} == {"first-scan"}

    # a maker enters one of their own published pieces over HTTP
    own, _ = _object(c, uid="u1", title="U One's Piece")
    r = cl.post(f"/seasons/{slug}/entries", json={"object_row_id": own, "studio": "U Shop",
                                                  "edition_size": 4, "price_note": "$40"},
                headers=_auth())
    assert r.status_code == 200 and r.json()["pending"] is True
    assert r.json()["entry"]["status"] == "pending" and r.json()["entry"]["mine"] is True
    assert cl.post(f"/seasons/{slug}/entries", json={"object_row_id": rid}, headers=_auth()).status_code == 400

    r = cl.get(f"/seasons/entries/{eid}/card.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:4] == b"\x89PNG" and "max-age=3600" in r.headers["cache-control"]
    from PIL import Image
    assert Image.open(io.BytesIO(r.content)).size == (1080, 1350)
    pending_id = cl.get("/me/collection", headers=_auth()).json()["entries"][0]["id"]
    assert cl.get(f"/seasons/entries/{pending_id}/card.png").status_code == 404   # not approved


def test_api_me_claims_is_the_makers_inbox(demo_db):
    c = _conn()
    sid = _season(c)
    _, rid = _entry(c, sid, "inbox-maker", studio="Inbox Shop")
    cl = _client()
    maker = _auth("inbox-maker", "maker@example.com", "Inbox Maker")
    collector = _auth("inbox-col", "col@example.com", "Inbox Collector")

    assert cl.get("/me/claims").status_code == 401
    r = cl.get("/me/claims", headers=maker)
    assert r.status_code == 200 and r.json()["claims"] == [] and r.json()["pending"] == 0

    r = cl.post("/claims", json={"object_row_id": rid, "confidence": 35, "location": "Richmond"},
                headers=collector)
    assert r.status_code == 200 and r.json()["status"] == "pending" and "maker" in r.json()["note"]
    r = cl.post("/claims", json={"object_row_id": rid, "confidence": 92},
                headers=_auth("inbox-col2", "c2@example.com", "Sure Collector"))
    assert r.json()["status"] == "verified" and "note" not in r.json()

    d = cl.get("/me/claims", headers=maker).json()
    assert d["pending"] == 1 and len(d["claims"]) == 2 and "Glowtbook" in d["confirm_in"]
    first = d["claims"][0]                                    # pending first
    assert first["status"] == "pending" and first["collector_name"] == "Inbox Collector"
    assert first["confidence"] == 35 and first["object_row_id"] == rid and first["location"] == "Richmond"
    assert first["title"] == "Test Piece" and first["studio"] == "Inbox Shop" and first["verified"] is False
    assert first["image_url"] == f"/api/objects/{rid}/image"
    assert all("collector_uid" not in x for x in d["claims"])
    assert cl.get("/me/claims", headers=_auth("someone-else")).json()["claims"] == []

    # the maker confirms (Glowtbook); the inbox and the collector's shelf agree
    S.confirm_claim(c, first["id"], "inbox-maker")
    d = cl.get("/me/claims", headers=maker).json()
    assert d["pending"] == 0 and {x["status"] for x in d["claims"]} == {"approved", "verified"}
    shelf = cl.get("/me/collection", headers=collector).json()
    assert shelf["collection"][0]["verified"] is True
    assert {b["badge"] for b in shelf["badges"]} == {"first-scan"}
