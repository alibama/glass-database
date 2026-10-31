"""
central.wikibase.stage
======================
The site's tables → one staged item per thing the wiki should hold (``wb_items``). This is
the step that makes the public record wiki-shaped: only approved rows, only public columns,
free text split into items (a place, a technique, a studio type), every item with a stable
key, and a report of which columns were used and which were left behind, so nothing is
carried over by accident.

What is staged, and from where (every table is optional: a missing one is skipped):

  people      artists, artists_legacy, profiles, artist_submissions (approved), museum_artists
              (+ a *collection* statement), rakow_commissions (+ *award received*), the makers
              named on published pieces, both ends of an approved lineage row
  studios     studios, studios_directory_legacy, studio_submissions, the studio named on a
              season entry
  schools     university_programs and programs: one item per institution, one per program
  museums     the museums of museum_artists
  funders     funding
  shows       maker_events (listed), events, open_calls, trade_* and art_fairs
  seasons     every collecting event that is not a draft; results once it is closed
  pieces      every approved row of objects, with its story, size, colour, hashes and URLs
  vocabulary  classes, techniques, traits, colours, forms, occupations, event types, units,
              countries, the Glass Database source item (from code: ``model.fixed_items``)
  places      made from the cities, regions and countries the rows name

Never staged: collectors and their collections, claims, votes, buyers, prices paid, emails,
phones, demographics, anything in a restricted dataset or a private column, unpublished
pieces, fingerprint vectors.

Re-running is safe: items are upserted by key, a pushed item whose source changed becomes
``changed``, a pushed item whose source row vanished becomes ``orphan`` (never deleted on the
wiki), a never-pushed item whose row vanished is dropped from staging.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from central import approvals, techniques
from central import manifest as _manifest
from central.wikibase import store
from central.wikibase.model import (
    COLOURS,
    FORM_WORDS,
    INSTANCE_OF,
    KNOWN_ENTITIES,
    KNOWN_PLACES,
    STUDIO_TYPES,
    US_STATE_NAMES,
    US_STATES,
    WIKIDATA_PROP,
    Item,
    clip,
    coordinate,
    country_key,
    country_label,
    ext,
    fixed_items,
    item,
    key_for,
    mono,
    norm,
    quantity,
    slug,
    string,
    time_value,
    url,
)

Log = type(print)

# column candidates, in order of preference, per meaning
NAME_COLS = ("artist_name", "display_name", "full_name", "name", "artist", "maker")
STUDIO_NAME_COLS = ("name", "studio_name", "studio", "shop", "organization")
PLACE_COLS = {"city": ("city", "town", "city_town"), "region": ("region", "state", "state_region", "province", "state_province"),
              "country": ("country", "nation")}
BASED_COLS = ("based_in", "nationality_base", "location", "based", "where_based", "based_city")
WEB_COLS = ("website", "url", "web", "link", "site", "website_or_social", "homepage")
INSTAGRAM_COLS = ("instagram", "instagram_handle", "ig", "social")
TECH_COLS = ("tech_primary", "primary_technique", "techniques", "technique", "primary_techniques")
TECH_COLS_2 = ("tech_secondary", "secondary_technique", "secondary_techniques")
TECH_COLS_3 = ("tech_occasional", "occasional_technique", "occasional_techniques")
ROLE_COLS = ("roles", "role")
TYPE_COLS = ("type", "studio_type", "kind", "category")
FOUNDED_COLS = ("founded", "year_founded", "established", "inception", "since", "year")
LAT_COLS = ("lat", "latitude")
LNG_COLS = ("lng", "lon", "long", "longitude")
INSTITUTION_COLS = ("institution", "school", "university", "college", "name")
PROGRAM_COLS = ("program_department", "program", "department", "program_name", "degree")
SHOW_NAME_COLS = ("event_show_name", "show_event_exhibition_name", "show", "fair", "event", "event_name", "name",
                  "title", "opportunity_title", "opportunity")
DATE_COLS = ("event_start", "start", "start_date", "starts", "date", "dates", "when", "deadline")
END_COLS = ("event_end", "end", "end_date", "ends")
SKIP_NAME_WORDS = ("test", "example", "asdf", "unknown", "n/a", "na", "none", "anonymous", "tbd")

SOURCE_PRIORITY = {"artists": 0, "profiles": 1, "artist_submissions": 2, "artists_legacy": 3, "museum_artists": 4,
                   "rakow_commissions": 5, "lineage_submissions": 6, "objects": 7,
                   "studios": 0, "studio_submissions": 1, "studios_directory_legacy": 2, "season_entries": 3}


def _sha(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _clean(v) -> str:
    if v is None:
        return ""
    s = " ".join(str(v).split()).strip()
    return "" if s.lower() in ("nan", "none", "null", "n/a", "-", "—") else s


def _looks_like_name(s: str) -> bool:
    s = _clean(s)
    if not s or len(s) < 2 or len(s) > 120 or "@" in s or s.lower() in SKIP_NAME_WORDS:
        return False
    return bool(re.search(r"[A-Za-zÀ-ɏ]", s))


def _handle(s: str) -> str:
    """An Instagram handle from ``@name``, a URL or plain text; '' when it is not one."""
    s = _clean(s)
    if not s:
        return ""
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]{2,30})", s)
    if m:
        return m.group(1).rstrip(".")
    if s.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_.]{2,30}", s):
        return s[1:].rstrip(".")
    if re.fullmatch(r"[A-Za-z0-9_.]{2,30}", s) and "." not in s.strip("."):
        return s
    return ""


def _website(s: str) -> str:
    s = _clean(s)
    if not s or " " in s or "@" in s and not s.startswith("http"):
        return ""
    if "instagram.com" in s or "facebook.com" in s and False:
        return ""
    if not s.startswith(("http://", "https://")):
        if "." not in s:
            return ""
        s = "https://" + s
    return s[:500]


def _year(s: str) -> str:
    m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", _clean(s))
    return m.group(1) if m else ""


def split_list(s: str) -> list[str]:
    out = []
    for part in re.split(r"\s*[|;/]\s*|\s*,\s*(?![^()]*\))", str(s or "")):
        p = _clean(part)
        if p and p not in out:
            out.append(p)
    return out


def _split_place(text: str) -> tuple[str, str, str]:
    """'Crozet, VA' → ('Crozet', 'Virginia', 'United States'); 'Venice, IT' → ('Venice', '', 'Italy');
    'Portland, Oregon, USA' → ('Portland', 'Oregon', 'United States'); 'Italy' → ('', '', 'Italy')."""
    parts = [p for p in (_clean(x) for x in re.split(r"[,/;]|\s[-–]\s", text or "")) if p]
    city = region = country = ""
    for p in parts:
        if country_label(p) and not country:
            country = country_label(p)
        elif p.upper() in US_STATES and not region:
            region = US_STATES[p.upper()]
        elif norm(p) in US_STATE_NAMES and not region:
            region = US_STATE_NAMES[norm(p)]
        elif not city:
            city = p
        elif not region:
            region = p
    if region in US_STATES.values() and not country:
        country = "United States"
    return city, region, country


class Stager:
    def __init__(self, conn, public_base: str, log: Log = print):
        self.conn, self.base, self.log = conn, public_base.rstrip("/"), log
        self.items: dict[str, Item] = {}
        self.hashes: dict[str, str] = {}
        self.people: dict[str, str] = {}        # normalised name → key (first wins: priority order)
        self.studios: dict[str, str] = {}
        self.report: dict = {"tables": {}, "held": [], "unknown_techniques": Counter(), "places": 0}
        self._tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._public_cols: dict[str, set[str]] = {}
        for r in store.rows(conn.execute("SELECT tbl, column, is_public FROM _columns")) if "_columns" in self._tables else []:
            if r["is_public"]:
                self._public_cols.setdefault(r["tbl"], set()).add(r["column"])
        self._restricted = set()
        if "_datasets" in self._tables:
            self._restricted = {r[0] for r in conn.execute("SELECT tbl FROM _datasets WHERE visibility='restricted'")}

    # ------------------------------------------------------------------ table access
    def has(self, table: str) -> bool:
        return table in self._tables and table not in self._restricted

    def cols(self, table: str) -> list[str]:
        return [r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')]

    def public_cols(self, table: str) -> list[str]:
        cols = [c for c in self.cols(table) if not c.startswith("_")]
        registered = self._public_cols.get(table)
        out = []
        for c in cols:
            if registered is not None and c not in registered:
                continue
            if _manifest.is_private_column(c):
                continue
            out.append(c)
        return out

    def rows(self, table: str, *, gate: bool = True, where: str = "", args=()) -> list[dict]:
        if not self.has(table):
            return []
        cols = self.cols(table)
        sql = f'SELECT * FROM "{table}"'
        conds = []
        if gate and "_row_id" in cols:
            conds.append(approvals.approved_subquery())
            args = (table, *args)
        if where:
            conds.append(where)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        try:
            return store.rows(self.conn.execute(sql, args))
        except Exception as e:  # noqa: BLE001
            self.log(f"  {table}: could not read ({e})")
            return []

    def pick(self, row: dict, cols: tuple, public: list[str], used: set) -> str:
        for c in cols:
            if c in public and _clean(row.get(c)):
                used.add(c)
                return _clean(row[c])
        for c in public:                       # a column whose slug contains the word
            if any(w in c for w in cols) and _clean(row.get(c)):
                used.add(c)
                return _clean(row[c])
        return ""

    def note_table(self, table: str, n_rows: int, used: set, public: list[str], made: int) -> None:
        self.report["tables"][table] = {"rows": n_rows, "items": made, "used": sorted(used),
                                        "ignored": sorted(set(public) - used)}
        if n_rows:
            self.log(f"  {table}: {n_rows} rows → {made} items; used {', '.join(sorted(used)) or 'nothing'}"
                     + (f"; left out {', '.join(sorted(set(public) - used))}" if set(public) - used else ""))

    # ------------------------------------------------------------------ item registry
    def put(self, it: Item, source: dict | None = None, priority_table: str = "") -> Item:
        """Register an item; a second item with the same key merges its claims into the first."""
        self.hashes.setdefault(it.key, _sha(source) if source is not None else _sha(it.to_json()))
        old = self.items.get(it.key)
        if old is None:
            self.items[it.key] = it
            return it
        for c in it.claims:
            if not any(x.sig() == c.sig() for x in old.claims):
                old.claims.append(c)
        for a in it.aliases:
            if a not in old.aliases and a != old.label:
                old.aliases.append(a)
        if not old.description and it.description:
            old.description = it.description
        if not old.url and it.url:
            old.url = it.url
        return old

    def person_key(self, name: str) -> str | None:
        return self.people.get(norm(name))

    def register_person(self, name: str, key: str) -> None:
        self.people.setdefault(norm(name), key)

    # ------------------------------------------------------------------ places
    def place(self, city: str = "", region: str = "", country: str = "", free_text: str = "") -> str | None:
        """The key of a place item for a city/region/country (made on first use)."""
        if free_text and not (city or region or country):
            city, region, country = _split_place(free_text)
        city, region = _clean(city), _clean(region)
        if region.upper() in US_STATES:
            region = US_STATES[region.upper()]
            country = country or "United States"
        clabel = country_label(country) if country else None
        if country and not clabel:
            clabel = country.title() if len(country) > 3 else None
        if not (city or region):
            if not clabel:
                return None
            return self.country(clabel)
        label = city or region
        tail = region if city and region else (clabel or "")
        if city and not region and clabel:
            tail = clabel
        key = key_for("place", slug(f"{label} {tail}" if tail else label))
        if key not in self.items:
            desc = ", ".join(x for x in [region if city else "", clabel or ""] if x) or "place"
            it = Item(key, "place", label, desc, [], key_for("class", "place"), source_table="places")
            it.wikidata = KNOWN_PLACES.get(norm(f"{label}, {tail}")) if tail else None
            if it.wikidata:
                it.add(WIKIDATA_PROP, ext(it.wikidata))
            if clabel:
                ck = self.country(clabel)
                if ck:
                    it.add("country", item(ck))
            if city and region:
                rk = self.place("", region, clabel or "")
                if rk:
                    it.add("located in", item(rk))
            self.put(it)
            self.report["places"] += 1
        return key

    def country(self, label: str) -> str | None:
        ck = country_key(label)
        if ck:
            return ck
        key = key_for("country", slug(label))
        if key not in self.items:
            self.put(Item(key, "country", label, "country", [], key_for("class", "country"), source_table="places"))
        return key

    # ------------------------------------------------------------------ vocab lookups
    def technique_key(self, label: str) -> str | None:
        lab = _clean(label)
        if not lab:
            return None
        t = techniques.resolve(lab)
        if t:
            return key_for("technique", t["id"])
        low = norm(lab)
        for tid, tl, _g, _c in techniques.TECHNIQUES:
            if low == norm(tl) or (len(low) > 4 and (low in norm(tl) or norm(tl) in low)):
                return key_for("technique", tid)
        aliases = {"blown": "offhand-blown", "blown glass": "offhand-blown", "glassblowing": "offhand-blown",
                   "glass blowing": "offhand-blown", "hot glass": "offhand-blown", "furnace": "offhand-blown",
                   "cane": "cane-murrine", "murrine": "cane-murrine", "murrini": "cane-murrine", "incalmo": "offhand-blown",
                   "reticello": "cane-murrine", "flameworking": "flameworking", "flamework": "flameworking",
                   "lampworking": "flameworking", "lampwork": "flameworking", "torchwork": "flameworking",
                   "boro": "borosilicate-flameworking", "borosilicate": "borosilicate-flameworking",
                   "beadmaking": "soft-glass-beadmaking", "beads": "soft-glass-beadmaking", "fusing": "fusing-slumping",
                   "fused": "fusing-slumping", "slumping": "fusing-slumping", "kiln": "kiln-casting", "casting": "kiln-casting",
                   "kiln casting": "kiln-casting", "cast": "kiln-casting", "pate de verre": "pate-de-verre",
                   "cold working": "cold-working", "coldworking": "cold-working", "cold work": "cold-working",
                   "engraving": "engraving-sandblasting", "sandblasting": "engraving-sandblasting",
                   "stained glass": "stained-glass", "stained": "stained-glass", "leaded": "stained-glass",
                   "neon": "neon-plasma", "plasma": "neon-plasma", "mosaic": "mixed-media", "sculpture": "hot-sculpted",
                   "hot sculpting": "hot-sculpted", "sculpted": "hot-sculpted", "solid": "hot-sculpted",
                   "ornament": None, "mixed media": "mixed-media", "scientific": "scientific-apparatus",
                   "enamel": "enamel-grisaille", "painting": "enamel-grisaille", "restoration": "restoration-conservation"}
        for word, tid in aliases.items():
            if word in low:
                return key_for("technique", tid) if tid else None
        self.report["unknown_techniques"][lab] += 1
        return None

    def colour_key(self, name: str) -> str | None:
        n = norm(name)
        fix = {"gray": "grey", "colorless": "clear", "colourless": "clear", "transparent": "clear", "multicolor": "multicolour",
               "magenta": "purple", "lavender": "violet", "aqua": "teal", "cyan": "teal", "tan": "brown", "beige": "cream"}
        n = fix.get(n, n)
        return key_for("colour", n) if any(c == n for c, _ in COLOURS) else None

    def form_keys(self, *texts: str) -> list[str]:
        out = []
        for t in texts:
            for w in re.findall(r"[a-z]+", norm(t)):
                f = FORM_WORDS.get(w)
                if f and key_for("form", f) not in out:
                    out.append(key_for("form", f))
        return out[:2]

    def studio_class(self, type_text: str) -> str:
        t = norm(type_text)
        for word, cls in STUDIO_TYPES.items():
            if word in t:
                return key_for("class", cls)
        return key_for("class", "studio")

    # ------------------------------------------------------------------ people
    def person_from_row(self, table: str, row: dict, public: list[str], used: set, *, name_cols=NAME_COLS,
                        default_occupation: bool = True) -> Item | None:
        name = self.pick(row, name_cols, public, used)
        if not _looks_like_name(name):
            return None
        key = key_for(table, row.get("_row_id") or row.get("id") or _sha(row))
        based = self.pick(row, BASED_COLS, public, used)
        desc = "glass artist" if default_occupation else "person in the glass field"
        if based:
            city, region, country = _split_place(based)
            where = ", ".join(x for x in [city, region or country] if x) or based
            desc = f"{desc}, {where}"
        it = Item(key, "person", clip(name), clip(desc), [], key_for("class", "person"), source_table=table,
                  source_row=str(row.get("_row_id") or row.get("id") or ""),
                  url=f"{self.base}/explore/?view=datasets&q={_q(name)}")
        if default_occupation:
            it.add("occupation", item(key_for("occupation", "glass-artist")))
        roles = self.pick(row, ROLE_COLS, public, used)
        for r in split_list(roles):
            if r == "Artist or Maker":
                it.add("occupation", item(key_for("occupation", "glass-artist")))
            elif r in techniques.ROLES and not r.startswith(("Other", "Prefer")):
                it.add("occupation", item(key_for("occupation", slug(r))))
        if based:
            pk = self.place(free_text=based)
            if pk:
                it.add("work location", item(pk))
        web = _website(self.pick(row, WEB_COLS, public, used))
        if web and "instagram.com" not in web:
            it.add("official website", url(web))
        ig = _handle(self.pick(row, INSTAGRAM_COLS, public, used)) or (_handle(web) if "instagram.com" in (web or "") else "")
        if ig:
            it.add("Instagram username", ext(ig))
        for cols, ordinal in ((TECH_COLS, None), (TECH_COLS_2, "2"), (TECH_COLS_3, "3")):
            for t in split_list(self.pick(row, cols, public, used)):
                tk = self.technique_key(t)
                if tk:
                    it.add("technique", item(tk), [("series ordinal", string(ordinal))] if ordinal else None)
        qid = KNOWN_ENTITIES.get(("person", norm(name)))
        if qid:
            it.wikidata = qid
            it.add(WIKIDATA_PROP, ext(qid))
        return it

    def stage_people(self) -> None:
        for table in ("artists", "profiles", "artist_submissions", "artists_legacy"):
            rows = self.rows(table)
            public = self.public_cols(table) if rows else []
            used: set = set()
            made = 0
            for r in rows:
                it = self.person_from_row(table, r, public, used)
                if not it:
                    continue
                self.put(it, {k: r.get(k) for k in public})
                self.register_person(it.label, it.key)
                made += 1
            self.note_table(table, len(rows), used, public, made)

    def stage_museum_artists(self) -> None:
        table = "museum_artists"
        rows = self.rows(table)
        public = self.public_cols(table) if rows else []
        used: set = set()
        made = 0
        for r in rows:
            museum = self.pick(r, ("museum", "institution", "collection"), public, used)
            name = self.pick(r, ("artist", "artist_name", "name"), public, used)
            if not _looks_like_name(name):
                continue
            mkey = None
            if museum:
                mkey = key_for("museum", slug(museum))
                if mkey not in self.items:
                    m = Item(mkey, "museum", clip(museum), "museum holding glass", [], key_for("class", "museum"),
                             source_table=table)
                    q = KNOWN_ENTITIES.get(("museum", norm(museum)))
                    if q:
                        m.wikidata = q
                        m.add(WIKIDATA_PROP, ext(q))
                    self.put(m)
            pkey = self.person_key(name)
            if pkey:
                it = self.items[pkey]
            else:
                it = self.person_from_row(table, r, public, used, name_cols=("artist", "artist_name", "name"))
                if not it:
                    continue
                it.description = "glass artist held in museum collections"
                self.put(it, {k: r.get(k) for k in public})
                self.register_person(it.label, it.key)
                made += 1
            if mkey:
                it.add("collection", item(mkey))
        self.note_table(table, len(rows), used, public, made)

    def stage_rakow(self) -> None:
        table = "rakow_commissions"
        rows = self.rows(table)
        public = self.public_cols(table) if rows else []
        used: set = set()
        made = 0
        if rows:
            akey = key_for("award", "rakow-commission")
            a = Item(akey, "award", "Rakow Commission", "annual commission of the Corning Museum of Glass", [],
                     key_for("class", "award"), source_table=table, wikidata=KNOWN_ENTITIES[("award", "rakow commission")])
            a.add(WIKIDATA_PROP, ext(a.wikidata))
            self.put(a)
        for r in rows:
            name = self.pick(r, ("artist", "artist_name", "recipient", "name"), public, used)
            year = _year(self.pick(r, ("year", "date"), public, used))
            if not _looks_like_name(name):
                continue
            pkey = self.person_key(name)
            if pkey:
                it = self.items[pkey]
            else:
                it = self.person_from_row(table, r, public, used, name_cols=("artist", "artist_name", "recipient", "name"))
                if not it:
                    continue
                it.description = "glass artist, Rakow Commission recipient"
                self.put(it, {k: r.get(k) for k in public})
                self.register_person(it.label, it.key)
                made += 1
            it.add("award received", item(key_for("award", "rakow-commission")),
                   [("point in time", time_value(year))] if year else None)
        self.note_table(table, len(rows), used, public, made)

    # ------------------------------------------------------------------ studios
    def studio_from_row(self, table: str, row: dict, public: list[str], used: set) -> Item | None:
        name = self.pick(row, STUDIO_NAME_COLS, public, used)
        if not _looks_like_name(name):
            return None
        key = key_for(table, row.get("_row_id") or row.get("id") or _sha(row))
        city = self.pick(row, PLACE_COLS["city"], public, used)
        region = self.pick(row, PLACE_COLS["region"], public, used)
        country = self.pick(row, PLACE_COLS["country"], public, used)
        typ = self.pick(row, TYPE_COLS, public, used)
        clab = country_label(country) or country
        reg = US_STATES.get(region.upper(), region) if region else ""
        where = ", ".join(x for x in [city, reg if reg and (not clab or clab == "United States") else clab] if x)
        what = (typ.lower() if typ and len(typ) < 40 else "glass studio")
        if what in ("studio", "other", "mixed"):
            what = "glass studio"
        it = Item(key, "studio", clip(name), clip(f"{what} in {where}" if where else what), [], self.studio_class(typ),
                  source_table=table, source_row=str(row.get("_row_id") or row.get("id") or ""),
                  url=f"{self.base}/studios.html?q={_q(name)}")
        if it.cls != key_for("class", "studio"):
            it.add(INSTANCE_OF, item(key_for("class", "studio")))    # a hot shop is also a glass studio
        pk = self.place(city, region, country)
        if pk:
            it.add("located in" if (city or region) else "country", item(pk))
        ck = self.country(country_label(country)) if country_label(country) else None
        if ck and (city or region):
            it.add("country", item(ck))
        lat, lng = self.pick(row, LAT_COLS, public, used), self.pick(row, LNG_COLS, public, used)
        try:
            if lat and lng and abs(float(lat)) <= 90 and abs(float(lng)) <= 180 and (float(lat) or float(lng)):
                it.add("coordinates", coordinate(lat, lng))
        except ValueError:
            pass
        founded = _year(self.pick(row, FOUNDED_COLS, public, used))
        if founded:
            it.add("inception", time_value(founded))
        web = _website(self.pick(row, WEB_COLS, public, used))
        if web and "instagram.com" not in web:
            it.add("official website", url(web))
        ig = _handle(self.pick(row, INSTAGRAM_COLS, public, used))
        if ig:
            it.add("Instagram username", ext(ig))
        return it

    def stage_studios(self) -> None:
        for table in ("studios", "studio_submissions", "studios_directory_legacy"):
            rows = self.rows(table)
            public = self.public_cols(table) if rows else []
            used: set = set()
            made = 0
            for r in rows:
                it = self.studio_from_row(table, r, public, used)
                if not it:
                    continue
                self.put(it, {k: r.get(k) for k in public})
                self.studios.setdefault(norm(it.label), it.key)
                made += 1
            self.note_table(table, len(rows), used, public, made)

    def studio_key(self, name: str, create_from: str = "") -> str | None:
        k = self.studios.get(norm(name))
        if k or not create_from or not _looks_like_name(name):
            return k
        key = key_for("studio", slug(name))
        it = Item(key, "studio", clip(name), "glass studio", [], key_for("class", "studio"), source_table=create_from,
                  url=f"{self.base}/studios.html?q={_q(name)}")
        self.put(it)
        self.studios[norm(name)] = key
        return key

    # ------------------------------------------------------------------ schools, funders
    def stage_schools(self) -> None:
        for table in ("university_programs", "programs"):
            rows = self.rows(table)
            public = self.public_cols(table) if rows else []
            used: set = set()
            made = 0
            for r in rows:
                inst = self.pick(r, INSTITUTION_COLS, public, used)
                prog = self.pick(r, PROGRAM_COLS, public, used)
                if not _looks_like_name(inst):
                    continue
                skey = key_for("school", slug(inst))
                city = self.pick(r, PLACE_COLS["city"], public, used)
                region = self.pick(r, PLACE_COLS["region"], public, used)
                country = self.pick(r, PLACE_COLS["country"], public, used)
                web = _website(self.pick(r, WEB_COLS, public, used))
                if skey not in self.items:
                    s = Item(skey, "school", clip(inst), "school with a glass program", [], key_for("class", "school"),
                             source_table=table, url=f"{self.base}/explore/?view=datasets&q={_q(inst)}")
                    q = KNOWN_ENTITIES.get(("school", norm(inst)))
                    if q:
                        s.wikidata = q
                        s.add(WIKIDATA_PROP, ext(q))
                    pk = self.place(city, region, country)
                    if pk:
                        s.add("located in" if (city or region) else "country", item(pk))
                    if web:
                        s.add("official website", url(web))
                    self.put(s, {k: r.get(k) for k in public})
                    made += 1
                if prog and norm(prog) != norm(inst):
                    pkey = key_for(table, r.get("_row_id") or _sha(r))
                    p = Item(pkey, "program", clip(prog), clip(f"glass program at {inst}"), [], key_for("class", "program"),
                             source_table=table, source_row=str(r.get("_row_id") or ""),
                             url=f"{self.base}/explore/?view=datasets&q={_q(inst)}")
                    p.add("part of", item(skey))
                    if web:
                        p.add("official website", url(web))
                    self.put(p, {k: r.get(k) for k in public})
                    made += 1
            self.note_table(table, len(rows), used, public, made)

    def stage_funders(self) -> None:
        table = "funding"
        rows = self.rows(table)
        public = self.public_cols(table) if rows else []
        used: set = set()
        made = 0
        for r in rows:
            name = self.pick(r, ("funding_source", "source", "funder", "organization", "name"), public, used)
            if not _looks_like_name(name):
                continue
            typ = self.pick(r, ("type", "kind", "category"), public, used)
            it = Item(key_for(table, r.get("_row_id") or _sha(r)), "funder", clip(name),
                      clip(f"{typ.lower()} funding work in glass" if typ and len(typ) < 60 else "funder of work in glass"),
                      [], key_for("class", "funder"), source_table=table, source_row=str(r.get("_row_id") or ""),
                      url=f"{self.base}/explore/?view=datasets&q={_q(name)}")
            web = _website(self.pick(r, WEB_COLS, public, used))
            if web:
                it.add("official website", url(web))
            self.put(it, {k: r.get(k) for k in public})
            made += 1
        self.note_table(table, len(rows), used, public, made)

    # ------------------------------------------------------------------ shows
    def stage_shows(self) -> None:
        # maker-hosted shows and sales: listed ones only
        table = "maker_events"
        rows = self.rows(table, gate=False, where="listing='approved' AND status<>'draft'")
        used: set = set()
        public = ["title", "host_name", "studio", "venue", "city", "region", "country", "starts", "ends", "description",
                  "piece_type", "slug"]
        made = 0
        for r in rows:
            title = _clean(r.get("title"))
            if not title:
                continue
            used.update(k for k in public if _clean(r.get(k)))
            year = _year(r.get("starts") or "")
            it = Item(key_for(table, r["id"]), "show", clip(f"{title} ({year})" if year else title),
                      clip(" ".join(x for x in [f"show or sale by {r.get('host_name') or r.get('studio') or 'a maker'}",
                                               f"at {r['venue']}" if _clean(r.get("venue")) else "",
                                               f"in {r['city']}" if _clean(r.get("city")) else ""] if x)),
                      [], key_for("class", "show"), source_table=table, source_row=str(r["id"]),
                      url=f"{self.base}/events.html#ev-{r.get('slug') or r['id']}")
            if time_value(r.get("starts") or ""):
                it.add("start time", time_value(r["starts"]))
            if time_value(r.get("ends") or ""):
                it.add("end time", time_value(r["ends"]))
            pk = self.place(r.get("city") or "", r.get("region") or "", r.get("country") or "")
            if pk:
                it.add("located in", item(pk))
            sk = self.studio_key(r.get("studio") or r.get("host_name") or "")
            if sk:
                it.add("participant", item(sk))
            for fk in self.form_keys(r.get("piece_type") or "", title):
                it.add("form", item(fk))
            self.put(it, {k: r.get(k) for k in public})
            made += 1
        self.note_table(table, len(rows), used, public, made)
        # ingested shows, calls, fairs
        for table, cls in (("events", "show"), ("open_calls", "show"), ("trade_craft_retail", "trade-fair"),
                           ("trade_wholesale", "trade-fair"), ("trade_interior_arch", "trade-fair"), ("art_fairs", "art-fair")):
            rows = self.rows(table)
            public = self.public_cols(table) if rows else []
            used = set()
            made = 0
            for r in rows:
                name = self.pick(r, SHOW_NAME_COLS, public, used)
                if not _looks_like_name(name):
                    continue
                city = self.pick(r, PLACE_COLS["city"] + ("location", "where"), public, used)
                region = self.pick(r, PLACE_COLS["region"], public, used)
                country = self.pick(r, PLACE_COLS["country"], public, used)
                start = self.pick(r, DATE_COLS, public, used)
                end = self.pick(r, END_COLS, public, used)
                web = _website(self.pick(r, WEB_COLS, public, used))
                what = {"show": "show or open call", "trade-fair": "trade show", "art-fair": "art fair"}[cls]
                it = Item(key_for(table, r.get("_row_id") or _sha(r)), "show", clip(name),
                          clip(f"{what} in {city}" if city else what), [], key_for("class", cls), source_table=table,
                          source_row=str(r.get("_row_id") or ""), url=f"{self.base}/explore/?view=datasets&q={_q(name)}")
                tv = time_value(start) if start else None
                if tv:
                    it.add("start time", tv)
                tv = time_value(end) if end else None
                if tv:
                    it.add("end time", tv)
                if city or region or country:
                    pk = self.place(free_text=city) if (city and not region and not country) else self.place(city, region, country)
                    if pk:
                        it.add("located in", item(pk))
                if web:
                    it.add("official website", url(web))
                self.put(it, {k: r.get(k) for k in public})
                made += 1
            self.note_table(table, len(rows), used, public, made)

    # ------------------------------------------------------------------ seasons and pieces
    def stage_seasons(self) -> dict[str, list[dict]]:
        """Collecting-event items; returns {object_row_id: [entry facts]} for the pieces."""
        by_object: dict[str, list[dict]] = {}
        if not self.has("seasons"):
            return by_object
        rows = self.rows("seasons", gate=False, where="status<>'draft'")
        made = 0
        for s in rows:
            key = key_for("seasons", s["id"])
            it = Item(key, "season", clip(s.get("title") or s.get("slug") or "collecting event"),
                      clip(s.get("theme") or "collecting event on the Glass Database"), [], key_for("class", "season"),
                      source_table="seasons", source_row=str(s["id"]), url=f"{self.base}/games.html?season={s.get('slug')}")
            for prop, col in (("start time", "starts"), ("end time", "ends")):
                tv = time_value(s.get(col) or "")
                if tv:
                    it.add(prop, tv)
            self.put(it, {k: s.get(k) for k in ("title", "theme", "starts", "ends", "status", "slug")})
            made += 1
            closed = s.get("status") == "closed"
            entries = store.rows(self.conn.execute(
                "SELECT e.*, (SELECT COUNT(*) FROM season_votes v WHERE v.entry_id=e.id) AS votes FROM season_entries e "
                "WHERE e.season_id=? AND e.status='approved'", (s["id"],)))
            ranked = sorted(entries, key=lambda e: (-int(e.get("votes") or 0), e.get("created_at") or "", e["id"]))
            for i, e in enumerate(ranked, 1):
                by_object.setdefault(e["object_row_id"], []).append(
                    {"season_key": key, "season_title": s.get("title"), "studio": e.get("studio") or "",
                     "edition_size": e.get("edition_size"), "rank": i if closed else None, "closed": closed,
                     "when": s.get("starts") or "", "won": closed and i == 1 and int(e.get("votes") or 0) > 0,
                     "closes": s.get("voting_closes") or s.get("ends") or ""})
        if any(x["won"] for xs in by_object.values() for x in xs):
            self.put(Item(key_for("award", "crowd-favourite"), "award", "Crowd Favourite",
                          "people's choice of a Glass Database collecting event", [], key_for("class", "award"),
                          source_table="seasons", url=f"{self.base}/games.html"))
        self.note_table("seasons", len(rows), {"title", "theme", "starts", "ends", "status"}, [], made)
        return by_object

    def stage_objects(self, entries: dict[str, list[dict]]) -> None:
        table = "objects"
        rows = self.rows(table)
        public = self.public_cols(table) if rows else []
        used: set = set()
        made = 0
        for r in rows:
            rid = r["_row_id"]
            title = _clean(r.get("title")) or "Untitled piece"
            maker = _clean(r.get("maker"))
            year = _year(r.get("year") or "")
            used.update(k for k in ("title", "maker", "year", "techniques", "dimensions", "description", "content_hash")
                        if _clean(r.get(k)))
            key = key_for("object", r.get("content_hash") or rid)
            it = Item(key, "object", clip(title), clip(f"glass piece by {maker}" + (f", {year}" if year else "")
                                                       if maker else (f"glass piece, {year}" if year else "glass piece")),
                      [], key_for("class", "object"), source_table=table, source_row=rid,
                      url=f"{self.base}/explore/?view=objects#obj-{rid}")
            it.add("title", mono(title))
            if maker:
                mk = self.person_key(maker)
                if not mk:
                    m = Item(key_for("maker", slug(maker)), "person", clip(maker), "glass maker", [], key_for("class", "person"),
                             source_table=table, url=f"{self.base}/explore/?view=datasets&q={_q(maker)}")
                    m.add("occupation", item(key_for("occupation", "glass-artist")))
                    self.put(m)
                    self.register_person(maker, m.key)
                    mk = m.key
                it.add("maker", item(mk))
            if year:
                it.add("inception", time_value(year))
            for t in split_list(r.get("techniques") or ""):
                tk = self.technique_key(t)
                if tk:
                    it.add("technique", item(tk))
            man = _json(r.get("manifest_json"))
            fp = _json(r.get("fingerprint_json")) or man.get("fingerprint") or {}
            theme = " ".join(x.get("season_title") or "" for x in entries.get(rid, []))
            for fk in self.form_keys(title, r.get("techniques") or "", theme):
                it.add("form", item(fk))
            colour = ((fp.get("metadata") or {}).get("dominantColor") or {}) if isinstance(fp, dict) else {}
            ck = self.colour_key(colour.get("name") or "") if isinstance(colour, dict) else None
            if ck:
                it.add("colour", item(ck), [("determination method", item(key_for("event-type", "verified")))])
            dims = re.match(r"^\s*([\d.]+)\s*[×x*]\s*([\d.]+)\s*[×x*]\s*([\d.]+)\s*mm", str(r.get("dimensions") or ""), re.I)
            if dims:
                for prop, v in (("width", dims.group(1)), ("depth", dims.group(2)), ("height", dims.group(3))):
                    it.add(prop, quantity(v, key_for("unit", "mm")))
            if r.get("content_hash"):
                it.add("Content Credential hash", string(r["content_hash"]))
            if fp and isinstance(fp, dict):
                it.add("fingerprint SHA-256", string(hashlib.sha256(
                    json.dumps(fp, sort_keys=True, separators=(",", ":")).encode()).hexdigest()))
                rating = fp.get("rating") or r.get("fingerprint_rating")
                try:
                    if rating is not None and str(rating) != "":
                        it.add("Glass ID strength", quantity(int(float(rating))))
                except (TypeError, ValueError):
                    pass
            it.add("image URL", url(f"{self.base}/api/objects/{rid}/image"))
            it.add("described at URL", url(f"{self.base}/explore/?view=objects#obj-{rid}"))
            for ev in _events(man):
                et = key_for("event-type", ev.get("event_type") if ev.get("event_type") in
                             {c for c, _, _ in __import__("central.wikibase.model", fromlist=["EVENT_TYPES"]).EVENT_TYPES} else "other")
                quals = []
                tv = time_value(ev.get("event_date") or "")
                if tv:
                    quals.append(("point in time", tv))
                if _clean(ev.get("actor")):
                    quals.append(("subject named as", string(ev["actor"])))
                loc = _clean(ev.get("location"))
                if loc:
                    sk = self.studio_key(loc)
                    if sk:
                        quals.append(("location", item(sk)))
                it.add("story event", item(et), quals, dedupe=False)
            for e in entries.get(rid, []):
                quals = []
                tv = time_value(e.get("when") or "")
                if tv:
                    quals.append(("point in time", tv))
                if e.get("rank"):
                    quals.append(("series ordinal", string(str(e["rank"]))))
                it.add("participant in", item(e["season_key"]), quals)
                if e.get("edition_size") and not it.has("edition size"):
                    it.add("edition size", quantity(int(e["edition_size"])))
                if e.get("studio"):
                    sk = self.studio_key(e["studio"], create_from="season_entries")
                    if sk:
                        it.add("made at", item(sk))
                if e.get("won"):
                    q = [("point in time", time_value(e["closes"]))] if time_value(e.get("closes") or "") else []
                    it.add("award received", item(key_for("award", "crowd-favourite")), q)
            self.put(it, {k: r.get(k) for k in public} | {"entries": entries.get(rid, [])})
            made += 1
        self.note_table(table, len(rows), used, public, made)

    # ------------------------------------------------------------------ lineage
    def stage_lineage(self) -> None:
        table = "lineage_submissions"
        rows = self.rows(table)
        public = self.public_cols(table) if rows else []
        used: set = set()
        made = 0
        for r in rows:
            student = self.pick(r, ("student_name", "student"), public, used)
            mentor = self.pick(r, ("mentor_name", "mentor", "teacher"), public, used)
            if not (_looks_like_name(student) and _looks_like_name(mentor)) or norm(student) == norm(mentor):
                continue
            sk, mk = self.person_key(student), self.person_key(mentor)
            if not sk or not mk:
                self.report["held"].append({"table": table, "row": r.get("_row_id"),
                                            "why": f"{'student' if not sk else 'mentor'} not found among the people: "
                                                   f"{student if not sk else mentor}"})
                continue
            quals = [("subject named as", string(self.pick(r, ("relation",), public, used)))] \
                if self.pick(r, ("relation",), public, used) else []
            years = self.pick(r, ("years", "year", "when"), public, used)
            ys = re.findall(r"\b(1[89]\d\d|20\d\d)\b", years)
            if ys:
                quals.append(("start time", time_value(ys[0])))
                if len(ys) > 1:
                    quals.append(("end time", time_value(ys[-1])))
            where = self.pick(r, ("where", "studio", "school"), public, used)
            if where:
                wk = self.studio_key(where)
                if wk:
                    quals.append(("location", item(wk)))
            self.items[sk].add("student of", item(mk), quals)
            self.items[mk].add("student", item(sk), [q for q in quals if q[0] != "location"])
            made += 1
        self.note_table(table, len(rows), used, public, made)

    # ------------------------------------------------------------------ run
    def build(self) -> dict[str, Item]:
        self.log("staging the classes and vocabularies from code")
        for it in fixed_items():
            it.source_table = "code"
            self.put(it)
        self.log("people")
        self.stage_people()
        self.stage_museum_artists()
        self.stage_rakow()
        self.log("studios")
        self.stage_studios()
        self.log("schools and programs, funders")
        self.stage_schools()
        self.stage_funders()
        self.log("shows, sales and fairs")
        self.stage_shows()
        self.log("collecting events and published pieces")
        entries = self.stage_seasons()
        self.stage_objects(entries)
        self.log("lineage")
        self.stage_lineage()
        if self.report["unknown_techniques"]:
            top = ", ".join(f"{k} ({n})" for k, n in self.report["unknown_techniques"].most_common(12))
            self.log(f"  technique words with no vocabulary term (left out): {top}")
        self.log(f"  {self.report['places']} place items made from cities, regions and countries")
        return self.items


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _json(s) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s) if isinstance(s, str) else s
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _events(man: dict) -> list[dict]:
    out = []
    for a in man.get("assertions") or []:
        if isinstance(a, dict) and a.get("label") == "org.glassdatabase.provenance.events":
            for ev in a.get("data") or []:
                if isinstance(ev, dict):
                    out.append(ev)
    if not out and isinstance(man.get("events"), list):
        out = [e for e in man["events"] if isinstance(e, dict)]
    return out


def run(conn, log: Log = print, params: dict | None = None) -> dict:
    """The *stage* job: build every item and upsert it; mark orphans. Returns the summary."""
    params = params or {}
    store.ensure(conn)
    base = params.get("public_base") or store.get_settings(conn)["public_base"]
    st = Stager(conn, base, log)
    items = st.build()
    seen = set()
    statuses: Counter = Counter()
    for key, it in items.items():
        seen.add(key)
        status = store.upsert_item(conn, it, source_hash=st.hashes.get(key, ""))
        statuses[status] += 1
    conn.commit()
    # rows that vanished
    gone = store.rows(conn.execute("SELECT key, qid, status FROM wb_items"))
    orphaned = dropped = 0
    for r in gone:
        if r["key"] in seen:
            continue
        if r["qid"]:
            if r["status"] != "orphan":
                store.set_item(conn, r["key"], status="orphan", notes="source row is gone; the wiki item stays")
                orphaned += 1
        else:
            conn.execute("DELETE FROM wb_items WHERE key=?", (r["key"],))
            dropped += 1
    conn.commit()
    kinds = Counter(it.kind for it in items.values())
    log(f"staged {len(items)} items: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    log("status after staging: " + ", ".join(f"{k} {n}" for k, n in sorted(statuses.items()))
        + (f"; {orphaned} orphaned" if orphaned else "") + (f"; {dropped} dropped" if dropped else ""))
    for h in st.report["held"][:20]:
        log(f"  held: {h['table']} {h['row']}: {h['why']}")
    return {"items": len(items), "kinds": dict(kinds), "statuses": dict(statuses), "orphaned": orphaned,
            "dropped": dropped, "tables": st.report["tables"], "held": st.report["held"],
            "unknown_techniques": dict(st.report["unknown_techniques"].most_common(50)), "places": st.report["places"]}
