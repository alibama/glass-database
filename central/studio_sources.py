"""
central.studio_sources
=====================
A registry of studios that publish open kiln/furnace operating data (CC-BY) via a
read-only API. Anyone can submit one from the studios page; it lands **pending** and
is only polled after an admin approves it — that human review is the main defense
against SSRF (we never fetch an arbitrary URL on submission), backed by a URL check.

Expected studio API contract (what a studio's app should expose):
  GET {api_url}/summary   -> JSON object: firings, energy_kwh, cost_usd, since, ...
  GET {api_url}/firings   -> JSON array (optional; recent firings)
  CORS-open, CC-BY, https, no device addresses / secrets.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

_TTL = 900   # 15 minutes per source
_cache: dict[str, dict] = {}


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS studio_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, city TEXT, region TEXT,
        country TEXT, lat TEXT, lng TEXT, api_url TEXT, attribution TEXT,
        submitted_by TEXT, status TEXT DEFAULT 'pending', added_at TEXT)""")
    conn.commit()


def url_ok(url: str) -> tuple[bool, str]:
    """https only; reject hosts that resolve to private/loopback/reserved IPs."""
    p = urlparse(url or "")
    if p.scheme != "https":
        return False, "URL must be https"
    host = p.hostname
    if not host:
        return False, "no host in URL"
    if host in ("localhost", "metadata.google.internal", "metadata"):
        return False, "internal host not allowed"
    try:
        for res in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(res[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast):
                return False, "URL resolves to a non-public address"
    except Exception:
        pass  # transient DNS failure — the admin still reviews before it's polled
    return True, ""


def add(conn, name, api_url, lat="", lng="", city="", region="", country="",
        attribution="", submitted_by="") -> tuple[int | None, str]:
    ensure(conn)
    if not (name or "").strip():
        return None, "name required"
    ok, why = url_ok(api_url)
    if not ok:
        return None, why
    cur = conn.execute(
        "INSERT INTO studio_sources (name,city,region,country,lat,lng,api_url,attribution,"
        "submitted_by,status,added_at) VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?)",
        (name.strip(), city, region, country, str(lat), str(lng), api_url.strip(),
         attribution.strip() or f"{name.strip()} (CC-BY-4.0)", submitted_by,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid, ""


def list_sources(conn, status: str = "approved") -> list[dict]:
    ensure(conn)
    rows = conn.execute("SELECT * FROM studio_sources WHERE status=? ORDER BY id DESC",
                        (status,)).fetchall()
    return [dict(r) for r in rows]


def set_status(conn, source_id: int, status: str) -> None:
    ensure(conn)
    conn.execute("UPDATE studio_sources SET status=? WHERE id=?", (status, source_id))
    conn.commit()


def _poll(api_url: str, verify: bool = True) -> dict:
    now = time.time()
    c = _cache.get(api_url)
    if c and now - c["at"] < _TTL:
        return {**c["data"], "cached": True, "age_s": int(now - c["at"])}
    import httpx
    out = {"ok": False, "source": api_url}
    try:
        with httpx.Client(timeout=8, verify=verify, follow_redirects=True) as cl:
            r = cl.get(f"{api_url.rstrip('/')}/summary"); r.raise_for_status()
            out["summary"] = r.json()
            try:
                fr = cl.get(f"{api_url.rstrip('/')}/firings", params={"limit": 5})
                if fr.status_code < 300:
                    out["firings"] = fr.json()
            except Exception:
                pass
        out["ok"] = True
        _cache[api_url] = {"at": now, "data": out}
    except Exception as ex:  # noqa: BLE001
        if c:
            return {**c["data"], "cached": True, "stale": True, "error": str(ex)}
        out["error"] = str(ex)
    return out


def aggregate(conn, verify: bool = True) -> list[dict]:
    """Poll every approved source (each on its own 15-min cache)."""
    out = []
    for s in list_sources(conn, "approved"):
        d = _poll(s["api_url"], verify=verify)
        out.append({"name": s["name"], "city": s.get("city"), "region": s.get("region"),
                    "country": s.get("country"), "lat": s.get("lat"), "lng": s.get("lng"),
                    "attribution": s.get("attribution"), **d})
    return out
