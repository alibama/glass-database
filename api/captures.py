"""
api.captures — Glass ID lookup, candidates and capture-session manifests
========================================================================
The server side of the capture pages' lookup-and-compare flow and of *Send for
analysis*. The arithmetic is ``central.glassid`` (the page's own matching rules in
Python, run against every registered piece); the storage is ``central.captures``.

Lookup and compare (signed in or not; a person's own pieces only when signed in)
  GET  /glassid/lookup?q=               registered pieces by Glass ID (record id), title,
                                        maker or the words on the record; plus mine
  GET  /glassid/recent                  my latest capture sessions, my latest registered
                                        pieces, the newest public pieces with a Glass ID
  GET  /glassid/pieces/{ref}            the reference bundle for the compare panel:
                                        words, every photo, the Glass ID summary
                                        (``public:<row_id>`` or ``mine:<id>``)
  POST /glassid/candidates              {fingerprint, k?, descriptor?, exclude?} -> the
                                        registered pieces most like this capture, each
                                        with its confidence, verdict, breakdown by
                                        descriptor, the views that carried it and the
                                        reasons in words
  POST /glassid/compare                 {fingerprint, ref} -> the same for one piece

Capture-session manifests (the person who sent one, and administrators)
  POST /me/captures                     body = the session zip (manifest.json + images/…)
                                        or the manifest JSON alone
  GET  /me/captures                     my sessions, newest first
  GET  /me/captures/{mid}               one of mine, with the manifest
  GET  /me/captures/{mid}/files/{name}  one of its pictures (my own registration frames,
                                        beside a later check)
  POST /me/captures/{mid}/decision      {decision, note?, object?} - what I decided,
                                        also after the fact
  GET  /admin/captures?kind=&decision=&q=&since=&limit=&offset=   every session
  GET  /admin/captures/{mid}            one, with the manifest and its files
  GET  /admin/captures/{mid}/manifest.json
  GET  /admin/captures/{mid}/files/{name}
  GET  /admin/captures/{mid}/download   the whole session as a zip
  POST /admin/captures/{mid}/decision   an administrator's annotation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import current_user, is_admin_request, require_admin, require_user  # noqa: E402
from central import captures, glassid  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(tags=["glass id"])
MAX_K = 20
MAX_BODY = captures.MAX_ZIP


def _central():
    try:
        return connect()
    except Exception:  # noqa: BLE001
        return None


def _store():
    from glowtbook import store as gstore
    return gstore.open_store()


def _who(u: dict | None) -> str:
    if not u:
        return ""
    return (u.get("email") or u.get("name") or u.get("uid") or "")[:120]


async def _body(request: Request) -> bytes:
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/"):
        try:
            form = await request.form()
            f = form.get("file") or next((v for v in form.values() if hasattr(v, "read")), None)
            return await f.read() if f is not None else b""
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(400, f"couldn't read the upload: {ex}")
    return await request.body()


def _fingerprint_of(payload: dict) -> dict:
    fp = payload.get("fingerprint") if isinstance(payload, dict) else None
    if isinstance(fp, str):
        try:
            fp = json.loads(fp)
        except Exception:
            fp = None
    if not isinstance(fp, dict) or not isinstance(fp.get("frames"), list) or not fp["frames"]:
        raise HTTPException(400, "send {fingerprint: {frames: [...]}} - the capture's descriptors "
                                 "(chist / dhash / emb per frame), no pictures needed")
    if len(fp["frames"]) > 400:
        raise HTTPException(413, "too many frames (400 at most)")
    return fp


# ===================================================================================
# Lookup and compare
# ===================================================================================
@router.get("/glassid/lookup", summary="Find registered pieces by Glass ID, title or maker")
def lookup(request: Request, q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=50)):
    u = current_user(request)
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    out = glassid.lookup(conn, _store() if u else None, u["uid"] if u else None, q, limit=limit)
    out["signed_in"] = bool(u)
    return out


@router.get("/glassid/recent", summary="What to reach for: my sessions, my pieces, the newest public pieces")
def recent(request: Request, limit: int = Query(8, ge=1, le=30)):
    u = current_user(request)
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    out = glassid.recent(conn, _store() if u else None, u["uid"] if u else None, limit=limit)
    out["signed_in"] = bool(u)
    return out


@router.get("/glassid/pieces/{ref}", summary="The reference bundle for the compare panel")
def piece(ref: str, request: Request):
    u = current_user(request)
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    if ref.startswith("mine:") and not u:
        raise HTTPException(401, "Sign in to see your own pieces")
    p = glassid.piece(conn, _store() if u else None, u["uid"] if u else None, ref)
    if not p:
        raise HTTPException(404, "No such registered piece (or not yours)")
    return p


@router.post("/glassid/candidates", summary="The registered pieces most like this capture, with the reasons")
def candidates(payload: dict, request: Request):
    """Lookup by photo. The page sends the capture's descriptors; the server scores them
    against every public piece with a Glass ID (and the caller's own, when signed in)
    with the same rules Check a Piece uses, and returns the best ``k`` with their
    confidence, verdict, per-descriptor breakdown, the views that carried the score,
    the mat size check and the reasons in words. Features, not verdicts: the person
    decides, and the decision travels in the session manifest."""
    fp = _fingerprint_of(payload)
    k = payload.get("k") or 5
    try:
        k = max(1, min(MAX_K, int(k)))
    except (TypeError, ValueError):
        k = 5
    desc = payload.get("descriptor")
    desc = desc if desc in glassid.DESCRIPTORS else None
    exclude = payload.get("exclude") or []
    if not isinstance(exclude, list):
        exclude = [exclude]
    u = current_user(request)
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    store = _store() if u else None
    refs = glassid.all_refs(conn, store, u["uid"] if u else None, exclude=[str(x) for x in exclude][:50])
    out = glassid.candidates(fp, refs, k=k, descriptor=desc)
    out["corpus"] = {"public": sum(1 for r in refs if r["kind"] == "public"),
                     "mine": sum(1 for r in refs if r["kind"] == "mine"), "signed_in": bool(u)}
    try:
        from central import analytics
        analytics.log(conn, "fingerprint", "candidates", "glassid:candidates")
    except Exception:  # noqa: BLE001
        pass
    return out


@router.post("/glassid/compare", summary="This capture against one registered piece, side by side")
def compare(payload: dict, request: Request):
    fp = _fingerprint_of(payload)
    ref = str(payload.get("ref") or "")
    u = current_user(request)
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    if ref.startswith("mine:") and not u:
        raise HTTPException(401, "Sign in to compare against your own pieces")
    store = _store() if u else None
    target = next((r for r in glassid.all_refs(conn, store, u["uid"] if u else None) if r["ref"] == ref), None)
    if not target:
        raise HTTPException(404, "No such registered piece (or not yours)")
    desc = payload.get("descriptor")
    out = glassid.compare(fp, target["fingerprint"], descriptor=desc if desc in glassid.DESCRIPTORS else None)
    out["piece"] = {k: v for k, v in target.items() if k != "fingerprint"}
    out["piece"]["fingerprint"] = glassid.summary(target["fingerprint"])
    return out


# ===================================================================================
# Capture-session manifests: the person's side
# ===================================================================================
@router.post("/me/captures", summary="Send a capture session for analysis (the zip the page builds)")
async def upload(request: Request, u: dict = Depends(require_user)):
    data = await _body(request)
    if not data:
        raise HTTPException(400, "empty body - send the session zip (manifest.json + images/) or the manifest JSON")
    if len(data) > MAX_BODY:
        raise HTTPException(413, f"session too large ({len(data) // (1024 * 1024)} MB; {MAX_BODY // (1024 * 1024)} MB at most)")
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    try:
        row = captures.save_zip(conn, data, u)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    try:
        from central import analytics
        analytics.log(conn, "fingerprint", row.get("kind") or "", "capture:manifest")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, **row}


@router.get("/me/captures", summary="My capture sessions")
def my_captures(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                kind: str = Query("", max_length=20), u: dict = Depends(require_user)):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    return captures.list_manifests(conn, uid=u["uid"], kind=kind or None, limit=limit, offset=offset)


def _mine_or_admin(request: Request, conn, mid: str) -> dict:
    row = captures.get(conn, mid)
    if not row:
        raise HTTPException(404, "No such capture session")
    u = current_user(request)
    if is_admin_request(request) or (u and row.get("user_uid") == u.get("uid")):
        return row
    if u:
        raise HTTPException(404, "No such capture session")
    raise HTTPException(401, "Sign in first")


@router.get("/me/captures/{mid}", summary="One of my capture sessions, with its manifest")
def my_capture(mid: str, request: Request):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    row = _mine_or_admin(request, conn, mid)
    for f in row.get("files") or []:
        f["url"] = f"/api/me/captures/{mid}/files/{f['file']}"
    return row


@router.get("/me/captures/{mid}/files/{name:path}", summary="A picture from one of my capture sessions")
def my_capture_file(mid: str, name: str, request: Request):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    _mine_or_admin(request, conn, mid)
    return _serve_file(mid, name, private=True)


@router.post("/me/captures/{mid}/decision", summary="What I decided about this session")
def my_decision(mid: str, payload: dict, request: Request, u: dict = Depends(require_user)):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    row = captures.get(conn, mid, with_manifest=False)
    if not row or (row.get("user_uid") != u.get("uid") and not is_admin_request(request)):
        raise HTTPException(404, "No such capture session")
    try:
        out = captures.set_decision(conn, mid, str(payload.get("decision") or ""), note=str(payload.get("note") or ""),
                                    by=_who(u), matched=payload.get("object") if isinstance(payload.get("object"), dict) else None)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, **out}


def _serve_file(mid: str, name: str, *, private: bool) -> Response:
    p = captures.file_path(mid, name)
    if not p:
        raise HTTPException(404, "No such file in this session")
    mime = {".png": "image/png", ".webp": "image/webp", ".json": "application/json"}.get(p.suffix.lower(), "image/jpeg")
    return FileResponse(p, media_type=mime,
                        headers={"Cache-Control": "private, max-age=600" if private else "private, max-age=60"})


# ===================================================================================
# Capture-session manifests: the administrators' side
# ===================================================================================
@router.get("/admin/captures", summary="Every capture session sent for analysis")
def admin_list(kind: str = Query("", max_length=20), decision: str = Query("", max_length=30),
               q: str = Query("", max_length=120), since: str = Query("", max_length=40),
               limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
               u: dict = Depends(require_admin)):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    out = captures.list_manifests(conn, kind=kind or None, decision=decision or None, q=q or None,
                                  since=since or None, limit=limit, offset=offset)
    out["summary"] = captures.stats(conn)
    out["decisions"] = [{"id": d, "label": captures.DECISION_LABELS[d]} for d in captures.DECISIONS]
    out["kinds"] = list(captures.KINDS)
    out["dir"] = str(captures.captures_dir())
    return out


@router.get("/admin/captures/{mid}", summary="One capture session, with its manifest and files")
def admin_one(mid: str, u: dict = Depends(require_admin)):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    row = captures.get(conn, mid)
    if not row:
        raise HTTPException(404, "No such capture session")
    row["download"] = f"/api/admin/captures/{mid}/download"
    return row


@router.get("/admin/captures/{mid}/manifest.json", summary="The manifest alone")
def admin_manifest(mid: str, u: dict = Depends(require_admin)):
    p = captures.file_path(mid, "manifest.json")
    if not p:
        raise HTTPException(404, "No such capture session")
    return Response(p.read_bytes(), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{mid}.manifest.json"'})


@router.get("/admin/captures/{mid}/files/{name:path}", summary="A picture from a capture session")
def admin_file(mid: str, name: str, u: dict = Depends(require_admin)):
    return _serve_file(mid, name, private=True)


@router.get("/admin/captures/{mid}/download", summary="The whole session as a zip")
def admin_download(mid: str, u: dict = Depends(require_admin)):
    conn = _central()
    if conn is None or not captures.get(conn, mid, with_manifest=False):
        raise HTTPException(404, "No such capture session")
    data = captures.zip_bytes(mid)
    if data is None:
        raise HTTPException(404, "The session's files are gone from disk")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{mid}.zip"'})


@router.post("/admin/captures/{mid}/decision", summary="Annotate a session's decision")
def admin_decision(mid: str, payload: dict, u: dict = Depends(require_admin)):
    conn = _central()
    if conn is None:
        raise HTTPException(503, "The registry is not available")
    try:
        out = captures.set_decision(conn, mid, str(payload.get("decision") or ""), note=str(payload.get("note") or ""),
                                    by=_who(u) or "admin",
                                    matched=payload.get("object") if isinstance(payload.get("object"), dict) else None)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if not out:
        raise HTTPException(404, "No such capture session")
    return {"ok": True, **out}
