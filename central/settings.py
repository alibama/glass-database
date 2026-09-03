"""Small key/value settings store (admin-editable runtime config, e.g. the
Discord webhook) kept in the database so it survives without touching .env."""
from __future__ import annotations


def ensure_settings(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS _settings "
                 "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()


def get(conn, key: str, default: str = "") -> str:
    ensure_settings(conn)
    r = conn.execute("SELECT value FROM _settings WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def set(conn, key: str, value: str) -> None:  # noqa: A001
    ensure_settings(conn)
    conn.execute("INSERT OR REPLACE INTO _settings (key,value,updated_at) "
                 "VALUES (?,?,datetime('now'))", (key, value))
    conn.commit()
