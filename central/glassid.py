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
# --- rules v2 (glassid-2), mirrored in glassid-kit.js -----------------------------------
# One-to-one: every registered view can carry at most one shot, so a reference with many
# views stops lending its best view to every shot (the best-of-N bias of the assessment).
# With the Photo Mat in view on both sides, a shot may only pair with a registered view
# taken from a compatible angle, after the best of twelve 30-degree turns of the piece.
POSE_EL_TOL = 25.0              # degrees of elevation apart a compatible pair may be
POSE_AZ_TOL = 45.0              # degrees of azimuth apart, after the turn
POSE_STEP = 30                  # the turns tried: 0, 30, ... 330
POSE_MIN = 3                    # shots with a mat pose on each side before angles are used
MIN_DESC_SHOTS = 3              # shots that must carry the descriptor before anything is said
COLOUR_SEEN_MIN = 0.5           # colour sees less than this share of the shots -> not enough
RULES = ("v1", "v2", "v3")
RULES_DEFAULT = "v3"
# v3 (24 November 2026): the AI vectors are centred on a fixed, versioned registry mean
# (central/glassid_v3.json, mirrored in glassid-kit.js) and renormalised before the cosine,
# whose useful range [V3_DINO_LO, V3_DINO_HI] is stretched onto 0..1 so STRONG_T and the
# verdict thresholds keep their meaning; and when colour and AI can both be read, the
# score is the lower of the two - both have to agree. Colour is compared as in v2 (square-
# root counts ranked pieces slightly better but let more different pieces past 70 on the
# 29-piece bench, so it was left out). See docs/GLASS-ID-MATCHING-ASSESSMENT.md section 10.
V3_DINO_LO, V3_DINO_HI = 0.0, 0.5
# descriptor keys on a frame; "_p" are computed on the piece (cropped by the mat), the
# others on the whole frame. Preference order for the headline, best first.
DESC_KEY = {"dino_p": "emb_p", "dino": "emb", "color_p": "chist_p", "color": "chist", "dhash": "dhash"}
DESCRIPTORS = ("dino_p", "dino", "color_p", "color", "dhash")
DESCRIPTOR_WORDS = {"dino_p": "AI texture on the piece (DINOv2)", "dino": "AI texture (DINOv2)",
                    "color_p": "colour of the piece", "color": "colour histogram", "dhash": "shape hash (dHash)"}
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


def _v3_file() -> dict:
    global _V3
    if _V3 is None:
        try:
            with open(__file__.rsplit("/", 1)[0] + "/glassid_v3.json") as fh:
                _V3 = json.load(fh)
        except Exception:  # noqa: BLE001 - without the mean, v3 compares the AI vectors uncentred
            _V3 = {}
    return _V3


_V3 = None


def dino_mean(dim: int):
    m = _nums(_v3_file().get("dino_mean"))
    return m if m and len(m) == dim else None


def _v3_vec(v, desc: str):
    """A frame's vector as v3 compares it: AI centred on the registry mean; colour as is."""
    v = _nums(v)
    if v is None:
        return None
    if desc in ("dino", "dino_p"):
        m = dino_mean(len(v))
        return [x - y for x, y in zip(v, m)] if m else v
    return v


def v3_stretch(c: float) -> float:
    return max(0.0, min(1.0, (c - V3_DINO_LO) / (V3_DINO_HI - V3_DINO_LO)))


def frame_sim(q: dict, r: dict, desc: str, rules: str = "v2") -> float:
    k = DESC_KEY.get(desc, "chist")
    if desc == "dhash":
        return dhash_sim(hamming(q.get("dhash"), r.get("dhash")))
    a, b = q.get(k), r.get(k)
    if not a or not b:
        return 0.0
    if rules == "v3":
        a, b = _v3_vec(a, desc), _v3_vec(b, desc)
        if desc in ("dino", "dino_p"):
            return v3_stretch(cosine(a, b))
    if desc in ("dino", "dino_p"):
        return cosine(a, b)
    return hist_corr(a, b)


def has_desc(fr: dict, desc: str) -> bool:
    v = fr.get(DESC_KEY.get(desc, "chist"))
    return isinstance(v, (list, tuple)) and len(v) > 0


