"""
central.price_commit  —  PROTOTYPE: confidential-but-assertable pricing.

Keep prices private, but be able to *prove* them later — to an insurer or a tax
authority — without publishing them. Two building blocks:

1. Hash commitment (selective disclosure).
   commit = SHA-256(value | currency | salt). Publish/sign the commitment; keep
   (value, salt) private. To assert the price to someone, reveal (value, salt); they
   verify it matches the commitment you signed earlier. Reveals nothing until opened,
   and you can't change the number after committing.

2. Pedersen commitment (homomorphic — prove a TOTAL, not the parts).
   C(m, r) = g^m · h^r mod p, in a prime-order subgroup where log_g(h) is unknown.
   Pedersen commitments multiply: ∏ C(m_i, r_i) = C(Σ m_i, Σ r_i). So you can publish
   one commitment per piece, then prove your *portfolio total* equals a declared value
   (for an insurance floor or a tax return) by revealing only the total and the total
   blinding factor — never the individual prices.

This is a prototype to explore the concept. It is NOT a full zero-knowledge system:
a Pedersen commitment proves the arithmetic, not that a price is fair (an appraiser
does that), and true "prove a value is in a range without revealing it" needs a range
proof (Bulletproofs) — see docs/PRICING-COMMITMENTS.md.
"""
from __future__ import annotations

import hashlib
import json
import secrets

# --- 1. hash commitment -----------------------------------------------------

def _canon(value, currency: str) -> bytes:
    return json.dumps({"v": str(value), "c": currency.upper()}, sort_keys=True).encode()


def hash_commit(value, currency: str = "USD", salt: str | None = None) -> dict:
    salt = salt or secrets.token_hex(16)
    h = hashlib.sha256(_canon(value, currency) + bytes.fromhex(salt)).hexdigest()
    return {"scheme": "sha256-commit", "commitment": h, "salt": salt}


def hash_verify(commitment: str, value, currency: str, salt: str) -> bool:
    h = hashlib.sha256(_canon(value, currency) + bytes.fromhex(salt)).hexdigest()
    return secrets.compare_digest(h, commitment)


# --- 2. Pedersen commitment (homomorphic) -----------------------------------
# RFC 3526 2048-bit MODP prime (a safe prime p = 2q+1). g,h are quadratic residues
# (order-q subgroup); h is hashed-to-group so log_g(h) is unknown (Pedersen hiding).
_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
_Q = (_P - 1) // 2
_G = 4  # = 2^2, a quadratic residue and generator of the order-q subgroup


def _h() -> int:
    seed = int(hashlib.sha256(b"glassdatabase/pedersen/h/v1").hexdigest(), 16) % _P
    return pow(seed, 2, _P)  # square -> quadratic residue, unknown log base g


def pedersen_commit(m: int, r: int | None = None) -> dict:
    """m is an integer price (e.g. cents). Returns commitment + blinding r."""
    r = r if r is not None else secrets.randbelow(_Q)
    C = (pow(_G, m % _Q, _P) * pow(_h(), r % _Q, _P)) % _P
    return {"scheme": "pedersen-modp2048", "commitment": hex(C), "r": hex(r)}


def pedersen_verify(commitment: str, m: int, r: int) -> bool:
    C = int(commitment, 16)
    return C == (pow(_G, m % _Q, _P) * pow(_h(), r % _Q, _P)) % _P


def pedersen_sum(commitments: list[str]) -> str:
    """Homomorphic sum: product of commitments == commitment to the sum."""
    prod = 1
    for c in commitments:
        prod = (prod * int(c, 16)) % _P
    return hex(prod)


def verify_total(sum_commitment: str, total_m: int, total_r: int) -> bool:
    """Prove Σ m_i == total_m by revealing only the total and total blinding."""
    return pedersen_verify(sum_commitment, total_m, total_r)


def assertion(commitments: list[dict], note: str = "") -> dict:
    """A public, signable C2PA/record assertion carrying only commitments (no prices)."""
    return {"label": "org.glassdatabase.price-commitment", "data": {
        "scheme": commitments[0]["scheme"] if commitments else "sha256-commit",
        "commitments": [c["commitment"] for c in commitments],
        "note": note or "Prices are committed but private; the owner can reveal to verify.",
    }}
