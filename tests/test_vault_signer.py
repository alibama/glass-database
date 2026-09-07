"""Vault signer: config gating, DER->P1363 conversion, and disk-signer fallback."""

from glowtbook import vault_signer


def test_available_gated_by_env(monkeypatch):
    monkeypatch.delenv("C2PA_SIGNER", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    assert vault_signer.available() is False
    monkeypatch.setenv("C2PA_SIGNER", "vault")
    assert vault_signer.available() is False          # no token
    monkeypatch.setenv("VAULT_TOKEN", "t")
    assert vault_signer.available() is True


def test_der_to_p1363_is_64_bytes():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    der = k.sign(b"data", ec.ECDSA(hashes.SHA256()))
    assert len(vault_signer.der_to_p1363(der)) == 64


def test_local_signer_still_default(monkeypatch):
    import pytest

    from glowtbook import c2pa_sign as C
    if not C.available():
        pytest.skip("c2pa not installed")
    monkeypatch.delenv("C2PA_SIGNER", raising=False)
    import io

    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (120, 120), (30, 60, 160)).save(b, "JPEG")
    signed = C.sign_image(b.getvalue(), "P", "AP",
                          {"content_hash": "x", "sourcing": "t", "contributor": "AP"}, mime="image/jpeg")
    assert C.read_credentials(signed)["validation_state"] == "Valid"
