"""Local vision adapter (Ollama VLM + fingerprint-embedding probe) and the corpus export.
No network: the model call is monkeypatched; the export runs against the demo DB."""
import base64
import io
import json

import pytest
from PIL import Image

from glowtbook import vision


# --- prompt -----------------------------------------------------------------
def test_build_prompt_uses_vocabularies_and_house_rule():
    p = vision.build_prompt(["Cane & Murrine"])
    low = p.lower()
    assert "Offhand Blown Glass" in p            # a technique label
    assert "Vetro a reticello" in p              # a trait label
    assert 'facet "filigrana"' in p              # grouped by facet id
    assert "features" in low and "not" in low and "verdict" in low
    for key in ("techniques", "traits", "colour", "construction", "evidence", "caveats"):
        assert f'"{key}"' in p
    assert "JSON" in p and "Cane & Murrine" in p  # json-only + the contributor hint
    assert "CONTRIBUTOR HINT" not in vision.build_prompt()


# --- parsing ----------------------------------------------------------------
def test_parse_fenced_json_validates_labels_and_clamps():
    text = ('Sure! Here is the analysis:\n```json\n' + json.dumps({
        "techniques": [{"label": "offhand blown glass", "confidence": 1.7},
                       {"label": "Unicorn Casting", "confidence": 0.4}],
        "traits": {"filigrana": [{"label": "vetro a reticello", "confidence": "0.8"}],
                   "stem": [{"label": "Dragon stem", "confidence": 0.2}],
                   "Foot & pontil": ["Folded foot"]},
        "colour": {"dominant": "amber", "family": "warm", "notes": ""},
        "construction": {"cane": "yes", "murrine": False, "incalmo": 0, "notes": "twisted canes"},
        "evidence": ["white threads crossing in a net"], "caveats": "scale unknown"})
        + '\n```\nLet me know if you need more.')
    r = vision.parse_response(text)
    assert r["techniques"] == [{"label": "Offhand Blown Glass", "confidence": 1.0}]
    assert r["traits"]["filigrana"] == [{"label": "Vetro a reticello", "confidence": 0.8}]
    assert r["traits"]["foot"][0]["label"] == "Folded foot"      # facet label -> facet id
    assert "Unicorn Casting" in r["unmapped"] and "Dragon stem" in r["unmapped"]
    assert "stem" not in r["traits"]
    assert r["construction"] == {"cane": True, "murrine": False, "incalmo": False,
                                 "notes": "twisted canes"}
    assert r["colour"]["dominant"] == "amber" and r["evidence"] and r["caveats"]


def test_parse_flat_traits_and_alt_labels_land_in_their_facet():
    r = vision.parse_response('{"traits": ["reticello", "melon knop"], "colour": "cobalt"}')
    assert r["traits"]["filigrana"][0]["label"] == "Vetro a reticello"
    assert r["traits"]["stem"][0]["label"] == "Mould-blown ribbed knop stem"
    assert r["colour"]["dominant"] == "cobalt"


def test_parse_garbage_is_empty_structure_and_never_raises():
    empty = vision.parse_response("no json here at all")
    assert empty["techniques"] == [] and empty["traits"] == {} and empty["unmapped"] == []
    assert empty["colour"] == {"dominant": "", "family": "", "notes": ""}
    assert empty["construction"]["cane"] is False and empty["evidence"] == []
    assert vision.parse_response(None) == empty
    assert vision.parse_response("{not: valid json") == empty
    assert vision.parse_response('[1, 2, 3]') == empty


# --- suggest() --------------------------------------------------------------
def test_suggest_without_url_is_off(monkeypatch, sample_image_bytes):
    monkeypatch.setattr(vision, "OLLAMA_URL", "")
    assert vision.available() is False and vision.reachable() is False
    assert vision.models() == []
    r = vision.suggest(sample_image_bytes)
    assert r["ok"] is False and "OLLAMA_URL" in r["error"] and r["model"] == vision.MODEL


