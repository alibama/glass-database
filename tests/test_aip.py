"""BagIt AIP creation + validation, and MinIO/S3 push via a live mock server."""
from pathlib import Path

import pytest

bagit = pytest.importorskip("bagit")
from glowtbook import aip  # noqa: E402


def _originals(tmp):
    from PIL import Image
    p = Path(tmp) / "front.png"
    Image.new("RGB", (1000, 800), (120, 60, 30)).save(p)
    return [p]


def test_bag_is_valid(tmp_path):
    originals = _originals(tmp_path)
    rec = aip.archive_object_aip("obj1", "abcd1234ef567890", originals,
                                 {"title": "Sommerso"}, tmp_path / "bags", push=False)
    assert rec["tar_bytes"] > 0 and rec["payload_files"]
    import tarfile
    ex = tmp_path / "ex"; ex.mkdir()
    with tarfile.open(rec["tar_path"]) as t:
        t.extractall(ex)
    bag_dir = next(ex.iterdir())
    bagit.Bag(str(bag_dir)).validate()          # raises if invalid
    assert (bag_dir / "data" / "metadata.json").exists()
    assert (bag_dir / "manifest-sha512.txt").exists()


def test_minio_push_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("boto3")
    try:
        from moto.server import ThreadedMotoServer
    except Exception:
        pytest.skip("moto[server] not installed")
    server = ThreadedMotoServer(port=5099)
    server.start()
    try:
        monkeypatch.setenv("MINIO_ENDPOINT", "localhost:5099")
        monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
        monkeypatch.setenv("MINIO_SECRET_KEY", "sk")
        monkeypatch.setenv("MINIO_BUCKET", "glassdb-aip")
        monkeypatch.setenv("MINIO_SECURE", "0")
        rec = aip.archive_object_aip("objX", "beef1234cafe5678", _originals(tmp_path),
                                     {"title": "x"}, tmp_path / "bags", push=True)
        assert rec["minio"]["bucket"] == "glassdb-aip"
        import boto3
        s3 = boto3.client("s3", endpoint_url="http://localhost:5099",
                          aws_access_key_id="ak", aws_secret_access_key="sk")
        body = s3.get_object(Bucket="glassdb-aip", Key=rec["minio"]["key"])["Body"].read()
        assert len(body) == rec["tar_bytes"]
    finally:
        server.stop()
