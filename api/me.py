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
  POST /me/objects/{id}/images          body = one JPEG/PNG/WebP (raw or multipart "file");
                                        ?role=auto|primary|photo|detail (auto: primary if the
                                        piece has none yet) — the capture app sends its best shot
  GET  /me/objects/{id}/image           the piece's top image (owner only)
  POST /me/objects/{id}/publish         {include_value?, sign?, archive?} → the condensed record
                                        goes to the registry (review queue when moderation is on)
Every card carries ``image_url`` (null until the piece has a top image).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

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


IMAGE_ROLES = ("primary", "photo", "detail")


def _primary_image(store, uid: str, oid: int):
    """The piece's top image: the ``primary`` one, else the first photo whose file is still there."""
    rows = store.execute(
        "SELECT * FROM object_image WHERE object_id=? AND user_id=? AND role!='video' "
        "ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id", (oid, uid)).fetchall()
    for r in rows:
        if r["aip_path"] and Path(r["aip_path"]).exists():
            return r
    return None


def _image_url(request: Request, store, uid: str, card: dict) -> dict:
    card["image_url"] = (f"{_api_prefix(request)}/me/objects/{card['id']}/image"
                         if _primary_image(store, uid, card["id"]) else None)
    return card


def _sniff_image(data: bytes):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None, None


async def _body_bytes(request: Request) -> bytes:
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/"):
        try:
            form = await request.form()
            f = form.get("file") or next((v for v in form.values() if hasattr(v, "read")), None)
            return await f.read() if f is not None else b""
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(400, f"couldn't read the upload: {ex}")
    return await request.body()


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


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


def _with_next(central, uid: str, cards: list[dict]) -> list[dict]:
    """Attach ``next`` (what to do with the piece, as a link) to each card —
    the same words the Glowtbook home table shows (``glowtbook.store.next_step``)."""
    season, claims, entered = None, {}, set()
    if central is not None:
        try:
            from central import seasons
            seasons.ensure(central)
            season = seasons.current_season(central)
            claims = seasons.claim_counts(central, [c["publication"].get("row_id") for c in cards])
            if season:
                entered = {e["object_row_id"] for e in seasons.my_entries(central, uid)
                           if e.get("season_id") == season["id"]}
        except Exception:  # noqa: BLE001
            season, claims, entered = None, {}, set()
    for c in cards:
        c["next"] = gstore.next_step(c, season=season,
                                     claims=claims.get(c["publication"].get("row_id")), entered=entered)
        c["url"] = gstore.piece_url(c["id"])
    return cards


@router.get("/objects", summary="My pieces")
def my_objects(request: Request, u: dict = Depends(require_user)):
    central = _central(); store = _store()
    cards = _with_next(central, u["uid"], gstore.list_objects(store, central, u["uid"]))
    return {"objects": [_image_url(request, store, u["uid"], c) for c in cards]}


@router.post("/objects", summary="Quick-create a piece (from the capture app)")
def create(payload: dict, request: Request, u: dict = Depends(require_user)):
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
    return _image_url(request, store, u["uid"], gstore.object_card(store, _central(), u["uid"], o))


@router.get("/objects/{oid}", summary="One of my pieces")
def one(oid: int, request: Request, u: dict = Depends(require_user)):
    store = _store()
    o = _own(store, u["uid"], oid)
    card = _image_url(request, store, u["uid"], gstore.object_card(store, _central(), u["uid"], o))
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
    card = _image_url(request, store, u["uid"],
                      gstore.object_card(store, _central(), u["uid"], _own(store, u["uid"], oid)))
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



