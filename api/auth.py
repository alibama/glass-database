"""
api.auth — sign-in for the static surfaces
==========================================
Google (or any OIDC broker) sign-in handled by the API, so plain pages — the
fingerprint capture apps, Seasons, the graph — can know who you are. Reuses the
same ``[auth.<provider>]`` block in ``.streamlit/secrets.toml`` that Glowtbook's
``st.login()`` uses; register ONE extra redirect URI with the provider:

    https://<domain>/api/auth/callback

Routes
  GET  /auth/providers   → is sign-in configured, and how
  GET  /auth/login?next= → start the authorization-code + PKCE flow
  GET  /auth/callback    → finish it, set the ``gdb_session`` cookie, redirect
  GET|POST /auth/logout  → clear the cookie
  GET  /auth/dev-login   → ONLY with GLASSDB_DEV_LOGIN=1: a fake local sign-in

Any route can resolve the caller with ``current_user(request)`` (None when
anonymous) or ``Depends(require_user)`` (401 when anonymous). Both accept the
cookie or an ``Authorization: Bearer`` token (what a paired phone sends).
``Depends(require_admin)`` is the same for administrators (``central.users`` roles,
or the ``X-API-Key`` admin token).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import sys
import time
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import sessions  # noqa: E402

router = APIRouter(prefix="/auth", tags=["auth"])
STATE_COOKIE = "gdb_oauth"
STATE_TTL = 20 * 60                 # a sign-in has twenty minutes to come back from the provider
_META_CACHE: dict[str, tuple[float, dict]] = {}
log = logging.getLogger("glassdb.auth")


# --- pending sign-ins ----------------------------------------------------------
# The state cookie is the first line; a copy of the same signed token is kept in the
# central database keyed by the public ``state`` value, so a sign-in still finishes
# when the browser drops the cookie between leaving for the provider and coming back
# (seen on phones: in-app browsers, storage partitioning, a reload mid-flow). The
# record is single-use and expires with STATE_TTL.
def _state_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _pending_db():
    try:
        from central.dbconn import connect
        c = connect()
        c.execute("CREATE TABLE IF NOT EXISTS oauth_pending (state_hash TEXT PRIMARY KEY, "
                  "token TEXT NOT NULL, created_at REAL NOT NULL)")
        return c
    except Exception:  # noqa: BLE001
        return None


def _pending_put(state_hash: str, token: str) -> None:
    c = _pending_db()
    if c is None:
        return
    try:
        c.execute("DELETE FROM oauth_pending WHERE created_at < ?", (time.time() - STATE_TTL,))
        c.execute("INSERT OR REPLACE INTO oauth_pending (state_hash, token, created_at) VALUES (?,?,?)",
                  (state_hash, token, time.time()))
        c.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not record the pending sign-in")


def _pending_take(state_hash: str) -> str | None:
    """The token for this state, once; the record is removed whether or not it was fresh."""
    c = _pending_db()
    if c is None or not state_hash:
        return None
    try:
        r = c.execute("SELECT token FROM oauth_pending WHERE state_hash=? AND created_at >= ?",
                      (state_hash, time.time() - STATE_TTL)).fetchone()
        c.execute("DELETE FROM oauth_pending WHERE state_hash=?", (state_hash,))
        c.commit()
        return r[0] if r else None
    except Exception:  # noqa: BLE001
        return None


def _fail_page(request: Request, title: str, why: str, nxt: str = "/", status: int = 400) -> HTMLResponse:
    """A readable page instead of raw JSON when a sign-in cannot finish (ASCII only:
    phones have shown this page with the wrong charset)."""
    again = f"{_api_prefix(request)}/auth/login?next={quote(_safe_next(nxt), safe='')}"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} - Glass Database</title>
<style>body{{margin:0;background:#F4EEE4;color:#1F1A16;font:16px/1.5 Inter,system-ui,sans-serif}}
main{{max-width:34rem;margin:0 auto;padding:3rem 1.25rem}}h1{{font:400 2rem/1.15 Georgia,serif;margin:0 0 1rem}}
p{{margin:0 0 1rem;color:#4A423A}}a.b{{display:inline-block;border:1px solid #1F1A16;padding:.7rem 1.1rem;color:#1F1A16;text-decoration:none;
font:500 12px/1 ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}}a.b:hover{{background:#1F1A16;color:#F4EEE4}}
small{{display:block;margin-top:2rem;color:#6B6157;font-size:13px}}</style></head><body><main>
<p style="font:500 11px/1 ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:#B3261E">Sign-in</p>
<h1>{escape(title)}</h1><p>{escape(why)}</p>
<p><a class="b" href="{escape(again)}">Try again</a> &nbsp; <a class="b" href="/">Home</a></p>
<small>Nothing you were doing is lost: the capture app keeps your shots on the phone, and your pieces are in Glowtbook.
If this keeps happening, say so on the Discord and name the phone and browser.</small></main></body></html>"""
    return HTMLResponse(body, status_code=status)


