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
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import sessions  # noqa: E402

router = APIRouter(prefix="/auth", tags=["auth"])
STATE_COOKIE = "gdb_oauth"
_META_CACHE: dict[str, tuple[float, dict]] = {}


# --- resolving the caller ---------------------------------------------------
def current_user(request: Request) -> dict | None:
    return sessions.from_request_headers(request.headers, request.cookies)


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Sign in first (GET /api/auth/login), or open this page from "
                                 "Glowtbook so it's paired to your account.")
    return u


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


def _secure(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"


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
    state = sessions.issue("oauth", ttl=600, kind="state",
                           extra={"next": _safe_next(next), "v": verifier, "nonce": nonce, "p": name})
    params = {"response_type": "code", "client_id": cfg["client_id"],
              "redirect_uri": redirect_uri(request), "scope": "openid email profile",
              "state": hashlib.sha256(state.encode()).hexdigest()[:32],
              "code_challenge": challenge, "code_challenge_method": "S256", "nonce": nonce}
    resp = RedirectResponse(meta["authorization_endpoint"] + "?" + urlencode(params))
    resp.set_cookie(STATE_COOKIE, state, max_age=600, path="/", httponly=True,
                    samesite="lax", secure=_secure(request))
    return resp


@router.get("/callback", name="auth_callback", summary="Finish sign-in and set the session cookie")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(400, f"Sign-in was cancelled or failed: {error}")
    raw_state = request.cookies.get(STATE_COOKIE, "")
    st = sessions.verify(raw_state, kinds=("state",))
    if not st or hashlib.sha256(raw_state.encode()).hexdigest()[:32] != state:
        raise HTTPException(400, "Sign-in state didn't match (expired or tampered) — try again.")
    p = provider()
    if not p or p[0] != st.get("p"):
        raise HTTPException(503, "Sign-in provider changed mid-flow — try again.")
    _, cfg = p
    meta = _metadata(cfg["server_metadata_url"])
    import httpx
    with httpx.Client(timeout=15) as c:
        tok = c.post(meta["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(request),
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "code_verifier": st.get("v", "")})
        if tok.status_code != 200:
            raise HTTPException(400, f"Token exchange failed: {tok.text[:200]}")
        access = tok.json().get("access_token")
        ui = c.get(meta["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"})
        if ui.status_code != 200:
            raise HTTPException(400, "Couldn't read your profile from the provider.")
        info = ui.json()
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
    resp = RedirectResponse(st.get("next") or "/", status_code=303)
    set_session_cookie(resp, request, sessions.issue(uid, email, name))
    resp.delete_cookie(STATE_COOKIE, path="/")
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