def pose_of(fr: dict):
    """(azimuth, elevation) in degrees from the Photo Mat, or None. Azimuth is measured
    round the placement area in the mat's own frame, so it means the same on any phone."""
    m = fr.get("mat") if isinstance(fr, dict) else None
    if not isinstance(m, dict):
        return None
    try:
        az, el = float(m.get("az")), float(m.get("el"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(az) and math.isfinite(el)):
        return None
    return az % 360.0, el


def _circ(d: float) -> float:
    return abs((d + 180.0) % 360.0 - 180.0)


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
    return any(has_desc(fr, "dino") or has_desc(fr, "dino_p") for fr in frames_of(fp))


def has_pose(fp) -> bool:
    return any(pose_of(fr) for fr in frames_of(fp))


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
            "views_with_embeddings": sum(1 for fr in frames if has_desc(fr, "dino") or has_desc(fr, "dino_p")),
            "has_embeddings": bool(md.get("hasEmbeddings")) or has_embeddings(f),
            "views_with_mat": sum(1 for fr in frames if pose_of(fr)),
            "dominant_colour": dc, "dimensions_mm": dimensions_of(f)}


def pick_descriptor(query_fp, ref_fp) -> str:
    """The headline descriptor: the first in DESCRIPTORS (AI on the piece, AI on the frame,
    colour of the piece, colour of the frame) that at least MIN_DESC_SHOTS frames carry on
    both sides; colour of the frame when nothing does. Old registrations carry only the
    frame-wide descriptors and are compared on those, so they keep working unchanged."""
    qf, rf = frames_of(query_fp), frames_of(ref_fp)
    for d in DESCRIPTORS:
        if d == "dhash":
            continue
        if sum(1 for f in qf if has_desc(f, d)) >= MIN_DESC_SHOTS and sum(1 for f in rf if has_desc(f, d)) >= MIN_DESC_SHOTS:
            return d
    for d in ("dino", "color"):
        if any(has_desc(f, d) for f in qf) and any(has_desc(f, d) for f in rf):
            return d
    return "color"


# =============================================================================== matching
def _sim_matrix(qf: list[dict], rf: list[dict], desc: str, rules: str = "v2") -> list[list[float]]:
    """similarity[i][j] of query frame i against reference frame j for one descriptor."""
    if not qf or not rf:
        return []
    if desc == "dhash" or _np is None:
        return [[frame_sim(q, r, desc, rules) for r in rf] for q in qf]
    key = DESC_KEY.get(desc, "chist")

    def rows(frames):
        vecs, ok = [], []
        for fr in frames:
            v = _v3_vec(fr.get(key), desc) if rules == "v3" else _nums(fr.get(key))
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
        return [[frame_sim(q, r, desc, rules) for r in rf] for q in qf]
    if desc in ("color", "color_p"):
        Q = Q - Q.mean(axis=1, keepdims=True)
        R = R - R.mean(axis=1, keepdims=True)
    qn = _np.linalg.norm(Q, axis=1, keepdims=True)
    rn = _np.linalg.norm(R, axis=1, keepdims=True)
    qn[qn == 0] = 1.0
    rn[rn == 0] = 1.0
    S = (Q / qn) @ (R / rn).T
    if rules == "v3" and desc in ("dino", "dino_p"):
        S = (S - V3_DINO_LO) / (V3_DINO_HI - V3_DINO_LO)
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
                    "has_descriptor": has_desc(q, desc)})
    return out


