"""
api.admin_data — collecting events, the tables and usage, for the static admin pages
====================================================================================
The last three sections of the Streamlit console as API routes, so the website's
own pages can do what the console did: ``/events-admin.html`` runs collecting
events, ``/datasets.html`` is every table in the registry, ``/analytics.html`` is
usage. Every route needs an administrator (``api.auth.require_admin``: a signed-in
admin email, or the ``X-API-Key`` admin token). Thin wrappers: the work is done by
``central.seasons``, ``central.approvals`` and ``central.analytics``, nothing is
decided here.

Collecting events (``central.seasons``; the code still says *season*)
  GET    /admin/events                      every event with its counts, categories and
                                            approved entries; the presets and defaults
                                            for the create form
  POST   /admin/events                      create one {title, theme, description, starts,
                                            entries_close, voting_closes, ends, rules,
                                            prize_note, votes_per_voter, categories (text,
                                            one ``title | vote|measured | solo`` per line),
                                            prizes (text, one ``title | awarded for | detail |
                                            sponsor | link`` per line), open (bool)}
  POST   /admin/events/{id}                 edit those fields (not the status, not the awards)
  POST   /admin/events/{id}/status          {status: open|voting|closed}; closing hands out
                                            the end-of-event badges, names the crowd favourite
                                            and writes each prize's leader in as its winner
  DELETE /admin/events/{id}                 a draft only
  POST   /admin/events/{id}/prizes          add a prize {title, awarded_for, detail, sponsor,
                                            sponsor_url, winner_text}, or {defaults: true} for
                                            the theme's default prizes
  POST   /admin/events/{id}/prizes/{pid}    edit any of those fields; winner_text names the winner
  DELETE /admin/events/{id}/prizes/{pid}    remove a prize
  POST   /admin/events/entries/{id}/pull    take an approved entry off the event page
  POST   /admin/events/entries/{id}         tidy an entry's studio {studio}, so one shop is one
                                            name on the studio board

The tables (``_datasets``: the registry's tables that go through the publication gate)
  GET    /admin/datasets                    every table with its counts and columns
  GET    /admin/datasets/{tbl}              rows, paged (?limit=&offset=&q=), the registry's
                                            columns, each row's gate status
  POST   /admin/datasets/{tbl}/rows         add a row {values: {column: value}}; published
  POST   /admin/datasets/{tbl}/rows/{rid}   edit a cell {column, value} or {values: {...}}; published
  POST   /admin/datasets/{tbl}/import       a CSV in the request body; rows with a known
                                            ``_row_id`` update, the rest are added; published
  GET    /admin/datasets/{tbl}/export.csv   the whole table, every column, ``_row_id`` first

Every edit, added row and imported row is approved through ``central.approvals`` with the
administrator as the reviewer, exactly as the console's grid did. There is no delete here:
a row that should not be public is rejected through ``POST /admin/decide``.

Usage (``central.analytics``)
  GET    /admin/analytics?days=7|30|90      the summary: views, visitors, by day, by surface,
                                            the most-used views, events, countries, the
                                            newsletter count
  GET    /admin/subscribers.csv             the newsletter list (the same file as
                                            ``/subscribers.csv``, for a signed-in admin)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import require_admin  # noqa: E402
from central import approvals  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(prefix="/admin", tags=["admin"])

PRESETS = (("Pumpkins", "pumpkin"), ("Ornaments", "ornament"), ("General", ""))
DEFAULT_RULES = ("One entry per piece; the piece must already be published through Glowtbook "
                 "(with its Glass ID, credentialed, approved). Editions welcome — say how many. "
                 "Collectors add a piece to their collection by scanning it with Check a Piece "
                 "(a match of 70+ counts straight away). Votes can be swapped until voting closes.")
DEFAULT_PRIZE = "Bragging rights, a homepage feature, and the Crowd Favourite badge"
EVENT_FIELDS = ("title", "theme", "description", "starts", "entries_close", "voting_closes", "ends",
                "rules", "prize_note")
PAGE_MAX = 200
IMPORT_MAX = 25 * 1024 * 1024          # a CSV larger than this is not a sheet someone edited


# --- helpers ----------------------------------------------------------------------
def _conn():
    c = connect()
    approvals.ensure_approvals(c)
    return c


def _who(u: dict) -> str:
    return (u.get("email") or u.get("name") or "admin")[:120]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _text(v) -> str:
    return "" if v is None else str(v)


# ===================================================================================
# Collecting events
# ===================================================================================
def _event_shape(conn, s: dict, with_entries: bool = True) -> dict:
    from central import seasons
    s = dict(s)
    s["categories"] = [{k: c.get(k) for k in ("id", "slug", "title", "kind", "measure", "solo_only", "sort")}
                       for c in seasons.categories(conn, s["id"])]
    s["categories_text"] = seasons.categories_text(s["categories"])
    s["prizes"] = seasons.prize_standings(conn, s["id"])
    s["prizes_text"] = seasons.prizes_text(s["prizes"])
    s["page"] = f"/games.html?season={s.get('slug') or ''}"
    s["pending_entries"] = int(conn.execute(
        "SELECT COUNT(*) FROM season_entries WHERE season_id=? AND status='pending'", (s["id"],)).fetchone()[0])
    if with_entries:
        s["entries"] = [{k: e.get(k) for k in ("id", "object_row_id", "title", "artist_name", "studio",
                                                "edition_size", "price_note", "vote_count", "claim_count",
                                                "rarity", "solo", "legendary", "fingerprinted", "created_at",
                                                "image_url")}
                        for e in seasons.entries(conn, s["id"], "approved")]
    return s


def events_overview(conn) -> dict:
    from central import seasons
    seasons.ensure(conn)
    today = date.today()
    return {
        "summary": seasons.summary(conn),
        "review_mode": seasons.entry_review(),
        "events": [_event_shape(conn, s) for s in seasons.list_seasons(conn, include_draft=True)],
        "presets": [{"label": label, "theme": theme,
                     "categories_text": seasons.categories_text(seasons.default_categories(theme)),
                     "prizes_text": seasons.prizes_text(seasons.default_prizes(theme))}
                    for label, theme in PRESETS],
        "defaults": {"starts": str(today), "entries_close": str(today + timedelta(days=21)),
                     "voting_closes": str(today + timedelta(days=28)), "ends": str(today + timedelta(days=30)),
                     "rules": DEFAULT_RULES, "prize_note": DEFAULT_PRIZE, "votes_per_voter": 3},
        "prize_kinds": [{"key": k, **meta} for k, meta in seasons.PRIZE_FOR.items()],
        "studios": seasons.known_studios(conn),
        "statuses": list(seasons.STATUSES),
    }


def _event_fields(payload: dict) -> dict:
    out = {k: _text(payload.get(k)).strip() for k in EVENT_FIELDS}
    for k in ("starts", "entries_close", "voting_closes", "ends"):
        v = out[k]
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise HTTPException(400, f"{k.replace('_', ' ')} must be a date like 2026-10-01")
    try:
        out["votes_per_voter"] = max(1, min(10, int(payload.get("votes_per_voter") or 3)))
    except (TypeError, ValueError):
        raise HTTPException(400, "votes per voter is a whole number from 1 to 10")
    return out


def _get_event(conn, sid: int) -> dict:
    from central import seasons
    s = seasons.get_season(conn, int(sid))
    if not s:
        raise HTTPException(404, "no such collecting event")
    return s


@router.get("/events", summary="Every collecting event, with counts, awards and approved entries")
def get_events(u: dict = Depends(require_admin)):
    return events_overview(_conn())


@router.post("/events", summary="Create a collecting event")
def post_events(payload: dict, u: dict = Depends(require_admin)):
    from central import seasons
    payload = payload or {}
    f = _event_fields(payload)
    if not f["title"]:
        raise HTTPException(400, "a title, at least")
    cats = seasons.parse_categories(_text(payload.get("categories")))
    prz = seasons.parse_prizes(_text(payload.get("prizes")))
    conn = _conn()
    seasons.ensure(conn)
    try:
        sid = seasons.create_season(
            conn, title=f["title"], theme=f["theme"], description=f["description"], starts=f["starts"],
            entries_close=f["entries_close"], voting_closes=f["voting_closes"], ends=f["ends"],
            rules=f["rules"], prize_note=f["prize_note"], votes_per_voter=f["votes_per_voter"],
            status="open" if payload.get("open") else "draft", categories=cats or None,
            prizes=prz or None)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, "id": sid, "event": _event_shape(conn, _get_event(conn, sid))}


@router.post("/events/{sid}", summary="Edit a collecting event's words and dates")
def post_event(sid: int, payload: dict, u: dict = Depends(require_admin)):
    conn = _conn()
    _get_event(conn, sid)
    f = _event_fields(payload or {})
    if not f["title"]:
        raise HTTPException(400, "a title, at least")
    sets = ", ".join(f"{k}=?" for k in (*EVENT_FIELDS, "votes_per_voter"))
    conn.execute(f"UPDATE seasons SET {sets} WHERE id=?",
                 [*(f[k] for k in EVENT_FIELDS), f["votes_per_voter"], int(sid)])
    conn.commit()
    return {"ok": True, "event": _event_shape(conn, _get_event(conn, sid))}


@router.post("/events/{sid}/status", summary="Open, start voting, or close a collecting event")
def post_event_status(sid: int, payload: dict, u: dict = Depends(require_admin)):
    from central import seasons
    status = _text((payload or {}).get("status")).strip().lower()
    if status not in seasons.STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(seasons.STATUSES)}")
    conn = _conn()
    _get_event(conn, sid)
    out: dict = {"ok": True, "status": status}
    if status == "closed":
        res = seasons.close_season(conn, int(sid))
        out["crowd_favourite"] = res.get("crowd_favourite")
        out["badges"] = res.get("badges") or {}
    else:
        seasons.set_status(conn, int(sid), status)
    out["event"] = _event_shape(conn, _get_event(conn, sid))
    return out


@router.delete("/events/{sid}", summary="Delete a draft collecting event")
def delete_event(sid: int, u: dict = Depends(require_admin)):
    conn = _conn()
    s = _get_event(conn, sid)
    if s.get("status") != "draft":
        raise HTTPException(409, "only a draft can be deleted; close a live event instead")
    conn.execute("DELETE FROM season_categories WHERE season_id=?", (int(sid),))
    conn.execute("DELETE FROM season_prizes WHERE season_id=?", (int(sid),))
    conn.execute("DELETE FROM seasons WHERE id=? AND status='draft'", (int(sid),))
    conn.commit()
    return {"ok": True, "deleted": int(sid)}


@router.post("/events/entries/{eid}/pull", summary="Take an approved entry off the event page")
def pull_entry(eid: int, u: dict = Depends(require_admin)):
    from central import seasons
    conn = _conn()
    try:
        seasons.approve_entry(conn, int(eid), False)
    except ValueError as ex:
        raise HTTPException(404, str(ex))
    e = seasons.get_entry(conn, int(eid)) or {}
    return {"ok": True, "entry": int(eid), "status": e.get("status"), "season_id": e.get("season_id")}


@router.post("/events/entries/{eid}", summary="Tidy an entry's studio")
def post_entry(eid: int, payload: dict, u: dict = Depends(require_admin)):
    """The studio board and the studio prizes group entries by the shop's name, so two
    spellings of one shop should become one. Blank means Independent."""
    from central import seasons
    payload = payload or {}
    if "studio" not in payload:
        raise HTTPException(400, "studio is the one thing this edits")
    conn = _conn()
    try:
        e = seasons.set_entry_studio(conn, int(eid), _text(payload.get("studio")))
    except ValueError as ex:
        raise HTTPException(404, str(ex))
    return {"ok": True, "entry": {k: e.get(k) for k in ("id", "studio", "title", "artist_name", "season_id")}}


# --- prizes: what is on the table, editable any time ------------------------------------
PRIZE_FIELDS = ("title", "awarded_for", "detail", "sponsor", "sponsor_url", "winner_text")


def _prize_payload(payload: dict) -> dict:
    return {k: _text(payload.get(k)) for k in PRIZE_FIELDS if k in payload}


def _prize_of(conn, sid: int, pid: int) -> dict:
    from central import seasons
    p = seasons.get_prize(conn, int(pid))
    if not p or int(p["season_id"]) != int(sid):
        raise HTTPException(404, "no such prize on this collecting event")
    return p


@router.post("/events/{sid}/prizes", summary="Add a prize, or the theme's default prizes")
def post_prize(sid: int, payload: dict, u: dict = Depends(require_admin)):
    from central import seasons
    conn = _conn()
    s = _get_event(conn, sid)
    payload = payload or {}
    try:
        if payload.get("defaults"):
            ids = seasons.add_prizes(conn, int(sid),
                                     seasons.default_prizes(f"{s.get('theme') or ''} {s.get('title') or ''}"))
        else:
            ids = [seasons.add_prize(conn, int(sid), **_prize_payload(payload))]
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, "ids": ids, "event": _event_shape(conn, _get_event(conn, sid))}


@router.post("/events/{sid}/prizes/{pid}", summary="Edit a prize, or name its winner")
def post_prize_edit(sid: int, pid: int, payload: dict, u: dict = Depends(require_admin)):
    from central import seasons
    conn = _conn()
    _get_event(conn, sid)
    _prize_of(conn, sid, pid)
    try:
        seasons.update_prize(conn, int(pid), **_prize_payload(payload or {}))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, "event": _event_shape(conn, _get_event(conn, sid))}


@router.delete("/events/{sid}/prizes/{pid}", summary="Remove a prize")
def delete_prize(sid: int, pid: int, u: dict = Depends(require_admin)):
    from central import seasons
    conn = _conn()
    _get_event(conn, sid)
    _prize_of(conn, sid, pid)
    seasons.delete_prize(conn, int(pid))
    return {"ok": True, "deleted": int(pid), "event": _event_shape(conn, _get_event(conn, sid))}


# ===================================================================================
# The tables
# ===================================================================================
def _has_table(conn, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _table_cols(conn, tbl: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")')]


def _tables(conn) -> dict[str, dict]:
    """The registry's tables that go through the publication gate (they have a _row_id)."""
    if not _has_table(conn, "_datasets"):
        return {}
    out = {}
    for d in _rows(conn.execute("SELECT tbl, domain, visibility, row_count, description FROM _datasets "
                                "ORDER BY domain, tbl")):
        if _has_table(conn, d["tbl"]) and "_row_id" in _table_cols(conn, d["tbl"]):
            out[d["tbl"]] = d
    return out


