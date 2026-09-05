"""Login ledger + Google-email admin roles."""

from central import users
from central.dbconn import connect


def test_record_login_upserts(demo_db):
    c = connect()
    users.record_login(c, "Rae@Example.org", "Rae")
    users.record_login(c, "rae@example.org", "Rae Sutter")   # same email, different case
    row = c.execute("SELECT email,name,logins FROM users").fetchall()
    assert len(row) == 1 and row[0][0] == "rae@example.org" and row[0][2] == 2


def test_promote_and_demote(demo_db):
    c = connect()
    users.record_login(c, "a@x.org", "A")
    assert users.is_admin(c, "a@x.org") is False
    users.set_admin(c, "a@x.org", True)
    assert users.is_admin(c, "A@X.org") is True    # case-insensitive
    users.set_admin(c, "a@x.org", False)
    assert users.is_admin(c, "a@x.org") is False


def test_grant_admin_before_first_login(demo_db):
    c = connect()
    users.set_admin(c, "new@x.org", True)          # never logged in yet
    assert users.is_admin(c, "new@x.org") is True


def test_bootstrap_emails_always_admin(demo_db, monkeypatch):
    monkeypatch.setenv("GLASSDB_ADMIN_EMAILS", "boss@x.org, other@x.org")
    c = connect()
    assert users.is_admin(c, "boss@x.org") is True
    assert users.is_admin(c, "nobody@x.org") is False
    # a bootstrap user is flagged admin on first login
    users.record_login(c, "boss@x.org", "Boss")
    assert dict(users.list_users(c)[0])["via_config"] is True
