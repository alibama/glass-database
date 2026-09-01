"""Object re-identification fingerprint integration."""
import io
import json
import zipfile

import pytest

fp_mod = pytest.importorskip("glowtbook.fingerprint")
pytestmark = pytest.mark.skipif(not fp_mod.available(), reason="object-fingerprint not installed")


def _blocks(seed, size=(400, 300), block=40):
    import random

    from PIL import Image
    rng = random.Random(seed)
    im = Image.new("RGB", size); px = im.load(); grid = {}
    for by in range(0, size[1], block):
        for bx in range(0, size[0], block):
            grid[(bx, by)] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = grid[(x - x % block, y - y % block)]
    b = io.BytesIO(); im.save(b, "JPEG", quality=90); return b.getvalue()


def _enroll_zip(seeds):
    import object_fingerprint as ofp
    frames = []
    imgs = []
    for i, s in enumerate(seeds):
        b = _blocks(s); imgs.append((f"frames/{i:03d}.jpg", b))
        frames.append({"file": f"frames/{i:03d}.jpg", "cell": f"{i}:1",
                       "dhash": ofp.dhash(b), "detail": 0.08, "sector": i})
    doc = {"tool": "object-fingerprint", "version": 1, "created": "2026-01-01T00:00:00Z",
           "rating": 84, "tier": "Strong",
           "subscores": {"coverage": 0.9, "detail": 0.82, "robustness": 0.75},
           "params": {}, "frames": frames}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fingerprint.json", json.dumps(doc))
        for name, b in imgs:
            z.writestr(name, b)
    return buf.getvalue(), imgs


def _cand_zip(seeds):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, s in enumerate(seeds):
            z.writestr(f"{i:03d}.jpg", _blocks(s))
    return buf.getvalue()


def test_load_enrollment():
    zb, _ = _enroll_zip([1, 2, 3, 4, 5, 6, 7, 8])
    r = fp_mod.load_enrollment(zb)
    assert r["ok"] and r["rating"] == 84 and r["tier"] == "Strong"
    assert r["n_frames"] == 8 and r["sheet"] and r["fingerprint"]["frames"]


def test_verify_same_and_different():
    zb, _ = _enroll_zip([1, 2, 3, 4, 5, 6, 7, 8])
    ref = fp_mod.load_enrollment(zb)["fingerprint"]
    same = fp_mod.verify_zip(ref, _cand_zip([1, 2, 3, 4, 5, 6, 7, 8]))
    diff = fp_mod.verify_zip(ref, _cand_zip([101, 102, 103, 104, 105, 106, 107, 108]))
    assert same["verdict"] == "match-likely" and same["confidence"] >= 70
    assert diff["verdict"] == "no-match" and diff["confidence"] < 40


def test_assertion_and_summary():
    zb, _ = _enroll_zip([1, 2, 3, 4])
    ref = fp_mod.load_enrollment(zb)["fingerprint"]
    assert fp_mod.summary(ref)["tier"] == "Strong"
    a = fp_mod.assertion(ref)
    assert a and "label" in a and "data" in a


def test_bad_zip_is_graceful():
    r = fp_mod.load_enrollment(b"not a zip")
    assert r["ok"] is False and "error" in r
