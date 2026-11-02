"""
central.wikibase.wikidata
=========================
What Wikidata already knows about what we stage. The *wikidata* job (the page's **Look up**
button) finds the Wikidata item for a staged person, studio, school, museum, funder, award,
show, place, country or vocabulary term, records it as the item's *Wikidata ID* (the same
column the OpenRefine import fills, so the next push carries it to the wiki), and reports
what Wikidata holds that we do not. It reads Wikidata; it never writes to it.

Two sources:

* **The glass-artist index.** Three SPARQL reads of every item on Wikidata whose occupation
  is glass artist, glassblower, stained-glass artist, glass painter, glass designer or glass
  engraver (about 2,100 people): labels, aliases and descriptions; their links to
  institutions and places (*has works in the collection*, *affiliation*, *employer*,
  *educated at*, *work location*, *award received*); websites and Instagram accounts. A
  person of ours whose name is exactly one of them is matched; two namesakes wait for a
  person to pick.
* **The Wikidata reconciliation service** (``wikidata-reconciliation.wmcloud.org``, the one
  OpenRefine uses) for everything else, ten queries a request, each with the Wikidata class
  the kind calls for (human settlement, U.S. state, museum, educational institution,
  organization, color, occupation …) and the country when we know it.

The decisions are deliberately narrow. A candidate must carry our label (or one of our
aliases) exactly; a place must name its region, or failing that its country, in its
description; a person found only through the service must be described as a glass worker; a
technique or trait must be described in terms of glass. One candidate that passes is
``matched`` and written to the item; several are ``unsure`` and wait on the page, where an
administrator picks one (``confirmed``) or says none (``rejected``); no candidate is ``none``.
An item that already had an id (the hand lists in ``model``, OpenRefine, a reviewer) is
``known`` and is not looked up. Re-running looks only at items not yet checked or whose label
changed; ``recheck`` redoes the automatic decisions and never a person's.

The report at the end of a run: what Wikidata adds to the people we matched (museums holding
their work, websites and Instagram accounts we lack), and the people Wikidata ties to our own
institutions and places that we have no row for, each with its Wikidata link, so a
coordination with a museum can start from what is already recorded.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import httpx

from central.wikibase import store
from central.wikibase.client import USER_AGENT
from central.wikibase.jobs import beat
from central.wikibase.model import COUNTRIES, US_STATES, Item, key_for, norm, slug

RECONCILE_URL = "https://wikidata-reconciliation.wmcloud.org/en/api"
SPARQL_URL = "https://query.wikidata.org/sparql"
WD = "http://www.wikidata.org/entity/"
BATCH = 10                      # queries per reconciliation request
PAUSE = 0.4                     # seconds between requests
CANDIDATES = 6                  # kept per item

# occupations whose holders make up the glass-artist index
GLASS_OCCUPATIONS = {"Q2865798": "glass artist", "Q1413824": "glassblower", "Q2205972": "stained-glass artist",
                     "Q18431816": "glass painter", "Q113995863": "glass designer", "Q18510097": "glass engraver"}
# the links the index carries from a person to an institution or a place
LINK_PROPS = {"P6379": "has works in the collection", "P1416": "affiliation", "P108": "employer",
              "P69": "educated at", "P937": "work location", "P166": "award received"}

# the Wikidata class a kind's candidates must be instances of (the service includes subclasses)
TYPES = {"country": "Q6256", "museum": "Q33506", "school": "Q2385804", "studio": "Q43229", "funder": "Q43229",
         "award": "Q618779", "colour": "Q1075", "occupation": "Q12737077", "person": "Q5"}
SHOW_TYPES = {"art-fair": "Q1792535", "trade-fair": "Q57305", "art-exhibition": "Q667276", "show": "Q1656682"}
PLACE_TYPES = {"city": "Q486972", "us-state": "Q35657", "region": "Q10864048"}
# words that say "glass" in a description or class name; nothing that also names a mineral
# ("crystal"), an oven ("kiln") or teeth ("enamel"), since a wrong id on a vocabulary term
# would be pushed as fact
GLASS_WORDS = ("glass", "glaz", "vitr", "verre", "lampwork", "flamework", "stained", "murrin", "millefiori",
               "cristallo", "lattimo")
ART_WORDS = ("artist", "sculpt", "design", "paint", "craft", "ceram", "potter", "jewel", "maker", "artisan",
             "educator", "curator", "gaffer")
# kinds in the order they are looked up: what people link to comes first, so the
# neighbourhood report at the end can use the ids this very run found
LOOKUP_KINDS = ("country", "place", "museum", "school", "funder", "award", "studio", "show", "colour",
                "occupation", "technique", "trait", "person")
ANCHOR_KINDS = ("museum", "school", "funder", "award", "studio", "place")
STATUSES = ("known", "matched", "unsure", "none", "confirmed", "rejected")

COUNTRY_QID = {key_for("country", slug(label)): qid for label, qid in COUNTRIES.values() if qid}
US_STATE_NAMES = set(US_STATES.values())


def _has(words, *texts) -> bool:
    t = " " + " ".join(norm(x) for x in texts if x) + " "
    return any(w in t for w in words)


# --------------------------------------------------------------------------- the glass-artist index
@dataclass
class Person:
    qid: str
    label: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    links: list[tuple[str, str, str]] = field(default_factory=list)   # (property label, target qid, target label)
    website: str = ""
    instagram: str = ""

    def facts(self) -> dict:
        by: dict[str, list[str]] = defaultdict(list)
        for prop, _q, label in self.links:
            if label and label not in by[prop]:
                by[prop].append(label)
        return {"qid": self.qid, "label": self.label, "description": self.description, "links": dict(by),
                "website": self.website, "instagram": self.instagram}


class Index:
    """Wikidata's glass artists, by normalised name (labels and aliases)."""

    def __init__(self, people: list[Person]):
        self.people = {p.qid: p for p in people}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for p in people:
            for n in {norm(p.label), *(norm(a) for a in p.aliases)}:
                if n and p.qid not in self.by_name[n]:
                    self.by_name[n].append(p.qid)

    def __len__(self) -> int:
        return len(self.people)

    def find(self, label: str, aliases=()) -> list[Person]:
        seen: list[str] = []
        for n in [norm(label), *(norm(a) for a in aliases)]:
            for q in self.by_name.get(n, []):
                if q not in seen:
                    seen.append(q)
        return [self.people[q] for q in seen]


