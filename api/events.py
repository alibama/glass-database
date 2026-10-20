"""
api.events — maker events and purchase registration
===================================================
Public
  GET  /events                          listed events (admin-approved listing), newest first
  GET  /events/{slug}                   one event (works unlisted — the link/QR at the table),
                                        approved pieces if the host shows them, can_register
  GET  /events/registrations/{id}/image the piece photo (approved, or yours / your event's)
Signed in (buyer)
  POST /events/{slug}/registrations     register a purchase; also creates the piece in your Glowtbook
  GET  /me/registrations                your registrations with status + a "fingerprint it" link
Signed in (host)
  POST /events                          create an event (you are the host); usable at once
  POST /events/{slug}                   edit your event (fields, open/close)
  GET  /me/events                       your events with per-status counts
  GET  /events/{slug}/registrations     the review queue (?status=pending_host)
  POST /events/{slug}/review            {ids: [...], approve: true|false, note?}  (bulk)
                                        {all: true, approve: true} approves every pending one
Site approval (stage two) lives in the admin console, not here.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import current_user, require_user  # noqa: E402
from central import events as E  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(tags=["events"])
MAX_IMAGE = 8 * 1024 * 1024


def _c():
    c = connect()
    E.ensure(c)
    return c


def _event_or_404(c, slug):
    ev = E.get_event(c, slug)
    if not ev or ev["status"] == "draft":
        raise HTTPException(404, "No such event")
    return ev


def _host_or_403(ev, u):
    if ev["host_uid"] != u["uid"]:
        raise HTTPException(403, "Not your event")


@router.get("/events", summary="Listed maker events")
def list_events(include_closed: int = Query(1, ge=0, le=1)):
    c = _c()
    return {"events": E.list_events(c, include_closed=bool(include_closed))}


@router.get("/events/{slug}", summary="One event (works for unlisted events via their link)")
def one_event(slug: str, request: Request):
    c = _c()
    ev = E.public_event(c, slug)
    if not ev:
        raise HTTPException(404, "No such event")
    u = current_user(request)
    ev["is_host"] = bool(u) and E.get_event(c, slug)["host_uid"] == u["uid"]
    return ev


@router.post("/events", summary="Create an event (you become its host)")
def create_event(payload: dict, u: dict = Depends(require_user)):
    c = _c()
    try:
        ev = E.create_event(c, host_uid=u["uid"], host_name=(payload.get("host_name") or u.get("name") or ""),
                            host_email=u.get("email", ""), title=payload.get("title", ""),
                            **{k: payload.get(k) for k in E.EDITABLE if k in payload and k != "title"})
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    ev.pop("host_email", None)
    ev["register_url"] = f"/register.html?event={ev['slug']}"
    return ev


@router.post("/events/{slug}", summary="Edit your event")
def edit_event(slug: str, payload: dict, u: dict = Depends(require_user)):
    c = _c()
    try:
        ev = E.update_event(c, slug, u["uid"], **{k: payload.get(k) for k in E.EDITABLE if k in payload})
    except ValueError as ex:
        raise HTTPException(403 if "not your" in str(ex) else 400, str(ex))
    ev.pop("host_email", None)
    return ev


@router.get("/me/events", summary="Events you host, with registration counts")
def my_events(u: dict = Depends(require_user)):
    c = _c()
    out = []
    for ev in E.host_events(c, u["uid"]):
        ev.pop("host_email", None)
        ev["register_url"] = f"/register.html?event={ev['slug']}"
        out.append(ev)
    return {"events": out}


@router.get("/events/{slug}/registrations", summary="Your event's registrations (host)")
def event_registrations(slug: str, status: str | None = None, u: dict = Depends(require_user)):
    c = _c()
    ev = _event_or_404(c, slug)
    _host_or_403(ev, u)
    if status and status not in E.STATUSES:
        raise HTTPException(400, f"status must be one of {E.STATUSES}")
    rows = E.registrations(c, ev["id"], status, with_email=True)
    for r in rows:
        r.pop("image_b64", None)
        r["image_url"] = f"/api/events/registrations/{r['id']}/image" if r["has_image"] else None
    return {"event": {k: ev.get(k) for k in E.PUBLIC_EVENT_FIELDS}, "registrations": rows,
            "counts": E._counts(c, ev["id"])}


@router.post("/events/{slug}/review", summary="Host review: approve/reject registrations (bulk)")
def host_review(slug: str, payload: dict, u: dict = Depends(require_user)):
    c = _c()
    ev = _event_or_404(c, slug)
    _host_or_403(ev, u)
    approve = bool(payload.get("approve", True))
    try:
        if payload.get("all"):
            n = E.host_approve_all(c, ev["id"], u["uid"]) if approve else \
                E.host_review(c, ev["id"], u["uid"],
                              [r["id"] for r in E.registrations(c, ev["id"], "pending_host")], False,
                              payload.get("note", ""))
        else:
            n = E.host_review(c, ev["id"], u["uid"], payload.get("ids") or [], approve, payload.get("note", ""))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, "changed": n, "counts": E._counts(c, ev["id"]),
            "next": "Approved pieces go to the site's review queue before they're published." if approve else None}


@router.post("/events/{slug}/registrations", summary="Register a purchase from this event")
async def register(slug: str, request: Request, u: dict = Depends(require_user)):
    c = _c()
    ev = _event_or_404(c, slug)
    payload = await request.json()
    img_b64 = None
    raw = payload.get("image_b64")
    if raw:
        if "," in raw[:80] and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            data = base64.b64decode(raw)
        except Exception:
            raise HTTPException(400, "image_b64 isn't valid base64")
        if len(data) > MAX_IMAGE:
            raise HTTPException(413, "image too large (8 MB max)")
        try:
            from glowtbook import media
            cond, _ = media.condense_image(data)
            img_b64 = base64.b64encode(cond).decode()
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(400, f"couldn't read the image: {ex}")
    # the piece also lands in the buyer's own Glowtbook, so it can be fingerprinted later
    object_id = None
    try:
        from glowtbook import store as gstore
        from glowtbook.contribute import add_event, create_object
        s = gstore.open_store()
        maker = ev.get("host_name") or ev.get("studio") or ""
        object_id = create_object(s, u["uid"], title=(payload.get("title") or "")[:200], maker=maker,
                                  year=(ev.get("starts") or "")[:4], techniques="",
                                  materials="glass", dimensions="",
                                  description=(payload.get("description") or "")[:2000])
        d = (ev.get("starts") or "")[:10]
        add_event(s, object_id, u["uid"], event_type="created", event_date=d, actor=maker,
                  location=ev.get("venue") or "", note=f"Made for {ev['title']}")
        add_event(s, object_id, u["uid"], event_type="acquired", event_date=d, actor=u.get("name") or "",
                  location=ev.get("venue") or "", note=f"Bought at {ev['title']}")
    except Exception:  # noqa: BLE001
        object_id = None
    try:
        reg = E.register(c, ev["slug"], buyer_uid=u["uid"], buyer_name=u.get("name") or u.get("email") or "",
                         buyer_email=u.get("email", ""), title=payload.get("title", ""),
                         description=payload.get("description", ""), piece_ref=payload.get("piece_ref", ""),
                         price_paid=payload.get("price_paid", ""), image_b64=img_b64,
                         show_name=bool(payload.get("show_name")), object_id=object_id)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    out = E._shape(reg, private=True)
    out["fingerprint_url"] = f"/fingerprint/enroll.html?object={object_id}" if object_id else None
    out["glowtbook_url"] = f"/glowtbook/?page=objects&object={object_id}" if object_id else "/glowtbook/"
    out["next"] = ("Registered — the host reviews it first, then the site. " +
                   ("Fingerprint it so it can be verified later." if ev.get("fingerprinting") != "none" else ""))
    return out


@router.get("/me/registrations", summary="Your registered purchases")
def my_registrations(u: dict = Depends(require_user)):
    c = _c()
    rows = E.my_registrations(c, u["uid"])
    for r in rows:
        r["image_url"] = f"/api/events/registrations/{r['id']}/image" if r["has_image"] else None
        r["fingerprint_url"] = f"/fingerprint/enroll.html?object={r['object_id']}" if r.get("object_id") else None
        r["glowtbook_url"] = f"/glowtbook/?page=objects&object={r['object_id']}" if r.get("object_id") else None
    return {"registrations": rows}


@router.get("/events/registrations/{reg_id}/image", summary="A registration's photo")
def registration_image(reg_id: int, request: Request):
    c = _c()
    r = E.get_registration(c, reg_id)
    if not r:
        raise HTTPException(404, "No such registration")
    u = current_user(request)
    allowed = r["status"] == "approved" or (u and u["uid"] in (r["buyer_uid"], r["host_uid"]))
    if not allowed:
        raise HTTPException(404, "No such registration")
    data = E.image_bytes(c, reg_id)
    if not data:
        raise HTTPException(404, "No image")
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=600"})
