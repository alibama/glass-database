"""Privacy-first analytics: logging, aggregation, and that no IP is stored."""
from central import analytics as A
from central.dbconn import connect


def test_log_and_summary(demo_db):
    c = connect()
    for _ in range(3):
        A.log(c, "explore", "Datasets", "view", ip="1.2.3.4")
    A.log(c, "explore", "Community", "view", ip="1.2.3.4")
    A.log(c, "glowtbook", "", "view", ip="9.9.9.9")
    A.log(c, "intake", "artist", "submit:artist")
    s = A.summary(c, 30)
    assert s["views"] == 5 and s["visitors"] == 2
    assert dict((r[0], r[1]) for r in s["by_surface"])["explore"] == 4
    assert any(str(e).startswith("submit") for e, _ in s["by_event"])


def test_no_ip_is_stored(demo_db):
    c = connect()
    A.log(c, "explore", "Datasets", "view", ip="203.0.113.7")
    cols = {r[1] for r in c.execute("PRAGMA table_info(analytics_events)")}
    assert "ip" not in cols
    sess = c.execute("SELECT session FROM analytics_events WHERE session!='' LIMIT 1").fetchone()[0]
    assert "203.0.113.7" not in sess and len(sess) == 16   # a hash, not the IP


def test_daily_session_rotates_and_empty_ip():
    assert A.daily_session("") == ""
    assert A.daily_session("1.1.1.1") == A.daily_session("1.1.1.1")   # stable within a day
    assert A.country_for("1.1.1.1") == ""   # no GeoIP DB configured


def test_printed_qr_code_counts_and_redirects(demo_db):
    from fastapi.testclient import TestClient

    from api.main import app
    c = TestClient(app)
    c.cookies.clear()
    conn = connect(); conn.execute("DELETE FROM analytics_events WHERE surface='qr'"); conn.commit()
    r = c.get("/go/mat", follow_redirects=False, headers={"X-Forwarded-For": "198.51.100.4"})
    assert r.status_code == 302 and r.headers["location"] == A.QR_CODES["mat"]["target"]
    assert r.headers["location"].startswith("/fingerprint/verify.html")
    c.get("/go/MAT", follow_redirects=False, headers={"X-Forwarded-For": "198.51.100.4"})   # same person, same day
    c.get("/go/mat", follow_redirects=False, headers={"X-Forwarded-For": "198.51.100.9", "DNT": "1"})
    assert c.get("/go/elsewhere", follow_redirects=False).status_code == 404               # not an open redirect
    st = {q["code"]: q for q in A.qr_stats(connect(), 30)}["mat"]
    assert st["scans"] == 3 and st["visitors"] == 1 and st["all_time"] == 3 and st["by_day"][0]["scans"] == 3
    cols = {r[1] for r in connect().execute("PRAGMA table_info(analytics_events)")}
    assert "ip" not in cols


def test_mat_back_sheet_has_a_working_qr(tmp_path):
    import pytest
    cv2 = pytest.importorskip("cv2")
    import shutil

    from scripts import make_mat_back as M
    shutil.copy(M.OUT / "capture-mat.pdf", tmp_path / "capture-mat.pdf")
    assert M.main(["--out", str(tmp_path)]) == 0
    img = cv2.imread(str(tmp_path / "capture-mat-back.png"))
    assert img.shape[:2] == (3300, 2550)                                  # Letter at 300 dpi
    assert cv2.QRCodeDetector().detectAndDecode(cv2.resize(img, (850, 1100)))[0] == "https://glassdatabase.org/api/go/mat"
    import re
    pages = re.findall(rb"/Type\s*/Page(?!s)", (tmp_path / "capture-mat-2sided.pdf").read_bytes())
    assert len(pages) == 2                                                # front + back
    assert M.main(["--code", "nope", "--out", str(tmp_path)]) == 2
