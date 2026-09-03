"""
central.intake
==============
Config-driven public intake "sheets" for growing the database. Each form writes a
**pending** row through the publication gate (nothing shows until an admin
approves it) and fires a Discord notification with one-click approve/reject
links. Adding a new content type is just another entry in FORMS.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from central import approvals, notify

# kind: text | textarea | url | email | date | select:<comma options>
# field: (name, label, kind, required, private)
FORMS = {
    "artist": {
        "title": "Artist", "table": "artist_submissions", "domain": "artists",
        "desc": "Community-submitted artists (pending review)",
        "fields": [
            ("name", "Name", "text", True, False),
            ("location", "Based in", "text", False, False),
            ("website", "Website", "url", False, False),
            ("techniques", "Techniques / focus", "text", False, False),
            ("instagram", "Instagram / social", "text", False, False),
            ("bio", "Short bio", "textarea", False, False),
            ("email", "Contact email", "email", False, True),
            ("submitted_by", "Your name or email", "text", False, True),
        ],
    },
    "studio": {
        "title": "Studio", "table": "studio_submissions", "domain": "studios",
        "desc": "Community-submitted studios (pending review)",
        "fields": [
            ("name", "Studio name", "text", True, False),
            ("city", "City", "text", False, False),
            ("region", "State / region", "text", False, False),
            ("country", "Country", "text", False, False),
            ("studio_type", "Type", "select:Hot shop,Flameworking,Kiln / fusing,Cold shop,Mixed,Other",
             False, False),
            ("website", "Website", "url", False, False),
            ("access", "Public access / rentals?", "text", False, False),
            ("description", "Description", "textarea", False, False),
            ("email", "Contact email", "email", False, True),
            ("submitted_by", "Your name or email", "text", False, True),
        ],
    },
    "event": {
        "title": "Event", "table": "event_submissions", "domain": "events",
        "desc": "Community-submitted events & exhibitions (pending review)",
        "fields": [
            ("title", "Title", "text", True, False),
            ("organization", "Host / venue", "text", False, False),
            ("location", "Location", "text", False, False),
            ("start_date", "Starts", "date", False, False),
            ("end_date", "Ends", "date", False, False),
            ("url", "Link", "url", False, False),
            ("description", "Description", "textarea", False, False),
            ("email", "Contact email", "email", False, True),
            ("submitted_by", "Your name or email", "text", False, True),
        ],
    },
}


def form(key: str) -> dict:
    return FORMS[key]


def ensure(conn, key: str) -> None:
    f = FORMS[key]
    tbl = f["table"]
    approvals.ensure_approvals(conn)
    cols = ", ".join(f'"{c}" TEXT' for c, *_ in f["fields"])
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{tbl}" (_row_id TEXT PRIMARY KEY, '
                 f'_source_file TEXT, _source_sheet TEXT, _imported_at TEXT, {cols})')
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")')}
    for c, *_ in f["fields"]:
        if c not in have:
            conn.execute(f'ALTER TABLE "{tbl}" ADD COLUMN "{c}" TEXT')
    now = datetime.now(timezone.utc).isoformat()
    n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
    conn.execute("""INSERT OR REPLACE INTO _datasets
        (tbl,domain,source_file,source_sheet,visibility,row_count,description,updated_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (tbl, f["domain"], "intake", "form", "public", n, f["desc"], now))
    for i, (c, label, _kind, _req, pub) in enumerate(f["fields"]):
        conn.execute("""INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public)
                        VALUES (?,?,?,?,?)""", (tbl, c, label, i, 0 if pub else 1))
    conn.commit()


def submit(conn, key: str, values: dict, base_url: str = "") -> str:
    """Write a pending submission and fire the Discord notification. Returns row id."""
    f = FORMS[key]
    ensure(conn, key)
    now = datetime.now(timezone.utc).isoformat()
    seed = key + (values.get(f["fields"][0][0], "") or "") + now
    rid = hashlib.sha1(seed.encode()).hexdigest()[:16]
    names = [c for c, *_ in f["fields"]]
    allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *names]
    row = [rid, "intake", "form", now, *[(values.get(c) or "").strip() for c in names]]
    conn.execute(f'INSERT INTO "{f["table"]}" ({", ".join(chr(34)+c+chr(34) for c in allc)}) '
                 f'VALUES ({", ".join("?" for _ in allc)})', row)
    conn.commit()   # pending: no _approvals row yet

    public = {label: values.get(c) for c, label, _k, _r, priv in f["fields"] if not priv}
    title = (values.get(f["fields"][0][0]) or f["title"]).strip()
    notify.notify_submission(f["title"], title, public, f["table"], rid, base_url)
    return rid