SPARQL_PAUSE = 2.0            # seconds between queries to the query service, which throttles bursts
_last_sparql = 0.0


def sparql(client: httpx.Client, query: str, log=None) -> list[dict]:
    """One query to the Wikidata query service, paced, and tried again after a 429 or 503 (what the service says in
    Retry-After, at least 5, 10, 20 seconds) before giving up."""
    global _last_sparql
    last: Exception | None = None
    for attempt in range(4):
        wait = _last_sparql + SPARQL_PAUSE - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_sparql = time.monotonic()
        r = client.get(SPARQL_URL, params={"query": query, "format": "json"},
                       headers={"Accept": "application/sparql-results+json"}, timeout=180)
        if r.status_code in (429, 503) and attempt < 3:
            try:
                retry = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                retry = 0.0
            pause = min(max(retry, 5.0 * (2 ** attempt)), 60.0)
            if log:
                log(f"  the query service answered {r.status_code}; waiting {pause:.0f} s")
            last = httpx.HTTPStatusError(f"{r.status_code} from the query service", request=r.request, response=r)
            time.sleep(pause)
            continue
        r.raise_for_status()
        return r.json().get("results", {}).get("bindings", [])
    raise last or RuntimeError("the query service did not answer")


def _v(b: dict, k: str) -> str:
    return str((b.get(k) or {}).get("value") or "")


