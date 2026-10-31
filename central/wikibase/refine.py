"""
central.wikibase.refine
=======================
The OpenRefine phase, in both directions, plus the matching behind the reconciliation
service the API exposes.

Out: ``export_csv(conn, kind)`` writes one row per staged item of a kind with the columns a
reviewer edits in OpenRefine: ``key``, ``label``, ``description``, ``aliases`` (pipe-joined),
``status``, ``wikidata_qid``, ``merge_into`` (empty), ``cluster``, ``canonical``, ``source_table``,
``url``, then one column per property the kind uses (pipe-joined values; item values as their
keys, with the label in brackets). Reconcile ``label`` against Wikidata in OpenRefine, copy the
match into ``wikidata_qid`` (``cell.recon.match.id``), fix labels, descriptions and aliases,
mark rows ``skipped`` or ``ready``, write a key into ``merge_into`` when two rows are the same
thing, export CSV, upload it back.

In: ``import_csv(conn, text)`` applies, per key: ``wikidata_qid``; ``status`` when it is one of
ready / skipped / new / held; ``merge_into`` (the row becomes a duplicate of that key);
``label``, ``description`` and ``aliases`` when they differ from what staging built (kept in
``review_json`` so a re-stage does not undo them); and the property columns of *string*,
*external-id*, *url* and *time* properties when they changed (item-valued columns are not
imported: they are keys, edit those on the site). Nothing is deleted from the wiki.

``manifest()`` builds the manifest OpenRefine's Wikibase extension wants for our wiki, once
the properties exist (it needs the P ids of *instance of* and *subclass of*).

``reconcile(conn, query, type, limit)`` ranks staged items for a query string: exact label or
alias first, then similarity; the service answers OpenRefine's reconciliation calls with it.
"""
from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher

from central.wikibase import store
from central.wikibase.model import (
    KEY_PROP,
    PROP_BY_LABEL,
    WIKIDATA_PROP,
    Claim,
    Value,
    norm,
)

EDIT_COLS = ("key", "label", "description", "aliases", "status", "wikidata_qid", "merge_into", "cluster", "canonical",
             "source_table", "url")
IMPORTABLE_TYPES = ("string", "external-id", "url", "time")
STATUSES_IN = ("ready", "skipped", "new", "held")


def _props_used(items: list) -> list[str]:
    seen: dict[str, int] = {}
    for it in items:
        for c in it.claims:
            seen[c.prop] = seen.get(c.prop, 0) + 1
    return sorted(seen, key=lambda p: (-seen[p], p))


def export_rows(conn, kind: str, status=None) -> tuple[list[str], list[dict]]:
    rows = store.items(conn, kind=kind, status=status)
    items = [store.effective_item(r) for r in rows]
    labels = {r["key"]: r["label"] for r in store.rows(conn.execute("SELECT key, label FROM wb_items"))}
    props = [p for p in _props_used(items) if p not in (KEY_PROP, WIKIDATA_PROP)]
    out = []
    for r, it in zip(rows, items):
        d = {"key": r["key"], "label": it.label, "description": it.description, "aliases": " | ".join(it.aliases),
             "status": r["status"], "wikidata_qid": r["wikidata_qid"] or (it.wikidata or ""), "merge_into": "",
             "cluster": r["cluster"], "canonical": r["canonical"], "source_table": r["source_table"], "url": it.url}
        for p in props:
            vals = []
            for c in it.claims:
                if c.prop != p:
                    continue
                v = c.value
                if v.kind == "item":
                    vals.append(f"{v.value} [{labels.get(v.value, '')}]" if labels.get(v.value) else v.value)
                elif v.kind == "coordinate":
                    vals.append(v.value)
                else:
                    vals.append(str(v.value))
            d[p] = " | ".join(vals)
        out.append(d)
    return list(EDIT_COLS) + props, out


def export_csv(conn, kind: str, status=None) -> str:
    cols, rows = export_rows(conn, kind, status)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _parse_value(prop: str, text: str) -> Value | None:
    p = PROP_BY_LABEL.get(prop)
    if not p or p.datatype not in IMPORTABLE_TYPES:
        return None
    text = text.strip()
    if not text:
        return None
    if p.datatype == "time":
        from central.wikibase.model import time_value
        return time_value(text)
    if p.datatype == "url":
        from central.wikibase.model import url
        return url(text)
    if p.datatype == "external-id":
        from central.wikibase.model import ext
        return ext(text)
    from central.wikibase.model import string
    return string(text)


