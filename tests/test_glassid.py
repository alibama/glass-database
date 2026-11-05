"""The matching arithmetic of Glass ID on the server (central.glassid), in step with the
capture pages: the maths, the four-clearly-different-pieces guarantee, the size gate,
candidate ranking, the registry references behind the lookup, and the lookup /
candidates / compare / pieces routes. Synthetic fingerprints throughout; the one test
that needs real pictures draws them."""
import io
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central import approvals, glassid, sessions
from central.dbconn import connect

ROOT = Path(__file__).resolve().parent.parent
_rng = random.Random(7)

# sixteen hue bins x four saturation bins: a piece is "one hue" with a little noise
HUES = {"orange": 1, "amber": 2, "green": 6, "blue": 10, "violet": 12, "red": 15}


def chist(hue_bin, noise=25):
    ch = [_rng.randint(0, noise) for _ in range(64)]
    ch[hue_bin * 4 + 2] += 650
    ch[hue_bin * 4 + 3] += 250
    return ch


def unit(k, dim=16, noise=0.04):
    v = [1.0 if i == k else 0.0 for i in range(dim)]
    return [round(x + _rng.uniform(-noise, noise), 5) for x in v]


def fp(hue, *, n=12, emb=None, dims=None, sharp=40.0, rating=84, cells=8):
    frames = []
    for i in range(n):
        fr = {"file": f"frames/{i:03d}.jpg", "seq": i + 1, "sector": i % cells, "cell": f"{i % cells}:1",
              "sharp": sharp, "detail": 0.05, "chist": chist(HUES[hue]),
              "dhash": [_rng.getrandbits(32), _rng.getrandbits(32)]}
        if emb is not None and i < 8:
            fr["emb"] = unit(emb)
        frames.append(fr)
    md = {"dominantColor": {"name": hue, "hex": "#000"}, "hasEmbeddings": emb is not None}
    if dims:
        md["dimensions_mm"] = dims
    return {"tool": "glass-fingerprint", "version": 1, "created": "2026-11-05T10:00:00Z", "rating": rating,
            "tier": "Strong", "metadata": md, "frames": frames}


