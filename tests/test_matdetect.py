"""Photo Mat measurement, checked on rendered phone pictures.

Each test renders a 3-D piece (sphere, cylinder) standing on the printed mat
(glowtbook/mat_template.png) lying on a textured table, through a pinhole camera
orbiting at a few heights, with a soft shadow and the exposure of an ordinary
room, then shrinks every frame to the 384 px, quality-60 JPEG thumbnail that
enroll.html stores. The measurement only ever sees those thumbnails.
"""
import io
import math
import os
import zipfile

import pytest

matdetect = pytest.importorskip("glowtbook.matdetect")
pytestmark = pytest.mark.skipif(not matdetect.available(), reason="opencv/aruco not installed")

PLANE_PPM, PLANE_EXT = 3.0, 350.0       # rendered table: 3 px/mm, 350 mm past the page
CENTRE = (108.0, 128.0)                  # middle of the grey placement area, page mm
_PLANE = None


def _plane():
    """The table with the mat lying on it, page mm -> px at PLANE_PPM."""
    global _PLANE
    if _PLANE is None:
        import cv2
        import numpy as np
        page = cv2.imread(os.path.join(os.path.dirname(matdetect.__file__), "mat_template.png"))
        rng = np.random.default_rng(42)
        W, H = int((216 + 2 * PLANE_EXT) * PLANE_PPM), int((279 + 2 * PLANE_EXT) * PLANE_PPM)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        grain = np.sin(xx / 9.0 + 3 * np.sin(yy / 140.0)) * 0.5 + 0.5
        noise = cv2.GaussianBlur(rng.random((H, W)).astype(np.float32), (0, 0), 4)
        v = 0.75 + 0.15 * grain + 0.075 * (noise - noise.mean()) / (noise.std() + 1e-6)
        tab = np.stack([70 * v, 105 * v, 150 * v], 2)
        pg = cv2.resize(page, (int(216 * PLANE_PPM), int(279 * PLANE_PPM)), interpolation=cv2.INTER_LINEAR)
        o = int(PLANE_EXT * PLANE_PPM)
        tab[o:o + pg.shape[0], o:o + pg.shape[1]] = pg
        _PLANE = np.clip(tab, 0, 255).astype(np.uint8)
    return _PLANE


def _camera(az, el, dist, W=960, H=1280):
    import numpy as np
    f = 0.8 * max(W, H)
    T = np.array([*CENTRE, 0.0])
    a, e = math.radians(az), math.radians(el)
    C = T + dist * np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), -math.sin(e)])
    z = (T - C) / np.linalg.norm(T - C)
    up = np.array([0, 0, 1.0])                   # page z points into the table
    y = up - (up @ z) * z
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    R = np.stack([x, y, z])
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]])
    return K, R, -R @ C, (W, H)


def _points(kind, dims):
    """Surface points of the piece, standing on the mat at CENTRE (z up = negative)."""
    import numpy as np
    cx, cy = CENTRE
    pts = []
    if kind == "sphere":
        r = dims[0] / 2
        for th in np.linspace(0, math.pi, 30):
            for ph in np.linspace(0, 2 * math.pi, 60):
                pts.append((cx + r * math.sin(th) * math.cos(ph), cy + r * math.sin(th) * math.sin(ph),
                            -(r + r * math.cos(th))))
    else:                                        # cylinder: diameter, height
        r, h = dims[0] / 2, dims[1]
        for z in (0, h):
            for ph in np.linspace(0, 2 * math.pi, 90):
                pts.append((cx + r * math.cos(ph), cy + r * math.sin(ph), -z))
    return np.array(pts, float)


