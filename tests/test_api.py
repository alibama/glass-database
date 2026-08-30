"""API: self-describing datasets, private-column withholding, restricted 403,
and the object image/video endpoints."""
from fastapi.testclient import TestClient


def _client(demo_db):
    from api.main import app
    return TestClient(app)


def test_datasets_listed_and_private_withheld(demo_db):
    cl = _client(demo_db)
    ds = {d["tbl"] for d in cl.get("/datasets").json()["datasets"]}
    assert {"studios", "artists"} <= ds
    row = cl.get("/datasets/studios").json()["rows"][0]
    assert "email_address" not in row          # private column withheld


def test_restricted_dataset_blocked(demo_db):
    cl = _client(demo_db)
    assert cl.get("/datasets/studio_intake").status_code == 403


def test_object_media_404_when_absent(demo_db):
    cl = _client(demo_db)
    assert cl.get("/objects/nope/image").status_code == 404
    assert cl.get("/objects/nope/video").status_code == 404


def test_csv_export(demo_db):
    cl = _client(demo_db)
    r = cl.get("/datasets/artists?format=csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
