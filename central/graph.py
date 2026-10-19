"""
central.graph
=============
Build the registry's relationship graph from the data we already have — no graph
database, no new service. It is the knowledge-graph seam of the Glass Database:
the same structure is drawn client-side (``/graph.html``), served as JSON
(``/api/graph.json``) and as JSON-LD (``/api/graph.jsonld``) for RAG pipelines
and local LLMs.

Nodes ─ artist · technique · studio · object · collector
Edges ─ uses (artist/object → technique) · made_by (object → artist)
        studied_under (artist → artist; structured lineage first, text-match
        fallback) · based_at (artist/object → studio) · similar_to (object ↔
        object, DINOv2 centroid cosine ≥ 0.85) · collected_by (object → collector)

Everything comes through the publication gate (``central.approvals``): only
approved rows become nodes. Edges carry their derivation (``meta.confidence``)
so a consumer can tell a structured, reviewed link from a substring match —
features, not verdicts (docs/GLASS-TRAITS.md §2). See docs/GRAPH.md.
"""
from __future__ import annotations

import json
import math
from collections import deque
from urllib.parse import quote

from central import approvals, techniques

SIMILAR_THRESHOLD = 0.85     # cosine similarity for a similar_to edge
SIMILAR_MAX_OBJECTS = 600    # pairwise cap (pure-python cosine; newest objects win)
MIN_MATCH_LEN = 5            # shortest name we'll substring-match in free text

GDB_VOCAB = "https://glassdatabase.org/vocab/graph#"
GDB_NODE = "https://glassdatabase.org/graph#"


# --------------------------------------------------------------------------- helpers
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


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).strip()


def _key(s: str) -> str:
    return _norm(s).lower()


