"""
central.wikibase.jobs
=====================
Background runs the admin page starts and polls. State lives in the database (``wb_runs``,
``wb_log``), not in memory, because the API runs as two uvicorn workers and the worker that
answers a poll is not always the one running the job. One job runs at a time; a run whose
heartbeat is older than ``STALE`` seconds counts as dead (a restarted service) and no longer
blocks the next one.

    run_id = start(conn, "stage", fn, params, by_email)   # fn(conn, log, params) -> summary dict
    status(conn, run_id, after=<last log id>)              # what the page polls
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timezone

from central.wikibase import store

STALE = 180          # seconds without a heartbeat → the run is considered dead
KINDS = ("stage", "dedupe", "bootstrap", "dry-run", "push", "pull", "check")


class Busy(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age(ts: str | None) -> float:
    if not ts:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return 1e9


def running(conn) -> dict | None:
    """The run in progress, if its heartbeat is fresh; stale ones are marked ``lost``."""
    for r in store.rows(conn.execute("SELECT * FROM wb_runs WHERE status='running' ORDER BY id DESC")):
        if _age(r["heartbeat"]) <= STALE:
            return r
        conn.execute("UPDATE wb_runs SET status='lost', finished_at=? WHERE id=?", (_now(), r["id"]))
        conn.commit()
    return None


class Logger:
    """Writes lines to ``wb_log`` as they happen (a commit per line: the page polls)."""

    def __init__(self, conn, run_id: int, echo: bool = False):
        self.conn, self.run_id, self.n, self.echo = conn, run_id, 0, echo

    def __call__(self, line: str) -> None:
        self.n += 1
        ts = _now()
        if self.echo:
            import sys
            print(line, file=sys.stderr, flush=True)
        try:
            self.conn.execute("INSERT INTO wb_log (run_id, ts, line) VALUES (?,?,?)", (self.run_id, ts, str(line)[:4000]))
            self.conn.execute("UPDATE wb_runs SET heartbeat=? WHERE id=?", (ts, self.run_id))
            self.conn.commit()
        except Exception:  # noqa: BLE001 — a log line must never kill the run
            pass


def start(conn, kind: str, fn, params: dict | None = None, by_email: str = "", *, thread: bool = True) -> int:
    """Record the run and execute ``fn(conn, log, params)`` in a thread (or inline when
    ``thread=False``, for tests and the command line). Returns the run id."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    store.ensure(conn)
    busy = running(conn)
    if busy:
        raise Busy(f"a {busy['kind']} run (#{busy['id']}) is still going; wait for it to finish")
    cur = conn.execute("INSERT INTO wb_runs (kind,status,started_at,heartbeat,by_email,params_json) VALUES (?,?,?,?,?,?)",
                       (kind, "running", _now(), _now(), by_email, json.dumps(params or {})))
    conn.commit()
    run_id = int(cur.lastrowid)

    def body():
        c = store.open_db()           # the thread's own connection
        log = Logger(c, run_id, echo=not thread)
        try:
            summary = fn(c, log, dict(params or {})) or {}
            st = "failed" if summary.get("failed") else "done"
            c.execute("UPDATE wb_runs SET status=?, finished_at=?, summary_json=? WHERE id=?",
                      (st, _now(), json.dumps(summary, ensure_ascii=False, default=str), run_id))
            c.commit()
            log(f"finished: {st}")
        except Exception as e:  # noqa: BLE001
            log(f"error: {e}")
            log(traceback.format_exc()[-3000:])
            c.execute("UPDATE wb_runs SET status='failed', finished_at=?, summary_json=? WHERE id=?",
                      (_now(), json.dumps({"error": str(e)[:1000]}), run_id))
            c.commit()
        finally:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    if thread:
        t = threading.Thread(target=body, name=f"wikibase-{kind}-{run_id}", daemon=True)
        t.start()
    else:
        body()
    return run_id


def status(conn, run_id: int, after: int = 0) -> dict | None:
    r = store.run(conn, run_id)
    if not r:
        return None
    if r["status"] == "running" and _age(r["heartbeat"]) > STALE:
        conn.execute("UPDATE wb_runs SET status='lost', finished_at=? WHERE id=?", (_now(), run_id))
        conn.commit()
        r["status"] = "lost"
    r["lines"] = store.log_lines(conn, run_id, after)
    return r