def assign_frames(qf: list[dict], rf: list[dict], desc: str, rules: str = "v2") -> tuple[list[dict], dict]:
    """Rules v2: pair shots with registered views one-to-one. Candidate pairs at or above
    STRONG_T are taken strongest first; a view already taken cannot be taken again. When
    both sides have at least POSE_MIN shots with a mat pose, a pair of posed frames must
    also be within POSE_EL_TOL of elevation and POSE_AZ_TOL of azimuth once the piece is
    turned by one of the POSE_STEP offsets; every offset is tried and the best kept.

    Returns (pairs, info): one pair per shot in the shape of ``match_frames`` plus
    ``assigned`` (False for a shot whose best view was already taken or was at an
    incompatible angle: it is shown, not counted), and info {pose_used, turn}. Under
    rules v3 the similarities are v3's (see ``frame_sim``); the pairing is the same."""
    S = _sim_matrix(qf, rf, desc, rules)
    qp = [pose_of(f) for f in qf]
    rp = [pose_of(f) for f in rf]
    use_pose = sum(1 for p in qp if p) >= POSE_MIN and sum(1 for p in rp if p) >= POSE_MIN
    cand = []
    for i in range(len(qf)):
        row = S[i] if S else []
        for j, s in enumerate(row):
            s = round(float(s), 6)
            if s >= STRONG_T:
                cand.append((s, i, j))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))
    offsets = list(range(0, 360, POSE_STEP)) if use_pose else [None]
    best = None
    for off in offsets:
        used_q, used_r, chosen, resid = set(), set(), {}, 0.0
        for s, i, j in cand:
            if i in used_q or j in used_r:
                continue
            if off is not None and qp[i] and rp[j]:
                d = _circ(qp[i][0] - rp[j][0] - off)
                if abs(qp[i][1] - rp[j][1]) > POSE_EL_TOL or d > POSE_AZ_TOL:
                    continue
                resid += d
            used_q.add(i)
            used_r.add(j)
            chosen[i] = (s, j)
            if len(used_q) == len(qf) or len(used_r) == len(rf):
                break
        # more explained first, then more pairs, then the turn the pairs agree with best
        key = (round(sum(v[0] for v in chosen.values()), 6), len(chosen), -round(resid, 6))
        if best is None or key > best[0]:
            best = (key, off, chosen)
    _, off, chosen = best
    pairs = []
    for i, q in enumerate(qf):
        if i in chosen:
            s, j = chosen[i]
            assigned = True
        else:
            row = S[i] if S else []
            j = max(range(len(row)), key=lambda k: row[k]) if row else -1
            s = round(float(row[j]), 6) if j >= 0 else 0.0
            assigned = False
        r = rf[j] if j >= 0 else None
        sim = max(0.0, s)
        pairs.append({"shot": q.get("seq", i + 1), "sim": round(sim, 4), "dist": js_round((1 - sim) * 100),
                      "ref": r.get("file") if r else None, "ref_cell": r.get("cell") if r else None,
                      "ref_index": j if j >= 0 else None, "has_descriptor": has_desc(q, desc),
                      "assigned": assigned})
    return pairs, {"pose_used": use_pose, "turn": off}


def aggregate(pairs: list[dict], rules: str = "v1") -> dict:
    """The page's ``recompute`` -> a confidence from 0 to 100.

    v1: quality of the 8 strongest pairs x spread over distinct reference views.
    v2: the share of the capture the registration explains - each shot that carries
    the descriptor scores the similarity of the registered view it was paired with
    (one-to-one, at STRONG_T or better), or 0 when it was paired with none; the
    confidence is the mean. On the live registry (15 pieces, 17 November 2026) this cut
    colour's false accepts at 70 from 14% to 3% of different-piece pairs (see
    docs/GLASS-ID-MATCHING-ASSESSMENT.md, section 9). Quality and spread are still
    reported for the reasons."""
    strong = [p for p in pairs if p["sim"] >= STRONG_T and p.get("assigned", True)]
    top = sorted((p["sim"] for p in strong), reverse=True)[:TOP_SIMS]
    quality = sum(top) / len(top) if top else 0.0
    cells = sorted({str(p["ref_cell"]) for p in strong if p["ref_cell"] is not None})
    spread = min(1.0, len(cells) / SPREAD_TARGET)
    if rules in ("v2", "v3"):
        have = sum(1 for p in pairs if p.get("has_descriptor", True))
        conf = js_round(100 * sum(p["sim"] for p in strong) / have) if have else 0
    else:
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


def insufficient(desc: str, q_has: int, r_has: int, shots: int) -> str | None:
    """Rules v2: why these shots cannot support any verdict under ``desc``, or None. A
    pale, clear or dark piece gives the colour histogram almost no pixels, and before v2
    the empty comparison read as "No match here"."""
    if q_has < MIN_DESC_SHOTS:
        return (f"only {q_has} of {shots} shot(s) carry the {DESCRIPTOR_WORDS[desc]}; "
                f"{MIN_DESC_SHOTS} are needed before anything can be said")
    if r_has < MIN_DESC_SHOTS:
        return f"the registered piece has only {r_has} view(s) with the {DESCRIPTOR_WORDS[desc]}"
    if desc in ("color", "color_p") and shots and q_has < COLOUR_SEEN_MIN * shots:
        return (f"colour could be read on only {q_has} of {shots} shots - a pale, clear or very dark piece "
                "is described mostly by what is around it")
    return None


