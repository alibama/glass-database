"""
central.wikibase.dedupe
=======================
The same person, studio or place staged twice (the artist list and the legacy sheet, a
studio in the directory and in a submission, a maker named on a piece who is also in the
directory) becomes one wiki item. Clusters are built per kind:

* exact: the same normalised name (accents, case, punctuation and spacing removed), for
  studios the same name *and* the same city when both have one;
* linked: the same website host or the same Instagram handle;
* near: names whose similarity is at least ``FUZZY`` with the same first letter and the same
  set of tokens or one token's difference (``Rae Sutter`` ≈ ``Rae  Sutter``, ``R. Sutter``
  is left alone).

One item per cluster is the **canonical**: the one from the most trusted source
(``stage.SOURCE_PRIORITY``: the directory before the legacy sheet before a submission before
a name on a piece), then the one with the most statements. The others become
``duplicate`` and point at it; their statements and labels are folded into the canonical
(aliases, the union of claims, and each duplicate's own *Glass Database ID*, so every source
row still finds the item). A reviewer can split a cluster from the page (status ``ready`` on
the duplicate and a *different from* between the two).

Nothing is ever deleted from the wiki by this module; it only decides what to push.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from urllib.parse import urlparse

from central.wikibase import store
from central.wikibase.model import KEY_PROP, Item, ext, norm

FUZZY = 0.94
KINDS = ("person", "studio", "place", "school", "museum", "funder", "show")
TITLES = re.compile(r"^(mr|mrs|ms|dr|prof|sir|dame)\.?\s+", re.I)


def _priority(row: dict) -> tuple:
    from central.wikibase.stage import SOURCE_PRIORITY
    it = store.load_item(row)
    return (SOURCE_PRIORITY.get(row["source_table"], 50), -len(it.claims), -len(it.description), row["key"])


def _name_key(label: str) -> str:
    s = TITLES.sub("", norm(label))
    return " ".join(sorted(s.split())) if s else ""


def _host(u: str) -> str:
    try:
        h = (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _links(it: Item) -> set[str]:
    out = set()
    for c in it.claims:
        if c.prop == "official website":
            h = _host(c.value.value)
            if h and h not in ("example.com", "instagram.com", "facebook.com", "linktr.ee", "etsy.com"):
                out.add("web:" + h)
        elif c.prop == "Instagram username":
            out.add("ig:" + c.value.value.lower())
    return out


def _city(it: Item) -> str:
    for c in it.claims:
        if c.prop == "located in":
            return c.value.value
    return ""


class Union:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster(rows: list[dict]) -> dict[str, list[dict]]:
    """Rows of one kind → {cluster id: rows}; singletons included."""
    u = Union()
    items = {r["key"]: store.load_item(r) for r in rows}
    by_name: dict[str, list[str]] = defaultdict(list)
    by_link: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        it = items[r["key"]]
        u.find(r["key"])
        nk = _name_key(it.label)
        if nk:
            if it.kind == "studio":
                nk = nk + "|" + _city(it)
            by_name[nk].append(r["key"])
        for lk in _links(it):
            by_link[lk].append(r["key"])
    for keys in list(by_name.values()) + list(by_link.values()):
        for k in keys[1:]:
            u.join(keys[0], k)
    # studios with the same name and no city on one side join the one that has a city
    if rows and items[rows[0]["key"]].kind == "studio":
        plain: dict[str, list[str]] = defaultdict(list)
        for nk, keys in by_name.items():
            plain[nk.split("|")[0]] += keys
        for keys in plain.values():
            cities = {_city(items[k]) for k in keys}
            if len(cities - {""}) <= 1:
                for k in keys[1:]:
                    u.join(keys[0], k)
    # near names: same first letter, close ratio, token sets differ by at most one token
    names = sorted({(_name_key(items[k].label), k) for k in items if _name_key(items[k].label)})
    buckets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for nk, k in names:
        buckets[nk[0]].append((nk, k))
    for group in buckets.values():
        for i in range(len(group)):
            for j in range(i + 1, min(len(group), i + 400)):
                a, ka = group[i]
                b, kb = group[j]
                if a == b or u.find(ka) == u.find(kb):
                    continue
                ta, tb = set(a.split()), set(b.split())
                if len(ta ^ tb) > 1 and not (ta <= tb or tb <= ta):
                    continue
                if SequenceMatcher(None, a, b).ratio() >= FUZZY:
                    u.join(ka, kb)
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[u.find(r["key"])].append(r)
    return out


def run(conn, log=print, params: dict | None = None) -> dict:
    """The *dedupe* job over the staged items. Returns the summary."""
    store.ensure(conn)
    summary: dict = {"clusters": 0, "duplicates": 0, "by_kind": {}}
    for kind in KINDS:
        rows = store.items(conn, kind=kind)
        rows = [r for r in rows if r["status"] not in ("skipped",)]
        if not rows:
            continue
        groups = cluster(rows)
        n_dup = 0
        n_cl = 0
        for root, members in groups.items():
            if len(members) < 2:
                # a former duplicate whose cluster dissolved goes back to new/ready
                r = members[0]
                if r["status"] == "duplicate":
                    store.set_item(conn, r["key"], status="ready" if r["qid"] else "new", canonical="", cluster="")
                elif r["cluster"]:
                    store.set_item(conn, r["key"], cluster="", canonical="")
                continue
            n_cl += 1
            members.sort(key=_priority)
            # a member already on the wiki is the canonical, whatever its source: never make a second item
            pushed = [m for m in members if m["qid"]]
            if pushed:
                members.sort(key=lambda m: (0 if m["qid"] else 1, _priority(m)))
            canon = members[0]
            kept = [m for m in members if m["status"] == "split"]     # reviewer said: keep apart
            cid = "c:" + canon["key"]
            canon_item = store.effective_item(canon)
            extra_keys: list[str] = []
            for m in members:
                if m["key"] == canon["key"] or m in kept:
                    store.set_item(conn, m["key"], cluster=cid)
                    continue
                dup = store.load_item(m)
                for c in dup.claims:
                    if c.prop in (KEY_PROP,):
                        continue
                    if not any(x.sig() == c.sig() for x in canon_item.claims):
                        canon_item.claims.append(c)
                for a in [dup.label, *dup.aliases]:
                    if a and a != canon_item.label and a not in canon_item.aliases:
                        canon_item.aliases.append(a)
                extra_keys.append(m["key"])
                if m["qid"] and m["qid"] != canon["qid"]:
                    note = f"also on the wiki as {m['qid']}: mark one 'different from' the other by hand"
                    log(f"  {kind}: {m['key']} and {canon['key']} are both on the wiki ({m['qid']}, {canon['qid']})")
                else:
                    note = f"same {kind} as {canon['key']}"
                store.set_item(conn, m["key"], status="duplicate", cluster=cid, canonical=canon["key"], notes=note)
                n_dup += 1
            for k in extra_keys:
                canon_item.add(KEY_PROP, ext(k))
            base = store.load_item(canon)
            base.aliases, base.claims = canon_item.aliases, [c for c in canon_item.claims]
            store.set_item(conn, canon["key"], cluster=cid, canonical="",
                           item_json=json.dumps(base.to_json(), ensure_ascii=False),
                           content_hash=store.effective_item(dict(canon, item_json=json.dumps(base.to_json()))).content_hash())
            if canon["status"] == "pushed" and canon["pushed_hash"] != store.effective_item(
                    dict(canon, item_json=json.dumps(base.to_json()))).content_hash():
                store.set_item(conn, canon["key"], status="changed")
        summary["by_kind"][kind] = {"items": len(rows), "clusters": n_cl, "duplicates": n_dup}
        summary["clusters"] += n_cl
        summary["duplicates"] += n_dup
        log(f"  {kind}: {len(rows)} items, {n_cl} clusters with more than one, {n_dup} marked duplicate")
    log(f"{summary['duplicates']} duplicates folded into {summary['clusters']} canonical items")
    return summary


def clusters(conn, kind: str | None = None, limit: int = 200) -> list[dict]:
    """The clusters with more than one member, for the review page."""
    where = "cluster<>''" + (" AND kind=?" if kind else "")
    rows = store.rows(conn.execute(f"SELECT key, kind, label, description, status, cluster, canonical, qid, source_table, "
                                   f"wikidata_qid FROM wb_items WHERE {where} ORDER BY cluster, status DESC, key",
                                   (kind,) if kind else ()))
    out: dict[str, dict] = {}
    for r in rows:
        c = out.setdefault(r["cluster"], {"id": r["cluster"], "kind": r["kind"], "members": []})
        c["members"].append(r)
    cl = [c for c in out.values() if len(c["members"]) > 1]
    cl.sort(key=lambda c: (-len(c["members"]), c["id"]))
    return cl[:limit]


def split(conn, key: str) -> dict | None:
    """A reviewer says this duplicate is a different thing: it stands on its own again and
    both items carry *different from* the other."""
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    if not r or not r["canonical"]:
        return r
    canon = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (r["canonical"],)))
    me = store.load_item(r)
    me.add("different from", __import__("central.wikibase.model", fromlist=["item"]).item(r["canonical"]))
    store.set_item(conn, key, status="split", canonical="", item_json=json.dumps(me.to_json(), ensure_ascii=False),
                   notes="kept apart by a reviewer")
    if canon:
        ci = store.load_item(canon)
        ci.claims = [c for c in ci.claims if not (c.prop == KEY_PROP and c.value.value == key)]
        ci.aliases = [a for a in ci.aliases if a != me.label]
        ci.add("different from", __import__("central.wikibase.model", fromlist=["item"]).item(key))
        store.set_item(conn, canon["key"], item_json=json.dumps(ci.to_json(), ensure_ascii=False),
                       status="changed" if canon["qid"] else canon["status"])
    return store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))


def summary(conn) -> dict:
    c = Counter(r[0] for r in conn.execute("SELECT kind FROM wb_items WHERE status='duplicate'"))
    return {"duplicates": sum(c.values()), "by_kind": dict(c),
            "clusters": conn.execute("SELECT COUNT(DISTINCT cluster) FROM wb_items WHERE cluster<>''").fetchone()[0]}