def _require_tbl(conn, tbl: str) -> dict:
    d = _tables(conn).get(tbl)
    if not d:
        raise HTTPException(404, f"{tbl!r} is not a table in the registry")
    return d


def _registry_cols(conn, tbl: str) -> list[dict]:
    """The table's columns as the registry describes them (label, public or private),
    plus any column the table has that the registry does not mention."""
    have = [c for c in _table_cols(conn, tbl) if not c.startswith("_")]
    reg = _rows(conn.execute("SELECT column, label, is_public FROM _columns WHERE tbl=? ORDER BY ordinal", (tbl,)))
    out = [{"column": r["column"], "label": r["label"] or r["column"], "is_public": int(r["is_public"] or 0)}
           for r in reg if r["column"] in have]
    seen = {c["column"] for c in out}
    out += [{"column": c, "label": c.replace("_", " ").title(), "is_public": 0} for c in have if c not in seen]
    return out


def _status_map(conn, tbl: str, rids: list[str]) -> dict[str, str]:
    out = {rid: "pending" for rid in rids}
    for i in range(0, len(rids), 400):
        chunk = rids[i:i + 400]
        q = ",".join("?" for _ in chunk)
        for rid, st in conn.execute(f"SELECT row_id, status FROM _approvals WHERE tbl=? AND row_id IN ({q})",
                                    (tbl, *chunk)).fetchall():
            out[rid] = st
    return out