def fuse_with(desc: str, breakdown: dict) -> str | None:
    """Rules v3: the colour descriptor an AI headline must agree with - the piece's colour
    when both sides carry it, else the frame's - or None when the headline is not AI or no
    colour can be read well enough to say anything."""
    if desc not in ("dino", "dino_p"):
        return None
    for c in (("color_p", "color") if desc == "dino_p" else ("color", "color_p")):
        b = breakdown.get(c) or {}
        if b.get("available") and not b.get("insufficient"):
            return c
    return None


def compare(query_fp, ref_fp, *, descriptor: str | None = None, effort: int | None = None,
            rules: str = RULES_DEFAULT) -> dict:
    """Score one capture against one registered fingerprint: the page's confidence and
    verdict under the chosen descriptor, every descriptor's own aggregate, the pairs
    and views that carried the score, the mat size check, and the reasons in words.

    ``rules`` "v3" (default, 24 November 2026) is v2 with the AI vectors centred on the
    registry mean and an AI headline that must agree with colour (see V3_DINO_LO). "v2"
    pairs shots with registered views one-to-one, by mat angle when both sides have it,
    and says "Not enough to tell" when the descriptor cannot see the piece; "v1" is the arithmetic shipped before 17 November 2026 (best view per
    shot), kept so old numbers can be reproduced and compared."""
    rules = rules if rules in RULES else RULES_DEFAULT
    q, r = as_fp(query_fp), as_fp(ref_fp)
    qf, rf = frames_of(q), frames_of(r)
    desc = descriptor if descriptor in DESCRIPTORS else pick_descriptor(q, r)
    eff = effort_of(qf) if effort is None else int(effort)
    breakdown: dict[str, dict] = {}
    pairs_by: dict[str, list[dict]] = {}
    info_by: dict[str, dict] = {}
    for d in DESCRIPTORS:
        q_has = sum(1 for fr in qf if has_desc(fr, d))
        r_has = sum(1 for fr in rf if has_desc(fr, d))
        if not q_has or not r_has:
            breakdown[d] = {"available": False, "confidence": 0, "views_matched": 0,
                            "shots_with_descriptor": q_has, "reference_views_with_descriptor": r_has,
                            "why": (f"the new capture has no {DESCRIPTOR_WORDS[d]} on any shot" if not q_has
                                    else f"the registered piece has no {DESCRIPTOR_WORDS[d]} on file")}
            pairs_by[d] = []
            continue
        if rules != "v1":
            pairs, info = assign_frames(qf, rf, d, rules)
        else:
            pairs, info = match_frames(qf, rf, d), {"pose_used": False, "turn": None}
        agg = aggregate(pairs, rules)
        cls, label = verdict(agg, eff)
        why_not = insufficient(d, q_has, r_has, len(qf)) if rules != "v1" else None
        if why_not and cls != "match":
            cls, label = "insufficient", "Not enough to tell"
        agg.update({"available": True, "verdict": cls, "label": label, "insufficient": why_not,
                    "shots_with_descriptor": q_has, "reference_views_with_descriptor": r_has,
                    "pose_used": info["pose_used"], "turn": info["turn"]})
        breakdown[d] = agg
        pairs_by[d] = pairs
        info_by[d] = info
    head = breakdown[desc]
    if not head.get("available"):
        # fall back the way the page would: colour, then shape
        for d in ("color_p", "color", "dhash", "dino_p", "dino"):
            if breakdown[d].get("available"):
                desc, head = d, breakdown[d]
                break
    conf = int(head.get("confidence", 0))
    cls, label = (head.get("verdict", "inconc"), head.get("label", "Inconclusive")) if head.get("available") \
        else ("inconc", "Inconclusive")
    agreed = fuse_with(desc, breakdown) if rules == "v3" and descriptor not in DESCRIPTORS else None
    if agreed and breakdown[agreed]["confidence"] < conf:
        conf = int(breakdown[agreed]["confidence"])
        cls, label = breakdown[agreed]["verdict"], breakdown[agreed]["label"]
    size = size_check(dimensions_of(q), dimensions_of(r))
    reasons: list[str] = []
    caveats: list[str] = []
    size_conflict = size["ok"] is False
    if size_conflict and cls == "match":
        cls, label = "inconc", "Inconclusive"
        caveats.append("The pictures matched but the mat sizes do not; held at inconclusive.")
    if head.get("available"):
        how = ("each registered view paired with at most one shot" if rules != "v1" else
               "each shot against its best registered view")
        if rules != "v1":
            reasons.append(f"{_cap(DESCRIPTOR_WORDS[desc])}: {head['confidence']}/100 - {head['strong']} of "
                           f"{head['shots_with_descriptor']} shots found a registered view of their own at "
                           f"{STRONG_T:.2f} or better ({how}); the score is their mean similarity, a shot "
                           f"with no view counting 0. {head['views_matched']} distinct reference view(s) matched.")
        else:
            reasons.append(f"{_cap(DESCRIPTOR_WORDS[desc])}: {conf}/100 - the {min(TOP_SIMS, head['strong'])} strongest "
                           f"of {len(qf)} shots average {head['quality']:.2f} ({how}), "
                           f"{head['views_matched']} distinct reference view(s) matched at {STRONG_T:.2f} or better "
                           f"(spread {head['spread']:.2f} of {SPREAD_TARGET} views).")
        if agreed:
            reasons.append(f"Colour and AI texture must agree: {_cap(DESCRIPTOR_WORDS[agreed])} scored "
                           f"{breakdown[agreed]['confidence']}/100 and the AI {head['confidence']}/100; "
                           "the lower of the two is the score.")
        if head.get("pose_used"):
            reasons.append(f"Angles from the Photo Mat on both sides: shots were only paired with views taken from "
                           f"a similar angle, with the piece turned {head.get('turn') or 0} degrees between the two.")
        if head.get("insufficient"):
            reasons.append("Not enough to tell: " + head["insufficient"] + ".")
        elif head["shots_with_descriptor"] < len(qf):
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
        elif d == "dino" and not breakdown["dino_p"].get("available"):
            reasons.append("No AI texture comparison: " + b["why"] + ". Colour alone cannot separate two "
                           "pieces of the same palette.")
    reasons.append("Size: " + size["text"] + ".")
    rsum = summary(r)
    if rsum.get("rating") is not None and rsum["rating"] < 75:
        caveats.append(f"The registered Glass ID is only {rsum['rating']}/100 ({rsum.get('tier') or 'weak'}); "
                       "a thin reference matches less and mismatches more.")
    if eff < MIN_EFFORT:
        caveats.append(f"{eff} sharp shot(s) so far; the page does not declare a no-match before {MIN_EFFORT}.")
    if desc in ("color", "color_p"):
        caveats.append("Colour is view-stable but cannot tell two similarly coloured pieces apart; "
                       "a colour-only match is weaker evidence than it looks.")
    pairs = sorted(pairs_by.get(desc) or [], key=lambda p: (not p.get("assigned", True), -p["sim"]))
    return {"descriptor": desc, "rules": rules, "agreed_with": agreed, "confidence": conf, "verdict": cls, "label": label,
            "quality": head.get("quality", 0.0), "spread": head.get("spread", 0.0),
            "views_matched": head.get("views_matched", 0), "shots": len(qf), "effort": eff,
            "reference_views": len(rf), "breakdown": breakdown, "pairs": pairs[:12],
            "pose_used": bool(head.get("pose_used")), "turn": head.get("turn"),
            "carried_by": head.get("carried_by", {"shots": [], "reference_views": []}),
            "size": size, "size_conflict": size_conflict, "reasons": reasons, "caveats": caveats,
            "thresholds": thresholds()}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def thresholds() -> dict:
    return {"SIM_LO": SIM_LO, "SIM_HI": SIM_HI, "STRONG_T": STRONG_T, "SPREAD_TARGET": SPREAD_TARGET,
            "MATCH_READY": MATCH_READY, "NO_MATCH": NO_MATCH, "MIN_EFFORT": MIN_EFFORT,
            "MIN_CELLS_MATCH": MIN_CELLS_MATCH, "SIZE_TOLERANCE": SIZE_TOLERANCE,
            "AMBIGUOUS_MARGIN": AMBIGUOUS_MARGIN, "POSE_EL_TOL": POSE_EL_TOL, "POSE_AZ_TOL": POSE_AZ_TOL,
            "POSE_STEP": POSE_STEP, "POSE_MIN": POSE_MIN, "MIN_DESC_SHOTS": MIN_DESC_SHOTS,
            "COLOUR_SEEN_MIN": COLOUR_SEEN_MIN, "RULES": RULES_DEFAULT}


