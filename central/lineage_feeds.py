"""
central.lineage_feeds
=====================
Pull lineage (who taught whom) that a partner studio publishes as a read-only feed, into
the same ``lineage_submissions`` table the public lineage form writes — so it goes through
the same publication gate (``central.approvals``) and, once a reviewer approves it, shows
up in the relationship graph.

The first feed is Charlottesville Glass: residents record their lineage on their own
residency pages at cvilleglass.org, and the site publishes it, in the lineage form's own
field names, at ``https://cvilleglass.org/api/residents.php``::

    {"studio": "Charlottesville Glass",
     "residents": [{"name", "url", ..., "lineage": [
         {"student_name", "student_gid", "mentor_name", "mentor_gid",
          "relation", "where", "years", "note", "source"}]}]}

``*_gid`` is a Glass Database id (``gdb:profiles:821c590e17cf275d``) when the person is
linked to a maker here; the graph prefers it to name matching.

Rules
  * Re-runnable. A row's id is derived from feed URL + student + mentor + relation, so a
    second run updates in place instead of duplicating.
  * Pending by default. New rows have no ``_approvals`` entry (= not public). A row whose
    content changed goes back to pending; an unchanged row keeps its decision.
  * Withdrawn rows (gone from the feed) are rejected — hidden, never deleted — with
    reviewer ``feed:withdrawn``; if one comes back it is pending again.
  * Provenance on every row: ``_source_file`` = the feed URL, ``_source_sheet`` = ``feed``,
    ``source_url`` = the resident's page (or the row's own ``source``), ``feed_studio``.
  * Fetch safety: https only, public hosts only (``studio_sources.url_ok``), no redirects,
    capped response size. Feeds come from ``DEFAULT_FEEDS`` plus ``GLASSDB_LINEAGE_FEEDS``
    (comma-separated) — an operator's list, never a URL a visitor typed.

Run it from ``scripts/import_lineage_feeds.py`` or the admin Review queue
(``POST /api/admin/lineage-feeds/import``).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from central import approvals, intake, notify
from central.studio_sources import url_ok

TABLE = "lineage_submissions"
DEFAULT_FEEDS = ["https://cvilleglass.org/api/residents.php"]
MAX_BYTES = 2_000_000
RELATIONS = next(f for f in intake.FORMS["lineage"]["fields"] if f["name"] == "relation")["options"]
# extra columns beyond the form's own: (name, label, public)
EXTRA_COLS = [("student_gid", "Student Glass Database id", 1),
              ("mentor_gid", "Mentor Glass Database id", 1),
              ("source_url", "Source page", 1),
              ("feed_studio", "Imported from", 1)]
CONTENT = ("student_name", "mentor_name", "relation", "where", "years", "note",
           "student_gid", "mentor_gid", "source_url", "feed_studio")
WITHDRAWN = "feed:withdrawn"


def feeds() -> list[str]:
    extra = [u.strip() for u in os.environ.get("GLASSDB_LINEAGE_FEEDS", "").split(",") if u.strip()]
    return list(dict.fromkeys(DEFAULT_FEEDS + extra))


def ensure(conn) -> None:
    intake.ensure(conn, "lineage")
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{TABLE}")')}
    base = len(intake.data_fields("lineage"))
    for i, (col, label, public) in enumerate(EXTRA_COLS):
        if col not in have:
            conn.execute(f'ALTER TABLE "{TABLE}" ADD COLUMN "{col}" TEXT')
        conn.execute("INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public) VALUES (?,?,?,?,?)",
                     (TABLE, col, label, base + i, public))
    conn.commit()


def _s(v) -> str:
    return " ".join(str(v).split()).strip() if v not in (None, "") else ""


def _key(s: str) -> str:
    return _s(s).lower()


def _gid(v) -> str:
    v = _s(v)
    return v if v.startswith("gdb:") else ""


def row_id(feed_url: str, student: str, mentor: str, relation: str) -> str:
    seed = "|".join(("lineage-feed", feed_url.strip(), _key(student), _key(mentor), _key(relation)))
    return hashlib.sha1(seed.encode()).hexdigest()[:16]


def parse(feed: dict, feed_url: str) -> list[dict]:
    """The feed's lineage rows, cleaned, one per (student, mentor, relation). Pure."""
    if not isinstance(feed, dict):
        raise ValueError("feed is not a JSON object")
    studio = _s(feed.get("studio")) or feed_url
    out: dict[str, dict] = {}
    for res in feed.get("residents") or []:
        if not isinstance(res, dict):
            continue
        for ln in res.get("lineage") or []:
            if not isinstance(ln, dict):
                continue
            student, mentor = _s(ln.get("student_name")), _s(ln.get("mentor_name"))
            if not student or not mentor or _key(student) == _key(mentor):
                continue
            rel = _s(ln.get("relation"))
            rel = next((r for r in RELATIONS if r.lower() == rel.lower()), "Studied under")
            src = _s(ln.get("source"))
            src = src if src.startswith(("http://", "https://")) else _s(res.get("url"))
            note = _s(ln.get("note"))[:2000]
            row = {"student_name": student, "mentor_name": mentor, "relation": rel,
                   "where": _s(ln.get("where"))[:300], "years": _s(ln.get("years"))[:60], "note": note,
                   "student_gid": _gid(ln.get("student_gid")), "mentor_gid": _gid(ln.get("mentor_gid")),
                   "source_url": src[:500], "feed_studio": studio[:200]}
            out[row_id(feed_url, student, mentor, rel)] = row
    return [{"_row_id": k, **v} for k, v in out.items()]