def _refresh_count(conn, tbl: str) -> None:
    conn.execute(f'UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM "{tbl}"), updated_at=? WHERE tbl=?',
                 (_now(), tbl))


def _clean_values(conn, tbl: str, values, allow_empty: bool = False) -> dict[str, str]:
    """{column: text} limited to the table's own data columns; the internal ``_`` columns
    cannot be set this way."""
    if not isinstance(values, dict) or not values:
        raise HTTPException(400, "values is a {column: value} object")
    have = set(_table_cols(conn, tbl))
    out: dict[str, str] = {}
    for k, v in values.items():
        col = str(k)
        if col.startswith("_") or col not in have:
            raise HTTPException(400, f"{tbl} has no column {col!r}")
        out[col] = _text(v)
    if not allow_empty and not any(v.strip() for v in out.values()):
        raise HTTPException(400, "fill at least one field")
    return out


def _insert_row(conn, tbl: str, values: dict[str, str], source: str, rid: str = "", salt: str = "") -> str:
    now = _now()
    rid = rid or hashlib.sha1((tbl + "|" + source + "|" + json.dumps(values, sort_keys=True) + now + salt)
                              .encode()).hexdigest()[:16]
    allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *values]
    conn.execute(f'INSERT INTO "{tbl}" ({", ".join(chr(34) + x + chr(34) for x in allc)}) '
                 f'VALUES ({", ".join("?" for _ in allc)})', [rid, source, source, now, *values.values()])
    return rid