def test_suggest_with_canned_server(monkeypatch, sample_image_bytes):
    monkeypatch.setattr(vision, "OLLAMA_URL", "http://127.0.0.1:11434/")
    calls = {}

    def fake_post(url, payload, timeout):
        calls["url"], calls["payload"], calls["timeout"] = url, payload, timeout
        content = json.dumps({
            "techniques": [{"label": "Cane & Murrine", "confidence": 0.9}],
            "traits": {"filigrana": [{"label": "Zanfirico", "confidence": 0.7}]},
            "colour": {"dominant": "cobalt", "family": "cool", "notes": "transparent"},
            "construction": {"cane": True, "murrine": False, "incalmo": False,
                             "notes": "twisted white threads"},
            "evidence": ["spiral white threads along the body"], "caveats": "single view"})
        return {"model": "qwen2.5vl:7b", "message": {"role": "assistant", "content": content}}

    monkeypatch.setattr(vision, "_post", fake_post)
    r = vision.suggest(sample_image_bytes, techniques_hint=["Cane & Murrine"], timeout=12.5)
    assert r["ok"] is True and r["model"] == "qwen2.5vl:7b"
    assert isinstance(r["latency_s"], float) and r["latency_s"] >= 0
    assert r["traits"]["filigrana"] == [{"label": "Zanfirico", "confidence": 0.7}]
    assert r["techniques"][0]["label"] == "Cane & Murrine"
    assert r["construction"]["cane"] is True and r["unmapped"] == []
    # the request: /api/chat, json format, non-streaming, low temperature, condensed image
    assert calls["url"] == "http://127.0.0.1:11434/api/chat" and calls["timeout"] == 12.5
    p = calls["payload"]
    assert p["model"] == "qwen2.5vl:7b" and p["format"] == "json" and p["stream"] is False
    assert p["options"]["temperature"] == 0.1
    msg = p["messages"][0]
    assert msg["role"] == "user" and "Cane & Murrine" in msg["content"]
    img = base64.b64decode(msg["images"][0])
    assert img[:8] == b"\x89PNG\r\n\x1a\n"            # PNG stays PNG through condense_image
    assert max(Image.open(io.BytesIO(img)).size) <= 1024  # downscaled from 1600 px


def test_suggest_server_error_is_friendly(monkeypatch, sample_image_bytes):
    monkeypatch.setattr(vision, "OLLAMA_URL", "http://127.0.0.1:11434")

    def boom(url, payload, timeout):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(vision, "_post", boom)
    r = vision.suggest(sample_image_bytes)
    assert r["ok"] is False and "socket closed" in r["error"]


# --- fingerprint fallback + Stage 1 probe -------------------------------------
def test_suggest_from_fingerprint_reads_colour():
    from glowtbook import fingerprint as fp_mod
    from tests.test_fingerprint import _export_zip

    ref = fp_mod.load_enrollment(_export_zip(embeddings=True))["fingerprint"]
    r = vision.suggest_from_fingerprint(ref)
    assert r["ok"] is True and r["model"] is None
    assert r["colour"]["dominant"] == "amber" and r["colour"]["family"] == "orange"
    assert r["has_embeddings"] is True and r["views"] == 6 and r["hex"] == "#c60"
    assert r["techniques"] == [] and r["traits"] == {}
    assert "OLLAMA_URL" in r["caveats"] and any("amber" in e for e in r["evidence"])
    # tolerant of junk
    assert vision.suggest_from_fingerprint({})["colour"]["dominant"] == ""
    assert vision.suggest_from_fingerprint("not json")["views"] == 0


def test_colour_family():
    assert vision.colour_family("#0033aa") == "blue"
    assert vision.colour_family("#eeeeee") == "neutral"
    assert vision.colour_family("cobalt") == "cool"
    assert vision.colour_family("") == ""


