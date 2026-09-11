"""Studio open-data source registry: SSRF guard, moderation, aggregation."""
from central import studio_sources as S
from central.dbconn import connect


def test_url_guard():
    assert S.url_ok("http://x.com")[0] is False       # https only
    assert S.url_ok("https://localhost")[0] is False   # internal
    assert S.url_ok("https://glassdatabase.org")[0] is True


def test_add_is_pending_and_moderated(demo_db):
    c = connect()
    sid, err = S.add(c, "Test Studio", "https://glassdatabase.org", lat="38", lng="-78")
    assert sid and not err
    assert len(S.list_sources(c, "pending")) >= 1
    assert not any(s["id"] == sid for s in S.list_sources(c, "approved"))   # not polled yet
    S.set_status(c, sid, "approved")
    assert any(s["id"] == sid for s in S.list_sources(c, "approved"))


def test_bad_url_rejected(demo_db):
    c = connect()
    sid, err = S.add(c, "Bad", "http://internal")
    assert sid is None and err
