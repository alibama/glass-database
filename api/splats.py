"""
api.splats
==========
Prototype: a maker uploads a Gaussian splat of one of their pieces (trained on
their own phone or laptop with Scaniverse, Polycam, Brush, OpenSplat...) and gets
a share link that opens a rotatable view on ``/splat.html``.

* ``POST /api/splats?object=<oid>`` — body = the file (raw or multipart ``file``);
  formats ``.splat``, ``.ply`` (Gaussian PLY, plain or compressed) and ``.ksplat``;
  150 MB cap; one splat per piece (uploading again replaces it).
* ``GET  /api/splats/{id}``       — the file, public to anyone with the link.
* ``GET  /api/splats/{id}/meta``  — format, size, title.
* ``GET  /api/splats/mine``       — the signed-in user's splats.
* ``DELETE /api/splats/{id}``     — owner only.

Files live under ``data/splat_media/``; rows in the central DB table ``splats``.
A splat is a picture, not provenance: it is never signed into a credential and
never used for fingerprinting.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from api.auth import require_user  # noqa: E402
from central.dbconn import connect  # noqa: E402
from glowtbook import store as gstore  # noqa: E402

router = APIRouter(prefix="/splats", tags=["splats"])

MEDIA = Path(os.environ.get("GLASSDB_SPLAT_DIR") or (gstore.DATA / "splat_media"))
MAX_BYTES = 150 * 1024 * 1024
FORMATS = ("splat", "ply", "ksplat")
MIME = {"splat": "application/octet-stream", "ply": "application/octet-stream",
        "ksplat": "application/octet-stream"}


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS splats (
        id TEXT PRIMARY KEY, uid TEXT NOT NULL, oid INTEGER NOT NULL,
        fmt TEXT NOT NULL, bytes INTEGER NOT NULL, title TEXT DEFAULT '',
        created_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_splats_uid ON splats(uid)")
    conn.commit()


def _conn():
    c = connect()
    ensure(c)
    return c


def _path(sid: str, fmt: str) -> Path:
    return MEDIA / f"{sid}.{fmt}"


def sniff_format(data: bytes, hint: str = "") -> str | None:
    """Work out which of the three formats this is, from the bytes and a filename hint."""
    hint = (hint or "").lower().rsplit(".", 1)[-1]
    if data[:3] == b"ply":
        return "ply"
    if hint == "ksplat":
        return "ksplat"
    if hint == "splat" or (len(data) % 32 == 0 and len(data) >= 32 * 16):
        return "splat"
    return None


def _shape(r) -> dict:
    d = dict(r)
    d["url"] = f"/api/splats/{d['id']}"
    d["share"] = f"/splat.html?s={d['id']}"
    return d


@router.get("/mine", summary="My splats")
def mine(u: dict = Depends(require_user)):
    c = _conn()
    rows = c.execute("SELECT * FROM splats WHERE uid=? ORDER BY created_at DESC", (u["uid"],)).fetchall()
    return {"splats": [_shape(r) for r in rows]}


@router.post("", summary="Upload a splat of one of my pieces (replaces any earlier one)")
async def upload(request: Request, object: int = Query(..., ge=1), u: dict = Depends(require_user),
                 name: str = Query("", description="Original filename, used to tell the format")):
    store = gstore.open_store()
    obj = gstore.get_object(store, u["uid"], object)
    if not obj:
        raise HTTPException(404, "No such piece in your registry")
    ctype = (request.headers.get("content-type") or "").lower()
    data = b""
    if ctype.startswith("multipart/"):
        try:
            form = await request.form()
            f = form.get("file") or next((v for v in form.values() if hasattr(v, "read")), None)
            if f is not None:
                name = name or getattr(f, "filename", "") or ""
                data = await f.read()
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(400, f"couldn't read the upload: {ex}")
    else:
        data = await request.body()
    if not data:
        raise HTTPException(400, "empty body — send a .splat, .ply or .ksplat file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (150 MB max — crop it in SuperSplat first)")
    fmt = sniff_format(data, name)
    if fmt not in FORMATS:
        raise HTTPException(400, "not a splat I can read: expected .splat, a Gaussian .ply or .ksplat")
    c = _conn()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = c.execute("SELECT id, fmt FROM splats WHERE uid=? AND oid=?", (u["uid"], object)).fetchall()
    for r in old:
        try:
            _path(r["id"], r["fmt"]).unlink()
        except FileNotFoundError:
            pass
        c.execute("DELETE FROM splats WHERE id=?", (r["id"],))
    sid = secrets.token_urlsafe(9)
    MEDIA.mkdir(parents=True, exist_ok=True)
    _path(sid, fmt).write_bytes(data)
    title = (obj["title"] if "title" in obj.keys() else "") or ""
    c.execute("INSERT INTO splats (id, uid, oid, fmt, bytes, title, created_at) VALUES (?,?,?,?,?,?,?)",
              (sid, u["uid"], object, fmt, len(data), title, now))
    c.commit()
    row = c.execute("SELECT * FROM splats WHERE id=?", (sid,)).fetchone()
    return {"ok": True, "splat": _shape(row)}


@router.get("/{sid}/meta", summary="What a splat is (format, size, title)")
def meta(sid: str):
    c = _conn()
    r = c.execute("SELECT * FROM splats WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "no such splat")
    d = _shape(r)
    d.pop("uid", None)
    return d


@router.get("/{sid}", summary="The splat file (anyone with the link)")
def file(sid: str):
    c = _conn()
    r = c.execute("SELECT fmt FROM splats WHERE id=?", (sid,)).fetchone()
    if not r or not _path(sid, r["fmt"]).exists():
        raise HTTPException(404, "no such splat")
    return FileResponse(_path(sid, r["fmt"]), media_type=MIME[r["fmt"]],
                        filename=f"{sid}.{r['fmt']}",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@router.delete("/{sid}", summary="Remove one of my splats")
def remove(sid: str, u: dict = Depends(require_user)):
    c = _conn()
    r = c.execute("SELECT * FROM splats WHERE id=? AND uid=?", (sid, u["uid"])).fetchone()
    if not r:
        raise HTTPException(404, "no such splat")
    try:
        _path(sid, r["fmt"]).unlink()
    except FileNotFoundError:
        pass
    c.execute("DELETE FROM splats WHERE id=?", (sid,))
    c.commit()
    return {"ok": True}
