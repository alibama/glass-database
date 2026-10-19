"""
central.vision_index
====================
Stage 1 of docs/GLASS-VISION.md, made real: an in-memory nearest-neighbour index
over the approved registry's DINOv2 fingerprint centroids. A piece whose
fingerprint carries embeddings gets technique and style-trait *suggestions* from
the public pieces that look most like it - no vision model, no Ollama, no
training, nothing leaves the machine.

How it works
  * ``load_records(conn)`` walks the public ``objects`` table (the central
    publication gate: approved rows only) and keeps every object whose
    ``manifest_json.fingerprint`` has at least one ``frames[*].emb`` vector. Each
    record carries the registry's clean labels - canonical technique labels and
    SKOS traits grouped by facet - plus the L2-normalised mean of its vectors.
    The shape is the same as one line of ``scripts/export_vision_corpus.py``'s
    ``corpus.jsonl`` (minus the image), so the two are interchangeable.
  * ``VisionIndex`` keeps the records bucketed by embedding dimension (real
    captures are 384-d; tests use tiny vectors) and answers ``neighbours()`` by
    cosine similarity - a numpy matrix product when numpy is importable, a
    pure-python loop otherwise.
  * ``VisionIndex.suggest()`` hands the k nearest records to
    ``glowtbook.vision.knn_suggest`` for the vote: a label's *score* is its
    similarity-weighted share of the neighbourhood, *support* is how many of the
    k neighbours carry it.
  * ``get()`` caches the built index for ``CACHE_TTL`` seconds (per database);
    ``suggest_for_fingerprint(fp)`` is the one-call entry point the Glowtbook
    Style tab uses.

House rule (docs/GLASS-TRAITS.md section 2): **features, not verdicts.** Every
result is a suggestion with its evidence - which pieces, how similar, how many
agreed - for a person to confirm. Maker, origin and value are never inferred.
Measured accuracy: ``python -m scripts.eval_vision_probe`` (leave-one-out over
this same index) prints and writes per-label precision/recall/support.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter

from central import approvals, glass_traits

try:  # optional accelerator - the index is pure python without it
    import numpy as _np
except Exception:  # pragma: no cover - import guard  # noqa: BLE001
    _np = None

CACHE_TTL = 300.0        # seconds a built index is reused before it is rebuilt
MAX_OBJECTS = 20000      # newest approved objects considered (keeps a build bounded)
DEFAULT_K = 5            # neighbours voted when the caller does not say
IMAGE_URL = "/api/objects/{id}/image"
OBJECT_URL = "/explore/?view=objects#obj-{id}"


# --------------------------------------------------------------------------- records
def _load_json(s) -> dict:
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _vision():
    """glowtbook.vision, imported lazily (it pulls httpx and the VLM prompt in)."""
    from glowtbook import vision
    return vision


def split_techniques(s) -> list[str]:
    """'Offhand Blown Glass, Cane & Murrine' / 'a|b' / ['a', 'b'] -> canonical registry
    labels, in order, de-duplicated. Unknown spellings are kept as written so nothing
    is silently dropped."""
    canon = _vision().canonical_technique
    parts: list[str] = []
    if isinstance(s, (list, tuple)):
        for x in s:
            parts.extend(split_techniques(x))
        s = ""
    for part in re.split(r"[|,]", str(s or "")):
        part = part.strip()
        if part:
            parts.append(part)
    out: list[str] = []
    for p in parts:
        lab = canon(p) or p
        if lab not in out:
            out.append(lab)
    return out


def traits_by_facet(manifest: dict) -> dict[str, list[str]]:
    """manifest.traits ([{id,label,facet}] or labels) -> {facet_id: [labels]}."""
    out: dict[str, list[str]] = {}
    traits = manifest.get("traits") if isinstance(manifest, dict) else None
    items: list = []
    if isinstance(traits, dict):            # {facet: [labels]} (the per-user shape)
        for labs in traits.values():
            items.extend(labs if isinstance(labs, list) else [labs])
    elif isinstance(traits, list):
        items = traits
    for t in items:
        if isinstance(t, str):
            c = glass_traits.resolve(t)
        elif isinstance(t, dict):
            c = glass_traits.resolve(t.get("id") or "") or glass_traits.resolve(t.get("label") or "")
        else:
            c = None
        if not c:
            continue
        bucket = out.setdefault(c["facet"], [])
        if c["label"] not in bucket:
            bucket.append(c["label"])
    return out


def record_from_row(row) -> dict | None:
    """One index record from an ``objects`` row (sqlite3.Row or dict) - or None when
    the fingerprint carries no embeddings."""
    keys = set(row.keys()) if hasattr(row, "keys") else set()

    def get(k):
        return row[k] if k in keys else None

    manifest = _load_json(get("manifest_json"))
    fp = manifest.get("fingerprint")
    if not isinstance(fp, dict):
        fp = _load_json(get("fingerprint_json"))
    cen = _vision().centroid(fp) if fp else None
    if not cen:
        return None
    md = fp.get("metadata") if isinstance(fp.get("metadata"), dict) else {}
    dc = md.get("dominantColor") if isinstance(md.get("dominantColor"), dict) else {}
    dominant = ({"name": str(dc.get("name") or ""), "hex": str(dc.get("hex") or "")}
                if dc.get("name") or dc.get("hex") else None)
    techs = split_techniques(get("techniques") or "")
    for t in split_techniques(manifest.get("techniques") or []):
        if t not in techs:
            techs.append(t)
    return {
        "object_id": str(get("_row_id") or ""),
        "title": str(get("title") or "").strip(),
        "techniques": techs,
        "traits": traits_by_facet(manifest),
        "dominant_colour": dominant,
        "embedding_centroid": [round(float(x), 6) for x in cen],
    }


def _has_table(conn, name: str) -> bool:
    try:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def load_records(conn, *, include_pending: bool = False, limit: int = MAX_OBJECTS) -> list[dict]:
    """Index records for the public objects (approved rows only - the gate). With
    ``include_pending`` not-yet-reviewed rows come along for a local experiment;
    rejected rows never do."""
    if not _has_table(conn, "objects"):
        return []
    approvals.ensure_approvals(conn)
    cols = "_row_id, title, techniques, manifest_json, fingerprint_json"
    if include_pending:
        sql = (f"SELECT {cols} FROM objects WHERE _row_id NOT IN "
               "(SELECT row_id FROM _approvals WHERE tbl = ? AND status = 'rejected')")
    else:
        sql = f"SELECT {cols} FROM objects WHERE {approvals.approved_subquery()}"
    sql += f" ORDER BY published_at DESC, _row_id DESC LIMIT {int(limit)}"
    out: list[dict] = []
    for row in conn.execute(sql, ("objects",)).fetchall():
        try:
            rec = record_from_row(row)
        except Exception:  # noqa: BLE001 - one bad manifest must not sink the index
            rec = None
        if rec:
            out.append(rec)
    return out


def records_from_corpus(lines) -> list[dict]:
    """Records from ``corpus.jsonl`` lines (one per *image*): de-duplicated to one
    per object, keeping the first record that carries a centroid."""
    seen: set[str] = set()
    out: list[dict] = []
    for ln in lines:
        if isinstance(ln, (str, bytes)):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
        else:
            rec = ln
        if not isinstance(rec, dict):
            continue
        oid = str(rec.get("object_id") or "")
        cen = rec.get("embedding_centroid")
        if not oid or oid in seen or not isinstance(cen, list) or not cen:
            continue
        seen.add(oid)
        out.append({
            "object_id": oid, "title": str(rec.get("title") or ""),
            "techniques": [str(t) for t in (rec.get("techniques") or []) if t],
            "traits": {f: [str(x) for x in labs] for f, labs in (rec.get("traits") or {}).items()
                       if isinstance(labs, list) and labs},
            "dominant_colour": rec.get("dominant_colour"),
            "embedding_centroid": [float(x) for x in cen],
        })
    return out


# --------------------------------------------------------------------------- the index
def _unit(v) -> list[float] | None:
    try:
        vec = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    n = math.sqrt(sum(x * x for x in vec))
    if not vec or n == 0 or math.isnan(n):
        return None
    return [x / n for x in vec]


class VisionIndex:
    """Nearest-neighbour search over index records, bucketed by vector dimension."""

    def __init__(self, records, *, source: str = "registry"):
        self.source = source
        self.built_at = time.time()
        self.records: list[dict] = []
        self.dropped = 0
        self._buckets: dict[int, list[dict]] = {}
        self._matrix: dict[int, object] = {}
        for rec in records or []:
            if not isinstance(rec, dict):
                self.dropped += 1
                continue
            u = _unit(rec.get("embedding_centroid") or [])
            if not u:
                self.dropped += 1
                continue
            rec = dict(rec)
            rec["embedding_centroid"] = u
            self.records.append(rec)
            self._buckets.setdefault(len(u), []).append(rec)
        if _np is not None:
            for dim, recs in self._buckets.items():
                self._matrix[dim] = _np.array([r["embedding_centroid"] for r in recs],
                                              dtype=float)

    def __len__(self) -> int:
        return len(self.records)

    @property
    def dims(self) -> dict[int, int]:
        return {d: len(r) for d, r in self._buckets.items()}

    def stats(self) -> dict:
        techs: Counter = Counter()
        traits: Counter = Counter()
        for r in self.records:
            techs.update(r["techniques"])
            traits.update(lab for labs in r["traits"].values() for lab in labs)
        return {"objects": len(self.records), "dims": self.dims, "dropped": self.dropped,
                "source": self.source, "built_at": self.built_at,
                "age_s": round(time.time() - self.built_at, 1),
                "techniques": dict(techs.most_common()), "traits": dict(traits.most_common()),
                "numpy": _np is not None}

    def query_vector(self, fp_or_centroid) -> list[float] | None:
        """A unit vector from a fingerprint dict/JSON or a raw centroid list."""
        if isinstance(fp_or_centroid, (list, tuple)):
            return _unit(fp_or_centroid)
        cen = _vision().centroid(fp_or_centroid)
        return _unit(cen) if cen else None

    def neighbours(self, fp_or_centroid, k: int = DEFAULT_K, exclude=None) -> list[tuple[float, dict]]:
        """The k most similar records as (cosine, record), best first. Records whose
        object_id is in ``exclude`` are skipped (the piece itself, once published)."""
        q = self.query_vector(fp_or_centroid)
        if not q:
            return []
        recs = self._buckets.get(len(q)) or []
        if not recs:
            return []
        skip = {str(x) for x in (exclude or ())}
        k = max(1, int(k))
        if _np is not None and len(q) in self._matrix:
            sims = (self._matrix[len(q)] @ _np.array(q, dtype=float)).tolist()
        else:
            sims = [sum(a * b for a, b in zip(q, rec["embedding_centroid"])) for rec in recs]
        # rounded, then tie-broken on id, so numpy and pure python agree on the order
        scored = [(round(max(-1.0, min(1.0, s)), 6), rec) for s, rec in zip(sims, recs)
                  if rec["object_id"] not in skip]
        scored.sort(key=lambda t: (-t[0], t[1]["object_id"]))
        return scored[:k]

    def suggest(self, fp_or_centroid, k: int = DEFAULT_K, exclude=None) -> dict:
        """Vote techniques and traits-by-facet from the k nearest public pieces."""
        base = {"ok": False, "source": "knn", "k": max(1, int(k)), "corpus_size": len(self),
                "techniques": [], "traits": {}, "neighbours": [], "evidence": [], "caveats": ""}
        q = self.query_vector(fp_or_centroid)
        if not q:
            base["error"] = ("This fingerprint has no embeddings. Capture it with the AI "
                             "descriptor on (the capture app's default) to get suggestions "
                             "from similar pieces.")
            return base
        if not len(self):
            base["error"] = ("No public piece has a fingerprint with embeddings yet, so there "
                             "is nothing to compare against.")
            return base
        top = self.neighbours(q, k=k, exclude=exclude)
        if not top:
            base["error"] = (f"No public piece has a {len(q)}-d embedding to compare against "
                             f"(the index holds {self.dims}).")
            return base
        vision = _vision()
        corpus = [rec for _, rec in top]
        kk = len(corpus)
        techniques = vision.knn_suggest(q, corpus, k=kk, key="techniques")
        traits: dict[str, list[dict]] = {}
        for fid, _flabel, _fdef in glass_traits.FACETS:
            voted = vision.knn_suggest(q, corpus, k=kk, key=f"traits.{fid}")
            if voted:
                traits[fid] = voted
        neighbours = []
        for sim, rec in top:
            neighbours.append({
                "object_row_id": rec["object_id"], "title": rec["title"],
                "score": round(sim, 4),
                "image": IMAGE_URL.format(id=rec["object_id"]),
                "href": OBJECT_URL.format(id=rec["object_id"]),
                "techniques": list(rec["techniques"]),
                "traits": {f: list(labs) for f, labs in rec["traits"].items()},
                "dominant_colour": rec.get("dominant_colour"),
            })
        evidence = [f"{kk} nearest public piece(s) by DINOv2 fingerprint similarity "
                    f"(best {neighbours[0]['score']:.2f}, weakest {neighbours[-1]['score']:.2f}) "
                    f"out of {len(self)} with embeddings"]
        for t in techniques[:3]:
            evidence.append(f"{t['support']} of {kk} neighbours are tagged {t['label']}")
        base.update({
            "ok": True, "k": kk, "techniques": techniques, "traits": traits,
            "neighbours": neighbours, "evidence": evidence,
            "caveats": ("Suggestions are votes from look-alike pieces in the public registry, "
                        "not a reading of this piece: the pooled fingerprint vector sees gross "
                        "form and colour, not cane or murrine detail. Features, not verdicts: "
                        "confirm before saving. Measured accuracy: scripts/eval_vision_probe.py."),
        })
        return base


# --------------------------------------------------------------------------- cache
_LOCK = threading.Lock()
_CACHE: dict[str, VisionIndex] = {}


def _db_key() -> str:
    import os

    from central import dbconn
    if dbconn.using_turso():
        return "turso:" + os.environ.get("TURSO_DATABASE_URL", "")
    return "sqlite:" + str(dbconn.local_path())


def build(conn=None, *, include_pending: bool = False, limit: int = MAX_OBJECTS) -> VisionIndex:
    """Build an index from the registry (opens and closes a connection when none is given)."""
    own = conn is None
    if own:
        from central.dbconn import connect
        conn = connect()
    try:
        recs = load_records(conn, include_pending=include_pending, limit=limit)
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return VisionIndex(recs, source="registry" + ("+pending" if include_pending else ""))


def get(conn=None, *, max_age: float = CACHE_TTL, force: bool = False) -> VisionIndex:
    """The cached index for the current database, rebuilt when older than ``max_age``
    seconds (or ``force``). Safe to call from several Streamlit sessions at once."""
    key = _db_key()
    with _LOCK:
        idx = _CACHE.get(key)
        if idx is not None and not force and (time.time() - idx.built_at) < max_age:
            return idx
        idx = build(conn)
        _CACHE[key] = idx
        return idx


def invalidate() -> None:
    """Forget every cached index (call after approving or deleting objects)."""
    with _LOCK:
        _CACHE.clear()


def suggest_for_fingerprint(fp, *, conn=None, k: int = DEFAULT_K, exclude=None,
                            max_age: float = CACHE_TTL) -> dict:
    """{techniques: [{label, score, support}], traits: {facet: [...]},
    neighbours: [{object_row_id, title, score, image, href, ...}], ok, evidence,
    caveats} for a fingerprint (dict or JSON) - from the cached registry index.
    Never raises: a missing fingerprint, an empty registry or a broken database
    all come back as ``ok: False`` with an ``error`` the UI can show."""
    try:
        idx = get(conn, max_age=max_age)
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "source": "knn", "error": f"Could not read the registry: {ex}",
                "techniques": [], "traits": {}, "neighbours": [], "evidence": [], "caveats": ""}
    return idx.suggest(fp, k=k, exclude=exclude)
