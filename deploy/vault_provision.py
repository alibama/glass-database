#!/usr/bin/env python3
"""
Provision the C2PA signing CERTIFICATE to match a key held in HashiCorp Vault.

The Vault Transit key is the source of truth (generated non-exportable in Vault).
This reads Vault's public key, builds a self-signed X.509 cert whose public key is
that Vault key, signs the cert's TBS *via Vault*, and writes it to cert.pem. So the
cert and the signing key are guaranteed to be the same keypair — otherwise C2PA
signing fails with "COSE signature invalid".

Run on the box (Vault unsealed; key already created, e.g.
`vault write -f transit/keys/glassdb-c2pa type=ecdsa-p256 exportable=false`):

    VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<app-token> \
    python deploy/vault_provision.py --org "<Your Legal Org>"

For production you'd instead generate a CSR for the Vault key and have the C2PA
Trust-List CA issue the cert. This self-signed cert is for the test phase and
validates as untrusted until then.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import os
import sys


def _vault(method, path):
    import httpx
    addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        print("Set VAULT_TOKEN."); sys.exit(1)
    return addr, token, httpx


def vault_public_key_pem(mount, key) -> str:
    addr, token, httpx = _vault("get", "")
    r = httpx.get(f"{addr}/v1/{mount}/keys/{key}", headers={"X-Vault-Token": token}, timeout=15)
    r.raise_for_status()
    keys = r.json()["data"]["keys"]
    return keys[str(max(int(k) for k in keys))]["public_key"]


def vault_sign_der(mount, key, tbs: bytes) -> bytes:
    addr, token, httpx = _vault("post", "")
    r = httpx.post(f"{addr}/v1/{mount}/sign/{key}", headers={"X-Vault-Token": token},
                   json={"input": base64.b64encode(tbs).decode(),
                         "hash_algorithm": "sha2-256", "prehashed": False}, timeout=15)
    r.raise_for_status()
    return base64.b64decode(r.json()["data"]["signature"].split(":")[-1])   # DER


def cert_from_pubkey(pub_pem, cn, org, country, sign_der) -> bytes:
    from asn1crypto import pem as apem
    from asn1crypto import x509 as ax
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    pub = serialization.load_pem_public_key(pub_pem.encode())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, country)])
    now = datetime.datetime.now(datetime.timezone.utc)
    throwaway = ec.generate_private_key(ec.SECP256R1())      # only to shape the TBS
    ski = x509.SubjectKeyIdentifier.from_public_key(pub)
    tmp = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pub)
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - datetime.timedelta(days=1))
           .not_valid_after(now + datetime.timedelta(days=730))
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
               key_encipherment=False, data_encipherment=False, key_agreement=False,
               key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False),
               critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
           .add_extension(ski, critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski),
                          critical=False)
           .sign(throwaway, hashes.SHA256()))
    tbs = tmp.tbs_certificate_bytes
    cert = ax.Certificate({"tbs_certificate": ax.TbsCertificate.load(tbs),
                           "signature_algorithm": {"algorithm": "sha256_ecdsa"},
                           "signature_value": sign_der(tbs)})
    return apem.armor("CERTIFICATE", cert.dump())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="glassdb-c2pa")
    ap.add_argument("--mount", default="transit")
    ap.add_argument("--cn", default="Glass Database")
    ap.add_argument("--org", required=True, help="Legal organization name (goes on the cert)")
    ap.add_argument("--country", default="US")
    ap.add_argument("--cert-out", default="/opt/glassdatabase/data/c2pa/cert.pem")
    args = ap.parse_args()

    pub = vault_public_key_pem(args.mount, args.key)
    pem = cert_from_pubkey(pub, args.cn, args.org, args.country,
                           lambda tbs: vault_sign_der(args.mount, args.key, tbs))

    # sanity: the assembled cert must self-verify
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    c = x509.load_pem_x509_certificate(pem)
    c.public_key().verify(c.signature, c.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256()))

    os.makedirs(os.path.dirname(args.cert_out), exist_ok=True)
    with open(args.cert_out, "wb") as f:
        f.write(pem)
    print(f"Wrote {args.cert_out} — cert matches Vault key '{args.key}' (self-verify OK).")
    print("Set C2PA_SIGNER=vault + VAULT_* on the app, remove any old data/c2pa/key.pem, "
          "restart glassdb-glowtbook, and re-contribute a test object.")


if __name__ == "__main__":
    main()