# --- the maths ------------------------------------------------------------------------
def test_maths_match_the_page():
    assert glassid.hist_corr([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert glassid.hist_corr([1, 2, 3, 4], [4, 3, 2, 1]) == 0.0          # clipped at 0, like histCorr
    assert glassid.hist_corr([5, 5, 5], [1, 2, 3]) == 0.0                # no variance -> 0
    assert glassid.hist_corr([1, 2], [1, 2, 3]) == 0.0                   # mismatched lengths
    assert glassid.cosine([1, 0], [1, 0]) == 1.0 and glassid.cosine([1, 0], [-1, 0]) == 0.0
    assert glassid.hamming([0xFFFFFFFF, 0], [0, 0]) == 32 and glassid.hamming(None, [1, 2]) == 64
    assert glassid.dhash_sim(6) == 1.0 and glassid.dhash_sim(22) == 0.0 and glassid.dhash_sim(14) == 0.5
    assert glassid.js_round(2.5) == 3 and glassid.js_round(0.49) == 0
    t = glassid.thresholds()
    assert (t["MATCH_READY"], t["NO_MATCH"], t["STRONG_T"], t["MIN_EFFORT"]) == (70, 40, 0.5, 8)


def test_thresholds_are_the_pages_thresholds():
    """The numbers in verify.html and the kit are the server's numbers."""
    page = (ROOT / "deploy" / "fingerprint-web" / "verify.html").read_text(encoding="utf-8")
    kit = (ROOT / "deploy" / "fingerprint-web" / "glassid-kit.js").read_text(encoding="utf-8")
    assert f"SIM_LO={glassid.SIM_LO}, SIM_HI={glassid.SIM_HI};" in page
    assert f"STRONG_T={glassid.STRONG_T};" in page and f"SPREAD_TARGET={glassid.SPREAD_TARGET};" in page
    assert f"MATCH_READY={glassid.MATCH_READY}, NO_MATCH={glassid.NO_MATCH};" in page
    assert f"MIN_EFFORT={glassid.MIN_EFFORT};" in page
    assert (f"SIM_LO: {glassid.SIM_LO}, SIM_HI: {glassid.SIM_HI}, STRONG_T: {glassid.STRONG_T}, "
            f"SPREAD_TARGET: {glassid.SPREAD_TARGET}, MATCH_READY: {glassid.MATCH_READY}, NO_MATCH: {glassid.NO_MATCH}, "
            f"MIN_EFFORT: {glassid.MIN_EFFORT}") in kit


def test_frames_are_normalised_like_apply_ref():
    frames = glassid.frames_of({"frames": [{"file": "frames/003.jpg", "chist": [1] * 64}, {"chist": [1] * 64, "sector": None}]})
    assert frames[0]["cell"] == "cframes/003.jpg" and frames[0]["sector"] == -1 and frames[0]["seq"] == 1
    assert frames[1]["file"] == "frames/001.jpg" and frames[1]["cell"] == "cframes/001.jpg"
    assert glassid.frames_of("not json") == [] and glassid.frames_of(None) == []


# --- the easy wins and the gates --------------------------------------------------------
def test_four_clearly_different_pieces_never_match_and_each_matches_itself():
    pieces = {h: fp(h, emb=i) for i, h in enumerate(("orange", "blue", "green", "violet"))}
    again = {h: fp(h, emb=i) for i, h in enumerate(("orange", "blue", "green", "violet"))}   # a second capture
    for a, qa in pieces.items():
        for b, rb in again.items():
            c = glassid.compare(qa, rb)
            if a == b:
                assert c["verdict"] == "match" and c["confidence"] >= glassid.MATCH_READY, (a, c["confidence"])
                assert c["views_matched"] >= glassid.MIN_CELLS_MATCH
            else:
                assert c["verdict"] == "nomatch" and c["confidence"] < glassid.NO_MATCH, (a, b, c["confidence"])
            # every descriptor is reported, with the pairs and the views that carried the score
            assert set(c["breakdown"]) == {"dino", "color", "dhash"}
            assert c["descriptor"] == "dino" and c["breakdown"]["color"]["available"]
            assert c["reasons"] and c["reasons"][0].startswith("AI texture (DINOv2):")
            assert "carried_by" in c and len(c["pairs"]) <= 12


def test_colour_alone_when_no_embeddings_and_caveat_says_so():
    c = glassid.compare(fp("orange"), fp("orange"))
    assert c["descriptor"] == "color" and c["verdict"] == "match"
    assert c["breakdown"]["dino"]["available"] is False
    assert any("Colour" in s and "similarly coloured" in s for s in c["caveats"])
    assert any(s.startswith("No AI texture comparison") for s in c["reasons"])
    # one side with embeddings, the other without -> colour, not zeros
    c2 = glassid.compare(fp("orange", emb=0), fp("orange"))
    assert c2["descriptor"] == "color" and c2["confidence"] >= glassid.MATCH_READY


def test_no_match_needs_effort_and_a_thin_reference_is_flagged():
    c = glassid.compare(fp("orange", n=4), fp("blue"))          # four shots: not enough to say no
    assert c["verdict"] == "inconc" and any("sharp shot" in s for s in c["caveats"])
    c = glassid.compare(fp("orange", n=4), fp("blue"), effort=12)
    assert c["verdict"] == "nomatch"
    c = glassid.compare(fp("orange"), fp("orange", rating=55))
    assert any("only 55/100" in s for s in c["caveats"])
    blurry = fp("orange"); [f.__setitem__("sharp", 3) for f in blurry["frames"]]
    assert glassid.effort_of(glassid.frames_of(blurry)) == 0


def test_size_gate_holds_a_picture_match_at_inconclusive():
    small = fp("orange", dims={"width": 70, "depth": 68, "height": 75})
    big = fp("orange", dims={"width": 70, "depth": 68, "height": 110})
    c = glassid.compare(small, big)
    assert c["breakdown"]["color"]["verdict"] == "match"          # the pictures say match
    assert c["size_conflict"] and c["verdict"] == "inconc" and c["size"]["ok"] is False
    assert "apart" in c["size"]["text"] and any("mat sizes do not" in s for s in c["caveats"])
    ok = glassid.compare(small, fp("orange", dims={"width": 72, "depth": 69, "height": 77}))
    assert ok["size"]["ok"] is True and ok["verdict"] == "match" and "within" in ok["size"]["text"]
    none = glassid.compare(small, fp("orange"))
    assert none["size"]["ok"] is None and "registered piece" in none["size"]["text"]
    assert glassid.size_check(None, None)["text"] == "no mat measurement on either side"


def test_candidates_rank_margin_and_ambiguity():
    refs = [{"ref": "public:a", "kind": "public", "id": "a", "title": "Blue cube", "fingerprint": fp("blue", emb=1)},
            {"ref": "public:b", "kind": "public", "id": "b", "title": "Orange cube", "fingerprint": fp("orange", emb=0)},
            {"ref": "mine:3", "kind": "mine", "id": 3, "title": "Green cube", "fingerprint": fp("green", emb=2)},
            {"ref": "public:broken", "kind": "public", "id": "x", "title": "Broken", "fingerprint": {"frames": "nope"}}]
    out = glassid.candidates(fp("orange", emb=0), refs, k=3)
    titles = [c["title"] for c in out["candidates"]]
    assert titles[0] == "Orange cube" and out["candidates"][0]["verdict"] == "match"
    assert out["margin"] >= 50 and out["ambiguous"] is False and out["considered"] == 4
    assert out["candidates"][0]["fingerprint"]["views"] == 12 and "frames" not in out["candidates"][0]["fingerprint"]
    assert out["query"]["has_embeddings"] and out["with_embeddings"] == 3
    # two look-alikes of the same hue: the margin is thin and the caller is told
    import copy
    twin = fp("orange")
    twins = [{"ref": "public:t1", "kind": "public", "id": "t1", "title": "Twin 1", "fingerprint": twin},
             {"ref": "public:t2", "kind": "public", "id": "t2", "title": "Twin 2", "fingerprint": copy.deepcopy(twin)}]
    out = glassid.candidates(fp("orange"), twins, k=2)
    assert out["ambiguous"] is True and out["margin"] < glassid.AMBIGUOUS_MARGIN
    assert any("do not separate them" in s for s in out["caveats"])
    # nothing close: say so
    out = glassid.candidates(fp("red"), twins, k=2)
    assert out["candidates"][0]["confidence"] < glassid.NO_MATCH and any("Nothing on file" in s for s in out["caveats"])
    assert glassid.candidates(fp("red"), [], k=2)["caveats"]


# --- the references behind the lookup ----------------------------------------------------
def _insert_public(c, rid, title, maker, fingerprint, status="approved"):
    from glowtbook.contribute import ensure_central_objects
    ensure_central_objects(c)
    man = {"title": title, "maker": maker, "fingerprint": fingerprint}
    c.execute("INSERT OR REPLACE INTO objects (_row_id,title,maker,year,techniques,manifest_json,content_hash,published_at) "
              "VALUES (?,?,?,?,?,?,?,datetime('now'))", (rid, title, maker, "2026", "Offhand Blown Glass",
                                                        json.dumps(man), "hash-" + rid))
    c.execute("INSERT INTO object_images (object_row_id, role, caption, image_b64) VALUES (?,?,?,?)",
              (rid, "primary", "", "aGVsbG8="))
    c.commit()
    approvals.ensure_approvals(c)
    if status:
        approvals.set_status(c, "objects", [rid], status)


@pytest.fixture
def registry(demo_db):
    c = connect()
    _insert_public(c, "gidA", "Tangerine Cube", "Raging Buffalo Glass", fp("orange", emb=0, dims={"width": 70, "depth": 68, "height": 75}))
    _insert_public(c, "gidB", "Cobalt Cube", "Raging Buffalo Glass", fp("blue", emb=1))
    _insert_public(c, "gidP", "Pending Cube", "Someone", fp("green", emb=2), status=None)
    _insert_public(c, "gidR", "Rejected Cube", "Someone", fp("violet", emb=3), status="rejected")
    glassid.invalidate()
    yield c
    for rid in ("gidA", "gidB", "gidP", "gidR"):
        c.execute("DELETE FROM objects WHERE _row_id=?", (rid,))
        c.execute("DELETE FROM object_images WHERE object_row_id=?", (rid,))
        c.execute("DELETE FROM _approvals WHERE tbl='objects' AND row_id=?", (rid,))
    c.commit()
    glassid.invalidate()


def test_public_refs_respect_the_gate_and_cache(registry):
    ids = {r["id"] for r in glassid.public_refs(registry)}
    assert {"gidA", "gidB"} <= ids and "gidP" not in ids and "gidR" not in ids
    assert "gidP" in {r["id"] for r in glassid.public_refs(registry, include_pending=True)}
    a = next(r for r in glassid.public_refs(registry) if r["id"] == "gidA")
    assert a["ref"] == "public:gidA" and a["image"] == "/api/objects/gidA/image" and a["maker"] == "Raging Buffalo Glass"
    assert a["check"] == "/fingerprint/verify.html?object=gidA"
    first = glassid.cached_public_refs(registry)
    assert glassid.cached_public_refs(registry) is first              # reused inside the TTL
    assert glassid.cached_public_refs(registry, force=True) is not first


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_CAPTURES_DIR", str(tmp_path / "caps"))
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def _register_mine(client, h, title, fingerprint):
    oid = client.post("/me/objects", json={"title": title, "maker": "Me"}, headers=h).json()["id"]
    r = client.post(f"/me/objects/{oid}/fingerprint", content=json.dumps(fingerprint).encode(),
                    headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return oid


def test_lookup_recent_pieces_and_candidates_routes(client, registry):
    h = _auth()
    mine = _register_mine(client, h, "My Lime Cube", fp("green", emb=2))
    # lookup: by words, by record id, by maker; mine only when signed in
    j = client.get("/glassid/lookup?q=cube").json()
    assert {p["title"] for p in j["public"]} >= {"Tangerine Cube", "Cobalt Cube"} and j["mine"] == [] and j["signed_in"] is False
    assert "Pending Cube" not in {p["title"] for p in j["public"]}
    j = client.get("/glassid/lookup?q=gidA").json()
    assert [p["title"] for p in j["public"]] == ["Tangerine Cube"] and j["public"][0]["fingerprint"]["views"] == 12
    assert j["public"][0]["fingerprint"]["dimensions_mm"]["height"] == 75 and "frames" not in j["public"][0]["fingerprint"]
    j = client.get("/glassid/lookup?q=raging buffalo", headers=h).json()
    assert len(j["public"]) >= 2 and j["mine"] == []
    j = client.get("/glassid/lookup?q=lime", headers=h).json()
    assert [m["title"] for m in j["mine"]] == ["My Lime Cube"] and j["mine"][0]["ref"] == f"mine:{mine}"
    assert client.get("/glassid/lookup?q=").json() == {"q": "", "public": [], "mine": [], "signed_in": False}
    # recent: my pieces + the newest public pieces
    j = client.get("/glassid/recent", headers=h).json()
    assert [m["id"] for m in j["mine"]] == [mine] and len(j["public"]) >= 2 and j["captures"] == []
    # the reference bundle for the compare panel
    p = client.get("/glassid/pieces/public:gidA").json()
    assert p["title"] == "Tangerine Cube" and p["images"] == ["/api/objects/gidA/image?i=0"]
    assert p["fingerprint"]["has_embeddings"] and p["fingerprint_url"] == "/api/objects/gidA/fingerprint"
    assert client.get("/glassid/pieces/public:gidP").status_code == 404
    assert client.get(f"/glassid/pieces/mine:{mine}").status_code == 401
    assert client.get(f"/glassid/pieces/mine:{mine}", headers=_auth("u2")).status_code == 404
    p = client.get(f"/glassid/pieces/mine:{mine}", headers=h).json()
    assert p["title"] == "My Lime Cube" and p["images"] == [] and p["has_fingerprint"] and p["captures"] == []
    assert client.get("/glassid/pieces/nonsense").status_code == 404
    # candidates: anonymous sees public pieces; signed in, their own too; the orange capture finds the tangerine
    q = fp("orange", emb=0, dims={"width": 71, "depth": 69, "height": 76})
    j = client.post("/glassid/candidates", json={"fingerprint": q, "k": 3}).json()
    assert j["candidates"][0]["ref"] == "public:gidA" and j["candidates"][0]["verdict"] == "match"
    assert j["candidates"][0]["size"]["ok"] is True and j["corpus"]["mine"] == 0 and j["corpus"]["signed_in"] is False
    assert all(c["kind"] == "public" for c in j["candidates"])
    j = client.post("/glassid/candidates", json={"fingerprint": fp("green", emb=2), "k": 3}, headers=h).json()
    assert j["candidates"][0]["ref"] == f"mine:{mine}" and j["corpus"]["mine"] == 1
    j = client.post("/glassid/candidates", json={"fingerprint": q, "exclude": ["public:gidA"]}).json()
    assert all(c["ref"] != "public:gidA" for c in j["candidates"])
    assert client.post("/glassid/candidates", json={"fingerprint": {"frames": []}}).status_code == 400
    assert client.post("/glassid/candidates", json={}).status_code == 400
    # compare against one piece
    j = client.post("/glassid/compare", json={"fingerprint": q, "ref": "public:gidA"}).json()
    assert j["confidence"] >= glassid.MATCH_READY and j["piece"]["title"] == "Tangerine Cube" and "frames" not in j["piece"]["fingerprint"]
    assert client.post("/glassid/compare", json={"fingerprint": q, "ref": "public:nope"}).status_code == 404
    assert client.post("/glassid/compare", json={"fingerprint": q, "ref": f"mine:{mine}"}).status_code == 401
    assert client.post("/glassid/compare", json={"fingerprint": q, "ref": f"mine:{mine}"}, headers=h).status_code == 200


def test_a_published_piece_of_mine_is_listed_once(client, registry):
    """all_refs: my piece that is already public (same content hash) is the public record, not a twin."""
    h = _auth("u5", "u5@example.com", "U Five")
    oid = _register_mine(client, h, "Twin Cube", fp("red", emb=4))
    from glowtbook import store as gstore
    st = gstore.open_store()
    st.execute("INSERT INTO contribution (user_id, object_id, content_hash, manifest_json) VALUES (?,?,?,?)",
               ("u5", oid, "hash-gidA", "{}"))
    st.commit()
    refs = glassid.all_refs(registry, st, "u5")
    assert "public:gidA" in {r["ref"] for r in refs} and f"mine:{oid}" not in {r["ref"] for r in refs}


# --- descriptors from pictures ---------------------------------------------------------
def _picture(colour, bg=(40, 40, 40), size=(640, 480)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle([200, 120, 440, 360], fill=colour)
    d.ellipse([260, 180, 380, 300], fill=tuple(max(0, c - 60) for c in colour))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90); return buf.getvalue()


def test_descriptors_from_pictures_separate_colours():
    orange = [_picture((230, 120, 20)), _picture((235, 125, 25), bg=(45, 40, 38)), _picture((225, 115, 18))]
    blue = [_picture((30, 80, 220)), _picture((28, 85, 225)), _picture((35, 78, 215))]
    d = glassid.descriptors(orange[0])
    assert d["sharp"] > glassid.SHARP_MIN and 0 < d["detail"] < 1 and len(d["chist"]) == 64 and sum(d["chist"]) in (999, 1000, 1001)
    assert len(d["dhash"]) == 2 and all(0 <= x <= 0xFFFFFFFF for x in d["dhash"])
    assert glassid.dominant_colour(d["chist"])["name"] in ("orange", "yellow", "red")
    fo = glassid.fingerprint_from_images([(f"{i}.jpg", b) for i, b in enumerate(orange)])
    fb = glassid.fingerprint_from_images([(f"{i}.jpg", b) for i, b in enumerate(blue)])
    assert len(fo["frames"]) == 3 and fo["metadata"]["hasEmbeddings"] is False and fo["metadata"]["dominantColor"]
    same = glassid.compare(fo, glassid.fingerprint_from_images([("x.jpg", orange[1]), ("y.jpg", orange[2])]), effort=12)
    diff = glassid.compare(fo, fb, effort=12)
    assert same["breakdown"]["color"]["quality"] > 0.9 and diff["breakdown"]["color"]["confidence"] < glassid.NO_MATCH
    assert glassid.fingerprint_from_images([("bad.jpg", b"not an image")])["frames"] == []


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to run the page's JavaScript")
def test_descriptor_port_agrees_with_the_pages_javascript(tmp_path):
    """The Python dHash and colour histogram equal the page's own functions, run in Node
    on the same 160x120 pixel buffer, bit for bit (so server scores mean what the page's do)."""
    import numpy as np
    html = (ROOT / "deploy" / "fingerprint-web" / "enroll.html").read_text(encoding="utf-8")
    js = html.split("<script>")[-1].split("</script>")[0]

    def grab(name):
        i = js.index(f"function {name}(")
        depth, j = 0, i
        while True:
            c = js[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return js[i:j + 1]
            j += 1
    d, w, h = glassid._rgb_array(_picture((200, 90, 160), bg=(230, 225, 215)))
    rgba = np.zeros((h, w, 4), np.uint8); rgba[..., :3] = d.astype(np.uint8); rgba[..., 3] = 255
    (tmp_path / "buf.rgba").write_bytes(rgba.tobytes())
    g = 0.299 * d[..., 0] + 0.587 * d[..., 1] + 0.114 * d[..., 2]
    py = {"dhash": glassid.dhash_of(g, w, h), "chist": glassid.colour_hist(glassid.white_balance(d.copy()), w, h)}
    script = ("const CROP=0.6; const CH_HB=16, CH_SB=4, CH_N=CH_HB*CH_SB;\n"
              + "\n".join(grab(n) for n in ("popcount", "dhash", "rgb2hs", "whiteBalance", "colorHist"))
              + f"""
const fs=require("fs"); const raw=fs.readFileSync({json.dumps(str(tmp_path / 'buf.rgba'))});
const d=new Uint8ClampedArray(raw.buffer, raw.byteOffset, raw.length); const w={w}, h={h};
const g=new Float32Array(w*h); for(let i=0,p=0;i<d.length;i+=4,p++) g[p]=0.299*d[i]+0.587*d[i+1]+0.114*d[i+2];
const dh=dhash(g,w,h); whiteBalance(d); const ch=colorHist(d,w,h);
process.stdout.write(JSON.stringify({{dhash:dh, chist:ch}}));""")
    (tmp_path / "parity.js").write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(tmp_path / "parity.js")], capture_output=True, text=True, check=True)
    js_res = json.loads(out.stdout)
    assert js_res["dhash"] == py["dhash"]
    assert js_res["chist"] == py["chist"]
