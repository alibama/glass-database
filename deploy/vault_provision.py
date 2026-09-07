#!/usr/bin/env python3
"""
Provision the C2PA signing key into HashiCorp Vault (Transit) and write the
matching certificate to data/c2pa/cert.pem.

Flow: generate an ES256 keypair + self-signed test cert locally, BYOK-import the
PRIVATE key into Vault (Transit), write the cert, then securely delete the local
private key. After this, the app host holds only the public cert; signing happens
in Vault, and the key is non-exportable.

Run on the box (Vault unsealed, transit enabled):
    VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=... \
    python deploy/vault_provision.py --key glassdb-c2pa --cn "Glass Database" --org "<Legal Org>"

For production you'd instead generate a CSR for the Vault key and have the C2PA
Trust-List CA issue the cert; the self-signed cert here is for the test phase and
validates as untrusted until then.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import os
import sys


def _self_signed(cn, org, country):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, country)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=730))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                           key_encipherment=False, data_encipherment=False, key_agreement=False,
                           key_cert_sign=False, crl_sign=False, encipher_only=False,
                           decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    key_der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    return cert.public_bytes(serialization.Encoding.PEM), key_der


def _byok_wrap(target_pkcs8_der: bytes, wrapping_pub_pem: str) -> str:
    """Wrap per Vault BYOK: AES-256-KWP the target key, RSA-OAEP(SHA256) the AES key."""
    import os as _os

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap_with_padding
    wrap_pub = serialization.load_pem_public_key(wrapping_pub_pem.encode())
    aes = _os.urandom(32)
    wrapped_target = aes_key_wrap_with_padding(aes, target_pkcs8_der)
    wrapped_aes = wrap_pub.encrypt(aes, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return base64.b64encode(wrapped_aes + wrapped_target).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="glassdb-c2pa")
    ap.add_argument("--cn", default="Glass Database")
    ap.add_argument("--org", required=True, help="Legal organization name (goes on the cert)")
    ap.add_argument("--country", default="US")
    ap.add_argument("--mount", default="transit")
    ap.add_argument("--cert-out", default="/opt/glassdatabase/data/c2pa/cert.pem")
    args = ap.parse_args()

    addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        print("Set VAULT_TOKEN (a token allowed to import into transit)."); sys.exit(1)
    import httpx
    h = {"X-Vault-Token": token}

    cert_pem, key_der = _self_signed(args.cn, args.org, args.country)
    wrap = httpx.get(f"{addr}/v1/{args.mount}/wrapping_key", headers=h, timeout=15)
    wrap.raise_for_status()
    ciphertext = _byok_wrap(key_der, wrap.json()["data"]["public_key"])
    imp = httpx.post(f"{addr}/v1/{args.mount}/keys/{args.key}/import", headers=h,
                     json={"ciphertext": ciphertext, "type": "ecdsa-p256", "hash_function": "SHA256"},
                     timeout=30)
    if imp.status_code >= 300:
        print("Vault import failed:", imp.status_code, imp.text); sys.exit(1)

    os.makedirs(os.path.dirname(args.cert_out), exist_ok=True)
    with open(args.cert_out, "wb") as f:
        f.write(cert_pem)
    # shred the local private key material from memory-adjacent state
    del key_der
    print(f"Imported '{args.key}' into Vault (non-exportable). Cert written to {args.cert_out}.")
    print("Set on the app: C2PA_SIGNER=vault, VAULT_ADDR, VAULT_TOKEN, "
          f"VAULT_TRANSIT_KEY={args.key}. Remove any old data/c2pa/key.pem.")


if __name__ == "__main__":
    main()
