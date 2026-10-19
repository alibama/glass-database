"""Stage 1 of the vision plan: the k-NN index over the approved registry's
fingerprint centroids, its suggestions, the leave-one-out evaluation and the
Style tab's no-Ollama path. Synthetic objects only (tiny vectors, three
clusters); no model, no network."""
import json
import random

import pytest

from central import approvals, vision_index
from central.dbconn import connect

DIM = 16
A = [1.0] + [0.0] * (DIM - 1)                   # cluster A: reticello goblets
B = [0.0, 1.0] + [0.0] * (DIM - 2)              # cluster B: kiln-cast
C = [0.0, 0.0, 1.0] + [0.0] * (DIM - 3)         # cluster C: stained glass
_rng = random.Random(42)


def _vec(base, noise=0.06):
    return [round(b + _rng.uniform(-noise, noise), 5) for b in base]


def _fp(base, views=2, name="amber"):
    return {"rating": 80, "tier": "Strong",
            "metadata": {"dominantColor": {"name": name, "hex": "#c60"}, "hasEmbeddings": True},
            "frames": [{"chist": [0.1] * 8, "emb": _vec(base)} for _ in range(views)]
            + [{"chist": [0.1] * 8}]}


def _insert(c, rid, title, base, techniques, traits=None, status="approved", emb=True,
            manifest_techniques=None):
    from glowtbook.contribute import ensure_central_objects
    ensure_central_objects(c)
    man = {"title": title, "maker": "Test Maker"}
    if manifest_techniques:
        man["techniques"] = manifest_techniques
    if traits:
        man["traits"] = [{"id": t.lower().replace(" ", "-"), "label": t, "facet": "?"} for t in traits]
    if emb:
        man["fingerprint"] = _fp(base)
    c.execute("INSERT OR REPLACE INTO objects (_row_id,title,maker,year,techniques,manifest_json,"
              "content_hash,published_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
              (rid, title, "Test Maker", "2026", techniques, json.dumps(man), "hash-" + rid))
    c.commit()
    approvals.ensure_approvals(c)
    if status:
        approvals.set_status(c, "objects", [rid], status)


@pytest.fixture(scope="module")
def clusters(demo_db):
    """Four approved pieces per cluster, plus the rows the index must leave out."""
    c = connect()
    for i in range(4):
        _insert(c, f"vidxA{i}", f"Reticello Goblet {i}", A, "Offhand Blown Glass, Cane & Murrine",
                traits=["Vetro a reticello", "Goblet"])
        _insert(c, f"vidxB{i}", f"Cast Block {i}", B, "Kiln / Open-Mold Casting | Cold working")
        _insert(c, f"vidxC{i}", f"Window Panel {i}", C, "Stained Glass")
    _insert(c, "vidxPend", "Pending Twin", A, "Stained Glass", status=None)       # not reviewed
    _insert(c, "vidxRej", "Rejected Twin", A, "Stained Glass", status="rejected")
    _insert(c, "vidxNoEmb", "No Embeddings", A, "Stained Glass", emb=False)
    c.close()
    vision_index.invalidate()
    yield
    vision_index.invalidate()


# --- records + the gate -------------------------------------------------------
def test_load_records_respects_the_gate_and_canonicalises(clusters):
    c = connect()
    recs = {r["object_id"]: r for r in vision_index.load_records(c)}
    assert {f"vidx{k}{i}" for k in "ABC" for i in range(4)} <= set(recs)
    assert "vidxPend" not in recs and "vidxRej" not in recs and "vidxNoEmb" not in recs
    a0 = recs["vidxA0"]
    assert a0["title"] == "Reticello Goblet 0" and a0["techniques"] == ["Offhand Blown Glass", "Cane & Murrine"]
    assert a0["traits"] == {"filigrana": ["Vetro a reticello"], "form": ["Goblet"]}
    assert a0["dominant_colour"] == {"name": "amber", "hex": "#c60"}
    assert len(a0["embedding_centroid"]) == DIM
    assert sum(x * x for x in a0["embedding_centroid"]) == pytest.approx(1.0, abs=1e-4)
    # '|' separators and the old short spellings resolve to the registry labels
    assert recs["vidxB0"]["techniques"] == ["Kiln / Open-Mold Casting", "Cold Working (Grinding / Polishing)"]
    # pending rows come along only on request; rejected never do
    with_pending = {r["object_id"] for r in vision_index.load_records(c, include_pending=True)}
    assert "vidxPend" in with_pending and "vidxRej" not in with_pending
    c.close()


