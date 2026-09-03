"""
central.notify
==============
Post new submissions to a Discord channel with **one-click approve / reject
links**. The links carry an HMAC signature over (table, row) so only someone
holding the shared secret could have produced them — and since the message only
lands in your access-controlled Discord channel, seeing the message is the
permission. Clicking hits /api/moderate, which verifies the signature and sets
the approval status.

Config (all optional — absent = no notifications):
  DISCORD_WEBHOOK_URL   the channel webhook (Server Settings → Integrations)
  GLASSDB_ADMIN_TOKEN   reused as the signing secret (already set by the installer)
"""
from __future__ import annotations

import hashlib
import hmac
import os

ACTIONS = ("approve", "reject")


def _secret() -> str:
    return (os.environ.get("GLASSDB_ADMIN_TOKEN")
            or os.environ.get("GLASSDB_MODERATION_SECRET") or "change-me")


def sign(tbl: str, row: str) -> str:
    return hmac.new(_secret().encode(), f"{tbl}:{row}".encode(), hashlib.sha256).hexdigest()[:32]


def verify(tbl: str, row: str, sig: str) -> bool:
    try:
        return hmac.compare_digest(sign(tbl, row), sig or "")
    except Exception:
        return False


def moderation_links(base_url: str, tbl: str, row: str) -> tuple[str, str]:
    base = (base_url or "").rstrip("/")
    s = sign(tbl, row)
    mk = lambda a: f"{base}/api/moderate?tbl={tbl}&row={row}&action={a}&sig={s}"  # noqa: E731
    return mk("approve"), mk("reject")


def notify_submission(kind: str, title: str, fields: dict, tbl: str, row: str,
                      base_url: str) -> bool:
    """Post an embed with the submission's details + approve/reject links.
    Returns True if a webhook was configured and the post was attempted."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return False
    approve, reject = moderation_links(base_url, tbl, row)
    embed = {
        "title": f"New {kind}: {title}"[:256],
        "color": 0xEA580C,
        "fields": [{"name": k[:256], "value": (str(v)[:1024] or "—"), "inline": True}
                   for k, v in fields.items() if v][:12],
        "description": f"[✅ Approve & publish]({approve})  ·  [⛔ Reject]({reject})",
        "footer": {"text": "Glass Database · review queue"},
    }
    try:
        import httpx
        r = httpx.post(url, json={"username": "Glass Database", "embeds": [embed]}, timeout=10)
        return r.status_code < 300
    except Exception:
        return False
