"""
Glass Database — read API
=========================
A generic, read-only HTTP API other developers can build on. It self-describes
from the ingest registry, so every dataset is explorable without bespoke code,
and the interactive docs at /docs (Swagger) are the "convenient exploration"
surface.

Safety model:
  * READ-ONLY. No write endpoints — content is added via the admin/CLI.
  * PUBLIC by default. Restricted datasets and private columns (emails, phones,
    claim tokens, internal notes) are withheld unless a caller sends a valid
    admin key, matching the Removal & Correction Policy.
  * Table and column names are validated against the registry before hitting
    SQL, so arbitrary identifiers can't be injected.

Run:  uvicorn api.main:app --reload
Docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals  # noqa: E402
from central.dbconn import connect  # noqa: E402

ADMIN_KEY = os.environ.get("GLASSDB_ADMIN_TOKEN", "")
MAX_LIMIT = 200
DIP_MEDIA = Path(__file__).resolve().parent.parent / "data" / "dip_media"

app = FastAPI(
    title="Glass Database API",
    version="0.1.0",
    root_path=os.environ.get("ROOT_PATH", ""),   # e.g. "/api" behind a reverse proxy
    description=(
        "Read-only, self-describing access to the Glass Database — studios, "
        "artists, programs, opportunities, events, trade shows and more. "
        "Published under a Creative Commons license. Restricted data and "
        "personal contact fields are withheld from public responses."
    ),
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    try:
        approvals.ensure_approvals(connect())
    except Exception:
        pass


# --- helpers ---------------------------------------------------------------
def _is_admin(request: Request) -> bool:
    key = request.headers.get("x-api-key", "")
    return bool(ADMIN_KEY) and key == ADMIN_KEY


def _dataset(conn, table: str):
    row = conn.execute(
        "SELECT tbl, domain, visibility, row_count, description FROM _datasets WHERE tbl=?",
        (table,),
    ).fetchone()
    return dict(row) if row else None


def _columns(conn, table: str, admin: bool):
    rows = conn.execute(
        "SELECT column, label, ordinal, is_public FROM _columns WHERE tbl=? ORDER BY ordinal",
        (table,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if admin or d["is_public"]:
            out.append(d)
    return out


def _visible_cols(conn, table: str, admin: bool) -> list[str]:
    cols = [c["column"] for c in _columns(conn, table, admin)]
    return ["_row_id", *cols, "_source_file", "_source_sheet", "_imported_at"]


def _require_table(conn, table: str, admin: bool):
    ds = _dataset(conn, table)
    if not ds:
        raise HTTPException(404, f"No such dataset: {table}")
    if ds["visibility"] == "restricted" and not admin:
        raise HTTPException(403, f"Dataset '{table}' is restricted.")
    return ds


# --- endpoints -------------------------------------------------------------
@app.get("/", summary="Service overview")
def root(request: Request):
    conn = connect()
    admin = _is_admin(request)
    where = "" if admin else "WHERE visibility='public'"
    rows = conn.execute(
        f"SELECT domain, COUNT(*) n, SUM(row_count) rows FROM _datasets {where} GROUP BY domain ORDER BY domain"
    ).fetchall()
    return {
        "service": "Glass Database API",
        "license": "CC-BY (see glassdatabase.org)",
        "docs": "/docs",
        "endpoints": ["/datasets", "/datasets/{table}", "/datasets/{table}/{row_id}", "/schema/{table}"],
        "domains": [dict(r) for r in rows],
        "admin": admin,
    }


@app.get("/datasets", summary="List datasets")
def list_datasets(request: Request):
    conn = connect()
    admin = _is_admin(request)
    where = "" if admin else "WHERE visibility='public'"
    rows = conn.execute(
        f"SELECT tbl, domain, visibility, row_count, description, source_file, source_sheet "
        f"FROM _datasets {where} ORDER BY domain, tbl"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not admin:
            # public row_count reflects what's actually published; hide datasets
            # with nothing approved yet
            try:
                appr = conn.execute(
                    "SELECT COUNT(*) FROM _approvals WHERE tbl=? AND status='approved'",
                    (d["tbl"],)).fetchone()[0]
            except Exception:
                appr = 0
            if appr == 0:
                continue
            d["row_count"] = appr
        out.append(d)
    return {"count": len(out), "datasets": out}


@app.get("/schema/{table}", summary="Columns of a dataset")
def schema(table: str, request: Request):
    conn = connect()
    admin = _is_admin(request)
    ds = _require_table(conn, table, admin)
    return {"dataset": ds, "columns": _columns(conn, table, admin)}


@app.get("/datasets/{table}", summary="Rows of a dataset (filter, search, paginate, CSV)")
def rows(
    table: str,
    request: Request,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, description="Case-insensitive search across visible text columns"),
    order_by: str | None = Query(None, description="A visible column name"),
    desc: bool = False,
    format: str = Query("json", pattern="^(json|csv)$"),
):
    conn = connect()
    admin = _is_admin(request)
    _require_table(conn, table, admin)
    visible = _visible_cols(conn, table, admin)
    vset = set(visible)

    # arbitrary column filters: ?country=USA&type=... (validated against columns)
    filters = {k: v for k, v in request.query_params.items()
               if k in vset and k not in {"limit", "offset", "q", "order_by", "desc", "format"}}

    where, params = [], []
    for col, val in filters.items():
        where.append(f'"{col}" = ?'); params.append(val)
    if q:
        text_cols = [c for c in visible if not c.startswith("_")]
        if text_cols:
            where.append("(" + " OR ".join(f'"{c}" LIKE ?' for c in text_cols) + ")")
            params += [f"%{q}%"] * len(text_cols)
    if not admin:                       # publication gate: public sees approved rows only
        where.append(approvals.approved_subquery()); params.append(table)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_sql = ""
    if order_by and order_by in vset:
        order_sql = f'ORDER BY "{order_by}" ' + ("DESC" if desc else "ASC")

    collist = ", ".join(f'"{c}"' for c in visible)
    total = conn.execute(f'SELECT COUNT(*) FROM "{table}" {where_sql}', params).fetchone()[0]
    cur = conn.execute(
        f'SELECT {collist} FROM "{table}" {where_sql} {order_sql} LIMIT ? OFFSET ?',
        [*params, limit, offset],
    )
    data = [dict(zip(visible, r)) for r in cur.fetchall()]

    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=visible)
        w.writeheader(); w.writerows(data)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
        )

    return {
        "dataset": table, "total": total, "limit": limit, "offset": offset,
        "returned": len(data), "filters": filters, "rows": data,
    }


@app.get("/objects/{row_id}/image", summary="Primary image of a public object (bytes, credentials intact)")
def object_image(row_id: str, i: int = Query(0, ge=0, description="Image index (0 = primary)")):
    """Serve a contributed object's image exactly as stored — no re-encoding — so any
    embedded C2PA Content Credential stays intact and can be verified at
    contentcredentials.org/verify or c2paviewer.com by pasting this URL."""
    import base64 as _b64
    conn = connect()
    # only public/approved objects have rows here
    try:
        exists = conn.execute("SELECT 1 FROM objects WHERE _row_id=?", (row_id,)).fetchone()
    except Exception:
        raise HTTPException(404, "No such object")   # objects table not created yet
    if not exists:
        raise HTTPException(404, "No such object")
    rows = conn.execute(
        "SELECT image_b64 FROM object_images WHERE object_row_id=? ORDER BY "
        "CASE role WHEN 'primary' THEN 0 ELSE 1 END, id", (row_id,)).fetchall()
    if not rows or i >= len(rows):
        raise HTTPException(404, "No such image")
    try:
        raw = _b64.b64decode(rows[i]["image_b64"])
    except Exception:
        raise HTTPException(500, "Image could not be decoded")
    return Response(content=raw, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/moderate", summary="One-click approve/reject from a signed link (Discord)")
def moderate(tbl: str, row: str, action: str, sig: str):
    from central import notify
    if action not in notify.ACTIONS:
        raise HTTPException(400, "bad action")
    if not notify.verify(tbl, row, sig):
        raise HTTPException(403, "invalid or expired signature")
    conn = connect()
    approvals.ensure_approvals(conn)
    status = "approved" if action == "approve" else "rejected"
    approvals.set_status(conn, tbl, [row], status, reviewer="discord")
    ok = action == "approve"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{'Approved' if ok else 'Rejected'}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;display:grid;place-items:center;
height:100vh;margin:0;background:{'#052e16' if ok else '#450a0a'};color:#fff;text-align:center;padding:2rem}}
h1{{font-size:1.6rem;margin:0 0 .4rem}}code{{opacity:.7;font-size:.85rem}}
a{{color:#fdba74}}</style></head><body><div>
<h1>{'✅ Approved &amp; published' if ok else '⛔ Rejected'}</h1>
<p>{'It’s now live.' if ok else 'It won’t appear publicly.'}</p>
<p><code>{tbl} / {row}</code></p>
<p><a href="/admin/">Open the admin console</a></p></div></body></html>"""
    return Response(content=body, media_type="text/html", status_code=200)