def import_csv(conn, text: str, log=print) -> dict:
    """Apply an edited CSV. Returns counts of what changed."""
    store.ensure(conn)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "key" not in reader.fieldnames:
        raise ValueError("the CSV needs a 'key' column (export one from this page first)")
    counts = {"rows": 0, "wikidata": 0, "status": 0, "merged": 0, "edits": 0, "unknown": 0, "claims": 0}
    prop_cols = [c for c in reader.fieldnames if c in PROP_BY_LABEL and PROP_BY_LABEL[c].datatype in IMPORTABLE_TYPES]
    for row in reader:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        counts["rows"] += 1
        r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
        if not r:
            counts["unknown"] += 1
            continue
        try:
            review = json.loads(r.get("review_json") or "{}")
        except ValueError:
            review = {}
        built = store.load_item(r)
        fields: dict = {}
        q = (row.get("wikidata_qid") or "").strip()
        if q and re.fullmatch(r"Q\d+", q) and q != (r["wikidata_qid"] or ""):
            fields["wikidata_qid"] = q
            counts["wikidata"] += 1
        st = (row.get("status") or "").strip().lower()
        if st in STATUSES_IN and st != r["status"] and r["status"] not in ("pushed", "changed"):
            fields["status"] = st
            counts["status"] += 1
        merge = (row.get("merge_into") or "").strip()
        if merge and merge != key and store.one(conn.execute("SELECT key FROM wb_items WHERE key=?", (merge,))):
            fields["status"], fields["canonical"] = "duplicate", merge
            fields["cluster"] = "c:" + merge
            fields["notes"] = "merged by the CSV import"
            counts["merged"] += 1
        changed = False
        for col in ("label", "description"):
            v = (row.get(col) or "").strip()
            if v and v != getattr(built, col) and v != review.get(col):
                review[col] = v
                changed = True
        if "aliases" in row and row.get("aliases") is not None:
            al = [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]
            if al != built.aliases and al != review.get("aliases"):
                review["aliases"] = al
                changed = True
        for col in prop_cols:
            raw = row.get(col)
            if raw is None:
                continue
            want = [v.strip() for v in raw.split("|") if v.strip()]
            have = [c.value.value for c in built.claims if c.prop == col]
            if want == have or (not want and not have):
                continue
            add = [Claim(col, pv).to_json() for pv in (_parse_value(col, v) for v in want) if pv]
            review.setdefault("drop", [])
            if col not in review["drop"]:
                review["drop"].append(col)
            review["add"] = [c for c in review.get("add", []) if c.get("p") != col] + add
            changed = True
            counts["claims"] += 1
        if changed:
            fields["review_json"] = json.dumps(review, ensure_ascii=False)
            counts["edits"] += 1
        if fields:
            eff = store.effective_item(dict(r, **{k: v for k, v in fields.items() if k in ("review_json", "wikidata_qid")}))
            fields["content_hash"] = eff.content_hash()
            fields["label"], fields["description"] = eff.label, eff.description
            if r["status"] in ("pushed", "changed") and "status" not in fields:
                fields["status"] = "pushed" if eff.content_hash() == r["pushed_hash"] else "changed"
            store.set_item(conn, key, **fields)
    log(f"CSV import: {counts['rows']} rows; {counts['wikidata']} Wikidata ids, {counts['status']} statuses, "
        f"{counts['merged']} merges, {counts['edits']} edited rows, {counts['claims']} property columns"
        + (f"; {counts['unknown']} keys not in staging" if counts["unknown"] else ""))
    return counts


# --------------------------------------------------------------------------- reconciliation
def _score(q: str, label: str, aliases: list[str]) -> float:
    nq = norm(q)
    if not nq:
        return 0.0
    if nq == norm(label):
        return 100.0
    best = 0.0
    for a in aliases:
        if nq == norm(a):
            return 99.0
        best = max(best, SequenceMatcher(None, nq, norm(a)).ratio())
    best = max(best, SequenceMatcher(None, nq, norm(label)).ratio())
    toks_q, toks_l = set(nq.split()), set(norm(label).split())
    if toks_q and toks_q <= toks_l:
        best = max(best, 0.9)
    return round(best * 100, 1)


