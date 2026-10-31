"""
central.wikibase.sync
=====================
Reads, not writes: what the wiki says about what we pushed, and whether we can reach it.

* ``check``: the *check* job and the admin page's **Test** button. Site info, the bot's sign-in
  and rights (when credentials are saved), how many of our properties exist, how many Glass
  Database IDs the wiki holds, whether SPARQL answers. Nothing is written.
* ``pull``: the *pull* job. Fetches every pushed item from the wiki (50 a request) into
  ``wb_cache`` (its JSON, label, when fetched) and compares it with what we sent: a label
  changed by hand, statements added on the wiki, an item that vanished. The cache is the
  seam the rebuild reads from: the pages keep reading SQLite, and this table is the wiki's
  copy of the public record, refreshed after every push and by a nightly ``pull``.
* ``changes``: what the next push would do, for the page (counts of new / changed / failed).

Incoming data on the site (forms, Glowtbook publishes, event registrations) reaches the wiki
through *stage* → *push*: staging is idempotent and reads the approval gate, so a newly
approved row is simply a ``new`` item the next time stage runs, and a changed row a
``changed`` one. ``docs/WIKIBASE.md`` has the schedule.
"""
from __future__ import annotations

import json
from collections import Counter

from central.wikibase import store
from central.wikibase.client import WikibaseError
from central.wikibase.model import KEY_PROP, LANG, PROPERTIES
from central.wikibase.push import make_api


def check(conn, log=print, params: dict | None = None) -> dict:
    store.ensure(conn)
    out: dict = {"ok": False}
    api = make_api(conn, dry_run=True, log=log)
    try:
        out["site"] = api.site_info()
        log(f"wiki: {out['site'].get('sitename')} ({out['site'].get('generator')}) at {api.base}")
        ext = out["site"].get("extensions") or []
        out["wikibase"] = any("Wikibase" in e for e in ext)
        if api.user and api.password:
            try:
                who = api.login()
                out["login"] = api.whoami()
                out["login"]["name"] = who
                log(f"signed in as {who}: groups {', '.join(out['login'].get('groups') or []) or 'none'}; "
                    f"edit {'yes' if out['login'].get('can_edit') else 'NO'}; "
                    f"create properties {'yes' if out['login'].get('can_create_properties') else 'NO'}")
            except WikibaseError as e:
                out["login"] = {"error": str(e)}
                log(f"sign-in failed: {e}")
        else:
            out["login"] = {"error": "no bot credentials saved"}
            log("no bot credentials saved (reads only)")
        props = api.properties()
        have = [p.label for p in PROPERTIES if p.label in props]
        out["properties"] = {"on_wiki": len(props), "ours_present": len(have), "ours_total": len(PROPERTIES)}
        log(f"properties on the wiki: {len(props)}; of ours: {len(have)}/{len(PROPERTIES)}")
        for label, p in props.items():
            store.remember_prop(conn, label, p["pid"], p["datatype"])
        key_pid = props.get(KEY_PROP, {}).get("pid")
        if key_pid:
            keys = api.key_map(key_pid)
            out["keys_on_wiki"] = len(keys)
            log(f"Glass Database IDs on the wiki: {len(keys)}")
        try:
            api.sparql("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o } LIMIT 1")
            out["sparql"] = True
            log("SPARQL answers")
        except Exception as e:  # noqa: BLE001
            out["sparql"] = False
            log(f"SPARQL did not answer ({e})")
        out["ok"] = bool(out.get("wikibase")) and "error" not in (out.get("login") or {})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
        log(f"could not reach the wiki: {e}")
    finally:
        api.close()
    return out


def pull(conn, log=print, params: dict | None = None) -> dict:
    """Fetch what we pushed and note drift. ``params``: limit."""
    store.ensure(conn)
    params = params or {}
    rows = store.rows(conn.execute("SELECT key, kind, label, qid, item_json, pushed_at FROM wb_items WHERE qid<>'' "
                                   "ORDER BY pushed_at DESC" + (f" LIMIT {int(params['limit'])}" if params.get("limit") else "")))
    api = make_api(conn, dry_run=True, log=log)
    drift: Counter = Counter()
    notes = []
    try:
        key_pid = store.props(conn).get(KEY_PROP, {}).get("pid")
        qids = [r["qid"] for r in rows]
        log(f"fetching {len(qids)} items from {api.base}")
        ents = api.get_entities(qids)
        now = store.now()
        for r in rows:
            e = ents.get(r["qid"])
            if e is None:
                continue
            if not e:
                drift["missing"] += 1
                notes.append(f"{r['key']}: {r['qid']} is gone from the wiki")
                store.set_item(conn, r["key"], notes=f"{r['qid']} is gone from the wiki")
                conn.execute("DELETE FROM wb_cache WHERE qid=?", (r["qid"],))
                continue
            label = ((e.get("labels") or {}).get(LANG) or {}).get("value") or ""
            conn.execute("INSERT INTO wb_cache (qid,key,kind,label,json,fetched_at) VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(qid) DO UPDATE SET key=excluded.key, kind=excluded.kind, label=excluded.label, "
                         "json=excluded.json, fetched_at=excluded.fetched_at",
                         (r["qid"], r["key"], r["kind"], label, json.dumps(e, ensure_ascii=False), now))
            ours = json.loads(r["item_json"])
            if label and label != r["label"]:
                drift["label"] += 1
                notes.append(f"{r['key']}: label on the wiki is “{label}”, ours “{r['label']}”")
            n_live = sum(len(v) for v in (e.get("claims") or {}).values())
            n_ours = len(ours.get("claims") or []) + 1 + (1 if ours.get("cls") else 0)
            if n_live > n_ours + 1:
                drift["added"] += 1
            if key_pid and not (e.get("claims") or {}).get(key_pid):
                drift["no_key"] += 1
                notes.append(f"{r['key']}: {r['qid']} lost its Glass Database ID")
        conn.commit()
        log(f"cached {len(ents)} items; drift: " + (", ".join(f"{k} {n}" for k, n in drift.items()) or "none"))
        for n in notes[:30]:
            log("  " + n)
        return {"fetched": len(ents), "drift": dict(drift), "notes": notes[:200]}
    finally:
        api.close()


def changes(conn) -> dict:
    """Counts the page shows: what a push would do now."""
    c = store.counts(conn)
    return {"new": c["status"].get("new", 0), "ready": c["status"].get("ready", 0),
            "changed": c["status"].get("changed", 0), "failed": c["status"].get("failed", 0),
            "held": c["status"].get("held", 0), "orphan": c["status"].get("orphan", 0),
            "pushed": c["status"].get("pushed", 0), "duplicate": c["status"].get("duplicate", 0),
            "pending": c["pending"], "total": c["total"], "properties": c["properties"]}
