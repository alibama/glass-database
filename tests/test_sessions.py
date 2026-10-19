"""Signed site-wide sessions: issue/verify, expiry, kinds, pairing links."""
import time

from central import sessions


def test_issue_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    tok = sessions.issue("google|123", "Someone@Example.com", "Some One")
    u = sessions.verify(tok)
    assert u and u["uid"] == "google|123" and u["email"] == "someone@example.com"
    assert u["name"] == "Some One" and u["kind"] == "session" and u["exp"] > time.time()


def test_tampered_or_foreign_tokens_fail(monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "a")
    tok = sessions.issue("u1")
    assert sessions.verify(tok)
    p, sig = tok.split(".")
    assert sessions.verify(p + "x." + sig) is None          # payload changed
    assert sessions.verify(p + "." + sig[:-2] + "zz") is None  # signature changed
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "b")      # different server key
    assert sessions.verify(tok) is None
    assert sessions.verify(None) is None and sessions.verify("nonsense") is None


def test_expiry_and_kinds(monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "k")
    assert sessions.verify(sessions.issue("u", ttl=-5)) is None
    st = sessions.issue("oauth", ttl=60, kind="state", extra={"next": "/x"})
    assert sessions.verify(st) is None                      # not a session/pair token
    s = sessions.verify(st, kinds=("state",))
    assert s and s["next"] == "/x"


def test_pairing_link_puts_token_in_fragment(monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "k")
    link = sessions.pairing_link("https://glassdatabase.org/", "u1", "u@x.org", "U", object_id=12)
    assert link.startswith("https://glassdatabase.org/fingerprint/enroll.html#token=")
    assert link.endswith("&object=12") and "?" not in link
    tok = link.split("#token=")[1].split("&")[0]
    from urllib.parse import unquote
    u = sessions.verify(unquote(tok))
    assert u and u["kind"] == "pair" and u["uid"] == "u1"


def test_request_header_resolution(monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "k")
    tok = sessions.issue("u9", "n@x.org", "N")
    assert sessions.from_request_headers({"authorization": f"Bearer {tok}"}, {})["uid"] == "u9"
    assert sessions.from_request_headers({}, {sessions.COOKIE: tok})["uid"] == "u9"
    assert sessions.from_request_headers({}, {}) is None
