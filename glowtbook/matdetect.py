"""
glowtbook.matdetect
===================
Turn capture frames shot on the printed Photo Mat into real measurements (mm).

The mat (deploy/fingerprint-web/capture-mat.pdf, US Letter) carries four ArUco
markers (DICT_4X4_50, ids 0-3, 29.9 mm) at known places on the page. Every frame
is handled in mat coordinates, so tilt, rotation and distance drop out:

1. **Pose.** The marker corners give the homography from the mat plane (mm) to
   the picture, the camera's focal length (median over the session's tilted
   views) and, with it, where the camera was: its foot point G on the plane and
   its height Hc above it.
2. **Flatten.** The picture is warped onto a top-down grid of the plane, 1 px per
   mm, reaching 250 mm past the page on every side: the table the mat lies on is
   part of the same plane, and low side views throw the piece's silhouette well
   past the paper.
3. **Segment.** On the page, against ``mat_template.png`` (the printed page),
   with each frame's own exposure, white balance and light fall-off fitted as a
   per-channel gain with a gentle gradient. Off the page, against the table
   itself: flattened, the same patch of table looks the same in every frame, so
   its median across the session is the table, and a frame's object is what
   differs from it. Soft shadows (darker, same colour) count as background.
4. **Footprint.** Every point the piece stands on is covered by it in every view,
   so the ground vote (object in nearly all frames) seeds where the piece is.
5. **Carve.** A point at height z above plane point X lands on the plane at
   G + (X - G) * Hc / (Hc - z). It belongs to the piece only if every frame that
   sees where it lands shows object there (a visual hull). Every column is tried
   at every height (under an ornament's bulge the ground is visible). Width x
   depth = minimum-area rectangle of the columns that hold something; height =
   the top of what survives. Height needs at least one view from the side
   (elevation below ~65 deg); with only straight-down views it is reported as
   None.

A visual hull can only be bigger than the piece: expect heights a few mm high
for a rounded top seen only from steep angles. Clear glass that shows the mat
through itself under-measures. Runs server-side on import (OpenCV); degrades to
"no measurement" if OpenCV isn't installed or the mat isn't visible.

``mat_template.png`` is the PDF's 300 dpi page image resized to 2 px/mm
(432 x 558). If the mat changes, regenerate it and ``MARKER_CORNERS_MM``.
"""
from __future__ import annotations

import io
import math
import os
import statistics


def available() -> bool:
    try:
        import cv2  # noqa: F401
        return hasattr(__import__("cv2"), "aruco")
    except Exception:
        return False


MARKER_MM = 29.89
# Marker corners on the printed page, mm from the top-left (TL, TR, BR, BL), as
# OpenCV reports them for the upright mat. Measured from capture-mat.pdf.
_X0, _X1, _Y0, _Y1 = 15.24, 170.69, 49.53, 176.53
MARKER_CORNERS_MM = {
    i: [(x, y), (x + MARKER_MM, y), (x + MARKER_MM, y + MARKER_MM), (x, y + MARKER_MM)]
    for i, (x, y) in {0: (_X0, _Y0), 1: (_X1, _Y0), 2: (_X0, _Y1), 3: (_X1, _Y1)}.items()
}
PAGE_MM = (216, 279)
TEMPLATE_PX = (432, 558)                  # mat_template.png, 2 px/mm
EXT_MM = 250                              # how far past the page the grid reaches
GRID = (PAGE_MM[0] + 2 * EXT_MM, PAGE_MM[1] + 2 * EXT_MM)   # (w, h), 1 px = 1 mm
# The grey placement area, page mm (x0, y0, x1, y1): where the piece should stand.
PLACE_MM = (50.0, 83.0, 200.5, 174.0)

MIN_MARKERS = 2
SIDE_VIEW_MAX_ELEV = 65.0                 # deg; steeper views can't see height
MAX_HEIGHT_MM = 400.0
_MIN_FOOTPRINT_MM2 = 60.0

_TEMPLATE = None


def _px(v):
    """page mm -> grid px (same for x and y)."""
    return v + EXT_MM


