"""The extracted, UI-agnostic contribution module: local CRUD + central publish."""
import sqlite3

from glowtbook import contribute


def _local_db(tmp_path):
    c = sqlite3.connect(tmp_path / "gb.db")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE object (id INTEGER PRIMARY KEY, user_id TEXT, title TEXT, maker TEXT DEFAULT '',
        year TEXT DEFAULT '', techniques TEXT DEFAULT '', materials TEXT DEFAULT '',
        dimensions TEXT DEFAULT '', description TEXT DEFAULT '', acquired TEXT DEFAULT '',
        current_location TEXT DEFAULT '', value_amount TEXT DEFAULT '', value_currency TEXT DEFAULT 'USD',
        insured INTEGER DEFAULT 0, status TEXT DEFAULT 'in collection',
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE object_image (id INTEGER PRIMARY KEY, object_id INTEGER, user_id TEXT,
        role TEXT, aip_path TEXT, caption TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE prov_event (id INTEGER PRIMARY KEY, object_id INTEGER, user_id TEXT, event_type TEXT,
        event_date TEXT, actor TEXT DEFAULT '', location TEXT DEFAULT '', note TEXT DEFAULT '',
        value_amount TEXT DEFAULT '', value_currency TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')));
    """)
    return c


def test_local_crud(tmp_path):
    c = _local_db(tmp_path)
    oid = contribute.create_object(c, "u1", title="Reticello vase", maker="AP", year="2025",
                                   techniques="Cane / murrine")
    assert oid
    contribute.update_object(c, oid, "u1", title="Reticello vase II", maker="AP", year="2025",
                             techniques="Cane / murrine", materials="soda-lime", dimensions="20cm",
                             description="", value_amount="1200", value_currency="USD", insured=1)
    row = c.execute("SELECT title, insured FROM object WHERE id=?", (oid,)).fetchone()
    assert row["title"].endswith("II") and row["insured"] == 1
    contribute.add_event(c, oid, "u1", event_type="created", event_date="2025", actor="AP")
    contribute.add_image(c, oid, "u1", role="primary", aip_path="/tmp/x.jpg", caption="front")
    assert c.execute("SELECT COUNT(*) FROM prov_event").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM object_image").fetchone()[0] == 1


def test_contribute_object_signature():
    # the central publish pipeline is importable and takes the documented args
    import inspect
    params = inspect.signature(contribute.contribute_object).parameters
    for p in ["uid", "display", "obj", "events", "images", "include_value",
              "sign", "object_id", "archive_aip", "push_minio"]:
        assert p in params
