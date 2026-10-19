"""
api.me — the signed-in person's own pieces
==========================================
The write side the capture apps need. Everything here is scoped to the caller
(``require_user``): their Glowtbook objects, a quick-create, saving a
fingerprint straight onto a piece, and recording provenance events (a
verification, an acquisition). Pieces live in the per-user Glowtbook store;
their public state is resolved against the central registry.

  GET  /me                              who am I (200 even when anonymous)
  GET  /me/objects                      my pieces, with fingerprint + publication state
  POST /me/objects                      quick-create {title, maker?, year?, techniques?}
  GET  /me/objects/{id}                 one piece (+ events)
  GET  /me/objects/{id}/fingerprint     the raw fingerprint (verify.html?mine=<id>)
  POST /me/objects/{id}/fingerprint     body = the enroll export (.zip) or fingerprint.json
                                        ?set_dimensions=1 copies mat-measured W×D×H onto the piece
  POST /me/objects/{id}/events          {event_type, event_date?, actor?, location?, note?}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import _api_prefix, current_user, require_user  # noqa: E402
from central.dbconn import connect  # noqa: E402
from glowtbook import store as gstore  # noqa: E402

router = APIRouter(prefix="/me", tags=["me"])
EVENT_TYPES = {"created", "acquired", "exhibited", "appraised", "sold", "loaned", "damaged",
               "restored", "verified", "claimed", "entered", "other"}


def _store():
    return gstore.open_store()


def _central():
    try:
        return connect()
    except Exception:  # noqa: BLE001
        return None


def _own(store, uid, oid):
    o = gstore.get_object(store, uid, oid)
    if not o:
        raise HTTPException(404, "No such piece in your registry")
    return o


@router.get("", summary="Who am I")
def me(request: Request):
    u = current_user(request)
    if not u:
        return {"signed_in": False, "login": f"{_api_prefix(request)}/auth/login"}
    store = _store(); central = _central()
    objs = gstore.list_objects(store, central, u["uid"])
    is_admin = False
    try:
        from central import users
        is_admin = central is not None and users.is_admin(central, u.get("email", ""))
    except Exception:  # noqa: BLE001
        pass
    return {"signed_in": True, "uid": u["uid"], "email": u.get("email", ""), "name": u.get("name", ""),
            "kind": u.get("kind"), "expires": u.get("exp"), "is_admin": is_admin,
            "counts": {"objects": len(objs),
                       "fingerprinted": sum(1 for o in objs if o["fingerprint"]),
                       "published": sum(1 for o in objs if o["publication"]["state"] == "published"),
                       "pending": sum(1 for o in objs if o["publication"]["state"] == "pending")}}


@router.get("/objects", summary="My pieces")
def my_objects(u: dict = Depends(require_user)):
    return {"objects": gstore.list_objects(_store(), _central(), u["uid"])}


@router.post("/objects", summary="Quick-create a piece (from the capture app)")
def create(payload: dict, u: dict = Depends(require_user)):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    techs = payload.get("techniques") or []
    if isinstance(techs, str):
        techs = [t for t in techs.split("|") if t]
    from glowtbook.contribute import create_object
    store = _store()
    oid = create_object(store, u["uid"], title=title[:200], maker=(payload.get("maker") or "")[:200],
                        year=str(payload.get("year") or "")[:20], techniques="|".join(techs),
                        materials=(payload.get("materials") or "")[:200], dimensions="",
                        description=(payload.get("description") or "")[:2000])
    o = _own(store, u["uid"], oid)
    return gstore.object_card(store, _central(), u["uid"], o)


@router.get("/objects/{oid}", summary="One of my pieces")
def one(oid: int, u: dict = Depends(require_user)):
    store = _store()
    o = _own(store, u["uid"], oid)
    card = gstore.object_card(store, _central(), u["uid"], o)
    card["events_list"] = [dict(r) for r in store.execute(
        "SELECT event_type,event_date,actor,location,note,created_at FROM prov_event "
        "WHERE object_id=? AND user_id=? ORDER BY event_date, id", (oid, u["uid"])).fetchall()]
    return card


@router.get("/objects/{oid}/fingerprint", summary="The raw fingerprint of one of my pieces")
def my_fingerprint(oid: int, u: dict = Depends(require_user)):
    o = _own(_store(), u["uid"], oid)
    if not o["fingerprint_json"]:
        raise HTTPException(404, "No fingerprint on this piece yet")
    return json.loads(o["fingerprint_json"])


@router.post("/objects/{oid}/fingerprint", summary="Save a fingerprint onto one of my pieces")
async def save_fingerprint(oid: int, request: Request, u: dict = Depends(require_user),
                           set_dimensions: int = Query(0, ge=0, le=1)):
    """Body: the capture app's export — a .zip (fingerprint.json + frame thumbnails)
    or the fingerprint.json alone. Thumbnails are used once, for mat measurement,
    and are not stored in the registry (descriptors only)."""
    store = _store()
    _own(store, u["uid"], oid)
    ctype = (request.headers.get("content-type") or "").lower()
    data = b""
    if ctype.startswith("multipart/"):
        try:
            form = await request.form()
            f = form.get("file") or next((v for v in form.values() if hasattr(v, "read")), None)
            data = await f.read() if f is not None else b""
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(400, f"couldn't read the upload: {ex}")
    else:
        data = await request.body()
    if not data:
        raise HTTPException(400, "empty body — send the enroll .zip or fingerprint.json")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "export too large (40 MB max)")
    from glowtbook import fingerprint as _fp
    r = _fp.load_enrollment(data)
    if not r.get("ok"):
        raise HTTPException(400, f"couldn't read that export: {r.get('error')}")
    gstore.set_fingerprint(store, u["uid"], oid, r["fingerprint"])
    dims = r.get("dimensions")
    dims_txt = dims.get("dimensions") if dims and dims.get("ok") else None
    if dims_txt and set_dimensions:
        gstore.set_dimensions(store, u["uid"], oid, dims_txt)
    try:
        from central import analytics
        c = _central()
        if c is not None:
            analytics.log(c, "fingerprint", "enroll", "fingerprint:save")
    except Exception:  # noqa: BLE001
        pass
    card = gstore.object_card(store, _central(), u["uid"], _own(store, u["uid"], oid))
    return {"ok": True, "rating": r["rating"], "tier": r["tier"], "views": r["n_frames"],
            "dimensions": dims_txt, "dimensions_set": bool(dims_txt and set_dimensions),
            "object": card}


@router.post("/objects/{oid}/events", summary="Add a provenance event to one of my pieces")
def add_event(oid: int, payload: dict, u: dict = Depends(require_user)):
    store = _store()
    _own(store, u["uid"], oid)
    et = (payload.get("event_type") or "other").strip().lower()
    if et not in EVENT_TYPES:
        raise HTTPException(400, f"event_type must be one of {sorted(EVENT_TYPES)}")
    from glowtbook.contribute import add_event as _add
    _add(store, oid, u["uid"], event_type=et, event_date=str(payload.get("event_date") or "")[:40],
         actor=(payload.get("actor") or u.get("name") or "")[:200],
         location=(payload.get("location") or "")[:200], note=(payload.get("note") or "")[:2000])
    return {"ok": True}