def _template():
    """(template on the grid as float BGR, page mask, trusted mask) — trusted =
    page pixels away from sharp print, which a warp can't line up exactly."""
    global _TEMPLATE
    if _TEMPLATE is None:
        import cv2
        import numpy as np
        t = cv2.imread(os.path.join(os.path.dirname(__file__), "mat_template.png"), cv2.IMREAD_COLOR)
        if t is None or (t.shape[1], t.shape[0]) != TEMPLATE_PX:
            raise RuntimeError("mat_template.png missing or the wrong size")
        t = cv2.resize(t, PAGE_MM, interpolation=cv2.INTER_AREA)
        T = np.zeros((GRID[1], GRID[0], 3), np.float32)
        page = np.zeros((GRID[1], GRID[0]), bool)
        T[EXT_MM:EXT_MM + PAGE_MM[1], EXT_MM:EXT_MM + PAGE_MM[0]] = cv2.GaussianBlur(t.astype(np.float32), (3, 3), 0)
        page[EXT_MM:EXT_MM + PAGE_MM[1], EXT_MM:EXT_MM + PAGE_MM[0]] = True
        g = cv2.cvtColor(t, cv2.COLOR_BGR2GRAY)
        sharp = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 25
        sharp |= g < 60                                         # marker blacks
        sharp = cv2.dilate(sharp.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        trusted = np.zeros_like(page)
        trusted[EXT_MM:EXT_MM + PAGE_MM[1], EXT_MM:EXT_MM + PAGE_MM[0]] = ~sharp
        # the paper's own edge against the table
        trusted &= cv2.erode(page.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
        _TEMPLATE = (T, page, trusted)
    return _TEMPLATE


def _decode(img_bytes):
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)


def _detector():
    import cv2
    p = cv2.aruco.DetectorParameters()
    # The stored frames are 384 px thumbnails: the markers can be ~25 px across and
    # foreshortened. Looser search, sub-pixel corners.
    p.minMarkerPerimeterRate = 0.01
    p.adaptiveThreshWinSizeMin, p.adaptiveThreshWinSizeMax, p.adaptiveThreshWinSizeStep = 3, 53, 4
    p.polygonalApproxAccuracyRate = 0.05
    p.perspectiveRemovePixelPerCell = 8
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), p)


