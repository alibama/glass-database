"""
central.glassid
===============
The matching arithmetic of Glass ID, on the server, in step with the capture pages.

The pages (``deploy/fingerprint-web/enroll.html`` and ``verify.html``) compute the
descriptors in the browser - a white-balanced HSV colour histogram, a dHash and,
with AI on, a pooled DINOv2 vector per frame - and *Check a Piece* decides a
match client-side against one reference. This module is the same arithmetic in
Python, so that

  * one capture can be checked against **every** registered piece at once
    (``candidates()``, what the pages call through ``POST /api/glassid/candidates``:
    the lookup-by-photo), with the score broken down by descriptor, the views that
    carried it, and the reasons spelled out;
  * two fingerprints can be compared side by side (``compare()``) and come out with
    the confidence and verdict the page would show;
  * a stored capture session can be re-scored offline (``scripts/glassid_bench.py``),
    so the thresholds are set from data instead of guessed;
  * an image can be turned into the page's descriptors without a browser
    (``descriptors()``), for the bench and for manifests that carry pictures only.

The thresholds are mirrored from ``verify.html`` and must be changed in both
places. Nothing here is a verdict on authenticity: every result is decision
support with its evidence attached (docs/GLASS-ID-MATCHING-ASSESSMENT.md says
how much that evidence is worth today).
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timezone

try:  # the arithmetic is pure python without it, only slower
    import numpy as _np
except Exception:  # pragma: no cover - import guard  # noqa: BLE001
    _np = None

# --- thresholds, mirrored from deploy/fingerprint-web/verify.html ----------------
SIM_LO, SIM_HI = 6, 22          # dHash hamming distance -> similarity ramp
STRONG_T = 0.5                  # a shot/reference pair at or above this counts as matched
SPREAD_TARGET = 6               # distinct reference views matched for full spread
MATCH_READY, NO_MATCH = 70, 40  # confidence: "Match likely" from 70, "No match" under 40
MIN_EFFORT = 8                  # sharp shots taken before "No match" may be declared
MIN_CELLS_MATCH = 3             # distinct reference views a "Match likely" needs
SHARP_MIN = 12                  # the page's blur gate (Laplacian variance)
TOP_SIMS = 8                    # the strongest matched pairs averaged into "quality"
# server-side only: two mat-measured pieces whose height or footprint differ by more than
# this fraction are not one piece, whatever the pictures say
SIZE_TOLERANCE = 0.25
AMBIGUOUS_MARGIN = 10           # top two candidates closer than this (both above NO_MATCH)
DESCRIPTORS = ("dino", "color", "dhash")
DESCRIPTOR_WORDS = {"dino": "AI texture (DINOv2)", "color": "colour histogram", "dhash": "shape hash (dHash)"}
CACHE_TTL = 300.0
OBJECT_URL = "/explore/?view=objects&piece={id}"
CHECK_URL = "/fingerprint/verify.html?object={id}"
MINE_URL = "/account.html?piece={id}#golive"
MINE_CHECK_URL = "/fingerprint/verify.html?mine={id}"


# =============================================================================== maths
def _nums(v, n=None):
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    if not out or any(math.isnan(x) for x in out):
        return None
    if n is not None and len(out) != n:
        return None
    return out


def hist_corr(a, b) -> float:
    """Pearson correlation of two histograms, clipped at 0 (the page's ``histCorr``)."""
    a, b = _nums(a), _nums(b)
    if not a or not b or len(a) != len(b):
        return 0.0
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = da = db = 0.0
    for x, y in zip(a, b):
        u, v = x - ma, y - mb
        num += u * v
        da += u * u
        db += v * v
    d = math.sqrt(da * db)
    return max(0.0, num / d) if d else 0.0


def cosine(a, b) -> float:
    """Cosine similarity clipped at 0 (the page's ``cosine``); 0 for anything unusable."""
    a, b = _nums(a), _nums(b)
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return max(0.0, min(1.0, dot / (na * nb))) if na and nb else 0.0


def _u32(x) -> int:
    try:
        return int(x) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return 0


def hamming(a, b) -> int:
    """Bits that differ between two ``[hi, lo]`` dHashes (64 when either is missing)."""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 64
    return bin(_u32(a[0]) ^ _u32(b[0])).count("1") + bin(_u32(a[1]) ^ _u32(b[1])).count("1")


def dhash_sim(dist: int) -> float:
    """Hamming distance -> similarity on the page's ramp: 1 at SIM_LO bits, 0 at SIM_HI."""
    return max(0.0, min(1.0, (SIM_HI - dist) / (SIM_HI - SIM_LO)))


def frame_sim(q: dict, r: dict, desc: str) -> float:
    if desc == "dino":
        return cosine(q.get("emb"), r.get("emb")) if q.get("emb") and r.get("emb") else 0.0
    if desc == "dhash":
        return dhash_sim(hamming(q.get("dhash"), r.get("dhash")))
    return hist_corr(q.get("chist"), r.get("chist"))


def js_round(x: float) -> int:
    """JavaScript's Math.round for the non-negative numbers used here (.5 rounds up)."""
    return int(math.floor(x + 0.5))


# =============================================================================== fingerprints
def as_fp(fp) -> dict:
    if isinstance(fp, (str, bytes, bytearray)):
        try:
            fp = json.loads(fp)
        except Exception:  # noqa: BLE001
            return {}
    return fp if isinstance(fp, dict) else {}


def frames_of(fp) -> list[dict]:
    """The frames of a fingerprint, normalised the way ``verify.html``'s ``applyRef``
    does it: a ``cell`` for every frame, ``sector`` -1 when unknown, a ``seq``."""
    out = []
    for i, fr in enumerate(as_fp(fp).get("frames") or []):
        if not isinstance(fr, dict):
            continue
        f = dict(fr)
        f.setdefault("file", f"frames/{i:03d}.jpg")
        if f.get("cell") in (None, ""):
            f["cell"] = "c" + str(f["file"])
        if f.get("sector") is None:
            f["sector"] = -1
        f.setdefault("seq", i + 1)
        out.append(f)
    return out


def has_embeddings(fp) -> bool:
    return any(isinstance(fr.get("emb"), (list, tuple)) and fr["emb"] for fr in frames_of(fp))


def has_colour(fp) -> bool:
    return any(isinstance(fr.get("chist"), (list, tuple)) and fr["chist"] for fr in frames_of(fp))


def dimensions_of(fp) -> dict | None:
    md = as_fp(fp).get("metadata")
    d = md.get("dimensions_mm") if isinstance(md, dict) else None
    if not isinstance(d, dict):
        return None
    try:
        out = {k: float(d[k]) for k in ("width", "depth", "height") if d.get(k) is not None}
    except (TypeError, ValueError):
        return None
    return out or None


def summary(fp) -> dict:
    """What a lookup result or a candidate says about a registered fingerprint."""
    f = as_fp(fp)
    frames = frames_of(f)
    md = f.get("metadata") if isinstance(f.get("metadata"), dict) else {}
    dc = md.get("dominantColor") if isinstance(md.get("dominantColor"), dict) else None
    return {"rating": f.get("rating"), "tier": f.get("tier"), "created": f.get("created"),
            "views": len(frames),
            "views_with_embeddings": sum(1 for fr in frames if fr.get("emb")),
            "has_embeddings": bool(md.get("hasEmbeddings")) or any(fr.get("emb") for fr in frames),
            "dominant_colour": dc, "dimensions_mm": dimensions_of(f)}


def pick_descriptor(query_fp, ref_fp) -> str:
    """The page prefers the AI descriptor when the reference carries embeddings - here
    both sides must, or the comparison would be all zeros."""
    return "dino" if has_embeddings(query_fp) and has_embeddings(ref_fp) else "color"


# =============================================================================== matching
def _sim_matrix(qf: list[dict], rf: list[dict], desc: str) -> list[list[float]]:
    """similarity[i][j] of query frame i against reference frame j for one descriptor."""
    if not qf or not rf:
        return []
    if desc == "dhash" or _np is None:
        return [[frame_sim(q, r, desc) for r in rf] for q in qf]
    key, dim = ("emb", None) if desc == "dino" else ("chist", None)

    def rows(frames):
        vecs, ok = [], []
        for fr in frames:
            v = _nums(fr.get(key))
            vecs.append(v)
            ok.append(v is not None)
        dims = {len(v) for v in vecs if v}
        if len(dims) != 1:
            return None, ok
        d = dims.pop()
        m = _np.zeros((len(frames), d), dtype=float)
        for i, v in enumerate(vecs):
            if v is not None:
                m[i] = v
        return m, ok

    Q, qok = rows(qf)
    R, rok = rows(rf)
    if Q is None or R is None or Q.shape[1] != R.shape[1]:
        return [[frame_sim(q, r, desc) for r in rf] for q in qf]
    if desc == "color":
        Q = Q - Q.mean(axis=1, keepdims=True)
        R = R - R.mean(axis=1, keepdims=True)
    qn = _np.linalg.norm(Q, axis=1, keepdims=True)
    rn = _np.linalg.norm(R, axis=1, keepdims=True)
    qn[qn == 0] = 1.0
    rn[rn == 0] = 1.0
    S = (Q / qn) @ (R / rn).T
    S = _np.clip(S, 0.0, 1.0)
    for i, ok in enumerate(qok):
        if not ok:
            S[i, :] = 0.0
    for j, ok in enumerate(rok):
        if not ok:
            S[:, j] = 0.0
    return S.tolist()


def match_frames(qf: list[dict], rf: list[dict], desc: str) -> list[dict]:
    """For every query frame, its best reference frame under ``desc`` (the page's
    ``matchFrame`` for each shot): ``{shot, sim, dist, ref, ref_cell, ref_index}``."""
    S = _sim_matrix(qf, rf, desc)
    out = []
    for i, q in enumerate(qf):
        row = S[i] if S else []
        best_j, best = -1, -1.0
        for j, s in enumerate(row):
            if s > best:
                best, best_j = s, j
        sim = max(0.0, best)
        r = rf[best_j] if best_j >= 0 else None
        out.append({"shot": q.get("seq", i + 1), "sim": round(sim, 4), "dist": js_round((1 - sim) * 100),
                    "ref": r.get("file") if r else None, "ref_cell": r.get("cell") if r else None,
                    "ref_index": best_j if best_j >= 0 else None,
                    "has_descriptor": bool(q.get("emb") if desc == "dino" else
                                           q.get("dhash") if desc == "dhash" else q.get("chist"))})
    return out


def aggregate(pairs: list[dict]) -> dict:
    """The page's ``recompute``: quality of the strongest pairs x spread over distinct
    reference views -> a confidence from 0 to 100."""
    strong = [p for p in pairs if p["sim"] >= STRONG_T]
    top = sorted((p["sim"] for p in strong), reverse=True)[:TOP_SIMS]
    quality = sum(top) / len(top) if top else 0.0
    cells = sorted({str(p["ref_cell"]) for p in strong if p["ref_cell"] is not None})
    spread = min(1.0, len(cells) / SPREAD_TARGET)
    conf = js_round(100 * quality * (0.4 + 0.6 * spread))
    return {"confidence": conf, "quality": round(quality, 4), "spread": round(spread, 4),
            "views_matched": len(cells), "cells": cells, "strong": len(strong),
            "carried_by": {"shots": [p["shot"] for p in sorted(strong, key=lambda p: -p["sim"])[:TOP_SIMS]],
                           "reference_views": [p["ref"] for p in sorted(strong, key=lambda p: -p["sim"])[:TOP_SIMS]]}}


def effort_of(qf: list[dict]) -> int:
    """Sharp shots (the page counts a shot toward the no-match effort only when it is not blurry)."""
    n = 0
    for fr in qf:
        s = fr.get("sharp")
        try:
            n += 1 if s is None or float(s) >= SHARP_MIN else 0
        except (TypeError, ValueError):
            n += 1
    return n


def verdict(agg: dict, effort: int) -> tuple[str, str]:
    """The page's ``verdictFor``."""
    if agg["confidence"] >= MATCH_READY and agg["views_matched"] >= MIN_CELLS_MATCH:
        return "match", "Match likely"
    if effort >= MIN_EFFORT and agg["confidence"] < NO_MATCH and agg["views_matched"] < 2:
        return "nomatch", "No match here"
    return "inconc", "Inconclusive"


def size_check(q_dims: dict | None, r_dims: dict | None) -> dict:
    """Compare mat-measured sizes: height and the longest footprint axis, each within
    SIZE_TOLERANCE. ``ok`` is None when either side was not measured."""
    out = {"query": q_dims, "reference": r_dims, "ok": None, "difference": None, "text": ""}
    if not q_dims or not r_dims:
        out["text"] = ("no mat measurement on either side" if not q_dims and not r_dims else
                       "no mat measurement on the new capture" if not q_dims else
                       "no mat measurement on the registered piece")
        return out

    def axes(d):
        foot = [v for k, v in d.items() if k in ("width", "depth") and v]
        return (max(foot) if foot else None), d.get("height")
    qf, qh = axes(q_dims)
    rf, rh = axes(r_dims)
    diffs = []
    for a, b in ((qf, rf), (qh, rh)):
        if a and b:
            diffs.append(abs(a - b) / max(a, b))
    if not diffs:
        out["text"] = "sizes could not be compared"
        return out
    out["difference"] = round(max(diffs), 3)
    out["ok"] = out["difference"] <= SIZE_TOLERANCE

    def fmt(d):
        return " × ".join(f"{int(round(d[k]))}" for k in ("width", "depth", "height") if d.get(k)) + " mm"
    pct = int(round(out["difference"] * 100))
    out["text"] = (f"{fmt(q_dims)} vs {fmt(r_dims)} on the mat, within {pct}%" if out["ok"] else
                   f"{fmt(q_dims)} vs {fmt(r_dims)} on the mat: {pct}% apart, more than one piece can be")
    return out


def compare(query_fp, ref_fp, *, descriptor: str | None = None, effort: int | None = None) -> dict:
    """Score one capture against one registered fingerprint: the page's confidence and
    verdict under the chosen descriptor, every descriptor's own aggregate, the pairs
    and views that carried the score, the mat size check, and the reasons in words."""
    q, r = as_fp(query_fp), as_fp(ref_fp)
    qf, rf = frames_of(q), frames_of(r)
    desc = descriptor if descriptor in DESCRIPTORS else pick_descriptor(q, r)
    eff = effort_of(qf) if effort is None else int(effort)
    breakdown: dict[str, dict] = {}
    pairs_by: dict[str, list[dict]] = {}
    for d in DESCRIPTORS:
        q_has = sum(1 for fr in qf if (fr.get("emb") if d == "dino" else fr.get("dhash") if d == "dhash" else fr.get("chist")))
        r_has = sum(1 for fr in rf if (fr.get("emb") if d == "dino" else fr.get("dhash") if d == "dhash" else fr.get("chist")))
        if not q_has or not r_has:
            breakdown[d] = {"available": False, "confidence": 0, "views_matched": 0,
                            "shots_with_descriptor": q_has, "reference_views_with_descriptor": r_has,
                            "why": (f"the new capture has no {DESCRIPTOR_WORDS[d]} on any shot" if not q_has
                                    else f"the registered piece has no {DESCRIPTOR_WORDS[d]} on file")}
            pairs_by[d] = []
            continue
        pairs = match_frames(qf, rf, d)
        agg = aggregate(pairs)
        cls, label = verdict(agg, eff)
        agg.update({"available": True, "verdict": cls, "label": label,
                    "shots_with_descriptor": q_has, "reference_views_with_descriptor": r_has})
        breakdown[d] = agg
        pairs_by[d] = pairs
    head = breakdown[desc]
    if not head.get("available"):
        # fall back the way the page would: colour, then shape
        for d in ("color", "dhash", "dino"):
            if breakdown[d].get("available"):
                desc, head = d, breakdown[d]
                break
    conf = int(head.get("confidence", 0))
    cls, label = (head.get("verdict", "inconc"), head.get("label", "Inconclusive")) if head.get("available") \
        else ("inconc", "Inconclusive")
    size = size_check(dimensions_of(q), dimensions_of(r))
    reasons: list[str] = []
    caveats: list[str] = []
    size_conflict = size["ok"] is False
    if size_conflict and cls == "match":
        cls, label = "inconc", "Inconclusive"
        caveats.append("The pictures matched but the mat sizes do not; held at inconclusive.")
    if head.get("available"):
        reasons.append(f"{_cap(DESCRIPTOR_WORDS[desc])}: {conf}/100 - the {min(TOP_SIMS, head['strong'])} strongest "
                       f"of {len(qf)} shots average {head['quality']:.2f} against the registered views, "
                       f"{head['views_matched']} distinct reference view(s) matched at {STRONG_T:.2f} or better "
                       f"(spread {head['spread']:.2f} of {SPREAD_TARGET} views).")
        if head["shots_with_descriptor"] < len(qf):
            reasons.append(f"Only {head['shots_with_descriptor']} of {len(qf)} shots carry the {DESCRIPTOR_WORDS[desc]}; "
                           "the rest score 0 under it.")
    else:
        reasons.append("Neither side has a descriptor the other also has, so nothing could be compared.")
    for d in DESCRIPTORS:
        if d == desc:
            continue
        b = breakdown[d]
        if b.get("available"):
            reasons.append(f"{_cap(DESCRIPTOR_WORDS[d])}, for comparison: {b['confidence']}/100, "
                           f"{b['views_matched']} view(s) matched.")
        elif d == "dino":
            reasons.append("No AI texture comparison: " + b["why"] + ". Colour alone cannot separate two "
                           "pieces of the same palette.")
    reasons.append("Size: " + size["text"] + ".")
    rsum = summary(r)
    if rsum.get("rating") is not None and rsum["rating"] < 75:
        caveats.append(f"The registered Glass ID is only {rsum['rating']}/100 ({rsum.get('tier') or 'weak'}); "
                       "a thin reference matches less and mismatches more.")
    if eff < MIN_EFFORT:
        caveats.append(f"{eff} sharp shot(s) so far; the page does not declare a no-match before {MIN_EFFORT}.")
    if desc == "color":
        caveats.append("Colour is view-stable but cannot tell two similarly coloured pieces apart; "
                       "a colour-only match is weaker evidence than it looks.")
    pairs = sorted(pairs_by.get(desc) or [], key=lambda p: -p["sim"])
    return {"descriptor": desc, "confidence": conf, "verdict": cls, "label": label,
            "quality": head.get("quality", 0.0), "spread": head.get("spread", 0.0),
            "views_matched": head.get("views_matched", 0), "shots": len(qf), "effort": eff,
            "reference_views": len(rf), "breakdown": breakdown, "pairs": pairs[:12],
            "carried_by": head.get("carried_by", {"shots": [], "reference_views": []}),
            "size": size, "size_conflict": size_conflict, "reasons": reasons, "caveats": caveats,
            "thresholds": thresholds()}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def thresholds() -> dict:
    return {"SIM_LO": SIM_LO, "SIM_HI": SIM_HI, "STRONG_T": STRONG_T, "SPREAD_TARGET": SPREAD_TARGET,
            "MATCH_READY": MATCH_READY, "NO_MATCH": NO_MATCH, "MIN_EFFORT": MIN_EFFORT,
            "MIN_CELLS_MATCH": MIN_CELLS_MATCH, "SIZE_TOLERANCE": SIZE_TOLERANCE,
            "AMBIGUOUS_MARGIN": AMBIGUOUS_MARGIN}


def candidates(query_fp, refs: list[dict], *, k: int = 5, descriptor: str | None = None,
               effort: int | None = None) -> dict:
    """Rank registered pieces against one capture. ``refs`` are the dicts ``public_refs``
    and ``my_refs`` build (each with a ``fingerprint``). Returns the top ``k`` with the
    full comparison each, the margin between the first two, and whether that margin
    is too thin to call."""
    q = as_fp(query_fp)
    scored = []
    for ref in refs or []:
        fp = ref.get("fingerprint")
        if not fp:
            continue
        try:
            c = compare(q, fp, descriptor=descriptor, effort=effort)
        except Exception as ex:  # noqa: BLE001 - one bad fingerprint must not sink the search
            c = {"descriptor": "color", "confidence": 0, "verdict": "inconc", "label": "Inconclusive",
                 "reasons": [f"could not be compared: {ex}"], "caveats": [], "breakdown": {},
                 "pairs": [], "views_matched": 0, "size": {}, "size_conflict": False}
        item = {kk: vv for kk, vv in ref.items() if kk != "fingerprint"}
        item["fingerprint"] = summary(fp)
        item.update(c)
        scored.append(item)
    scored.sort(key=lambda it: (-it["confidence"], -it.get("views_matched", 0), str(it.get("title") or "")))
    top = scored[:max(1, int(k))]
    out = {"ok": True, "descriptor": top[0]["descriptor"] if top else (descriptor or "color"),
           "candidates": top, "considered": len(scored),
           "with_embeddings": sum(1 for r in refs or [] if has_embeddings(r.get("fingerprint"))),
           "query": {"shots": len(frames_of(q)), "has_embeddings": has_embeddings(q),
                     "dimensions_mm": dimensions_of(q)},
           "margin": None, "ambiguous": False, "caveats": [], "thresholds": thresholds()}
    if len(top) >= 2:
        out["margin"] = top[0]["confidence"] - top[1]["confidence"]
        out["ambiguous"] = out["margin"] < AMBIGUOUS_MARGIN and top[1]["confidence"] >= NO_MATCH
        if out["ambiguous"]:
            out["caveats"].append(f"The two best candidates are {out['margin']} point(s) apart "
                                  f"({top[0]['confidence']} and {top[1]['confidence']}); these shots do not "
                                  "separate them. Take more angles, or compare them side by side.")
    for it in top:
        it["margin"] = out["margin"] if it is top[0] else None
    if not scored:
        out["caveats"].append("No registered piece with a Glass ID to compare against.")
    if top and not has_embeddings(q) and out["with_embeddings"]:
        out["caveats"].append("This capture has no AI samples; only colour and shape could be compared. "
                              "Turn AI on in the capture app for texture.")
    if top and top[0]["confidence"] < NO_MATCH:
        out["caveats"].append("Nothing on file comes close; if the piece is registered, the capture "
                              "may be too different from its registration (lighting, framing, descriptor).")
    return out


# =============================================================================== the references
def _load_json(s) -> dict:
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _row_fp(row) -> dict | None:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    manifest = _load_json(row["manifest_json"] if "manifest_json" in keys else None)
    fp = manifest.get("fingerprint")
    if not isinstance(fp, dict) and "fingerprint_json" in keys:
        fp = _load_json(row["fingerprint_json"])
    return fp if isinstance(fp, dict) and fp.get("frames") else None


def _has_table(conn, name: str) -> bool:
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                 (name,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def _ref_public(row, fp: dict) -> dict:
    rid = str(row["_row_id"])
    return {"ref": f"public:{rid}", "kind": "public", "id": rid, "row_id": rid,
            "title": (row["title"] or "").strip(), "maker": (row["maker"] or "").strip(),
            "year": row["year"] or "", "dimensions": row["dimensions"] or "",
            "image": f"/api/objects/{rid}/image", "href": OBJECT_URL.format(id=rid),
            "check": CHECK_URL.format(id=rid), "published_at": row["published_at"],
            "fingerprint": fp}


def public_refs(conn, *, include_pending: bool = False, limit: int = 5000) -> list[dict]:
    """Every public piece (approved rows - the publication gate) with a Glass ID."""
    if not _has_table(conn, "objects"):
        return []
    from central import approvals
    approvals.ensure_approvals(conn)
    cols = "_row_id, title, maker, year, dimensions, manifest_json, fingerprint_json, published_at"
    if include_pending:
        sql = (f"SELECT {cols} FROM objects WHERE _row_id NOT IN "
               "(SELECT row_id FROM _approvals WHERE tbl = ? AND status = 'rejected')")
    else:
        sql = f"SELECT {cols} FROM objects WHERE {approvals.approved_subquery()}"
    sql += f" ORDER BY published_at DESC, _row_id DESC LIMIT {int(limit)}"
    out = []
    for row in conn.execute(sql, ("objects",)).fetchall():
        try:
            fp = _row_fp(row)
        except Exception:  # noqa: BLE001
            fp = None
        if fp:
            out.append(_ref_public(row, fp))
    return out


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _db_key() -> str:
    import os

    from central import dbconn
    if dbconn.using_turso():
        return "turso:" + os.environ.get("TURSO_DATABASE_URL", "")
    return "sqlite:" + str(dbconn.local_path())


def cached_public_refs(conn=None, *, max_age: float = CACHE_TTL, force: bool = False) -> list[dict]:
    """``public_refs`` reused for ``max_age`` seconds per database (the fingerprints are
    big JSON blobs; parsing them on every lookup would be the slow part)."""
    key = _db_key()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and not force and time.time() - hit[0] < max_age:
            return hit[1]
    own = conn is None
    if own:
        from central.dbconn import connect
        conn = connect()
    try:
        refs = public_refs(conn)
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    with _LOCK:
        _CACHE[key] = (time.time(), refs)
    return refs


def invalidate() -> None:
    with _LOCK:
        _CACHE.clear()


def my_refs(store, uid: str) -> list[dict]:
    """The signed-in person's own pieces with a Glass ID (their Glowtbook store), so a
    capture is checked against what they have registered but not yet published too."""
    if store is None or not uid:
        return []
    out = []
    rows = store.execute("SELECT id, title, maker, year, dimensions, fingerprint_json, fingerprint_at "
                         "FROM object WHERE user_id=? AND fingerprint_json IS NOT NULL ORDER BY id DESC",
                         (uid,)).fetchall()
    try:
        with_image = {r[0] for r in store.execute(
            "SELECT DISTINCT object_id FROM object_image WHERE user_id=? AND role!='video'", (uid,)).fetchall()}
    except Exception:  # noqa: BLE001
        with_image = set()
    for row in rows:
        fp = _load_json(row["fingerprint_json"])
        if not fp.get("frames"):
            continue
        oid = int(row["id"])
        out.append({"ref": f"mine:{oid}", "kind": "mine", "id": oid, "row_id": None,
                    "title": (row["title"] or "").strip(), "maker": (row["maker"] or "").strip(),
                    "year": row["year"] or "", "dimensions": row["dimensions"] or "",
                    "image": f"/api/me/objects/{oid}/image" if oid in with_image else None,
                    "href": MINE_URL.format(id=oid),
                    "check": MINE_CHECK_URL.format(id=oid), "registered_at": row["fingerprint_at"],
                    "fingerprint": fp})
    return out


def all_refs(conn, store, uid: str | None, *, exclude=()) -> list[dict]:
    """Public pieces plus the caller's own, without duplicates: a published piece of
    mine appears once, as the public record."""
    skip = {str(x) for x in (exclude or ())}
    refs = [r for r in cached_public_refs(conn) if r["ref"] not in skip and r["id"] not in skip]
    mine = my_refs(store, uid) if uid else []
    if mine and conn is not None:
        # a piece of mine that is published is already in the public list (same content hash)
        try:
            hashes = {row["content_hash"]: row["_row_id"] for row in
                      conn.execute("SELECT content_hash, _row_id FROM objects").fetchall()}
            cons = {row["object_id"]: row["content_hash"] for row in
                    store.execute("SELECT object_id, content_hash FROM contribution WHERE user_id=? "
                                  "ORDER BY id", (uid,)).fetchall()}
            pub_ids = {r["id"] for r in refs}
            mine = [m for m in mine if hashes.get(cons.get(m["id"])) not in pub_ids]
        except Exception:  # noqa: BLE001
            pass
    return refs + [m for m in mine if m["ref"] not in skip and str(m["id"]) not in skip]


def _strip(ref: dict) -> dict:
    out = {k: v for k, v in ref.items() if k != "fingerprint"}
    out["fingerprint"] = summary(ref.get("fingerprint"))
    return out


def lookup(conn, store, uid: str | None, q: str, *, limit: int = 20) -> dict:
    """Find registered pieces by Glass ID (the record id), title, maker or the words on
    the record; the caller's own registered pieces too when signed in."""
    q = (q or "").strip()
    out = {"q": q, "public": [], "mine": []}
    if not q:
        return out
    ql = q.lower()
    toks = [t for t in re.split(r"\s+", ql) if t]
    if _has_table(conn, "objects"):
        from central import approvals
        approvals.ensure_approvals(conn)
        like = f"%{q}%"
        rows = conn.execute(
            f"SELECT _row_id, title, maker, year, dimensions, manifest_json, fingerprint_json, published_at, "
            f"contributor, description FROM objects WHERE {approvals.approved_subquery()} AND "
            "(_row_id LIKE ? OR title LIKE ? OR maker LIKE ? OR contributor LIKE ? OR description LIKE ?) "
            "ORDER BY published_at DESC LIMIT ?",
            ("objects", q + "%", like, like, like, like, int(limit) * 3)).fetchall()
        for row in rows:
            fp = _row_fp(row)
            if not fp:
                continue
            hay = " ".join(str(row[k] or "") for k in ("_row_id", "title", "maker", "contributor", "description")).lower()
            if all(t in hay for t in toks):
                out["public"].append(_strip(_ref_public(row, fp)))
            if len(out["public"]) >= limit:
                break
    if uid and store is not None:
        for m in my_refs(store, uid):
            hay = f"{m['id']} {m['title']} {m['maker']}".lower()
            if ql == str(m["id"]) or all(t in hay for t in toks):
                out["mine"].append(_strip(m))
            if len(out["mine"]) >= limit:
                break
    return out


def recent(conn, store, uid: str | None, *, limit: int = 8) -> dict:
    """What a person reaches for on the phone: their latest capture sessions, their
    latest registered pieces, and the newest public pieces with a Glass ID."""
    out = {"captures": [], "mine": [], "public": []}
    if uid:
        try:
            from central import captures
            out["captures"] = captures.list_manifests(conn, uid=uid, limit=limit)["items"]
        except Exception:  # noqa: BLE001
            out["captures"] = []
        out["mine"] = [_strip(m) for m in my_refs(store, uid)[:limit]]
    out["public"] = [_strip(r) for r in cached_public_refs(conn)[:limit]]
    return out


def piece(conn, store, uid: str | None, ref: str) -> dict | None:
    """The reference bundle the compare panel shows: the piece's words, every stored
    photo, the Glass ID summary and, for one of mine, the capture sessions that hold
    its registration frames."""
    kind, _, ident = (ref or "").partition(":")
    if kind == "public" and ident:
        if not _has_table(conn, "objects"):
            return None
        from central import approvals
        approvals.ensure_approvals(conn)
        row = conn.execute("SELECT _row_id, title, maker, year, techniques, dimensions, description, manifest_json, "
                           f"fingerprint_json, published_at FROM objects WHERE _row_id=? AND {approvals.approved_subquery()}",
                           (ident, "objects")).fetchone()
        if not row:
            return None
        fp = _row_fp(row)
        n_img = 0
        if _has_table(conn, "object_images"):
            n_img = conn.execute("SELECT COUNT(*) FROM object_images WHERE object_row_id=?", (ident,)).fetchone()[0]
        return {"ref": ref, "kind": "public", "id": ident, "row_id": ident, "title": row["title"] or "",
                "maker": row["maker"] or "", "year": row["year"] or "", "techniques": row["techniques"] or "",
                "dimensions": row["dimensions"] or "", "description": (row["description"] or "")[:400],
                "images": [f"/api/objects/{ident}/image?i={i}" for i in range(int(n_img))],
                "href": OBJECT_URL.format(id=ident), "check": CHECK_URL.format(id=ident),
                "fingerprint": summary(fp) if fp else None, "has_fingerprint": bool(fp),
                "fingerprint_url": f"/api/objects/{ident}/fingerprint" if fp else None,
                "published_at": row["published_at"], "captures": []}
    if kind == "mine" and ident and uid and store is not None:
        try:
            oid = int(ident)
        except ValueError:
            return None
        row = store.execute("SELECT * FROM object WHERE id=? AND user_id=?", (oid, uid)).fetchone()
        if not row:
            return None
        fp = _load_json(row["fingerprint_json"]) if row["fingerprint_json"] else None
        imgs = store.execute("SELECT id, role FROM object_image WHERE object_id=? AND user_id=? AND role!='video' "
                             "ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id", (oid, uid)).fetchall()
        caps = []
        try:
            from central import captures
            caps = [c for c in captures.list_manifests(conn, uid=uid, object_id=oid, limit=5)["items"]]
        except Exception:  # noqa: BLE001
            caps = []
        return {"ref": ref, "kind": "mine", "id": oid, "row_id": None, "title": row["title"] or "",
                "maker": row["maker"] or "", "year": row["year"] or "", "techniques": row["techniques"] or "",
                "dimensions": row["dimensions"] or "", "description": (row["description"] or "")[:400],
                "images": [f"/api/me/objects/{oid}/image"] if imgs else [],
                "href": MINE_URL.format(id=oid), "check": MINE_CHECK_URL.format(id=oid),
                "fingerprint": summary(fp) if fp and fp.get("frames") else None,
                "has_fingerprint": bool(fp and fp.get("frames")),
                "fingerprint_url": f"/api/me/objects/{oid}/fingerprint" if fp and fp.get("frames") else None,
                "registered_at": row["fingerprint_at"], "captures": caps}
    return None


# =============================================================================== descriptors from pixels
AW, AR = 160, 0.75
GRAD_T = 90.0
CH_HB, CH_SB = 16, 4
CROP = 0.6
HUE_NAMES = [(15, "red"), (42, "orange"), (70, "yellow"), (160, "green"), (200, "cyan"),
             (255, "blue"), (290, "violet"), (335, "magenta"), (360, "red")]


def _rgb_array(image_bytes: bytes, aw: int = AW, ar: float = AR):
    import io

    from PIL import Image
    ah = int(round(aw * ar))
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((aw, ah), Image.BILINEAR)
    if _np is None:
        raise RuntimeError("numpy is needed to compute descriptors from an image")
    return _np.asarray(im, dtype=float), aw, ah


def white_balance(d):
    """The page's mat-anchored white balance: the brightest 15% of pixels are the
    illuminant; scale the channels so their mean is neutral. A no-op when fewer than
    30 pixels qualify."""
    lum = 0.299 * d[..., 0] + 0.587 * d[..., 1] + 0.114 * d[..., 2]
    mx = float(lum.max()) if lum.size else 0.0
    sel = lum >= mx * 0.85
    if int(sel.sum()) < 30:
        return d
    means = d[sel].mean(axis=0)
    avg = float(means.mean())
    k = _np.array([avg / (m or 1.0) for m in means])
    # the page writes back into a Uint8ClampedArray: clamp, then round to the nearest integer
    return _np.rint(_np.minimum(255.0, d * k))


def colour_hist(d, w: int, h: int):
    """The page's ``colorHist``: a 16x4 hue-saturation histogram of the central region,
    per mille, or None when fewer than 40 pixels are coloured."""
    x0, x1, y0, y1 = int(w * 0.30), int(w * 0.70), int(h * 0.20), int(h * 0.80)
    reg = d[y0:y1, x0:x1].reshape(-1, 3)
    mx = reg.max(axis=1)
    mn = reg.min(axis=1)
    df = mx - mn
    s = _np.where(mx > 0, df / _np.where(mx > 0, mx, 1), 0.0)
    v = mx / 255.0
    r, g, b = reg[:, 0], reg[:, 1], reg[:, 2]
    dfs = _np.where(df > 0, df, 1.0)
    hue = _np.where(mx == r, ((g - b) / dfs) % 6, _np.where(mx == g, (b - r) / dfs + 2, (r - g) / dfs + 4)) * 60
    hue = _np.where(df > 0, hue, 0.0)
    hue = _np.where(hue < 0, hue + 360, hue)
    keep = (s > 0.24) & (v > 0.16) & (v < 0.96)
    cnt = int(keep.sum())
    if cnt < 40:
        return None
    hb = _np.minimum(CH_HB - 1, _np.floor(hue[keep] / 360 * CH_HB)).astype(int)
    sb = _np.minimum(CH_SB - 1, _np.floor(s[keep] * CH_SB)).astype(int)
    hist = _np.bincount(hb * CH_SB + sb, minlength=CH_HB * CH_SB).astype(float)
    return [js_round(x / cnt * 1000) for x in hist]


def dhash_of(g, w: int, h: int) -> list[int]:
    """The page's ``dhash``: 9x8 samples of the central 60%, row-wise gradient bits,
    packed as two unsigned 32-bit ints ``[hi, lo]``."""
    bw, bh = 9, 8
    ox, oy, cw, ch = w * (1 - CROP) / 2, h * (1 - CROP) / 2, w * CROP, h * CROP
    sm = [[0.0] * bw for _ in range(bh)]
    for y in range(bh):
        for x in range(bw):
            sx = min(w - 1, int(math.floor(ox + (x + .5) * cw / bw)))
            sy = min(h - 1, int(math.floor(oy + (y + .5) * ch / bh)))
            sm[y][x] = float(g[sy][sx])
    hi = lo = 0
    bit = 0
    for y in range(bh):
        for x in range(bw - 1):
            v = 1 if sm[y][x] < sm[y][x + 1] else 0
            if bit < 32:
                hi = ((hi << 1) | v) & 0xFFFFFFFF
            else:
                lo = ((lo << 1) | v) & 0xFFFFFFFF
            bit += 1
    return [hi, lo]


def dominant_colour(chist) -> dict | None:
    """The page's ``dominantColor``: the fullest hue/saturation bin as a name and hex."""
    if not chist:
        return None
    bi = max(range(len(chist)), key=lambda i: chist[i])
    if chist[bi] <= 0:
        return None
    hb, sat = bi // CH_SB, ((bi % CH_SB) + 0.5) / CH_SB
    hue = (hb + 0.5) / CH_HB * 360
    name = next(n for lim, n in HUE_NAMES if hue <= lim)
    c = sat * 0.85
    x = c * (1 - abs((hue / 60) % 2 - 1))
    m = 0.85 - c
    h6 = hue / 60
    rgb = ([c, x, 0] if h6 < 1 else [x, c, 0] if h6 < 2 else [0, c, x] if h6 < 3 else
           [0, x, c] if h6 < 4 else [x, 0, c] if h6 < 5 else [c, 0, x])
    return {"name": name, "hex": "#" + "".join(f"{int(round((z + m) * 255)):02x}" for z in rgb)}


def descriptors(image_bytes: bytes) -> dict:
    """The page's ``analyze()`` for one picture: sharpness (Laplacian variance), detail
    (gradient fraction), dHash on the grey image and the white-balanced colour
    histogram - computed on the same 160x120 buffer the page uses."""
    d, w, h = _rgb_array(image_bytes)
    g = 0.299 * d[..., 0] + 0.587 * d[..., 1] + 0.114 * d[..., 2]
    inner = g[1:-1, 1:-1]
    lap = 4 * inner - g[1:-1, :-2] - g[1:-1, 2:] - g[:-2, 1:-1] - g[2:, 1:-1]
    n = lap.size
    sharp = float((lap * lap).sum() / n - (lap.sum() / n) ** 2)
    gx = g[1:-1, 2:] - g[1:-1, :-2]
    gy = g[2:, 1:-1] - g[:-2, 1:-1]
    detail = float(((gx * gx + gy * gy) > GRAD_T).sum() / n)
    dh = dhash_of(g, w, h)
    ch = colour_hist(white_balance(d.copy()), w, h)
    return {"sharp": round(sharp, 1), "detail": round(detail, 4), "dhash": dh, "chist": ch}


def fingerprint_from_images(images: list[tuple[str, bytes]], *, created: str | None = None) -> dict:
    """A fingerprint document (descriptors only) from pictures, for a bench or a manifest
    that carries images without descriptors. Not a substitute for the page: the page
    sees the live camera buffer, this sees stored JPEGs."""
    frames = []
    for i, (name, data) in enumerate(images):
        try:
            desc = descriptors(data)
        except Exception:  # noqa: BLE001
            continue
        frames.append({"file": name, "seq": i + 1, "cell": "c" + name, "sector": -1, **desc})
    chists = [f["chist"] for f in frames if f.get("chist")]
    agg = None
    if chists:
        agg = [js_round(sum(c[i] for c in chists) / len(chists)) for i in range(len(chists[0]))]
    return {"tool": "glassid-descriptors", "version": 1,
            "created": created or datetime.now(timezone.utc).isoformat(),
            "metadata": {"dominantColor": dominant_colour(agg), "hasEmbeddings": False,
                         "source": "central.glassid.descriptors"},
            "frames": frames}
