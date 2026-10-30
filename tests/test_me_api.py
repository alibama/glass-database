"""The signed-in person's own pieces through the API (what the capture apps use)."""
import pytest
from fastapi.testclient import TestClient

from central import sessions
from tests.test_fingerprint import _export_zip


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def test_me_anonymous(client):
    j = client.get("/me").json()
    assert j["signed_in"] is False and j["login"].endswith("/auth/login")
    assert client.get("/me/objects").status_code == 401
    assert client.post("/me/objects", json={"title": "x"}).status_code == 401


def test_providers_reports_unconfigured(client):
    j = client.get("/auth/providers").json()
    assert j["configured"] is False and "pair" in j["pairing"].lower()
    assert client.get("/auth/login", follow_redirects=False).status_code == 503
    assert client.get("/auth/dev-login", follow_redirects=False).status_code == 404


def test_dev_login_only_when_enabled(client, monkeypatch):
    monkeypatch.setenv("GLASSDB_DEV_LOGIN", "1")
    r = client.get("/auth/dev-login?next=/fingerprint/enroll.html", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/fingerprint/enroll.html"
    assert sessions.COOKIE in r.cookies
    assert client.get("/me").json()["signed_in"] is True   # cookie jar carries it
    r = client.post("/auth/logout")
    assert r.status_code == 200
    # open-redirect guard
    r = client.get("/auth/dev-login?next=https://evil.example", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_create_list_fingerprint_events(client):
    h = _auth()
    me = client.get("/me", headers=h).json()
    assert me["signed_in"] and me["counts"]["objects"] == 0

    r = client.post("/me/objects", json={"title": "Amber goblet", "maker": "U One",
                                         "techniques": ["Offhand Blown Glass"]}, headers=h)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["title"] == "Amber goblet" and o["fingerprint"] is None
    assert o["publication"]["state"] == "private"
    oid = o["id"]

    # someone else can't see or touch it
    assert client.get(f"/me/objects/{oid}", headers=_auth("u2")).status_code == 404
    assert client.post(f"/me/objects/{oid}/fingerprint", content=_export_zip(),
                       headers={**_auth("u2"), "Content-Type": "application/zip"}).status_code == 404

    # save a fingerprint from the capture app (raw zip body)
    r = client.post(f"/me/objects/{oid}/fingerprint?set_dimensions=1", content=_export_zip(rating=84),
                    headers={**h, "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["rating"] == 84 and j["tier"] == "Strong" and j["views"] == 6
    assert j["object"]["fingerprint"]["rating"] == 84 and j["object"]["fingerprint_at"]

    # the raw fingerprint is retrievable by its owner only (verify.html?mine=<id>)
    assert client.get(f"/me/objects/{oid}/fingerprint", headers=h).json()["frames"]
    assert client.get(f"/me/objects/{oid}/fingerprint", headers=_auth("u2")).status_code == 404

    # bad payloads
    assert client.post(f"/me/objects/{oid}/fingerprint", content=b"", headers=h).status_code == 400
    assert client.post(f"/me/objects/{oid}/fingerprint", content=b"garbage", headers=h).status_code == 400

    # provenance events (a verification)
    r = client.post(f"/me/objects/{oid}/events",
                    json={"event_type": "verified", "note": "match 93/100"}, headers=h)
    assert r.status_code == 200
    assert client.post(f"/me/objects/{oid}/events", json={"event_type": "nope"}, headers=h).status_code == 400
    d = client.get(f"/me/objects/{oid}", headers=h).json()
    assert d["events"] == 1 and d["events_list"][0]["event_type"] == "verified"

    lst = client.get("/me/objects", headers=h).json()["objects"]
    assert len(lst) == 1 and lst[0]["fingerprint"]["tier"] == "Strong"
    assert client.get("/me", headers=h).json()["counts"]["fingerprinted"] == 1


def test_pair_token_works_as_bearer(client, monkeypatch):
    link = sessions.pairing_link("https://x.org", "u7", "u7@x.org", "U Seven", object_id=3)
    from urllib.parse import unquote
    tok = unquote(link.split("#token=")[1].split("&")[0])
    j = client.get("/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert j["signed_in"] and j["kind"] == "pair" and j["name"] == "U Seven"


def test_providers_configured_from_secrets_and_login_redirects(client, tmp_path, monkeypatch):
    from tests.test_sessions import SECRETS
    f = tmp_path / "secrets.toml"
    f.write_text(SECRETS, encoding="utf-8")
    monkeypatch.setenv("GLASSDB_SECRETS_PATH", str(f))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://glassdatabase.org")
    j = client.get("/auth/providers").json()
    assert j["configured"] is True and j["provider"] == "google" and j["note"] is None
    assert j["redirect_uri"] == "https://glassdatabase.org/auth/callback"
    assert j["diagnostics"]["providers"] == ["google"] and j["diagnostics"]["parser"]

    import api.auth as A
    monkeypatch.setattr(A, "_metadata", lambda url: {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo"})
    r = client.get("/auth/login?next=/fingerprint/enroll.html", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in loc and "client_id=abc.apps.googleusercontent.com" in loc
    assert "redirect_uri=https%3A%2F%2Fglassdatabase.org%2Fauth%2Fcallback" in loc
    assert A.STATE_COOKIE in r.cookies


def test_unconfigured_login_explains_why(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SECRETS_PATH", str(tmp_path / "nope.toml"))
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 503 and "no secrets.toml found" in r.json()["detail"]
    j = client.get("/auth/providers").json()
    assert j["configured"] is False and "no secrets.toml found" in j["note"]


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (64, 48), (179, 38, 30)).save(buf, "PNG"); return buf.getvalue()


def test_top_image_and_one_call_publish(client, monkeypatch):
    """The capture app's best shot becomes the top image; Publish is one call; both show on the card."""
    monkeypatch.setenv("GLASSDB_MODERATION", "1")
    h = _auth("u9", "u9@example.com", "U Nine")
    oid = client.post("/me/objects", json={"title": "Amber pumpkin"}, headers=h).json()["id"]
    assert client.get(f"/me/objects/{oid}", headers=h).json()["image_url"] is None
    assert client.get(f"/me/objects/{oid}/image", headers=h).status_code == 404
    assert client.post(f"/me/objects/{oid}/images", content=b"not an image",
                       headers={**h, "Content-Type": "image/jpeg"}).status_code == 400
    # auto: the first photo is the top image
    r = client.post(f"/me/objects/{oid}/images?role=auto&caption=best shot", content=_png(),
                    headers={**h, "Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "primary" and r.json()["object"]["image_url"].endswith(f"/me/objects/{oid}/image")
    img = client.get(f"/me/objects/{oid}/image", headers=h)
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/png")
    assert client.get(f"/me/objects/{oid}/image", headers=_auth("u2")).status_code == 404
    # a second auto photo is just a photo; an explicit primary demotes the first
    assert client.post(f"/me/objects/{oid}/images", content=_png(), headers=h).json()["role"] == "photo"
    assert client.post(f"/me/objects/{oid}/images?role=primary", content=_png(), headers=h).json()["role"] == "primary"
    assert client.get(f"/me/objects/{oid}", headers=h).json()["images"] == 3
    # publish in one call -> the review queue; the card says pending and the next step says so
    assert client.post(f"/me/objects/{oid}/publish", json={}, headers=_auth("u2")).status_code == 404
    r = client.post(f"/me/objects/{oid}/publish", json={"sign": False}, headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["pending"] is True and j["content_hash"] and j["images"] == 3
    assert j["object"]["publication"]["state"] == "pending"
    mine = client.get("/me/objects", headers=h).json()["objects"]
    me = next(o for o in mine if o["id"] == oid)
    assert me["publication"]["state"] == "pending" and me["image_url"]
    from central.dbconn import connect
    c = connect()
    row = c.execute("SELECT status, title FROM object_submissions WHERE content_hash=?", (j["content_hash"],)).fetchone()
    assert row and row[0] == "pending" and row[1] == "Amber pumpkin"
    c.execute("DELETE FROM object_submission_images WHERE submission_id IN (SELECT id FROM object_submissions WHERE content_hash=?)", (j["content_hash"],))
    c.execute("DELETE FROM object_submissions WHERE content_hash=?", (j["content_hash"],)); c.commit()
