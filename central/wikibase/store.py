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
  wb_wikidata  what the Wikidata lookup decided per staged item (known / matched / unsure /
             none, and a person's confirmed / rejected), with the candidates it saw

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
CREATE TABLE IF NOT EXISTS wb_wikidata (
    key TEXT PRIMARY KEY, kind TEXT DEFAULT '', label TEXT DEFAULT '', status TEXT DEFAULT 'none',
    qid TEXT DEFAULT '', why TEXT DEFAULT '', candidates_json TEXT DEFAULT '[]', facts_json TEXT DEFAULT '{}',
    checked_at TEXT, decided_by TEXT DEFAULT '', decided_at TEXT);
CREATE INDEX IF NOT EXISTS ix_wb_wikidata_status ON wb_wikidata(status, kind);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(conn) -> None:
    conn.executescript(DDL)
    # columns added after a table first shipped
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wb_wikidata)")}
    if "suggestion_json" not in cols:
        conn.execute("ALTER TABLE wb_wikidata ADD COLUMN suggestion_json TEXT DEFAULT ''")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wb_items)")}
    if "enrich_json" not in cols:                 # what an outside search found for a record (website, Instagram, city)
        conn.execute("ALTER TABLE wb_items ADD COLUMN enrich_json TEXT DEFAULT ''")
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
               "item_json", "label", "description", "content_hash", "review_json", "enrich_json"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    sets["updated_at"] = now()
    conn.execute(f"UPDATE wb_items SET {', '.join(k + '=?' for k in sets)} WHERE key=?", (*sets.values(), key))
    conn.commit()
    return one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))


def set_wikidata_qid(conn, key: str, qid: str) -> dict | None:
    """Give a staged item its Wikidata id (or take it away with ``""``) and keep the item's
    record true: the content hash, the shown label and description, and ``pushed`` ↔
    ``changed`` for an item that is on the wiki. The same rule the review route applies."""
    r = one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    if not r:
        return None
    qid = (qid or "").strip()
    eff = effective_item(dict(r, wikidata_qid=qid))
    fields = {"wikidata_qid": qid, "content_hash": eff.content_hash(), "label": eff.label, "description": eff.description}
    if r["status"] in ("pushed", "changed"):
        fields["status"] = "pushed" if eff.content_hash() == r["pushed_hash"] else "changed"
    return set_item(conn, key, **fields)


# --------------------------------------------------------------------------- the Wikidata lookup
def upsert_wikidata(conn, key: str, kind: str, label: str, status: str, qid: str = "", why: str = "", *,
                    candidates=None, facts=None, checked_at: str | None = None, decided_by: str | None = None,
                    decided_at: str | None = None) -> None:
    """One lookup row per staged item. A person's decision (``decided_by``) is kept when an
    automatic pass writes the row again."""
    conn.execute(
        "INSERT INTO wb_wikidata (key,kind,label,status,qid,why,candidates_json,facts_json,checked_at,decided_by,decided_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, label=excluded.label, "
        "status=excluded.status, qid=excluded.qid, why=excluded.why, candidates_json=excluded.candidates_json, "
        "facts_json=excluded.facts_json, checked_at=COALESCE(excluded.checked_at, wb_wikidata.checked_at), "
        "decided_by=COALESCE(excluded.decided_by, wb_wikidata.decided_by), "
        "decided_at=COALESCE(excluded.decided_at, wb_wikidata.decided_at)",
        (key, kind, label, status, qid or "", why or "", json.dumps(candidates or [], ensure_ascii=False),
         json.dumps(facts or {}, ensure_ascii=False), checked_at, decided_by, decided_at))


def wikidata_rows(conn, *, status=None, kind: str | None = None, keys=None, q: str = "", limit: int = 100,
                  offset: int = 0) -> tuple[list[dict], int]:
    """Lookup rows with the staged item's description and status beside them, and the total."""
    where, args = [], []
    if status:
        sts = [status] if isinstance(status, str) else list(status)
        where.append(f"w.status IN ({','.join('?' * len(sts))})"); args += sts
    if kind:
        where.append("w.kind=?"); args.append(kind)
    if keys:
        ks = list(keys)
        where.append(f"w.key IN ({','.join('?' * len(ks))})"); args += ks
    if q:
        where.append("(w.label LIKE ? OR w.key LIKE ?)"); args += [f"%{q}%"] * 2
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM wb_wikidata w{sql_where}", args).fetchone()[0]
    out = rows(conn.execute(
        "SELECT w.*, i.description AS item_description, i.status AS item_status, i.qid AS item_qid, i.wikidata_qid "
        f"FROM wb_wikidata w LEFT JOIN wb_items i ON i.key=w.key{sql_where} "
        f"ORDER BY w.kind, w.label, w.key LIMIT {int(limit)} OFFSET {int(offset)}", args))
    for r in out:
        for col, default in (("candidates_json", []), ("facts_json", {}), ("suggestion_json", {})):
            try:
                r[col[:-5]] = json.loads(r.pop(col, None) or "")
            except (ValueError, TypeError):
                r[col[:-5]] = default
    return out, total