@app.get("/vocab/glass-traits.ttl", summary="Venetian/façon-de-Venise trait thesaurus (SKOS/Turtle)")
def glass_traits_ttl():
    from central import glass_traits
    return Response(content=glass_traits.to_skos(), media_type="text/turtle; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="glass-traits.ttl"',
                             "Cache-Control": "public, max-age=3600"})


@app.get("/vocab/glass-traits.json", summary="Trait thesaurus as JSON (facets + concepts)")
def glass_traits_json():
    from central import glass_traits as gt
    return {"scheme": "https://glassdatabase.org/vocab/glass-traits",
            "facets": [{"id": f[0], "label": f[1], "definition": f[2],
                        "concepts": [{"id": c["id"], "label": c["label"],
                                      "definition": c["definition"], "alt": c["alt"],
                                      "uri": gt.VOCAB + c["id"]}
                                     for c in gt.CONCEPTS if c["facet"] == f[0]]}
                       for f in gt.FACETS]}


@app.get("/opportunities.ics", summary="Subscribable calendar of approved opportunities")
def opportunities_ics():
    from central import opportunities as opp
    conn = connect()
    try:
        opp.ensure_opportunities(conn)
        ics = opp.build_ics(opp.approved(conn), base_url=os.environ.get("PUBLIC_BASE_URL", ""))
    except Exception:
        ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    return Response(content=ics, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="glass-opportunities.ics"',
                             "Cache-Control": "public, max-age=1800"})