@router.get("/datasets", summary="Every table in the registry, with its counts and columns")
def get_datasets(u: dict = Depends(require_admin)):
    conn = _conn()
    out = []
    for tbl, d in _tables(conn).items():
        out.append({**d, "counts": approvals.counts(conn, tbl), "columns": _registry_cols(conn, tbl)})
    return {"tables": out}


@router.get("/datasets/{tbl}", summary="Rows of a table, paged, with each row's gate status")
def get_dataset(tbl: str, limit: int = Query(50, ge=1, le=PAGE_MAX), offset: int = Query(0, ge=0),
                q: str = Query("", description="case-insensitive search across the data columns"),
                u: dict = Depends(require_admin)):
    conn = _conn()
    d = _require_tbl(conn, tbl)
    cols = _registry_cols(conn, tbl)
    names = [c["column"] for c in cols]
    where, params = "", []
    if q.strip() and names:
        where = "WHERE " + " OR ".join(f'"{c}" LIKE ?' for c in names)
        params = [f"%{q.strip()}%"] * len(names)
    total = conn.execute(f'SELECT COUNT(*) FROM "{tbl}" {where}', params).fetchone()[0]
    collist = ", ".join(f'"{c}"' for c in ["_row_id", "_source_file", "_imported_at", *names])
    rows = _rows(conn.execute(f'SELECT {collist} FROM "{tbl}" {where} ORDER BY rowid DESC LIMIT ? OFFSET ?',
                              [*params, limit, offset]))
    status = _status_map(conn, tbl, [r["_row_id"] for r in rows])
    for r in rows:
        r["_status"] = status.get(r["_row_id"], "pending")
    return {**d, "counts": approvals.counts(conn, tbl), "columns": cols, "rows": rows,
            "total": total, "limit": limit, "offset": offset, "q": q}


