"""An https site stays https: the API redirects forwarded-http requests and sets Secure cookies;
the Apache files carry the redirect and HSTS."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_DEV_LOGIN", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://glassdatabase.org")
    from api.main import app
    return TestClient(app)


def test_forwarded_http_is_redirected_to_https(client):
    r = client.get("/me?x=1", headers={"x-forwarded-proto": "http", "host": "glassdatabase.org"},
                   follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://glassdatabase.org/me?x=1"
    # the same request over https, or with no forwarded header at all, is served
    assert client.get("/me", headers={"x-forwarded-proto": "https"}).status_code == 200
    assert client.get("/me").status_code == 200


def test_cookie_is_secure_on_an_https_site(client):
    r = client.get("/auth/dev-login?next=/", follow_redirects=False)
    assert r.status_code == 303
    assert "secure" in r.headers["set-cookie"].lower()


def test_apache_files_force_https():
    vhost = (ROOT / "deploy" / "apache-glassdatabase.conf").read_text(encoding="utf-8")
    assert "RewriteCond %{HTTPS} !=on" in vhost and "acme-challenge" in vhost
    assert "RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]" in vhost
    proxy = (ROOT / "deploy" / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8")
    assert "Strict-Transport-Security" in proxy
    assert "rewrite" in (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")


def test_capture_rating_is_not_a_frame_counter():
    s = (ROOT / "deploy" / "fingerprint-web" / "enroll.html").read_text(encoding="utf-8")
    assert "const PSEUDO_MIN=14" in s and "NO_COMPASS_COV=0.75" in s
    assert "noveltyOf(dh)>=PSEUDO_MIN" in s
    assert "*35*g" in s and "*25*g" in s and "g=Math.sqrt(covf)" in s
