"""
central.wikibase.jobs
=====================
Background runs the admin page starts and polls. State lives in the database (``wb_runs``,
``wb_log``), not in memory, because the API runs as two uvicorn workers and the worker that
answers a poll is not always the one running the job.

One job runs at a time. A run proves it is alive with a **heartbeat**: every log line and every
call to ``beat()`` (the pusher beats once per item, the wiki client once per request) refreshes
it. A run counts as dead when the heartbeat is older than ``STALE`` seconds, or when it is older
than this process (the service restarted under it) and nothing has beaten for ``GRACE`` seconds;
such a run is marked ``lost`` and no longer blocks the next one. A run that is still executing
when it finds itself marked ``lost`` or asked to stop (``stop()``, the Stop button) raises
``Cancelled`` at its next beat and ends as ``stopped``: two runs never write at the same time.

    run_id = start(conn, "stage", fn, params, by_email)   # fn(conn, log, params) -> summary dict
    status(conn, run_id, after=<last log id>)              # what the page polls
    stop(conn, run_id)                                     # ask a running job to stop
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone

from central.wikibase import store

STALE = 600          # seconds without a heartbeat → the run is considered dead
GRACE = 30           # a run from before this process booted gets this long to beat again
BEAT_EVERY = 3       # seconds between heartbeat writes (beats in between are free)
KINDS = ("stage", "dedupe", "wikidata", "bootstrap", "dry-run", "push", "pull", "check")
LIVE = ("running", "stopping")


class Busy(RuntimeError):
    pass


class Cancelled(Exception):
    """Raised inside a run (from ``beat``) when it was asked to stop or declared lost."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


BOOT = _now()        # when this process started: runs older than this with no fresh beat are dead


def _age(ts: str | None) -> float:
    if not ts:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return 1e9


def dead(r: dict) -> bool:
    """A live-looking row whose process is gone: no beat for ``STALE`` seconds, or no beat since
    this process booted after a grace period (a service restart kills the threads, not the rows)."""
    hb = r.get("heartbeat") or ""
    age = _age(hb)
    return age > STALE or (hb < BOOT and age > GRACE)


def _mark_lost(conn, run_id: int) -> None:
    conn.execute("UPDATE wb_runs SET status='lost', finished_at=? WHERE id=? AND status IN ('running','stopping')",
                 (_now(), run_id))
    conn.commit()


def running(conn) -> dict | None:
    """The run in progress, if its heartbeat is fresh; dead ones are marked ``lost``."""
    for r in store.rows(conn.execute("SELECT * FROM wb_runs WHERE status IN ('running','stopping') ORDER BY id DESC")):
        if not dead(r):
            return r
        _mark_lost(conn, r["id"])
    return None


class Logger:
    """Writes lines to ``wb_log`` as they happen (a commit per line: the page polls) and keeps the
    run's heartbeat fresh. ``beat()`` is the cheap call for loops that do not log every step."""

    def __init__(self, conn, run_id: int, echo: bool = False):
        self.conn, self.run_id, self.n, self.echo = conn, run_id, 0, echo
        self._last = 0.0

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
            self._last = time.monotonic()
        except Exception:  # noqa: BLE001 — a log line must never kill the run
            pass

    def beat(self, force: bool = False) -> None:
        """Refresh the heartbeat (at most every ``BEAT_EVERY`` seconds) and stop the run if it was
        asked to, or if it was declared lost while it was still going."""
        now = time.monotonic()
        if not force and now - self._last < BEAT_EVERY:
            return
        self._last = now
        try:
            self.conn.execute("UPDATE wb_runs SET heartbeat=? WHERE id=? AND status='running'", (_now(), self.run_id))
            self.conn.commit()
            row = self.conn.execute("SELECT status FROM wb_runs WHERE id=?", (self.run_id,)).fetchone()
        except Exception:  # noqa: BLE001 — a hiccup on the heartbeat is not a reason to stop
            return
        st = row[0] if row else "running"
        if st == "stopping":
            raise Cancelled("stopped from the page")
        if st != "running":
            raise Cancelled(f"the run was marked {st} while it was still going")


def beat(log) -> None:
    """Heartbeat through whatever ``log`` a job was given (a plain ``print`` has none)."""
    b = getattr(log, "beat", None)
    if b:
        b()


def start(conn, kind: str, fn, params: dict | None = None, by_email: str = "", *, thread: bool = True) -> int:
    """Record the run and execute ``fn(conn, log, params)`` in a thread (or inline when
    ``thread=False``, for tests and the command line). Returns the run id."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    store.ensure(conn)
    # one gate for both workers: take the write lock before looking, so two clicks a moment
    # apart cannot both find the table free
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        pass
    busy = running(conn)
    if busy:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise Busy(f"a {busy['kind']} run (#{busy['id']}) is still going; wait for it, or stop it from the Runs section")
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
        except Cancelled as e:
            c.execute("UPDATE wb_runs SET status='stopped', finished_at=?, summary_json=? WHERE id=?",
                      (_now(), json.dumps({"stopped": str(e)}), run_id))
            c.commit()
            log(f"stopped: {e}. Running the step again continues where this left off.")
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


def stop(conn, run_id: int) -> dict | None:
    """Ask a running job to stop: it ends at its next heartbeat (after the item it is on). A run
    whose process is gone is marked ``lost`` instead."""
    r = store.run(conn, run_id)
    if not r:
        return None
    if r["status"] in LIVE:
        if dead(r):
            _mark_lost(conn, run_id)
            r["status"] = "lost"
        elif r["status"] == "running":
            conn.execute("UPDATE wb_runs SET status='stopping' WHERE id=? AND status='running'", (run_id,))
            conn.commit()
            r["status"] = "stopping"
    return r


def status(conn, run_id: int, after: int = 0) -> dict | None:
    r = store.run(conn, run_id)
    if not r:
        return None
    if r["status"] in LIVE and dead(r):
        _mark_lost(conn, run_id)
        r["status"] = "lost"
    r["lines"] = store.log_lines(conn, run_id, after)
    return r
