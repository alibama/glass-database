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


@pytest.fixture(autouse=True)
def _review_mode(monkeypatch):
    """These tests were written when entries waited for an admin; keep them in that mode.
    The default (entries of a public piece go live at once) has its own test at the end."""
    monkeypatch.setenv("GLASSDB_SEASON_ENTRY_REVIEW", "1")


def _conn():
    from glowtbook.contribute import ensure_central_objects, ensure_object_staging
    c = connect()
    S.ensure(c); ensure_central_objects(c); ensure_object_staging(c)
    return c


def _object(c, uid=None, title="Test Piece", maker="Tess Maker", approve=True, image=False,
            fingerprint=False, dims=""):
    """A public object. With `uid`, an object_submissions row says who published it;
    `fingerprint` puts a fingerprint on file, `dims` what the mat measured."""
    n = next(_N)
    rid, chash = f"obj-t-{n:04d}", f"hash-t-{n:04d}"
    c.execute("INSERT OR REPLACE INTO objects (_row_id,_source_file,_source_sheet,_imported_at,"
              "title,maker,year,techniques,content_hash,manifest_json,fingerprint_json,dimensions) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (rid, "test", "test", "2026", title, maker, "2026", "Blown", chash, "{}",
               '{"rating": 80, "frames": []}' if fingerprint else None, dims or ""))
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


def _entry(c, sid, uid, studio="Studio A", approve=True, edition=5, solo=False, legendary=False,
           legend="", **kw):
    rid, _ = _object(c, uid=uid, **kw)
    eid = S.enter(c, sid, object_row_id=rid, artist_uid=uid, artist_name=f"Maker {uid}",
                  studio=studio, edition_size=edition, price_note="$50", solo=solo,
                  legendary=legendary, legend=legend)
    if approve:
        S.approve_entry(c, eid, True)
    return eid, rid


# --- seasons ----------------------------------------------------------------------
def test_ensure_create_list_current(demo_db):
    c = _conn()
    for t in ("seasons", "season_entries", "season_votes", "season_claims", "season_badges",
              "season_categories", "season_award_votes"):
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
    cols = {r[1] for r in c.execute("PRAGMA table_info(season_entries)").fetchall()}
    assert {"solo", "legendary", "legend"} <= cols
    S.ensure(c)                                               # idempotent: the ALTERs are guarded
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


# --- awards, rarity, rules ----------------------------------------------------------------
LEGEND = ("Blown on the last night of the residency, from the end of a cane pulled for "
          "something else entirely. The twist runs the wrong way.")


def test_default_categories_by_theme_and_parse(demo_db):
    c = _conn()
    sid = _season(c, theme="Blown-glass pumpkins from every shop")
    cats = S.categories(c, sid)
    assert [x["slug"] for x in cats] == ["best-stem", "best-colour", "biggest-pumpkin",
                                         "biggest-pumpkin-solo"]
    assert [x["kind"] for x in cats] == ["vote", "vote", "measured", "measured"]
    assert cats[2]["measure"] == "height" and cats[2]["solo_only"] == 0 and cats[3]["solo_only"] == 1
    orn = _season(c, theme="Heirloom ornaments")
    assert [x["title"] for x in S.categories(c, orn)] == ["Best hook", "Best colour",
                                                           "Most likely to survive the cat"]
    other = _season(c, theme="Marbles")
    assert [x["title"] for x in S.categories(c, other)] == ["Best in show", "Best colour"]
    parsed = S.parse_categories("Best foot | vote\n\nTallest | measured | solo\nJust a title\n | vote\n"
                                "Best colour | vote\nBest colour | VOTE")
    assert parsed[:3] == [{"title": "Best foot", "kind": "vote", "measure": "", "solo_only": 0},
                          {"title": "Tallest", "kind": "measured", "measure": "height", "solo_only": 1},
                          {"title": "Just a title", "kind": "vote", "measure": "", "solo_only": 0}]
    custom = _season(c, theme="Pumpkins", categories=parsed)
    assert [x["slug"] for x in S.categories(c, custom)] == ["best-foot", "tallest", "just-a-title",
                                                            "best-colour", "best-colour-2"]
    assert S.categories_text(parsed).splitlines()[1] == "Tallest | measured | solo"
    assert S.get_category(c, custom, "tallest")["kind"] == "measured"
    assert S.get_category(c, custom, "nope") is None
    demo = S.get_season(c, "pumpkin-patch-2026")               # the seed got the pumpkin set
    assert {x["slug"] for x in S.categories(c, demo["id"])} >= {"best-stem", "biggest-pumpkin"}
    with pytest.raises(ValueError):
        S.add_categories(c, custom, [{"title": "Heaviest", "kind": "measured", "measure": "weight"}])


