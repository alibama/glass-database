"""The sign-in callback finishes even when the browser dropped the state cookie, fails as a
readable page, hands static pages a token, and the API keeps one public host."""
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from central import sessions


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://glassdatabase.org")
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api import auth
    monkeypatch.setattr(auth, "provider", lambda: ("google", {"client_id": "cid", "client_secret": "sec",
                                                              "server_metadata_url": "https://idp.test/meta"}))
    monkeypatch.setattr(auth, "_metadata", lambda url: {"authorization_endpoint": "https://idp.test/auth",
                                                        "token_endpoint": "https://idp.test/token",
                                                        "userinfo_endpoint": "https://idp.test/me"})
    monkeypatch.setattr(auth, "_exchange", lambda cfg, meta, code, verifier, redirect:
                        {"sub": "g-1", "email": "g@example.com", "name": "G One"})
    from api.main import app
    # https base: the Secure cookies are sent back, as a real browser would
    yield TestClient(app, base_url="https://testserver")
    # the callback records the login in the shared demo database; leave it as found
    try:
        from central.dbconn import connect
        c = connect(); c.execute("DELETE FROM users WHERE email='g@example.com'"); c.commit()
    except Exception:  # noqa: BLE001
        pass


def _start(client, nxt):
    r = client.get(f"/auth/login?next={nxt}", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://idp.test/auth?")
    q = parse_qs(urlsplit(loc).query)
    assert q["redirect_uri"] == ["https://glassdatabase.org/auth/callback"]
    return q["state"][0]


def test_callback_with_the_cookie_hands_static_pages_a_token(client):
    state = _start(client, "/fingerprint/enroll.html")
    assert "gdb_oauth" in client.cookies
    r = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/fingerprint/enroll.html#session=")
    tok = parse_qs(urlsplit(loc).fragment)["session"][0]
    assert sessions.verify(tok)["email"] == "g@example.com"
    assert "gdb_session=" in r.headers["set-cookie"] and "secure" in r.headers["set-cookie"].lower()


def test_callback_without_the_cookie_uses_the_server_record_once(client):
    state = _start(client, "/games.html")
    client.cookies.clear()                                     # the phone dropped the cookie
    r = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/games.html#session=")
    client.cookies.clear()                                     # a replay: record gone, no session
    r = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 400 and "text/html" in r.headers["content-type"]
    assert "Try again" in r.text and "finish" in r.text


def test_replay_while_signed_in_goes_to_my_account(client):
    state = _start(client, "/account.html")
    assert client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False).status_code == 303
    assert "gdb_session" in client.cookies
    r = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)   # back button
    assert r.status_code == 303 and r.headers["location"] == "/account.html"


def test_streamlit_pages_get_the_cookie_only(client):
    state = _start(client, "/glowtbook/?page=home")
    r = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/glowtbook/?page=home"


def test_bad_state_and_provider_errors_are_pages(client):
    r = client.get("/auth/callback?code=c&state=nope", follow_redirects=False)
    assert r.status_code == 400 and "Try again" in r.text and "â" not in r.text
    r = client.get("/auth/callback?error=access_denied", follow_redirects=False)
    assert r.status_code == 400 and "cancelled" in r.text


def test_state_cookie_and_providers_diagnostics(client):
    r = client.get("/auth/login?next=/", follow_redirects=False)
    sc = r.headers["set-cookie"].lower()
    assert "gdb_oauth=" in sc and "max-age=1200" in sc and "secure" in sc
    assert client.get("/auth/providers").json()["secret_source"] == "env:GLASSDB_SESSION_SECRET"


def test_www_is_sent_to_the_one_public_host(client):
    r = client.get("/me", headers={"x-forwarded-proto": "https", "x-forwarded-host": "www.glassdatabase.org"},
                   follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://glassdatabase.org/me"
    assert client.get("/me", headers={"x-forwarded-proto": "https", "x-forwarded-host": "glassdatabase.org"}).status_code == 200


def test_secret_source_names_the_fallbacks(monkeypatch):
    monkeypatch.delenv("GLASSDB_SESSION_SECRET", raising=False)
    monkeypatch.setattr(sessions, "auth_config", lambda: {})
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "change-me")
    assert sessions.secret_source() == "ephemeral"
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "abc123")
    assert sessions.secret_source() == "env:GLASSDB_ADMIN_TOKEN"