# --- resolving the caller ---------------------------------------------------
def current_user(request: Request) -> dict | None:
    return sessions.from_request_headers(request.headers, request.cookies)


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Sign in first (GET /api/auth/login), or open this page from "
                                 "Glowtbook so it's paired to your account.")
    return u


def is_admin_request(request: Request) -> bool:
    """An administrator: a signed-in person whose email is an admin (``central.users``:
    GLASSDB_ADMIN_EMAILS or promoted in Admin → Users), or a caller holding the API admin
    key (``X-API-Key`` = GLASSDB_ADMIN_TOKEN, what scripts and cron use)."""
    key = os.environ.get("GLASSDB_ADMIN_TOKEN", "")
    if key and request.headers.get("x-api-key", "") == key:
        return True
    u = current_user(request)
    if not u or not u.get("email"):
        return False
    try:
        from central import users
        from central.dbconn import connect
        return users.is_admin(connect(), u["email"])
    except Exception:  # noqa: BLE001
        return False


def require_admin(request: Request) -> dict:
    """The admin routes of the static admin pages (review queue, the wiki). 401 when
    anonymous, 403 when signed in but not an administrator."""
    if is_admin_request(request):
        return current_user(request) or {"uid": "api-key", "email": "", "name": "API key", "kind": "key"}
    if current_user(request):
        raise HTTPException(403, "You're signed in, but you're not an administrator. Ask an admin to "
                                 "promote you (Admin → Users), then reload.")
    raise HTTPException(401, "Sign in first (GET /api/auth/login).")


# --- provider config ----------------------------------------------------------
def provider() -> tuple[str, dict] | None:
    """(name, {client_id, client_secret, server_metadata_url}) — the first
    configured provider sub-table, else the top-level [auth] triple, else None."""
    cfg = sessions.auth_config()
    if not cfg:
        return None
    need = sessions.PROVIDER_KEYS
    for k, v in cfg.items():
        if isinstance(v, dict) and all(v.get(n) for n in need):
            return k, dict(v)
    if all(cfg.get(n) for n in need):
        return "default", {n: cfg[n] for n in need}
    return None


def why_not_configured() -> str:
    """A specific, admin-readable reason sign-in is off (no secret values)."""
    d = sessions.secrets_diagnostics()
    if not d["exists"]:
        return (f"no secrets.toml found (looked at {d['path']}); copy .streamlit/secrets.toml.example, "
                "fill in [auth.google], and restart the API")
    if d["error"]:
        return f"secrets.toml at {d['path']} could not be read: {d['error']}"
    if not d["has_auth"]:
        return f"secrets.toml at {d['path']} has no [auth] block"
    if d["incomplete"] and not d["providers"]:
        parts = "; ".join(f"[auth.{k}] is missing {', '.join(v)}" for k, v in d["incomplete"].items())
        return f"secrets.toml at {d['path']}: {parts}"
    return f"secrets.toml at {d['path']} has [auth] but no provider table with client_id, client_secret and server_metadata_url"


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _api_prefix(request: Request) -> str:
    return (request.scope.get("root_path") or os.environ.get("ROOT_PATH") or "").rstrip("/")


def redirect_uri(request: Request) -> str:
    cfg = sessions.auth_config()
    if cfg.get("api_redirect_uri"):
        return str(cfg["api_redirect_uri"])
    return f"{_public_base(request)}{_api_prefix(request)}/auth/callback"


