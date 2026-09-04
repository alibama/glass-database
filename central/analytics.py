"""
central.analytics
================
Privacy-first, self-hosted usage analytics — no cookies, no third parties, no raw
IPs stored, data stays in your own database.

What it captures: which surface/view is used, key events (submissions, verifies),
and — only if a GeoLite2 database is configured — a coarse **country**. Visitors
are counted with a per-day rotating hash (hash of IP + day + salt), so we can
approximate daily uniques without storing IPs or tracking anyone across days.
Honours Do-Not-Track at the call site (see brand.track).
"""
from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timezone

_SALT = os.environ.get("GLASSDB_ANALYTICS_SALT") or os.environ.get("GLASSDB_ADMIN_TOKEN") or "gdb"


def ensure_analytics(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT,
        surface TEXT, view TEXT, event TEXT, country TEXT, session TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_an_day ON analytics_events(day)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_an_surface ON analytics_events(surface)")
    conn.commit()


def daily_session(ip: str) -> str:
    """A per-day visitor id that never stores the IP and can't be linked across days."""
    if not ip:
        return ""
    return hashlib.sha256(f"{ip}|{date.today().isoformat()}|{_SALT}".encode()).hexdigest()[:16]


def country_for(ip: str) -> str:
    """Country from a GeoLite2 DB if GEOIP_DB points at one; else '' (no lookup)."""
    db = os.environ.get("GEOIP_DB", "")
    if not ip or not db or not os.path.exists(db):
        return ""
    try:
        import geoip2.database
        with geoip2.database.Reader(db) as r:
            return r.country(ip.split(",")[0].strip()).country.iso_code or ""
    except Exception:
        return ""


def log(conn, surface: str, view: str = "", event: str = "view",
        ip: str = "", country: str = "", session: str = "") -> None:
    try:
        ensure_analytics(conn)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO analytics_events (ts,day,surface,view,event,country,session) "
            "VALUES (?,?,?,?,?,?,?)",
            (now.isoformat(), now.date().isoformat(), surface, view[:60], event[:60],
             country, session or daily_session(ip)))
        conn.commit()
    except Exception:
        pass   # analytics must never break a page


def _rows(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return []


def summary(conn, days: int = 30) -> dict:
    ensure_analytics(conn)
    since = f"date('now','-{int(days)} day')"
    where = f"day >= {since}"
    tot = _rows(conn, f"SELECT COUNT(*) v, COUNT(DISTINCT session) s FROM analytics_events "
                      f"WHERE {where} AND event='view'")
    views = tot[0][0] if tot else 0
    visitors = tot[0][1] if tot else 0
    return {
        "days": days, "views": views, "visitors": visitors,
        "by_surface": _rows(conn, f"SELECT surface, COUNT(*) n, COUNT(DISTINCT session) v "
                                  f"FROM analytics_events WHERE {where} AND event='view' "
                                  "GROUP BY surface ORDER BY n DESC"),
        "by_view": _rows(conn, f"SELECT surface||' · '||view label, COUNT(*) n "
                              f"FROM analytics_events WHERE {where} AND event='view' AND view!='' "
                              "GROUP BY label ORDER BY n DESC LIMIT 15"),
        "by_day": _rows(conn, f"SELECT day, COUNT(*) n, COUNT(DISTINCT session) v "
                             f"FROM analytics_events WHERE {where} AND event='view' "
                             "GROUP BY day ORDER BY day"),
        "by_country": _rows(conn, f"SELECT country, COUNT(DISTINCT session) v FROM analytics_events "
                                 f"WHERE {where} AND country!='' GROUP BY country ORDER BY v DESC LIMIT 15"),
        "by_event": _rows(conn, f"SELECT event, COUNT(*) n FROM analytics_events "
                               f"WHERE {where} AND event!='view' GROUP BY event ORDER BY n DESC LIMIT 15"),
    }


def prune(conn, keep_days: int = 400) -> None:
    """Optional retention — drop events older than keep_days."""
    try:
        conn.execute(f"DELETE FROM analytics_events WHERE day < date('now','-{int(keep_days)} day')")
        conn.commit()
    except Exception:
        pass
