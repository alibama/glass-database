"""
api.makers — maker cards, career records and the site's numbers
================================================================
  GET /makers?q=name           makers whose name contains q (the record's names, for a search box)
  GET /makers/{ref}            the public card: ref is a Glass Database ID (gdb:artists:12,
                               gdb:maker:…) or ``name:<name>``; traits, bio completeness, the
                               knowledge score, the career across every collecting event
  GET /stats                   the site's numbers (central.stats.site_stats)
  GET /stats/makers            everyone scored at once: the boards for the stats page

The maker's own card, with the where-to-fill list, is ``GET /api/me/card`` (api.me). Reads
are open; account ids and emails never leave here.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import makers, records, stats  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(tags=["makers"])


@router.get("/makers", summary="Makers whose name contains q")
def find(q: str = Query("", description="Part of a name"), limit: int = Query(20, ge=1, le=100)):
    hits = [r for r in records.search(connect(), q, limit * 2) if r.get("kind") in ("person", "maker")]
    return {"q": q, "makers": [{"gid": r["gid"], "name": r["label"], "description": r.get("description", ""),
                                "url": f"/maker.html?m={r['gid']}"} for r in hits[:limit]]}


@router.get("/makers/{ref:path}", summary="A maker's public card")
def one(ref: str):
    conn = connect()
    ref = (ref or "").strip()
    if ref.startswith("name:"):
        m = makers.find(conn, name=ref[5:].strip())
    else:
        m = makers.find(conn, gid=ref)
    if not m:
        raise HTTPException(404, "No maker on the record by that id or name")
    return makers.card(conn, m, private=False)


@router.get("/stats", summary="The site's numbers")
def site(live: bool = True):
    return stats.site_stats(connect(), live=live)


@router.get("/stats/makers", summary="Every maker scored: the boards behind the stats page")
def boards(limit: int = Query(10, ge=1, le=50)):
    return makers.leaderboards(connect(), limit=limit)
