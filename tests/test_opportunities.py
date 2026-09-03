"""Artist opportunities: intake, approval gate, ICS calendar, Google links."""
from fastapi.testclient import TestClient

from central import approvals
from central import opportunities as O
from central.dbconn import connect


def _submit(c, **kw):
    base = {"title": "Open Call", "organization": "Org", "opp_type": "Open call",
            "url": "https://x.org", "location": "Online", "deadline": "2026-11-15",
            "description": "Desc, with, commas;", "contact_email": "p@x.org", "submitted_by": "me"}
    base.update(kw)
    return O.submit(c, base)


def test_intake_is_pending_until_approved(demo_db):
    c = connect(); O.ensure_opportunities(c)
    rid = _submit(c)
    assert O.approved(c) == []                       # pending -> not public
    approvals.set_status(c, O.TABLE, [rid], "approved")
    appr = O.approved(c)
    assert len(appr) == 1 and appr[0]["title"] == "Open Call"
    assert "contact_email" not in appr[0]            # private column withheld


def test_ics_is_valid_and_escaped(demo_db):
    c = connect(); O.ensure_opportunities(c)
    rid = _submit(c, title="Show 2026")
    approvals.set_status(c, O.TABLE, [rid], "approved")
    ics = O.build_ics(O.approved(c))
    assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT" in ics and "DTSTART;VALUE=DATE:20261115" in ics
    assert "\\," in ics and "\\;" in ics             # commas/semicolons escaped
    assert "\r\n" in ics                             # CRLF line endings


def test_gcal_link_and_days_until():
    row = {"title": "Grant", "opp_type": "Grant / funding", "deadline": "2026-11-15",
           "url": "https://x.org", "location": "Online"}
    link = O.gcal_link(row)
    assert link.startswith("https://calendar.google.com/calendar/render?")
    assert "dates=20261115" in link
    assert O.days_until("2000-01-01") < 0            # past
    assert O.days_until("bad-date") is None


def test_ics_endpoint(demo_db):
    c = connect(); O.ensure_opportunities(c)
    rid = _submit(c)
    approvals.set_status(c, O.TABLE, [rid], "approved")
    from api.main import app
    r = TestClient(app).get("/opportunities.ics")
    assert r.status_code == 200 and "text/calendar" in r.headers["content-type"]
    assert "BEGIN:VEVENT" in r.text


def test_month_grid_html(demo_db):
    rows = [{"_row_id": "a", "title": "Open Call", "opp_type": "Open call",
             "url": "https://x", "deadline": "2026-11-15", "organization": "Org"}]
    assert O.initial_month(rows) == (2026, 11)
    h = O.month_grid_html(rows, 2026, 11)
    assert "November 2026" in h and 'scope="col"' in h
    assert ">15<" in h and "Open Call" in h
    # HTML-escapes chip content
    rows[0]["title"] = "<b>x</b>"
    assert "<b>x</b>" not in O.month_grid_html(rows, 2026, 11)
