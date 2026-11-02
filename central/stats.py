"""
central.stats
=============
The site's numbers in one place, for ``/stats.html`` and ``GET /api/stats``: what the record
holds (the Directory's tables, the pieces and how many carry a Glass ID and Content
Credentials, the makers and studios), what the collecting events have produced, what the
Wikibase migration has pushed and what the Wikidata lookup found, and, best effort, what the
wiki itself reports (pages, items, edits, users through its API). Cached for ten minutes;
the live wiki numbers for an hour. Nothing here writes.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from central import approvals

TTL = 600
WIKI_TTL = 3600
_CACHE: dict = {"at": 0.0, "data": None}
_WIKI: dict = {"at": 0.0, "url": "", "data": None}


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _n(conn, sql: str, *params) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    except Exception:  # noqa: BLE001
        return 0


def _approved_count(conn, table: str) -> int:
    if not _has_table(conn, table):
        return 0
    return _n(conn, f'SELECT COUNT(*) FROM "{table}" WHERE {approvals.approved_subquery()}', table)


def wiki_live(url: str, force: bool = False) -> dict | None:
    """What the wiki says about itself: pages, items (articles), edits, users. None when the
    wiki is not configured or does not answer within five seconds."""
    url = (url or "").rstrip("/")
    if not url:
        return None
    now = time.time()
    if not force and _WIKI["data"] is not None and _WIKI["url"] == url and now - _WIKI["at"] < WIKI_TTL:
        return _WIKI["data"]
    data = None
    try:
        q = urllib.parse.urlencode({"action": "query", "meta": "siteinfo", "siprop": "statistics", "format": "json"})
        req = urllib.request.Request(f"{url}/w/api.php?{q}", headers={"User-Agent": "GlassDatabase stats (glassdatabase.org)"})
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310  (the configured wiki's own API)
            st = json.loads(r.read().decode("utf-8")).get("query", {}).get("statistics", {})
        data = {"pages": st.get("pages"), "items": st.get("articles"), "edits": st.get("edits"),
                "users": st.get("users"), "active_users": st.get("activeusers"),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    except Exception:  # noqa: BLE001
        data = None
    _WIKI.update({"at": now, "url": url, "data": data})
    return data


def site_stats(conn, *, live: bool = True, force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["at"] < TTL:
        return _CACHE["data"]
    out: dict = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    # the Directory
    datasets = []
    if _has_table(conn, "_datasets"):
        for r in conn.execute("SELECT tbl, domain, description, updated_at FROM _datasets WHERE visibility='public' "
                              "ORDER BY domain, tbl").fetchall():
            datasets.append({"table": r[0], "domain": r[1], "description": r[2] or "", "updated_at": r[3] or "",
                             "rows": _approved_count(conn, r[0])})
    out["directory"] = {"datasets": datasets, "rows": sum(d["rows"] for d in datasets)}
    # pieces
    objects = _approved_count(conn, "objects")
    out["pieces"] = {
        "total": objects,
        "registered": _n(conn, f"SELECT COUNT(*) FROM objects WHERE ((fingerprint_json IS NOT NULL AND fingerprint_json<>'') "
                               f"OR manifest_json LIKE '%\"fingerprint\"%') AND {approvals.approved_subquery()}", "objects")
        if objects else 0,
        "credentialed": _n(conn, f"SELECT COUNT(*) FROM objects WHERE has_credentials=1 AND {approvals.approved_subquery()}",
                           "objects") if objects else 0,
        "with_images": _n(conn, f"SELECT COUNT(DISTINCT object_row_id) FROM object_images WHERE object_row_id IN "
                                f"(SELECT _row_id FROM objects WHERE {approvals.approved_subquery()})", "objects")
        if objects and _has_table(conn, "object_images") else 0,
        "makers": _n(conn, f"SELECT COUNT(DISTINCT lower(trim(maker))) FROM objects WHERE COALESCE(maker,'')<>'' "
                           f"AND {approvals.approved_subquery()}", "objects") if objects else 0,
    }
    # people and places
    out["people"] = {"accounts": _n(conn, "SELECT COUNT(*) FROM users") if _has_table(conn, "users") else 0,
                     "artists": _approved_count(conn, "artists"), "studios": _approved_count(conn, "studios"),
                     "lineage_links": _approved_count(conn, "lineage_submissions")}
    # collecting events
    ev: dict = {}
    if _has_table(conn, "seasons"):
        try:
            from central import seasons
            seasons.ensure(conn)
            ev = seasons.summary(conn)
            ev["prizes_settled"] = _n(conn, "SELECT COUNT(*) FROM season_prizes WHERE COALESCE(winner_text,'')<>''")
            ev["studios_entering"] = _n(conn, "SELECT COUNT(DISTINCT lower(trim(studio))) FROM season_entries "
                                              "WHERE status='approved' AND COALESCE(studio,'')<>''")
            ev["makers_entering"] = _n(conn, "SELECT COUNT(DISTINCT COALESCE(NULLIF(artist_uid,''), lower(trim(artist_name)))) "
                                             "FROM season_entries WHERE status='approved'")
        except Exception:  # noqa: BLE001
            ev = {}
    out["events"] = ev
    # the wiki
    wb: dict = {"configured": False, "url": "", "items_staged": 0, "items_pushed": 0, "duplicates_folded": 0,
                "with_wikidata": 0, "last_push": "", "by_kind": [], "properties": 0, "live": None}
    if _has_table(conn, "wb_items"):
        try:
            from central.wikibase import store
            s = store.get_settings(conn)
            wb["configured"] = bool(s.get("url"))
            wb["url"] = s.get("url") or ""
            counts = store.counts(conn)
            wb["counts"] = counts
        except Exception:  # noqa: BLE001
            pass
        wb["items_staged"] = _n(conn, "SELECT COUNT(*) FROM wb_items WHERE COALESCE(canonical,'')=''")
        wb["items_pushed"] = _n(conn, "SELECT COUNT(*) FROM wb_items WHERE COALESCE(qid,'')<>''")
        wb["duplicates_folded"] = _n(conn, "SELECT COUNT(*) FROM wb_items WHERE COALESCE(canonical,'')<>''")
        wb["with_wikidata"] = _n(conn, "SELECT COUNT(*) FROM wb_items WHERE COALESCE(wikidata_qid,'')<>'' "
                                       "AND COALESCE(canonical,'')=''")
        wb["last_push"] = (conn.execute("SELECT MAX(pushed_at) FROM wb_items").fetchone() or [None])[0] or ""
        wb["by_kind"] = [{"kind": r[0], "staged": int(r[1]), "pushed": int(r[2] or 0), "wikidata": int(r[3] or 0)}
                         for r in conn.execute(
                             "SELECT kind, COUNT(*), SUM(CASE WHEN COALESCE(qid,'')<>'' THEN 1 ELSE 0 END), "
                             "SUM(CASE WHEN COALESCE(wikidata_qid,'')<>'' THEN 1 ELSE 0 END) FROM wb_items "
                             "WHERE COALESCE(canonical,'')='' GROUP BY kind ORDER BY COUNT(*) DESC").fetchall()]
        wb["properties"] = _n(conn, "SELECT COUNT(*) FROM wb_props") if _has_table(conn, "wb_props") else 0
        wb["live"] = wiki_live(wb.get("url", "")) if live and wb.get("url") else None
    out["wikibase"] = wb
    wd: dict = {}
    if _has_table(conn, "wb_wikidata"):
        for r in conn.execute("SELECT status, COUNT(*) FROM wb_wikidata GROUP BY status").fetchall():
            wd[r[0] or "unknown"] = int(r[1])
        wd["checked"] = sum(v for k, v in wd.items() if k != "checked")
        wd["suggested"] = _n(conn, "SELECT COUNT(*) FROM wb_wikidata WHERE COALESCE(suggestion_json,'')<>''")
    if _has_table(conn, "wb_items"):
        wd["enriched"] = _n(conn, "SELECT COUNT(*) FROM wb_items WHERE COALESCE(enrich_json,'')<>''")
    out["wikidata"] = wd
    # makers at a glance (the leaderboards module scores everyone; take its counts)
    try:
        from central import makers
        out["makers"] = makers.leaderboards(conn)["counts"]
    except Exception:  # noqa: BLE001
        out["makers"] = {}
    _CACHE.update({"at": now, "data": out})
    return out


def forget() -> None:
    _CACHE.update({"at": 0.0, "data": None})
    _WIKI.update({"at": 0.0, "data": None})
