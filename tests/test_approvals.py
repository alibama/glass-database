"""Publication gate: nothing is served until approved; batch approval works."""
import hashlib
import importlib
import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from central import approvals
from central.dbconn import connect


def _add_pending_row(tbl="studios"):
    c = connect()
    rid = "pendtest-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8]
    now = datetime.now(timezone.utc).isoformat()
    c.execute(f'INSERT INTO "{tbl}" (_row_id,_source_file,_source_sheet,_imported_at,name,city,country) '
              f'VALUES (?,?,?,?,?,?,?)', (rid, "t", "t", now, "Pending Studio", "Nowhere", "USA"))
    c.commit()
    return rid


def test_pending_row_hidden_until_approved(demo_db):
    from api.main import app
    cl = TestClient(app)
    rid = _add_pending_row()
    ids = {r["_row_id"] for r in cl.get("/datasets/studios?limit=200").json()["rows"]}
    assert rid not in ids
    assert cl.get(f"/datasets/studios/{rid}").status_code == 404
    approvals.set_status(connect(), "studios", [rid], "approved")
    ids = {r["_row_id"] for r in cl.get("/datasets/studios?limit=200").json()["rows"]}
    assert rid in ids
    assert cl.get(f"/datasets/studios/{rid}").status_code == 200


def test_admin_key_sees_pending(demo_db):
    os.environ["GLASSDB_ADMIN_TOKEN"] = "secret"
    import api.main as m
    importlib.reload(m)
    try:
        cl = TestClient(m.app)
        rid = _add_pending_row()
        ids = {r["_row_id"] for r in cl.get(
            "/datasets/studios?limit=200", headers={"x-api-key": "secret"}).json()["rows"]}
        assert rid in ids
    finally:
        del os.environ["GLASSDB_ADMIN_TOKEN"]
        importlib.reload(m)


def test_batch_approve_all(demo_db):
    c = connect()
    _add_pending_row("studios")
    assert approvals.counts(c, "studios")["pending"] >= 1
    approvals.approve_all(c, "studios")
    after = approvals.counts(c, "studios")
    assert after["pending"] == 0 and after["approved"] == after["total"]