def test_centroid_cosine_and_knn_pick_the_right_label():
    fp = {"frames": [{"emb": [1, 0, 0]}, {"emb": [1, 0, 0]}, {"chist": [0.1]}]}
    assert vision.centroid(fp) == pytest.approx([1.0, 0.0, 0.0])
    assert vision.centroid({"frames": [{"chist": []}]}) is None
    assert vision.centroid({"frames": [{"emb": [0, 0, 0]}]}) is None
    assert vision.cosine([1, 0], [0, 1]) == 0.0
    assert vision.cosine([1, 1], [1, 1]) == pytest.approx(1.0)
    assert vision.cosine([], [1]) == 0.0 and vision.cosine([0, 0], [1, 1]) == 0.0

    corpus = [
        {"object_id": "a", "embedding_centroid": [0.98, 0.10, 0.0],
         "techniques": ["Cane & Murrine"], "traits": {"filigrana": ["Zanfirico"]}},
        {"object_id": "b", "embedding_centroid": [0.95, 0.20, 0.0],
         "techniques": ["Cane & Murrine", "Offhand Blown Glass"], "traits": {}},
        {"object_id": "c", "embedding_centroid": [0.0, 0.0, 1.0],
         "techniques": ["Kiln / Open-Mold Casting"], "traits": {}},
        {"object_id": "d", "embedding_centroid": None, "techniques": ["Stained Glass"]},
        "not a record",
    ]
    top = vision.knn_suggest(fp, corpus, k=2)
    assert top[0] == {"label": "Cane & Murrine", "score": 1.0, "support": 2}
    assert top[1]["label"] == "Offhand Blown Glass" and top[1]["support"] == 1
    assert 0 < top[1]["score"] < 1
    assert all(t["label"] not in ("Kiln / Open-Mold Casting", "Stained Glass") for t in top)
    # a raw centroid works too, and dict-valued keys (traits by facet) flatten
    tr = vision.knn_suggest([1, 0, 0], corpus, k=3, key="traits")
    assert tr[0]["label"] == "Zanfirico"
    assert vision.knn_suggest([0, 0, 1], corpus, k=1)[0]["label"] == "Kiln / Open-Mold Casting"
    assert vision.knn_suggest({"frames": []}, corpus) == []
    assert vision.knn_suggest(fp, []) == []


