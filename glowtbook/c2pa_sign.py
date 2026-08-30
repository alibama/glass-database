"""
glowtbook.c2pa_sign
===================
Content Credentials for contributed object images. Signs each condensed DIP
JPEG with a C2PA manifest (embedding our provenance as an assertion) so the
credential travels *with* the image, and can read it back to verify.

Certificate model:
  * For a proof-of-concept this generates a self-signed ES256 cert under
    data/c2pa/. Self-signed = a well-formed, readable credential that verifiers
    will flag "untrusted" — perfect for demonstrating the pipeline.
  * For production, drop in an end-entity cert from a C2PA Trust List CA
    (e.g. SSL.com's 2026 free tier) as data/c2pa/cert.pem + key.pem; nothing
    else changes.

Signing uses a callback + `cryptography` (raw P1363 ES256), which is the
reliable path in c2pa-python 0.37.
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
CERT_DIR = DATA / "c2pa"
CERT_PATH = CERT_DIR / "cert.pem"
KEY_PATH = CERT_DIR / "key.pem"


def _libs():
    import c2pa  # noqa
    from cryptography.hazmat.primitives import hashes, serialization  # noqa
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature  # noqa
    return c2pa, hashes, serialization, ec, decode_dss_signature


def available() -> bool:
    try:
        _libs(); return True
    except Exception:
        return False


def ensure_test_cert() -> tuple[bytes, bytes]:
    """Return (cert_pem, key_pem), generating a self-signed ES256 test cert if absent."""
    if CERT_PATH.exists() and KEY_PATH.exists():
        return CERT_PATH.read_bytes(), KEY_PATH.read_bytes()
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Glassdatabase Test Signer"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Glassdatabase")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .add_extension(ski, critical=False)   # c2pa requires a Subject Key Identifier
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski),
                           critical=False)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    KEY_PATH.write_bytes(key_pem); os.chmod(KEY_PATH, 0o600)
    CERT_PATH.write_bytes(cert_pem)
    return cert_pem, key_pem


def _signer(cert_pem: bytes, key_pem: bytes):
    c2pa, hashes, serialization, ec, decode_dss_signature = _libs()
    key = serialization.load_pem_private_key(key_pem, password=None)

    def cb(data: bytes) -> bytes:
        der = key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")   # COSE wants raw R||S

    return c2pa.Signer.from_callback(cb, c2pa.C2paSigningAlg.ES256, cert_pem.decode(), None)


def sign_jpeg(src_jpeg: bytes, title: str, author: str, provenance: dict) -> bytes:
    """Embed a C2PA manifest into a JPEG and return the signed bytes."""
    c2pa, *_ = _libs()
    cert_pem, key_pem = ensure_test_cert()
    manifest = {
        "claim_generator_info": [{"name": "Glowtbook", "version": "0.1"}],
        "title": title or "Glass object",
        "assertions": [
            {"label": "stds.schema-org.CreativeWork",
             "data": {"@context": "https://schema.org", "@type": "CreativeWork",
                      "author": [{"@type": "Person", "name": author or "unknown"}]}},
            {"label": "glassdb.provenance", "data": provenance},
        ],
    }
    signer = _signer(cert_pem, key_pem)
    with tempfile.TemporaryDirectory() as d:
        s, o = os.path.join(d, "s.jpg"), os.path.join(d, "o.jpg")
        Path(s).write_bytes(src_jpeg)
        c2pa.Builder(json.dumps(manifest)).sign_file(s, o, signer)
        return Path(o).read_bytes()


def read_credentials(image_bytes: bytes) -> dict | None:
    """Read the embedded C2PA manifest, or None if there isn't one."""
    c2pa, *_ = _libs()
    try:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "i.jpg"); Path(p).write_bytes(image_bytes)
            data = json.loads(c2pa.Reader(p).json())
    except Exception:
        return None
    am = data.get("active_manifest")
    active = data.get("manifests", {}).get(am, {}) if am else {}
    if not active:
        return None
    sig = active.get("signature_info", {}) or {}
    return {
        "title": active.get("title"),
        "issuer": sig.get("issuer") or sig.get("common_name"),
        "time": sig.get("time"),
        "validation_state": data.get("validation_state"),
        "assertions": [a.get("label") for a in active.get("assertions", [])],
    }
