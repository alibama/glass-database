#!/usr/bin/env python3
"""
Measure the Stage 1 probe: leave-one-out nearest-neighbour voting over the
registry's DINOv2 fingerprint centroids (docs/GLASS-VISION.md section 2).

Every object with embeddings is held out in turn, its k nearest *other* objects
vote (``glowtbook.vision.knn_suggest`` through ``central.vision_index``), and the
labels whose weighted share reaches ``--threshold`` count as predicted. The report
is per-label **precision / recall / F1 / support** for techniques and for every
trait facet, plus micro and macro averages per group, so the number next to a
suggestion is measured, not promised. Also reported: how often the nearest
neighbour shares at least one technique (the "looks like" sanity check), and the
prevalence of each label so a reader can judge against chance.

Honesty notes. Registry labels are contributor-entered and admin-approved - weak
supervision, not expert adjudication - so this measures agreement with the
registry, not truth. Labels with tiny support give noisy numbers; ``--min-support``
keeps them out of the macro averages (they still print). One record per object:
a ``corpus.jsonl`` (one line per *image*) is de-duplicated first, otherwise a
piece would find its own twin at similarity 1.0.

Usage:
    python -m scripts.eval_vision_probe                       # live registry (approved objects)
    python -m scripts.eval_vision_probe --include-pending     # plus not-yet-reviewed rows
    python -m scripts.eval_vision_probe --corpus data/vision_corpus/corpus.jsonl
    python -m scripts.eval_vision_probe --k 7 --threshold 0.4 --out data/vision_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import glass_traits, vision_index  # noqa: E402

DEFAULT_OUT = Path("data") / "vision_eval.json"


# --- scoring ----------------------------------------------------------------


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def _true_labels(rec: dict) -> dict[str, set[str]]:
    """{group: labels} for one record: 'techniques' and 'traits.<facet>'."""
    out = {"techniques": set(rec.get("techniques") or [])}
    for fid, _l, _d in glass_traits.FACETS:
        labs = (rec.get("traits") or {}).get(fid) or []
        if labs:
            out[f"traits.{fid}"] = set(labs)
    return out


def evaluate(records: list[dict], *, k: int = vision_index.DEFAULT_K, threshold: float = 0.5,
             min_support: int = 3) -> dict:
    """Leave-one-out over ``records`` (index-record shape). Returns the full report."""
    from glowtbook import vision

    idx = vision_index.VisionIndex(records, source="eval")
    groups = ["techniques"] + [f"traits.{fid}" for fid, _l, _d in glass_traits.FACETS]
    tp: dict[str, Counter] = {g: Counter() for g in groups}
    fp: dict[str, Counter] = {g: Counter() for g in groups}
    fn: dict[str, Counter] = {g: Counter() for g in groups}
    support: dict[str, Counter] = {g: Counter() for g in groups}
    predicted: dict[str, Counter] = {g: Counter() for g in groups}
    top1_sims: list[float] = []
    top1_shares_technique = 0
    top1_with_techniques = 0        # held-out pieces that have any technique to share
    evaluated = 0
    no_neighbour = 0
    t0 = time.monotonic()
    for rec in idx.records:
        truth = _true_labels(rec)
        for g, labs in truth.items():
            support[g].update(labs)
        top = idx.neighbours(rec["embedding_centroid"], k=k, exclude=[rec["object_id"]])
        if not top:
            no_neighbour += 1
            continue
        evaluated += 1
        top1_sims.append(top[0][0])
        if truth["techniques"]:
            top1_with_techniques += 1
            if truth["techniques"] & set(top[0][1].get("techniques") or []):
                top1_shares_technique += 1
        corpus = [r for _, r in top]
        for g in groups:
            voted = vision.knn_suggest(rec["embedding_centroid"], corpus, k=len(corpus), key=g)
            pred = {v["label"] for v in voted if v["score"] >= threshold}
            true = truth.get(g, set())
            predicted[g].update(pred)
            for lab in pred & true:
                tp[g][lab] += 1
            for lab in pred - true:
                fp[g][lab] += 1
            for lab in true - pred:
                fn[g][lab] += 1

    n = len(idx.records)
    report_groups: dict[str, dict] = {}
    for g in groups:
        labels = sorted(set(support[g]) | set(predicted[g]))
        if not labels:
            continue
        per_label = []
        for lab in labels:
            p, r, f = _prf(tp[g][lab], fp[g][lab], fn[g][lab])
            per_label.append({
                "label": lab, "support": support[g][lab], "predicted": predicted[g][lab],
                "prevalence": round(support[g][lab] / n, 4) if n else 0.0,
                "tp": tp[g][lab], "fp": fp[g][lab], "fn": fn[g][lab],
                "precision": p, "recall": r, "f1": f,
            })
        per_label.sort(key=lambda d: (-d["support"], d["label"]))
        mp, mr, mf = _prf(sum(tp[g].values()), sum(fp[g].values()), sum(fn[g].values()))
        eligible = [d for d in per_label if d["support"] >= min_support]
        macro = None
        if eligible:
            macro = {
                "precision": round(sum(d["precision"] for d in eligible) / len(eligible), 4),
                "recall": round(sum(d["recall"] for d in eligible) / len(eligible), 4),
                "f1": round(sum(d["f1"] for d in eligible) / len(eligible), 4),
                "labels": len(eligible),
            }
        facet = g.split(".", 1)[1] if g.startswith("traits.") else None
        report_groups[g] = {
            "title": glass_traits.facet_label(facet) if facet else "Techniques",
            "labels": per_label,
            "micro": {"precision": mp, "recall": mr, "f1": mf,
                      "tp": sum(tp[g].values()), "fp": sum(fp[g].values()),
                      "fn": sum(fn[g].values())},
            "macro": macro,
            "objects_with_labels": sum(1 for r in idx.records if _true_labels(r).get(g)),
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "leave-one-out k-NN vote over DINOv2 fingerprint centroids "
                  "(central.vision_index + glowtbook.vision.knn_suggest)",
        "k": k, "threshold": threshold, "min_support": min_support,
        "objects": n, "dropped": idx.dropped, "evaluated": evaluated,
        "no_neighbour": no_neighbour, "dims": idx.dims, "numpy": idx.stats()["numpy"],
        "top1": {
            "mean_similarity": round(sum(top1_sims) / len(top1_sims), 4) if top1_sims else None,
            "shares_a_technique": (round(top1_shares_technique / top1_with_techniques, 4)
                                   if top1_with_techniques else None),
            "pieces_with_techniques": top1_with_techniques,
        },
        "groups": report_groups,
        "seconds": round(time.monotonic() - t0, 2),
    }


# --- presentation -----------------------------------------------------------


def _pct(x) -> str:
    return "  -  " if x is None else f"{100 * x:5.1f}"


def format_table(rep: dict) -> str:
    lines = []
    lines.append(f"Stage 1 probe - leave-one-out k-NN (k={rep['k']}, threshold={rep['threshold']}) "
                 f"over {rep['objects']} object(s) with embeddings"
                 + (f", {rep['dropped']} dropped" if rep.get("dropped") else "")
                 + f"; {rep['evaluated']} evaluated in {rep['seconds']}s")
    t1 = rep.get("top1") or {}
    if t1.get("mean_similarity") is not None:
        share = (f"; shares a technique with the held-out piece "
                 f"{100 * t1['shares_a_technique']:.1f}% of the time "
                 f"({t1.get('pieces_with_techniques', 0)} piece(s) with techniques)"
                 if t1.get("shares_a_technique") is not None else "")
        lines.append(f"nearest neighbour: mean cosine {t1['mean_similarity']:.3f}{share}")
    if not rep["groups"]:
        lines.append("")
        lines.append("Nothing to score: no approved object carries a fingerprint with embeddings. "
                     "Capture pieces with the AI descriptor on, approve them, and run again "
                     "(or point --corpus at an exported corpus.jsonl).")
        return "\n".join(lines)
    width = max(24, min(48, max(len(d["label"]) for g in rep["groups"].values()
                                for d in g["labels"])))
    for g, body in rep["groups"].items():
        lines.append("")
        lines.append(f"== {body['title']} ({g}) - {body['objects_with_labels']} labelled object(s)")
        lines.append(f"  {'label':<{width}} {'supp':>5} {'pred':>5} {'prev%':>6} {'prec%':>6} "
                     f"{'rec%':>6} {'f1%':>6}")
        for d in body["labels"]:
            lab = d["label"] if len(d["label"]) <= width else d["label"][:width - 1] + "…"
            lines.append(f"  {lab:<{width}} {d['support']:>5} {d['predicted']:>5} "
                         f"{_pct(d['prevalence']):>6} {_pct(d['precision']):>6} "
                         f"{_pct(d['recall']):>6} {_pct(d['f1']):>6}")
        m = body["micro"]
        lines.append(f"  {'micro (all labels)':<{width}} {'':>5} {'':>5} {'':>6} "
                     f"{_pct(m['precision']):>6} {_pct(m['recall']):>6} {_pct(m['f1']):>6}")
        if body["macro"]:
            mc = body["macro"]
            lines.append(f"  {'macro (support >= ' + str(rep['min_support']) + ')':<{width}} "
                         f"{mc['labels']:>5} {'':>5} {'':>6} {_pct(mc['precision']):>6} "
                         f"{_pct(mc['recall']):>6} {_pct(mc['f1']):>6}")
    lines.append("")
    lines.append("supp = objects carrying the label; pred = times it was suggested; prev% = share of "
                 "all objects (chance level). Registry labels are contributor-entered, so this is "
                 "agreement with the registry, not truth - features, not verdicts.")
    return "\n".join(lines)


# --- entry point ------------------------------------------------------------


def load_records(args) -> tuple[list[dict], str]:
    if args.corpus:
        path = Path(args.corpus)
        with path.open(encoding="utf-8") as fh:
            return vision_index.records_from_corpus(fh), f"corpus {path}"
    from central.dbconn import connect
    conn = connect()
    try:
        recs = vision_index.load_records(conn, include_pending=args.include_pending)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return recs, "live registry" + (" (approved + pending)" if args.include_pending else " (approved)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="eval_vision_probe",
        description="Leave-one-out evaluation of the k-NN fingerprint probe (Stage 1).")
    ap.add_argument("--corpus", help="a corpus.jsonl from scripts/export_vision_corpus "
                                     "(default: read the live registry)")
    ap.add_argument("--include-pending", action="store_true",
                    help="live registry: also score not-yet-approved objects")
    ap.add_argument("--k", type=int, default=vision_index.DEFAULT_K, help="neighbours voting")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="weighted share a label needs to count as predicted (0-1)")
    ap.add_argument("--min-support", type=int, default=3,
                    help="labels with fewer objects stay out of the macro averages")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="JSON report path ('' to skip)")
    args = ap.parse_args(argv)
    records, source = load_records(args)
    rep = evaluate(records, k=args.k, threshold=args.threshold, min_support=args.min_support)
    rep["source"] = source
    print(f"source: {source}")
    print(format_table(rep))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nJSON report written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
