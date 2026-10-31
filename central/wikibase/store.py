"""
central.wikibase.store
======================
The local tables behind the migration, in the central database (``central.dbconn``):

  wb_items   one row per would-be wiki item: its JSON (``model.Item``), where it came from,
             its status, its cluster, the Wikidata id OpenRefine found, the wiki id once
             pushed, and the content hash that was pushed (what ``--resume`` compares)
  wb_props   our property labels → P ids on the wiki (and the datatype)
  wb_runs    every job (stage, dedupe, bootstrap, dry run, push, pull), with a summary
  wb_log     the lines a run wrote, which the page polls
  wb_cache   the wiki's copy of what we pushed (entity JSON), for the rebuild's cache

Settings (``central.settings``): the wiki URL, the bot user, the bot password (encrypted
with the site secret, like connected social accounts), whether to send ``bot=1``, the
pause between writes, the public base URL used in references.

Statuses of a staged item:
  new        staged, never pushed, nobody looked at it
  ready      an admin (or the CSV import) said it can go
  held       something is wrong (the log says what); never pushed until fixed
  skipped    an admin said leave it out
  duplicate  the same thing as another item in its cluster (``canonical`` names it)
  pushed     on the wiki, and the wiki matches the current source
  changed    on the wiki, but the source row changed since (the next push updates it)
  failed     the last push failed on this item (``error`` says why); retried next time
  orphan     was pushed, and its source row is gone (never deleted on the wiki)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from central import settings as _settings
from central.dbconn import connect

PUSHABLE = ("new", "ready", "changed", "failed")

DDL = """
CREATE TABLE IF NOT EXISTS wb_items (
    key TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT DEFAULT '', description TEXT DEFAULT '',
    item_json TEXT NOT NULL, source_table TEXT DEFAULT '', source_row TEXT DEFAULT '',
    source_hash TEXT DEFAULT '', content_hash TEXT DEFAULT '', status TEXT DEFAULT 'new',
    cluster TEXT DEFAULT '', canonical TEXT DEFAULT '', wikidata_qid TEXT DEFAULT '',
    qid TEXT DEFAULT '', pushed_hash TEXT DEFAULT '', pushed_at TEXT DEFAULT '',
    error TEXT DEFAULT '', notes TEXT DEFAULT '', review_json TEXT DEFAULT '', staged_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_wb_items_kind ON wb_items(kind, status);
CREATE INDEX IF NOT EXISTS ix_wb_items_cluster ON wb_items(cluster);
CREATE INDEX IF NOT EXISTS ix_wb_items_qid ON wb_items(qid);
CREATE TABLE IF NOT EXISTS wb_props (label TEXT PRIMARY KEY, pid TEXT, datatype TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS wb_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, status TEXT DEFAULT 'running',
    started_at TEXT, finished_at TEXT, heartbeat TEXT, by_email TEXT DEFAULT '',
    params_json TEXT DEFAULT '{}', summary_json TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS wb_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, ts TEXT, line TEXT);
CREATE INDEX IF NOT EXISTS ix_wb_log_run ON wb_log(run_id, id);
CREATE TABLE IF NOT EXISTS wb_cache (
    qid TEXT PRIMARY KEY, key TEXT, kind TEXT, label TEXT, json TEXT, fetched_at TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(conn) -> None:
    conn.executescript(DDL)
    conn.commit()


def open_db():
    c = connect()
    ensure(c)
    return c


def rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(cur) -> dict | None:
    r = rows(cur)
    return r[0] if r else None


# --------------------------------------------------------------------------- settings
KEYS = ("wikibase_url", "wikibase_user", "wikibase_password_enc", "wikibase_bot", "wikibase_pause",
        "wikibase_public_base")


def get_settings(conn) -> dict:
    """What the admin page may see: no password, only whether one is set."""
    import os

    from central.wikibase.model import PUBLIC_BASE_DEFAULT, SITE_DEFAULT
    return {
        "url": _settings.get(conn, "wikibase_url", SITE_DEFAULT) or SITE_DEFAULT,
        "user": _settings.get(conn, "wikibase_user", ""),
        "password_set": bool(_settings.get(conn, "wikibase_password_enc", "")),
        "bot": _settings.get(conn, "wikibase_bot", "0") == "1",
        "pause": float(_settings.get(conn, "wikibase_pause", "1.0") or 1.0),
        "public_base": _settings.get(conn, "wikibase_public_base", "")
        or os.environ.get("PUBLIC_BASE_URL", "").rstrip("/") or PUBLIC_BASE_DEFAULT,
    }


def set_settings(conn, *, url: str | None = None, user: str | None = None, password: str | None = None,
                 bot: bool | None = None, pause: float | None = None, public_base: str | None = None) -> dict:
    if url is not None:
        _settings.set(conn, "wikibase_url", url.strip().rstrip("/"))
    if user is not None:
        _settings.set(conn, "wikibase_user", user.strip())
    if password is not None:
        if password == "":
            _settings.set(conn, "wikibase_password_enc", "")
        else:
            from central import social
            _settings.set(conn, "wikibase_password_enc", social.encrypt(password))
    if bot is not None:
        _settings.set(conn, "wikibase_bot", "1" if bot else "0")
    if pause is not None:
        _settings.set(conn, "wikibase_pause", str(max(0.0, float(pause))))
    if public_base is not None:
        _settings.set(conn, "wikibase_public_base", public_base.strip().rstrip("/"))
    return get_settings(conn)


def credentials(conn) -> tuple[str, str]:
    """(user, password) for the bot, decrypted; ('', '') when not set."""
    user = _settings.get(conn, "wikibase_user", "")
    enc = _settings.get(conn, "wikibase_password_enc", "")
    if not (user and enc):
        return user, ""
    try:
        from central import social
        return user, social.decrypt(enc)
    except Exception:  # noqa: BLE001 — the secret changed since it was saved
        return user, ""


# --------------------------------------------------------------------------- items
def upsert_item(conn, it, *, source_hash: str, status: str | None = None, notes: str = "") -> str:
    """Write a staged item. Keeps what the migration already knows about the key (status,
    cluster, qid, pushed hash, Wikidata id) and decides the status from the content:
    pushed items whose content changed become ``changed``; new keys are ``new``."""
    from central.wikibase.model import Item
    assert isinstance(it, Item)
    old = one(conn.execute("SELECT * FROM wb_items WHERE key=?", (it.key,)))
    shown = it                                  # what the label/description columns show
    if old:
        try:
            review = json.loads(old.get("review_json") or "{}")
        except ValueError:
            review = {}
        shown = apply_review(Item.from_json(it.to_json()), review, old.get("wikidata_qid") or "")
    content = shown.content_hash()
    ts = now()
    if old is None:
        st = status or "new"
        conn.execute("""INSERT INTO wb_items (key,kind,label,description,item_json,source_table,source_row,source_hash,
                        content_hash,status,notes,staged_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (it.key, it.kind, it.label, it.description, json.dumps(it.to_json(), ensure_ascii=False),
                      it.source_table, it.source_row, source_hash, content, st, notes, ts, ts))
        return st
    st = old["status"]
    if status:
        st = status
    elif st in ("pushed", "changed"):
        st = "pushed" if old["pushed_hash"] == content else "changed"
    elif st == "orphan":
        st = "changed" if old["qid"] else "new"
    elif st == "failed" and old["content_hash"] != content:
        st = "ready" if old["qid"] else "new"
    # a reviewer's edits to label/description/aliases (review_json) are re-applied by stage
    conn.execute("""UPDATE wb_items SET kind=?, label=?, description=?, item_json=?, source_table=?, source_row=?,
                    source_hash=?, content_hash=?, status=?, notes=?, staged_at=?, updated_at=? WHERE key=?""",
                 (it.kind, shown.label, shown.description, json.dumps(it.to_json(), ensure_ascii=False), it.source_table,
                  it.source_row, source_hash, content, st, notes, ts, ts, it.key))
    return st


def load_item(row: dict):
    from central.wikibase.model import Item
    return Item.from_json(json.loads(row["item_json"]))


def apply_review(it, review: dict | None, wikidata_qid: str = ""):
    """A reviewer's edits (the page, the CSV import) on top of what staging built: label,
    description, aliases, the Wikidata id, claims to add, properties to drop. Returns ``it``."""
    from central.wikibase.model import WIKIDATA_PROP, Claim, ext
    review = review or {}
    if review.get("label"):
        it.label = review["label"]
    if review.get("description"):
        it.description = review["description"]
    if review.get("aliases") is not None:
        it.aliases = [a for a in review["aliases"] if a and a != it.label]
    for prop in review.get("drop") or []:
        it.claims = [c for c in it.claims if c.prop != prop]
    for c in review.get("add") or []:
        try:
            it.add(c["p"], Claim.from_json(c).value, dedupe=True)
        except (KeyError, TypeError):
            pass
    qid = (wikidata_qid or review.get("wikidata") or it.wikidata or "").strip()
    if qid:
        it.wikidata = qid
        it.claims = [c for c in it.claims if c.prop != WIKIDATA_PROP]
        it.add(WIKIDATA_PROP, ext(qid))
    return it


def effective_item(row: dict):
    """The item as it would be pushed: staging's build plus the reviewer's edits."""
    try:
        review = json.loads(row.get("review_json") or "{}")
    except ValueError:
        review = {}
    return apply_review(load_item(row), review, row.get("wikidata_qid") or "")


def items(conn, *, kind: str | None = None, status=None, keys=None, limit: int | None = None,
          offset: int = 0, q: str = "") -> list[dict]:
    where, args = [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if status:
        sts = [status] if isinstance(status, str) else list(status)
        where.append(f"status IN ({','.join('?' * len(sts))})"); args += sts
    if keys:
        ks = list(keys)
        where.append(f"key IN ({','.join('?' * len(ks))})"); args += ks
    if q:
        where.append("(label LIKE ? OR key LIKE ? OR description LIKE ?)"); args += [f"%{q}%"] * 3
    sql = "SELECT * FROM wb_items" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY kind, label, key"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return rows(conn.execute(sql, args))


def counts(conn) -> dict:
    """Per kind, per status; plus totals the page shows."""
    out: dict = {"kinds": {}, "status": {}, "total": 0}
    for r in rows(conn.execute("SELECT kind, status, COUNT(*) n FROM wb_items GROUP BY kind, status")):
        out["kinds"].setdefault(r["kind"], {})[r["status"]] = r["n"]
        out["status"][r["status"]] = out["status"].get(r["status"], 0) + r["n"]
        out["total"] += r["n"]
    out["live"] = out["status"].get("pushed", 0) + out["status"].get("changed", 0)
    out["pending"] = sum(out["status"].get(s, 0) for s in PUSHABLE)
    out["properties"] = conn.execute("SELECT COUNT(*) FROM wb_props WHERE pid<>''").fetchone()[0]
    return out


def set_item(conn, key: str, **fields) -> dict | None:
    allowed = {"status", "cluster", "canonical", "wikidata_qid", "qid", "pushed_hash", "pushed_at", "error", "notes",
               "item_json", "label", "description", "content_hash", "review_json"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    sets["updated_at"] = now()
    conn.execute(f"UPDATE wb_items SET {', '.join(k + '=?' for k in sets)} WHERE key=?", (*sets.values(), key))
    conn.commit()
    return one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))


def props(conn) -> dict[str, dict]:
    return {r["label"]: r for r in rows(conn.execute("SELECT * FROM wb_props"))}


def remember_prop(conn, label: str, pid: str, datatype: str) -> None:
    conn.execute("INSERT INTO wb_props (label,pid,datatype,updated_at) VALUES (?,?,?,?) ON CONFLICT(label) DO UPDATE "
                 "SET pid=excluded.pid, datatype=excluded.datatype, updated_at=excluded.updated_at",
                 (label, pid, datatype, now()))
    conn.commit()


# --------------------------------------------------------------------------- runs
def runs(conn, limit: int = 20) -> list[dict]:
    out = rows(conn.execute("SELECT * FROM wb_runs ORDER BY id DESC LIMIT ?", (int(limit),)))
    for r in out:
        try:
            r["params"] = json.loads(r.pop("params_json") or "{}")
        except ValueError:
            r["params"] = {}
        try:
            r["summary"] = json.loads(r.pop("summary_json") or "{}")
        except ValueError:
            r["summary"] = {}
    return out


def run(conn, run_id: int) -> dict | None:
    r = one(conn.execute("SELECT * FROM wb_runs WHERE id=?", (int(run_id),)))
    if not r:
        return None
    for k in ("params", "summary"):
        try:
            r[k] = json.loads(r.pop(k + "_json") or "{}")
        except ValueError:
            r[k] = {}
    return r


def log_lines(conn, run_id: int, after: int = 0, limit: int = 2000) -> list[dict]:
    return rows(conn.execute("SELECT id, ts, line FROM wb_log WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                             (int(run_id), int(after), int(limit))))