def candidates(query_fp, refs: list[dict], *, k: int = 5, descriptor: str | None = None,
               effort: int | None = None, rules: str = RULES_DEFAULT) -> dict:
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
            c = compare(q, fp, descriptor=descriptor, effort=effort, rules=rules)
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


# --- the Photo Mat, server side: the same pose, crop and strip correction as glassid-mat.js
PLACE_MM = (50.0, 83.0, 200.5, 174.0)          # the grey placement area (matdetect.PLACE_MM)
PLACE_CENTRE = ((PLACE_MM[0] + PLACE_MM[2]) / 2, (PLACE_MM[1] + PLACE_MM[3]) / 2)
STRIP_MM = {"x0": 15.0, "w": 15.5, "y0": 217.0, "y1": 229.5, "neutrals": 5}
MAT_CROP_H = 150.0
MAT_MIN_EL, MAT_TOP_EL = 12.0, 80.0


def _lin(v):
    v = _np.asarray(v, dtype=float) / 255.0
    return _np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _srgb(x):
    x = _np.clip(x, 0.0, 1.0)
    return 255.0 * _np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


def _neutral(rgb, H) -> dict:
    """The strip's five neutral patches through the homography (glassid-mat.js ``neutral``)."""
    h, w = rgb.shape[:2]
    patches = []
    for i in range(STRIP_MM["neutrals"]):
        cx = STRIP_MM["x0"] + STRIP_MM["w"] * i + STRIP_MM["w"] / 2
        cy = (STRIP_MM["y0"] + STRIP_MM["y1"]) / 2
        px = []
        for gx in range(-2, 3):
            for gy in range(-2, 3):
                v = H @ _np.array([cx + gx * 2.0, cy + gy * 1.5, 1.0])
                x, y = int(round(v[0] / v[2])), int(round(v[1] / v[2]))
                if not (0 <= x < w and 0 <= y < h):
                    return {"ok": False, "why": "colour strip not fully in view"}
                px.append(rgb[y, x])
        patches.append([float(_np.median([p[c] for p in px])) for c in range(3)])
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in patches]
    if any(lum[i] > lum[i - 1] - 4 for i in range(1, len(lum))):
        return {"ok": False, "why": "the grey patches don't read in order"}
    if lum[0] - lum[4] < 60:
        return {"ok": False, "why": "too little range between the white and black patches"}
    nominal = [float(x) for x in _lin([255, 192, 128, 64, 0])]
    gain, offset = [], []
    for c in range(3):
        X = [nominal[i] for i in range(5) if patches[i][c] < 250]
        Y = [float(_lin(patches[i][c])) for i in range(5) if patches[i][c] < 250]
        if len(X) < 3 or max(X) < 0.2:
            return {"ok": False, "why": "the grey patches are clipped"}
        mx, my = sum(X) / len(X), sum(Y) / len(Y)
        sxx = sum((x - mx) ** 2 for x in X)
        g = sum((x - mx) * (y - my) for x, y in zip(X, Y)) / sxx
        if not g >= 0.25:
            return {"ok": False, "why": "the strip is too dark to correct from"}
        gain.append(round(g, 5))
        offset.append(round(my - g * mx, 5))
    return {"ok": True, "white": patches[0], "black": patches[4], "gain": gain, "offset": offset}