def test_award_vote_replace_toggle_no_self_vote(demo_db):
    c = _conn()
    sid = _season(c, theme="pumpkins")
    e1, _ = _entry(c, sid, "aw-a"); e2, _ = _entry(c, sid, "aw-b")
    pend, _ = _entry(c, sid, "aw-c", approve=False)
    stem = S.get_category(c, sid, "best-stem"); big = S.get_category(c, sid, "biggest-pumpkin")
    with pytest.raises(ValueError):                           # sign in
        S.award_vote(c, sid, stem["id"], e1, "")
    with pytest.raises(ValueError, match="own piece"):        # never your own
        S.award_vote(c, sid, stem["id"], e1, "aw-a")
    with pytest.raises(ValueError, match="measured"):         # the mat decides
        S.award_vote(c, sid, big["id"], e1, "v1")
    with pytest.raises(ValueError):                           # pending entry
        S.award_vote(c, sid, stem["id"], pend, "v1")
    with pytest.raises(ValueError):                           # no such award
        S.award_vote(c, sid, 999999, e1, "v1")
    r = S.award_vote(c, sid, stem["id"], e1, "v1")
    assert r["picked"] is True and r["votes"] == 1 and r["category_slug"] == "best-stem"
    assert S.my_award_votes(c, sid, "v1") == {stem["id"]: e1}
    r = S.award_vote(c, sid, stem["id"], e2, "v1")            # pick again = swap
    assert r["picked"] is True and r["votes"] == 1 and S.my_award_votes(c, sid, "v1") == {stem["id"]: e2}
    assert c.execute("SELECT COUNT(*) FROM season_award_votes WHERE category_id=? AND entry_id=?",
                     (stem["id"], e1)).fetchone()[0] == 0
    r = S.award_vote(c, sid, stem["id"], e2, "v1")            # same again = remove
    assert r["picked"] is False and r["votes"] == 0 and S.my_award_votes(c, sid, "v1") == {}
    S.award_vote(c, sid, stem["id"], e2, "v1"); S.award_vote(c, sid, stem["id"], e2, "v2")
    S.award_vote(c, sid, stem["id"], e1, "v3")
    aw = {a["slug"]: a for a in S.awards(c, sid)}
    st = aw["best-stem"]["standings"]
    assert [(s["entry_id"], s["votes"], s["rank"]) for s in st] == [(e2, 2, 1), (e1, 1, 2)]
    assert aw["best-stem"]["leader"]["entry_id"] == e2 and "artist_uid" not in st[0]
    assert aw["best-colour"]["standings"] == [] and aw["best-colour"]["leader"] is None
    assert set(aw["best-stem"]["eligible"]) == {e1, e2}
    # a solo-only vote award only takes solo entries
    S.add_categories(c, sid, [{"title": "Best solo stem", "kind": "vote", "solo_only": 1}])
    sc = S.get_category(c, sid, "best-solo-stem")
    with pytest.raises(ValueError, match="solo"):
        S.award_vote(c, sid, sc["id"], e1, "v1")
    e3, _ = _entry(c, sid, "aw-d", solo=True)
    assert S.award_vote(c, sid, sc["id"], e3, "v1")["picked"] is True
    assert {a["slug"]: a["eligible"] for a in S.awards(c, sid)}["best-solo-stem"] == [e3]
    S.set_status(c, sid, "voting")
    assert S.award_vote(c, sid, stem["id"], e1, "v4")["picked"] is True
    S.set_status(c, sid, "closed")
    with pytest.raises(ValueError):
        S.award_vote(c, sid, stem["id"], e1, "v9")


