"""
api.admin — the review queue, for the static admin pages
=========================================================
What the Streamlit console's moderation sections do, as API routes, so the website's
own Review queue page (``/review.html``) can do the reviewing. Every route needs an
administrator (``api.auth.require_admin``: a signed-in admin email, or the
``X-API-Key`` admin token). Thin wrappers: the decisions call the same functions the
console calls, nothing is decided here.

  GET  /admin/queue                      every queue's pending rows + counts
  POST /admin/decide                     {queue, id, action: approve|reject, note?}
  POST /admin/approve-all                {tbl} — a whole dataset's pending rows
  GET  /admin/submissions/{id}/image     a pending piece's picture   (?i=0)
  GET  /admin/registrations/{id}/image   a show registration's picture
  GET  /admin/harvest/{id}/image         a harvested picture
  GET  /admin/users · POST /admin/users  {email, admin}
  GET  /admin/discord · POST /admin/discord {webhook?, enabled?} · POST /admin/discord/test

Queues (the ``queue`` word ``POST /admin/decide`` takes): ``object_submissions``
(pieces), ``submissions`` (artist profiles), ``listing`` (a show or sale),
``registration`` (a piece bought at one), ``entry`` and ``claim`` (collecting events),
``harvest``, ``studio_source``, ``feedback`` — or the name of any dataset table, with
the row's ``_row_id`` as ``id``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import require_admin  # noqa: E402
from central import approvals, settings, users  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(prefix="/admin", tags=["admin"])

QUEUES = ("object_submissions", "submissions", "listing", "registration", "entry", "claim",
          "harvest", "studio_source", "feedback")


# --- helpers ----------------------------------------------------------------------
def _conn():
    c = connect()
    approvals.ensure_approvals(c)
    return c


def _has_table(conn, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _who(u: dict) -> str:
    return (u.get("email") or u.get("name") or "admin")[:120]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _image(data: bytes | None) -> Response:
    if not data:
        raise HTTPException(404, "no picture")
    return Response(content=data, media_type=_sniff(data), headers={"Cache-Control": "private, max-age=300"})


def _dataset_tables(conn) -> list[dict]:
    """The registry's tables that go through the publication gate (they have a _row_id)."""
    if not _has_table(conn, "_datasets"):
        return []
    out = []
    for d in _rows(conn.execute("SELECT tbl, domain, visibility, description FROM _datasets ORDER BY domain, tbl")):
        if not _has_table(conn, d["tbl"]):
            continue
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{d["tbl"]}")')}
        if "_row_id" in cols:
            out.append(d)
    return out


def _public_columns(conn, tbl: str) -> list[dict]:
    return _rows(conn.execute("SELECT column, label FROM _columns WHERE tbl=? AND is_public=1 ORDER BY ordinal",
                              (tbl,)))


# --- the queues --------------------------------------------------------------------
def _object_submissions(conn) -> tuple[dict, list[dict]]:
    if not _has_table(conn, "object_submissions"):
        return {}, []
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM object_submissions GROUP BY status").fetchall())
    rows = _rows(conn.execute(
        "SELECT id, content_hash, submitted_by, title, maker, year, techniques, materials, dimensions, "
        "description, sourcing, value_display, has_credentials, received_at, status "
        "FROM object_submissions WHERE status='pending' ORDER BY received_at DESC"))
    have_images = _has_table(conn, "object_submission_images")
    for r in rows:
        r["images"] = (conn.execute("SELECT COUNT(*) FROM object_submission_images WHERE submission_id=?",
                                    (r["id"],)).fetchone()[0] if have_images else 0)
        r["image_url"] = f"/api/admin/submissions/{r['id']}/image" if r["images"] else None
    return counts, rows


def _profile_submissions(conn) -> tuple[dict, list[dict]]:
    if not _has_table(conn, "submissions"):
        return {}, []
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM submissions GROUP BY status").fetchall())
    rows = _rows(conn.execute("SELECT id, content_hash, display_name, payload_json, status, sourcing, received_at "
                              "FROM submissions WHERE status='pending_review' ORDER BY received_at DESC"))
    for r in rows:
        try:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        except ValueError:
            r["payload"] = {}
            r.pop("payload_json", None)
    return counts, rows


def _datasets(conn) -> list[dict]:
    out = []
    for d in _dataset_tables(conn):
        c = approvals.counts(conn, d["tbl"])
        cols = _public_columns(conn, d["tbl"])
        rows = []
        if c["pending"]:
            rows = [dict(r) for r in approvals.pending_rows(conn, d["tbl"], [x["column"] for x in cols], limit=50)]
        out.append({**d, "counts": c, "columns": cols, "rows": rows})
    return out


