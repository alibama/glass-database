"""The Photo Mat on the phone (deploy/fingerprint-web/glassid-mat.js) and its server mirror
(central.glassid.mat_descriptors): camera angle round the piece from the markers, the
piece's crop, and colour corrected from the printed strip. Views are rendered from
mat_template.png with a known camera, so the true angle is known."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from central import glassid

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "deploy" / "fingerprint-web"
NODE = shutil.which("node")


def render(az, el, dist=450.0, cast=(1.0, 1.0, 1.0), piece=False, size=(960, 720)):
    """RGB picture of the mat seen from (az, el) round the placement area's centre."""
    t = cv2.imread(str(ROOT / "glowtbook" / "mat_template.png"))            # 2 px/mm, BGR
    cx, cy = glassid.PLACE_CENTRE
    if piece:
        c = (int(cx * 2), int(cy * 2))
        cv2.ellipse(t, c, (60, 45), 0, 0, 360, (40, 140, 200), -1)
        cv2.ellipse(t, c, (30, 20), 0, 0, 360, (160, 60, 40), -1)
    W, H = size
    f = (W / 2) / math.tan(math.radians(65 / 2))
    C = np.array([cx + dist * math.cos(math.radians(el)) * math.cos(math.radians(az)),
                  cy + dist * math.cos(math.radians(el)) * math.sin(math.radians(az)),
                  -dist * math.sin(math.radians(el))])
    zc = np.array([cx, cy, 0.0]) - C
    zc /= np.linalg.norm(zc)
    xc = np.cross(zc, [0, 0, -1.0])
    xc /= np.linalg.norm(xc)
    R = np.vstack([xc, np.cross(zc, xc), zc])
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]])
    Hm = K @ np.column_stack([R[:, 0], R[:, 1], -R @ C]) @ np.diag([0.5, 0.5, 1])
    img = cv2.warpPerspective(t, Hm, (W, H), flags=cv2.INTER_LINEAR, borderValue=(90, 110, 140))
    img = np.clip(img.astype(float) * np.array(cast[::-1]), 0, 255).astype(np.uint8)
    return img[..., ::-1].copy()


def jpeg(rgb):  # PNG, lossless: the numbers are about geometry, not compression
    ok, b = cv2.imencode(".png", rgb[..., ::-1])
    return b.tobytes()


VIEWS = [(270, 50), (0, 35), (135, 60), (45, 25), (200, 70), (300, 40)]


def test_server_reads_the_angle_round_the_piece():
    for az, el in VIEWS:
        m = glassid.mat_descriptors(jpeg(render(az, el)))
        assert m, (az, el)
        assert glassid._circ(m["mat"]["az"] - az) < 2.5 and abs(m["mat"]["el"] - el) < 1.5, (az, el, m["mat"])
        assert m["mat"]["n"] == 4 and m["mat"]["cc"] == "strip" and m["crop"] and m["cell"].startswith("m")


def test_server_refuses_without_the_mat():
    assert glassid.mat_descriptors(jpeg(np.full((480, 640, 3), 128, np.uint8))) is None


def test_strip_correction_cancels_a_colour_cast():
    casts = [(1, 1, 1), (1.25, 1.0, 0.7), (0.75, 0.95, 1.2), (0.85, 0.95, 0.7)]
    hs = [glassid.mat_descriptors(jpeg(render(60, 45, cast=c, piece=True)))["chist_p"] for c in casts]
    for h in hs[1:]:
        assert glassid.hist_corr(hs[0], h) > 0.95
    # without the correction the same piece reads as another colour under a warm light
    raw = [glassid.piece_colour(cv2.resize(render(60, 45, cast=c, piece=True)[200:500, 300:700], (128, 128))) for c in casts[:2]]
    assert glassid.hist_corr(raw[0], raw[1]) < 0.7