def test_measured_award_ranks_by_mat_height(demo_db):
    c = _conn()
    sid = _season(c, theme="Pumpkin patch")
    a, _ = _entry(c, sid, "m-a", fingerprint=True, dims="118 × 112 × 134 mm (W×D×H)", solo=True)
    b, _ = _entry(c, sid, "m-b", fingerprint=True, dims="90 × 90 × 150.5 mm (W×D×H)")
    unm, _ = _entry(c, sid, "m-c", fingerprint=True)         # never on the mat: not ranked
    aw = {x["slug"]: x for x in S.awards(c, sid)}
    assert [(s["entry_id"], s["height_mm"], s["rank"]) for s in aw["biggest-pumpkin"]["standings"]] \
        == [(b, 150.5, 1), (a, 134.0, 2)]
    assert [s["entry_id"] for s in aw["biggest-pumpkin-solo"]["standings"]] == [a]
    assert aw["biggest-pumpkin"]["leader"]["entry_id"] == b
    assert S.parse_height_mm("12 x 10 x 8 mm") == 8.0 and S.parse_height_mm("about 10 cm") is None
    assert S.parse_height_mm("") is None and S.parse_height_mm(None) is None
    es = {e["id"]: e for e in S.entries(c, sid)}
    assert es[a]["height_mm"] == 134.0 and es[unm]["height_mm"] is None


def test_legendary_rules(demo_db):
    c = _conn()
    sid = _season(c)
    uid = "leg-maker"
    plain, _ = _object(c, uid=uid)                            # no fingerprint
    with pytest.raises(ValueError, match="edition of one"):
        S.enter(c, sid, object_row_id=plain, artist_uid=uid, artist_name="L", edition_size=2,
                legendary=True, legend=LEGEND)
    with pytest.raises(ValueError, match="[Ff]ingerprint"):
        S.enter(c, sid, object_row_id=plain, artist_uid=uid, artist_name="L", edition_size=1,
                legendary=True, legend=LEGEND)
    fp, _ = _object(c, uid=uid, fingerprint=True)
    with pytest.raises(ValueError, match="legend"):
        S.enter(c, sid, object_row_id=fp, artist_uid=uid, artist_name="L", edition_size=1,
                legendary=True, legend="too short")
    eid = S.enter(c, sid, object_row_id=fp, artist_uid=uid, artist_name="L", edition_size=1,
                  legendary=True, legend=LEGEND)
    e = S.get_entry(c, eid)
    assert e["legendary"] == 1 and e["legend"] == LEGEND and e["fingerprinted"] is True
    assert e["rarity"] == "rare"                              # not claimed: reads as its base rarity
    fp2, _ = _object(c, uid=uid, fingerprint=True)
    with pytest.raises(ValueError, match="[Oo]ne legendary"):
        S.enter(c, sid, object_row_id=fp2, artist_uid=uid, artist_name="L", edition_size=1,
                legendary=True, legend=LEGEND)
    # not legendary: no legend is kept even if one was typed
    e2 = S.get_entry(c, S.enter(c, sid, object_row_id=fp2, artist_uid=uid, artist_name="L", legend=LEGEND))
    assert e2["legendary"] == 0 and e2["legend"] == "" and e2["solo"] == 0
    # five entries per maker per season
    for _ in range(3):
        rid, _ = _object(c, uid=uid)
        S.enter(c, sid, object_row_id=rid, artist_uid=uid, artist_name="L")
    rid, _ = _object(c, uid=uid)
    with pytest.raises(ValueError, match="entries per maker"):
        S.enter(c, sid, object_row_id=rid, artist_uid=uid, artist_name="L")


def test_rarity_points_legendaries_out_and_points_board(demo_db):
    c = _conn()
    sid = _season(c)
    common, rc = _entry(c, sid, "r-a", edition=10)
    unc, ru = _entry(c, sid, "r-b", edition=10, fingerprint=True)
    rare, rr = _entry(c, sid, "r-c", edition=3, fingerprint=True)
    leg, rl = _entry(c, sid, "r-d", edition=1, fingerprint=True, legendary=True, legend=LEGEND)
    _entry(c, sid, "r-e", edition=1, fingerprint=True, legendary=True, legend=LEGEND, approve=False)
    es = {e["id"]: e for e in S.entries(c, sid)}
    assert [(es[i]["rarity"], es[i]["points"]) for i in (common, unc, rare, leg)] \
        == [("common", 1), ("uncommon", 2), ("rare", 5), ("rare", 5)]
    assert S.rarity_of({"edition_size": 1, "legendary": 1}, True, False) == "rare"
    assert S.rarity_of({"edition_size": 1, "legendary": 1}, True, True) == "legendary"
    assert S.rarity_of({"edition_size": 4}, True, False) == "uncommon"
    assert S.rarity_of({"edition_size": 1}, False, True) == "common"
    assert S.legendaries_out(c, sid) == 1                      # the pending one doesn't count
    assert S.get_season(c, sid)["legendaries_out"] == 1
    S.claim(c, object_row_id=rc, collector_uid="pc1", collector_name="Points One", confidence=90)
    S.claim(c, object_row_id=rr, collector_uid="pc1", collector_name="Points One", confidence=90)
    S.claim(c, object_row_id=ru, collector_uid="pc2", collector_name="Points Two", confidence=90)
    low = S.claim(c, object_row_id=rl, collector_uid="pc2", collector_name="Points Two", confidence=40)
    assert S.get_entry(c, leg)["rarity"] == "rare" and S.legendaries_out(c, sid) == 1   # pending claim
    lb = S.leaderboard(c, sid)["points"]
    assert [(h["collector_name"], h["points"], h["pieces"]) for h in lb] \
        == [("Points One", 6, 2), ("Points Two", 2, 1)]
    S.set_claim_status(c, low["id"], "approved")               # the maker / an admin confirms
    e = S.get_entry(c, leg)
    assert e["rarity"] == "legendary" and e["points"] == 20 and S.legendaries_out(c, sid) == 0
    lb = S.leaderboard(c, sid)["points"]
    assert [(h["collector_name"], h["points"], h["legendaries"], h["rank"]) for h in lb] \
        == [("Points Two", 22, 1, 1), ("Points One", 6, 0, 2)]
    assert "legend_hunter" in {b["badge"] for b in S.badges_for(c, "pc2")}
    assert "legend_hunter" not in {b["badge"] for b in S.badges_for(c, "pc1")}
    sm = S.summary(c)
    assert sm["legendaries_out"] >= 0 and sm["award_votes"] >= 1          # the seed's pick


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


