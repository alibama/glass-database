"""My account + the one next-step function every pieces table uses."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central import nav, sessions
from glowtbook import store as gstore

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def _card(oid=7, fp=True, state="private", row_id="obj-x"):
    return {"id": oid, "title": "Amber goblet",
            "fingerprint": {"rating": 88, "tier": "Strong"} if fp else None,
            "publication": {"state": state, "row_id": row_id}}


def test_next_step_walks_the_ladder():
    season = {"id": 3, "title": "Court of the Gourds", "slug": "gourds-2026"}
    assert gstore.next_step(_card(fp=False))["kind"] == "register"
    assert gstore.next_step(_card(fp=False))["url"] == "/fingerprint/enroll.html?object=7"
    assert gstore.next_step(_card())["kind"] == "publish"
    assert gstore.next_step(_card())["url"] == "/account.html?piece=7#golive"
    assert gstore.next_step(_card(state="rejected"))["label"].startswith("Fix it")
    assert gstore.next_step(_card(state="pending"))["kind"] == "review"
    n = gstore.next_step(_card(state="published"), claims={"ok": 1, "pending": 2})
    assert n["kind"] == "claims" and "2 collection requests" in n["label"] and n["url"].endswith("tab=seasons")
    n = gstore.next_step(_card(state="published"), season=season)
    assert n["kind"] == "enter" and "Court of the Gourds" in n["label"]
    n = gstore.next_step(_card(state="published"), season=season, entered={"obj-x"})
    assert n["kind"] == "entered" and n["url"] == "/games.html?season=gourds-2026"
    assert gstore.next_step(_card(state="published"))["kind"] == "done"
    # every step is a link
    for kind_card in (_card(fp=False), _card(), _card(state="pending"), _card(state="published")):
        assert gstore.next_step(kind_card)["url"].startswith("/")


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


def test_me_objects_carry_next_and_url(client):
    h = {"Authorization": f"Bearer {sessions.issue('u1', 'u1@example.com', 'U One')}"}
    oid = client.post("/me/objects", json={"title": "Amber goblet"}, headers=h).json()["id"]
    objs = client.get("/me/objects", headers=h).json()["objects"]
    o = next(x for x in objs if x["id"] == oid)
    assert o["url"] == f"/glowtbook/?page=objects&object={oid}"
    assert o["next"]["kind"] == "register" and o["next"]["url"].endswith(f"object={oid}")


def test_my_account_is_in_the_menu_and_the_pages_exist():
    assert ("account", "My account", "/account.html") in nav.NAV
    acct = (DEPLOY / "account.html").read_text(encoding="utf-8")
    assert 'data-nav data-active="account"' in acct
    for path in ("/me/objects", "/me/collection", "/me/claims", "/social/status"):
        assert path in acct
    infra = (DEPLOY / "infrastructure.html").read_text(encoding="utf-8")
    assert "Being built" in infra and "Running" in infra and "discord.gg" in infra
    home = (DEPLOY / "landing-index.html").read_text(encoding="utf-8")
    assert "/infrastructure.html" in home and "our own AI" in home
    assert (ROOT / "docs" / "INFRASTRUCTURE.md").exists()


def test_capture_app_puts_actions_before_the_shots():
    s = (DEPLOY / "fingerprint-web" / "enroll.html").read_text(encoding="utf-8")
    assert s.index('id="saveBtn"') < s.index('id="grid"')
    assert 'id="pctline"' in s and "S.overlay=false" in s


def test_walkthrough_short_is_where_people_look():
    short = "MjqwGRfsR_s"
    tour = (DEPLOY / "tour.html").read_text(encoding="utf-8")
    assert f"youtube-nocookie.com/embed/{short}" in tour and "coming soon" not in tour
    assert short in (DEPLOY / "landing-index.html").read_text(encoding="utf-8")
    assert short in (DEPLOY / "fingerprint-web" / "enroll.html").read_text(encoding="utf-8")
    assert short in (ROOT / "docs" / "FINGERPRINT-WORKFLOW.md").read_text(encoding="utf-8")


def test_go_live_panel_and_piece_cards_are_in_place():
    """One panel for register → top image → publish → enter; one card renderer for every piece grid."""
    acct = (DEPLOY / "account.html").read_text(encoding="utf-8")
    assert 'id="golive"' in acct and "GDB.pieceCard(" in acct
    for path in ("/me/objects/\" + o.id + \"/publish", "/me/objects/\" + o.id + \"/images", "/entries"):
        assert path in acct, path
    js = (DEPLOY / "site.js").read_text(encoding="utf-8")
    assert "function pieceCard(" in js and "pieceCard: pieceCard" in js
    assert ".pc-hp" in (DEPLOY / "paper.css").read_text(encoding="utf-8")
    assert "GDB.pieceCard(" in (DEPLOY / "collection.html").read_text(encoding="utf-8")
    games = (DEPLOY / "games.html").read_text(encoding="utf-8")
    assert games.count("GDB.pieceCard(") == 2 and 'class="card third"' not in games
    enroll = (DEPLOY / "fingerprint-web" / "enroll.html").read_text(encoding="utf-8")
    assert "heroGrab" in enroll and "/images?role=auto" in enroll and "#golive" in enroll
    assert gstore.golive_url(7) == "/account.html?piece=7#golive"
