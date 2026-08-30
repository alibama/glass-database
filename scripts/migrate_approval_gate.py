#!/usr/bin/env python3
"""
One-time migration for an existing database: turn on the publication gate.

It only creates the (empty) `_approvals` table. Because the gate is default-deny,
that immediately makes every existing row *pending* — nothing is served publicly
until you approve it in Admin → ✅ Approvals (where "Approve ALL pending content"
re-publishes everything at once, or you can approve per dataset).

No existing table is altered and no data is deleted.

Usage:
    python -m scripts.migrate_approval_gate
    GLASSDB_PATH=/opt/glassdatabase/data/glassdb.db python -m scripts.migrate_approval_gate
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals
from central.dbconn import connect, local_path

if __name__ == "__main__":
    c = connect()
    approvals.ensure_approvals(c)
    pub = c.execute("SELECT COUNT(*) FROM _datasets WHERE visibility='public'").fetchone()[0]
    print(f"Publication gate enabled on {local_path()}.")
    print(f"{pub} public dataset(s) are now pending until approved "
          "(Admin → ✅ Approvals → “Approve ALL pending content”).")
