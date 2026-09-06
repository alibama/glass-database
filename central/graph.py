"""
central.graph
============
Build a lightweight relationship graph from the data we already have — no graph
database, no new service. Nodes are artists, techniques, studios; edges are
"uses technique", "studied under" (mentor links found by matching a submission's
studied_under text to other artists in the directory), and "based at" studios.
Served as JSON and drawn client-side. Grows as the directory does.
"""
from __future__ import annotations

from central import approvals


def _rows(conn, sql, args=()):
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        return []


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k):
            return str(d[k]).strip()
    return ""


def build(conn) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, label, ntype):
        nodes.setdefault(nid, {"id": nid, "label": label, "type": ntype})
        return nid

    # --- community-submitted artists: clean structured techniques + mentors ---
    subs = _rows(conn, f'SELECT * FROM artist_submissions WHERE {approvals.approved_subquery()}',
                 ("artist_submissions",))
    for r in subs:
        name = _first(r, "artist_name", "name")
        if not name:
            continue
        aid = node("artist:" + name.lower(), name, "artist")
        for col in ("tech_primary", "tech_secondary", "tech_occasional", "techniques"):
            for t in (r.get(col) or "").split(" | "):
                t = t.strip()
                if t:
                    edges.append({"source": aid, "target": node("tech:" + t.lower(), t, "technique"),
                                  "type": "uses"})

    # --- ingested artists (best effort: name + a technique-ish column) ---
    for r in _rows(conn, "SELECT * FROM artists LIMIT 5000"):
        name = _first(r, "artist_name", "name", "artist")
        if not name:
            continue
        aid = node("artist:" + name.lower(), name, "artist")
        tech = _first(r, "primary_technique", "technique", "primary_focus", "discipline")
        if tech:
            edges.append({"source": aid, "target": node("tech:" + tech.lower(), tech, "technique"),
                          "type": "uses"})

    # --- studios as nodes (context; direct artist links added when a field exists) ---
    for r in _rows(conn, "SELECT * FROM studios LIMIT 5000"):
        sname = _first(r, "name", "studio_name", "studio")
        if sname:
            node("studio:" + sname.lower(), sname, "studio")

    # --- mentor links: match studied_under text to known artist names ---
    artist_names = [(n["label"].lower(), n["id"]) for n in nodes.values() if n["type"] == "artist"]
    for r in subs:
        name = _first(r, "artist_name", "name")
        if not name:
            continue
        aid = "artist:" + name.lower()
        su = (r.get("studied_under") or "").lower()
        if not su:
            continue
        for other_label, other_id in artist_names:
            if other_id != aid and len(other_label) >= 5 and other_label in su:
                edges.append({"source": aid, "target": other_id, "type": "studied_under"})

    # de-dup edges
    seen, uniq = set(), []
    for e in edges:
        k = (e["source"], e["target"], e["type"])
        if k not in seen:
            seen.add(k); uniq.append(e)
    # drop orphan technique/studio nodes with no edges (keep artists)
    connected = {e["source"] for e in uniq} | {e["target"] for e in uniq}
    keep = [n for n in nodes.values() if n["type"] == "artist" or n["id"] in connected]
    return {"nodes": keep, "edges": uniq,
            "counts": {"artists": sum(1 for n in keep if n["type"] == "artist"),
                       "techniques": sum(1 for n in keep if n["type"] == "technique"),
                       "studios": sum(1 for n in keep if n["type"] == "studio"),
                       "edges": len(uniq)}}
