#!/usr/bin/env python3
"""
Build the C2PA Conformance evidence package for the Glass Database Generator
Product (asserted scope: generate + validate image/jpeg and image/png).

For each generated media type we emit a signed output `X-sample.EXT`, and for each
validated media type we ingest a raw input as a parentOf ingredient
`X-ingredientN.EXT` — so one sample proves both the generation of that format and
the validation (ingestion) of its ingredient format, matching the program's
example layout.

Ingredients MUST come from the program's ingredient library (the Google Drive link
in the assessor email). Pass them with --jpeg-ingredient / --png-ingredient. If you
omit them, synthetic placeholders are generated so you can dry-run the pipeline —
but DO NOT submit those; re-run with the official library files.

    python deploy/c2pa_evidence.py --out ./c2pa-evidence \
        --jpeg-ingredient library/some.jpg --png-ingredient library/some.png

Then self-test every file in https://c2pa-conformulator.netlify.app/ before
submitting. The self-signed cert will flag as *untrusted* — that's expected until
you pass; clear any OTHER issues first.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path


def _synth(mime, color=(150, 60, 30)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (900, 700), color).save(b, "JPEG" if mime == "image/jpeg" else "PNG")
    return b.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./c2pa-evidence")
    ap.add_argument("--jpeg-ingredient")
    ap.add_argument("--png-ingredient")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from glowtbook import c2pa_sign as C
    if not C.available():
        print("c2pa-python not installed here. pip install c2pa-python"); sys.exit(1)

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    prov = {"content_hash": "sample", "sourcing": "conformance-sample",
            "contributor": "A. Parker"}

    # (letter, output mime, output name, ingredient path/synth, ingredient name)
    plan = [
        ("a", "image/jpeg", "a-sample.jpeg",
         args.jpeg_ingredient, "image/jpeg", "a-ingredient1.jpeg"),
        ("b", "image/png", "b-sample.png",
         args.png_ingredient, "image/png", "b-ingredient1.png"),
    ]
    synthetic = False
    for letter, mime, sample_name, ing_path, ing_mime, ing_name in plan:
        if ing_path and Path(ing_path).exists():
            ing = Path(ing_path).read_bytes()
        else:
            ing = _synth(ing_mime); synthetic = True
        # write the raw input ingredient
        (out / ing_name).write_bytes(ing)
        # sign an output that INGESTS that raw input as a parentOf ingredient
        signed = C.sign_image(ing, "Glass object (conformance sample)", "A. Parker", prov,
                              mime=mime, parent_bytes=ing, parent_format=ing_mime)
        (out / sample_name).write_bytes(signed)
        creds = C.read_credentials(signed) or {}
        print(f"{sample_name:16} <- {ing_name:18} | validation: {creds.get('validation_state')} "
              f"| assertions: {', '.join(creds.get('assertions') or [])}")

    print(f"\nWrote evidence to {out.resolve()}")
    if synthetic:
        print("!! Synthetic ingredients used — replace with the program's library files before "
              "submitting, and re-run.")
    print("Next: run every file through the Conformulator; expect only the untrusted-cert flag.")


if __name__ == "__main__":
    main()
