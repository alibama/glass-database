"""
glowtbook.store
===============
The per-user Glowtbook store — journal entries, objects, images, provenance
events, contributions — as one schema + helpers, extracted from the Streamlit
app so the **API can open the same database**. That is what lets the capture
app save a fingerprint straight onto one of your pieces, the verify app record a
verification, and the Seasons page enter a piece you've published.

Everything is keyed by ``user_id``; demo mode (no login configured) shares the
id ``__demo__``. The file name is kept from earlier deploys.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA / "glowtbook_demo.db"


def db_path() -> Path:
    """The store file: GLOWTBOOK_DB_PATH if set (tests, alternate deploys), else the default.
    Resolved per call so an env override set after import still applies."""
    return Path(os.environ.get("GLOWTBOOK_DB_PATH") or DB_PATH)
DEMO_UID = "__demo__"

_SCHEMA = """
  CREATE TABLE IF NOT EXISTS profile (
    user_id TEXT PRIMARY KEY, display_name TEXT DEFAULT '', website TEXT DEFAULT '',
    nationality_base TEXT DEFAULT '', status TEXT DEFAULT 'active',
    techniques TEXT DEFAULT '', contributions TEXT DEFAULT '',
    career_highlights TEXT DEFAULT '');
  CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY, user_id TEXT, entry_date TEXT, title TEXT, body TEXT,
    tags TEXT DEFAULT '', location TEXT DEFAULT '', technique TEXT DEFAULT '',
    image_path TEXT DEFAULT '', object_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')));
  CREATE TABLE IF NOT EXISTS object (
    id INTEGER PRIMARY KEY, user_id TEXT, title TEXT, maker TEXT DEFAULT '',
    year TEXT DEFAULT '', techniques TEXT DEFAULT '', materials TEXT DEFAULT '',
    dimensions TEXT DEFAULT '', description TEXT DEFAULT '',
    acquired TEXT DEFAULT '', current_location TEXT DEFAULT '',
    value_amount TEXT DEFAULT '', value_currency TEXT DEFAULT 'USD', insured INTEGER DEFAULT 0,
    status TEXT DEFAULT 'in collection', fingerprint_json TEXT, traits_json TEXT,
    fingerprint_at TEXT, colour_sources TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
  CREATE TABLE IF NOT EXISTS object_image (
    id INTEGER PRIMARY KEY, object_id INTEGER, user_id TEXT, role TEXT DEFAULT 'photo',
    aip_path TEXT, caption TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')));
  CREATE TABLE IF NOT EXISTS prov_event (
    id INTEGER PRIMARY KEY, object_id INTEGER, user_id TEXT, event_type TEXT,
    event_date TEXT, actor TEXT DEFAULT '', location TEXT DEFAULT '', note TEXT DEFAULT '',
    value_amount TEXT DEFAULT '', value_currency TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')));
  CREATE TABLE IF NOT EXISTS contribution (
    id INTEGER PRIMARY KEY, user_id TEXT, object_id INTEGER, content_hash TEXT,
    manifest_json TEXT, created_at TEXT DEFAULT (datetime('now')));
"""

# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add
# these to a database made by an older version, so add any that are missing.
# (ALTER ADD COLUMN defaults must be constants — no datetime() here.)
EXPECTED_COLUMNS = {
    "journal": [("tags", "TEXT DEFAULT ''"), ("location", "TEXT DEFAULT ''"),
                ("technique", "TEXT DEFAULT ''"), ("image_path", "TEXT DEFAULT ''"),
                ("object_id", "INTEGER")],
    "object": [("acquired", "TEXT DEFAULT ''"), ("current_location", "TEXT DEFAULT ''"),
               ("value_amount", "TEXT DEFAULT ''"), ("value_currency", "TEXT DEFAULT 'USD'"),
               ("insured", "INTEGER DEFAULT 0"), ("status", "TEXT DEFAULT 'in collection'"),
               ("updated_at", "TEXT"), ("fingerprint_json", "TEXT"), ("traits_json", "TEXT"),
               ("fingerprint_at", "TEXT"), ("colour_sources", "TEXT DEFAULT ''")],
    "object_image": [("role", "TEXT DEFAULT 'photo'"), ("caption", "TEXT DEFAULT ''")],
    "prov_event": [("location", "TEXT DEFAULT ''"), ("note", "TEXT DEFAULT ''"),
                   ("value_amount", "TEXT DEFAULT ''"), ("value_currency", "TEXT DEFAULT ''")],
    "profile": [("website", "TEXT DEFAULT ''"), ("nationality_base", "TEXT DEFAULT ''"),
                ("status", "TEXT DEFAULT 'active'"), ("techniques", "TEXT DEFAULT ''"),
                ("contributions", "TEXT DEFAULT ''"), ("career_highlights", "TEXT DEFAULT ''")],
}


def migrate_columns(c: sqlite3.Connection) -> None:
    for table, cols in EXPECTED_COLUMNS.items():
        try:
            have = {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
        except sqlite3.OperationalError:
            continue
        for col, decl in cols:
            if col not in have:
                try:
                    c.execute(f'ALTER TABLE "{table}" ADD COLUMN {col} {decl}')
                except sqlite3.OperationalError:
                    pass  # already added by a concurrent run


def open_store(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and create/migrate) the per-user store. Safe to call from several
    processes — WAL + busy timeout, like the central DB."""
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, check_same_thread=False)
    c.row_factory = sqlite3.Row
    for pragma in ("journal_mode = WAL", "busy_timeout = 5000"):
        try:
            c.execute(f"PRAGMA {pragma};")
        except Exception:  # noqa: BLE001
            pass
    c.executescript(_SCHEMA)
    migrate_columns(c)
    c.commit()
    return c


# --- queries the API and the app share -------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint_summary(fp_json: str | None) -> dict | None:
    if not fp_json:
        return None
    try:
        from glowtbook import fingerprint as _fp
        s = _fp.summary(fp_json)
        s["views"] = len((_fp._as_obj(fp_json).get("frames") or []))
        return s
    except Exception:  # noqa: BLE001
        return None


def publication_status(central, store, uid: str, oid: int) -> dict:
    """Where this piece stands in the public registry: private (never
    contributed), pending (in the review queue), published (an approved public
    row), or rejected. Resolved through the contribution's content hash."""
    out = {"state": "private", "content_hash": None, "row_id": None, "submitted_at": None}
    row = store.execute("SELECT content_hash, created_at FROM contribution WHERE user_id=? AND object_id=? "
                        "ORDER BY id DESC LIMIT 1", (uid, oid)).fetchone()
    if not row:
        return out
    out["content_hash"], out["submitted_at"] = row["content_hash"], row["created_at"]
    if central is None:
        out["state"] = "pending"
        return out
    try:
        pub = central.execute("SELECT _row_id FROM objects WHERE content_hash=?",
                              (row["content_hash"],)).fetchone()
    except Exception:  # noqa: BLE001
        pub = None
    if pub:
        try:
            ok = central.execute("SELECT 1 FROM _approvals WHERE tbl='objects' AND row_id=? AND status='approved'",
                                 (pub[0],)).fetchone()
        except Exception:  # noqa: BLE001
            ok = True
        out["row_id"] = pub[0]
        out["state"] = "published" if ok else "pending"
        return out
    try:
        sub = central.execute("SELECT status FROM object_submissions WHERE content_hash=?",
                              (row["content_hash"],)).fetchone()
    except Exception:  # noqa: BLE001
        sub = None
    if sub:
        out["state"] = "rejected" if sub[0] == "rejected" else "pending"
    return out


def object_card(store, central, uid: str, o) -> dict:
    """One object as the API/pages see it: metadata + fingerprint summary +
    publication state + image/event counts. `o` is a row from `object`."""
    oid = o["id"]
    n_img = store.execute("SELECT COUNT(*) FROM object_image WHERE object_id=? AND user_id=?",
                          (oid, uid)).fetchone()[0]
    n_ev = store.execute("SELECT COUNT(*) FROM prov_event WHERE object_id=? AND user_id=?",
                         (oid, uid)).fetchone()[0]
    keys = o.keys()
    fp_json = o["fingerprint_json"] if "fingerprint_json" in keys else None
    return {
        "id": oid, "title": o["title"], "maker": o["maker"], "year": o["year"],
        "techniques": [t for t in (o["techniques"] or "").split("|") if t],
        "materials": o["materials"], "dimensions": o["dimensions"],
        "description": o["description"],
        "colour_sources": (o["colour_sources"] if "colour_sources" in keys else "") or "",
        "created_at": o["created_at"], "updated_at": o["updated_at"],
        "images": n_img, "events": n_ev,
        "fingerprint": fingerprint_summary(fp_json),
        "fingerprint_at": (o["fingerprint_at"] if "fingerprint_at" in keys else None),
        "publication": publication_status(central, store, uid, oid),
    }


def list_objects(store, central, uid: str) -> list[dict]:
    rows = store.execute("SELECT * FROM object WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    return [object_card(store, central, uid, o) for o in rows]


def get_object(store, uid: str, oid: int):
    return store.execute("SELECT * FROM object WHERE id=? AND user_id=?", (oid, uid)).fetchone()


def set_fingerprint(store, uid: str, oid: int, fingerprint: dict | str) -> None:
    fp = fingerprint if isinstance(fingerprint, str) else json.dumps(fingerprint)
    store.execute("UPDATE object SET fingerprint_json=?, fingerprint_at=?, updated_at=datetime('now') "
                  "WHERE id=? AND user_id=?", (fp, _now(), oid, uid))
    store.commit()


def set_dimensions(store, uid: str, oid: int, dims: str) -> None:
    store.execute("UPDATE object SET dimensions=?, updated_at=datetime('now') WHERE id=? AND user_id=?",
                  (dims, oid, uid))
    store.commit()