# --- the corpus export ------------------------------------------------------
def _png_bytes(w=640, h=480, colour=(200, 110, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "PNG")
    return buf.getvalue()


def _insert_object(c, rid: str, title: str, approved: bool) -> None:
    from central import approvals
    manifest = {
        "manifest_version": "glowtbook-0.1",
        "assertions": [{"label": "org.glassdatabase.object",
                        "data": {"title": title,
                                 "colour_sources": "Reichenbach R-102 amber; Kugler K-203 opal"}}],
        "traits": [{"id": "goblet", "label": "Goblet", "facet": "form"},
                   {"id": "vetro-a-reticello", "label": "Vetro a reticello", "facet": "filigrana"}],
        "fingerprint": {
            "rating": 84, "tier": "Strong",
            "metadata": {"dominantColor": {"name": "amber", "hex": "#c60"}, "hasEmbeddings": True,
                         "dimensions_mm": {"width": 41, "depth": 26, "height": 92}},
            "frames": [{"chist": [0.1] * 64, "emb": [0.5, 0.5, 0.0]},
                       {"chist": [0.1] * 64, "emb": [0.5, 0.5, 0.0]},
                       {"chist": [0.1] * 64}]},
    }
    c.execute("""INSERT OR REPLACE INTO objects
        (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,techniques,materials,
         dimensions,description,contributor,sourcing,manifest_json,content_hash,published_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, "glowtbook", "contributions", "2026-01-01", title, "Demo Maker", "2021",
         "Offhand Blown Glass, Cane & Murrine", "soda-lime glass", "9 x 9 x 4 cm",
         "A small reticello goblet", "tester", "self-reported", json.dumps(manifest), rid,
         "2026-01-01"))
    c.execute("DELETE FROM object_images WHERE object_row_id=?", (rid,))
    c.execute("INSERT INTO object_images (object_row_id,role,caption,image_b64) VALUES (?,?,?,?)",
              (rid, "primary", "front view", base64.b64encode(_png_bytes()).decode()))
    c.commit()
    approvals.ensure_approvals(c)
    if approved:
        approvals.set_status(c, "objects", [rid], "approved")


def _jsonl(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_export_vision_corpus_in_process(demo_db, tmp_path, capsys):
    import scripts.export_vision_corpus as m
    from central.dbconn import connect
    from glowtbook.contribute import ensure_central_objects

    c = connect()
    ensure_central_objects(c)
    _insert_object(c, "visiontest0001", "Reticello Goblet", approved=True)
    _insert_object(c, "visiontest0002", "Pending Piece", approved=False)
    c.close()

    assert m.main(["--out", str(tmp_path), "--format", "both"]) == 0
    recs = _jsonl(tmp_path / "corpus.jsonl")
    mine = [r for r in recs if r["object_id"] == "visiontest0001"]
    assert len(mine) == 1
    assert not any(r["object_id"] == "visiontest0002" for r in recs)  # the gate holds
    r = mine[0]
    assert r["caption"].endswith(".") and "Reticello Goblet" in r["caption"]
    assert "amber" in r["caption"] and "Demo Maker" not in r["caption"]
    assert r["role"] == "primary" and r["caption_user"] == "front view"
    assert r["maker"] == "Demo Maker" and r["year"] == "2021"
    assert r["techniques"] == ["Offhand Blown Glass", "Cane & Murrine"]
    assert r["traits"] == {"form": ["Goblet"], "filigrana": ["Vetro a reticello"]}
    assert r["dominant_colour"] == {"name": "amber", "hex": "#c60"}
    assert r["dimensions_mm"] == {"width": 41, "depth": 26, "height": 92}
    assert r["colour_sources"].startswith("Reichenbach")
    assert r["embedding_centroid"] == pytest.approx([0.7071, 0.7071, 0.0], abs=1e-3)
    assert r["license"] == "CC-BY-4.0" and r["source"] == "glassdatabase.org"
    assert r["object_url"] == "/explore/?view=objects#obj-visiontest0001"
    img = tmp_path / r["image"]
    assert r["image"].startswith("images/") and img.exists() and img.suffix == ".png"
    with Image.open(img) as im:
        assert max(im.size) <= 1024 and not dict(im.getexif())

    train = [t for t in _jsonl(tmp_path / "train.jsonl") if t["images"] == [r["image"]]]
    assert len(train) == 1
    user, assistant = train[0]["messages"]
    assert user["role"] == "user" and user["content"].startswith("<image>\n")
    assert "Vetro a reticello" in user["content"]            # the closed-vocab prompt
    target = json.loads(assistant["content"])
    assert {"techniques", "traits", "colour", "construction", "evidence", "caveats"} <= set(target)
    assert target["techniques"][0] == {"label": "Offhand Blown Glass", "confidence": 1.0}
    assert target["traits"]["filigrana"][0]["label"] == "Vetro a reticello"
    assert target["construction"]["cane"] is True and target["colour"]["dominant"] == "amber"
    assert target["colour"]["notes"].startswith("Reichenbach")
    assert "Demo Maker" not in assistant["content"]          # features, not verdicts
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "CC-BY-4.0" in readme and "embedding_centroid" in readme
    out = capsys.readouterr().out
    assert "images:" in out and "Cane & Murrine" in out

    # pending rows come along only with the flag (and only jsonl is written)
    out2 = tmp_path / "pending"
    assert m.main(["--out", str(out2), "--format", "jsonl", "--include-pending"]) == 0
    ids = {r["object_id"] for r in _jsonl(out2 / "corpus.jsonl")}
    assert {"visiontest0001", "visiontest0002"} <= ids
    assert not (out2 / "train.jsonl").exists()