@app.get("/objects/{row_id}/fingerprint", summary="Re-identification fingerprint of a public object")
def object_fingerprint(row_id: str):
    """Return the object's stored re-identification fingerprint (for verification)."""
    import json as _json
    conn = connect()
    try:
        row = conn.execute("SELECT manifest_json FROM objects WHERE _row_id=?", (row_id,)).fetchone()
    except Exception:
        raise HTTPException(404, "No such object")
    if not row:
        raise HTTPException(404, "No such object")
    try:
        fp = (_json.loads(row["manifest_json"] or "{}")).get("fingerprint")
    except Exception:
        fp = None
    if not fp:
        raise HTTPException(404, "No fingerprint for this object")
    return fp


@app.get("/objects/{row_id}/video", summary="Condensed DIP video of a public object, if any")
def object_video(row_id: str):
    """Serve the transcoded (web-friendly) DIP video for an approved object."""
    conn = connect()
    try:
        row = conn.execute("SELECT content_hash FROM objects WHERE _row_id=?", (row_id,)).fetchone()
    except Exception:
        raise HTTPException(404, "No such object")   # objects table not created yet
    if not row:
        raise HTTPException(404, "No such object")
    path = DIP_MEDIA / f"{row['content_hash']}.mp4"
    if not path.exists():
        raise HTTPException(404, "No video for this object")
    return Response(content=path.read_bytes(), media_type="video/mp4",
                    headers={"Cache-Control": "public, max-age=3600",
                             "Accept-Ranges": "bytes"})


@app.get("/datasets/{table}/{row_id}", summary="One row by id")
def one(table: str, row_id: str, request: Request):
    conn = connect()
    admin = _is_admin(request)
    _require_table(conn, table, admin)
    visible = _visible_cols(conn, table, admin)
    collist = ", ".join(f'"{c}"' for c in visible)
    gate = "" if admin else f" AND {approvals.approved_subquery()}"
    gp = [] if admin else [table]
    r = conn.execute(f'SELECT {collist} FROM "{table}" WHERE _row_id=?{gate}',
                     (row_id, *gp)).fetchone()
    if not r:
        raise HTTPException(404, "No such row")
    return dict(zip(visible, r))
