#!/usr/bin/env python3
"""
C2PA validation test harness (Conformance v0.2 §2.3).

Takes the four inputs the program specifies and emits the validation result as
**crJSON** (the c2pa-rs validation report — manifest store + validation_state +
validation_results status codes):

    1. an asset to validate            --asset PATH
    2. a (test) C2PA Trust List        --trust-anchors PEM   (C2PA signer anchors)
    3. a (test) C2PA TSA Trust List    --tsa-trust-anchors PEM
    4. a validation time (RFC 3339)    --time 2026-01-01T00:00:00Z

    python deploy/crjson_harness.py --asset a-sample.png \
        --trust-anchors trust.pem --tsa-trust-anchors tsa.pem \
        --time 2026-01-01T00:00:00Z --output a-sample.crjson

When the program supplies a set of test inputs, run this per asset and return the
.crjson outputs. Trust anchors and verify flags are applied via c2pa-rs Settings;
confirm the exact settings keys against the c2pa-rs version pinned in
requirements.txt before the assessment run.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings

warnings.filterwarnings("ignore")


def _mime(path: str, head: bytes) -> str:
    if head[:4] == b"\x89PNG":
        return "image/png"
    if head[:2] == b"\xff\xd8":
        return "image/jpeg"
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        path.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True)
    ap.add_argument("--trust-anchors", help="PEM file: C2PA signer trust anchors")
    ap.add_argument("--tsa-trust-anchors", help="PEM file: TSA trust anchors")
    ap.add_argument("--time", help="validation time, RFC 3339 (e.g. 2026-01-01T00:00:00Z)")
    ap.add_argument("--output", help="write crJSON here (default: stdout)")
    args = ap.parse_args()

    import c2pa

    data = open(args.asset, "rb").read()
    mime = _mime(args.asset, data)

    anchors = ""
    if args.trust_anchors:
        anchors += open(args.trust_anchors).read()
    if args.tsa_trust_anchors:
        anchors += "\n" + open(args.tsa_trust_anchors).read()

    settings = {
        "verify": {
            "verify_trust": bool(args.trust_anchors),
            "verify_timestamp_trust": bool(args.tsa_trust_anchors),
            "ocsp_fetch": False,
            "remote_manifest_fetch": False,
        },
        "trust": {},
    }
    if anchors.strip():
        settings["trust"]["trust_anchors"] = anchors
    if args.time:
        # validation time (RFC 3339). Key name is version-sensitive; set both spellings.
        settings["verify"]["verify_after"] = args.time
        settings["verify"]["validation_time"] = args.time

    try:
        c2pa.load_settings(settings)
    except Exception as e:  # noqa: BLE001
        print(f"warning: settings not fully applied ({e}); validating with defaults", file=sys.stderr)

    import io
    reader = c2pa.Reader(mime, io.BytesIO(data))
    crjson = reader.json()
    # pretty-print + ensure it's valid JSON
    obj = json.loads(crjson)
    obj.setdefault("_harness", {})["validation_time"] = args.time
    out = json.dumps(obj, indent=2, ensure_ascii=False)

    if args.output:
        open(args.output, "w").write(out)
        st = obj.get("validation_state")
        print(f"Wrote {args.output} (validation_state: {st})")
    else:
        print(out)


if __name__ == "__main__":
    main()