def _events(conn) -> tuple[list[dict], list[dict]]:
    from central import events
    events.ensure(conn)
    listings = events.pending_listings(conn)
    for ev in listings:
        ev.pop("host_email", None)
    regs = []
    for r in events.pending_admin(conn):
        r.pop("image_b64", None)
        r["image_url"] = f"/api/admin/registrations/{r['id']}/image" if r.get("has_image") else None
        regs.append(r)
    return listings, regs


def _seasons(conn) -> tuple[list[dict], list[dict]]:
    from central import seasons
    seasons.ensure(conn)
    entries = seasons.pending_entries(conn)
    claims = seasons.pending_claims(conn)
    for c in claims:
        c.pop("report_json", None)
    return entries, claims


def _harvest(conn) -> tuple[dict, list[dict]]:
    from central import harvest
    rows = harvest.list_items(conn, "pending")
    for r in rows:
        r.pop("manifest_json", None)
        r["image_url"] = f"/api/admin/harvest/{r['id']}/image"
    return harvest.counts(conn), rows


def _studio_sources(conn) -> list[dict]:
    from central import studio_sources
    rows = studio_sources.list_sources(conn, "pending")
    for r in rows:
        ok, why = studio_sources.url_ok(r.get("api_url") or "")
        r["url_ok"], r["url_note"] = ok, why
    return rows


def _feedback(conn) -> list[dict]:
    from central import feedback
    return feedback.open_items(conn)


def queue(conn, reviewer: str = "") -> dict:
    os_counts, os_rows = _object_submissions(conn)
    pr_counts, pr_rows = _profile_submissions(conn)
    datasets = _datasets(conn)
    listings, regs = _events(conn)
    entries, claims = _seasons(conn)
    hv_counts, hv_rows = _harvest(conn)
    sources = _studio_sources(conn)
    fb = _feedback(conn)
    counts = {
        "object_submissions": len(os_rows), "submissions": len(pr_rows),
        "datasets": sum(d["counts"]["pending"] for d in datasets),
        "listings": len(listings), "registrations": len(regs),
        "entries": len(entries), "claims": len(claims), "harvest": len(hv_rows),
        "studio_sources": len(sources), "feedback": sum(1 for f in fb if not f.get("resolved")),
    }
    counts["total"] = sum(counts.values())
    return {"counts": counts, "reviewer": reviewer,
            "object_submissions": os_rows, "object_submission_counts": os_counts,
            "submissions": pr_rows, "submission_counts": pr_counts,
            "datasets": datasets, "listings": listings, "registrations": regs,
            "entries": entries, "claims": claims, "harvest": hv_rows, "harvest_counts": hv_counts,
            "studio_sources": sources, "feedback": fb}


# --- artist profile promotion (what the console's Review queue does on approve) ----
PROFILE_COLS = ["display_name", "website", "nationality_base", "status",
                "techniques", "contributions", "career_highlights", "approved_at"]


def _ensure_profiles(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        _row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, _imported_at TEXT,
        display_name TEXT, website TEXT, nationality_base TEXT, status TEXT,
        techniques TEXT, contributions TEXT, career_highlights TEXT, approved_at TEXT)""")
    n = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    conn.execute("""INSERT OR REPLACE INTO _datasets
        (tbl, domain, source_file, source_sheet, visibility, row_count, description, updated_at)
        VALUES ('profiles','artists','glowtbook','contributions','public',?,
                'Artist-contributed profiles, approved from Glowtbook submissions',?)""", (n, _now()))
    for i, col in enumerate(PROFILE_COLS):
        conn.execute("""INSERT OR REPLACE INTO _columns (tbl, column, label, ordinal, is_public)
                        VALUES ('profiles',?,?,?,1)""", (col, col.replace("_", " ").title(), i))
    conn.commit()


def _promote_profile(conn, sub: dict, reviewer: str) -> str:
    payload = json.loads(sub.get("payload_json") or "{}")
    _ensure_profiles(conn)
    now = _now()
    rid = hashlib.sha1(("profiles|" + (sub.get("content_hash") or str(sub["id"]))).encode()).hexdigest()[:16]
    conn.execute("""INSERT OR REPLACE INTO profiles
        (_row_id,_source_file,_source_sheet,_imported_at,display_name,website,
         nationality_base,status,techniques,contributions,career_highlights,approved_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, "glowtbook", "contributions", now,
         payload.get("display_name", ""), payload.get("website", ""),
         payload.get("nationality_base", ""), payload.get("status", ""),
         ", ".join(payload.get("techniques", []) or []),
         ", ".join(payload.get("contributions", []) or []),
         payload.get("career_highlights", ""), now))
    conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM profiles) WHERE tbl='profiles'")
    conn.execute("UPDATE submissions SET status='approved' WHERE id=?", (sub["id"],))
    conn.commit()
    approvals.set_status(conn, "profiles", [rid], "approved", reviewer=reviewer)
    return rid