def test_api_awards_and_legendary_hidden_until_claimed(demo_db):
    c = _conn()
    sid = _season(c, theme="pumpkins")
    slug = S.get_season(c, sid)["slug"]
    e1, _ = _entry(c, sid, "api-a", fingerprint=True, dims="100 × 100 × 120 mm (W×D×H)")
    e2, r2 = _entry(c, sid, "api-b", edition=1, fingerprint=True, legendary=True, legend=LEGEND)
    cl = _client()
    d = cl.get(f"/seasons/{slug}").json()
    assert d["legendaries_out"] == 1 and d["season"]["legendaries_out"] == 1
    by = {e["id"]: e for e in d["entries"]}
    assert by[e2]["rarity"] == "rare" and "legendary" not in by[e2] and "legend" not in by[e2]
    assert by[e1]["rarity"] == "uncommon" and by[e1]["height_mm"] == 120 and by[e1]["points"] == 2
    assert [a["slug"] for a in d["awards"]] == ["best-stem", "best-colour", "biggest-pumpkin",
                                                "biggest-pumpkin-solo"]
    assert d["my_picks"] == {} and d["leaderboard"]["points"] == []
    mine = cl.get(f"/seasons/{slug}", headers=_auth("api-b", "b@example.com", "B")).json()
    assert {e["id"]: e for e in mine["entries"]}[e2]["legendary"] == 1      # the maker sees it

    assert cl.post(f"/seasons/{slug}/awards/best-stem/vote", json={"entry_id": e1}).status_code == 401
    assert cl.post(f"/seasons/{slug}/awards/best-stem/vote", json={"entry_id": e1},
                   headers=_auth("api-a")).status_code == 400                # own piece
    assert cl.post(f"/seasons/{slug}/awards/no-such/vote", json={"entry_id": e1},
                   headers=_auth()).status_code == 404
    assert cl.post(f"/seasons/{slug}/awards/biggest-pumpkin/vote", json={"entry_id": e1},
                   headers=_auth()).status_code == 400                       # measured
    assert cl.post(f"/seasons/{slug}/awards/best-stem/vote", json={}, headers=_auth()).status_code == 400
    r = cl.post(f"/seasons/{slug}/awards/best-stem/vote", json={"entry_id": e1}, headers=_auth())
    assert r.status_code == 200 and r.json()["picked"] is True and r.json()["votes"] == 1
    a = cl.get(f"/seasons/{slug}/awards", headers=_auth()).json()
    assert a["my_picks"] == {"best-stem": e1} and a["can_vote"] is True
    aw = {x["slug"]: x for x in a["awards"]}
    assert aw["best-stem"]["leader"]["entry_id"] == e1 and aw["best-stem"]["leader"]["votes"] == 1
    top = aw["biggest-pumpkin"]["standings"][0]
    assert top["entry_id"] == e1 and top["height_mm"] == 120 and "artist_uid" not in top
    r = cl.post(f"/seasons/{slug}/awards/best-stem/vote", json={"entry_id": e1}, headers=_auth())
    assert r.json()["picked"] is False
    assert cl.get(f"/seasons/{slug}/awards", headers=_auth()).json()["my_picks"] == {}

    # entering over HTTP with the flags; validation errors come through as 400s
    own, _ = _object(c, uid="u1", fingerprint=True)
    r = cl.post(f"/seasons/{slug}/entries", json={"object_row_id": own, "edition_size": 1,
                                                  "legendary": True, "legend": "short"}, headers=_auth())
    assert r.status_code == 400 and "legend" in r.json()["detail"]
    r = cl.post(f"/seasons/{slug}/entries", json={"object_row_id": own, "edition_size": 1, "solo": True,
                                                  "legendary": True, "legend": LEGEND}, headers=_auth())
    assert r.status_code == 200 and r.json()["entry"]["legendary"] == 1 and r.json()["entry"]["solo"] == 1

    # a claim reveals the legendary to everyone and scores 20
    r = cl.post("/claims", json={"object_row_id": r2, "confidence": 92},
                headers=_auth("hunter", "h@example.com", "Hunter"))
    assert r.status_code == 200 and "legend_hunter" in r.json()["badges"]
    d = cl.get(f"/seasons/{slug}").json()
    pub = {e["id"]: e for e in d["entries"]}[e2]
    assert pub["rarity"] == "legendary" and pub["legendary"] == 1 and pub["legend"] == LEGEND
    assert pub["points"] == 20 and d["legendaries_out"] == 0
    pts = cl.get(f"/seasons/{slug}/leaderboard").json()["points"]
    assert pts[0]["collector_name"] == "Hunter" and pts[0]["points"] == 20 and "collector_uid" not in pts[0]


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


