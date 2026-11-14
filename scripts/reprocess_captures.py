#!/usr/bin/env python3
"""
Re-run the server's Photo Mat measurement over capture sessions with the code in
this checkout, and show it beside what the server recorded when each one arrived.

The loop for improving the measurement:

    python -m scripts.pull_capture --all --since 2026-11-01      # sessions -> data/pulled_captures/
    # edit glowtbook/matdetect.py
    python -m scripts.reprocess_captures data/pulled_captures    # before / after, per session
    python -m scripts.reprocess_captures data/pulled_captures --frames   # per-picture detail too

Each folder is one session as stored (``manifest.json`` + ``images/``). The table shows
the dimensions recorded at upload, the dimensions this checkout's code gets now, and the
size the person typed for the piece when the manifest carries one (``--truth W,D,H`` sets
it for a single session). ``--json out.json`` writes everything, per frame included.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _sessions(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        if (p / "manifest.json").is_file():
            out.append(p)
        elif p.is_dir():
            out += sorted(q.parent for q in p.glob("*/manifest.json"))
    return out


def _dims(d) -> str:
    if not isinstance(d, dict):
        return "—"
    w, de, h = d.get("width_mm"), d.get("depth_mm"), d.get("height_mm")
    if w is None:
        return d.get("reason") or "—"
    return f"{w}×{de}×{h if h is not None else '?'}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, default=[ROOT / "data" / "pulled_captures"])
    ap.add_argument("--frames", action="store_true", help="print what the mat gave in every picture")
    ap.add_argument("--truth", help="W,D,H in mm for a single session, to print the error")
    ap.add_argument("--json", type=Path, help="write the full results here")
    a = ap.parse_args(argv)

    from central import captures
    from glowtbook import matdetect
    if not matdetect.available():
        print("OpenCV with ArUco is not installed (pip install opencv-contrib-python-headless)", file=sys.stderr)
        return 2
    sessions = _sessions(a.paths)
    if not sessions:
        print("no sessions found (folders with manifest.json)", file=sys.stderr)
        return 1
    truth = [float(x) for x in a.truth.split(",")] if a.truth else None
    results = []
    print(f"{'session':28s} {'kind':7s} {'pics':>4s} {'mat':>4s}  {'recorded':>16s}  {'now':>16s}  piece")
    for s in sessions:
        man = json.loads((s / "manifest.json").read_text(encoding="utf-8"))
        files = {p.relative_to(s).as_posix(): p.read_bytes() for p in sorted(s.rglob("*"))
                 if p.is_file() and p.suffix.lower() in captures.IMAGE_EXT}
        before = ((man.get("server") or {}).get("mat") or {})
        now = captures._mat_report(files)
        rec = {"session": s.name, "kind": man.get("kind"), "pictures": len(files),
               "object": ((man.get("decision") or {}).get("object") or man.get("object") or {}).get("title"),
               "before": before.get("dimensions"), "now": now.get("dimensions"), "mat_now": now}
        results.append(rec)
        print(f"{s.name:28s} {str(rec['kind'] or ''):7s} {len(files):>4d} {now.get('with_mat', 0):>4d}  "
              f"{_dims(before.get('dimensions')):>16s}  {_dims(now.get('dimensions')):>16s}  {rec['object'] or ''}")
        if truth and isinstance(now.get("dimensions"), dict) and now["dimensions"].get("ok"):
            d = now["dimensions"]
            got = [d.get("width_mm"), d.get("depth_mm"), d.get("height_mm")]
            err = ["?" if g is None else f"{g - t:+.0f}" for g, t in zip(got, truth)]
            print(f"{'':28s} error vs {a.truth}: W {err[0]}  D {err[1]}  H {err[2]} mm")
        if a.frames:
            for im in now.get("images") or []:
                print(f"    {im.get('file', ''):34s} markers {im.get('markers', 0)} ids {im.get('ids')} "
                      f"mm/px {im.get('mm_per_px')} elev {im.get('elevation_deg')} "
                      f"cam {im.get('camera_height_mm')} outline {im.get('object_mm')}"
                      + (f"  error: {im['error']}" if im.get("error") else ""))
    if a.json:
        a.json.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
