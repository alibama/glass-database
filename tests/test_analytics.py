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
