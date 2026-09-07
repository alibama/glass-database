"""
central.harvest
==============
Consent-gated harvesting of glass-related content from a contributor's own
website / social — with the artist kept as owner and in control.

Rules baked in:
  * We only accept a candidate if that artist ticked harvest consent on their
    (approved) directory entry — consent is re-checked at record time.
  * Every harvested image is signed with C2PA provenance that asserts the ARTIST
    as creator/owner and records where and how it was obtained (harvested, with
    consent, from <source>).
  * Nothing is published. Items land as **pending**; an admin (or, later, the
    artist) approves each one individually before it can appear.

The actual fetching lives in a separate runner (deploy/harvest_runner.py) so this
module stays testable and source-agnostic: an API/OAuth fetch and a Playwright
scrape both just call record_candidate().
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone


def ensure_harvest(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS harvested_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, artist_name TEXT, artist_email TEXT,
        source TEXT, source_url TEXT, content_hash TEXT, caption TEXT,
        image_b64 TEXT, manifest_json TEXT, harvested_at TEXT, status TEXT DEFAULT 'pending')""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_harv_status ON harvested_items(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_harv_hash ON harvested_items(content_hash)")
    conn.commit()


def consent_ok(conn, artist_name: str = "", artist_email: str = "") -> bool:
    """True only if a matching, *approved* artist has harvest_consent set."""
    from central import approvals
    try:
        clause = "lower(artist_name)=?" if artist_name else "lower(email)=?"
        val = (artist_name or artist_email).strip().lower()
        rows = conn.execute(
            f'SELECT harvest_consent FROM artist_submissions '
            f'WHERE {clause} AND {approvals.approved_subquery()}', (val, "artist_submissions")).fetchall()
        return any((r[0] or "").strip().lower() == "yes" for r in rows)
    except Exception:
        return False


def sign_ownership(image_bytes: bytes, artist_name: str, source_url: str):
    """Sign the image with C2PA asserting the artist as owner + harvest provenance.
    Falls back to (image, minimal manifest) if C2PA isn't available."""
    content_hash = hashlib.sha256(image_bytes).hexdigest()[:16]
    prov = {"content_hash": content_hash, "sourcing": "harvested-with-consent",
            "contributor": artist_name, "source_url": source_url,
            "rights": f"© {artist_name}. Harvested with the artist's consent; "
                      "the artist retains ownership and controls publication."}
    try:
        from glowtbook import c2pa_sign
        if c2pa_sign.available():
            signed = c2pa_sign.sign_jpeg(image_bytes, f"Work by {artist_name}", artist_name, prov)
            return signed, {"content_hash": content_hash, "provenance": prov, "c2pa": True}
    except Exception:
        pass
    return image_bytes, {"content_hash": content_hash, "provenance": prov, "c2pa": False}


def record_candidate(conn, artist_name: str, source: str, source_url: str,
                     image_bytes: bytes, caption: str = "", artist_email: str = "") -> int | None:
    """Store one harvested image as pending — only if the artist consented.
    Returns the item id, an existing id on duplicate, or None if consent is missing."""
    ensure_harvest(conn)
    if not consent_ok(conn, artist_name, artist_email):
        return None
    content_hash = hashlib.sha256(image_bytes).hexdigest()[:16]
    dup = conn.execute("SELECT id FROM harvested_items WHERE content_hash=?", (content_hash,)).fetchone()
    if dup:
        return dup[0]
    signed, manifest = sign_ownership(image_bytes, artist_name, source_url)
    import json
    cur = conn.execute(
        "INSERT INTO harvested_items (artist_name,artist_email,source,source_url,content_hash,"
        "caption,image_b64,manifest_json,harvested_at,status) VALUES (?,?,?,?,?,?,?,?,?, 'pending')",
        (artist_name, artist_email, source, source_url, content_hash, caption,
         base64.b64encode(signed).decode(), json.dumps(manifest),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid


def list_items(conn, status: str | None = "pending", artist_name: str = "") -> list[dict]:
    ensure_harvest(conn)
    sql = "SELECT id,artist_name,source,source_url,content_hash,caption,manifest_json,harvested_at,status " \
          "FROM harvested_items"
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if artist_name:
        where.append("lower(artist_name)=?"); args.append(artist_name.lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY harvested_at DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def image_bytes(conn, item_id: int) -> bytes | None:
    ensure_harvest(conn)
    r = conn.execute("SELECT image_b64 FROM harvested_items WHERE id=?", (item_id,)).fetchone()
    return base64.b64decode(r[0]) if r else None


def set_status(conn, item_id: int, status: str) -> None:
    ensure_harvest(conn)
    conn.execute("UPDATE harvested_items SET status=? WHERE id=?", (status, item_id))
    conn.commit()


def counts(conn) -> dict:
    ensure_harvest(conn)
    return {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM harvested_items GROUP BY status")}