def _markers(img):
    """{id: 4x2 corners in px} for the mat's own markers (best of normal size and
    a 2x upscale, which finds small, tilted markers the first pass misses)."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    best = {}
    for up in (1, 2):
        g = gray if up == 1 else cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        corners, ids, _ = _detector().detectMarkers(g)
        found = {}
        if ids is not None:
            for c, i in zip(corners, ids.flatten()):
                i = int(i)
                if i not in MARKER_CORNERS_MM:
                    continue
                q = c[0] / up
                area = cv2.contourArea(q.astype(np.float32))
                if i not in found or area > found[i][1]:
                    found[i] = (q, area)
        if len(found) > len(best):
            best = found
        if len(best) == 4:
            break
    return {i: q for i, (q, _) in best.items()}


def _focal_from_h(H, w, h):
    """Focal length (px) implied by a plane homography, principal point at the
    centre and square pixels (Zhang's constraints). None when the view is too
    close to straight-on to say."""
    import numpy as np
    C = np.array([[1, 0, -w / 2], [0, 1, -h / 2], [0, 0, 1]], float)
    M = C @ H
    h1, h2 = M[:, 0], M[:, 1]
    ests = []
    d1 = h1[2] * h2[2]
    if abs(d1) > 1e-12:
        ests.append(-(h1[0] * h2[0] + h1[1] * h2[1]) / d1)
    d2 = h1[2] ** 2 - h2[2] ** 2
    if abs(d2) > 1e-12:
        ests.append(-(h1[0] ** 2 + h1[1] ** 2 - h2[0] ** 2 - h2[1] ** 2) / d2)
    ests = [math.sqrt(e) for e in ests if e > 0]
    lo, hi = 0.4 * max(w, h), 3.0 * max(w, h)
    ests = [e for e in ests if lo <= e <= hi]
    return statistics.median(ests) if ests else None


def _pose(src, dst, w, h, f):
    """Camera foot point G (page mm) and height Hc (mm) above the mat."""
    import cv2
    import numpy as np
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], float)
    obj = np.hstack([src, np.zeros((len(src), 1))]).astype(np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, dst.astype(np.float64), K, None, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    R, _ = cv2.Rodrigues(rvec)
    C = (-R.T @ tvec).ravel()
    # page x right, y down => z points into the table; the camera sits at z < 0
    return (float(C[0]), float(C[1])), float(-C[2])


def _frame(img_bytes):
    """Markers, homography and focal estimate for one picture, or a dict with
    ``error`` set."""
    import cv2
    import numpy as np
    img = _decode(img_bytes)
    if img is None:
        return {"error": "not an image"}
    h, w = img.shape[:2]
    mk = _markers(img)
    out = {"ids": sorted(mk), "image_px": (w, h), "img": img}
    if not mk:
        return {**out, "error": "no mat markers"}
    edge = [float(np.linalg.norm(c[i] - c[(i + 1) % 4])) for c in mk.values() for i in range(4)]
    out["marker_px"] = sum(edge) / len(edge)
    if len(mk) < MIN_MARKERS:
        return {**out, "error": f"only {len(mk)} of 4 markers visible"}
    src = np.array([p for i in sorted(mk) for p in MARKER_CORNERS_MM[i]], np.float32)
    dst = np.array([p for i in sorted(mk) for p in mk[i]], np.float32)
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None:
        return {**out, "error": "no homography"}
    out.update(H=H, src=src, dst=dst, f_est=_focal_from_h(H, w, h))
    return out


def _rectify(fr):
    """Pose, then the picture flattened onto the plane grid: ``rect`` (float BGR),
    ``cover`` (grid px the picture really shows)."""
    import cv2
    import numpy as np
    pose = _pose(fr["src"], fr["dst"], *fr["image_px"], fr["f"])
    if pose is None or pose[1] <= 20:
        fr["error"] = "camera pose not recovered"
        return fr
    fr["G"], fr["Hc"] = pose
    A = np.array([[1, 0, -EXT_MM], [0, 1, -EXT_MM], [0, 0, 1]], float)    # grid px -> mm
    M = fr["H"] @ A                                                       # grid px -> image px
    gw, gh = GRID
    fr["rect"] = cv2.warpPerspective(fr["img"], M, GRID, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                     borderValue=(0, 0, 0)).astype(np.float32)
    yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32)
    w3 = M[2, 0] * xx + M[2, 1] * yy + M[2, 2]
    u = (M[0, 0] * xx + M[0, 1] * yy + M[0, 2]) / np.where(w3 == 0, 1e-9, w3)
    v = (M[1, 0] * xx + M[1, 1] * yy + M[1, 2]) / np.where(w3 == 0, 1e-9, w3)
    iw, ih = fr["image_px"]
    cover = (w3 > 0) & (u >= 1) & (u < iw - 2) & (v >= 1) & (v < ih - 2)
    # very grazing plane points are smeared over many mm per pixel: leave them out
    dx, dy = xx - EXT_MM - fr["G"][0], yy - EXT_MM - fr["G"][1]
    cover &= np.hypot(dx, dy) < 5 * fr["Hc"]
    fr["cover"] = cv2.erode(cover.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    fr["rect"] = cv2.GaussianBlur(fr["rect"], (3, 3), 0)
    return fr


def _gain(fr):
    """Fit the frame's exposure / white balance / light fall-off on the page:
    picture ~ template * (a + b*x + c*y), per channel. Exposure acts as a gain in
    sRGB (a gamma curve turns k*linear into k^(1/2.2)*sRGB); there is no offset
    term, which would let the fit explain the white margin and the dark print and
    throw the grey placement area out as "object". Sets ``gain`` (grid x 3)."""
    import numpy as np
    T, page, trusted = _template()
    rect, cover = fr["rect"], fr["cover"]
    fit = trusted & cover
    if fit.sum() < 1500:
        fr["error"] = "too little of the mat visible"
        return fr
    gh, gw = GRID[1], GRID[0]
    yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32)
    # the gradient is fitted over the page and held flat beyond it
    xn = (np.clip(xx, EXT_MM, EXT_MM + PAGE_MM[0]) - EXT_MM) / PAGE_MM[0] - 0.5
    yn = (np.clip(yy, EXT_MM, EXT_MM + PAGE_MM[1]) - EXT_MM) / PAGE_MM[1] - 0.5
    bright = fit & (T.min(2) > 60)
    # Start from the paper right around the markers that were found: a marker
    # that was read isn't covered, so the paper beside it is mat. (A big piece
    # and its shadow can cover most of the rest of the readable page.)
    ring = np.zeros_like(fit)
    for i in fr["ids"]:
        x0, y0 = MARKER_CORNERS_MM[i][0]
        a, b = int(_px(x0)), int(_px(y0))
        mm = int(MARKER_MM)
        ring[b - 12:b + mm + 12, a - 12:a + mm + 12] = True
        ring[b - 3:b + mm + 3, a - 3:a + mm + 3] = False
    ring &= bright
    src = ring if ring.sum() > 80 else bright
    lr = np.log(np.maximum(rect[src], 1.0) / T[src])
    g0 = np.exp(np.median(lr, axis=0))
    gain = np.broadcast_to(g0.astype(np.float32), rect.shape).copy()
    for _ in range(3):
        res = np.linalg.norm(rect - T * gain, axis=2)
        # noise measured where we know it is mat, so a piece covering half the
        # page can't widen the inlier band and drag the fit onto itself
        r = res[src]
        sigma = 1.4826 * float(np.median(np.abs(r - np.median(r)))) + 1e-3
        use = bright & (res < max(15.0, 3 * sigma))
        if use.sum() < 500:
            break
        for ch in range(3):
            t = T[..., ch][use]
            A = np.stack([t, t * xn[use], t * yn[use]], 1)
            coef, *_ = np.linalg.lstsq(A, rect[..., ch][use], rcond=None)
            gain[..., ch] = coef[0] + coef[1] * xn + coef[2] * yn
    fr["gain"] = np.maximum(gain, 1e-3)
    r = np.linalg.norm(rect - T * fr["gain"], axis=2)[src]
    fr["sigma"] = 1.4826 * float(np.median(np.abs(r - np.median(r)))) + 1e-3
    return fr


def _segment(fr, table):
    """Object / known masks for one frame on the grid. ``table`` is the session's
    model of the table off the page (float BGR, in template units) and where it
    is defined."""
    import cv2
    import numpy as np
    T, page, trusted = _template()
    rect, cover, gain = fr["rect"], fr["cover"], fr["gain"]
    tab, tab_ok, tab_noise = table
    expect = np.where(page[..., None], T, tab)
    ok_bg = (trusted | tab_ok) & cover
    pred = expect * gain
    res = np.linalg.norm(rect - pred, axis=2)
    obj = np.zeros(res.shape, bool)
    m = trusted & cover                      # on the page: the print's noise level, as
    obj |= m & (res > max(30.0, 4 * fr["sigma"]))   # measured on paper known to be clear
    m = tab_ok & cover                       # on the table: each pixel's own
    on_tab = (m & (res > np.maximum(25.0, 4.5 * tab_noise))).astype(np.uint8)
    # wood grain and the paper's edge leave specks and thin lines: open them away
    obj |= cv2.morphologyEx(on_tab, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)) > 0
    s_img = rect.sum(2) + 1e-3
    s_pred = np.maximum(pred.sum(2), 1e-3)
    k = s_img / s_pred
    chroma = np.abs(rect / s_img[..., None] - pred / s_pred[..., None]).sum(2)
    obj &= ~((k > 0.45) & (k < 0.93) & (chroma < 0.04))      # a soft shadow is background
    # fill the print edges and the paper's edge from their neighbours
    num = cv2.boxFilter(obj.astype(np.float32), -1, (11, 11), normalize=False)
    den = cv2.boxFilter(ok_bg.astype(np.float32), -1, (11, 11), normalize=False)
    fill = ~ok_bg & cover & (num > 0.5 * np.maximum(den, 1)) & (den > 0)
    obj = (obj | fill).astype(np.uint8)
    obj = cv2.morphologyEx(obj, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    obj = cv2.morphologyEx(obj, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    # a 384 px JPEG blurs the piece's edge outwards by about a millimetre on the
    # plane; take it back so widths aren't padded
    obj = cv2.erode(obj, np.ones((3, 3), np.uint8)) > 0
    fr["obj"] = obj & cover
    fr["known"] = cover & (ok_bg | fr["obj"])
    return fr


def _table_model(frames, hide=None):
    """See _table_model_once; with ``hide`` (per-frame masks of where the piece
    may stand in front of the table), those frames are left out of each pixel's
    median, falling back to all frames where hiding leaves fewer than three."""
    base = _table_model_once(frames)
    if hide is None:
        return base
    import numpy as np
    med, ok, noise = _table_model_once(frames, hide)
    use = ok[..., None]
    return (np.where(use, med, base[0]), ok | base[1], np.where(ok, noise, base[2]))


def _table_model_once(frames, hide=None):
    """The table off the page, from the session itself: per plane pixel, the
    median across frames of the picture divided by the frame's fitted gain
    (template units), and how much it normally varies there (the median of the
    frames' deviations from it, so a piece covering it in a minority of frames
    doesn't count). Defined where at least three frames see it, away from the
    paper's edge (which shifts a mm or two with each frame's pose)."""
    import cv2
    import numpy as np
    import warnings
    _, page, _ = _template()
    gh, gw = GRID[1], GRID[0]
    empty = (np.zeros((gh, gw, 3), np.float32), np.zeros((gh, gw), bool), np.zeros((gh, gw), np.float32))
    if len(frames) < 3:
        return empty
    near_page = cv2.dilate(page.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
    masks = [fr["cover"] & ~near_page for fr in frames]
    if hide is not None:                       # leave out where the piece may stand in front
        masks = [m & ~h for m, h in zip(masks, hide)]
    cnt = sum(m.astype(np.int16) for m in masks)
    ok = cnt >= 3
    med = np.zeros((gh, gw, 3), np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for ch in range(3):
            st = np.full((len(frames), gh, gw), np.nan, np.float32)
            for i, (fr, m) in enumerate(zip(frames, masks)):
                st[i][m] = fr["rect"][..., ch][m] / fr["gain"][..., ch][m]
            med[..., ch] = np.nan_to_num(np.nanmedian(st, axis=0))
        st = np.full((len(frames), gh, gw), np.nan, np.float32)
        for i, (fr, m) in enumerate(zip(frames, masks)):
            g = fr["gain"].mean(2)
            d = np.linalg.norm(fr["rect"] / fr["gain"] - med, axis=2) * g
            st[i][m] = d[m]
        noise = np.nan_to_num(np.nanmedian(st, axis=0))
    return med, ok, noise.astype(np.float32)


def _shadow_of_piece(frames, centre, radius, height):
    """Per frame, the plane area a vertical cylinder (centre mm, radius, height)
    hides from the camera: the hull of its base and top as seen on the plane."""
    import cv2
    import numpy as np
    t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    ring = np.stack([np.cos(t), np.sin(t)], 1) * radius + centre
    out = []
    for fr in frames:
        G = np.asarray(fr["G"])
        z = min(height, fr["Hc"] - 10)
        top = G + (ring - G) * (fr["Hc"] / (fr["Hc"] - z))
        pts = np.vstack([ring, top]) + EXT_MM
        m = np.zeros((GRID[1], GRID[0]), np.uint8)
        cv2.fillConvexPoly(m, cv2.convexHull(pts.astype(np.float32)).astype(np.int32), 1)
        out.append(m > 0)
    return out


def _footprint(frames):
    """Plane pixels that are object in (nearly) every frame that sees them: the
    piece stands there. The component in (or nearest) the grey area."""
    import cv2
    import numpy as np
    hits = sum(fr["obj"].astype(np.int16) for fr in frames)
    seen = sum(fr["known"].astype(np.int16) for fr in frames)
    n = len(frames)
    need = np.maximum(min(2, n), np.ceil(0.75 * seen))
    vote = (seen >= min(2, n)) & (hits >= need)
    vote = cv2.morphologyEx(vote.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    nl, lbl, stats, cent = cv2.connectedComponentsWithStats(vote, 8)
    if nl < 2:
        return None
    x0, y0, x1, y1 = (_px(v) for v in PLACE_MM)
    best, score = None, 0
    for k in range(1, nl):
        cx, cy = cent[k]
        s = stats[k, cv2.CC_STAT_AREA] * (1.0 if (x0 <= cx <= x1 and y0 <= cy <= y1) else 0.2)
        if s > score:
            best, score = k, s
    fp = lbl == best
    return fp if fp.sum() >= _MIN_FOOTPRINT_MM2 else None


def _keep_attached(frames, fp):
    """Drop each frame's blobs that don't touch the piece (print noise, a hand,
    something on the table far away)."""
    import cv2
    import numpy as np
    seed = cv2.dilate(fp.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    for fr in frames:
        # bridge small gaps (the unread band along the paper's edge, a marker)
        # before deciding what belongs to the piece
        joined = cv2.dilate(fr["obj"].astype(np.uint8), np.ones((11, 11), np.uint8))
        nl, lbl = cv2.connectedComponents(joined, connectivity=8)
        keep = np.unique(lbl[seed & (joined > 0)])
        keep = keep[keep > 0]
        fr["obj"] &= np.isin(lbl, keep)


def _probe(frames, X, z):
    """(misses, hits, hits from side views) per plane point X (mm, n x 2) at
    height z."""
    import numpy as np
    gw, gh = GRID
    miss = np.zeros(len(X), np.int16)
    hit = np.zeros(len(X), np.int16)
    side = np.zeros(len(X), np.int16)
    for fr in frames:
        if fr["Hc"] - z <= 5:
            continue
        G = np.asarray(fr["G"])
        Q = G + (X - G) * (fr["Hc"] / (fr["Hc"] - z))
        qx = np.round(_px(Q[:, 0])).astype(int)
        qy = np.round(_px(Q[:, 1])).astype(int)
        inb = (qx >= 0) & (qx < gw) & (qy >= 0) & (qy < gh)
        known = np.zeros(len(X), bool)
        inside = np.zeros(len(X), bool)
        known[inb] = fr["known"][qy[inb], qx[inb]]
        inside[inb] = fr["obj"][qy[inb], qx[inb]]
        miss += known & ~inside
        hit += known & inside
        if fr.get("elev", 90) <= SIDE_VIEW_MAX_ELEV:
            side += known & inside
    return miss, hit, side


def _carve(fp, frames):
    """Visual-hull carve around the footprint. Returns (height_mm or None,
    grid of the highest surviving z per column, side-view frame count)."""
    import cv2
    import numpy as np
    fy, fx = np.nonzero(fp)
    c = np.array([fx.mean(), fy.mean()]) - EXT_MM
    n_side = 0
    for fr in frames:
        d = math.hypot(fr["G"][0] - c[0], fr["G"][1] - c[1])
        fr["elev"] = math.degrees(math.atan2(fr["Hc"], max(d, 1e-6)))
        n_side += fr["elev"] <= SIDE_VIEW_MAX_ELEV
    ext = max(np.ptp(fx), np.ptp(fy)) + 1
    r = int(0.6 * ext + 25)
    cand = cv2.dilate(fp.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2)) > 0
    ys, xs = np.nonzero(cand)
    X = np.stack([xs - EXT_MM, ys - EXT_MM], 1).astype(np.float64)
    n = len(frames)
    # one disagreeing frame in twenty may be a segmentation slip; fewer allowed
    # misses over-carve less than they under-carve (a fat hull is the usual error)
    tol = n // 20
    need = max(min(2, n), math.ceil(0.4 * n))
    top = np.zeros(len(X))
    zmax = min(MAX_HEIGHT_MM, 0.85 * min(fr["Hc"] for fr in frames))
    last, capped = 0.0, False
    # Each column is one solid run: it may start above the mat (under a bulge)
    # but once it has ended it stays ended, so a spot only steep views can see
    # doesn't come back as a floating "phantom" above the piece.
    state = np.zeros(len(X), np.int8)            # 0 not started, 1 in the piece, 2 ended
    gap = np.zeros(len(X), np.int16)
    for z in np.arange(1.0, zmax, 1.0):
        live = np.nonzero(state < 2)[0]
        if not len(live):
            break
        miss, hit, side = _probe(frames, X[live], z)
        # steep views keep almost any point above the piece "inside"; a side view
        # that can see the spot has to agree
        ok = (miss <= tol) & (hit >= need) & ((side >= 1) if n_side else True)
        on, off = live[ok], live[~ok]
        state[on] = 1
        gap[on] = 0
        top[on] = z
        running = off[state[off] == 1]
        gap[running] += 1
        state[running[gap[running] > 2]] = 2
        if len(on):
            last = z
        elif z - last > 10:
            break
    else:
        capped = last >= zmax - 2
    grid = np.zeros(fp.shape)
    grid[ys, xs] = top
    if not top.any() or capped or not n_side:
        return None, grid, n_side
    k = max(1, int(0.003 * np.count_nonzero(top)))        # ignore the very highest few columns
    h = float(np.sort(top)[-k])
    # Did the frames actually see past the top, or just run out of views of it?
    sel = top >= h - 1
    miss, _, _ = _probe(frames, X[sel], h + 3)
    if float((miss > tol).mean()) < 0.5:
        return None, grid, n_side
    return h, grid, n_side


def _height_range(h, base, top, frames):
    """[low, high] mm the height can honestly be said to lie in. A visual hull is
    never smaller than the piece: a flat top seen only from steep angles leaves a
    cone-shaped cap on it, (Hc - h) * R / (d + R) tall for the shallowest view
    that saw clear past the top (R the piece's radius, d its distance from the
    camera's foot point). A rounded top leaves almost none, so the reported
    height is the hull's and the range says how much lower a flat top could be."""
    import numpy as np
    ys, xs = np.nonzero(base)
    c = np.array([xs.mean(), ys.mean()]) - EXT_MM
    R = 0.5 * max(np.ptp(xs) + 1, np.ptp(ys) + 1)
    ty, tx = np.nonzero(top >= h - 1)
    X = np.stack([tx - EXT_MM, ty - EXT_MM], 1).astype(np.float64)
    caps = []
    for fr in frames:
        if fr.get("elev", 90) > SIDE_VIEW_MAX_ELEV or not len(X):
            continue
        miss, _, _ = _probe([fr], X, h + 3)
        if miss.mean() >= 0.5:                      # this view saw past the top
            d = math.hypot(fr["G"][0] - c[0], fr["G"][1] - c[1])
            caps.append((fr["Hc"] - h) * R / (d + R))
    cap = min(caps) if caps else 0.25 * h
    return [round(max(h - cap, 0)), round(h)]


def _rect_mm(mask):
    import cv2
    import numpy as np
    pts = np.column_stack(np.nonzero(mask)[::-1]).astype(np.float32)
    if len(pts) < 5:
        return None
    (_, _), (a, b), _ = cv2.minAreaRect(pts)
    a, b = a + 1, b + 1                                     # 1 px = 1 mm
    return max(a, b), min(a, b)


def _prepare(frames_bytes):
    """Decode, find the mat, recover poses and segment every frame. Returns
    (frames ready to measure, reason if too few)."""
    raw = []
    for b in frames_bytes:
        try:
            fr = _frame(b)
        except Exception as ex:  # noqa: BLE001
            fr = {"error": str(ex)[:120]}
        if "error" not in fr:
            raw.append(fr)
    if not raw:
        return [], "mat not detected"
    ests = [fr["f_est"] for fr in raw if fr.get("f_est")]
    w, h = raw[0]["image_px"]
    f = statistics.median(ests) if ests else 0.8 * max(w, h)    # ~64 deg across, a typical phone
    good = []
    for fr in raw:
        fr["f"] = f
        try:
            fr = _rectify(fr)
            if "error" not in fr:
                fr = _gain(fr)
        except Exception as ex:  # noqa: BLE001
            fr["error"] = str(ex)[:120]
        if "error" not in fr:
            good.append(fr)
    if not good:
        return [], "mat not readable"
    table = _table_model(good)
    for fr in good:
        _segment(fr, table)
    return good, None


def measure_frames(frames: list[bytes]) -> dict:
    """Combine a session's pictures into W x D x H (mm)."""
    if not available():
        return {"ok": False, "reason": "OpenCV not installed"}
    import cv2
    import numpy as np
    good, why = _prepare(frames)
    if len(good) < 2:
        return {"ok": False, "reason": f"{why or 'mat not detected'} in enough frames",
                "frames_measured": len(good)}
    fp = _footprint(good)
    if fp is None:
        return {"ok": False, "reason": "no piece found on the mat (or it moved between pictures)",
                "frames_measured": len(good)}
    _keep_attached(good, fp)
    height, top, n_side = _carve(fp, good)
    # More passes. For a big piece the same patch of table is behind it in most
    # of the frames that see it, so the session's median "table" there is the
    # piece. Rebuild the table leaving out, per frame, what a cylinder a little
    # bigger than the last estimate would hide, then segment and carve again,
    # until the height settles.
    for _ in range(3):
        if len(good) < 3:
            break
        hold = top >= 3.0
        ys, xs = np.nonzero(hold if hold.sum() > fp.sum() else fp)
        centre = np.array([xs.mean(), ys.mean()]) - EXT_MM
        radius = 0.5 * max(np.ptp(xs), np.ptp(ys)) + 3
        was = height if height else float(top.max())
        hide = _shadow_of_piece(good, centre, radius, 1.1 * max(was, 10.0) + 5)
        table = _table_model(good, hide)
        for fr in good:
            _segment(fr, table)
        fp2 = _footprint(good)
        if fp2 is None:
            break
        fp = fp2
        _keep_attached(good, fp)
        height, top, n_side = _carve(fp, good)
        now = height if height else float(top.max())
        if abs(now - was) < 2:
            break
    base = fp
    # columns that really hold the piece, not a flat shadow edge or a smudge
    tall = (top >= max(3.0, 0.15 * height if height else 3.0)).astype(np.uint8)
    if tall.any():
        nl, lbl, stats, _ = cv2.connectedComponentsWithStats(tall | fp.astype(np.uint8), 8)
        k = int(np.argmax(stats[1:, cv2.CC_STAT_AREA])) + 1
        hull = (lbl == k) & (tall > 0)
        if hull.sum() >= 0.5 * fp.sum():
            base = hull
    wd = _rect_mm(base)
    if wd is None:
        return {"ok": False, "reason": "footprint too small to measure", "frames_measured": len(good)}
    width, depth = round(wd[0]), round(wd[1])
    out = {"ok": True, "frames_measured": len(good), "frames_for_height": n_side,
           "width_mm": width, "depth_mm": depth,
           "height_mm": round(height) if height else None,
           "focal_px": round(good[0]["f"], 1)}
    if height:
        out["height_range_mm"] = _height_range(height, base, top, good)
        out["dimensions"] = f"{width} × {depth} × {round(height)} mm (W×D×H)"
    else:
        out["dimensions"] = f"{width} × {depth} mm (W×D); height not measured"
        out["note"] = ("no picture from the side" if not n_side else
                       "the side views didn't see clear of the top of the piece") + \
                      ": circle it once low, with the whole mat and some table in frame"
    return out


def _measure_one(img_bytes: bytes):
    """(width_mm, depth_mm) of what one picture shows standing on the mat, as
    projected onto it (a straight-down view gives the footprint), or None."""
    good, _ = _prepare([img_bytes])
    if not good:
        return None
    fp = _footprint(good)
    return _rect_mm(fp) if fp is not None else None


def mat_details(img_bytes: bytes) -> dict:
    """What the mat contributes in one picture, spelled out: which markers were seen,
    the scale they give, the camera's position, and the piece's outline on the mat
    when enough of the mat is visible. For capture-session manifests and the bench."""
    import numpy as np
    out = {"markers": 0, "ids": [], "mm_per_px": None, "marker_px": None, "image_px": None,
           "object_mm": None, "elevation_deg": None, "camera_height_mm": None}
    fr = _frame(img_bytes)
    if fr.get("error") == "not an image":
        out["error"] = "not an image"
        return out
    w, h = fr["image_px"]
    out["image_px"] = {"w": int(w), "h": int(h)}
    out["markers"] = len(fr.get("ids", []))
    out["ids"] = fr.get("ids", [])
    if fr.get("marker_px"):
        out["marker_px"] = round(fr["marker_px"], 1)
        out["mm_per_px"] = round(MARKER_MM / fr["marker_px"], 4)
    if "error" in fr:
        return out
    good, _ = _prepare([img_bytes])
    if not good:
        return out
    g = good[0]
    out["camera_height_mm"] = round(g["Hc"])
    fp = _footprint(good)
    if fp is not None:
        ys, xs = np.nonzero(fp)
        c = (xs.mean() - EXT_MM, ys.mean() - EXT_MM)
        d = math.hypot(g["G"][0] - c[0], g["G"][1] - c[1])
        out["elevation_deg"] = round(math.degrees(math.atan2(g["Hc"], d)), 1)
        r = _rect_mm(fp)
        if r:
            out["object_mm"] = {"w": round(r[0], 1), "h": round(r[1], 1)}
    return out


def measure_zip(zip_bytes: bytes, max_frames: int = 24) -> dict:
    """Convenience: pull frame images out of an enroll .zip and measure them.
    Frames are sampled evenly across the session so the orbit's side views are in."""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            names = sorted(n for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png")))
            if len(names) > max_frames:
                step = len(names) / max_frames
                names = [names[int(i * step)] for i in range(max_frames)]
            frames = [z.read(n) for n in names]
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "reason": str(ex)}
    return measure_frames(frames)
