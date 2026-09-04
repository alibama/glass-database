"""
central.feedback
================
Site feedback. Unlike intake submissions, feedback is not published — it's stored
privately for the team and pinged to Discord (no approve/reject links; it's not a
directory entry). Admins read and resolve it in the console.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from central import notify


def ensure_feedback(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id TEXT PRIMARY KEY, message TEXT, email TEXT, page TEXT, name TEXT,
        created_at TEXT, resolved INTEGER DEFAULT 0)""")
    conn.commit()


def submit(conn, message: str, email: str = "", page: str = "", name: str = "") -> str:
    ensure_feedback(conn)
    now = datetime.now(timezone.utc).isoformat()
    fid = hashlib.sha1((message[:80] + now).encode()).hexdigest()[:16]
    conn.execute("INSERT INTO feedback (id,message,email,page,name,created_at) VALUES (?,?,?,?,?,?)",
                 (fid, message.strip(), email.strip(), page, name.strip(), now))
    conn.commit()
    try:
        from central import analytics
        analytics.log(conn, "feedback", "", "feedback")
    except Exception:
        pass
    notify.notify_message("💬 New site feedback",
                          {"From": name or email or "anonymous", "Page": page or "—",
                           "Message": message[:1000]})
    return fid


def open_items(conn) -> list[dict]:
    ensure_feedback(conn)
    rows = conn.execute("SELECT * FROM feedback ORDER BY resolved, created_at DESC").fetchall()
    return [dict(r) for r in rows]


def resolve(conn, fid: str, resolved: bool = True) -> None:
    ensure_feedback(conn)
    conn.execute("UPDATE feedback SET resolved=? WHERE id=?", (1 if resolved else 0, fid))
    conn.commit()
