#!/usr/bin/env python3
"""
Score Glass ID's matching against real (or staged) captures, every session against
every other, with the page's own rules - so the thresholds are set from data.

Sessions come from one of three places:

    --manifests DIR    capture-session folders (what scripts/pull_capture.py writes, or the
                       server's data/capture_manifests): the fingerprint each manifest carries
                       (a registration's descriptors, or a check's shots), labelled by the
                       piece it was about (the decision's object, else the manifest's object,
                       else the reference checked against, else the session id)
    --images DIR       DIR/<piece>/*.jpg - pictures grouped by piece; descriptors are computed
                       the way the page does (central.glassid.descriptors) and each piece's
                       pictures are split into two sessions, so same-piece pairs exist
    --registry         every public piece with a Glass ID (one session each; only
                       different-piece pairs exist, so only the false-accept side is measured)

Every ordered pair (query session, reference session) is scored with
``central.glassid.compare`` under the page's thresholds. Pairs with the same label are
positives (the same piece), the rest negatives. The report gives, at the shipped
thresholds, true/false accepts and rejects, precision and recall for "Match likely",
how many negatives sit in the inconclusive band, the separation between the positive
and negative score distributions, and the threshold that would separate them best -
per descriptor when more than one is available. It prints the matrix and writes the
numbers as JSON.

    python -m scripts.glassid_bench --manifests data/pulled_captures
    python -m scripts.glassid_bench --images bench/cubes --out data/glassid_bench.json
    python -m scripts.glassid_bench --registry

Honesty notes. Sessions staged from one photograph (the --images path with crops of a
single picture) are optimistic for same-piece pairs: same light, same lens, same
moment. Registry demo data carries one synthetic frame per piece and says nothing.
The numbers that matter come from real sessions: several captures of the same piece
on different days and phones, and siblings from one batch.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from central import glassid  # noqa: E402

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


# --- sessions ------------------------------------------------------------------------
def _label_of(man: dict, mid: str) -> tuple[str, str]:
    dec = man.get("decision") if isinstance(man.get("decision"), dict) else {}
    obj = dec.get("object") if isinstance(dec.get("object"), dict) else None
    if not obj and isinstance(man.get("object"), dict):
        obj = man["object"]
    ref = man.get("reference") if isinstance(man.get("reference"), dict) else {}
    if obj and (obj.get("ref") or obj.get("row_id") or obj.get("id") is not None):
        key = obj.get("ref") or (f"public:{obj['row_id']}" if obj.get("row_id") else f"mine:{obj.get('id')}")
        return str(key), str(obj.get("title") or key)
    if ref and ref.get("id") is not None:
        key = f"{ref.get('kind') or 'public'}:{ref.get('id')}"
        return key, str(ref.get("title") or key)
    return mid, mid


def sessions_from_manifests(root: Path) -> list[dict]:
    out = []
    for mf in sorted(root.rglob("manifest.json")):
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        mid = ((man.get("server") or {}).get("manifest_id")) or mf.parent.name
        fp = man.get("fingerprint") if isinstance(man.get("fingerprint"), dict) else None
        if not fp or not fp.get("frames"):
            imgs = sorted(p for p in (mf.parent / "images").glob("*") if p.suffix.lower() in IMAGE_EXT) \
                if (mf.parent / "images").is_dir() else []
            if not imgs:
                continue
            fp = glassid.fingerprint_from_images([(p.name, p.read_bytes()) for p in imgs if "hero" not in p.name])
        label, title = _label_of(man, mid)
        dec = man.get("decision") if isinstance(man.get("decision"), dict) else {}
        out.append({"name": mid, "label": label, "title": title, "kind": man.get("kind") or "?",
                    "decision": dec.get("decision") or "undecided", "fingerprint": fp,
                    "frames": len(fp.get("frames") or []), "has_embeddings": glassid.has_embeddings(fp)})
    return out


def sessions_from_images(root: Path, split: int = 2) -> list[dict]:
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        imgs = sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT)
        if not imgs:
            continue
        parts = [imgs[i::split] for i in range(split)] if len(imgs) >= 2 * split else [imgs]
        for i, part in enumerate(parts):
            fp = glassid.fingerprint_from_images([(p.name, p.read_bytes()) for p in part])
            out.append({"name": f"{d.name}#{i + 1}", "label": d.name, "title": d.name, "kind": "images",
                        "decision": "", "fingerprint": fp, "frames": len(fp["frames"]), "has_embeddings": False})
    return out


def sessions_from_registry() -> list[dict]:
    from central.dbconn import connect
    refs = glassid.public_refs(connect())
    return [{"name": r["id"], "label": r["ref"], "title": r["title"] or r["id"], "kind": "registry", "decision": "",
             "fingerprint": r["fingerprint"], "frames": len(glassid.frames_of(r["fingerprint"])),
             "has_embeddings": glassid.has_embeddings(r["fingerprint"])} for r in refs]


# --- scoring ---------------------------------------------------------------------------
def _best_threshold(pos: list[int], neg: list[int]) -> dict | None:
    """The confidence cut that maximises recall minus false-accept rate (Youden)."""
    if not pos or not neg:
        return None
    best = None
    for t in range(0, 101):
        tpr = sum(1 for x in pos if x >= t) / len(pos)
        fpr = sum(1 for x in neg if x >= t) / len(neg)
        j = tpr - fpr
        if best is None or j > best["youden"]:
            best = {"threshold": t, "recall": round(tpr, 3), "false_accept_rate": round(fpr, 3), "youden": round(j, 3)}
    return best


def score(sessions: list[dict], *, descriptor: str | None = None) -> dict:
    n = len(sessions)
    matrix = [[None] * n for _ in range(n)]
    verdicts = [[None] * n for _ in range(n)]
    pos, neg, pos_pairs, neg_pairs = [], [], [], []
    for i, q in enumerate(sessions):
        for j, r in enumerate(sessions):
            if i == j:
                continue
            c = glassid.compare(q["fingerprint"], r["fingerprint"], descriptor=descriptor)
            matrix[i][j] = c["confidence"]
            verdicts[i][j] = c["verdict"]
            pair = {"query": q["name"], "reference": r["name"], "confidence": c["confidence"], "verdict": c["verdict"],
                    "descriptor": c["descriptor"], "views_matched": c["views_matched"]}
            if q["label"] == r["label"]:
                pos.append(c["confidence"]); pos_pairs.append(pair)
            else:
                neg.append(c["confidence"]); neg_pairs.append(pair)
    T = glassid.thresholds()
    tp = sum(1 for p in pos_pairs if p["verdict"] == "match")
    fn = len(pos_pairs) - tp
    fp = sum(1 for p in neg_pairs if p["verdict"] == "match")
    tn = sum(1 for p in neg_pairs if p["verdict"] == "nomatch")
    inconc_neg = sum(1 for p in neg_pairs if p["verdict"] == "inconc")
    inconc_pos = sum(1 for p in pos_pairs if p["verdict"] == "inconc")

    def dist(xs):
        if not xs:
            return None
        return {"n": len(xs), "min": min(xs), "max": max(xs), "median": statistics.median(xs),
                "mean": round(statistics.mean(xs), 1)}
    return {
        "descriptor": descriptor or "auto", "sessions": n,
        "positives": len(pos_pairs), "negatives": len(neg_pairs),
        "at_shipped_thresholds": {
            "true_accepts": tp, "false_rejects": fn, "false_accepts": fp, "true_rejects": tn,
            "negatives_inconclusive": inconc_neg, "positives_inconclusive": inconc_pos,
            "precision_match_likely": round(tp / (tp + fp), 3) if (tp + fp) else None,
            "recall_match_likely": round(tp / len(pos_pairs), 3) if pos_pairs else None,
            "false_accept_rate": round(fp / len(neg_pairs), 3) if neg_pairs else None,
            "thresholds": T},
        "positive_scores": dist(pos), "negative_scores": dist(neg),
        "separation": (min(pos) - max(neg)) if pos and neg else None,
        "best_threshold": _best_threshold(pos, neg),
        "matrix": matrix, "verdicts": verdicts,
        "worst_negatives": sorted(neg_pairs, key=lambda p: -p["confidence"])[:10],
        "weakest_positives": sorted(pos_pairs, key=lambda p: p["confidence"])[:10],
    }


def print_report(sessions: list[dict], rep: dict) -> None:
    names = [s["name"] for s in sessions]
    w = max(8, min(22, max(len(x) for x in names))) if names else 8
    print(f"\n== descriptor: {rep['descriptor']} · {rep['sessions']} sessions · "
          f"{rep['positives']} same-piece pairs · {rep['negatives']} different-piece pairs ==")
    print("query \\ reference".ljust(w + 2) + " ".join(n[:w].rjust(w) for n in names))
    for i, row in enumerate(rep["matrix"]):
        cells = []
        for j, v in enumerate(row):
            if v is None:
                cells.append("·".rjust(w))
            else:
                mark = "*" if sessions[i]["label"] == sessions[j]["label"] else " "
                cells.append((f"{v}{mark}").rjust(w))
        print(names[i][:w].ljust(w + 2) + " ".join(cells))
    a = rep["at_shipped_thresholds"]
    print(f"\nat the shipped thresholds (match >= {a['thresholds']['MATCH_READY']} with >= {a['thresholds']['MIN_CELLS_MATCH']} views; "
          f"no-match < {a['thresholds']['NO_MATCH']} after {a['thresholds']['MIN_EFFORT']} shots):")
    print(f"  same piece:      {a['true_accepts']} match likely, {a['positives_inconclusive']} inconclusive, "
          f"{a['false_rejects'] - a['positives_inconclusive']} no match  -> recall {a['recall_match_likely']}")
    print(f"  different piece: {a['false_accepts']} match likely (false accepts), {a['negatives_inconclusive']} inconclusive, "
          f"{a['true_rejects']} no match  -> false-accept rate {a['false_accept_rate']}, precision {a['precision_match_likely']}")
    if rep["positive_scores"]:
        p = rep["positive_scores"]; print(f"  same-piece scores:      min {p['min']} · median {p['median']} · max {p['max']} (n={p['n']})")
    if rep["negative_scores"]:
        q = rep["negative_scores"]; print(f"  different-piece scores: min {q['min']} · median {q['median']} · max {q['max']} (n={q['n']})")
    if rep["separation"] is not None:
        print(f"  separation (lowest same-piece minus highest different-piece): {rep['separation']}")
    if rep["best_threshold"]:
        b = rep["best_threshold"]
        print(f"  best single cut on these pairs: {b['threshold']} (recall {b['recall']}, false-accept rate {b['false_accept_rate']})")
    if rep["worst_negatives"]:
        print("  highest-scoring different-piece pairs: " + "; ".join(f"{p['query']} vs {p['reference']} = {p['confidence']}" for p in rep["worst_negatives"][:5]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifests", help="folder of capture-session folders")
    src.add_argument("--images", help="folder of per-piece picture folders")
    src.add_argument("--registry", action="store_true", help="every public piece with a Glass ID")
    ap.add_argument("--descriptor", choices=("auto", "color", "dino", "dhash", "all"), default="all",
                    help="which descriptor to score (default: auto plus each one that is available)")
    ap.add_argument("--split", type=int, default=2, help="--images: sessions per piece")
    ap.add_argument("--out", default=str(ROOT / "data" / "glassid_bench.json"), help="'' to skip the JSON")
    a = ap.parse_args(argv)
    if a.manifests:
        sessions = sessions_from_manifests(Path(a.manifests))
    elif a.images:
        sessions = sessions_from_images(Path(a.images), split=max(1, a.split))
    else:
        sessions = sessions_from_registry()
    sessions = [s for s in sessions if s["frames"]]
    if len(sessions) < 2:
        print("Nothing to score: fewer than two sessions with frames.")
        return 1
    labels = {}
    for s in sessions:
        labels.setdefault(s["label"], []).append(s["name"])
    print(f"{len(sessions)} session(s) across {len(labels)} piece(s); "
          f"{sum(1 for v in labels.values() if len(v) > 1)} piece(s) have more than one session")
    for s in sessions:
        print(f"  {s['name']}: {s['title']} · {s['kind']} · {s['frames']} frames"
              + (" · AI" if s["has_embeddings"] else "") + (f" · {s['decision']}" if s["decision"] else ""))
    which = ["auto"] if a.descriptor == "auto" else [a.descriptor] if a.descriptor != "all" else ["auto", "color", "dhash"] \
        + (["dino"] if sum(1 for s in sessions if s["has_embeddings"]) >= 2 else [])
    reports = {}
    for d in which:
        rep = score(sessions, descriptor=None if d == "auto" else d)
        reports[d] = rep
        print_report(sessions, rep)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "source": a.manifests or a.images or "registry",
                                   "sessions": [{k: v for k, v in s.items() if k != "fingerprint"} for s in sessions],
                                   "reports": reports}, indent=1), encoding="utf-8")
        print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
