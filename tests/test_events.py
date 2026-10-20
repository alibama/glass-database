"""Maker events + purchase registration: the two-stage approval flow, end to end."""
import base64
import io

import pytest
from fastapi.testclient import TestClient

from central import approvals, sessions
from central import events as E
from central.dbconn import connect


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    from api.main import app
    return TestClient(app)


def _auth(uid, name, email=None):
    return {"Authorization": f"Bearer {sessions.issue(uid, email or f'{uid}@example.com', name)}"}


def _png():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (900, 700), (210, 120, 40)).save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def test_core_flow(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    c = connect(); E.ensure(c)
    ev = E.create_event(c, host_uid="h", host_name="Raging Buffalo Glass", title="Ornament day",
                        studio="Raging Buffalo Glass", venue="2nd St Gallery", starts="2026-12-06",
                        piece_type="ornament", registration_closes="2026-12-31")
    assert ev["slug"].startswith("raging-buffalo-glass-ornament-day") and ev["listing"] == "pending"
    assert E.can_register(ev, today="2026-12-10") and not E.can_register(ev, today="2027-01-01")
    # unlisted event is not in the public directory but IS reachable by its link
    assert E.list_events(c) == [] and E.public_event(c, ev["slug"])["can_register"] is True
    E.set_listing(c, ev["id"], "approved")
    assert [e["slug"] for e in E.list_events(c)] == [ev["slug"]]

    r1 = E.register(c, ev["slug"], buyer_uid="b1", buyer_name="Pat", title="Blue ornament", piece_ref="12")
    r2 = E.register(c, ev["slug"], buyer_uid="b2", buyer_name="Sam", title="Red ornament", show_name=True)
    assert r1["status"] == "pending_host" and E._counts(c, ev["id"])["pending_host"] == 2
    with pytest.raises(ValueError):
        E.host_review(c, ev["id"], "not-the-host", [r1["id"]], True)
    assert E.host_review(c, ev["id"], "h", [r1["id"]], True) == 1
    assert E.host_review(c, ev["id"], "h", [r1["id"]], True) == 0        # already moved: no double count
    assert E.host_approve_all(c, ev["id"], "h") == 1                       # r2
    assert [r["id"] for r in E.pending_admin(c)] == [r1["id"], r2["id"]]
    assert E.public_registrations(c, ev["id"]) == []                       # nothing public before stage two

    done = E.admin_review(c, [r1["id"], r2["id"]], True, reviewer="tester")
    assert all(d["status"] == "approved" and d["object_row_id"] for d in done)
    pub = E.public_event(c, ev["slug"])
    assert pub["published"] == 2 and [p["buyer"] for p in pub["pieces"]] == ["Sam", "a collector"]
    row = c.execute("SELECT * FROM objects WHERE _row_id=?", (done[0]["object_row_id"],)).fetchone()
    assert row["sourcing"] == "event registration" and row["maker"].startswith("Raging Buffalo")
    assert c.execute(f"SELECT 1 FROM objects WHERE _row_id=? AND {approvals.approved_subquery()}",
                     (row["_row_id"], "objects")).fetchone()
    import json
    man = json.loads(row["manifest_json"])
    evs = next(a["data"] for a in man["assertions"] if a["label"] == "org.glassdatabase.provenance.events")
    assert [e["event_type"] for e in evs] == ["created", "acquired"] and man["event"]["venue"] == "2nd St Gallery"
    s = E.summary(c)
    assert s["approved"] == 2 and s["events"] == 1


def _listed(client) -> set[str]:
    return {e["slug"] for e in client.get("/events").json()["events"]}


def test_api_flow(client):
    # tokens are minted after the fixture sets the session secret
    HOST = _auth("host1", "Raging Buffalo Glass")
    BUYER = _auth("buyer1", "Pat Collector")
    OTHER = _auth("other1", "Someone Else")
    # anonymous: the directory lists only approved events (other tests may have some); writes need sign-in
    before = _listed(client)
    assert client.post("/events", json={"title": "x"}).status_code == 401

    r = client.post("/events", json={"title": "Ornament day", "studio": "Raging Buffalo Glass",
                                     "venue": "2nd St Gallery", "starts": "2026-12-06",
                                     "piece_type": "ornament"}, headers=HOST)
    assert r.status_code == 200, r.text
    ev = r.json(); slug = ev["slug"]
    assert ev["register_url"].endswith(slug) and "host_email" not in ev

    # public page works before the listing is approved; directory doesn't show it
    j = client.get(f"/events/{slug}").json()
    assert j["can_register"] and j["listed"] is False and j["is_host"] is False
    assert client.get(f"/events/{slug}", headers=HOST).json()["is_host"] is True
    assert slug not in _listed(client) and _listed(client) == before

    # buyer registers with a photo -> also gets a Glowtbook piece + a fingerprint link
    r = client.post(f"/events/{slug}/registrations", json={
        "title": "Blue ornament", "piece_ref": "12", "image_b64": _png(), "show_name": True}, headers=BUYER)
    assert r.status_code == 200, r.text
    reg = r.json()
    assert reg["status"] == "pending_host" and reg["object_id"] and reg["fingerprint_url"].endswith(str(reg["object_id"]))
    mine = client.get("/me/objects", headers=BUYER).json()["objects"]
    assert mine[0]["title"] == "Blue ornament" and mine[0]["maker"] == "Raging Buffalo Glass" and mine[0]["events"] == 2
    assert client.get("/me/registrations", headers=BUYER).json()["registrations"][0]["status"] == "pending_host"
    assert client.post(f"/events/{slug}/registrations", json={"title": ""}, headers=BUYER).status_code == 400

    # the photo is private until published (buyer and host can see it)
    img = f"/events/registrations/{reg['id']}/image"
    assert client.get(img).status_code == 404
    assert client.get(img, headers=BUYER).status_code == 200
    assert client.get(img, headers=HOST).headers["content-type"].startswith("image/")

    # host review: only the host; bulk approve
    assert client.get(f"/events/{slug}/registrations", headers=OTHER).status_code == 403
    q = client.get(f"/events/{slug}/registrations?status=pending_host", headers=HOST).json()
    assert q["counts"]["pending_host"] == 1 and q["registrations"][0]["buyer_email"] == "buyer1@example.com"
    assert client.post(f"/events/{slug}/review", json={"all": True, "approve": True}, headers=OTHER).status_code == 403
    r = client.post(f"/events/{slug}/review", json={"all": True, "approve": True}, headers=HOST).json()
    assert r["changed"] == 1 and r["counts"]["host_approved"] == 1

    # stage two is the admin's: publish
    c = connect()
    done = E.admin_review(c, [reg["id"]], True)
    assert done[0]["object_row_id"]
    assert client.get(img).status_code == 200                               # now public
    j = client.get(f"/events/{slug}").json()
    assert j["published"] == 1 and j["pieces"][0]["buyer"] == "Pat Collector"
    assert client.get(f"/objects/{done[0]['object_row_id']}/image").status_code == 200

    # host closes the event -> no more registrations
    assert client.post(f"/events/{slug}", json={"status": "closed"}, headers=HOST).json()["status"] == "closed"
    assert client.post(f"/events/{slug}/registrations", json={"title": "Late"}, headers=BUYER).status_code == 400
    assert client.post(f"/events/{slug}", json={"status": "open"}, headers=OTHER).status_code == 403
    evs = client.get("/me/events", headers=HOST).json()["events"]
    assert evs[0]["counts"]["approved"] == 1
