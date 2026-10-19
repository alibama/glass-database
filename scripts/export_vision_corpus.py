#!/usr/bin/env python3
"""
Export the registry's public objects as a vision-training corpus.

One record per image: the condensed, EXIF-free picture plus every clean label the
registry already holds for the piece (techniques, SKOS style traits, materials,
dimensions, the fingerprint's mat-calibrated dominant colour and DINOv2 centroid,
and the maker-recorded colour sources when present). Optionally also a ShareGPT-
style `train.jsonl` whose assistant turn is the same JSON shape `glowtbook.vision
.suggest()` returns - ready for an Unsloth / LLaMA-Factory LoRA run (see
docs/GLASS-VISION.md, Stage 2).

Only **approved** objects are exported (the central publication gate); pending
rows come along only with --include-pending, for a local experiment. Rejected
rows never do. Captions and training targets deliberately omit maker and year:
features, not verdicts (docs/GLASS-TRAITS.md section 2).

Usage:
    python -m scripts.export_vision_corpus --out data/vision_corpus
    python -m scripts.export_vision_corpus --out data/vision_corpus --format both --max-px 1024
    python -m scripts.export_vision_corpus --out /tmp/vc --include-pending --format sharegpt
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals, glass_traits  # noqa: E402
from central.dbconn import connect  # noqa: E402
from glowtbook import media, vision  # noqa: E402

LICENSE = "CC-BY-4.0"
SOURCE = "glassdatabase.org"
OBJECT_ASSERTION = "org.glassdatabase.object"
FORMATS = ("jsonl", "sharegpt", "both")


# --- field helpers ----------------------------------------------------------


def _load_json(s) -> dict:
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def split_techniques(s: str) -> list[str]:
    """'Offhand Blown Glass, Cane & Murrine' / 'a|b' -> canonical registry labels
    (unknown spellings are kept as written so nothing is silently dropped)."""
    out: list[str] = []
    for part in re.split(r"[|,]", s or ""):
        part = part.strip()
        if not part:
            continue
        lab = vision.canonical_technique(part) or part
        if lab not in out:
            out.append(lab)
    return out


def traits_by_facet(manifest: dict) -> dict[str, list[str]]:
    """manifest.traits ([{id,label,facet}] from glass_traits.resolve_many) -> {facet: [labels]}."""
    out: dict[str, list[str]] = {}
    for t in manifest.get("traits") or []:
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


def colour_sources(manifest: dict) -> str:
    """The maker-recorded colour rods/frits ("Reichenbach R-102, Kugler K-203 ...")
    from the object assertion, if the contributor supplied them."""
    for a in manifest.get("assertions") or []:
        if isinstance(a, dict) and a.get("label") == OBJECT_ASSERTION:
            d = a.get("data") or {}
            if isinstance(d, dict):
                v = d.get("colour_sources") or d.get("color_sources") or ""
                if v:
                    return str(v).strip()
    v = manifest.get("colour_sources") or ""
    return str(v).strip() if v else ""


def fingerprint_bits(manifest: dict) -> tuple[dict | None, dict | None, list[float] | None]:
    fp = manifest.get("fingerprint") or {}
    if not isinstance(fp, dict):
        return None, None, None
    md = fp.get("metadata") or {}
    dc = md.get("dominantColor") if isinstance(md, dict) else None
    dominant = None
    if isinstance(dc, dict) and (dc.get("name") or dc.get("hex")):
        dominant = {"name": str(dc.get("name") or ""), "hex": str(dc.get("hex") or "")}
    dims = md.get("dimensions_mm") if isinstance(md, dict) else None
    dims = dims if isinstance(dims, dict) and dims else None
    cen = vision.centroid(fp)
    return dominant, dims, ([round(float(x), 5) for x in cen] if cen else None)


def _dims_text(rec: dict) -> str:
    d = rec.get("dimensions_mm")
    if d and all(d.get(k) is not None for k in ("width", "depth", "height")):
        return f"{d['width']} x {d['depth']} x {d['height']} mm"
    return (rec.get("dimensions") or "").strip()


def caption(rec: dict) -> str:
    """One plain sentence from the fields. Maker and year are left out on purpose:
    the caption describes the piece, it does not attribute it."""
    tech = [t.lower() for t in rec.get("techniques") or []]
    head = "A " + (" and ".join(tech) if 0 < len(tech) <= 2 else
                   (", ".join(tech[:-1]) + " and " + tech[-1]) if tech else "glass") + " piece"
    if rec.get("title"):
        head += f' titled "{rec["title"].strip()}"'
    parts = [head]
    if rec.get("materials"):
        parts.append(f"in {rec['materials'].strip()}")
    dims = _dims_text(rec)
    if dims:
        parts.append(dims)
    dc = rec.get("dominant_colour") or {}
    if dc.get("name"):
        parts.append(f"dominant colour {dc['name']}")
    sentence = ", ".join(parts)
    labels = [lab for labs in (rec.get("traits") or {}).values() for lab in labs]
    if labels:
        sentence += "; traits: " + ", ".join(labels)
    return sentence.rstrip(" .") + "."


def target(rec: dict) -> dict:
    """The supervision target in the exact shape `vision.suggest()` returns, filled
    from registry labels (confidence 1.0 = human-entered)."""
    techs = [t for t in rec.get("techniques") or [] if vision.canonical_technique(t)]
    text = " ".join(str(rec.get(k) or "") for k in ("title", "description", "materials")).lower()
    text += " " + " ".join(techs).lower()
    traits = rec.get("traits") or {}
    cane = ("Cane & Murrine" in techs or " cane" in f" {text}" or bool(traits.get("filigrana")))
    dc = rec.get("dominant_colour") or {}
    evidence = []
    if dc.get("name"):
        evidence.append(f"dominant colour {dc['name']} measured on the white-balanced capture mat")
    for lab in traits.get("evidence") or []:
        evidence.append(f"{lab.lower()} recorded by the documenter")
    return {
        "techniques": [{"label": t, "confidence": 1.0} for t in techs],
        "traits": {facet: [{"label": lab, "confidence": 1.0} for lab in labs]
                   for facet, labs in traits.items() if labs},
        "colour": {"dominant": dc.get("name") or "",
                   "family": vision.colour_family(dc.get("hex") or dc.get("name") or ""),
                   "notes": rec.get("colour_sources") or ""},
        "construction": {"cane": bool(cane), "murrine": "murrin" in text,
                         "incalmo": "incalmo" in text, "notes": ""},
        "evidence": evidence,
        "caveats": "Labels come from the registry record for this piece, not from a "
                   "reading of this photograph alone.",
    }


# --- the export -------------------------------------------------------------


def _has_table(conn, name: str) -> bool:
    try:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def iter_objects(conn, include_pending: bool = False):
    """Public objects (the approvals gate), plus not-yet-reviewed ones with the flag.
    Rejected rows are never yielded."""
    if not _has_table(conn, "objects"):
        return
    approvals.ensure_approvals(conn)
    cols = ("_row_id, title, maker, year, techniques, materials, dimensions, description, "
            "manifest_json")
    if include_pending:
        sql = (f"SELECT {cols} FROM objects WHERE _row_id NOT IN "
               "(SELECT row_id FROM _approvals WHERE tbl = ? AND status = 'rejected')")
    else:
        sql = f"SELECT {cols} FROM objects WHERE {approvals.approved_subquery()}"
    for row in conn.execute(sql + " ORDER BY _row_id", ("objects",)).fetchall():
        yield dict(zip(("_row_id", "title", "maker", "year", "techniques", "materials",
                        "dimensions", "description", "manifest_json"), tuple(row)))


def object_record(row: dict) -> dict:
    manifest = _load_json(row.get("manifest_json"))
    dominant, dims_mm, cen = fingerprint_bits(manifest)
    rec = {
        "object_id": row["_row_id"],
        "title": (row.get("title") or "").strip(),
        "maker": (row.get("maker") or "").strip(),
        "year": (row.get("year") or "").strip(),
        "techniques": split_techniques(row.get("techniques") or ""),
        "traits": traits_by_facet(manifest),
        "materials": (row.get("materials") or "").strip(),
        "dimensions": (row.get("dimensions") or "").strip(),
        "dimensions_mm": dims_mm,
        "dominant_colour": dominant,
        "colour_sources": colour_sources(manifest),
        "embedding_centroid": cen,
    }
    rec["caption"] = caption(rec)
    rec["license"] = LICENSE
    rec["source"] = SOURCE
    rec["object_url"] = f"/explore/?view=objects#obj-{row['_row_id']}"
    return rec


def export(conn, out: Path, *, include_pending: bool = False, fmt: str = "jsonl",
           max_px: int = 1024) -> dict:
    out = Path(out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    want_corpus = fmt in ("jsonl", "both")
    want_train = fmt in ("sharegpt", "both")
    prompt = vision.build_prompt() if want_train else ""
    summary = {"objects": 0, "objects_with_images": 0, "images": 0, "with_embeddings": 0,
               "with_colour_sources": 0, "skipped_images": 0,
               "techniques": Counter(), "traits": Counter(), "out": str(out), "format": fmt,
               "include_pending": include_pending, "max_px": max_px}
    corpus_f = (out / "corpus.jsonl").open("w", encoding="utf-8") if want_corpus else None
    train_f = (out / "train.jsonl").open("w", encoding="utf-8") if want_train else None
    try:
        for row in iter_objects(conn, include_pending):
            summary["objects"] += 1
            base = object_record(row)
            imgs = conn.execute(
                "SELECT id, role, caption, image_b64 FROM object_images "
                "WHERE object_row_id = ? ORDER BY id", (row["_row_id"],)).fetchall()
            n_written = 0
            for n, im in enumerate(imgs):
                _id, role, cap, b64 = tuple(im)
                try:
                    raw = base64.b64decode(b64 or "")
                    data, mime = media.condense_image(raw, max_px=max_px)
                except Exception:  # noqa: BLE001 - unreadable blob: skip, keep going
                    summary["skipped_images"] += 1
                    continue
                ext = "png" if mime == "image/png" else "jpg"
                rel = f"images/{row['_row_id']}_{n}.{ext}"
                (out / rel).write_bytes(data)
                rec = {"image": rel, **base, "role": role or "", "caption_user": cap or ""}
                # keep the spec'd key order readable: image, object_id, role, caption_user, ...
                ordered = {k: rec[k] for k in ("image", "object_id", "role", "caption_user")}
                ordered.update({k: v for k, v in rec.items() if k not in ordered})
                if corpus_f:
                    corpus_f.write(json.dumps(ordered, ensure_ascii=False) + "\n")
                if train_f:
                    train_f.write(json.dumps({
                        "messages": [
                            {"role": "user", "content": "<image>\n" + prompt},
                            {"role": "assistant",
                             "content": json.dumps(target(base), ensure_ascii=False)}],
                        "images": [rel]}, ensure_ascii=False) + "\n")
                n_written += 1
            if n_written:
                summary["objects_with_images"] += 1
                summary["images"] += n_written
                if base["embedding_centroid"]:
                    summary["with_embeddings"] += 1
                if base["colour_sources"]:
                    summary["with_colour_sources"] += 1
                summary["techniques"].update(base["techniques"])
                summary["traits"].update(lab for labs in base["traits"].values() for lab in labs)
    finally:
        if corpus_f:
            corpus_f.close()
        if train_f:
            train_f.close()
    (out / "README.md").write_text(readme(summary), encoding="utf-8")
    return summary


def readme(s: dict) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    top_t = ", ".join(f"{k} ({v})" for k, v in s["techniques"].most_common(8)) or "-"
    top_tr = ", ".join(f"{k} ({v})" for k, v in s["traits"].most_common(8)) or "-"
    flag = " --include-pending" if s["include_pending"] else ""
    return f"""# Glass Database vision corpus