def _q(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def fetch_index(client: httpx.Client, log=print) -> list[Person]:
    """The index from Wikidata: three reads (names, links, websites)."""
    occ = " ".join(f"wd:{q}" for q in GLASS_OCCUPATIONS)
    who = f"?a wdt:P106 ?occ . VALUES ?occ {{ {occ} }}"
    people: dict[str, Person] = {}
    for b in sparql(client, f'SELECT ?a ?aLabel ?d (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?alts) WHERE {{ {who} '
                            f'OPTIONAL {{ ?a schema:description ?d FILTER(lang(?d)="en") }} '
                            f'OPTIONAL {{ ?a skos:altLabel ?alt FILTER(lang(?alt)="en") }} '
                            f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }} GROUP BY ?a ?aLabel ?d', log):
        q = _q(_v(b, "a"))
        label = _v(b, "aLabel")
        if not q or not label or label == q:
            continue
        p = people.setdefault(q, Person(q, label))
        p.description = p.description or _v(b, "d")
        for a in _v(b, "alts").split("|"):
            if a and a not in p.aliases:
                p.aliases.append(a)
    log(f"  Wikidata lists {len(people)} people in the glass occupations")
    props = " ".join(f"wdt:{p}" for p in LINK_PROPS)
    for b in sparql(client, f"SELECT ?a ?p ?o ?oLabel WHERE {{ {who} VALUES ?p {{ {props} }} ?a ?p ?o . "
                            f'SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}', log):
        p = people.get(_q(_v(b, "a")))
        if p:
            p.links.append((LINK_PROPS.get(_q(_v(b, "p")), _q(_v(b, "p"))), _q(_v(b, "o")), _v(b, "oLabel")))
    for b in sparql(client, f"SELECT ?a ?web ?ig WHERE {{ {who} OPTIONAL {{ ?a wdt:P856 ?web }} OPTIONAL {{ ?a wdt:P2003 ?ig }} "
                            f"FILTER(BOUND(?web) || BOUND(?ig)) }}", log):
        p = people.get(_q(_v(b, "a")))
        if p:
            p.website = p.website or _v(b, "web")
            p.instagram = p.instagram or _v(b, "ig")
    return list(people.values())


