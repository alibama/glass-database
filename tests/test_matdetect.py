"""ArUco capture-mat measurement."""
import io
import zipfile

import pytest

matdetect = pytest.importorskip("glowtbook.matdetect")
pytestmark = pytest.mark.skipif(not matdetect.available(), reason="opencv/aruco not installed")


def _frame(ow, oh, ppm=2.4):
    import cv2
    import numpy as np
    ad = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    W, H = int(216 * ppm), int(279 * ppm)
    img = np.full((H, W, 3), 245, np.uint8)
    for mid, cx, cy in [(0, 30, 40), (1, 186, 40), (2, 30, 200), (3, 186, 200)]:
        m = cv2.cvtColor(cv2.aruco.generateImageMarker(ad, mid, int(30 * ppm)), cv2.COLOR_GRAY2BGR)
        x, y = int((cx - 15) * ppm), int((cy - 15) * ppm)
        img[y:y + m.shape[0], x:x + m.shape[1]] = m
    cv2.rectangle(img, (int(50 * ppm), int(60 * ppm)), (int(166 * ppm), int(180 * ppm)), (128, 128, 128), -1)
    cv2.rectangle(img, (int((108 - ow / 2) * ppm), int((120 - oh / 2) * ppm)),
                  (int((108 + ow / 2) * ppm), int((120 + oh / 2) * ppm)), (30, 60, 160), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_measures_dimensions_within_tolerance():
    frames = [_frame(w, 90) for w in (40, 33, 25, 28, 40)]   # orbit a 40x25 footprint, 90 tall
    r = matdetect.measure_frames(frames)
    assert r["ok"]
    assert abs(r["height_mm"] - 90) <= 6
    assert abs(r["width_mm"] - 40) <= 6
    assert abs(r["depth_mm"] - 25) <= 6


def test_no_mat_is_graceful():
    import cv2
    import numpy as np
    blank = cv2.imencode(".jpg", np.full((300, 300, 3), 200, np.uint8))[1].tobytes()
    r = matdetect.measure_frames([blank, blank])
    assert r["ok"] is False


def test_measure_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, w in enumerate((40, 30, 25, 40)):
            z.writestr(f"frames/{i:03d}.jpg", _frame(w, 90))
        z.writestr("fingerprint.json", b"{}")
    r = matdetect.measure_zip(buf.getvalue())
    assert r["ok"] and "height_mm" in r
