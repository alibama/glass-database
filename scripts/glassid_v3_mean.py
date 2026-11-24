#!/usr/bin/env python3
"""
Rebuild the fixed AI-vector mean that Glass ID rules v3 centre on
(central/glassid_v3.json, mirrored into deploy/fingerprint-web/glassid-kit.js).

    python -m scripts.pull_registry                       # public Glass IDs -> data/live_fps/
    python -m scripts.glassid_v3_mean --fingerprints data/live_fps

Why: every DINOv2 vector shares a large common direction ("glass on a table under a
phone camera"), so two different pieces average a cosine of about 0.5 before centring.
The mean is versioned and only changes when this is run on purpose - scores must not
drift as pieces are added - and the rebuild is a reviewed commit like any threshold
change. The mean is taken per piece first, so a piece with many AI samples does not
pull it towards itself.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from central import glassid  # noqa: E402

OUT = ROOT / "central" / "glassid_v3.json"
KIT = ROOT / "deploy" / "fingerprint-web" / "glassid-kit.js"
MARK = re.compile(r"(/\*V3_DINO_MEAN\*/).*?(/\*END\*/)", re.S)


def build(paths: list[str]) -> dict:
    per_piece: dict[str, list[list[float]]] = {}
    for p in paths:
        d = json.loads(Path(p).read_text())
        fp = d.get("fingerprint", d)
        key = str(d.get("piece_id") or d.get("id") or Path(p).stem)
        for fr in glassid.frames_of(fp):
            v = glassid._nums(fr.get("emb"))
            if v and len(v) == 384:
                per_piece.setdefault(key, []).append(v)
    means = [[sum(c) / len(vs) for c in zip(*vs)] for vs in per_piece.values() if vs]
    if not means:
        raise SystemExit("no AI vectors found")
    mean = [round(sum(c) / len(means), 4) for c in zip(*means)]
    return {"version": date.today().isoformat(), "pieces": len(means),
            "vectors": sum(len(v) for v in per_piece.values()), "dim": len(mean), "dino_mean": mean}


def write(doc: dict) -> None:
    OUT.write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    js = KIT.read_text()
    block = json.dumps({"version": doc["version"], "mean": doc["dino_mean"]}, separators=(",", ":"))
    new, n = MARK.subn(lambda m: m.group(1) + block + m.group(2), js)
    if n != 1:
        raise SystemExit("glassid-kit.js has no /*V3_DINO_MEAN*/.../*END*/ block")
    KIT.write_text(new)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fingerprints", default=str(ROOT / "data" / "live_fps"))
    a = ap.parse_args(argv)
    doc = build(sorted(glob.glob(str(Path(a.fingerprints) / "*.json"))))
    write(doc)
    print(f"v3 mean {doc['version']}: {doc['pieces']} pieces, {doc['vectors']} vectors -> {OUT.relative_to(ROOT)} + kit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