def piece_colour(rgb, neutral: dict | None = None):
    """glassid-mat.js ``pieceColour``: the 16x4 hue-saturation histogram over the whole
    (cropped) buffer, per mille, after the strip's neutral correction when there is one."""
    d = rgb.astype(float)
    if neutral and neutral.get("ok"):
        g, o = _np.array(neutral["gain"]), _np.array(neutral["offset"])
        d = _np.rint(_srgb((_lin(d) - o) / g))
    reg = d.reshape(-1, 3)
    mx, mn = reg.max(axis=1), reg.min(axis=1)
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


def mat_descriptors(image_bytes: bytes, *, height_mm: float | None = None) -> dict | None:
    """What the capture pages record about the Photo Mat for one picture, computed from
    stored pixels with OpenCV: ``{"mat": {az, el, n, h, d, cc}, "chist_p", "crop"}``, or
    None when the mat is not usable (no OpenCV, fewer than two markers, too low). Close
    to the page's numbers, not bit for bit (another marker detector, another resampler)."""
    if _np is None:
        return None
    try:
        from glowtbook import matdetect
        if not matdetect.available():
            return None
        import cv2
        fr = matdetect._frame(image_bytes)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(fr, dict) or fr.get("error") or fr.get("H") is None:
        return None
    w, h = fr["image_px"]
    f = fr.get("f_est") or (w / 2) / math.tan(math.radians(65 / 2))
    K = _np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], float)
    obj = _np.hstack([fr["src"], _np.zeros((len(fr["src"]), 1))]).astype(_np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, fr["dst"].astype(_np.float64), K, None, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    R, _ = cv2.Rodrigues(rvec)
    C = (-R.T @ tvec).ravel()
    if C[2] >= -20:
        return None
    dx, dy, height = C[0] - PLACE_CENTRE[0], C[1] - PLACE_CENTRE[1], -C[2]
    az = (round(math.degrees(math.atan2(dy, dx)), 1) + 360) % 360
    el = math.degrees(math.atan2(height, math.hypot(dx, dy)))
    if el < MAT_MIN_EL:
        return None
    rgb = fr["img"][..., ::-1]
    neutral = _neutral(rgb, fr["H"])
    hz = -(height_mm or MAT_CROP_H)
    pts = _np.array([[x, y, z] for x, y in ((PLACE_MM[0], PLACE_MM[1]), (PLACE_MM[2], PLACE_MM[1]),
                                            (PLACE_MM[2], PLACE_MM[3]), (PLACE_MM[0], PLACE_MM[3])) for z in (0.0, hz)])
    proj, _ = cv2.projectPoints(pts, rvec, tvec, K, None)
    proj = proj.reshape(-1, 2)
    x0, x1 = max(0.0, proj[:, 0].min()), min(float(w), proj[:, 0].max())
    y0, y1 = max(0.0, proj[:, 1].min()), min(float(h), proj[:, 1].max())
    out = {"mat": {"az": round(az, 1), "el": round(el, 1), "n": len(fr["ids"]), "h": round(height), "d": round(math.hypot(dx, dy, height)),
                   "cc": "strip" if neutral.get("ok") else "none", "source": "server"}}
    if x1 - x0 >= 0.12 * w and y1 - y0 >= 0.12 * h:
        box = [int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))]
        small = cv2.resize(_np.ascontiguousarray(rgb[box[1]:box[3], box[0]:box[2]]), (128, 128), interpolation=cv2.INTER_LINEAR)
        out["chist_p"] = piece_colour(small, neutral)
        out["crop"] = [round(box[0] / w, 4), round(box[1] / h, 4), round(box[2] / w, 4), round(box[3] / h, 4)]
    if el >= MAT_TOP_EL:
        out["cell"] = "mtop"
    else:
        out["cell"] = f"m{int(az // 30) % 12}:{0 if el < 45 else 1}"
    return out


