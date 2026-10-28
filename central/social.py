"""
central.social
==============
Promoting work and collections: connected accounts, public collection links and
a log of what was shared.

* ``social_accounts`` — one row per (user, provider). For Bluesky we keep the
  handle, the DID and the **app password encrypted at rest** (Fernet, key derived
  from ``GLASSDB_SOCIAL_KEY`` or, failing that, the session secret). An app
  password is revocable in Bluesky's settings and scoped away from account
  management, which is why it is acceptable to hold; never store the main
  password.
* ``collection_shares`` — a collector opts in to a public page of their
  collection; the share id is unguessable and can be withdrawn.
* ``share_log`` — what was posted where (no content, just kind, ref, url, time),
  so a person can see and we can count.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timezone

PROVIDERS = ("bluesky",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS social_accounts (
        uid TEXT NOT NULL, provider TEXT NOT NULL, handle TEXT NOT NULL,
        did TEXT DEFAULT '', secret_enc TEXT NOT NULL,
        created_at TEXT, last_used TEXT, last_error TEXT DEFAULT '',
        PRIMARY KEY (uid, provider))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS collection_shares (
        uid TEXT PRIMARY KEY, share_id TEXT UNIQUE NOT NULL, name TEXT DEFAULT '',
        created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS share_log (
        id INTEGER PRIMARY KEY, uid TEXT NOT NULL, provider TEXT NOT NULL,
        kind TEXT NOT NULL, ref TEXT NOT NULL, url TEXT DEFAULT '', created_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_share_log_uid ON share_log(uid, created_at)")
    conn.commit()


# --- encryption ---------------------------------------------------------------------
def _key() -> bytes:
    raw = os.environ.get("GLASSDB_SOCIAL_KEY", "").strip()
    if not raw:
        from central import sessions
        raw = sessions.secret()
    if not raw:
        raise RuntimeError("no secret to encrypt connected accounts with: set GLASSDB_SESSION_SECRET")
    return base64.urlsafe_b64encode(hashlib.sha256(("social:" + raw).encode("utf-8")).digest())


def encrypt(text: str) -> str:
    from cryptography.fernet import Fernet
    return Fernet(_key()).encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    from cryptography.fernet import Fernet
    return Fernet(_key()).decrypt(token.encode("ascii")).decode("utf-8")


# --- connected accounts -------------------------------------------------------------
def status(conn, uid: str) -> dict:
    """What is connected, without secrets."""
    ensure(conn)
    out = {p: {"connected": False} for p in PROVIDERS}
    for r in conn.execute("SELECT provider, handle, did, created_at, last_used, last_error "
                          "FROM social_accounts WHERE uid=?", (uid,)).fetchall():
        d = dict(r)
        out[d.pop("provider")] = {"connected": True, **d}
    return out


def connect_bluesky(conn, uid: str, handle: str, app_password: str, *, verify=None) -> dict:
    """Verify the handle + app password against Bluesky (``verify(handle, pw) -> did``),
    then store the password encrypted. Raises ValueError with a plain message."""
    ensure(conn)
    handle = (handle or "").strip().lstrip("@").lower()
    app_password = (app_password or "").strip()
    if not handle or "." not in handle:
        raise ValueError("Enter your Bluesky handle, like name.bsky.social")
    if not app_password:
        raise ValueError("Enter an app password (Bluesky → Settings → App Passwords)")
    if verify is None:
        from glowtbook import bluesky
        verify = bluesky.verify_login
    try:
        did = verify(handle, app_password) or ""
    except Exception as ex:  # noqa: BLE001
        raise ValueError(f"Bluesky didn't accept that handle and app password ({ex})") from ex
    conn.execute("""INSERT INTO social_accounts (uid, provider, handle, did, secret_enc, created_at, last_used, last_error)
                    VALUES (?,?,?,?,?,?,NULL,'')
                    ON CONFLICT(uid, provider) DO UPDATE SET handle=excluded.handle, did=excluded.did,
                    secret_enc=excluded.secret_enc, created_at=excluded.created_at, last_error=''""",
                 (uid, "bluesky", handle, did, encrypt(app_password), _now()))
    conn.commit()
    return status(conn, uid)["bluesky"]


def disconnect(conn, uid: str, provider: str) -> None:
    ensure(conn)
    conn.execute("DELETE FROM social_accounts WHERE uid=? AND provider=?", (uid, provider))
    conn.commit()


def credentials(conn, uid: str, provider: str = "bluesky") -> tuple[str, str] | None:
    ensure(conn)
    r = conn.execute("SELECT handle, secret_enc FROM social_accounts WHERE uid=? AND provider=?",
                     (uid, provider)).fetchone()
    if not r:
        return None
    return r["handle"], decrypt(r["secret_enc"])


def mark_used(conn, uid: str, provider: str, error: str = "") -> None:
    conn.execute("UPDATE social_accounts SET last_used=?, last_error=? WHERE uid=? AND provider=?",
                 (_now(), error[:300], uid, provider))
    conn.commit()


def log_share(conn, uid: str, provider: str, kind: str, ref: str, url: str = "") -> None:
    ensure(conn)
    conn.execute("INSERT INTO share_log (uid, provider, kind, ref, url, created_at) VALUES (?,?,?,?,?,?)",
                 (uid, provider, kind, str(ref), url, _now()))
    conn.commit()


def shares_for(conn, uid: str, limit: int = 20) -> list[dict]:
    ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT provider, kind, ref, url, created_at FROM share_log WHERE uid=? ORDER BY id DESC LIMIT ?",
        (uid, int(limit))).fetchall()]


# --- public collections -------------------------------------------------------------
def share_collection(conn, uid: str, name: str = "") -> dict:
    """Opt in: a public, unguessable link to this person's collection (idempotent)."""
    ensure(conn)
    r = conn.execute("SELECT * FROM collection_shares WHERE uid=?", (uid,)).fetchone()
    if r:
        if name and name != r["name"]:
            conn.execute("UPDATE collection_shares SET name=? WHERE uid=?", (name[:80], uid)); conn.commit()
            r = conn.execute("SELECT * FROM collection_shares WHERE uid=?", (uid,)).fetchone()
        return dict(r)
    sid = secrets.token_urlsafe(8)
    conn.execute("INSERT INTO collection_shares (uid, share_id, name, created_at) VALUES (?,?,?,?)",
                 (uid, sid, (name or "")[:80], _now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM collection_shares WHERE uid=?", (uid,)).fetchone())


def unshare_collection(conn, uid: str) -> None:
    ensure(conn)
    conn.execute("DELETE FROM collection_shares WHERE uid=?", (uid,))
    conn.commit()


def my_collection_share(conn, uid: str) -> dict | None:
    ensure(conn)
    r = conn.execute("SELECT * FROM collection_shares WHERE uid=?", (uid,)).fetchone()
    return dict(r) if r else None


def public_collection(conn, share_id: str) -> dict | None:
    """The public shape of a shared collection: a display name and the pieces."""
    ensure(conn)
    r = conn.execute("SELECT * FROM collection_shares WHERE share_id=?", (share_id,)).fetchone()
    if not r:
        return None
    from central import seasons
    pieces = seasons.collection(conn, r["uid"])
    name = r["name"] or (pieces[0].get("collector_name") if pieces else "") or "A collector"
    points = sum(int(p.get("points") or 0) for p in pieces)
    return {"share_id": share_id, "name": name, "count": len(pieces), "points": points,
            "pieces": [{k: p.get(k) for k in ("object_row_id", "title", "maker", "artist_name", "studio",
                                                "season_title", "season_slug", "rarity", "points",
                                                "image_url", "confidence", "created_at")} for p in pieces]}
