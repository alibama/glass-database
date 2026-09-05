"""
central.snapshots
================
An undo safety net for admin batch operations. Before a bulk change we copy the
whole table to a timestamped `_snap_<tbl>_<ts>` table and record it; Restore swaps
the live table's contents back. Keeps the last few snapshots per table.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

KEEP = 8


def ensure_snap(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS _snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, snap_table TEXT,
        created_at TEXT, rows INTEGER, note TEXT)""")
    conn.commit()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def snapshot(conn, tbl: str, note: str = "") -> str:
    ensure_snap(conn)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    snap = f"_snap_{_safe(tbl)}_{ts}"
    conn.execute(f'DROP TABLE IF EXISTS "{snap}"')
    conn.execute(f'CREATE TABLE "{snap}" AS SELECT * FROM "{tbl}"')
    rows = conn.execute(f'SELECT COUNT(*) FROM "{snap}"').fetchone()[0]
    conn.execute("INSERT INTO _snapshots (tbl,snap_table,created_at,rows,note) VALUES (?,?,?,?,?)",
                 (tbl, snap, datetime.now(timezone.utc).isoformat(), rows, note))
    conn.commit()
    prune(conn, tbl)
    return snap


def list_snapshots(conn, tbl: str | None = None) -> list[dict]:
    ensure_snap(conn)
    if tbl:
        rows = conn.execute("SELECT * FROM _snapshots WHERE tbl=? ORDER BY id DESC", (tbl,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM _snapshots ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def restore(conn, snap_id: int) -> dict | None:
    ensure_snap(conn)
    m = conn.execute("SELECT * FROM _snapshots WHERE id=?", (snap_id,)).fetchone()
    if not m:
        return None
    m = dict(m)
    tbl, snap = m["tbl"], m["snap_table"]
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (snap,)).fetchone():
        return None
    conn.execute(f'DELETE FROM "{tbl}"')
    conn.execute(f'INSERT INTO "{tbl}" SELECT * FROM "{snap}"')
    try:
        conn.execute(f'UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM "{tbl}") WHERE tbl=?', (tbl,))
    except Exception:
        pass
    conn.commit()
    return {"tbl": tbl, "rows": m["rows"]}


def prune(conn, tbl: str, keep: int = KEEP) -> None:
    ensure_snap(conn)
    old = conn.execute("SELECT id, snap_table FROM _snapshots WHERE tbl=? ORDER BY id DESC",
                       (tbl,)).fetchall()[keep:]
    for row in old:
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{row[1]}"')
            conn.execute("DELETE FROM _snapshots WHERE id=?", (row[0],))
        except Exception:
            pass
    conn.commit()
