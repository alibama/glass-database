"""
central.users
=============
A light login ledger + admin-role store, keyed by Google email.

- record_login(): upsert name/email on each real (non-demo) login — first seen,
  last seen, count. Just the name and email; nothing else.
- is_admin(): true if the email is in the bootstrap list (GLASSDB_ADMIN_EMAILS,
  comma-separated — always admin, avoids lock-out) or flagged in the table.
- set_admin(): promote/demote a user (the mechanism the admin console uses).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def ensure_users(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY, name TEXT, first_seen TEXT, last_seen TEXT,
        logins INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0)""")
    conn.commit()


def _boot() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("GLASSDB_ADMIN_EMAILS", "").split(",") if e.strip()}


def record_login(conn, email: str, name: str = "") -> None:
    if not email:
        return
    ensure_users(conn)
    email = email.strip().lower()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT email FROM users WHERE email=?", (email,)).fetchone()
    if row:
        conn.execute("UPDATE users SET name=COALESCE(NULLIF(?,''),name), last_seen=?, "
                     "logins=logins+1 WHERE email=?", (name, now, email))
    else:
        conn.execute("INSERT INTO users (email,name,first_seen,last_seen,logins,is_admin) "
                     "VALUES (?,?,?,?,1,?)", (email, name, now, now, 1 if email in _boot() else 0))
    conn.commit()


def is_admin(conn, email: str) -> bool:
    if not email:
        return False
    email = email.strip().lower()
    if email in _boot():
        return True
    try:
        ensure_users(conn)
        r = conn.execute("SELECT is_admin FROM users WHERE email=?", (email,)).fetchone()
        return bool(r and r[0])
    except Exception:
        return False


def set_admin(conn, email: str, admin: bool = True) -> None:
    ensure_users(conn)
    email = email.strip().lower()
    # ensure the row exists even if they haven't logged in since the table was made
    if not conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO users (email,name,first_seen,last_seen,logins,is_admin) "
                     "VALUES (?,?,?,?,0,?)", (email, "", now, now, 1 if admin else 0))
    else:
        conn.execute("UPDATE users SET is_admin=? WHERE email=?", (1 if admin else 0, email))
    conn.commit()


def list_users(conn) -> list[dict]:
    ensure_users(conn)
    rows = conn.execute("SELECT email,name,first_seen,last_seen,logins,is_admin "
                        "FROM users ORDER BY last_seen DESC").fetchall()
    boot = _boot()
    out = []
    for r in rows:
        d = dict(r)
        d["via_config"] = d["email"] in boot
        out.append(d)
    return out
