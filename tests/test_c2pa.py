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
    signed = c2pa_sign.sign_jpeg(dip, "Reticello vase", "AP",
                                 {"content_hash": "abc123", "sourcing": "self-reported"})
    assert len(signed) > 0
    creds = c2pa_sign.read_credentials(signed)
    assert creds and "glassdb.provenance" in creds["assertions"]
    # an unsigned image has no credential
    assert c2pa_sign.read_credentials(dip) is None
