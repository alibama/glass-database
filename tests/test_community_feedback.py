"""Feedback, settings-backed Discord config, and the new exchange/job/resource forms."""
from central import approvals, feedback, intake, settings
from central.dbconn import connect


def test_settings_store(demo_db):
    c = connect()
    assert settings.get(c, "nope", "d") == "d"
    settings.set(c, "discord_webhook_url", "https://hook")
    assert settings.get(c, "discord_webhook_url") == "https://hook"


def test_feedback_stored_and_resolvable(demo_db):
    c = connect()
    fid = feedback.submit(c, "The map is slow on mobile", email="p@x.org", page="Datasets")
    items = feedback.open_items(c)
    assert any(i["id"] == fid and i["resolved"] == 0 for i in items)
    feedback.resolve(c, fid)
    assert all(i["resolved"] == 1 for i in feedback.open_items(c) if i["id"] == fid)
    # feedback is NOT a public dataset
    assert not c.execute("SELECT 1 FROM _datasets WHERE tbl='feedback'").fetchone()


def test_exchange_will_accept_trade(demo_db):
    c = connect()
    rid = intake.submit(c, "exchange", {
        "listing_type": "Want to sell (WTS)", "title": "Kiln, barely used",
        "description": "Skutt, works great", "price": "$400", "will_accept_trade": True,
        "trade_notes": "would take a torch", "email": "s@x.org"}, base_url="")
    row = dict(zip([d[0] for d in c.execute("SELECT * FROM exchange_submissions LIMIT 0").description],
                   c.execute("SELECT * FROM exchange_submissions WHERE _row_id=?", (rid,)).fetchone()))
    assert row["will_accept_trade"] == "yes" and row["listing_type"].startswith("Want to sell")
    assert approvals.counts(c, "exchange_submissions")["approved"] == 0  # pending


def test_job_and_resource_forms_exist():
    for key in ("job", "resource", "exchange"):
        f = intake.form(key)
        assert f["table"] and any(fd["required"] for fd in intake.data_fields(key))
    # resource matches the sample form's required fields
    req = {fd["name"] for fd in intake.data_fields("resource") if fd["required"]}
    assert {"category", "name", "location", "website", "offer", "email"} <= req
