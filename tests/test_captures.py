"""Capture-session manifests: the store (central.captures), the routes (api/captures.py),
the admin page's registration, the pull-down command and the bench."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central import captures, sessions
from central.dbconn import connect
from tests.test_glassid import fp

ROOT = Path(__file__).resolve().parent.parent
BOSS = "boss@example.com"


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_CAPTURES_DIR", str(tmp_path / "caps"))
    monkeypatch.setenv("GLASSDB_ADMIN_EMAILS", BOSS)
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "test-admin-key")
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _tidy(demo_db):
    yield
    c = connect()
    try:
        c.execute("DELETE FROM capture_manifests")
        c.commit()
    except Exception:  # noqa: BLE001
        pass


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def _boss():
    return _auth("boss", BOSS, "The Boss")


def _key():
    return {"X-API-Key": "test-admin-key"}


def _jpg(colour=(230, 120, 20), size=(384, 288)):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", size, colour).save(b, "JPEG"); return b.getvalue()


def _manifest(kind="enroll", dec="new_piece", oid=7, **extra):
    man = {"tool": "glass-id-capture-manifest", "version": 1, "kind": kind, "created": "2026-11-05T10:05:00Z",
           "app": {"page": f"{kind}.html", "version": "2026.11.05"},
           "device": {"ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/130.0.0.0 Mobile Safari/537.36", "resolution": {"w": 1920, "h": 1080}, "compass": True},
           "session": {"shots": 12, "ai": False, "rating": 84},
           "fingerprint": fp("orange", dims={"width": 70, "depth": 68, "height": 75}),
           "object": {"id": oid, "title": "Orange cube"},
           "candidates": {"descriptor": "color", "margin": 61, "ambiguous": False,
                          "items": [{"ref": "public:gidA", "kind": "public", "id": "gidA", "title": "Tangerine Cube",
                                     "confidence": 79, "verdict": "match", "label": "Match likely", "views_matched": 5,
                                     "reasons": ["Colour histogram: 79/100"]},
                                    {"ref": "public:gidB", "kind": "public", "id": "gidB", "title": "Cobalt Cube",
                                     "confidence": 18, "verdict": "nomatch", "label": "No match here", "views_matched": 0}]},
           "decision": {"decision": dec, "note": "first of the four cubes", "object": {"id": oid, "title": "Orange cube"},
                        "at": "2026-11-05T10:06:00Z"},
           "images": [{"file": "images/001.jpg", "seq": 1}, {"file": "images/hero.jpg", "role": "hero"}]}
    man.update(extra)
    return man


def _zip(man, files=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", json.dumps(man))
        for name, data in (files if files is not None else {"images/001.jpg": _jpg(), "images/002.jpg": _jpg((200, 100, 10)),
                                                              "images/hero.jpg": _jpg(size=(1600, 1200))}).items():
            z.writestr(name, data)
    return b.getvalue()


# --- the store -------------------------------------------------------------------------
def test_store_reads_sanitises_and_summarises(tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_CAPTURES_DIR", str(tmp_path / "caps"))
    c = connect()
    assert captures.safe_name("images/000.jpg") == "images/000.jpg" and captures.safe_name("hero.jpg") == "images/hero.jpg"
    assert captures.safe_name("../x.jpg") is None and captures.safe_name("images/../x.jpg") is None
    assert captures.safe_name("notes.txt") is None and captures.safe_name("a/b/c.jpg") is None
    assert captures.safe_name("manifest.json") == "manifest.json"
    man, files = captures.read_zip(_zip(_manifest(), {"images/001.jpg": _jpg(), "../evil.jpg": _jpg(), "notes.txt": b"x",
                                                     "images/fake.jpg": b"not an image"}))
    assert set(files) == {"images/001.jpg", "images/evil.jpg"}          # climbed-out name kept as a plain file; junk dropped
    with pytest.raises(ValueError):
        captures.read_zip(b"PKjunk")
    with pytest.raises(ValueError):
        captures.read_zip(b"")
    with pytest.raises(ValueError):
        captures.read_zip(_zip({"a": 1}, {})[:0] + zipfile_without_manifest())
    man2, files2 = captures.read_zip(json.dumps(_manifest()).encode())          # the manifest alone
    assert man2["kind"] == "enroll" and files2 == {}
    row = captures.save(c, man, files, {"uid": "u9", "email": "u9@example.com", "name": "U Nine"})
    assert row["manifest_id"].startswith("cap-") and row["kind"] == "enroll" and row["decision"] == "new_piece"
    assert row["device"] == "Pixel 8 · Android 14 · Chrome · 1920×1080"
    assert row["object_id"] == 7 and row["object_title"] == "Orange cube" and row["n_frames"] == 12
    assert row["top_candidate"] == "Tangerine Cube" and row["top_score"] == 79 and row["n_candidates"] == 2
    assert row["rating"] == 84 and row["n_images"] == 2 and row["url"] == f"/captures.html?m={row['manifest_id']}"
    root = tmp_path / "caps" / row["manifest_id"]
    assert (root / "manifest.json").is_file() and (root / "images" / "001.jpg").is_file()
    stored = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert stored["server"]["manifest_id"] == row["manifest_id"] and stored["server"]["by"]["email"] == "u9@example.com"
    assert stored["server"]["mat"]["checked"] == 2 and stored["server"]["mat"]["with_mat"] == 0
    assert "no ArUco markers" in stored["server"]["mat"]["note"] or "OpenCV" in stored["server"]["mat"]["note"]
    assert [f["file"] for f in captures.files_of(row["manifest_id"])] == ["images/001.jpg", "images/evil.jpg", "manifest.json"]
    assert captures.file_path(row["manifest_id"], "images/001.jpg").is_file()
    assert captures.file_path(row["manifest_id"], "../../etc/passwd") is None
    assert captures.file_path("cap-not-an-id", "images/001.jpg") is None
    # decisions keep their history in the manifest file and the row
    out = captures.set_decision(c, row["manifest_id"], "match_rejected", note="a different cube", by="u9@example.com",
                                matched={"ref": "public:gidA", "title": "Tangerine Cube", "row_id": "gidA"})
    assert out["decision"] == "match_rejected" and out["decision_label"] == "Match rejected" and out["object_row_id"] == "gidA"
    stored = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert stored["decision"]["decision"] == "match_rejected" and stored["decisions"][0]["decision"] == "new_piece"
    with pytest.raises(ValueError):
        captures.set_decision(c, row["manifest_id"], "maybe")
    assert captures.set_decision(c, "cap-00000000-000000-000000", "undecided") is None
    # listing and stats
    lst = captures.list_manifests(c, uid="u9")
    assert lst["total"] == 1 and lst["items"][0]["manifest_id"] == row["manifest_id"]
    assert captures.list_manifests(c, decision="match_rejected")["total"] == 1
    assert captures.list_manifests(c, decision="new_piece")["total"] == 0
    assert captures.list_manifests(c, q="tangerine")["total"] == 1 and captures.list_manifests(c, q="zzz")["total"] == 0
    assert captures.list_manifests(c, since="2999-01-01")["total"] == 0
    s = captures.stats(c)
    assert s["total"] == 1 and s["by_kind"] == {"enroll": 1} and s["people"] == 1 and s["last"]
    z = zipfile.ZipFile(io.BytesIO(captures.zip_bytes(row["manifest_id"])))
    assert sorted(z.namelist()) == [f"{row['manifest_id']}/images/001.jpg", f"{row['manifest_id']}/images/evil.jpg",
                                    f"{row['manifest_id']}/manifest.json"]
    assert captures.zip_bytes("cap-00000000-000000-000000") is None


def zipfile_without_manifest():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("images/001.jpg", _jpg())
    return b.getvalue()


def test_summary_words_for_a_check_session():
    s = captures.summarise(_manifest("verify", "match_confirmed", oid=None, object=None,
                                     reference={"kind": "public", "id": "gidA", "title": "Tangerine Cube", "rating": 84},
                                     match={"verdict": "Match likely", "confidence": 88, "descriptor": "dino", "shots": 15},
                                     decision={"decision": "match_confirmed", "at": "2026-11-05T11:00:00Z"}))
    assert s["kind"] == "verify" and s["object_row_id"] == "gidA" and s["object_title"] == "Tangerine Cube"
    assert s["verdict"] == "Match likely" and s["confidence"] == 88 and s["descriptor"] == "dino" and s["has_embeddings"] == 1
    assert captures.summarise({"decision": {"decision": "nonsense"}})["decision"] == "undecided"
    assert captures.device_line({"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                                       "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                                 "resolution": {"w": 1280, "h": 720}, "compass": False}) == "iPhone · iOS 17.5 · Safari · 1280×720 · no compass"
    assert captures.device_line(None) == ""


# --- the routes ---------------------------------------------------------------------------
def test_upload_read_back_decide_and_the_admin_side(client, tmp_path):
    h = _auth()
    data = _zip(_manifest())
    assert client.post("/me/captures", content=data).status_code == 401
    assert client.post("/me/captures", content=b"", headers=h).status_code == 400
    assert client.post("/me/captures", content=b"PKjunk", headers=h).status_code == 400
    assert client.post("/me/captures", content=b"[1,2]", headers=h).status_code == 400
    r = client.post("/me/captures", content=data, headers={**h, "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    j = r.json()
    mid = j["manifest_id"]
    assert j["ok"] and j["kind"] == "enroll" and j["n_images"] == 3 and j["decision"] == "new_piece"
    assert j["device"].startswith("Pixel 8") and j["top_candidate"] == "Tangerine Cube"
    # multipart works too, and the manifest JSON alone
    r = client.post("/me/captures", files={"file": ("session.zip", _zip(_manifest("verify", "undecided")), "application/zip")}, headers=h)
    assert r.status_code == 200 and r.json()["kind"] == "verify"
    r = client.post("/me/captures", content=json.dumps(_manifest(dec="undecided")).encode(),
                    headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["n_images"] == 0
    # mine, newest first; others see nothing
    lst = client.get("/me/captures", headers=h).json()
    assert lst["total"] == 3 and lst["items"][-1]["manifest_id"] == mid
    assert client.get("/me/captures?kind=verify", headers=h).json()["total"] == 1
    assert client.get("/me/captures", headers=_auth("u2")).json()["total"] == 0
    assert client.get(f"/me/captures/{mid}", headers=_auth("u2")).status_code == 404
    assert client.get(f"/me/captures/{mid}").status_code == 401
    one = client.get(f"/me/captures/{mid}", headers=h).json()
    assert one["manifest"]["server"]["manifest_id"] == mid and one["manifest"]["candidates"]["items"][0]["title"] == "Tangerine Cube"
    assert [f["file"] for f in one["files"]] == ["images/001.jpg", "images/002.jpg", "images/hero.jpg", "manifest.json"]
    assert one["files"][0]["url"] == f"/api/me/captures/{mid}/files/images/001.jpg"
    img = client.get(f"/me/captures/{mid}/files/images/001.jpg", headers=h)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get(f"/me/captures/{mid}/files/images/001.jpg", headers=_auth("u2")).status_code == 404
    assert client.get(f"/me/captures/{mid}/files/../manifest.json", headers=h).status_code == 404
    assert client.get(f"/me/captures/{mid}/files/images/nope.jpg", headers=h).status_code == 404
    # the decision, after the fact
    r = client.post(f"/me/captures/{mid}/decision", json={"decision": "match_confirmed", "note": "same cube after all",
                                                          "object": {"ref": "public:gidA", "title": "Tangerine Cube", "row_id": "gidA"}}, headers=h)
    assert r.status_code == 200 and r.json()["decision"] == "match_confirmed" and r.json()["object_row_id"] == "gidA"
    assert client.post(f"/me/captures/{mid}/decision", json={"decision": "nope"}, headers=h).status_code == 400
    assert client.post(f"/me/captures/{mid}/decision", json={"decision": "undecided"}, headers=_auth("u2")).status_code == 404
    # the person's recent sessions show in the lookup helpers
    rec = client.get("/glassid/recent", headers=h).json()
    assert len(rec["captures"]) == 3 and rec["captures"][0]["kind"] in ("enroll", "verify")
    # administrators: every route gated, then the list, one session, files, download, annotation
    for path in ("/admin/captures", f"/admin/captures/{mid}", f"/admin/captures/{mid}/manifest.json",
                 f"/admin/captures/{mid}/files/images/001.jpg", f"/admin/captures/{mid}/download"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=h).status_code == 403, path
        assert client.get(path, headers=_boss()).status_code == 200, path
        assert client.get(path, headers=_key()).status_code == 200, path
    assert client.post(f"/admin/captures/{mid}/decision", json={"decision": "undecided"}, headers=h).status_code == 403
    a = client.get("/admin/captures", headers=_boss()).json()
    assert a["total"] == 3 and a["summary"]["total"] == 3 and a["summary"]["by_kind"] == {"enroll": 2, "verify": 1}
    assert [d["id"] for d in a["decisions"]] == list(captures.DECISIONS) and a["kinds"] == ["enroll", "verify"]
    assert client.get("/admin/captures?kind=verify", headers=_boss()).json()["total"] == 1
    assert client.get("/admin/captures?decision=match_confirmed", headers=_boss()).json()["total"] == 1
    assert client.get("/admin/captures?q=Pixel", headers=_boss()).json()["total"] == 3
    assert client.get("/admin/captures?since=2999-01-01", headers=_boss()).json()["total"] == 0
    one = client.get(f"/admin/captures/{mid}", headers=_boss()).json()
    assert one["download"] == f"/api/admin/captures/{mid}/download" and one["manifest"]["decision"]["decision"] == "match_confirmed"
    assert one["manifest"]["decisions"][0]["decision"] == "new_piece"
    z = client.get(f"/admin/captures/{mid}/download", headers=_key())
    assert z.headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(z.content)).namelist())[-1] == f"{mid}/manifest.json"
    mj = client.get(f"/admin/captures/{mid}/manifest.json", headers=_boss())
    assert json.loads(mj.content)["server"]["manifest_id"] == mid
    r = client.post(f"/admin/captures/{mid}/decision", json={"decision": "match_rejected", "note": "reviewed: siblings"}, headers=_boss())
    assert r.status_code == 200 and r.json()["decision"] == "match_rejected" and r.json()["decision_note"] == "reviewed: siblings"
    assert client.get("/admin/captures/cap-00000000-000000-000000", headers=_boss()).status_code == 404
    assert client.get("/admin/captures/cap-00000000-000000-000000/download", headers=_boss()).status_code == 404


def test_upload_limits(client, monkeypatch):
    h = _auth()
    monkeypatch.setattr(captures, "MAX_FILE", 1000)
    r = client.post("/me/captures", content=_zip(_manifest(), {"images/big.jpg": _jpg(size=(1200, 900))}), headers=h)
    assert r.status_code == 400 and "too large" in r.json()["detail"]
    monkeypatch.setattr(captures, "MAX_FILES", 2)
    r = client.post("/me/captures", content=_zip(_manifest()), headers=h)
    assert r.status_code == 400 and "too many files" in r.json()["detail"]


def test_the_piece_bundle_names_the_registration_session(client):
    """verify.html loads a registered piece of mine and finds the session that holds its frames."""
    h = _auth("u3", "u3@example.com", "U Three")
    oid = client.post("/me/objects", json={"title": "Sent cube"}, headers=h).json()["id"]
    r = client.post(f"/me/objects/{oid}/fingerprint", content=json.dumps(fp("blue")).encode(), headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 200
    mid = client.post("/me/captures", content=_zip(_manifest(oid=oid)), headers=h).json()["manifest_id"]
    p = client.get(f"/glassid/pieces/mine:{oid}", headers=h).json()
    assert p["captures"] and p["captures"][0]["manifest_id"] == mid


# --- the page, the installer, the proxy -------------------------------------------------------
def test_captures_page_is_registered_everywhere():
    page = (ROOT / "deploy" / "captures.html").read_text(encoding="utf-8")
    assert '<a href="/captures.html" class="on">Captures</a>' in page and "/admin/captures" in page
    assert "scripts.pull_capture" in page and "GLASSDB" not in page.replace("GLASSDB_ADMIN_TOKEN", "")  # no key words on the page
    for name in ("review.html", "events-admin.html", "datasets.html", "analytics.html", "wikibase.html"):
        html = (ROOT / "deploy" / name).read_text(encoding="utf-8")
        assert '<a href="/captures.html">Captures</a>' in html, name
    assert "/captures.html" in (ROOT / "deploy" / "account.html").read_text(encoding="utf-8")
    inst = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert 'cp "$SRC/deploy/captures.html"' in inst and "glassid-kit.js" in inst and "capture_manifests" in inst
    assert "captures" in (ROOT / "deploy" / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8").split("RedirectMatch 301")[1]
    from scripts.dev_site import FILES
    assert FILES["/captures.html"] == "captures.html"
    for name in ("enroll.html", "verify.html"):
        html = (ROOT / "deploy" / "fingerprint-web" / name).read_text(encoding="utf-8")
        assert '<script src="/fingerprint/glassid-kit.js"></script>' in html and 'id="sendBlock"' in html, name
    assert 'id="dupe"' in (ROOT / "deploy" / "fingerprint-web" / "enroll.html").read_text(encoding="utf-8")
    v = (ROOT / "deploy" / "fingerprint-web" / "verify.html").read_text(encoding="utf-8")
    assert 'id="srcFind"' in v and 'id="findByPhoto"' in v and 'id="compare"' in v and 'id="cands"' in v
    assert "capture_manifests" in (ROOT / ".gitignore").read_text(encoding="utf-8")


# --- pull it down, score it ------------------------------------------------------------------
def test_pull_locally_and_bench_the_sessions(client, tmp_path, capsys):
    h = _auth("u4", "u4@example.com", "U Four")
    # two sessions about one piece (orange), one about another (blue): the bench finds one same-piece pair
    client.post("/me/captures", content=_zip(_manifest(dec="new_piece", oid=11)), headers=h)
    client.post("/me/captures", content=_zip(_manifest(dec="re_registered", oid=11,
                                                       fingerprint=fp("orange", dims={"width": 71, "depth": 69, "height": 76}))), headers=h)
    client.post("/me/captures", content=_zip(_manifest(dec="new_piece", oid=12, fingerprint=fp("blue"),
                                                       object={"id": 12, "title": "Blue cube"})), headers=h)
    from scripts import glassid_bench, pull_capture
    out = tmp_path / "pulled"
    assert pull_capture.main(["--local", "--list"]) == 0
    text = capsys.readouterr().out
    assert "Orange cube" in text and "3 session(s) in all" in text
    assert pull_capture.main(["--local", "--all", "--out", str(out)]) == 0
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert len(idx) == 3 and all((out / it["manifest_id"] / "manifest.json").is_file() for it in idx)
    assert all((out / it["manifest_id"] / "images" / "hero.jpg").is_file() for it in idx)
    assert pull_capture.main(["--local", "--latest", "--kind", "verify", "--out", str(out / "none")]) == 1   # nothing of that kind
    rep = tmp_path / "bench.json"
    assert glassid_bench.main(["--manifests", str(out), "--out", str(rep)]) == 0
    text = capsys.readouterr().out
    assert "3 session(s) across 2 piece(s)" in text and "same piece:" in text
    j = json.loads(rep.read_text(encoding="utf-8"))
    auto = j["reports"]["auto"]
    assert auto["positives"] == 2 and auto["negatives"] == 4
    assert auto["at_shipped_thresholds"]["true_accepts"] == 2 and auto["at_shipped_thresholds"]["false_accepts"] == 0
    assert auto["separation"] is not None and auto["best_threshold"]["recall"] == 1.0
    # pictures grouped by piece, split into two sessions each
    from tests.test_glassid import _picture
    for piece, col in (("orange", (230, 120, 20)), ("blue", (30, 80, 220))):
        d = tmp_path / "imgs" / piece
        d.mkdir(parents=True)
        for i in range(4):
            (d / f"{i}.jpg").write_bytes(_picture(tuple(max(0, c - 3 * i) for c in col)))
    assert glassid_bench.main(["--images", str(tmp_path / "imgs"), "--out", ""]) == 0
    text = capsys.readouterr().out
    assert "4 session(s) across 2 piece(s)" in text and "false-accept rate 0.0" in text
    assert glassid_bench.main(["--manifests", str(tmp_path / "empty-nowhere"), "--out", ""]) == 1
