"""Splat uploads: format sniffing, one-per-piece replacement, public file, owner-only delete."""
import struct

import pytest
from fastapi.testclient import TestClient

from central import sessions


def _fake_splat(n=32) -> bytes:
    one = struct.pack("<3f", 0, 0, 0) + struct.pack("<3f", .01, .01, .01) + bytes((200, 100, 30, 255)) + bytes((255, 128, 128, 128))
    return one * n


@pytest.fixture
def client(demo_db, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_SPLAT_DIR", str(tmp_path / "splats"))
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api import splats as S
    monkeypatch.setattr(S, "MEDIA", tmp_path / "splats")
    from api.main import app
    return TestClient(app)


def _auth(uid="u1", email="u1@example.com", name="U One"):
    return {"Authorization": f"Bearer {sessions.issue(uid, email, name)}"}


def test_sniff_format():
    from api.splats import sniff_format
    assert sniff_format(b"ply\nformat binary_little_endian 1.0\n") == "ply"
    assert sniff_format(_fake_splat(), "piece.splat") == "splat"
    assert sniff_format(_fake_splat()) == "splat"
    assert sniff_format(b"\x00" * 100, "thing.ksplat") == "ksplat"
    assert sniff_format(b"hello", "x.txt") is None


def test_upload_replace_fetch_delete(client):
    assert client.post("/splats?object=1", content=_fake_splat()).status_code == 401
    oid = client.post("/me/objects", json={"title": "Amber pumpkin"}, headers=_auth()).json()["id"]
    # wrong piece / bad bytes
    assert client.post(f"/splats?object={oid + 99}", content=_fake_splat(), headers=_auth()).status_code == 404
    assert client.post(f"/splats?object={oid}", content=b"not a splat", headers=_auth()).status_code == 400
    # upload
    j = client.post(f"/splats?object={oid}&name=pumpkin.splat", content=_fake_splat(), headers=_auth()).json()
    assert j["ok"] and j["splat"]["fmt"] == "splat" and j["splat"]["title"] == "Amber pumpkin"
    sid = j["splat"]["id"]
    assert j["splat"]["share"] == f"/splat.html?s={sid}"
    # public file + meta, no auth
    r = client.get(f"/splats/{sid}")
    assert r.status_code == 200 and r.content == _fake_splat()
    m = client.get(f"/splats/{sid}/meta").json()
    assert m["fmt"] == "splat" and m["bytes"] == len(_fake_splat()) and "uid" not in m
    # a second upload for the same piece replaces the first
    j2 = client.post(f"/splats?object={oid}", content=b"ply\nformat ascii 1.0\nend_header\n", headers=_auth()).json()
    assert j2["splat"]["fmt"] == "ply" and j2["splat"]["id"] != sid
    assert client.get(f"/splats/{sid}").status_code == 404
    mine = client.get("/splats/mine", headers=_auth()).json()["splats"]
    assert [s["id"] for s in mine] == [j2["splat"]["id"]]
    # only the owner can delete
    assert client.delete(f"/splats/{j2['splat']['id']}", headers=_auth("u2", "u2@example.com")).status_code == 404
    assert client.delete(f"/splats/{j2['splat']['id']}", headers=_auth()).json()["ok"]
    assert client.get(f"/splats/{j2['splat']['id']}").status_code == 404