def test_record_from_row_merges_manifest_techniques_and_fingerprint_json(demo_db):
    row = {"_row_id": "x", "title": " T ", "techniques": "Offhand blown glass",
           "manifest_json": json.dumps({"techniques": ["Cane & murrine"], "traits": ["Goblet"]}),
           "fingerprint_json": json.dumps(_fp(B))}
    rec = vision_index.record_from_row(row)
    assert rec["object_id"] == "x" and rec["title"] == "T"
    assert rec["techniques"] == ["Offhand Blown Glass", "Cane & Murrine"]
    assert rec["traits"] == {"form": ["Goblet"]}
    assert vision_index.record_from_row({"_row_id": "y", "manifest_json": "{}"}) is None
    assert vision_index.record_from_row({"_row_id": "z", "manifest_json": "not json"}) is None


def test_records_from_corpus_dedupes_by_object():
    lines = [json.dumps({"object_id": "o1", "image": "a.jpg", "techniques": ["Stained Glass"],
                         "traits": {"form": ["Goblet"]}, "embedding_centroid": [1, 0]}),
             json.dumps({"object_id": "o1", "image": "b.jpg", "techniques": ["Stained Glass"],
                         "embedding_centroid": [1, 0]}),
             json.dumps({"object_id": "o2", "embedding_centroid": None}),
             "", "not json", json.dumps([1, 2])]
    recs = vision_index.records_from_corpus(lines)
    assert [r["object_id"] for r in recs] == ["o1"]
    assert recs[0]["traits"] == {"form": ["Goblet"]} and recs[0]["embedding_centroid"] == [1.0, 0.0]


# --- the index ------------------------------------------------------------------
def test_suggest_for_fingerprint_votes_from_look_alikes(clusters):
    r = vision_index.suggest_for_fingerprint(_fp(A), k=3)
    assert r["ok"] is True and r["source"] == "knn" and r["k"] == 3
    assert r["techniques"][0] == {"label": "Cane & Murrine", "score": 1.0, "support": 3}
    assert {t["label"] for t in r["techniques"]} == {"Cane & Murrine", "Offhand Blown Glass"}
    assert r["traits"]["filigrana"][0]["label"] == "Vetro a reticello"
    assert r["traits"]["form"] == [{"label": "Goblet", "score": 1.0, "support": 3}]
    assert len(r["neighbours"]) == 3
    for n in r["neighbours"]:
        assert n["object_row_id"].startswith("vidxA")
        assert n["image"] == f"/api/objects/{n['object_row_id']}/image"
        assert n["href"] == f"/explore/?view=objects#obj-{n['object_row_id']}"
        assert 0.9 < n["score"] <= 1.0 and n["title"].startswith("Reticello")
        assert n["dominant_colour"]["name"] == "amber" and "Cane & Murrine" in n["techniques"]
    assert r["neighbours"] == sorted(r["neighbours"], key=lambda n: -n["score"])
    assert r["corpus_size"] >= 12 and r["evidence"] and "verdict" in r["caveats"]
    # the pending and rejected twins (nearest of all, had they counted) never vote
    assert not any(n["object_row_id"] in ("vidxPend", "vidxRej") for n in r["neighbours"])
    # a JSON string works; the other clusters vote their own labels
    rb = vision_index.suggest_for_fingerprint(json.dumps(_fp(B)), k=3)
    assert [t["label"] for t in rb["techniques"]] == ["Cold Working (Grinding / Polishing)",
                                                      "Kiln / Open-Mold Casting"]   # ties: by name
    assert all(t["score"] == 1.0 and t["support"] == 3 for t in rb["techniques"])
    assert rb["traits"] == {}
    rc = vision_index.suggest_for_fingerprint(_vec(C), k=2)          # a raw centroid too
    assert rc["techniques"] == [{"label": "Stained Glass", "score": 1.0, "support": 2}]