# --- deciding ----------------------------------------------------------------------
def decide(conn, queue_name: str, item_id, approve: bool, reviewer: str, note: str = "") -> dict:
    """Apply one decision. Returns a small dict describing what changed."""
    q = (queue_name or "").strip()
    if q == "object_submissions":
        if not _has_table(conn, "object_submissions"):
            raise HTTPException(404, "no such submission")
        sid = int(item_id)
        if approve:
            from glowtbook.contribute import promote_object_submission
            rid = promote_object_submission(conn, sid)
            if not rid:
                raise HTTPException(404, "no such submission")
            return {"published": rid, "url": f"/explore/?view=objects#obj-{rid}"}
        conn.execute("UPDATE object_submissions SET status='rejected', review_note=? WHERE id=?",
                     ((note or "")[:500], sid))
        conn.commit()
        return {"rejected": sid}
    if q == "submissions":
        if not _has_table(conn, "submissions"):
            raise HTTPException(404, "no such submission")
        sub = conn.execute("SELECT * FROM submissions WHERE id=?", (int(item_id),)).fetchone()
        if not sub:
            raise HTTPException(404, "no such submission")
        if approve:
            return {"published": _promote_profile(conn, dict(sub), reviewer)}
        conn.execute("UPDATE submissions SET status='rejected' WHERE id=?", (int(item_id),))
        conn.commit()
        return {"rejected": int(item_id)}
    if q == "listing":
        from central import events
        if not events.get_event(conn, int(item_id)):
            raise HTTPException(404, "no such show or sale")
        events.set_listing(conn, int(item_id), "approved" if approve else "rejected")
        return {"listing": "approved" if approve else "rejected"}
    if q == "registration":
        from central import events
        done = events.admin_review(conn, [int(item_id)], approve, reviewer=reviewer, note=note)
        if not done:
            raise HTTPException(404, "no registration waiting with that id")
        return {"published": done[0].get("object_row_id")} if approve else {"rejected": int(item_id)}
    if q == "entry":
        from central import seasons
        try:
            seasons.approve_entry(conn, int(item_id), approve)
        except ValueError as ex:
            raise HTTPException(404, str(ex))
        return {"entry": "approved" if approve else "rejected"}
    if q == "claim":
        from central import seasons
        try:
            seasons.set_claim_status(conn, int(item_id), "approved" if approve else "rejected")
        except ValueError as ex:
            raise HTTPException(404, str(ex))
        return {"claim": "approved" if approve else "rejected"}
    if q == "harvest":
        from central import harvest
        harvest.set_status(conn, int(item_id), "approved" if approve else "rejected")
        return {"harvest": "approved" if approve else "rejected"}
    if q == "studio_source":
        from central import studio_sources
        studio_sources.set_status(conn, int(item_id), "approved" if approve else "rejected")
        return {"studio_source": "approved" if approve else "rejected"}
    if q == "feedback":
        from central import feedback
        feedback.resolve(conn, str(item_id), approve)
        return {"feedback": "resolved" if approve else "reopened"}
    # a dataset table: the publication gate
    tables = {d["tbl"] for d in _dataset_tables(conn)}
    if q not in tables:
        raise HTTPException(400, f"unknown queue {q!r}; one of {', '.join(QUEUES)} or a dataset table")
    rid = str(item_id)
    if not conn.execute(f'SELECT 1 FROM "{q}" WHERE _row_id=?', (rid,)).fetchone():
        raise HTTPException(404, f"no row {rid} in {q}")
    status = "approved" if approve else "rejected"
    approvals.set_status(conn, q, [rid], status, reviewer=reviewer)
    return {"table": q, "row": rid, "status": status}


# --- routes --------------------------------------------------------------------------
@router.get("/queue", summary="Everything waiting for a reviewer")
def get_queue(u: dict = Depends(require_admin)):
    return queue(_conn(), _who(u))


