#!/usr/bin/env python3
"""
Pull every public Glass ID from a running Glass Database into a folder of fingerprint
JSON files, for the matching bench:

    python -m scripts.pull_registry                       # https://glassdatabase.org -> data/live_fps/
    python -m scripts.pull_registry --base http://127.0.0.1:8080 --out data/local_fps
    python -m scripts.glassid_bench --fingerprints data/live_fps --rules both

Only public data is read (``/api/datasets/objects`` and ``/api/objects/{id}/fingerprint``);
no sign-in. Each file is named by the piece's record id and carries its title and maker
under ``object`` so the bench can label it. Pieces without a Glass ID are skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "glassdb-bench/1 (+https://github.com/alibama/glass-database)"}


def _get(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="https://glassdatabase.org", help="site root (the API is under /api)")
    ap.add_argument("--out", default=str(ROOT / "data" / "live_fps"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, offset = [], 0
    while True:
        page = _get(f"{a.base}/api/datasets/objects?limit=200&offset={offset}")
        rows += page.get("rows") or []
        offset += page.get("returned") or 0
        if not page.get("returned") or offset >= (page.get("total") or 0):
            break
    n = 0
    for r in rows:
        rid = r.get("_row_id")
        try:
            fp = _get(f"{a.base}/api/objects/{rid}/fingerprint")
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
            continue
        if not isinstance(fp, dict) or not fp.get("frames"):
            continue
        fp["object"] = {**(fp.get("object") or {}), "id": rid, "title": r.get("title") or "", "maker": r.get("maker") or ""}
        (out / f"{rid}.json").write_text(json.dumps(fp), encoding="utf-8")
        n += 1
        print(f"  {rid}  {(r.get('title') or '')[:40]}  · {len(fp['frames'])} views")
    print(f"{n} Glass ID(s) of {len(rows)} public piece(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