def test_upgrade_fills_old_frames_from_their_pictures():
    fp = {"frames": [{"file": "frames/001.jpg", "chist": [1] * 64}, {"file": "frames/002.jpg", "mat": {"az": 1, "el": 30}},
                     {"file": "frames/003.jpg"}]}
    n = glassid.upgrade_frames(fp, {"frames/001.jpg": jpeg(render(90, 40, piece=True)), "frames/002.jpg": jpeg(render(10, 40)),
                                    "frames/003.jpg": b"not an image"})
    assert n == 1 and fp["frames"][0]["mat"]["source"] == "server" and fp["frames"][0]["chist_p"]
    assert fp["frames"][1]["mat"] == {"az": 1, "el": 30}               # the page's own numbers are kept
    assert "mat" not in fp["frames"][2] and fp["metadata"]["mat"]["server_upgraded"] == 1


@pytest.mark.skipif(not NODE, reason="node is needed to run the page's JavaScript")
def test_the_page_reads_the_same_angle_crop_and_colour_as_the_server(tmp_path):
    cases = [(az, el, (1, 1, 1)) for az, el in VIEWS] + [(60, 45, (1.25, 1.0, 0.7)), (60, 45, (0.75, 0.95, 1.2))]
    names = []
    for i, (az, el, cast) in enumerate(cases):
        rgb = render(az, el, cast=cast, piece=True)
        rgba = np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])
        (tmp_path / f"{i}.rgba").write_bytes(rgba.tobytes())
        names.append(str(tmp_path / f"{i}.rgba"))
    script = f"""
const fs=require("fs"); const V={json.dumps(str(WEB))};
const {{AR}}=require(V+"/vendor/js-aruco2/aruco.js"); require(V+"/vendor/js-aruco2/dictionaries/aruco_4x4_1000.js");
global.window={{AR}}; require(V+"/glassid-mat.js"); const M=window.GlassMat; const out=[];
for(const f of {json.dumps(names)}){{ const raw=fs.readFileSync(f);
  const img={{width:960,height:720,data:new Uint8ClampedArray(raw.buffer,raw.byteOffset,raw.length)}};
  M.reset(); const r=M.analyze(img); const b=M.cropBox(r); let ch=null;
  if(b){{ const sw=b[2]-b[0], sh=b[3]-b[1], d=new Uint8ClampedArray(128*128*4);
    for(let y=0;y<128;y++)for(let x=0;x<128;x++){{const sx=b[0]+Math.floor((x+.5)*sw/128), sy=b[1]+Math.floor((y+.5)*sh/128), o=(sy*960+sx)*4, q=(y*128+x)*4; d[q]=img.data[o];d[q+1]=img.data[o+1];d[q+2]=img.data[o+2];d[q+3]=255;}}
    ch=M.pieceColour({{width:128,height:128,data:d}},M.lut(r.neutral)); }}
  out.push({{ok:r.ok, pose:r.pose, neutral:!!(r.neutral&&r.neutral.ok), box:b, ch, cell:M.cellOf(r.pose)}}); }}
process.stdout.write(JSON.stringify(out));"""
    (tmp_path / "m.js").write_text(script)
    js = json.loads(subprocess.run([NODE, str(tmp_path / "m.js")], capture_output=True, text=True, check=True).stdout)
    for (az, el, cast), r in zip(cases, js):
        assert r["ok"] and r["neutral"], (az, el, r)
        assert glassid._circ(r["pose"]["az"] - az) < 2.5 and abs(r["pose"]["el"] - el) < 2.5, (az, el, r["pose"])
        srv = glassid.mat_descriptors(jpeg(render(az, el, cast=cast, piece=True)))
        # same angle as the server (cells can differ when a view sits on a 30-degree or 45-degree boundary)
        assert glassid._circ(r["pose"]["az"] - srv["mat"]["az"]) < 2.5 and abs(r["pose"]["el"] - srv["mat"]["el"]) < 2.5
        assert all(abs(a / s - b) < 0.02 for a, b, s in zip(r["box"], srv["crop"], (960, 720, 960, 720)))
        assert glassid.hist_corr(r["ch"], srv["chist_p"]) > 0.97
    # one piece under a warm and a cool light: the page's corrected colour agrees with itself
    plain = [r["ch"] for (az, el, c), r in zip(cases, js) if (az, el) == (60, 45)]
    assert len(plain) == 2 and glassid.hist_corr(plain[0], plain[1]) > 0.95
