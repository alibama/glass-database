"""
central.approvals
=================
A single publication gate for the whole registry. Nothing is served publicly
until it's been approved — enforced by a central `_approvals` table rather than
a column on every dataset, so:

  * the default is deny (a row with no entry is *not* published),
  * enabling the gate touches no existing table (safe on a live DB), and
  * batch approval — a whole dataset at once — is one SQL statement.

Public reads (API, explorer) filter each dataset to its approved rows. The admin
console writes approvals (single, selected, or whole-dataset).
"""
from __future__ import annotations

from datetime import datetime, timezone


def ensure_approvals(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS _approvals (
        tbl TEXT NOT NULL, row_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        reviewed_at TEXT, reviewer TEXT,
        PRIMARY KEY (tbl, row_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_lookup ON _approvals(tbl, status)")
    conn.commit()


def approved_subquery() -> str:
    """A WHERE-clause fragment; bind the table name as the single parameter.
    Use as:  f'... WHERE {approved_subquery()}'  with params [table]."""
    return "_row_id IN (SELECT row_id FROM _approvals WHERE tbl = ? AND status = 'approved')"


def set_status(conn, tbl: str, row_ids, status: str, reviewer: str = "admin") -> int:
    now = datetime.now(timezone.utc).isoformat()
    rows = [(tbl, rid, status, now, reviewer) for rid in row_ids]
    conn.executemany("""INSERT OR REPLACE INTO _approvals (tbl, row_id, status, reviewed_at, reviewer)
                        VALUES (?,?,?,?,?)""", rows)
    conn.commit()
    return len(rows)


def approve_all(conn, tbl: str, reviewer: str = "admin", only_pending: bool = True) -> int:
    """Approve every row of a dataset in one statement. Table name is quoted (it's
    validated against the registry by callers), not parameter-bound (SQL can't
    bind an identifier)."""
    now = datetime.now(timezone.utc).isoformat()
    where = ""
    if only_pending:
        where = ("WHERE _row_id NOT IN "
                 "(SELECT row_id FROM _approvals WHERE tbl = ? AND status = 'approved')")
    params = ([tbl, now, reviewer] + ([tbl] if only_pending else []))
    cur = conn.execute(
        f'''INSERT OR REPLACE INTO _approvals (tbl, row_id, status, reviewed_at, reviewer)
            SELECT ?, _row_id, 'approved', ?, ? FROM "{tbl}" {where}''', params)
    conn.commit()
    return cur.rowcount


def reject_all_pending(conn, tbl: str, reviewer: str = "admin") -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        f'''INSERT OR REPLACE INTO _approvals (tbl, row_id, status, reviewed_at, reviewer)
            SELECT ?, _row_id, 'rejected', ?, ? FROM "{tbl}"
            WHERE _row_id NOT IN (SELECT row_id FROM _approvals WHERE tbl = ? AND status IN ('approved','rejected'))''',
        [tbl, now, reviewer, tbl])
    conn.commit()
    return cur.rowcount


def counts(conn, tbl: str) -> dict:
    total = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
    appr = conn.execute("SELECT COUNT(*) FROM _approvals WHERE tbl=? AND status='approved'",
                        (tbl,)).fetchone()[0]
    rej = conn.execute("SELECT COUNT(*) FROM _approvals WHERE tbl=? AND status='rejected'",
                       (tbl,)).fetchone()[0]
    return {"total": total, "approved": appr, "rejected": rej,
            "pending": max(total - appr - rej, 0)}


def pending_rows(conn, tbl: str, cols: list[str], limit: int = 50):
    """Return up to `limit` rows of a dataset that are neither approved nor rejected."""
    collist = ", ".join(f'"{c}"' for c in ["_row_id", *cols])
    return conn.execute(
        f'''SELECT {collist} FROM "{tbl}"
            WHERE _row_id NOT IN (SELECT row_id FROM _approvals WHERE tbl=? AND status IN ('approved','rejected'))
            LIMIT {int(limit)}''', (tbl,)).fetchall()
