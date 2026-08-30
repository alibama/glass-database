"""
central.dbconn
==============
One connection factory used by the importer, the API and the admin, so all
three point at the same database with the same code.

Resolution order:
  1. If TURSO_DATABASE_URL is set AND the `libsql` package is importable,
     connect to Turso (the managed central DB).
  2. Otherwise open a local SQLite file (default: data/glassdb.db, override
     with GLASSDB_PATH). libSQL files are byte-compatible with SQLite, so the
     same file works locally and after `turso db create --from-file`.

Turso setup (once):
    curl -sSfL https://get.tur.so/install.sh | bash
    turso auth login
    turso db create glassdatabase --from-file data/glassdb.db   # WAL + checkpoint first
    turso db show   glassdatabase --url         # -> TURSO_DATABASE_URL
    turso db tokens create glassdatabase        # -> TURSO_AUTH_TOKEN
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_LOCAL = Path(__file__).resolve().parent.parent / "data" / "glassdb.db"


def local_path() -> Path:
    return Path(os.environ.get("GLASSDB_PATH", DEFAULT_LOCAL)).expanduser()


def using_turso() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL"))


def connect():
    """Return a DB-API connection (sqlite3.Row rows). Works for local + Turso."""
    if using_turso():
        try:
            import libsql  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "TURSO_DATABASE_URL is set but the 'libsql' package isn't "
                "installed. Run `pip install libsql`, or unset TURSO_DATABASE_URL "
                "to use the local file."
            ) from e
        conn = libsql.connect(
            database=os.environ["TURSO_DATABASE_URL"],
            auth_token=os.environ.get("TURSO_AUTH_TOKEN", ""),
        )
        return conn

    path = local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")   # required for `turso db import`
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def rows_as_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]