def test_categories_backfill_for_seasons_made_before_awards(demo_db):
    """A season from before awards existed has no categories; the first read gives it
    its theme's defaults, unless it is closed."""
    c = connect()
    sid = _season(c, title="Old Pumpkin Patch", theme="pumpkins everywhere")
    sid = sid["id"] if isinstance(sid, dict) else sid
    c.execute("DELETE FROM season_categories WHERE season_id=?", (sid,)); c.commit()
    assert S.categories(c, sid, backfill=False) == []
    cats = S.categories(c, sid)
    assert [x["title"] for x in cats][:2] == ["Best stem", "Best colour"]
    assert S.categories(c, sid) == cats          # idempotent
    closed = _season(c, title="Closed Long Ago", theme="pumpkins", status="closed")
    closed = closed["id"] if isinstance(closed, dict) else closed
    c.execute("DELETE FROM season_categories WHERE season_id=?", (closed,)); c.commit()
    assert S.categories(c, closed) == []


def test_default_mode_a_published_piece_goes_live_at_once(demo_db, monkeypatch):
    """A piece that is public was reviewed once already: entering it needs no second approval."""
    monkeypatch.setenv("GLASSDB_SEASON_ENTRY_REVIEW", "0")
    c = _conn(); sid = _season(c); slug = S.get_season(c, sid)["slug"]
    rid, _ = _object(c, uid="live1", title="Live Pumpkin")
    eid = S.enter(c, sid, object_row_id=rid, artist_uid="live1", artist_name="Live One")
    assert S.get_entry(c, eid)["status"] == "approved"
    assert eid in [x["id"] for x in S.entries(c, sid)]                    # on the season page
    assert any(b["badge"] == "maker-debut" for b in S.badges_for(c, "live1"))
    # an entry left pending from review mode goes live on the next pass
    c.execute("UPDATE season_entries SET status='pending' WHERE id=?", (eid,)); c.commit()
    assert S.release_backlog(c) >= 1 and S.get_entry(c, eid)["status"] == "approved"   # other tests leave pending rows too
    # over HTTP the answer says so, and nothing is pending
    from api.main import app
    cl = TestClient(app)
    rid2, _ = _object(c, uid="u1", title="Live Two")
    r = cl.post(f"/seasons/{slug}/entries", json={"object_row_id": rid2}, headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is False and r.json()["entry"]["status"] == "approved"
    assert "season page now" in r.json()["note"]
    # review mode still queues
    monkeypatch.setenv("GLASSDB_SEASON_ENTRY_REVIEW", "1")
    rid3, _ = _object(c, uid="live1", title="Queued Pumpkin")
    assert S.get_entry(c, S.enter(c, sid, object_row_id=rid3, artist_uid="live1", artist_name="Live One"))["status"] == "pending"