def _has_col(conn, table: str, col: str) -> bool:
    try:
        return col in {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    except Exception:
        return False


def _gated(conn, table: str, limit: int = 5000) -> list[dict]:
    """Approved rows of a table; falls back to all rows if it has no _row_id
    (legacy tables that pre-date the gate)."""
    if _has_col(conn, table, "_row_id"):
        return _rows(conn, f'SELECT * FROM "{table}" WHERE {approvals.approved_subquery()} '
                           f'LIMIT {int(limit)}', (table,))
    return _rows(conn, f'SELECT * FROM "{table}" LIMIT {int(limit)}')


def split_techniques(s: str) -> list[str]:
    """'Blown, Cane | Murrine' → ['Blown', 'Cane', 'Murrine']."""
    out = []
    for part in str(s or "").replace("|", ",").split(","):
        part = _norm(part)
        if part and part not in out:
            out.append(part)
    return out


def _href(ntype: str, label: str, rid: str = "") -> str:
    if ntype == "object":
        return f"/explore/?view=objects#obj-{quote(rid)}"
    if ntype == "studio":
        return "/studios.html?q=" + quote(label)
    if ntype in ("artist", "technique"):
        return "/explore/?view=datasets&q=" + quote(label)
    return ""


def _centroid(fp) -> list[float] | None:
    """Mean-pool the per-frame embeddings of a fingerprint into one unit vector."""
    if not isinstance(fp, dict):
        return None
    vecs = []
    for fr in fp.get("frames") or []:
        emb = fr.get("emb") if isinstance(fr, dict) else None
        if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
            if not vecs or len(emb) == len(vecs[0]):
                vecs.append(emb)
    if not vecs:
        return None
    n = len(vecs)
    c = [sum(col) / n for col in zip(*vecs)]
    norm = math.sqrt(sum(x * x for x in c))
    if norm == 0:
        return None
    return [x / norm for x in c]


def cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity (pure python; vectors need not be normalised)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _manifest(row: dict) -> dict:
    try:
        m = json.loads(row.get("manifest_json") or "{}")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _fingerprint(row: dict, man: dict):
    fp = man.get("fingerprint")
    if not fp and row.get("fingerprint_json"):
        try:
            fp = json.loads(row["fingerprint_json"])
        except Exception:
            fp = None
    return fp if isinstance(fp, dict) else None


def _event_locations(man: dict) -> list[str]:
    out = []
    for a in man.get("assertions") or []:
        if isinstance(a, dict) and a.get("label") == "org.glassdatabase.provenance.events":
            for ev in a.get("data") or []:
                if isinstance(ev, dict) and ev.get("location"):
                    out.append(str(ev["location"]))
    return out


# --------------------------------------------------------------------------- build
def build(conn) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    canon_tech = {label.lower(): label for label in techniques.LABELS}

    def node(nid, label, ntype, meta=None, rid=""):
        n = nodes.get(nid)
        if n is None:
            n = {"id": nid, "label": label, "type": ntype,
                 "href": _href(ntype, label, rid), "meta": {}}
            nodes[nid] = n
        if meta:
            for k, v in meta.items():
                if v not in (None, "", [], {}) and k not in n["meta"]:
                    n["meta"][k] = v
        return nid

    def tech(label):
        k = _key(label)
        return node("tech:" + k, canon_tech.get(k, _norm(label)), "technique",
                    {"vocab": (techniques.resolve(canon_tech.get(k, "")) or {}).get("gbo")})

    def artist(name, source):
        return node("artist:" + _key(name), _norm(name), "artist", {"source": source})

    def edge(src, dst, etype, **attrs):
        e = {"source": src, "target": dst, "type": etype}
        if isinstance(attrs.get("meta"), dict):
            attrs["meta"] = {k: v for k, v in attrs["meta"].items() if v not in (None, "")}
        e.update({k: v for k, v in attrs.items() if v not in (None, "", {}, [])})
        edges.append(e)

    # --- community-submitted artists: clean structured techniques (+ mentor text later) ---
    subs = _gated(conn, "artist_submissions")
    for r in subs:
        name = _first(r, "artist_name", "name")
        if not name:
            continue
        aid = artist(name, "submission")
        for col in ("tech_primary", "tech_secondary", "tech_occasional", "techniques"):
            for t in (r.get(col) or "").split(" | "):
                if t.strip():
                    edge(aid, tech(t), "uses")

    # --- directory artists (ingested; best effort: name + a technique-ish column) ---
    directory = _gated(conn, "artists")
    for r in directory:
        name = _first(r, "artist_name", "name", "artist")
        if not name:
            continue
        aid = artist(name, "directory")
        t = _first(r, "primary_technique", "technique", "primary_focus", "discipline")
        if t:
            edge(aid, tech(t), "uses")

    # --- studios (approved; legacy tables without _row_id fall back to all rows) ---
    studio_rows = _gated(conn, "studios") + _gated(conn, "studio_submissions")
    studio_names: list[tuple[str, str]] = []
    for r in studio_rows:
        sname = _first(r, "name", "studio_name", "studio")
        if not sname:
            continue
        sid = node("studio:" + _key(sname), _norm(sname), "studio",
                   {"city": _first(r, "city"), "country": _first(r, "country"),
                    "kind": _first(r, "type", "studio_type")})
        if len(_key(sname)) >= MIN_MATCH_LEN:
            studio_names.append((_key(sname), sid))

    # --- objects: the provenance registry ---
    objects = _rows(conn, f'SELECT * FROM objects WHERE {approvals.approved_subquery()} '
                          'ORDER BY published_at DESC', ("objects",))
    centroids: list[tuple[str, list[float]]] = []
    for r in objects:
        rid = str(r.get("_row_id") or "")
        if not rid:
            continue
        man = _manifest(r)
        fp = _fingerprint(r, man)
        md = (fp or {}).get("metadata") or {}
        color = md.get("dominantColor") if isinstance(md, dict) else None
        color_name = (color.get("name") or color.get("hex")) if isinstance(color, dict) else None
        traits = [t.get("label") for t in (man.get("traits") or [])
                  if isinstance(t, dict) and t.get("label")][:12]
        techs = split_techniques(r.get("techniques"))
        oid = node("object:" + rid, _norm(r.get("title")) or "Untitled", "object", {
            "maker": _norm(r.get("maker")), "year": _norm(r.get("year")),
            "techniques": techs, "image": f"/api/objects/{rid}/image",
            "dominant_color": color_name, "traits": traits,
            "fingerprinted": bool(fp)}, rid=rid)
        for t in techs:
            edge(oid, tech(t), "uses")
        maker = _norm(r.get("maker"))
        if maker:
            edge(oid, artist(maker, "object"), "made_by")
        c = _centroid(fp) if fp else None
        if c and len(centroids) < SIMILAR_MAX_OBJECTS:
            centroids.append((oid, c))
        for loc in _event_locations(man):
            lk = _key(loc)
            for sk, sid in studio_names:
                if sk in lk:
                    edge(oid, sid, "based_at",
                         meta={"confidence": "text-match", "via": "provenance-event"})

    # --- structured lineage (reviewed submissions) — highest confidence ---
    for r in _gated(conn, "lineage_submissions"):
        student, mentor = _first(r, "student_name"), _first(r, "mentor_name")
        if not student or not mentor or _key(student) == _key(mentor):
            continue
        edge(artist(student, "lineage"), artist(mentor, "lineage"), "studied_under",
             relation=_first(r, "relation") or "Studied under",
             meta={"confidence": "structured", "where": _first(r, "where"),
                   "years": _first(r, "years")})

    # --- mentor links from free text: match studied_under text to known artists ---
    artist_names = [(n["label"].lower(), n["id"]) for n in nodes.values()
                    if n["type"] == "artist"]
    for r in subs:
        name = _first(r, "artist_name", "name")
        su = (r.get("studied_under") or "").lower()
        if not name or not su:
            continue
        aid = "artist:" + _key(name)
        for other_label, other_id in artist_names:
            if other_id != aid and len(other_label) >= MIN_MATCH_LEN and other_label in su:
                edge(aid, other_id, "studied_under", relation="Studied under",
                     meta={"confidence": "text-match"})

    # --- based_at: an approved studio's name in an artist's own text ---
    if studio_names:
        for r in subs + directory:
            name = _first(r, "artist_name", "name", "artist")
            if not name:
                continue
            text = " ".join(str(r.get(k) or "") for k in
                            ("work_experience", "training_education", "nationality_base",
                             "based_in")).lower()
            if not text.strip():
                continue
            aid = "artist:" + _key(name)
            for sk, sid in studio_names:
                if sk in text:
                    edge(aid, sid, "based_at", meta={"confidence": "text-match"})

    # --- similar_to: fingerprint centroids (DINOv2 mean-pool) above the threshold ---
    for i in range(len(centroids)):
        oi, ci = centroids[i]
        for j in range(i + 1, len(centroids)):
            oj, cj = centroids[j]
            if len(ci) != len(cj):
                continue
            sim = sum(x * y for x, y in zip(ci, cj))     # both unit vectors → cosine
            if sim >= SIMILAR_THRESHOLD:
                edge(oi, oj, "similar_to", weight=round(sim, 3),
                     meta={"confidence": "fingerprint", "measure": "cosine"})

    # --- collected_by: verified season claims (table owned by another module) ---
    try:
        claims = _rows(conn, "SELECT object_row_id, collector_uid, collector_name, status "
                             "FROM season_claims WHERE status IN ('verified','approved')")
    except Exception:
        claims = []
    for cl in claims:
        oid = "object:" + str(cl.get("object_row_id") or "")
        uid = str(cl.get("collector_uid") or "").strip()
        if oid not in nodes or not uid:
            continue
        cid = node("collector:" + uid, _norm(cl.get("collector_name")) or "a collector",
                   "collector", {"count": 0})
        nodes[cid]["meta"]["count"] = nodes[cid]["meta"].get("count", 0) + 1
        edge(oid, cid, "collected_by")

    # --- de-dup (similar_to is undirected) ---
    seen, uniq = set(), []
    for e in edges:
        pair = (e["source"], e["target"])
        if e["type"] == "similar_to":
            pair = tuple(sorted(pair))
        k = (*pair, e["type"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    # drop orphan technique/studio nodes (keep artists, objects, collectors)
    connected = {e["source"] for e in uniq} | {e["target"] for e in uniq}
    keep = [n for n in nodes.values()
            if n["type"] in ("artist", "object", "collector") or n["id"] in connected]

    def count(ntype):
        return sum(1 for n in keep if n["type"] == ntype)
    return {"nodes": keep, "edges": uniq,
            "counts": {"artists": count("artist"), "techniques": count("technique"),
                       "studios": count("studio"), "objects": count("object"),
                       "collectors": count("collector"),
                       "lineage": sum(1 for e in uniq if e["type"] == "studied_under"),
                       "similar": sum(1 for e in uniq if e["type"] == "similar_to"),
                       "edges": len(uniq)}}


# --------------------------------------------------------------------------- queries
def _index(graph: dict):
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    adj: dict[str, list[tuple[str, dict]]] = {nid: [] for nid in by_id}
    for e in graph.get("edges", []):
        s, t = e["source"], e["target"]
        if s in adj and t in adj:
            adj[s].append((t, e))
            adj[t].append((s, e))
    return by_id, adj


def resolve(graph: dict, ref: str) -> str | None:
    """A node id, or a label (case-insensitive) → node id. Artists win ties."""
    if not ref:
        return None
    ref = str(ref).strip()
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    if ref in by_id:
        return ref
    k = ref.lower()
    if k in by_id:
        return k
    hits = [n for n in graph.get("nodes", []) if n["label"].lower() == k]
    if not hits:
        hits = [n for n in graph.get("nodes", []) if k in n["label"].lower()]
    if not hits:
        return None
    order = {"artist": 0, "object": 1, "studio": 2, "technique": 3, "collector": 4}
    hits.sort(key=lambda n: (order.get(n["type"], 9), len(n["label"])))
    return hits[0]["id"]


def shortest_path(graph: dict, a: str, b: str) -> dict | None:
    """BFS over the undirected view. a/b may be ids or labels. None if no path."""
    sa, sb = resolve(graph, a), resolve(graph, b)
    if not sa or not sb:
        return None
    by_id, adj = _index(graph)
    if sa == sb:
        return {"nodes": [by_id[sa]], "edges": [], "steps": 0}
    prev: dict[str, tuple[str, dict] | None] = {sa: None}
    q = deque([sa])
    while q:
        cur = q.popleft()
        if cur == sb:
            break
        for nxt, e in adj.get(cur, []):
            if nxt not in prev:
                prev[nxt] = (cur, e)
                q.append(nxt)
    if sb not in prev:
        return None
    path_nodes, path_edges, cur = [sb], [], sb
    while prev[cur] is not None:
        p, e = prev[cur]
        path_edges.append(e)
        path_nodes.append(p)
        cur = p
    path_nodes.reverse()
    path_edges.reverse()
    return {"nodes": [by_id[n] for n in path_nodes], "edges": path_edges,
            "steps": len(path_edges)}


def neighbors(graph: dict, node_id: str, depth: int = 1) -> dict | None:
    """The node, everything within `depth` hops, and its direct neighbours grouped
    by edge type (with direction) — what the side panel and an LLM both want."""
    nid = resolve(graph, node_id)
    if not nid:
        return None
    by_id, adj = _index(graph)
    depth = max(1, min(int(depth or 1), 4))
    dist = {nid: 0}
    q = deque([nid])
    found_edges: list[dict] = []
    seen_edges: set[int] = set()
    while q:
        cur = q.popleft()
        if dist[cur] >= depth:
            continue
        for nxt, e in adj.get(cur, []):
            if id(e) not in seen_edges:
                seen_edges.add(id(e))
                found_edges.append(e)
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    groups: dict[str, list[dict]] = {}
    for other, e in adj.get(nid, []):
        direction = "out" if e["source"] == nid else "in"
        if e["type"] == "similar_to":
            direction = "both"
        item = {"id": other, "label": by_id[other]["label"], "type": by_id[other]["type"],
                "direction": direction}
        for k in ("relation", "weight"):
            if k in e:
                item[k] = e[k]
        if e.get("meta", {}).get("confidence"):
            item["confidence"] = e["meta"]["confidence"]
        groups.setdefault(e["type"], []).append(item)
    return {"node": by_id[nid], "depth": depth,
            "nodes": [by_id[n] for n in sorted(dist, key=dist.get)],
            "edges": found_edges, "groups": groups}


# --------------------------------------------------------------------------- JSON-LD
_TYPES = {"artist": "schema:Person", "technique": "schema:DefinedTerm",
          "studio": "schema:Organization", "object": "schema:CreativeWork",
          "collector": "schema:Person"}
_PROPS = {"uses": "usesTechnique", "made_by": "schema:creator", "studied_under": "studiedUnder",
          "based_at": "basedAt", "similar_to": "similarTo", "collected_by": "collectedBy"}


def node_iri(nid: str) -> str:
    return GDB_NODE + quote(nid, safe=":")


def to_jsonld(graph: dict) -> dict:
    """The graph as JSON-LD: one node object per node, edges as properties."""
    ctx = {
        "schema": "https://schema.org/",
        "gbo": techniques.GBO,
        "gdb": GDB_VOCAB,
        "name": "schema:name",
        "url": {"@id": "schema:url", "@type": "@id"},
        "image": {"@id": "schema:image", "@type": "@id"},
        "creator": {"@id": "schema:creator", "@type": "@id"},
        "studiedUnder": {"@id": "gdb:studiedUnder", "@type": "@id"},
        "similarTo": {"@id": "gdb:similarTo", "@type": "@id"},
        "basedAt": {"@id": "gdb:basedAt", "@type": "@id"},
        "collectedBy": {"@id": "gdb:collectedBy", "@type": "@id"},
        "usesTechnique": {"@id": "gdb:usesTechnique", "@type": "@id"},
        "nodeType": "gdb:nodeType",
        "source": "gdb:source",
        "confidence": "gdb:confidence",
        "lineage": "gdb:lineage",
        "similarity": "gdb:similarity",
        "dominantColor": "gdb:dominantColor",
        "trait": "gdb:trait",
    }
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    out: dict[str, dict] = {}
    for n in graph.get("nodes", []):
        item = {"@id": node_iri(n["id"]), "@type": _TYPES.get(n["type"], "schema:Thing"),
                "name": n["label"], "nodeType": n["type"]}
        if n.get("href"):
            item["url"] = "https://glassdatabase.org" + n["href"]
        meta = n.get("meta") or {}
        if n["type"] == "technique" and meta.get("vocab"):
            item["schema:sameAs"] = meta["vocab"]
        if n["type"] == "artist" and meta.get("source"):
            item["source"] = meta["source"]
        if n["type"] == "object":
            if meta.get("image"):
                item["image"] = "https://glassdatabase.org" + meta["image"]
            if meta.get("year"):
                item["schema:dateCreated"] = meta["year"]
            if meta.get("dominant_color"):
                item["dominantColor"] = meta["dominant_color"]
            if meta.get("traits"):
                item["trait"] = list(meta["traits"])
        if n["type"] == "studio" and (meta.get("city") or meta.get("country")):
            item["schema:address"] = ", ".join(x for x in (meta.get("city"),
                                                            meta.get("country")) if x)
        out[n["id"]] = item
    for e in graph.get("edges", []):
        s, t = e["source"], e["target"]
        if s not in out or t not in out:
            continue
        prop = _PROPS.get(e["type"], "gdb:" + e["type"])
        out[s].setdefault(prop, []).append(node_iri(t))
        if e["type"] == "similar_to":
            out[t].setdefault(prop, []).append(node_iri(s))
            out[s].setdefault("similarity", []).append(
                {"@id": node_iri(t), "schema:value": e.get("weight")})
        if e["type"] == "studied_under":
            note = {"@id": node_iri(t), "schema:name": by_id[t]["label"],
                    "schema:roleName": e.get("relation") or "Studied under",
                    "confidence": (e.get("meta") or {}).get("confidence", "structured")}
            for k in ("where", "years"):
                if (e.get("meta") or {}).get(k):
                    note["gdb:" + k] = e["meta"][k]
            out[s].setdefault("lineage", []).append(note)
    return {"@context": ctx,
            "@id": "https://glassdatabase.org/api/graph.jsonld",
            "@type": "schema:Dataset",
            "schema:name": "Glass Database relationship graph",
            "schema:license": "https://creativecommons.org/licenses/by/4.0/",
            "gdb:counts": graph.get("counts", {}),
            "@graph": list(out.values())}