def upgrade_frames(fp: dict, images: dict[str, bytes], *, height_mm: float | None = None) -> int:
    """Fill in the Photo Mat fields (mat, chist_p, crop) on frames that lack them, from
    their stored pictures (``images`` by the frame's ``file``). Frames that already carry
    them (the page computed them) are left alone. Returns how many frames were upgraded.
    This is how a registration made before the mat-aware pages gains the new descriptors
    when its pictures are still at hand."""
    n = 0
    for fr in (fp.get("frames") or []):
        if not isinstance(fr, dict) or fr.get("mat"):
            continue
        data = images.get(fr.get("file") or "")
        if not data:
            continue
        m = mat_descriptors(data, height_mm=height_mm)
        if not m:
            continue
        fr["mat"] = m["mat"]
        if m.get("chist_p"):
            fr["chist_p"] = m["chist_p"]
            fr["crop"] = m["crop"]
        n += 1
    if n:
        fp.setdefault("metadata", {}).setdefault("mat", {})["server_upgraded"] = n
    return n


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
        fr = {"file": name, "seq": i + 1, "cell": "c" + name, "sector": -1, **desc}
        m = mat_descriptors(data)
        if m:
            fr["mat"], fr["cell"] = m["mat"], m["cell"]
            if m.get("chist_p"):
                fr["chist_p"], fr["crop"] = m["chist_p"], m["crop"]
        frames.append(fr)
    chists = [f["chist"] for f in frames if f.get("chist")]
    agg = None
    if chists:
        agg = [js_round(sum(c[i] for c in chists) / len(chists)) for i in range(len(chists[0]))]
    return {"tool": "glassid-descriptors", "version": 1,
            "created": created or datetime.now(timezone.utc).isoformat(),
            "metadata": {"dominantColor": dominant_colour(agg), "hasEmbeddings": False,
                         "source": "central.glassid.descriptors"},
            "frames": frames}