@router.post("/datasets/{tbl}/rows", summary="Add a row (published)")
def post_row(tbl: str, payload: dict, u: dict = Depends(require_admin)):
    conn = _conn()
    _require_tbl(conn, tbl)
    values = _clean_values(conn, tbl, (payload or {}).get("values"))
    rid = _insert_row(conn, tbl, values, "admin-ui")
    _refresh_count(conn, tbl)
    conn.commit()
    approvals.set_status(conn, tbl, [rid], "approved", reviewer=_who(u))
    return {"ok": True, "table": tbl, "row_id": rid, "status": "approved", "counts": approvals.counts(conn, tbl)}


@router.post("/datasets/{tbl}/rows/{rid}", summary="Edit a cell, or a few (published)")
def post_cell(tbl: str, rid: str, payload: dict, u: dict = Depends(require_admin)):
    conn = _conn()
    _require_tbl(conn, tbl)
    payload = payload or {}
    values = payload.get("values")
    if values is None and "column" in payload:
        values = {str(payload.get("column")): payload.get("value")}
    values = _clean_values(conn, tbl, values, allow_empty=True)
    if not conn.execute(f'SELECT 1 FROM "{tbl}" WHERE _row_id=?', (rid,)).fetchone():
        raise HTTPException(404, f"no row {rid} in {tbl}")
    sets = ", ".join(f'"{c}"=?' for c in values)
    conn.execute(f'UPDATE "{tbl}" SET {sets} WHERE _row_id=?', [*values.values(), rid])
    conn.commit()
    approvals.set_status(conn, tbl, [rid], "approved", reviewer=_who(u))
    return {"ok": True, "table": tbl, "row_id": rid, "values": values, "status": "approved"}


