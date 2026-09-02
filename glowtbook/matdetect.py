"""
glowtbook.matdetect
===================
Turn capture frames shot on the ArUco reference mat into real measurements.

The mat carries four ArUco markers (DICT_4X4_50, ids 0–3, 30 mm each). A detected
marker's own 30 mm edge gives a local mm-per-pixel scale, so we can measure the
piece in millimetres without any inter-marker geometry. Orbit frames give
several horizontal extents: the tallest is height, the widest is the object's
long axis, the narrowest a perpendicular (depth) estimate.

Runs server-side on import (OpenCV). Degrades to "no measurement" if OpenCV isn't
installed or the mat isn't visible — capture still works, you just don't get
dimensions.
"""
from __future__ import annotations

import io
import statistics


def available() -> bool:
    try:
        import cv2  # noqa: F401
        return hasattr(__import__("cv2"), "aruco")
    except Exception:
        return False

MARKER_MM = 30.0
_MIN_OBJECT_FRAC = 0.02   # ignore specks smaller than 2% of the mat region


def _measure_one(img_bytes: bytes):
    """Return (width_mm, height_mm) for one frame, or None if the mat/object
    can't be measured."""
    import cv2
    import numpy as np
    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ad = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    det = cv2.aruco.ArucoDetector(ad, cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None or len(ids) < 3:               # need most of the mat visible
        return None
    # local scale: average marker edge length -> mm/px
    edges = []
    for c in corners:
        p = c[0]
        edges += [float(np.linalg.norm(p[i] - p[(i + 1) % 4])) for i in range(4)]
    mm_px = MARKER_MM / (sum(edges) / len(edges))

    allpts = np.concatenate([c[0] for c in corners])
    x0, y0 = allpts.min(0); x1, y1 = allpts.max(0)
    x0, y0, x1, y1 = int(max(x0, 0)), int(max(y0, 0)), int(min(x1, img.shape[1])), int(min(y1, img.shape[0]))
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    # mask out the marker quads (dark corners) so they aren't measured as object
    marker_mask = np.zeros(roi.shape[:2], np.uint8)
    for c in corners:
        cv2.fillConvexPoly(marker_mask, (c[0] - [x0, y0]).astype(np.int32), 255)
    # The mat has two known background surfaces: the white margin (~245) and the
    # mid-gray placement area (~128). The object is whatever differs from BOTH.
    b = roi.astype(np.int16)
    d_white = np.linalg.norm(b - 245, axis=2)
    d_gray = np.linalg.norm(b - 128, axis=2)
    obj = ((d_white > 45) & (d_gray > 45) & (marker_mask == 0)).astype(np.uint8)
    obj = cv2.morphologyEx(obj, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    obj = cv2.morphologyEx(obj, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(obj, 8)
    if n < 2:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    k = int(areas.argmax()) + 1
    if stats[k, cv2.CC_STAT_AREA] < _MIN_OBJECT_FRAC * roi.shape[0] * roi.shape[1]:
        return None
    w = stats[k, cv2.CC_STAT_WIDTH] * mm_px
    h = stats[k, cv2.CC_STAT_HEIGHT] * mm_px
    return (float(w), float(h))


def measure_frames(frames: list[bytes]) -> dict:
    """Aggregate per-frame measurements into a dimension estimate (mm)."""
    if not available():
        return {"ok": False, "reason": "OpenCV not installed"}
    ws, hs = [], []
    detected = 0
    for b in frames:
        try:
            m = _measure_one(b)
        except Exception:
            m = None
        if m:
            detected += 1
            ws.append(m[0]); hs.append(m[1])
    if detected < 2:
        return {"ok": False, "reason": "mat not detected in enough frames", "frames_measured": detected}
    height = round(statistics.median(hs))
    width = round(max(ws))
    depth = round(min(ws))
    return {"ok": True, "frames_measured": detected,
            "height_mm": height, "width_mm": width, "depth_mm": depth,
            "dimensions": f"{width} × {depth} × {height} mm (W×D×H)"}


def measure_zip(zip_bytes: bytes, max_frames: int = 24) -> dict:
    """Convenience: pull frame images out of an enroll .zip and measure them."""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png"))]
            frames = [z.read(n) for n in names[:max_frames]]
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "reason": str(ex)}
    return measure_frames(frames)