@router.post("/objects/{oid}/images", summary="Add a photo to one of my pieces (the capture app's best shot)")
async def upload_image(oid: int, request: Request, u: dict = Depends(require_user),
                       role: str = Query("auto"), caption: str = Query("")):
    """One image per call: raw JPEG/PNG/WebP bytes, or multipart with a ``file`` part.
    ``role=auto`` makes it the top image (``primary``) when the piece has none yet, else a
    ``photo``. A new ``primary`` demotes the old one to ``photo``. The file is kept in the
    piece's archival folder like an upload from Glowtbook's Images tab."""
    store = _store()
    _own(store, u["uid"], oid)
    data = await _body_bytes(request)
    if not data:
        raise HTTPException(400, "empty body — send one image")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "image too large (15 MB max)")
    mime, ext = _sniff_image(data)
    if not mime:
        raise HTTPException(400, "that isn't a JPEG, PNG or WebP image")
    role = (role or "auto").strip().lower()
    if role == "auto":
        role = "photo" if _primary_image(store, u["uid"], oid) else "primary"
    if role not in IMAGE_ROLES:
        raise HTTPException(400, f"role must be one of {IMAGE_ROLES} or auto")
    from glowtbook.contribute import add_image, aip_dir
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = aip_dir(u["uid"], oid) / f"{stamp}-{role}{ext}"
    path.write_bytes(data)
    if role == "primary":
        store.execute("UPDATE object_image SET role='photo' WHERE object_id=? AND user_id=? AND role='primary'",
                      (oid, u["uid"]))
    add_image(store, oid, u["uid"], role=role, aip_path=path, caption=(caption or "")[:200])
    store.commit()
    card = _image_url(request, store, u["uid"],
                      gstore.object_card(store, _central(), u["uid"], _own(store, u["uid"], oid)))
    return {"ok": True, "role": role, "bytes": len(data), "object": card}


@router.get("/objects/{oid}/image", summary="The top image of one of my pieces")
def my_image(oid: int, u: dict = Depends(require_user)):
    store = _store()
    _own(store, u["uid"], oid)
    r = _primary_image(store, u["uid"], oid)
    if not r:
        raise HTTPException(404, "No photo on this piece yet")
    path = Path(r["aip_path"])
    mime = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/jpeg")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=60"})


@router.post("/objects/{oid}/publish", summary="Publish one of my pieces to the registry")
def publish(oid: int, payload: dict, request: Request, u: dict = Depends(require_user)):
    """The same thing as Glowtbook → Publish, in one call: a condensed copy of the piece,
    its story and its Glass ID go to the registry (the review queue when moderation is on),
    its pictures carry Content Credentials when signing is available. Returns the new
    publication state; ``pending`` means an admin sees it next."""
    payload = payload or {}
    store = _store()
    o = _own(store, u["uid"], oid)
    if not (o["title"] or "").strip():
        raise HTTPException(400, "give the piece a title first")
    imgs = store.execute("SELECT * FROM object_image WHERE object_id=? AND user_id=?", (oid, u["uid"])).fetchall()
    evs = store.execute("SELECT * FROM prov_event WHERE object_id=? AND user_id=? ORDER BY event_date, id",
                        (oid, u["uid"])).fetchall()
    from glowtbook import c2pa_sign
    from glowtbook.contribute import contribute_object
    sign = bool(payload.get("sign", True)) and c2pa_sign.available()
    display = u.get("name") or u.get("email") or "a Glowtbook user"
    try:
        manifest, _primary = contribute_object(
            u["uid"], display, o, evs,
            [{"aip_path": im["aip_path"], "role": im["role"], "caption": im["caption"]}
             for im in imgs if im["aip_path"] and Path(im["aip_path"]).exists()],
            bool(payload.get("include_value")), sign=sign, object_id=oid,
            archive_aip=bool(payload.get("archive")), push_minio=False, base_url=_public_base(request))
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(500, f"publishing failed: {ex}")
    store.execute("INSERT INTO contribution (user_id,object_id,content_hash,manifest_json) VALUES (?,?,?,?)",
                  (u["uid"], oid, manifest["content_hash"], json.dumps(manifest)))
    store.commit()
    try:
        from central import analytics
        c = _central()
        if c is not None:
            analytics.log(c, "glowtbook", "publish", "object:publish:api")
    except Exception:  # noqa: BLE001
        pass
    card = _image_url(request, store, u["uid"],
                      gstore.object_card(store, _central(), u["uid"], _own(store, u["uid"], oid)))
    return {"ok": True, "pending": bool(manifest.get("_pending")), "content_hash": manifest["content_hash"],
            "signed": bool(manifest.get("_signed")), "sign_error": manifest.get("_sign_error"),
            "images": len(imgs), "events": len(evs), "object": card}