# --------------------------------------------------------------------------- the reconciliation service
def reconcile_batch(client: httpx.Client, queries: dict) -> dict:
    """One request to the reconciliation service: ``{id: {query, type?, limit, properties?}}`` →
    ``{id: {"result": [...]}}``. Three tries, then the run fails with the reason."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = client.post(RECONCILE_URL, data={"queries": json.dumps(queries, ensure_ascii=False)}, timeout=90)
            r.raise_for_status()
            out = r.json()
            if not isinstance(out, dict):
                raise ValueError("the reconciliation service did not answer with a JSON object")
            return out
        except (httpx.HTTPError, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"the Wikidata reconciliation service did not answer ({last}); try again later")


def candidate(c: dict, ours: set[str]) -> dict:
    """A service result → what the page and the decision need. ``exact`` is the service's own
    word that one of the item's labels or aliases equals ours (the ``name`` it returns can be
    in any language); ``by_name`` says the name it returned is ours."""
    feats = {f.get("id"): f.get("value") for f in c.get("features") or [] if isinstance(f, dict)}
    name = str(c.get("name") or "")
    by_name = norm(name) in ours
    return {"qid": str(c.get("id") or ""), "label": name, "description": str(c.get("description") or "")[:200],
            "score": round(float(c.get("score") or 0), 1), "match": bool(c.get("match")),
            "types": [str(t.get("name") or "") for t in c.get("type") or [] if isinstance(t, dict)][:4],
            "exact": by_name or feats.get("all_labels") == 100, "by_name": by_name}


# --------------------------------------------------------------------------- decisions
def decide(cands: list[dict], *, context: list[str] | None = None, glass: bool = False,
           art_is_unsure: bool = False) -> tuple[str, str, str]:
    """``(status, qid, why)`` for one item from its candidates. ``context`` is a list of
    preference levels, each a list of words of which one must appear in the description (a
    place: its region, then its country). ``glass`` demands a glass word in the description or
    class names. ``art_is_unsure`` (people found through the service) keeps a namesake who is
    some other kind of artist for a person to look at instead of dropping them."""
    if not cands:
        return "none", "", "Wikidata has no item of that name and kind"
    exact = [c for c in cands if c["exact"]]
    if not exact:
        return "none", "", "no candidate carries the name exactly"
    if glass:
        # the description and the class names say what a thing is; its own name does not
        # ("Stained Glass" is also a band and a television series)
        fit = [c for c in exact if _has(GLASS_WORDS, c["description"], *c["types"])]
        if not fit:
            if art_is_unsure and any(_has(ART_WORDS, c["description"], *c["types"]) for c in exact):
                return "unsure", "", "a namesake who is some other kind of artist; nothing says glass"
            return "none", "", "namesakes exist, but none is described in terms of glass"
        exact = fit
    levels = [[norm(w) for w in level if w] for level in (context or [])]
    levels = [lv for lv in levels if lv]
    if levels:
        for words in levels:
            fit = [c for c in exact if any(" " + w + " " in " " + norm(c["description"]) + " " for w in words)]
            if fit:
                exact = fit
                break
        else:
            # nothing names our town, region or country: only the service's own unique match passes
            if not (len(exact) == 1 and exact[0]["match"]):
                return "unsure", "", "no candidate's description names our place; a person has to look"
    if len(exact) == 1:
        return "matched", exact[0]["qid"], "the one item of that name and kind" + (" described in terms of glass" if glass else "")
    by_name = [c for c in exact if c["by_name"]]
    if len(by_name) == 1:
        return "matched", by_name[0]["qid"], "the one item whose own name is ours (the others match an alias)"
    return "unsure", "", f"{len(exact)} items carry the name; a person has to pick"


# --------------------------------------------------------------------------- what to ask
def _desc_parts(it: Item) -> list[str]:
    return [p.strip() for p in (it.description or "").split(",") if p.strip()]


def country_qid_of(it: Item, items: dict[str, Item]) -> str:
    for k in it.values("country"):
        if k in COUNTRY_QID:
            return COUNTRY_QID[k]
    for k in it.values("located in") + it.values("work location"):
        p = items.get(k)
        if p:
            for ck in p.values("country"):
                if ck in COUNTRY_QID:
                    return COUNTRY_QID[ck]
    return ""


def parents_of(items: dict[str, Item]) -> set[str]:
    """The place keys other places are *located in*: regions, as opposed to towns."""
    return {k for it in items.values() if it.kind == "place" for k in it.values("located in")}


def query_for(it: Item, items: dict[str, Item], parents: set[str] | None = None) -> dict:
    """The reconciliation query for one staged item, plus how to judge the answer:
    ``{query, type?, properties?, context, glass, art_is_unsure}``."""
    text = re.sub(r"\s*\(\d{4}\)\s*$", "", it.label).strip()
    shortened = False
    if it.kind in ("technique", "trait"):
        # "Kiln / Open-Mold Casting" is asked as "Kiln": a shortened name can only ever be unsure
        text = re.sub(r"\s*\([^)]*\)", "", text)
        text = re.split(r"\s*[&/]\s*", text)[0].strip() or text
        shortened = norm(text) != norm(it.label)
    q: dict = {"query": text, "limit": CANDIDATES, "context": [], "glass": False, "art_is_unsure": False,
               "shortened": shortened}
    props = []
    cq = country_qid_of(it, items)
    if it.kind == "place":
        parts = [p for p in _desc_parts(it) if p.lower() != "place"]
        if it.key in (parents or set()) or (it.label in US_STATE_NAMES and len(parts) < 2):
            q["type"] = PLACE_TYPES["us-state"] if it.label in US_STATE_NAMES else PLACE_TYPES["region"]
            q["context"] = [parts[-1:]] if parts else []
        else:
            q["type"] = PLACE_TYPES["city"]
            q["context"] = [parts[:1], parts[-1:]] if parts else []
    elif it.kind == "show":
        q["type"] = SHOW_TYPES.get((it.cls or "").rsplit(":", 1)[-1], SHOW_TYPES["show"])
    elif it.kind in TYPES:
        q["type"] = TYPES[it.kind]
    if it.kind in ("technique", "trait", "studio"):
        q["glass"] = True
    if it.kind == "person":
        q["glass"], q["art_is_unsure"] = True, True
        props.append({"pid": "P106", "v": {"id": "Q2865798"}})
    elif it.kind in ("studio", "school", "museum", "funder", "show"):
        # the city, then the region: a tie between two namesakes goes to the one in our town
        parts = [p.strip() for p in re.split(r"\bin\b", it.description or "", 1)[-1].split(",")] if " in " in (it.description or "") else []
        q["context"] = [parts[:1], parts[1:2]] if parts else []
    if cq and it.kind in ("place", "museum", "school", "studio", "funder", "show"):
        props.append({"pid": "P17", "v": {"id": cq}})
    if props:
        q["properties"] = props
    return q


# --------------------------------------------------------------------------- the job
def _select(conn, params: dict) -> list[dict]:
    kinds = [k for k in (params.get("kinds") or LOOKUP_KINDS) if k in LOOKUP_KINDS]
    kinds.sort(key=LOOKUP_KINDS.index)
    rows = [r for r in store.items(conn, status=None) if r["kind"] in kinds and r["status"] not in ("skipped", "duplicate")]
    if params.get("keys"):
        want = set(params["keys"])
        rows = [r for r in rows if r["key"] in want]
    rows.sort(key=lambda r: (LOOKUP_KINDS.index(r["kind"]), r["label"]))
    if params.get("limit"):
        rows = rows[: int(params["limit"])]
    return rows


def run(conn, log=print, params: dict | None = None) -> dict:
    """The *wikidata* job. ``params``: kinds, keys, limit, recheck (redo matched / unsure /
    none; a person's confirmed / rejected always stand)."""
    params = params or {}
    store.ensure(conn)
    recheck = bool(params.get("recheck"))
    rows = _select(conn, params)
    all_items = {r["key"]: store.effective_item(r) for r in store.items(conn, status=None)}
    existing = {r["key"]: r for r in store.rows(conn.execute("SELECT * FROM wb_wikidata"))}
    written: Counter = Counter()
    by_kind: dict[str, Counter] = defaultdict(Counter)
    todo: list[tuple[dict, Item]] = []
    ours: set[str] = set()                           # items whose current id the lookup itself set
    now = store.now()
    for r in rows:
        it = all_items[r["key"]]
        have = (r.get("wikidata_qid") or it.wikidata or "").strip()
        ex = existing.get(r["key"])
        mine = bool(ex and ex["status"] == "matched" and ex["qid"] and ex["qid"] == (r.get("wikidata_qid") or ""))
        if have and not (mine and recheck):
            if not (ex and ex["qid"] == have and ex["status"] in ("matched", "confirmed", "known")):
                store.upsert_wikidata(conn, r["key"], r["kind"], it.label, "known", have,
                                      "had its Wikidata id before the lookup (a hand list, OpenRefine or a reviewer)", checked_at=now)
                written["known"] += 1
                by_kind[r["kind"]]["known"] += 1
            continue
        if ex and ex["status"] in ("confirmed", "rejected"):
            continue
        if ex and ex["status"] == "known":           # the id was taken away since: look it up now
            ex = None
        if ex and not recheck and ex["label"] == it.label:
            continue
        if mine:
            ours.add(r["key"])
        todo.append((r, it))
    conn.commit()
    log(f"{len(rows)} items in the kinds looked up; {len(todo)} to ask Wikidata about"
        + (" (recheck: automatic decisions are redone)" if recheck else "")
        + (f"; {written['known']} already carry an id" if written["known"] else ""))
    people = [(r, it) for r, it in todo if r["kind"] == "person"]
    others = [(r, it) for r, it in todo if r["kind"] != "person"]
    errors = 0
    client = httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        # the glass-artist index: our people against Wikidata's (the report at the end reads it too)
        index: Index | None = None
        try:
            beat(log)
            index = Index(fetch_index(client, log))
        except (httpx.HTTPError, ValueError) as e:
            errors += 1
            log(f"  could not read Wikidata's glass artists ({e}); people go through the reconciliation service only")
        parents = parents_of(all_items)
        leftovers: list[tuple[dict, Item]] = []
        for r, it in people:
            beat(log)
            found = index.find(it.label, it.aliases) if index else []
            if len(found) == 1:
                p = found[0]
                _write(conn, r, it, "matched", p.qid, "the one person in Wikidata's glass occupations with this name",
                       [_as_candidate(x) for x in found], p.facts(), written, by_kind, clear=r["key"] in ours)
            elif found:
                _write(conn, r, it, "unsure", "", f"{len(found)} people in Wikidata's glass occupations carry this name",
                       [_as_candidate(x) for x in found[:CANDIDATES]], {}, written, by_kind, clear=r["key"] in ours)
            else:
                leftovers.append((r, it))
        # everything else: the reconciliation service, ten at a time
        queue = others + leftovers
        asked = 0
        for i in range(0, len(queue), BATCH):
            beat(log)
            chunk = queue[i:i + BATCH]
            plans = {f"q{n}": query_for(it, all_items, parents) for n, (_r, it) in enumerate(chunk)}
            payload = {k: {kk: vv for kk, vv in v.items() if kk in ("query", "type", "limit", "properties")} for k, v in plans.items()}
            answers = reconcile_batch(client, payload)
            for n, (r, it) in enumerate(chunk):
                plan = plans[f"q{n}"]
                names = {norm(it.label), norm(plan["query"]), *(norm(a) for a in it.aliases)}
                cands = [candidate(c, names) for c in (answers.get(f"q{n}") or {}).get("result") or [] if isinstance(c, dict)]
                cands = cands[:CANDIDATES]
                status, qid, why = decide(cands, context=plan["context"], glass=plan["glass"], art_is_unsure=plan["art_is_unsure"])
                if status == "matched" and plan.get("shortened"):
                    status, qid, why = "unsure", "", f"asked as “{plan['query']}”, a shortened name: {why}; a person confirms"
                facts = {}
                if status == "matched" and index and qid in index.people:
                    facts = index.people[qid].facts()
                _write(conn, r, it, status, qid, why, cands, facts, written, by_kind, clear=r["key"] in ours)
            asked += len(chunk)
            if asked % 100 < BATCH or asked == len(queue):
                log(f"  asked about {asked}/{len(queue)}: " + ", ".join(f"{k} {n}" for k, n in sorted(written.items())))
            if i + BATCH < len(queue):
                time.sleep(PAUSE)
        conn.commit()
        report = _report(conn, index, all_items, log)
    finally:
        client.close()
    totals = store.wikidata_counts(conn)
    log("after the lookup: " + ", ".join(f"{k} {totals.get(k, 0)}" for k in STATUSES) + f"; {totals['linked']} items carry a Wikidata id")
    if written:
        log("this run: " + ", ".join(f"{k} {n}" for k, n in sorted(written.items())))
    return {"looked_up": len(todo), "by_status": dict(written), "by_kind": {k: dict(v) for k, v in by_kind.items()},
            "totals": totals, "index_people": len(index) if index else 0, "service_errors": errors, **report}


def _as_candidate(p: Person) -> dict:
    return {"qid": p.qid, "label": p.label, "description": p.description, "score": 100.0, "match": False,
            "types": ["human"], "exact": True, "by_name": True}


def _write(conn, r: dict, it: Item, status: str, qid: str, why: str, cands: list[dict], facts: dict,
           written: Counter, by_kind: dict, *, clear: bool = False) -> None:
    """Record a decision; a match goes onto the staged item. ``clear``: the item's current id
    came from an earlier run of the lookup, so anything but a match takes it away again."""
    store.upsert_wikidata(conn, r["key"], r["kind"], it.label, status, qid, why, candidates=cands, facts=facts,
                          checked_at=store.now())
    if status == "matched":
        if qid != (r.get("wikidata_qid") or ""):
            store.set_wikidata_qid(conn, r["key"], qid)
    elif clear:
        store.set_wikidata_qid(conn, r["key"], "")
    written[status] += 1
    by_kind[r["kind"]][status] += 1


def _report(conn, index: Index | None, all_items: dict[str, Item], log) -> dict:
    """What Wikidata adds to our people, and whom it knows near our institutions and places."""
    out: dict = {"adds": {"website": 0, "instagram": 0, "in_collections": 0}, "in_collections": {},
                 "suggested": [], "suggested_n": 0}
    if not index:
        return out
    linked = store.rows(conn.execute("SELECT w.key, w.kind, w.qid, i.label, i.item_json, i.wikidata_qid FROM wb_wikidata w "
                                     "JOIN wb_items i ON i.key=w.key WHERE w.status IN ('matched','confirmed','known') AND w.qid<>''"))
    ours_q: dict[str, str] = {}
    in_coll: dict[str, list[str]] = defaultdict(list)
    for r in linked:
        it = all_items.get(r["key"])
        if not it or r["kind"] != "person" or r["qid"] not in index.people:
            continue
        ours_q[r["qid"]] = r["key"]
        p = index.people[r["qid"]]
        if p.website and not it.has("official website"):
            out["adds"]["website"] += 1
        if p.instagram and not it.has("Instagram username"):
            out["adds"]["instagram"] += 1
        for prop, _q, label in p.links:
            if prop == "has works in the collection" and label and it.label not in in_coll[label]:
                in_coll[label].append(it.label)
    out["adds"]["in_collections"] = sum(len(v) for v in in_coll.values())
    out["in_collections"] = dict(sorted(in_coll.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:40])
    # anchors: our institutions and towns that have a Wikidata id (a region or a state would
    # pull in everyone who ever worked there)
    parents = parents_of(all_items)
    anchors: dict[str, str] = {}
    for r in linked:
        if r["kind"] in ANCHOR_KINDS and r["qid"] and r["key"] not in parents:
            anchors[r["qid"]] = r["label"]
    for it in all_items.values():
        if it.kind in ANCHOR_KINDS and it.wikidata and it.key not in parents:
            anchors.setdefault(it.wikidata, it.label)
    our_names = {norm(it.label) for it in all_items.values() if it.kind == "person"}
    for it in all_items.values():
        if it.kind == "person":
            our_names.update(norm(a) for a in it.aliases)
    suggested = []
    for p in index.people.values():
        if p.qid in ours_q or norm(p.label) in our_names:
            continue
        via = []
        for prop, q, label in p.links:
            if q in anchors:
                v = f"{label or anchors[q]} ({prop})"
                if v not in via:
                    via.append(v)
        if via:
            suggested.append({"qid": p.qid, "label": p.label, "description": p.description, "via": via})
    suggested.sort(key=lambda s: (-len(s["via"]), s["label"]))
    out["suggested_n"] = len(suggested)
    out["suggested"] = suggested[:400]
    if in_coll:
        log("  Wikidata says these museums hold our people's work: "
            + "; ".join(f"{m} ({len(v)})" for m, v in list(out["in_collections"].items())[:12]))
    if out["adds"]["website"] or out["adds"]["instagram"]:
        log(f"  Wikidata has {out['adds']['website']} websites and {out['adds']['instagram']} Instagram accounts for our people that we lack")
    log(f"  {len(anchors)} of our institutions and places have a Wikidata id; Wikidata ties {len(suggested)} glass artists "
        f"we do not hold to them" + (": " + "; ".join(f"{s['label']} ({s['qid']})" for s in suggested[:15]) if suggested else ""))
    return out


def suggest(conn, key: str, suggestion: dict, by: str = "") -> dict | None:
    """A suggestion from outside (lexflow's local-model panel, ``lexflow run resolve``): kept on the lookup row
    for the reviewer, who still decides. The row's status does not change and nothing is written to the item.
    Returns the row, or None when the key has no lookup row."""
    if not store.one(conn.execute("SELECT key FROM wb_wikidata WHERE key=?", (key,))):
        return None
    s = suggestion or {}
    qid = str(s.get("qid") or "").strip().upper()
    qid = qid if re.fullmatch(r"Q\d+", qid) else ""
    verdict = str(s.get("verdict") or (qid or "none")).strip()[:20]
    try:
        conf = max(0.0, min(1.0, float(s.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    models = {}
    for m, d in list((s.get("models") or {}).items())[:8]:
        if isinstance(d, dict):
            try:
                mc = max(0.0, min(1.0, float(d.get("confidence") or 0)))
            except (TypeError, ValueError):
                mc = 0.0
            models[str(m)[:60]] = {"verdict": str(d.get("verdict") or "")[:20], "confidence": round(mc, 3),
                                   "because": str(d.get("because") or "")[:300]}
    keep = {"qid": qid, "verdict": verdict, "confidence": round(conf, 3), "because": str(s.get("because") or "")[:600],
            "settled": bool(s.get("settled")), "models": models, "searched": bool(s.get("searched")),
            "version": str(s.get("version") or "")[:40], "by": (by or "")[:120], "at": store.now()}
    conn.execute("UPDATE wb_wikidata SET suggestion_json=? WHERE key=?", (json.dumps(keep, ensure_ascii=False), key))
    conn.commit()
    return store.wikidata_rows(conn, keys=[key])[0][0]


def confirm(conn, key: str, qid: str, by: str = "") -> dict | None:
    """A person's decision from the page: ``qid`` links the item (``confirmed``); an empty
    ``qid`` says it is not on Wikidata (``rejected``) and takes away an id the lookup set.
    Returns the lookup row, or None when the key is not staged."""
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    if not r:
        return None
    qid = (qid or "").strip()
    store.set_wikidata_qid(conn, key, qid)
    it = store.effective_item(dict(r, wikidata_qid=qid))
    store.upsert_wikidata(conn, key, r["kind"], it.label, "confirmed" if qid else "rejected", qid,
                          ("linked by " if qid else "ruled out by ") + (by or "an administrator") + " on the page",
                          decided_by=by or "an administrator", decided_at=store.now())
    conn.commit()
    return store.wikidata_rows(conn, keys=[key])[0][0]