def reconcile(conn, query: str, kind: str | None = None, limit: int = 5, properties: list | None = None) -> list[dict]:
    """Candidates for one reconciliation query: ``[{id, name, score, match, type, description, key, qid}]``.
    ``id`` is the wiki item id once the item is pushed, else the staging key (the resolver at
    /api/wikibase/go/<id> opens either)."""
    where = "status NOT IN ('skipped','duplicate')"
    args: list = []
    if kind:
        where += " AND kind=?"
        args.append(kind)
    rows = store.rows(conn.execute(f"SELECT key, kind, label, description, item_json, qid, wikidata_qid FROM wb_items "
                                   f"WHERE {where}", args))
    out = []
    nq = norm(query)
    first = nq[:1]
    for r in rows:
        if first and not (norm(r["label"])[:1] == first or first in norm(r["label"])):
            if nq not in norm(r["label"]) and len(nq) > 3:
                # cheap prefilter; aliases still get a look below only for short lists
                pass
        try:
            aliases = json.loads(r["item_json"]).get("aliases") or []
        except ValueError:
            aliases = []
        s = _score(query, r["label"], aliases)
        if s < 60:
            continue
        out.append({"id": r["qid"] or r["key"], "key": r["key"], "qid": r["qid"], "name": r["label"], "score": s,
                    "match": s >= 99, "type": [{"id": r["kind"], "name": r["kind"]}], "description": r["description"]})
    out.sort(key=lambda c: (-c["score"], c["name"]))
    out = out[:limit]
    if len([c for c in out if c["match"]]) > 1:         # two exact candidates: let the person choose
        for c in out:
            c["match"] = False
    return out


def service_manifest(base_url: str, wiki_url: str) -> dict:
    """The reconciliation service's manifest (W3C Reconciliation API 0.2)."""
    from central.wikibase.model import CLASSES
    return {
        "versions": ["0.2", "0.1"],
        "name": "Glass Database (staging + glass.wikibase.cloud)",
        "identifierSpace": wiki_url.rstrip("/") + "/entity/",
        "schemaSpace": wiki_url.rstrip("/") + "/prop/direct/",
        "view": {"url": base_url.rstrip("/") + "/api/wikibase/go/{{id}}"},
        "preview": {"url": base_url.rstrip("/") + "/api/wikibase/preview/{{id}}", "width": 420, "height": 160},
        "defaultTypes": [{"id": c.slug, "name": c.label} for c in CLASSES if c.slug in
                         ("person", "studio", "school", "museum", "place", "show", "object", "technique")],
        "documentation": base_url.rstrip("/") + "/wikibase.html#openrefine",
    }


def openrefine_manifest(conn, base_url: str, wiki_url: str) -> dict:
    """The manifest for OpenRefine's Wikibase extension (its *Add Wikibase* dialog takes this
    JSON). Needs the properties to exist on the wiki."""
    props = store.props(conn)
    need = {"instance of", "subclass of"}
    missing = [p for p in need if not props.get(p, {}).get("pid")]
    if missing:
        raise LookupError("run Bootstrap first: the manifest needs the P ids of " + ", ".join(missing))
    wiki = wiki_url.rstrip("/")
    return {
        "version": "2.0",
        "mediawiki": {"name": "Glass Database wiki", "root": wiki + "/wiki/", "main_page": wiki + "/wiki/Main_Page",
                      "api": wiki + "/w/api.php"},
        "wikibase": {"site_iri": wiki + "/entity/", "maxlag": 5,
                     "properties": {"instance_of": props["instance of"]["pid"], "subclass_of": props["subclass of"]["pid"]},
                     "constraints": {}},
        "oauth": {"registration_page": wiki + "/wiki/Special:OAuthConsumerRegistration/propose"},
        "entity_types": {"item": {"site_iri": wiki + "/entity/", "reconciliation_endpoint": base_url.rstrip("/") + "/api/wikibase/reconcile",
                                  "mediawiki_api": wiki + "/w/api.php"},
                         "property": {"site_iri": wiki + "/entity/", "mediawiki_api": wiki + "/w/api.php"}},
        "hide_structured_fields_in_mediainfo": True,
    }