Built {when} by `python -m scripts.export_vision_corpus --out {s['out']} --format {s['format']}{flag} --max-px {s['max_px']}`
from the public registry at https://{SOURCE}.

**Counts:** {s['objects']} object(s) walked, {s['objects_with_images']} with images,
{s['images']} image(s) exported, {s['with_embeddings']} object(s) with a DINOv2 centroid,
{s['with_colour_sources']} with maker-recorded colour sources, {s['skipped_images']} unreadable image(s) skipped.
Top techniques: {top_t}. Top traits: {top_tr}.

## Files
- `images/<object_id>_<n>.<jpg|png>` - the registry's condensed rendition, re-encoded to fit
  {s['max_px']} px on the long edge, EXIF stripped (orientation baked in). PNG stays PNG.
- `corpus.jsonl` - one JSON record per image (fields below).
- `train.jsonl` - ShareGPT/LLaMA-Factory style turns (`messages` + `images`): the user turn is
  `<image>` plus the registry's closed-vocabulary prompt (`glowtbook.vision.build_prompt()`),
  the assistant turn is the target JSON in the same shape `vision.suggest()` returns.
  Present when the format is `sharegpt` or `both`.

## Record fields (`corpus.jsonl`)
| field | meaning |
|---|---|
| `image` | path relative to this folder |
| `object_id` | registry row id; `object_url` is the public page fragment |
| `role`, `caption_user` | the image's role (primary / detail / backlit ...) and the contributor's caption |
| `title`, `maker`, `year` | registry fields (public). **Not** used in `caption` or the training target |
| `techniques` | registry technique labels (`central/techniques.py`) |
| `traits` | `{{facet_id: [labels]}}` from the SKOS trait thesaurus (`central/glass_traits.py`) |
| `materials`, `dimensions` | free-text registry fields |
| `dimensions_mm` | `{{width, depth, height}}` measured on the capture mat, when fingerprinted |
| `dominant_colour` | `{{name, hex}}` from the mat-white-balanced fingerprint, or null |
| `colour_sources` | colour rods/frits the maker recorded ("Reichenbach R-102 ...") or "" |
| `embedding_centroid` | L2-normalised mean of the fingerprint's DINOv2 views (384-d), or null |
| `caption` | one generated sentence from the fields above (no maker, no year) |
| `license`, `source` | `{LICENSE}`, `{SOURCE}` |