@router.post("/decide", summary="Approve or reject one waiting item")
def post_decide(payload: dict, u: dict = Depends(require_admin)):
    payload = payload or {}
    action = str(payload.get("action") or "").lower()
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if payload.get("id") in (None, ""):
        raise HTTPException(400, "id is required")
    conn = _conn()
    result = decide(conn, str(payload.get("queue") or ""), payload["id"], action == "approve",
                    _who(u), str(payload.get("note") or ""))
    return {"ok": True, "result": result, "counts": queue(conn)["counts"]}


@router.post("/approve-all", summary="Approve every pending row of a dataset")
def post_approve_all(payload: dict, u: dict = Depends(require_admin)):
    tbl = str((payload or {}).get("tbl") or "")
    conn = _conn()
    if tbl not in {d["tbl"] for d in _dataset_tables(conn)}:
        raise HTTPException(400, f"{tbl!r} is not a dataset table")
    n = approvals.approve_all(conn, tbl, reviewer=_who(u))
    return {"ok": True, "approved": n, "counts": approvals.counts(conn, tbl)}


@router.get("/submissions/{sid}/image", summary="A pending piece's picture")
def submission_image(sid: int, i: int = Query(0, ge=0), u: dict = Depends(require_admin)):
    conn = _conn()
    if not _has_table(conn, "object_submission_images"):
        raise HTTPException(404, "no picture")
    rows = conn.execute("SELECT image_b64 FROM object_submission_images WHERE submission_id=? ORDER BY "
                        "CASE role WHEN 'primary' THEN 0 ELSE 1 END, id", (sid,)).fetchall()
    if i >= len(rows) or not rows[i][0]:
        raise HTTPException(404, "no picture")
    try:
        return _image(base64.b64decode(rows[i][0]))
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "no picture")


@router.get("/registrations/{rid}/image", summary="A show registration's picture")
def registration_image(rid: int, u: dict = Depends(require_admin)):
    from central import events
    conn = _conn()
    events.ensure(conn)
    return _image(events.image_bytes(conn, rid))


@router.get("/harvest/{hid}/image", summary="A harvested picture")
def harvest_image(hid: int, u: dict = Depends(require_admin)):
    from central import harvest
    return _image(harvest.image_bytes(_conn(), hid))


@router.get("/users", summary="Everyone who has signed in, and who is an administrator")
def get_users(u: dict = Depends(require_admin)):
    return {"users": users.list_users(_conn()), "you": u.get("email", "")}


@router.post("/users", summary="Make someone an administrator, or stop")
def post_users(payload: dict, u: dict = Depends(require_admin)):
    email = str((payload or {}).get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "an email address, please")
    admin = bool((payload or {}).get("admin", True))
    conn = _conn()
    if not admin and email in users._boot():
        raise HTTPException(400, f"{email} is set in GLASSDB_ADMIN_EMAILS; change the .env to revoke it")
    users.set_admin(conn, email, admin)
    return {"ok": True, "email": email, "admin": admin, "users": users.list_users(conn)}


def _discord_state(conn) -> dict:
    import os
    url = settings.get(conn, "discord_webhook_url", os.environ.get("DISCORD_WEBHOOK_URL", "")).strip()
    return {"configured": bool(url), "webhook_tail": url[-6:] if url else "",
            "enabled": settings.get(conn, "discord_enabled", "1") != "0"}


@router.get("/discord", summary="Discord notifications: configured, enabled (never the URL)")
def get_discord(u: dict = Depends(require_admin)):
    return _discord_state(_conn())


@router.post("/discord", summary="Set the Discord webhook and the on/off switch")
def post_discord(payload: dict, u: dict = Depends(require_admin)):
    payload = payload or {}
    conn = _conn()
    if "webhook" in payload:
        wh = str(payload.get("webhook") or "").strip()
        if wh and not wh.startswith("https://"):
            raise HTTPException(400, "the webhook is an https URL from Discord's Integrations page")
        settings.set(conn, "discord_webhook_url", wh)
    if "enabled" in payload:
        settings.set(conn, "discord_enabled", "1" if payload.get("enabled") else "0")
    return {"ok": True, **_discord_state(conn)}


@router.post("/discord/test", summary="Send a test message to the channel")
def post_discord_test(payload: dict | None = None, u: dict = Depends(require_admin)):
    from central import notify
    wh = str((payload or {}).get("webhook") or "").strip()
    ok, msg = notify.send_test(wh)
    return {"ok": ok, "message": msg}
