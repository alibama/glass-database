"""Snapshot / restore undo safety net."""
from central import snapshots as S
from central.dbconn import connect


def _mktable(c):
    c.execute("CREATE TABLE IF NOT EXISTS t_s (_row_id TEXT PRIMARY KEY, v TEXT)")
    c.execute("DELETE FROM t_s")
    for i in range(3):
        c.execute("INSERT INTO t_s VALUES (?,?)", (f"r{i}", f"v{i}"))
    c.commit()


def test_snapshot_and_restore(demo_db):
    c = connect(); _mktable(c)
    S.snapshot(c, "t_s", "before")
    c.execute("DELETE FROM t_s WHERE _row_id='r0'")
    c.execute("UPDATE t_s SET v='X'"); c.commit()
    assert c.execute("SELECT COUNT(*) FROM t_s").fetchone()[0] == 2
    sid = S.list_snapshots(c, "t_s")[0]["id"]
    r = S.restore(c, sid)
    assert r["rows"] == 3
    rows = dict(c.execute("SELECT _row_id,v FROM t_s").fetchall())
    assert rows["r0"] == "v0" and rows["r1"] == "v1"


def test_prune_keeps_recent(demo_db):
    c = connect(); _mktable(c)
    for i in range(12):
        S.snapshot(c, "t_s", f"snap {i}")
    assert len(S.list_snapshots(c, "t_s")) <= S.KEEP