def _metadata(url: str) -> dict:
    now = time.time()
    hit = _META_CACHE.get(url)
    if hit and now - hit[0] < 3600:
        return hit[1]
    import httpx
    with httpx.Client(timeout=10, follow_redirects=True) as c:
        r = c.get(url); r.raise_for_status(); meta = r.json()
    _META_CACHE[url] = (now, meta)
    return meta


def _safe_next(nxt: str | None) -> str:
    nxt = (nxt or "/").strip()
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        return "/"
    return nxt


def public_https() -> bool:
    """The site is served over https (PUBLIC_BASE_URL says so): cookies are always
    Secure and plain-http requests are sent to https, whatever hop they came in on."""
    return os.environ.get("PUBLIC_BASE_URL", "").lower().startswith("https://")


def _secure(request: Request) -> bool:
    return public_https() or (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"


def set_session_cookie(response: Response, request: Request, token: str,
                       max_age: int = sessions.DEFAULT_TTL) -> None:
    response.set_cookie(sessions.COOKIE, token, max_age=max_age, path="/", httponly=True,
                        samesite="lax", secure=_secure(request))


# --- routes -------------------------------------------------------------------
@router.get("/providers", summary="Is sign-in configured for the static pages?")
def providers(request: Request):
    p = provider()
    dev = os.environ.get("GLASSDB_DEV_LOGIN") == "1"
    return {"configured": bool(p) or dev, "provider": (p[0] if p else ("dev" if dev else None)),
            "login": f"{_api_prefix(request)}/auth/login",
            "secret_source": sessions.secret_source(),
            "redirect_uri": redirect_uri(request) if p else None,
            "pairing": "Open the capture app from Glowtbook → Objects → Fingerprint to pair a "
                       "device without signing in again.",
            "note": None if p else ("Sign-in isn't configured on this server: " + why_not_configured()
                                    + ". Register /api/auth/callback as a redirect URI too (deploy/AUTH.md)."),
            "diagnostics": sessions.secrets_diagnostics()}


@router.get("/login", summary="Start sign-in (authorization code + PKCE)")
def login(request: Request, next: str = "/"):  # noqa: A002
    p = provider()
    if not p:
        if os.environ.get("GLASSDB_DEV_LOGIN") == "1":
            from urllib.parse import quote
            return RedirectResponse(f"{_api_prefix(request)}/auth/dev-login?next={quote(_safe_next(next), safe='')}")
        raise HTTPException(503, "Sign-in isn't configured on this server. Pair from Glowtbook "
                                 "(Objects → Fingerprint → Pair this phone) instead. Admin: "
                                 + why_not_configured() + " — details at /api/auth/providers.")
    name, cfg = p
    meta = _metadata(cfg["server_metadata_url"])
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    nonce = secrets.token_urlsafe(16)
    state = sessions.issue("oauth", ttl=STATE_TTL, kind="state",
                           extra={"next": _safe_next(next), "v": verifier, "nonce": nonce, "p": name})
    sh = _state_hash(state)
    _pending_put(sh, state)
    params = {"response_type": "code", "client_id": cfg["client_id"],
              "redirect_uri": redirect_uri(request), "scope": "openid email profile",
              "state": sh, "code_challenge": challenge, "code_challenge_method": "S256", "nonce": nonce}
    resp = RedirectResponse(meta["authorization_endpoint"] + "?" + urlencode(params))
    resp.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, path="/", httponly=True,
                    samesite="lax", secure=_secure(request))
    return resp


def _exchange(cfg: dict, meta: dict, code: str, verifier: str, redirect: str) -> dict:
    """Code -> tokens -> userinfo at the provider. Raises HTTPException with a plain message."""
    import httpx
    with httpx.Client(timeout=15) as c:
        tok = c.post(meta["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code_verifier": verifier})
        if tok.status_code != 200:
            raise HTTPException(400, f"Token exchange failed: {tok.text[:200]}")
        access = tok.json().get("access_token")
        ui = c.get(meta["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"})
        if ui.status_code != 200:
            raise HTTPException(400, "Couldn't read your profile from the provider.")
        return ui.json()


def _hands_token_to_page(path: str) -> bool:
    """Static pages load site.js, which keeps a token in localStorage and sends it as a
    Bearer header, so a sign-in survives a browser that drops cookies. The Streamlit
    apps and the API docs do not read it, so they get the cookie alone."""
    p = (path or "/").split("?")[0].split("#")[0]
    return not p.startswith(("/glowtbook", "/explore", "/admin", "/api"))


