"""
central.wikibase.push
=====================
Writes the staged items to the wiki in passes, the way lexflow's pusher does:

* **Bootstrap**: the properties. Every ``model.PROPERTIES`` entry that has no property with
  that label on the wiki is created with its datatype; then each property gets its
  *equivalent property* (the Wikidata twin's URL) and *formatter URL* statements when it
  lacks them. Labels → P ids are kept in ``wb_props``.
* **Pass 1, entities**: every pushable item that has no wiki id yet is created bare: labels,
  description, aliases, its *Glass Database ID* (and those of the rows folded into it) and
  *instance of* when the class item already has an id. Find-before-create: the id in
  ``wb_items``, then the wiki's map of every Glass Database ID (SPARQL), then the API's
  "already has label" answer, whose item is reused only when it carries our key; otherwise
  the key's tail is appended to the description and the create is retried once.
* **Pass 2, statements**: every pushable item gets its full statement set, with a reference
  on each statement (*stated in* Glass Database, *reference URL* the page on the site,
  *retrieved* today). An item we created is written with ``clear`` so a re-run replaces
  rather than appends; an item that existed on the wiki before us is read first and only
  what it lacks is appended. A successful write stores the item's content hash and marks it
  ``pushed``; a failed one is marked ``failed`` with the error, and the run goes on.

``dry_run`` (the default) performs no POST: new entities get placeholder ids, every body is
built as it would be sent, counts and the first bodies per kind are reported, and the whole
plan is written to ``data/wikibase/plan-<run>.json``. Writes are one at a time with a pause
between them (``wikibase_pause`` setting, 1 s by default) and honour ``maxlag``.

Never deletes or merges anything on the wiki.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from central.wikibase import store
from central.wikibase.client import Wikibase, WikibaseError, error_code, owner_of
from central.wikibase.model import (
    EARTH,
    GREGORIAN,
    INSTANCE_OF,
    KEY_PROP,
    KIND_ORDER,
    LABEL_MAX,
    LANG,
    PROPERTIES,
    STRING_MAX,
    URL_MAX,
    WIKIDATA_ENTITY,
    Claim,
    Item,
    Value,
    ext,
    ident_of,
    item,
    key_for,
    today,
    url,
)

Log = Callable[[str], None]
OUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "wikibase"
DATATYPES = {"item": "wikibase-item", "string": "string", "url": "url", "external-id": "external-id", "time": "time",
             "quantity": "quantity", "monolingualtext": "monolingualtext", "globe-coordinate": "globe-coordinate"}
EXAMPLES = 2
SOURCE_KEY = key_for("source", "glass-database")


class Unresolved(KeyError):
    """An item key or property label that has no id yet."""


# --------------------------------------------------------------------------- snaks
def time_dv(v: Value) -> dict:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v.value)
    if not m:
        raise ValueError(f"not a date: {v.value!r}")
    p = int(v.precision or 11)
    return {"time": f"+{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
            "precision": p, "calendarmodel": GREGORIAN}


def amount(s: str) -> str:
    s = str(s).strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        raise ValueError(f"not a quantity: {s!r}")
    return s if s[0] in "+-" else "+" + s


def snak(pid: str, v: Value, qid_of: Callable[[str], str], entity_base: str) -> dict:
    k = v.kind
    if k == "item":
        q = qid_of(v.value)
        dv = {"type": "wikibase-entityid", "value": {"entity-type": "item", "numeric-id": int(q[1:]), "id": q}}
    elif k in ("string", "external-id"):
        dv = {"type": "string", "value": str(v.value)[:STRING_MAX]}
    elif k == "url":
        dv = {"type": "string", "value": str(v.value)[:URL_MAX]}
    elif k == "time":
        dv = {"type": "time", "value": time_dv(v)}
    elif k == "quantity":
        unit = entity_base + qid_of(v.unit) if v.unit else "1"
        dv = {"type": "quantity", "value": {"amount": amount(v.value), "unit": unit}}
    elif k in ("monolingual", "monolingualtext"):
        dv = {"type": "monolingualtext", "value": {"text": str(v.value)[:STRING_MAX], "language": v.lang or LANG}}
    elif k == "coordinate":
        lat, lng = (float(x) for x in v.value.split(","))
        dv = {"type": "globe-coordinate", "value": {"latitude": lat, "longitude": lng, "precision": 0.0001, "globe": EARTH}}
    else:
        raise ValueError(f"unknown value kind {k!r}")
    return {"snaktype": "value", "property": pid, "datavalue": dv}


def snak_sig(s: dict) -> tuple:
    dv = s.get("datavalue") or {}
    t, v = dv.get("type"), dv.get("value")
    if t == "wikibase-entityid":
        return (s.get("property"), "entity", (v or {}).get("id"))
    if t == "time":
        return (s.get("property"), "time", (v or {}).get("time"), (v or {}).get("precision"))
    if t == "quantity":
        a = str((v or {}).get("amount", ""))
        try:
            a = f"{float(a):g}"
        except ValueError:
            pass
        return (s.get("property"), "quantity", a, (v or {}).get("unit"))
    if t == "monolingualtext":
        return (s.get("property"), "mono", (v or {}).get("text"), (v or {}).get("language"))
    if t == "globe-coordinate":
        return (s.get("property"), "coord", round(float((v or {}).get("latitude", 0)), 4),
                round(float((v or {}).get("longitude", 0)), 4))
    return (s.get("property"), "string", v if isinstance(v, str) else json.dumps(v, sort_keys=True))


@dataclass
class Write:
    pass_no: int
    kind: str
    key: str
    action: str            # create | update | clear | skip-exists | skip-current | skip-unresolved | fail
    qid: str | None = None
    note: str = ""
    error: str = ""
    code: str = ""
    body: dict | None = None
    placeholder: bool = False

    def row(self) -> dict:
        d = {"pass": self.pass_no, "kind": self.kind, "key": self.key, "action": self.action, "qid": self.qid,
             "note": self.note, "error": self.error, "code": self.code, "placeholder": self.placeholder}
        if self.body is not None:
            d["claims"] = len(self.body.get("claims") or [])
        return d


# --------------------------------------------------------------------------- the pusher
class Pusher:
    def __init__(self, conn, api: Wikibase, log: Log = print, *, dry_run: bool = True, public_base: str = "",
                 limit: int | None = None, kinds: list[str] | None = None, keys: list[str] | None = None):
        self.conn, self.api, self.log, self.dry_run = conn, api, log, dry_run
        self.base = public_base.rstrip("/")
        self.limit, self.kinds, self.keys = limit, kinds, keys
        self.pids: dict[str, str] = {}
        self.pdatatypes: dict[str, str] = {}
        self.qids: dict[str, str] = {}
        self.canonical: dict[str, str] = {}
        self.ours: set[str] = set()            # keys whose wiki item we created (we may clear it)
        self.placeholders: set[str] = set()
        self.live: dict[str, dict] = {}
        self.writes: list[Write] = []
        self.failures: Counter = Counter()
        self.dropped: list[str] = []
        self.rows: list[dict] = []
        self.items: dict[str, Item] = {}
        self.t0 = time.monotonic()

    # ---- ids
    def load_ids(self, live: bool = True) -> None:
        for label, r in store.props(self.conn).items():
            if r.get("pid"):
                self.pids[label] = r["pid"]
                self.pdatatypes[label] = r.get("datatype") or ""
        for r in store.rows(self.conn.execute("SELECT key, qid FROM wb_items WHERE qid<>''")):
            self.qids[r["key"]] = r["qid"]
            self.ours.add(r["key"])
        # a statement that points at a duplicate points at its canonical instead
        self.canonical = {r["key"]: r["canonical"] for r in store.rows(
            self.conn.execute("SELECT key, canonical FROM wb_items WHERE status='duplicate' AND canonical<>''"))}
        if not live:
            return
        try:
            for label, p in self.api.properties().items():
                if label not in self.pids:
                    self.pids[label] = p["pid"]
                    self.pdatatypes[label] = p["datatype"]
                    store.remember_prop(self.conn, label, p["pid"], p["datatype"])
            self.log(f"live: {len(self.pids)} properties known")
        except Exception as e:  # noqa: BLE001
            self.log(f"could not read the wiki's properties ({e}); going on with the local table")
        key_pid = self.pids.get(KEY_PROP)
        if key_pid:
            try:
                live_keys = self.api.key_map(key_pid)
                n = 0
                for key, q in live_keys.items():
                    if key not in self.qids:
                        self.qids[key] = q
                        n += 1
                        r = store.one(self.conn.execute("SELECT key FROM wb_items WHERE key=?", (key,)))
                        if r and not self.dry_run:
                            store.set_item(self.conn, key, qid=q)
                self.log(f"live: {len(live_keys)} Glass Database IDs on the wiki ({n} new to us)")
            except Exception as e:  # noqa: BLE001
                self.log(f"could not read the wiki's key map ({e})")

    def pid(self, label: str) -> str:
        p = self.pids.get(label)
        if not p:
            raise Unresolved(f"property {label!r} has no id yet")
        return p

    def qid_of(self, key: str) -> str:
        if re.fullmatch(r"Q\d+", key or ""):
            return key
        seen = 0
        while key in getattr(self, "canonical", {}) and seen < 5:     # a duplicate → its canonical
            key = self.canonical[key]
            seen += 1
        q = self.qids.get(key)
        if not q:
            raise Unresolved(f"item {key!r} has no id yet")
        return q

    def placeholder(self, key: str, kind: str = "item") -> str:
        q = self.api.placeholder(kind)
        if kind == "item":
            self.qids[key] = q
        else:
            self.pids[key] = q
        self.placeholders.add(key)
        return q

    def record(self, w: Write) -> Write:
        self.writes.append(w)
        if w.action == "fail":
            self.failures[w.code or "error"] += 1
        return w

    # ---- bodies
    def statement(self, c: Claim, dropped: list[str], with_reference: str = "") -> dict:
        st = {"mainsnak": snak(self.pid(c.prop), c.value, self.qid_of, self.api.entity_base), "type": "statement",
              "rank": "normal"}
        if c.qualifiers:
            quals: dict[str, list] = {}
            order: list[str] = []
            for q, v in c.qualifiers:
                try:
                    pid = self.pid(q)
                    s = snak(pid, v, self.qid_of, self.api.entity_base)
                except (Unresolved, ValueError) as e:
                    dropped.append(f"{c.prop} qualifier {q}: {e}")
                    continue
                quals.setdefault(pid, []).append(s)
                if pid not in order:
                    order.append(pid)
            if quals:
                st["qualifiers"], st["qualifiers-order"] = quals, order
        refs = list(c.references)
        if with_reference:
            refs.append([("stated in", item(SOURCE_KEY)), ("reference URL", url(with_reference)),
                         ("retrieved", Value("time", today(), precision=11))])
        out_refs = []
        for ref in refs:
            snaks: dict[str, list] = {}
            order = []
            for q, v in ref:
                try:
                    pid = self.pid(q)
                    s = snak(pid, v, self.qid_of, self.api.entity_base)
                except (Unresolved, ValueError):
                    continue
                snaks.setdefault(pid, []).append(s)
                if pid not in order:
                    order.append(pid)
            if snaks:
                out_refs.append({"snaks": snaks, "snaks-order": order})
        if out_refs:
            st["references"] = out_refs
        return st

    def labels_body(self, it: Item, add_aliases: bool = False) -> dict:
        body: dict[str, Any] = {"labels": {LANG: {"language": LANG, "value": it.label[:LABEL_MAX]}}}
        if it.description:
            body["descriptions"] = {LANG: {"language": LANG, "value": it.description[:LABEL_MAX]}}
        aliases = [a for a in dict.fromkeys(it.aliases) if a and a != it.label][:30]
        if aliases:
            body["aliases"] = {LANG: [dict({"language": LANG, "value": a[:LABEL_MAX]}, **({"add": ""} if add_aliases else {}))
                                      for a in aliases]}
        return body

    def key_claims(self, it: Item) -> list[Claim]:
        keys = [it.key] + [c.value.value for c in it.claims if c.prop == KEY_PROP and c.value.value != it.key]
        return [Claim(KEY_PROP, ext(k)) for k in dict.fromkeys(keys)]

    def bare_body(self, it: Item) -> dict:
        body = self.labels_body(it)
        claims = [self.statement(c, []) for c in self.key_claims(it)]
        if it.cls:
            try:
                claims.append(self.statement(Claim(INSTANCE_OF, item(it.cls)), []))
            except Unresolved:
                pass
        body["claims"] = claims
        return body

    def full_body(self, it: Item) -> tuple[dict, list[str]]:
        dropped: list[str] = []
        body = self.labels_body(it)
        claims = [self.statement(c, dropped) for c in self.key_claims(it)]
        if it.cls:
            try:
                claims.append(self.statement(Claim(INSTANCE_OF, item(it.cls)), dropped))
            except Unresolved as e:
                dropped.append(f"instance of: {e}")
        for c in it.claims:
            if c.prop == KEY_PROP:
                continue
            try:
                claims.append(self.statement(c, dropped, with_reference=it.url))
            except (Unresolved, ValueError) as e:
                dropped.append(f"{c.prop}: {e}")
        body["claims"] = claims
        return body, dropped

    def append_body(self, it: Item, qid: str) -> tuple[dict, list[str]]:
        full, dropped = self.full_body(it)
        live = self.live.get(qid)
        if live is None:
            self.live.update(self.api.get_entities([qid]))
            live = self.live.get(qid) or {}
        body: dict[str, Any] = {}
        if not (live.get("labels") or {}).get(LANG):
            body["labels"] = full["labels"]
        if full.get("descriptions") and not (live.get("descriptions") or {}).get(LANG):
            body["descriptions"] = full["descriptions"]
        have_al = {a["value"] for a in (live.get("aliases") or {}).get(LANG, [])}
        live_label = ((live.get("labels") or {}).get(LANG) or {}).get("value")
        new_al = [a for a in it.aliases if a not in have_al and a != live_label and a != it.label]
        if new_al:
            body["aliases"] = {LANG: [{"language": LANG, "value": a[:LABEL_MAX], "add": ""} for a in new_al]}
        have = {snak_sig(s.get("mainsnak") or {}) for ss in (live.get("claims") or {}).values() for s in ss}
        missing = [s for s in full["claims"] if snak_sig(s["mainsnak"]) not in have]
        if missing:
            body["claims"] = missing
        return body, dropped

    # ---- bootstrap: properties
    def bootstrap(self) -> None:
        self.log("properties")
        for p in PROPERTIES:
            if p.label in self.pids:
                self.record(Write(0, "property", p.label, "skip-exists", qid=self.pids[p.label]))
                continue
            body = {"datatype": DATATYPES[p.datatype], "labels": {LANG: {"language": LANG, "value": p.label}},
                    "descriptions": {LANG: {"language": LANG, "value": p.description[:LABEL_MAX]}}}
            w = Write(0, "property", p.label, "create", body=body)
            if self.dry_run:
                w.qid, w.placeholder = self.placeholder(p.label, "property"), True
                self.record(w)
                continue
            try:
                res = self.api.edit(body, new="property", summary=f"Glass Database: property {p.label}")
                w.qid = res["entity"]["id"]
                self.pids[p.label], self.pdatatypes[p.label] = w.qid, p.datatype
                store.remember_prop(self.conn, p.label, w.qid, p.datatype)
                self.log(f"  created {w.qid} {p.label}")
            except WikibaseError as e:
                other = owner_of(str(e))
                if other:
                    w.action, w.qid, w.note = "skip-exists", other, "found by label clash"
                    self.pids[p.label] = other
                    store.remember_prop(self.conn, p.label, other, p.datatype)
                else:
                    w.action, w.error, w.code = "fail", str(e)[:300], error_code(e)
                    self.log(f"  property {p.label}: {e}")
            self.record(w)
        # statements on the properties: equivalent property, formatter URL
        pids = [self.pids[p.label] for p in PROPERTIES if p.label in self.pids and p.label not in self.placeholders]
        if pids and not self.dry_run:
            try:
                self.live.update(self.api.get_entities(pids))
            except Exception as e:  # noqa: BLE001
                self.log(f"  could not read the properties' statements ({e})")
        for p in PROPERTIES:
            if p.label not in self.pids:
                continue
            pid = self.pids[p.label]
            claims = []
            try:
                if p.wikidata:
                    claims.append(self.statement(Claim("equivalent property", url(WIKIDATA_ENTITY + p.wikidata)), []))
                if p.formatter:
                    claims.append(self.statement(Claim("formatter URL", Value("string", p.formatter)), []))
            except Unresolved as e:
                self.record(Write(0, "property", p.label, "skip-unresolved", qid=pid, note=str(e)))
                continue
            if not claims:
                continue
            live = self.live.get(pid) or {}
            have = {snak_sig(s.get("mainsnak") or {}) for ss in (live.get("claims") or {}).values() for s in ss}
            missing = [s for s in claims if snak_sig(s["mainsnak"]) not in have] if live else claims
            if not missing:
                self.record(Write(0, "property", p.label, "skip-current", qid=pid))
                continue
            w = Write(0, "property", p.label, "update", qid=pid, body={"claims": missing},
                      placeholder=p.label in self.placeholders)
            if not self.dry_run:
                try:
                    self.api.edit(w.body, qid=pid, summary=f"Glass Database: property {p.label}, twins and formatter")
                except WikibaseError as e:
                    w.action, w.error, w.code = "fail", str(e)[:300], error_code(e)
                    self.log(f"  property {p.label}: {e}")
            self.record(w)
        self.log(f"  {sum(1 for w in self.writes if w.kind == 'property' and w.action == 'create')} to create, "
                 f"{sum(1 for w in self.writes if w.kind == 'property' and w.action == 'update')} to update")

    # ---- selection
    def select(self) -> None:
        rows = store.items(self.conn, status=store.PUSHABLE)
        if self.kinds:
            rows = [r for r in rows if r["kind"] in self.kinds]
        if self.keys:
            want = set(self.keys)
            rows = [r for r in rows if r["key"] in want]
        order = {k: i for i, k in enumerate(KIND_ORDER)}
        rows.sort(key=lambda r: (order.get(r["kind"], 99), r["key"]))
        if self.limit:
            rows = rows[: self.limit]
        self.rows = rows
        self.items = {r["key"]: store.effective_item(r) for r in rows}
        # every class and vocabulary item the selection points at must have an id: pull the
        # pushed ones' ids in (they are in self.qids already) and warn about the missing ones
        c = Counter(r["kind"] for r in rows)
        self.log(f"{len(rows)} items to push: " + ", ".join(f"{k} {n}" for k, n in sorted(c.items())))

    # ---- pass 1
    def pass1(self) -> None:
        self.log("pass 1: entities")
        for n, r in enumerate(self.rows, 1):
            it = self.items[r["key"]]
            self.create(r, it)
            if n % 100 == 0:
                self.log(f"  pass 1: {n}/{len(self.rows)}")

    def create(self, r: dict, it: Item) -> Write:
        key = it.key
        if key in self.qids:
            return self.record(Write(1, it.kind, key, "skip-exists", qid=self.qids[key],
                                     note="pass 2 " + ("replaces" if key in self.ours else "appends")))
        try:
            body = self.bare_body(it)
        except (Unresolved, ValueError) as e:
            return self.record(Write(1, it.kind, key, "fail", error=str(e)[:300], code="error"))
        w = Write(1, it.kind, key, "create", body=body)
        if self.dry_run:
            w.qid, w.placeholder = self.placeholder(key), True
            return self.record(w)
        suffixed = False
        while True:
            try:
                res = self.api.edit(body, new="item", summary=f"Glass Database: {it.kind} {key} ({it.source_table})")
                w.qid = res["entity"]["id"]
                self.qids[key] = w.qid
                self.ours.add(key)
                store.set_item(self.conn, key, qid=w.qid, error="")
                break
            except WikibaseError as e:
                other = owner_of(str(e))
                if other and self.live_key_of(other) == key:
                    w.action, w.qid, w.note = "skip-exists", other, "found by label clash; pass 2 appends"
                    self.qids[key] = other
                    store.set_item(self.conn, key, qid=other)
                    break
                if other and not suffixed:
                    tail = ident_of(key)[-12:]
                    desc = (it.description or "")[: LABEL_MAX - len(tail) - 3]
                    body["descriptions"] = {LANG: {"language": LANG, "value": f"{desc} [{tail}]"}}
                    it.description = f"{desc} [{tail}]"
                    w.note, suffixed = f"description suffixed after a label clash with {other}", True
                    continue
                w.action, w.error, w.code = "fail", str(e)[:300], error_code(e)
                store.set_item(self.conn, key, status="failed", error=w.error)
                self.log(f"  {key}: {e}")
                break
            except (KeyError, ValueError) as e:
                w.action, w.error, w.code = "fail", str(e)[:300], "error"
                store.set_item(self.conn, key, status="failed", error=w.error)
                break
        return self.record(w)

    def live_key_of(self, qid: str) -> str | None:
        try:
            self.live.update(self.api.get_entities([qid]))
        except Exception:  # noqa: BLE001
            return None
        ent = self.live.get(qid) or {}
        pid = self.pids.get(KEY_PROP)
        for s in (ent.get("claims") or {}).get(pid or "", []):
            dv = (s.get("mainsnak") or {}).get("datavalue") or {}
            if dv.get("value"):
                return dv["value"]
        return None

    # ---- pass 2
    def pass2(self) -> None:
        self.log("pass 2: statements")
        if not self.dry_run:
            foreign = [self.qids[r["key"]] for r in self.rows if r["key"] in self.qids and r["key"] not in self.ours]
            if foreign:
                try:
                    self.live.update(self.api.get_entities(foreign))
                except Exception as e:  # noqa: BLE001
                    self.log(f"  could not read {len(foreign)} existing items ({e})")
        for n, r in enumerate(self.rows, 1):
            it = self.items[r["key"]]
            self.update(r, it)
            if n % 100 == 0:
                self.log(f"  pass 2: {n}/{len(self.rows)}")

    def update(self, r: dict, it: Item) -> Write:
        key = it.key
        if key not in self.qids:
            return self.record(Write(2, it.kind, key, "skip-unresolved", note="no id: pass 1 did not create it"))
        qid = self.qids[key]
        own = key in self.ours or key in self.placeholders
        try:
            if own:
                body, dropped = self.full_body(it)
                w = Write(2, it.kind, key, "clear", qid=qid, body=body)
            else:
                body, dropped = self.append_body(it, qid)
                if not body:
                    if not self.dry_run:
                        store.set_item(self.conn, key, status="pushed", pushed_hash=it.content_hash(),
                                       pushed_at=store.now(), error="")
                    return self.record(Write(2, it.kind, key, "skip-current", qid=qid))
                w = Write(2, it.kind, key, "update", qid=qid, body=body)
        except (Unresolved, ValueError) as e:
            w = Write(2, it.kind, key, "fail", qid=qid, error=str(e)[:300], code="error")
            if not self.dry_run:
                store.set_item(self.conn, key, status="failed", error=w.error)
            return self.record(w)
        w.placeholder = key in self.placeholders
        if dropped:
            w.note = f"{len(dropped)} snaks dropped (no id): " + "; ".join(dropped[:3])
            self.dropped += [f"{key}: {d}" for d in dropped]
        if self.dry_run:
            return self.record(w)
        try:
            self.api.edit(body, qid=qid, clear=(w.action == "clear"),
                          summary=f"Glass Database: {it.kind} {key} ({'replace' if own else 'append'}; {it.source_table})")
            store.set_item(self.conn, key, status="pushed", pushed_hash=it.content_hash(), pushed_at=store.now(), error="")
        except WikibaseError as e:
            w.action, w.error, w.code = "fail", str(e)[:300], error_code(e)
            store.set_item(self.conn, key, status="failed", error=w.error)
            self.log(f"  {key}: {e}")
        return self.record(w)

    # ---- summary
    def summary(self) -> dict:
        per: dict[str, dict[str, Counter]] = {}
        for w in self.writes:
            per.setdefault(str(w.pass_no), {}).setdefault(w.kind, Counter())[w.action] += 1
        examples: dict[str, list] = {}
        for w in self.writes:
            if w.body is None:
                continue
            ex = examples.setdefault(f"{w.pass_no}:{w.kind}:{w.action}", [])
            if len(ex) < EXAMPLES:
                s = json.dumps(w.body, ensure_ascii=False, sort_keys=True)
                ex.append({"key": w.key, "qid": w.qid, "body": s if len(s) <= 1200 else s[:1200] + f"… ({len(s)} chars)"})
        creates = sum(1 for w in self.writes if w.action == "create")
        updates = sum(1 for w in self.writes if w.action in ("update", "clear"))
        return {"counts": {p: {k: dict(c) for k, c in kinds.items()} for p, kinds in per.items()},
                "creates": creates, "updates": updates, "total_writes": creates + updates,
                "api_writes": self.api.writes, "failures_by_code": dict(self.failures),
                "failures": [w.row() for w in self.writes if w.action == "fail"][:300],
                "dropped_snaks": len(self.dropped), "dropped_examples": self.dropped[:40],
                "placeholders": len(self.placeholders), "examples": examples,
                "seconds": round(time.monotonic() - self.t0, 1), "dry_run": self.dry_run, "items": len(self.rows)}

    def write_plan(self, run_id: int | str) -> Path:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"plan-{run_id}.json"
        plan = dict(self.summary(), writes=[w.row() for w in self.writes],
                    bodies=[{"pass": w.pass_no, "key": w.key, "qid": w.qid, "action": w.action, "body": w.body}
                            for w in self.writes if w.body is not None])
        path.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
        return path


def print_summary(s: dict, log: Log) -> None:
    for p in sorted(s["counts"]):
        name = {"0": "properties", "1": "pass 1 (entities)", "2": "pass 2 (statements)"}.get(p, p)
        log(f"{name}:")
        for kind, c in sorted(s["counts"][p].items(), key=lambda kv: (KIND_ORDER.index(kv[0]) if kv[0] in KIND_ORDER else 99)):
            log("  " + f"{kind:12} " + ", ".join(f"{a} {n}" for a, n in sorted(c.items())))
    log(f"creates {s['creates']}, updates {s['updates']}, API writes {s['api_writes']}"
        + (f"; snaks dropped for want of an id: {s['dropped_snaks']}" if s["dropped_snaks"] else "")
        + ("; failures: " + ", ".join(f"{k} {n}" for k, n in s["failures_by_code"].items()) if s["failures_by_code"] else "")
        + (" (dry run: nothing was written)" if s["dry_run"] else ""))


# --------------------------------------------------------------------------- jobs
def make_api(conn, dry_run: bool, log: Log) -> Wikibase:
    s = store.get_settings(conn)
    user, password = store.credentials(conn)
    user = user or os.environ.get("WIKIBASE_BOT_USER", "")
    password = password or os.environ.get("WIKIBASE_BOT_PASSWORD", "")
    api = Wikibase(s["url"], user, password, dry_run=dry_run, pause=s["pause"], bot=s["bot"], log=log)
    return api


def run(conn, log: Log = print, params: dict | None = None) -> dict:
    """The *bootstrap*, *dry-run* and *push* jobs. ``params``: really (bool), what
    (properties|items|all), limit, kinds, keys, live (read the wiki first; default True)."""
    params = params or {}
    store.ensure(conn)
    really = bool(params.get("really"))
    what = params.get("what") or "all"
    api = make_api(conn, dry_run=not really, log=log)
    s = store.get_settings(conn)
    try:
        if really:
            if not (api.user and api.password):
                raise WikibaseError("no bot credentials saved: fill in the bot user and password first")
            who = api.login()
            log(f"signed in to {api.base} as {who}")
        else:
            log(f"dry run against {api.base}: nothing will be written")
        p = Pusher(conn, api, log, dry_run=not really, public_base=s["public_base"], limit=params.get("limit"),
                   kinds=params.get("kinds"), keys=params.get("keys"))
        p.load_ids(live=params.get("live", True))
        if what in ("properties", "all"):
            p.bootstrap()
        if what in ("items", "all"):
            p.select()
            p.pass1()
            p.pass2()
        summary = p.summary()
        print_summary(summary, log)
        try:
            path = p.write_plan(params.get("run_id") or int(time.time()))
            summary["plan"] = str(path)
            log(f"plan written to {path}")
        except OSError as e:
            log(f"could not write the plan file ({e})")
        if summary["failures_by_code"]:
            summary["failed"] = True
        return summary
    finally:
        api.close()
