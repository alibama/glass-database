"""
glowtbook.vault_signer
=====================
Sign C2PA claims with a key held in HashiCorp Vault's Transit engine, so the
private key never lives on the app host. Enabled by env:

    C2PA_SIGNER=vault
    VAULT_ADDR=http://127.0.0.1:8200
    VAULT_TOKEN=...            (a token with the glassdb-c2pa policy)
    VAULT_TRANSIT_KEY=glassdb-c2pa
    VAULT_TRANSIT_MOUNT=transit

Vault Transit signs with the key and returns a DER ECDSA signature; C2PA/COSE
want raw R‖S (P1363), so we convert. The certificate (public) still lives on disk
at data/c2pa/cert.pem — only the private key moves to Vault.
"""
from __future__ import annotations

import base64
import os


def _cfg():
    return (os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/"),
            os.environ.get("VAULT_TOKEN", ""),
            os.environ.get("VAULT_TRANSIT_KEY", "glassdb-c2pa"),
            os.environ.get("VAULT_TRANSIT_MOUNT", "transit"))


def available() -> bool:
    _addr, token, _key, _mount = _cfg()
    return os.environ.get("C2PA_SIGNER") == "vault" and bool(token)


def der_to_p1363(der: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def sign_callback(data: bytes) -> bytes:
    """The c2pa callback: hand the to-be-signed bytes to Vault, get back R‖S."""
    import httpx
    addr, token, key, mount = _cfg()
    r = httpx.post(f"{addr}/v1/{mount}/sign/{key}",
                   headers={"X-Vault-Token": token},
                   json={"input": base64.b64encode(data).decode(),
                         "hash_algorithm": "sha2-256", "prehashed": False},
                   timeout=15)
    r.raise_for_status()
    sig = r.json()["data"]["signature"]            # "vault:v1:<base64 DER>"
    return der_to_p1363(base64.b64decode(sig.split(":")[-1]))


def public_key_pem() -> str:
    """The current public key for the Transit signing key (PEM), for cert issuance."""
    import httpx
    addr, token, key, mount = _cfg()
    r = httpx.get(f"{addr}/v1/{mount}/keys/{key}", headers={"X-Vault-Token": token}, timeout=15)
    r.raise_for_status()
    keys = r.json()["data"]["keys"]
    latest = str(max(int(k) for k in keys))
    return keys[latest]["public_key"]