def test_exclude_and_failure_modes(clusters):
    # the piece itself, once published, is left out of its own neighbourhood
    r = vision_index.suggest_for_fingerprint(_fp(A), k=4, exclude=["vidxA0", "vidxA1"])
    ids = {n["object_row_id"] for n in r["neighbours"]}
    assert "vidxA0" not in ids and "vidxA1" not in ids and {"vidxA2", "vidxA3"} <= ids
    # no embeddings -> ok False with a human sentence, never an exception
    r = vision_index.suggest_for_fingerprint({"frames": [{"chist": [1]}]})
    assert r["ok"] is False and "no embeddings" in r["error"] and r["neighbours"] == []
    assert vision_index.suggest_for_fingerprint("not json")["ok"] is False
    # a vector of another dimension finds no bucket to compare against
    r = vision_index.suggest_for_fingerprint([1.0, 0.0, 0.0, 0.0, 0.0], k=3)
    assert r["ok"] is False and "5-d" in r["error"]
    # an empty index says so
    assert vision_index.VisionIndex([]).suggest(_fp(A))["error"].startswith("No public piece")


def test_cache_ttl_force_and_invalidate(clusters, monkeypatch):
    first = vision_index.get()
    assert vision_index.get() is first                       # within the TTL
    assert vision_index.get(force=True) is not first         # forced rebuild
    now = vision_index.get()
    assert vision_index.get(max_age=0) is not now            # expired
    vision_index.invalidate()
    assert vision_index.get() is not now
    stats = vision_index.get().stats()
    assert stats["objects"] >= 12 and stats["dims"].get(DIM, 0) >= 12
    assert stats["techniques"]["Cane & Murrine"] >= 4 and stats["traits"]["Goblet"] >= 4
    # the cache is per database: the key follows GLASSDB_PATH
    key0 = vision_index._db_key()
    monkeypatch.setenv("GLASSDB_PATH", "somewhere-else.db")
    assert vision_index._db_key() != key0 and vision_index._db_key().startswith("sqlite:")
    # a broken database degrades to ok: False, not a traceback
    monkeypatch.setattr(vision_index, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    vision_index.invalidate()
    r = vision_index.suggest_for_fingerprint(_fp(A))
    assert r["ok"] is False and "boom" in r["error"]


def test_pure_python_path_matches_numpy(clusters, monkeypatch):
    recs = vision_index.load_records(connect())
    query = _fp(A)                                           # one query for both paths
    with_np = vision_index.VisionIndex(recs).suggest(query, k=4)
    monkeypatch.setattr(vision_index, "_np", None)
    pure = vision_index.VisionIndex(recs).suggest(query, k=4)
    assert [n["object_row_id"] for n in pure["neighbours"]] == [n["object_row_id"] for n in with_np["neighbours"]]
    for p, q in zip(pure["neighbours"], with_np["neighbours"]):
        assert p["score"] == pytest.approx(q["score"], abs=1e-4)
    assert pure["techniques"] == with_np["techniques"]


# --- the evaluation script ----------------------------------------------------------
def test_eval_probe_on_the_live_registry(clusters, tmp_path, capsys):
    import scripts.eval_vision_probe as m
    out = tmp_path / "eval.json"
    assert m.main(["--k", "3", "--out", str(out), "--min-support", "3"]) == 0
    text = capsys.readouterr().out
    assert "live registry (approved)" in text and "== Techniques" in text
    assert "Cane & Murrine" in text and "Filigrana decoration" in text and str(out) in text
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["k"] == 3 and rep["threshold"] == 0.5 and rep["objects"] >= 12
    assert rep["evaluated"] >= 12 and rep["seconds"] >= 0
    techs = {d["label"]: d for d in rep["groups"]["techniques"]["labels"]}
    # three clean clusters: every held-out piece is labelled by its own kind
    for lab in ("Cane & Murrine", "Offhand Blown Glass", "Kiln / Open-Mold Casting", "Stained Glass"):
        assert techs[lab]["support"] >= 4 and techs[lab]["precision"] == 1.0 and techs[lab]["recall"] == 1.0
        assert techs[lab]["tp"] >= 4 and techs[lab]["fp"] == 0 and techs[lab]["fn"] == 0
    assert 0 < techs["Stained Glass"]["prevalence"] < 1
    micro = rep["groups"]["techniques"]["micro"]
    assert micro["precision"] == 1.0 and micro["recall"] == 1.0 and micro["f1"] == 1.0
    macro = rep["groups"]["techniques"]["macro"]
    assert macro["labels"] >= 4 and macro["f1"] == 1.0
    fil = rep["groups"]["traits.filigrana"]
    assert fil["title"] == "Filigrana decoration" and fil["labels"][0]["label"] == "Vetro a reticello"
    assert fil["labels"][0]["recall"] == 1.0 and fil["objects_with_labels"] >= 4
    # other tests' pieces share the DB (untagged 8-d ones, a lone 3-d one): only pieces that
    # carry techniques count, and the lone one has no neighbour to be scored against
    assert rep["top1"]["shares_a_technique"] >= 0.9 and rep["top1"]["mean_similarity"] > 0.9
    assert rep["top1"]["pieces_with_techniques"] >= 12
    assert "central.vision_index" in rep["method"]


def test_eval_probe_on_a_corpus_file_dedupes_and_handles_empty(tmp_path, capsys):
    import scripts.eval_vision_probe as m
    corpus = tmp_path / "corpus.jsonl"
    lines = []
    for i in range(3):
        rec = {"image": f"images/a{i}_0.jpg", "object_id": f"a{i}", "title": f"A{i}",
               "techniques": ["Cane & Murrine"], "traits": {"form": ["Goblet"]},
               "embedding_centroid": _vec(A)}
        lines += [json.dumps(rec), json.dumps({**rec, "image": f"images/a{i}_1.jpg"})]
        lines.append(json.dumps({"image": f"images/b{i}_0.jpg", "object_id": f"b{i}", "title": f"B{i}",
                                 "techniques": ["Stained Glass"], "traits": {},
                                 "embedding_centroid": _vec(B)}))
    lines.append(json.dumps({"object_id": "none", "techniques": ["Stained Glass"],
                             "embedding_centroid": None}))
    corpus.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "r.json"
    assert m.main(["--corpus", str(corpus), "--k", "2", "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["objects"] == 6 and rep["evaluated"] == 6 and rep["source"].startswith("corpus")
    labs = {d["label"]: d for d in rep["groups"]["techniques"]["labels"]}
    assert labs["Cane & Murrine"] == {**labs["Cane & Murrine"], "support": 3, "tp": 3, "fp": 0, "fn": 0}
    assert rep["groups"]["techniques"]["macro"]["labels"] == 2        # both clear min-support 3
    assert rep["groups"]["traits.form"]["labels"][0]["support"] == 3
    assert "== Techniques" in capsys.readouterr().out
    # nothing to score is a sentence, not a crash, and still writes the JSON
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    out2 = tmp_path / "empty.json"
    assert m.main(["--corpus", str(empty), "--out", str(out2)]) == 0
    assert "Nothing to score" in capsys.readouterr().out
    assert json.loads(out2.read_text(encoding="utf-8"))["objects"] == 0


# --- the Style tab (no Ollama) -----------------------------------------------------
def test_style_tab_suggests_from_similar_pieces(clusters, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from glowtbook import store as gstore
    from tests.conftest import ROOT

    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    import streamlit as st
    st.cache_resource.clear()                      # the app caches its store connection
    s = gstore.open_store()
    s.execute("INSERT INTO object (id, user_id, title, techniques) VALUES (1, ?, 'My Goblet', '')",
              (gstore.DEMO_UID,))
    s.commit()
    gstore.set_fingerprint(s, gstore.DEMO_UID, 1, _fp(A))
    s.close()

    at = AppTest.from_file(str(ROOT / "glowtbook" / "app.py"), default_timeout=120)
    at.query_params["page"] = "objects"
    at.run()
    assert not at.exception, at.exception
    assert at.button(key="knn_go_1").label.endswith("Suggest from similar pieces")
    assert "knn_sugg_1" not in at.session_state
    at.button(key="knn_go_1").click().run()
    assert not at.exception, at.exception
    md = "\n".join(m.value for m in at.markdown)
    # four reticello goblets in the registry, k=5: the fifth neighbour is another kind
    assert "Cane & Murrine (4/5" in md and "Filigrana decoration**: Vetro a reticello (4/5" in md
    html = "\n".join(h.value for h in at.html)
    assert "/api/objects/vidxA" in html and "similarity" in html and "Reticello Goblet" in html
    caps = "\n".join(c.value for c in at.caption)
    assert "5 nearest public piece(s)" in caps and "OLLAMA_URL" in caps
    assert at.multiselect(key="tr_filigrana_1").value == []
    at.button(key="knn_apply_1").click().run()
    assert not at.exception, at.exception
    assert at.multiselect(key="tr_filigrana_1").value == ["Vetro a reticello"]
    assert at.multiselect(key="tr_form_1").value == ["Goblet"]
    st.cache_resource.clear()