def _shot(kind, dims, az, el, dist=420, bright=0.8, cast=(1, 1, 1), seed=0):
    import cv2
    import numpy as np
    rng = np.random.default_rng(seed)
    K, R, t, (W, H) = _camera(az, el, dist)
    Hpl = K @ np.column_stack([R[:, 0], R[:, 1], t])
    M = Hpl @ np.array([[1 / PLANE_PPM, 0, -PLANE_EXT], [0, 1 / PLANE_PPM, -PLANE_EXT], [0, 0, 1]])
    wall = (200, 205, 210)
    img = cv2.warpPerspective(_plane(), M, (W, H), borderValue=wall)
    Mi = np.linalg.inv(M)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    img[(Mi[2, 0] * xx + Mi[2, 1] * yy + Mi[2, 2]) <= 0] = wall      # above the horizon

    def project(P):
        c = (R @ P.T).T + t
        p = (K @ c.T).T
        return (p[:, :2] / p[:, 2:3]).astype(np.float32)

    P = _points(kind, dims)
    sh = P.copy()                                 # soft shadow from a light up and to the left
    hz = -sh[:, 2]
    sh[:, 0] += 0.6 * hz
    sh[:, 1] += 0.3 * hz
    sh[:, 2] = 0
    m = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(m, cv2.convexHull(project(sh)).astype(np.int32), 255)
    m = cv2.GaussianBlur(m, (0, 0), 6).astype(np.float32) / 255
    img = img * (1 - 0.35 * m[..., None])
    obj = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(obj, cv2.convexHull(project(P)).astype(np.int32), 255)
    col = np.array((60, 50, 190), float) * (0.75 + 0.5 * rng.random((H, W, 1)))
    img = np.where(obj[..., None] > 0, col, img)
    img = img * bright * np.array(cast)[None, None, :] + rng.normal(0, 3, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    s = 384 / max(W, H)
    img = cv2.resize(img, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes()


def _orbit(kind, dims, els=(80, 50, 30), n=12, **kw):
    return [_shot(kind, dims, az=i * 360 / n + 7, el=els[i % len(els)], seed=i, **kw) for i in range(n)]


def _close(got, want, tol):
    assert got is not None and abs(got - want) <= tol, f"{got} vs {want} ± {tol}"


def test_round_ornament():
    """The case that used to read ~10 inches: an 80 mm ball in ordinary light."""
    r = matdetect.measure_frames(_orbit("sphere", (80,)))
    assert r["ok"], r
    _close(r["width_mm"], 80, 6)
    _close(r["depth_mm"], 80, 6)
    _close(r["height_mm"], 80, 6)


def test_straight_sided_piece_under_warm_dim_light():
    r = matdetect.measure_frames(_orbit("cylinder", (40, 90), bright=0.7, cast=(0.85, 1.0, 1.15)))
    assert r["ok"], r
    _close(r["width_mm"], 40, 6)
    _close(r["depth_mm"], 40, 6)
    _close(r["height_mm"], 90, 6)


def test_straight_down_views_report_no_height():
    r = matdetect.measure_frames(_orbit("sphere", (80,), els=(85, 78), n=6))
    assert r["ok"], r
    assert r["height_mm"] is None and "note" in r
    _close(r["width_mm"], 80, 9)


def test_no_mat_is_graceful():
    import cv2
    import numpy as np
    blank = cv2.imencode(".jpg", np.full((300, 300, 3), 200, np.uint8))[1].tobytes()
    r = matdetect.measure_frames([blank, blank])
    assert r["ok"] is False


def test_mat_details_reports_pose():
    d = matdetect.mat_details(_shot("sphere", (80,), az=40, el=50))
    assert d["markers"] >= 2 and d["mm_per_px"]
    assert d["camera_height_mm"] and 250 < d["camera_height_mm"] < 400      # truth: 420 * sin 50° = 322


def test_measure_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, b in enumerate(_orbit("sphere", (80,), n=6)):
            z.writestr(f"frames/{i:03d}.jpg", b)
        z.writestr("fingerprint.json", b"{}")
    r = matdetect.measure_zip(buf.getvalue())
    assert r["ok"] and "height_mm" in r and "width_mm" in r