def import_csv(conn, tbl: str, text: str, reviewer: str) -> dict:
    """Upsert rows from a CSV: a row whose ``_row_id`` is already in the table updates in
    place, the rest are added (with the given id, or a fresh one); every touched row is
    approved. Columns the table does not have are ignored; the internal columns are
    never written from the file."""
    reader = csv.DictReader(io.StringIO(text))
    fields = [f for f in (reader.fieldnames or []) if f]
    have = set(_table_cols(conn, tbl))
    data_cols = [f for f in fields if f in have and not f.startswith("_")]
    if not data_cols:
        raise HTTPException(400, f"none of the CSV's columns ({', '.join(fields[:8]) or 'no header'}) are "
                                 f"columns of {tbl}")
    updated = added = skipped = 0
    touched: list[str] = []
    for n, r in enumerate(reader):
        rid = _text(r.get("_row_id")).strip() if "_row_id" in fields else ""
        values = {c: _text(r.get(c)) for c in data_cols}
        if rid and conn.execute(f'SELECT 1 FROM "{tbl}" WHERE _row_id=?', (rid,)).fetchone():
            sets = ", ".join(f'"{c}"=?' for c in data_cols)
            conn.execute(f'UPDATE "{tbl}" SET {sets} WHERE _row_id=?', [*values.values(), rid])
            updated += 1
        else:
            if not any(v.strip() for v in values.values()):
                skipped += 1
                continue
            rid = _insert_row(conn, tbl, values, "csv-import", rid=rid, salt=str(n))
            added += 1
        touched.append(rid)
    _refresh_count(conn, tbl)
    conn.commit()
    if touched:
        approvals.set_status(conn, tbl, touched, "approved", reviewer=reviewer)
    return {"updated": updated, "added": added, "skipped": skipped, "columns": data_cols}


@router.post("/datasets/{tbl}/import", summary="Import a CSV (upsert by _row_id; published)")
async def post_import(tbl: str, request: Request, u: dict = Depends(require_admin)):
    conn = _conn()
    _require_tbl(conn, tbl)
    raw = await request.body()
    if len(raw) > IMPORT_MAX:
        raise HTTPException(413, "that CSV is over 25 MB; import it with the command line instead")
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise HTTPException(400, "the CSV is empty")
    from central import snapshots
    snapshots.snapshot(conn, tbl, "before CSV import")          # the undo the console kept
    res = import_csv(conn, tbl, text, _who(u))
    return {"ok": True, "table": tbl, **res, "counts": approvals.counts(conn, tbl)}


@router.get("/datasets/{tbl}/export.csv", summary="The whole table as CSV, every column")
def export_csv(tbl: str, u: dict = Depends(require_admin)):
    conn = _conn()
    _require_tbl(conn, tbl)
    cols = _table_cols(conn, tbl)
    cols = ["_row_id", *[c for c in cols if c != "_row_id"]]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(cols)
    for r in conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{tbl}"'):
        w.writerow(["" if v is None else v for v in r])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{tbl}.csv"'})


# ===================================================================================
# Usage
# ===================================================================================
def _newsletter(conn) -> list[dict]:
    conn.execute("CREATE TABLE IF NOT EXISTS newsletter (email TEXT PRIMARY KEY, name TEXT, created_at TEXT, source TEXT)")
    return _rows(conn.execute("SELECT email, name, created_at, source FROM newsletter ORDER BY created_at DESC"))


def subscribers_csv_text(conn) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["email", "name", "created_at", "source"])
    for r in _newsletter(conn):
        w.writerow([r["email"], r["name"] or "", r["created_at"], r["source"]])
    return buf.getvalue()


@router.get("/analytics", summary="Usage over the last 7, 30 or 90 days")
def get_analytics(days: int = Query(30), u: dict = Depends(require_admin)):
    from central import analytics
    if days not in (7, 30, 90):
        raise HTTPException(400, "days is 7, 30 or 90")
    conn = _conn()
    s = analytics.summary(conn, days)
    subs = _newsletter(conn)
    return {
        "days": days, "views": int(s["views"] or 0), "visitors": int(s["visitors"] or 0),
        "submissions": sum(int(n) for e, n in s["by_event"] if str(e).startswith("submit")),
        "by_day": [{"day": d, "views": int(n), "visitors": int(v)} for d, n, v in s["by_day"]],
        "by_surface": [{"surface": a, "views": int(n), "visitors": int(v)} for a, n, v in s["by_surface"]],
        "by_view": [{"view": a, "views": int(n)} for a, n in s["by_view"]],
        "by_event": [{"event": a, "count": int(n)} for a, n in s["by_event"]],
        "by_country": [{"country": a, "visitors": int(v)} for a, v in s["by_country"]],
        "newsletter": {"count": len(subs), "latest": subs[0]["created_at"] if subs else None},
    }


@router.get("/subscribers.csv", summary="The newsletter list as CSV")
def get_subscribers(u: dict = Depends(require_admin)):
    return Response(subscribers_csv_text(_conn()), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="subscribers.csv"'})