@router.get("/callback", name="auth_callback", summary="Finish sign-in and set the session cookie")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return _fail_page(request, "Sign-in was cancelled", f"The provider said: {error}.")
    # 1. the cookie this browser was given when it left; 2. the server's copy of the same
    # token, found by the public state value, for a browser that dropped the cookie.
    raw_cookie = request.cookies.get(STATE_COOKIE, "")
    from_cookie = sessions.verify(raw_cookie, kinds=("state",)) if raw_cookie else None
    record = _pending_take(state)                     # single use, whichever path we take
    if from_cookie and _state_hash(raw_cookie) == state:
        st, how = from_cookie, "cookie"
    else:
        st = sessions.verify(record, kinds=("state",)) if record else None
        how = "record"
        if st:
            log.warning("sign-in finished from the server record (%s); ua=%s",
                        "no state cookie" if not raw_cookie else
                        ("cookie expired" if not from_cookie else "cookie for another attempt"),
                        request.headers.get("user-agent", "")[:120])
    if not st:
        why = ("no state cookie came back" if not raw_cookie else
               "the state cookie had expired" if not from_cookie else "the state cookie was for a different attempt")
        log.warning("sign-in could not finish: %s and no fresh record; ua=%s", why,
                    request.headers.get("user-agent", "")[:120])
        # the person may already be signed in (a refresh or back-button replay of the callback)
        if current_user(request):
            return RedirectResponse("/account.html", status_code=303)
        return _fail_page(request, "That sign-in didn't finish",
                          f"Your browser came back from the provider but {why}, so the sign-in could not be "
                          "trusted. This happens when a page reloads mid-way, in some in-app browsers, or after "
                          "twenty minutes. Try again; it usually works the second time.")
    p = provider()
    if not p or p[0] != st.get("p"):
        return _fail_page(request, "Sign-in provider changed", "The provider changed mid-flow. Try again.", status=503)
    _, cfg = p
    meta = _metadata(cfg["server_metadata_url"])
    info = _exchange(cfg, meta, code, st.get("v", ""), redirect_uri(request))
    uid = info.get("sub") or info.get("email")
    email = (info.get("email") or "").lower()
    name = info.get("name") or info.get("preferred_username") or email
    if not uid:
        raise HTTPException(400, "The provider returned no user id.")
    try:
        from central import users
        from central.dbconn import connect
        users.record_login(connect(), email, name)
    except Exception:  # noqa: BLE001
        pass
    nxt = st.get("next") or "/"
    token = sessions.issue(uid, email, name)
    # static pages also get the token in the URL fragment (never sent to the server, never
    # logged); site.js stores it and sends it as a Bearer header, so the capture app stays
    # signed in even when a browser drops the cookie.
    if _hands_token_to_page(nxt) and "#" not in nxt:
        nxt = f"{nxt}#session={quote(token, safe='')}"
    resp = RedirectResponse(nxt, status_code=303)
    set_session_cookie(resp, request, token)
    resp.delete_cookie(STATE_COOKIE, path="/")
    log.info("sign-in finished via %s for %s", how, email or uid)
    return resp


@router.get("/dev-login", summary="Local-only fake sign-in (GLASSDB_DEV_LOGIN=1)", include_in_schema=False)
def dev_login(request: Request, next: str = "/", name: str = "Dev Maker",  # noqa: A002
              email: str = "dev@example.com"):
    if os.environ.get("GLASSDB_DEV_LOGIN") != "1":
        raise HTTPException(404, "Not found")
    resp = RedirectResponse(_safe_next(next), status_code=303)
    set_session_cookie(resp, request, sessions.issue("dev:" + email, email, name))
    return resp


def _logout(request: Request):
    nxt = _safe_next(request.query_params.get("next"))
    resp = RedirectResponse(nxt, status_code=303) if request.method == "GET" else JSONResponse({"ok": True})
    resp.delete_cookie(sessions.COOKIE, path="/")
    return resp


@router.get("/logout", summary="Sign out (clears the cookie)")
def logout_get(request: Request):
    return _logout(request)


@router.post("/logout", summary="Sign out (clears the cookie)")
def logout_post(request: Request):
    return _logout(request)