## Licence and intended use
Data from the Glass Database is published under **Creative Commons Attribution 4.0**
(CC-BY-4.0). Attribute "Glass Database contributors, {SOURCE}" and link the object URL where
practical. Image rights stay with the contributor/maker; the registry carries the images with
their consent for documentation and this corpus inherits that purpose.

Use it to train or evaluate models that recognise **observable features** - process, colour,
construction (cane, murrine, incalmo ...), surface evidence - and to feed similarity search.
Please do **not** use it to build maker-attribution or valuation models: the project's house
rule is *features, not verdicts* (docs/GLASS-TRAITS.md section 2, docs/GLASS-VISION.md section 5).
Labels are contributor-entered and admin-approved, not expert-adjudicated; treat them as
weak supervision and report per-label precision/recall against a held-out, expert-checked set.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="export_vision_corpus",
        description="Export approved registry objects + images as a vision corpus.")
    ap.add_argument("--out", required=True, help="output folder (created)")
    ap.add_argument("--include-pending", action="store_true",
                    help="also export not-yet-approved objects (local experiments only)")
    ap.add_argument("--format", choices=FORMATS, default="jsonl",
                    help="corpus.jsonl, ShareGPT train.jsonl, or both")
    ap.add_argument("--max-px", type=int, default=1024, help="long-edge pixel cap for images")
    args = ap.parse_args(argv)
    conn = connect()
    try:
        s = export(conn, Path(args.out), include_pending=args.include_pending,
                   fmt=args.format, max_px=args.max_px)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"Vision corpus written to {s['out']} ({s['format']}"
          f"{', including pending' if s['include_pending'] else ''}).")
    print(f"  objects: {s['objects']}  with images: {s['objects_with_images']}  "
          f"images: {s['images']}  skipped: {s['skipped_images']}")
    print(f"  with DINOv2 centroid: {s['with_embeddings']}  "
          f"with colour sources: {s['with_colour_sources']}")
    if s["techniques"]:
        print("  techniques: " + ", ".join(f"{k} x{v}" for k, v in s["techniques"].most_common(8)))
    if s["traits"]:
        print("  traits:     " + ", ".join(f"{k} x{v}" for k, v in s["traits"].most_common(8)))
    if not s["images"]:
        print("  (nothing exported - approve some objects with images first, or pass "
              "--include-pending for a local experiment)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
