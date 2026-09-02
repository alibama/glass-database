"""Object-fingerprint integration (format-agnostic import + compact assertion)."""
import io
import json
import zipfile

from glowtbook import fingerprint as fp_mod


def _export_zip(rating=84, tier="Strong", n=6, embeddings=False):
    frames = []
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        for i in range(n):
            name = f"frames/{i:03d}.jpg"
            z.writestr(name, b"\xff\xd8\xff\xe0thumb")  # tiny thumbnail
            fr = {"file": name, "sector": i, "cell": f"{i}:1",
                  "dhash": [i + 1, i + 2], "chist": [0.1] * 64}
            if embeddings:
                fr["emb"] = [0.01 * i] * 384
            frames.append(fr)
        doc = {"tool": "glass-fingerprint", "version": 1, "created": "2026-01-01T00:00:00Z",
               "rating": rating, "tier": tier,
               "metadata": {"dominantColor": {"name": "amber", "hex": "#c60"},
                            "hasEmbeddings": embeddings},
               "frames": frames}
        z.writestr("fingerprint.json", json.dumps(doc))
    return zbuf.getvalue()


def test_import_raw_fingerprint():
    r = fp_mod.load_enrollment(_export_zip())
    assert r["ok"] and r["rating"] == 84 and r["tier"] == "Strong" and r["n_frames"] == 6
    assert r["fingerprint"]["frames"] and "chist" in r["fingerprint"]["frames"][0]
    assert r["sheet"]  # a preview thumbnail was pulled out


def test_import_plain_json():
    zb = _export_zip()
    with zipfile.ZipFile(io.BytesIO(zb)) as z:
        raw = z.read("fingerprint.json")
    r = fp_mod.load_enrollment(raw)
    assert r["ok"] and r["n_frames"] == 6


def test_summary_and_embeddings_flag():
    ref = fp_mod.load_enrollment(_export_zip(embeddings=True))["fingerprint"]
    s = fp_mod.summary(ref)
    assert s["tier"] == "Strong" and s["has_embeddings"] is True and s["dominant_color"] == "amber"


def test_compact_assertion_binds_a_hash():
    ref = fp_mod.load_enrollment(_export_zip())["fingerprint"]
    a = fp_mod.assertion(ref)
    assert a["label"] and a["data"]["fingerprint_sha256"]
    assert len(a["data"]["fingerprint_sha256"]) == 64 and a["data"]["views"] == 6
    # the assertion is compact — it does NOT embed the per-frame vectors
    assert "frames" not in a["data"]


def test_bad_zip_is_graceful():
    r = fp_mod.load_enrollment(b"not a zip and not json")
    assert r["ok"] is False and "error" in r