ENRICH_FIELDS = ("website", "instagram", "city")


def set_enrich(conn, key: str, data: dict | None, by: str = "") -> dict | None:
    """What an outside search (lexflow's enrich step) proposes for a record: a website, an Instagram account, a
    city, each with a confidence, a reason and the pages it was read from. Kept on the staged item for a
    reviewer; nothing enters the item until a person adds it. A field already added keeps its mark when the
    same value is proposed again. Returns the stored proposal, or None when the key is not staged."""
    import re as _re
    r = one(conn.execute("SELECT enrich_json FROM wb_items WHERE key=?", (key,)))
    if r is None:
        return None
    try:
        old = json.loads(r.get("enrich_json") or "{}")
    except ValueError:
        old = {}
    d = data or {}
    fields: dict = {}
    for f in ENRICH_FIELDS:
        p = (d.get("fields") or {}).get(f) if isinstance(d.get("fields"), dict) else d.get(f)
        if not isinstance(p, dict):
            continue
        value = str(p.get("value") or "").strip()
        if f == "website":
            if not _re.fullmatch(r"https?://[^\s<>\"']{4,500}", value):
                continue
        elif f == "instagram":
            value = value.lstrip("@").rstrip("/").rsplit("/", 1)[-1]
            if not _re.fullmatch(r"[A-Za-z0-9_.]{2,30}", value):
                continue
        else:
            value = value[:120]
            if not value:
                continue
        try:
            conf = max(0.0, min(1.0, float(p.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        entry = {"value": value, "confidence": round(conf, 3), "because": str(p.get("because") or "")[:400],
                 "evidence": [str(u)[:300] for u in (p.get("evidence") or []) if isinstance(u, str)][:6]}
        if f == "city":
            for k in ("region", "country"):
                if p.get(k):
                    entry[k] = str(p[k])[:120]
        fields[f] = entry
    applied = {}
    for f, when in (old.get("applied") or {}).items():
        if f in fields and fields[f]["value"] == ((old.get("fields") or {}).get(f) or {}).get("value"):
            applied[f] = when
    keep = {"fields": fields, "model": str(d.get("model") or "")[:60], "searched": bool(d.get("searched")),
            "version": str(d.get("version") or "")[:40], "by": (by or "")[:120], "at": now(), "applied": applied}
    conn.execute("UPDATE wb_items SET enrich_json=?, updated_at=? WHERE key=?", (json.dumps(keep, ensure_ascii=False), now(), key))
    conn.commit()
    return keep


def mark_enrich_applied(conn, key: str, field: str) -> None:
    r = one(conn.execute("SELECT enrich_json FROM wb_items WHERE key=?", (key,)))
    if not r or not r.get("enrich_json"):
        return
    try:
        en = json.loads(r["enrich_json"])
    except ValueError:
        return
    en.setdefault("applied", {})[field] = now()
    conn.execute("UPDATE wb_items SET enrich_json=? WHERE key=?", (json.dumps(en, ensure_ascii=False), key))
    conn.commit()


def enrichments(conn, kind: str | None = None, limit: int = 200) -> list[dict]:
    """Staged items that carry a proposal, newest first, with what the item already has."""
    where, args = ["enrich_json<>''"], []
    if kind:
        where.append("kind=?"); args.append(kind)
    out = []
    for r in rows(conn.execute(f"SELECT * FROM wb_items WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT {int(limit)}", args)):
        try:
            en = json.loads(r["enrich_json"])
        except ValueError:
            continue
        it = effective_item(r)
        out.append({"key": r["key"], "kind": r["kind"], "label": r["label"], "description": r["description"],
                    "status": r["status"], "enrich": en,
                    "has": {"website": it.values("official website"), "instagram": it.values("Instagram username"),
                            "place": it.values("work location") + it.values("located in")}})
    return out


def wikidata_counts(conn) -> dict:
    """Lookup rows per status, and how many staged items carry a Wikidata id from any source."""
    out = {r["status"]: r["n"] for r in rows(conn.execute("SELECT status, COUNT(*) n FROM wb_wikidata GROUP BY status"))}
    out["linked"] = conn.execute("SELECT COUNT(*) FROM wb_items WHERE wikidata_qid<>'' OR item_json LIKE '%\"wikidata\": \"Q%'").fetchone()[0]
    return out


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
