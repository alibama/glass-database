"""
api.records — a page for every Glass Database ID
=================================================
  GET /records/{gid}      everything public about one key: the source row's public columns,
                          the staged wiki item's facts, where it is on the wiki and on
                          Wikidata, and what else here is tied to it (central.records.resolve)
  GET /records?q=name     find a record by name (central.records.search)

Reads are open; nothing here writes. ``deploy/record.html`` renders the first at
``/record/<gid>``, which is the URL the wiki's references and the Resolve tool point at.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import records  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(tags=["records"])


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{host}"


@router.get("/records", summary="Find a record by name")
def find(q: str = Query("", description="Part of a name"), limit: int = Query(20, ge=1, le=100)):
    return {"q": q, "records": records.search(connect(), q, limit)}


@router.get("/records/{gid:path}", summary="One record by its Glass Database ID")
def one(gid: str, request: Request):
    rec = records.resolve(connect(), gid, base=_public_base(request))
    if not rec:
        raise HTTPException(404, "No public record with that Glass Database ID")
    return rec
