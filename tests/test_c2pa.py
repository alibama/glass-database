"""C2PA Content Credentials sign + read round-trip (self-signed test cert)."""
import pytest

c2pa_sign = pytest.importorskip("glowtbook.c2pa_sign")


@pytest.mark.skipif(not c2pa_sign.available(), reason="c2pa/cryptography not installed")
def test_sign_then_read(tmp_path, sample_image_bytes, monkeypatch):
    monkeypatch.setattr(c2pa_sign, "CERT_DIR", tmp_path / "c2pa")
    monkeypatch.setattr(c2pa_sign, "CERT_PATH", tmp_path / "c2pa" / "cert.pem")
    monkeypatch.setattr(c2pa_sign, "KEY_PATH", tmp_path / "c2pa" / "key.pem")
    from glowtbook import media
    dip = media.condense_image(sample_image_bytes)
    signed = c2pa_sign.sign_jpeg(dip, "Reticello vase", "A. Parker",
                                 {"content_hash": "abc123", "sourcing": "self-reported"},
                                 parent_bytes=sample_image_bytes, parent_format="image/png",
                                 year="2025")
    assert len(signed) > 0
    creds = c2pa_sign.read_credentials(signed)
    assert creds and "glassdb.provenance" in creds["assertions"]
    # CAWG metadata replaces schema.org CreativeWork, and carries the creator
    assert "cawg.metadata" in creds["assertions"]
    assert "stds.schema-org.CreativeWork" not in creds["assertions"]
    assert creds["creator"] == ["A. Parker"]
    # first action must be created or opened (spec) — here it's opened (edit intent)
    assert creds["actions"] and creds["actions"][0] == "c2pa.opened"
    # an unsigned image has no credential
    assert c2pa_sign.read_credentials(dip) is None


@pytest.mark.skipif(not c2pa_sign.available(), reason="c2pa/cryptography not installed")
def test_sign_created_when_no_parent(tmp_path, sample_image_bytes, monkeypatch):
    monkeypatch.setattr(c2pa_sign, "CERT_DIR", tmp_path / "c2pa")
    monkeypatch.setattr(c2pa_sign, "CERT_PATH", tmp_path / "c2pa" / "cert.pem")
    monkeypatch.setattr(c2pa_sign, "KEY_PATH", tmp_path / "c2pa" / "key.pem")
    from glowtbook import media
    dip = media.condense_image(sample_image_bytes)
    signed = c2pa_sign.sign_jpeg(dip, "Frame", "AP", {"content_hash": "x"})  # no parent
    creds = c2pa_sign.read_credentials(signed)
    assert creds["actions"] and creds["actions"][0] == "c2pa.created"
