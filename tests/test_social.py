"""Promotion: encrypted connected accounts, Bluesky posting through the API, public
collections, share cards and Open Graph landing pages."""
import pytest
from fastapi.testclient import TestClient

from central import sessions, social
from central.dbconn import connect


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def test_encrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    tok = social.encrypt("xxxx-yyyy-zzzz-wwww")
    assert tok != "xxxx-yyyy-zzzz-wwww" and social.decrypt(tok) == "xxxx-yyyy-zzzz-wwww"
    monkeypatch.setenv("GLASSDB_SOCIAL_KEY", "another")
    with pytest.raises(Exception):
        social.decrypt(tok)


def test_connect_status_credentials_disconnect(demo_db, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    c = connect()
    assert social.status(c, "u1")["bluesky"] == {"connected": False}
    with pytest.raises(ValueError):
        social.connect_bluesky(c, "u1", "nodots", "pw", verify=lambda h, p: "did:x")
    with pytest.raises(ValueError):
        social.connect_bluesky(c, "u1", "me.bsky.social", "", verify=lambda h, p: "did:x")

    def bad(h, p):
        raise RuntimeError("401")
    with pytest.raises(ValueError):
        social.connect_bluesky(c, "u1", "me.bsky.social", "pw", verify=bad)
    st = social.connect_bluesky(c, "u1", "@Me.bsky.social", "app-pw", verify=lambda h, p: "did:plc:abc")
    assert st["connected"] and st["handle"] == "me.bsky.social" and st["did"] == "did:plc:abc"
    assert "secret_enc" not in st
    assert social.credentials(c, "u1") == ("me.bsky.social", "app-pw")
    social.disconnect(c, "u1", "bluesky")
    assert social.credentials(c, "u1") is None


def test_collection_share_roundtrip(demo_db):
    c = connect()
    assert social.my_collection_share(c, "u9") is None
    sh = social.share_collection(c, "u9", "Rae")
    assert sh["share_id"] and sh["name"] == "Rae"
    assert social.share_collection(c, "u9")["share_id"] == sh["share_id"]       # idempotent
    pub = social.public_collection(c, sh["share_id"])
    assert pub["name"] == "Rae" and pub["count"] == len(pub["pieces"])
    social.unshare_collection(c, "u9")
    assert social.public_collection(c, sh["share_id"]) is None


def test_api_connect_post_and_share(client, monkeypatch):
    from glowtbook import bluesky
    monkeypatch.setattr(bluesky, "verify_login", lambda h, p: "did:plc:test")
    posted = {}

    def fake_post(handle, pw, text, image_bytes=None, image_mime="image/jpeg", alt="", links=None, **kw):
        posted.update(handle=handle, pw=pw, text=text, image=bool(image_bytes), mime=image_mime, links=links)
        return "https://bsky.app/profile/me.bsky.social/post/abc"
    monkeypatch.setattr(bluesky, "post", fake_post)

    assert client.get("/social/status").status_code == 401
    assert client.get("/social/status", headers=_auth()).json()["bluesky"]["connected"] is False
    # posting before connecting tells the page to connect
    r = client.post("/social/bluesky/post", json={"text": "hi"}, headers=_auth())
    assert r.status_code == 409
    j = client.post("/social/bluesky/connect", json={"handle": "me.bsky.social", "app_password": "pw"},
                    headers=_auth()).json()
    assert j["connected"] and j["handle"] == "me.bsky.social"
    r = client.post("/social/bluesky/post", json={"text": "Look at this\nhttps://x.test/a", "links": ["https://x.test/a"],
                                                  "image": "object:obj-demo-0001"}, headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://bsky.app/")
    assert posted["handle"] == "me.bsky.social" and posted["pw"] == "pw" and posted["image"] and posted["mime"] == "image/jpeg"
    assert posted["links"] == ["https://x.test/a"]
    shares = client.get("/social/shares", headers=_auth()).json()["shares"]
    assert shares and shares[0]["kind"] == "object" and shares[0]["ref"] == "obj-demo-0001"
    assert client.delete("/social/bluesky", headers=_auth()).json()["ok"]
    assert client.get("/social/status", headers=_auth()).json()["bluesky"]["connected"] is False


def test_api_cards_and_share_pages(client):
    # a published demo piece has a card and an Open Graph landing page
    r = client.get("/social/objects/obj-demo-0001/card.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    r = client.get("/social/share/object/obj-demo-0001")
    assert r.status_code == 200
    assert 'property="og:image"' in r.text and "/api/social/objects/obj-demo-0001/card.png" in r.text
    assert 'http-equiv="refresh"' in r.text
    assert client.get("/social/share/object/nope").status_code == 404
    assert client.get("/social/share/weird/1").status_code == 404
    t = client.get("/social/share-target/object/obj-demo-0001").json()
    assert t["title"] and t["image"].endswith("/card.png") and t["signed_in"] is False
    # a shared collection: public JSON + card + landing
    sh = client.post("/social/collections/share", json={"name": "Rae"}, headers=_auth()).json()
    assert sh["share_id"] and sh["url"].startswith("/collection.html?c=")
    pub = client.get(f"/social/collections/{sh['share_id']}").json()
    assert pub["name"] == "Rae" and "pieces" in pub
    assert client.get(f"/social/collections/{sh['share_id']}/card.png").status_code == 200
    assert client.get(f"/social/share/collection/{sh['share_id']}").status_code == 200
    assert client.get("/social/collections/mine", headers=_auth()).json()["shared"] is True
    assert client.delete("/social/collections/share", headers=_auth()).json()["ok"]
    assert client.get(f"/social/collections/{sh['share_id']}").status_code == 404


def test_link_facets():
    from glowtbook.bluesky import link_facets
    text = "Héllo https://a.test/x and more"
    f = link_facets(text, ["https://a.test/x"])
    assert f and f[0]["features"][0]["uri"] == "https://a.test/x"
    b = text.encode("utf-8")
    i = f[0]["index"]
    assert b[i["byteStart"]:i["byteEnd"]].decode() == "https://a.test/x"
