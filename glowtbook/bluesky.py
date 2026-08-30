"""
glowtbook.bluesky
=================
An optional ATProto (Bluesky) publish target — post a contributed object as a
public "receipt", the way the receipt project does. One pluggable adapter that
sits beside the central write; keeping things local just means not calling it.

Auth uses a Bluesky **app password** (Settings → App Passwords), entered at
publish time and never stored. Flow: createSession → (optional) uploadBlob →
createRecord(app.bsky.feed.post).

Network-dependent, so this can't be exercised in the build sandbox; the payload
construction is unit-tested and the calls follow the documented XRPC endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

PDS = "https://bsky.social"


def build_post_record(text: str, blob: dict | None, alt: str = "") -> dict:
    """The app.bsky.feed.post record body (separated out so it's testable offline)."""
    rec = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if blob is not None:
        rec["embed"] = {"$type": "app.bsky.embed.images",
                        "images": [{"alt": alt or "Glass object", "image": blob}]}
    return rec


def compose_text(title: str, maker: str, content_hash: str, link: str) -> str:
    lines = [f"🔥 {title}" + (f" — {maker}" if maker else ""),
             f"Provenance receipt {content_hash}"]
    if link:
        lines.append(link)
    txt = "\n".join(lines)
    return txt[:300]  # Bluesky post limit


def publish_object(handle: str, app_password: str, title: str, maker: str,
                   content_hash: str, image_bytes: bytes | None = None,
                   link: str = "", pds: str = PDS, timeout: float = 20.0) -> str:
    """Post the object to Bluesky; returns the post's web URL. Raises on failure."""
    with httpx.Client(base_url=pds, timeout=timeout) as client:
        r = client.post("/xrpc/com.atproto.server.createSession",
                        json={"identifier": handle, "password": app_password})
        r.raise_for_status()
        sess = r.json()
        jwt, did = sess["accessJwt"], sess["did"]
        auth = {"Authorization": f"Bearer {jwt}"}

        blob = None
        if image_bytes:
            up = client.post("/xrpc/com.atproto.repo.uploadBlob", headers={
                **auth, "Content-Type": "image/jpeg"}, content=image_bytes)
            up.raise_for_status()
            blob = up.json().get("blob")

        record = build_post_record(compose_text(title, maker, content_hash, link),
                                   blob, alt=title)
        cr = client.post("/xrpc/com.atproto.repo.createRecord", headers=auth, json={
            "repo": did, "collection": "app.bsky.feed.post", "record": record})
        cr.raise_for_status()
        uri = cr.json().get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        return f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else uri