def fetch(url: str, verify: bool = True) -> dict:
    ok, why = url_ok(url)
    if not ok:
        raise ValueError(why)
    import httpx
    with httpx.Client(timeout=15, verify=verify, follow_redirects=False,
                      headers={"Accept": "application/json",
                               "User-Agent": "GlassDatabase-lineage-import/1"}) as cl:
        with cl.stream("GET", url) as r:
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code} from {url}")
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise ValueError("feed larger than 2 MB")
    return json.loads(buf.decode("utf-8"))


def _status(conn, rid: str) -> tuple[str, str] | None:
    r = conn.execute("SELECT status, reviewer FROM _approvals WHERE tbl=? AND row_id=?", (TABLE, rid)).fetchone()
    return (r[0], r[1] or "") if r else None


def upsert(conn, rows: list[dict], feed_url: str, dry_run: bool = False) -> dict:
    """Write parsed rows for one feed. Returns counts and the ids touched."""
    ensure(conn)
    now = datetime.now(timezone.utc).isoformat()
    who = rows[0]["feed_studio"] if rows else feed_url
    res = {"feed": feed_url, "rows": len(rows), "new": 0, "changed": 0, "unchanged": 0,
           "withdrawn": 0, "restored": 0, "new_ids": [], "dry_run": dry_run}
    cols = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *CONTENT, "submitted_by"]
    qcols = ", ".join(f'"{c}"' for c in cols)
    seen = set()
    for row in rows:
        rid = row["_row_id"]
        seen.add(rid)
        old = conn.execute(f'SELECT * FROM "{TABLE}" WHERE _row_id=?', (rid,)).fetchone()
        old = dict(old) if old else None
        st = _status(conn, rid)
        if old is None:
            res["new"] += 1
            res["new_ids"].append(rid)
        elif all((old.get(c) or "") == row[c] for c in CONTENT):
            if st and st[1] == WITHDRAWN:            # back in the feed after being withdrawn
                res["restored"] += 1
            else:
                res["unchanged"] += 1
                continue
        else:
            res["changed"] += 1
        if dry_run:
            continue
        vals = [rid, feed_url, "feed", now, *[row[c] for c in CONTENT], f"{who} (lineage feed)"]
        conn.execute(f'INSERT OR REPLACE INTO "{TABLE}" ({qcols}) VALUES ({", ".join("?" for _ in cols)})', vals)
        if st:                                       # new content → back to pending review
            conn.execute("DELETE FROM _approvals WHERE tbl=? AND row_id=?", (TABLE, rid))
    gone = [r[0] for r in conn.execute(
        f'SELECT _row_id FROM "{TABLE}" WHERE _source_file=? AND _source_sheet=?', (feed_url, "feed"))
        if r[0] not in seen and (_status(conn, r[0]) or ("", ""))[1] != WITHDRAWN]
    res["withdrawn"] = len(gone)
    if gone and not dry_run:
        approvals.set_status(conn, TABLE, gone, "rejected", reviewer=WITHDRAWN)
    if not dry_run:
        conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM lineage_submissions), updated_at=? "
                     "WHERE tbl=?", (now, TABLE))
        conn.commit()
    return res


def import_feed(conn, url: str, *, feed: dict | None = None, dry_run: bool = False,
                verify: bool = True, notify_reviewers: bool = True) -> dict:
    """Fetch (unless ``feed`` is given), parse and upsert one feed."""
    data = feed if feed is not None else fetch(url, verify=verify)
    res = upsert(conn, parse(data, url), url, dry_run=dry_run)
    pending = res["new"] + res["changed"] + res["restored"]
    if pending and notify_reviewers and not dry_run:
        try:
            notify.notify_message("Lineage feed imported", {
                "Feed": url, "New": str(res["new"]), "Changed": str(res["changed"]),
                "Withdrawn": str(res["withdrawn"]), "Review": "/review.html#directory"})
        except Exception:  # noqa: BLE001  (a notification never blocks an import)
            pass
    return res


def import_all(conn, urls: list[str] | None = None, **kw) -> list[dict]:
    out = []
    for u in urls or feeds():
        try:
            out.append(import_feed(conn, u, **kw))
        except Exception as ex:  # noqa: BLE001  (one bad feed doesn't stop the others)
            out.append({"feed": u, "error": str(ex)})
    return out
