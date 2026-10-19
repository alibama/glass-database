"""
central.sessions
================
Site-wide identity for the parts of the site that are *not* Streamlit — the
fingerprint capture apps, the Seasons page, the relationship graph. Streamlit's
``st.login()`` only exists inside a Streamlit app, so those plain pages talk to
the FastAPI service, and the API needs its own answer to "who is this?".

Two ways a browser gets signed in, both ending in the same token:

* **Google sign-in handled by the API** — ``/api/auth/login`` → provider →
  ``/api/auth/callback``. It reuses the very same ``[auth.google]`` client that
  Glowtbook uses (``.streamlit/secrets.toml``); only one extra redirect URI
  (``…/api/auth/callback``) has to be registered with Google.
* **Pairing from Glowtbook** — a signed-in Glowtbook user is handed a short-lived
  link / QR code that opens the capture app on their phone already signed in.
  No OAuth round-trip on the phone, no second login.

The token is ``base64url(payload) . base64url(hmac)``, payload =
``{"u": uid, "e": email, "n": name, "x": expires, "k": kind}``. It travels as the
``gdb_session`` cookie (set by the callback) or an ``Authorization: Bearer``
header (set by the pages from localStorage after pairing). Signing key:

    GLASSDB_SESSION_SECRET
      → .streamlit/secrets.toml  [auth] cookie_secret
      → GLASSDB_ADMIN_TOKEN
      → a random per-process key (dev only — logins won't survive a restart)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import time
from pathlib import Path
from urllib.parse import quote

COOKIE = "gdb_session"
DEFAULT_TTL = 30 * 24 * 3600        # a month for a real sign-in
PAIR_TTL = 2 * 3600                 # pairing links are short-lived
_EPHEMERAL: dict[str, str] = {}


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def read_streamlit_secrets() -> dict:
    """The same secrets.toml Streamlit reads (project .streamlit/ first, then ~/.streamlit/)."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        return {}
    root = Path(__file__).resolve().parent.parent
    for p in (root / ".streamlit" / "secrets.toml", Path.home() / ".streamlit" / "secrets.toml"):
        try:
            if p.exists():
                return tomllib.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return {}


def auth_config() -> dict:
    """The [auth] block of secrets.toml (provider sub-tables included), or {}."""
    cfg = read_streamlit_secrets().get("auth") or {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def secret() -> str:
    s = os.environ.get("GLASSDB_SESSION_SECRET")
    if s:
        return s
    s = auth_config().get("cookie_secret")
    if s:
        return str(s)
    s = os.environ.get("GLASSDB_ADMIN_TOKEN")
    if s and s != "change-me":
        return s
    if "k" not in _EPHEMERAL:
        _EPHEMERAL["k"] = _secrets.token_hex(32)
    return _EPHEMERAL["k"]


def _sign(payload_b64: str) -> str:
    return _b64e(hmac.new(secret().encode(), payload_b64.encode(), hashlib.sha256).digest())


def issue(uid: str, email: str = "", name: str = "", ttl: int = DEFAULT_TTL,
          kind: str = "session", extra: dict | None = None) -> str:
    """Mint a signed token for a user. `kind` distinguishes a full sign-in
    ('session') from a pairing hand-off ('pair') or an OAuth state ('state')."""
    if not uid:
        raise ValueError("uid required")
    payload = {"u": str(uid), "e": (email or "").strip().lower(), "n": name or "",
               "x": int(time.time()) + int(ttl), "k": kind}
    if extra:
        payload.update(extra)
    p = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{p}.{_sign(p)}"


def verify(token: str | None, kinds: tuple[str, ...] = ("session", "pair")) -> dict | None:
    """Return {'uid','email','name','exp','kind',...} for a valid, unexpired token
    of an accepted kind, else None. Never raises."""
    if not token or "." not in token:
        return None
    try:
        p, sig = token.strip().split(".", 1)
        if not hmac.compare_digest(_sign(p), sig):
            return None
        d = json.loads(_b64d(p).decode())
        if int(d.get("x", 0)) < time.time():
            return None
        if kinds and d.get("k") not in kinds:
            return None
        out = {"uid": d.get("u"), "email": d.get("e", ""), "name": d.get("n", ""),
               "exp": int(d.get("x", 0)), "kind": d.get("k", "session")}
        for k, v in d.items():
            if k not in ("u", "e", "n", "x", "k"):
                out[k] = v
        return out if out["uid"] else None
    except Exception:  # noqa: BLE001
        return None


def pairing_link(base_url: str, uid: str, email: str = "", name: str = "",
                 page: str = "/fingerprint/enroll.html", object_id: int | str | None = None,
                 ttl: int = PAIR_TTL) -> str:
    """A link that opens a static page already signed in as `uid` (token in the
    URL *fragment*, so it never reaches server logs). Pages store it locally and
    send it as a Bearer header."""
    tok = issue(uid, email, name, ttl=ttl, kind="pair")
    frag = f"token={quote(tok, safe='')}"
    if object_id is not None:
        frag += f"&object={quote(str(object_id), safe='')}"
    return f"{base_url.rstrip('/')}{page}#{frag}"


def from_request_headers(headers, cookies) -> dict | None:
    """Resolve a user from an HTTP request: Bearer header first, then the cookie."""
    auth = (headers.get("authorization") or headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        u = verify(auth[7:].strip())
        if u:
            return u
    return verify((cookies or {}).get(COOKIE))
