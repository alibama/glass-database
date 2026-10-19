"""
api.graph_ext — the knowledge-graph seam
========================================
Read-only views over ``central.graph``:

  GET /graph.jsonld             the whole graph as JSON-LD (schema.org + gdb: vocab)
  GET /graph/neighbors?id=&depth=   a node, its neighbourhood, neighbours grouped by edge
  GET /graph/path?from=&to=     shortest path between two nodes (ids or labels)

``/graph.json`` itself stays in api.main. The built graph is cached in-process
for ``TTL`` seconds so the page's three calls don't rebuild it three times.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from central import graph
from central.dbconn import connect

router = APIRouter()

TTL = 120
_CACHE: dict = {"at": 0.0, "graph": None}


def cached_graph(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["graph"] is not None and now - _CACHE["at"] < TTL:
        return _CACHE["graph"]
    try:
        g = graph.build(connect())
    except Exception:
        g = {"nodes": [], "edges": [], "counts": {}}
    _CACHE["graph"], _CACHE["at"] = g, now
    return g


def invalidate() -> None:
    """Drop the cached graph (tests; admins after a burst of approvals)."""
    _CACHE["graph"], _CACHE["at"] = None, 0.0


@router.get("/graph.jsonld", summary="Relationship graph as JSON-LD (knowledge-graph export)")
def graph_jsonld():
    body = json.dumps(graph.to_jsonld(cached_graph()), ensure_ascii=False)
    return Response(content=body, media_type="application/ld+json",
                    headers={"Cache-Control": "public, max-age=600",
                             "Content-Disposition": 'inline; filename="graph.jsonld"'})


@router.get("/graph/neighbors", summary="A node and its neighbourhood (grouped by edge type)")
def graph_neighbors(id: str = Query(..., description="Node id or label (case-insensitive)"),
                    depth: int = Query(1, ge=1, le=3)):
    out = graph.neighbors(cached_graph(), id, depth)
    if not out:
        raise HTTPException(404, "no such node")
    return out


@router.get("/graph/path", summary="How are two nodes connected? (shortest path)")
def graph_path(from_: str = Query(..., alias="from", description="Node id or label"),
               to: str = Query(..., description="Node id or label")):
    p = graph.shortest_path(cached_graph(), from_, to)
    if not p:
        return JSONResponse({"detail": "no path"}, status_code=404)
    return p
