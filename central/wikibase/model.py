"""
central.wikibase.model
======================
The schema of the Glass Database's wiki, and the shapes the other modules pass around.

* ``PROPERTIES``: our properties. There are no federated properties on wikibase.cloud, so
  each one is ours, and the ones with a Wikidata twin say so (``equivalent property`` →
  the Wikidata property's URL), which is what lets a statement here be copied to Wikidata
  without translation. Property ids were checked against Wikidata on 2026-10-31.
* ``CLASSES``: the class items (``instance of`` targets), each anchored to a Wikidata item
  when an exact one exists.
* The vocabularies (``vocab_items()``): techniques (from ``central.techniques``), the trait
  thesaurus (``central.glass_traits``), colours, forms, occupations, story-event types,
  studio types, units, the ``Glass Database`` source item, countries.
* ``Item`` / ``Claim`` / ``Value``: a would-be wiki item with its statements, stored as JSON
  in the staging table and turned into API bodies by ``push``.
* ``key_for`` / ``kind_of``: every item has a key ``gdb:<kind>:<id>`` (``gdb:artists:<row id>``
  for an ingested row, ``gdb:object:<content hash>`` for a piece, ``gdb:technique:<slug>`` for
  a vocabulary term). The key is written on the item as its *Glass Database ID*, so a wiki
  item and our row always find each other, in either direction.

What stays out on purpose: dates of birth and death, place of birth, gender, ethnicity,
email, phone, home addresses, prices paid, buyers, votes in progress, collectors and their
collections, fingerprint vectors. A person may add their own birth year on the wiki; the
bot never writes it.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

SITE_DEFAULT = "https://glass.wikibase.cloud"
PUBLIC_BASE_DEFAULT = "https://glassdatabase.org"
LANG = "en"
LABEL_MAX = 250            # Wikibase's limit on labels, descriptions and aliases
STRING_MAX = 400           # string datatype
URL_MAX = 500
WIKIDATA_ENTITY = "http://www.wikidata.org/entity/"
GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
EARTH = "http://www.wikidata.org/entity/Q2"
LICENCE_NOTE = ("Statements are published as CC0 so they can flow to Wikidata; photographs and "
                "stories keep their own licences and stay on glassdatabase.org.")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- properties
@dataclass(frozen=True)
class Prop:
    label: str
    datatype: str                    # item | string | external-id | url | time | quantity | monolingualtext | globe-coordinate
    description: str
    wikidata: str | None = None      # the Wikidata property this one mirrors
    formatter: str | None = None     # external-id formatter URL ($1 = the id)


KEY_PROP = "Glass Database ID"
WIKIDATA_PROP = "Wikidata ID"
INSTANCE_OF = "instance of"
SUBCLASS_OF = "subclass of"

PROPERTIES: list[Prop] = [
    Prop(INSTANCE_OF, "item", "the class this item is a particular example of", "P31"),
    Prop(SUBCLASS_OF, "item", "every instance of this class is also an instance of that one", "P279"),
    Prop(KEY_PROP, "external-id", "the key of this item in the Glass Database (gdb:<kind>:<id>); the round-trip key",
         None, PUBLIC_BASE_DEFAULT + "/api/wikibase/go/$1"),
    Prop(WIKIDATA_PROP, "external-id", "the same thing on Wikidata", None, "https://www.wikidata.org/wiki/$1"),
    Prop("exact match", "url", "a concept in another vocabulary that means exactly this", "P2888"),
    Prop("equivalent property", "url", "the property in another vocabulary (Wikidata) that this property mirrors", "P1628"),
    Prop("formatter URL", "string", "web page of an identifier: $1 stands for the id", "P1630"),
    Prop("AAT ID", "external-id", "Getty Art & Architecture Thesaurus id", "P1014", "http://vocab.getty.edu/page/aat/$1"),
    Prop("ULAN ID", "external-id", "Getty Union List of Artist Names id", "P245", "http://vocab.getty.edu/page/ulan/$1"),
    Prop("VIAF ID", "external-id", "Virtual International Authority File id", "P214", "https://viaf.org/viaf/$1/"),
    Prop("Instagram username", "external-id", "Instagram account", "P2003", "https://www.instagram.com/$1/"),
    Prop("official website", "url", "the website of the person, studio, school or show", "P856"),
    Prop("country", "item", "the country", "P17"),
    Prop("located in", "item", "the city or region", "P131"),
    Prop("coordinates", "globe-coordinate", "where it is", "P625"),
    Prop("inception", "time", "when it was founded, made or started", "P571"),
    Prop("work location", "item", "where the person works (a city or region, never an address)", "P937"),
    Prop("occupation", "item", "what the person does in the glass field", "P106"),
    Prop("technique", "item", "a technique the person works in, or a piece was made with", "P2079"),
    Prop("student of", "item", "who the person studied or apprenticed under", "P1066"),
    Prop("student", "item", "who studied under the person", "P802"),
    Prop("educated at", "item", "school or program the person attended", "P69"),
    Prop("worked at", "item", "studio or organisation the person has worked at", "P108"),
    Prop("part of", "item", "the whole this is a part of (a program's school, a show's series)", "P361"),
    Prop("maker", "item", "who made the piece", "P170"),
    Prop("title", "monolingualtext", "the title of the piece", "P1476"),
    Prop("made from material", "item", "the glass or other material of the piece", "P186"),
    Prop("colour", "item", "the dominant colour of the piece", "P462"),
    Prop("form", "item", "what kind of thing the piece is (pumpkin, ornament, goblet …)", "P1419"),
    Prop("height", "quantity", "height of the piece in millimetres", "P2048"),
    Prop("width", "quantity", "width of the piece in millimetres", "P2049"),
    Prop("depth", "quantity", "depth of the piece in millimetres"),
    Prop("edition size", "quantity", "how many were made in this edition", "P1092"),
    Prop("made at", "item", "the studio the piece was made at", "P1071"),
    Prop("story event", "item", "an event in the piece's story: made, sold, exhibited, loaned, restored, checked", "P793"),
    Prop("participant in", "item", "a collecting event or show the piece or person took part in", "P1344"),
    Prop("award received", "item", "an award or commission", "P166"),
    Prop("collection", "item", "a museum or collection that holds the person's work", "P195"),
    Prop("image URL", "url", "the piece's picture on glassdatabase.org (the signed rendition)", "P4765"),
    Prop("described at URL", "url", "the page about this on glassdatabase.org", "P973"),
    Prop("Content Credential hash", "string", "the content hash of the piece's provenance manifest (C2PA)"),
    Prop("fingerprint SHA-256", "string", "SHA-256 of the piece's Glass ID fingerprint"),
    Prop("Glass ID strength", "quantity", "how strongly the piece can be re-identified, 0 to 100"),
    Prop("determination method", "item", "how a value was arrived at (qualifier: a model, a scan, a person)", "P459"),
    Prop("stated in", "item", "the source of a statement (reference)", "P248"),
    Prop("reference URL", "url", "where a statement was taken from (reference)", "P854"),
    Prop("retrieved", "time", "when the source was read (reference)", "P813"),
    Prop("start time", "time", "when it began", "P580"),
    Prop("end time", "time", "when it ended", "P582"),
    Prop("point in time", "time", "the date of an event (qualifier)", "P585"),
    Prop("participant", "item", "a person or studio that took part (qualifier)", "P710"),
    Prop("location", "item", "where it happened (qualifier)", "P276"),
    Prop("subject named as", "string", "how the source names someone or something (qualifier)", "P1810"),
    Prop("series ordinal", "string", "position in a series or a ranking (qualifier)", "P1545"),
    Prop("different from", "item", "a similar-looking item this one must not be merged with", "P1889"),
]
PROP_BY_LABEL = {p.label: p for p in PROPERTIES}


# --------------------------------------------------------------------------- classes
@dataclass(frozen=True)
class Cls:
    slug: str
    label: str
    description: str
    wikidata: str | None = None      # the item on Wikidata that is exactly this class
    parent: str | None = None        # slug of the class this one is a subclass of


CLASSES: list[Cls] = [
    Cls("person", "person", "a person: a maker, a teacher, a student, a dealer", "Q5"),
    Cls("studio", "glass studio", "a place where glass is made: hot shop, flame shop, kiln studio, cold shop, teaching studio"),
    Cls("hot-shop", "hot shop", "a furnace glass studio", parent="studio"),
    Cls("flame-shop", "flameworking studio", "a torch-based glass studio", parent="studio"),
    Cls("kiln-studio", "kiln studio", "a kiln-forming, fusing or casting studio", parent="studio"),
    Cls("cold-shop", "cold shop", "a grinding, polishing and engraving studio", parent="studio"),
    Cls("teaching-studio", "teaching studio", "a studio whose main work is classes and access", parent="studio"),
    Cls("mixed-studio", "mixed glass studio", "a studio working in more than one process", parent="studio"),
    Cls("school", "school or program", "an institution or program where glass is taught"),
    Cls("program", "glass program", "a degree or certificate program in glass at a school", parent="school"),
    Cls("museum", "museum", "a museum that holds or shows glass"),
    Cls("funder", "funder", "an organisation that funds work in glass"),
    Cls("object", "glass object", "one piece of glass, published through the Glass Database", "Q5567091"),
    Cls("technique", "glass technique", "a way of working glass"),
    Cls("form", "form", "what kind of thing a piece is: pumpkin, ornament, goblet, paperweight"),
    Cls("colour", "colour", "a colour of glass"),
    Cls("trait", "glass trait", "an observable feature of a piece from the trait thesaurus"),
    Cls("show", "show or sale", "a show, sale, fair or exhibition where glass is shown or sold"),
    Cls("art-exhibition", "art exhibition", "an exhibition of art", "Q667276", parent="show"),
    Cls("art-fair", "art fair", "a fair where galleries and artists sell art", "Q1792535", parent="show"),
    Cls("trade-fair", "trade fair", "a trade show", "Q57305", parent="show"),
    Cls("season", "collecting event", "a time-boxed collecting game on the Glass Database, with awards"),
    Cls("award", "award or commission", "an award, prize or commission"),
    Cls("place", "place", "a city, region or country"),
    Cls("country", "country", "a country", parent="place"),
    Cls("occupation", "occupation", "a role a person plays in the glass field"),
    Cls("event-type", "story event type", "a kind of event in a piece's story"),
    Cls("unit", "unit of measure", "a unit"),
    Cls("source", "source", "a database or publication statements are taken from"),
]
CLASS_BY_SLUG = {c.slug: c for c in CLASSES}

# The order kinds are written in: what others point at comes first.
KIND_ORDER = ["class", "unit", "source", "occupation", "event-type", "technique", "trait", "colour", "form",
              "country", "place", "award", "museum", "funder", "school", "program", "person", "studio", "show",
              "season", "object"]


# --------------------------------------------------------------------------- keys and text
_slug_re = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    s = _slug_re.sub("-", s).strip("-")
    return s[:80] or "x"


def norm(text: str) -> str:
    """The comparison form of a name: no accents, case, punctuation or double spaces."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def key_for(kind: str, ident: str) -> str:
    return f"gdb:{kind}:{ident}"


def kind_of(key: str) -> str:
    parts = (key or "").split(":", 2)
    return parts[1] if len(parts) >= 2 else ""


def ident_of(key: str) -> str:
    parts = (key or "").split(":", 2)
    return parts[2] if len(parts) == 3 else ""


def clip(s: str | None, n: int = LABEL_MAX) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- the shapes
@dataclass
class Value:
    kind: str                         # item | string | external-id | url | time | quantity | monolingual | coordinate
    value: str
    unit: str | None = None           # quantity: an item key (gdb:unit:mm) or None
    lang: str | None = None           # monolingual
    precision: int | None = None      # time: 9 year, 10 month, 11 day

    def sig(self) -> tuple:
        return (self.kind, self.value, self.unit, self.lang, self.precision)

    def to_json(self) -> dict:
        d = {"k": self.kind, "v": self.value}
        if self.unit:
            d["u"] = self.unit
        if self.lang:
            d["l"] = self.lang
        if self.precision is not None:
            d["p"] = self.precision
        return d

    @staticmethod
    def from_json(d: dict) -> "Value":
        return Value(d["k"], d["v"], d.get("u"), d.get("l"), d.get("p"))


def item(key: str) -> Value:
    return Value("item", key)


def string(s: str) -> Value:
    return Value("string", clip(s, STRING_MAX))


def ext(s: str) -> Value:
    return Value("external-id", clip(s, STRING_MAX))


def url(u: str) -> Value:
    return Value("url", str(u).strip()[:URL_MAX])


def mono(text: str, lang: str = LANG) -> Value:
    return Value("monolingual", clip(text, STRING_MAX), lang=lang)


def quantity(amount, unit: str | None = None) -> Value:
    return Value("quantity", str(amount), unit=unit)


def coordinate(lat, lng) -> Value:
    return Value("coordinate", f"{float(lat):.5f},{float(lng):.5f}")


def time_value(text: str) -> Value | None:
    """``2026``, ``2026-10``, ``2026-10-31`` (and ISO timestamps) → a time value with the
    precision the text carries; anything else → None."""
    s = str(text or "").strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    if y < 1000 or y > 2100:
        return None
    if d and mo:
        return Value("time", f"{y:04d}-{int(mo):02d}-{int(d):02d}", precision=11)
    if mo:
        return Value("time", f"{y:04d}-{int(mo):02d}-00", precision=10)
    return Value("time", f"{y:04d}-00-00", precision=9)


@dataclass
class Claim:
    prop: str
    value: Value
    qualifiers: list[tuple[str, Value]] = field(default_factory=list)
    references: list[list[tuple[str, Value]]] = field(default_factory=list)

    def to_json(self) -> dict:
        d = {"p": self.prop, "v": self.value.to_json()}
        if self.qualifiers:
            d["q"] = [[p, v.to_json()] for p, v in self.qualifiers]
        if self.references:
            d["r"] = [[[p, v.to_json()] for p, v in ref] for ref in self.references]
        return d

    @staticmethod
    def from_json(d: dict) -> "Claim":
        return Claim(d["p"], Value.from_json(d["v"]),
                     [(p, Value.from_json(v)) for p, v in d.get("q", [])],
                     [[(p, Value.from_json(v)) for p, v in ref] for ref in d.get("r", [])])

    def sig(self) -> tuple:
        return (self.prop, *self.value.sig())


@dataclass
class Item:
    key: str
    kind: str
    label: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    cls: str | None = None            # a class key (gdb:class:person) the item is an instance of
    claims: list[Claim] = field(default_factory=list)
    source_table: str = ""
    source_row: str = ""
    url: str = ""                     # the page on the site a reference points at
    wikidata: str | None = None       # the Wikidata twin, when known
    notes: list[str] = field(default_factory=list)

    def add(self, prop: str, value: Value | None, qualifiers=None, dedupe: bool = True) -> None:
        if value is None or value.value in (None, ""):
            return
        c = Claim(prop, value, list(qualifiers or []))
        if dedupe and any(x.sig() == c.sig() for x in self.claims):
            return
        self.claims.append(c)

    def has(self, prop: str) -> bool:
        return any(c.prop == prop for c in self.claims)

    def values(self, prop: str) -> list[str]:
        return [c.value.value for c in self.claims if c.prop == prop]

    def to_json(self) -> dict:
        return {"key": self.key, "kind": self.kind, "label": self.label, "description": self.description,
                "aliases": self.aliases, "cls": self.cls, "claims": [c.to_json() for c in self.claims],
                "source_table": self.source_table, "source_row": self.source_row, "url": self.url,
                "wikidata": self.wikidata, "notes": self.notes}

    @staticmethod
    def from_json(d: dict) -> "Item":
        return Item(d["key"], d["kind"], d.get("label", ""), d.get("description", ""), list(d.get("aliases") or []),
                    d.get("cls"), [Claim.from_json(c) for c in d.get("claims", [])], d.get("source_table", ""),
                    d.get("source_row", ""), d.get("url", ""), d.get("wikidata"), list(d.get("notes") or []))

    def content_hash(self) -> str:
        """A stable digest of everything the pusher would send: a changed source row changes it."""
        body = {"label": self.label, "description": self.description, "aliases": sorted(self.aliases),
                "cls": self.cls, "wikidata": self.wikidata,
                "claims": sorted(json.dumps(c.to_json(), sort_keys=True, ensure_ascii=False) for c in self.claims)}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------- known ids
# Countries: every spelling the sheets use → (label, Wikidata id). Add here when a new one appears.
COUNTRIES: dict[str, tuple[str, str | None]] = {}


def _country(label: str, qid: str | None, *aliases: str) -> None:
    for a in (label, *aliases):
        COUNTRIES[norm(a)] = (label, qid)


_country("United States", "Q30", "USA", "US", "U.S.", "U.S.A.", "United States of America", "America")
_country("United Kingdom", "Q145", "UK", "U.K.", "GB", "Great Britain", "Britain")
_country("England", "Q21"); _country("Scotland", "Q22"); _country("Wales", "Q25")
_country("Italy", "Q38", "IT", "Italia"); _country("Canada", "Q16", "CA"); _country("Germany", "Q183", "DE", "Deutschland")
_country("France", "Q142", "FR"); _country("Japan", "Q17", "JP"); _country("Australia", "Q408", "AU")
_country("Czech Republic", "Q213", "CZ", "Czechia"); _country("Netherlands", "Q55", "NL", "The Netherlands", "Holland")
_country("Sweden", "Q34", "SE"); _country("Denmark", "Q35", "DK"); _country("Spain", "Q29", "ES", "España")
_country("Mexico", "Q96", "MX", "México"); _country("New Zealand", "Q664", "NZ"); _country("Ireland", "Q27", "IE")
_country("Finland", "Q33", "FI"); _country("Norway", "Q20", "NO"); _country("Belgium", "Q31", "BE"); _country("Austria", "Q40", "AT")
_country("Switzerland", "Q39", "CH"); _country("Poland", "Q36", "PL"); _country("Türkiye", "Q43", "TR", "Turkey")
_country("Israel", "Q801", "IL"); _country("India", "Q668", "IN"); _country("China", "Q148", "CN"); _country("Brazil", "Q155", "BR", "Brasil")
_country("Argentina", "Q414", "AR"); _country("South Africa", "Q258", "ZA"); _country("Portugal", "Q45", "PT")
_country("South Korea", "Q884", "KR", "Korea"); _country("Taiwan", "Q865", "TW"); _country("Greece", "Q41", "GR")
_country("Hungary", "Q28", "HU"); _country("Slovakia", "Q214", "SK"); _country("Croatia", "Q224", "HR"); _country("Iceland", "Q189", "IS")
_country("Estonia", "Q191", "EE"); _country("Latvia", "Q211", "LV"); _country("Lithuania", "Q37", "LT"); _country("Chile", "Q298", "CL")
_country("Colombia", "Q739", "CO"); _country("Philippines", "Q928", "PH"); _country("Thailand", "Q869", "TH")
_country("Singapore", "Q334", "SG"); _country("United Arab Emirates", "Q878", "AE", "UAE"); _country("Egypt", "Q79", "EG")

# US states and a few regions: abbreviation → name (place labels read "Crozet, Virginia").
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
US_STATE_NAMES = {norm(v): v for v in US_STATES.values()}

# Places whose Wikidata item is certain; the OpenRefine pass finds the rest. Keyed by the
# normalised "city, region" or "city, country" the place item is built from.
KNOWN_PLACES: dict[str, str] = {
    "tacoma, washington": "Q199797", "seattle, washington": "Q5083", "portland, oregon": "Q6106",
    "venice, italy": "Q641", "new york, new york": "Q60", "new york city, new york": "Q60",
    "chicago, illinois": "Q1297", "los angeles, california": "Q65", "san francisco, california": "Q62",
    "london, united kingdom": "Q84", "london, england": "Q84", "paris, france": "Q90", "berlin, germany": "Q64",
    "prague, czech republic": "Q1085", "toronto, canada": "Q172", "vancouver, canada": "Q24639",
    "pittsburgh, pennsylvania": "Q1342", "philadelphia, pennsylvania": "Q1345", "boston, massachusetts": "Q100",
    "washington, district of columbia": "Q61", "toledo, ohio": "Q485172", "bristol, united kingdom": "Q23154",
    "bristol, england": "Q23154",
}

# People and organisations whose Wikidata item is certain (normalised label → id).
KNOWN_ENTITIES: dict[tuple[str, str], str] = {
    ("museum", "museum of glass"): "Q2894440",
    ("museum", "corning museum of glass"): "Q1134188",
    ("museum", "the corning museum of glass"): "Q1134188",
    ("school", "pilchuck glass school"): "Q16898382",
    ("school", "pilchuck"): "Q16898382",
    ("school", "hilltop artists"): "Q139770709",
    ("award", "rakow commission"): "Q135164758",
    ("person", "dale chihuly"): "Q312556",
}


# --------------------------------------------------------------------------- vocabularies
@dataclass(frozen=True)
class Term:
    kind: str                         # the key kind and the class slug it is an instance of
    slug: str
    label: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    wikidata: str | None = None
    aat: str | None = None
    exact: tuple[str, ...] = ()       # IRIs for exact match
    parent: str | None = None         # another term's key, for subclass of
    cls: str | None = None            # class slug when it differs from the kind (traits → their facet)
    described_at: str | None = None


COLOURS = [
    ("red", "Q3142"), ("orange", "Q39338"), ("yellow", "Q943"), ("green", "Q3133"), ("blue", "Q1088"),
    ("purple", "Q3257809"), ("white", "Q23444"), ("black", "Q23445"), ("brown", "Q47071"), ("pink", "Q429220"),
    ("grey", "Q42519"), ("violet", None), ("teal", None), ("amber", None), ("clear", None), ("gold", None),
    ("silver", None), ("turquoise", None), ("cream", None), ("multicolour", None),
]
COLOUR_ALIASES = {"grey": ("gray",), "clear": ("colourless", "colorless", "transparent"), "multicolour": ("multicolor",),
                  "purple": ("magenta",)}

FORMS = [
    ("pumpkin", "pumpkin", "a blown-glass pumpkin", None), ("ornament", "ornament", "a hanging ornament", None),
    ("paperweight", "paperweight", "a glass paperweight", "Q914149"), ("marble", "marble", "a glass marble", None),
    ("sculpture", "sculpture", "a sculptural piece", None), ("vessel", "vessel", "a container form", None),
    ("vase", "vase", "a vase", None), ("bowl", "bowl", "a bowl", None), ("bead", "bead", "a bead", None),
    ("pendant", "pendant", "a pendant", None), ("goblet", "goblet", "a stemmed drinking vessel", None),
    ("tumbler", "tumbler", "a flat-based drinking glass", None), ("cup", "cup", "a cup", None),
    ("plate", "plate", "a plate or platter", None), ("lamp", "lamp", "a lamp or light", None),
    ("pipe", "pipe", "a functional pipe", None), ("panel", "panel", "a flat glass panel", None),
    ("jewellery", "jewellery", "wearable glass", None), ("mushroom", "mushroom", "a glass mushroom", None),
    ("flower", "flower", "a glass flower", None), ("heart", "heart", "a glass heart", None),
    ("bird", "bird", "a glass bird", None), ("fish", "fish", "a glass fish", None),
    ("octopus", "octopus", "a glass octopus", None), ("skull", "skull", "a glass skull", None),
]
FORM_WORDS = {f[0]: f[0] for f in FORMS}
FORM_WORDS.update({"pumpkins": "pumpkin", "ornaments": "ornament", "gourd": "pumpkin", "gourds": "pumpkin",
                   "vases": "vase", "bowls": "bowl", "beads": "bead", "marbles": "marble", "goblets": "goblet",
                   "tumblers": "tumbler", "cups": "cup", "plates": "plate", "lamps": "lamp", "pendants": "pendant",
                   "earrings": "jewellery", "necklace": "jewellery", "ring": "jewellery", "bracelet": "jewellery",
                   "mushrooms": "mushroom", "flowers": "flower", "hearts": "heart", "birds": "bird",
                   "paperweights": "paperweight", "sculptures": "sculpture"})

EVENT_TYPES = [("created", "made", "the piece was made"), ("acquired", "acquired", "someone acquired the piece"),
               ("exhibited", "exhibited", "the piece was shown"), ("appraised", "appraised", "the piece was appraised"),
               ("sold", "sold", "the piece was sold"), ("loaned", "loaned", "the piece was loaned"),
               ("damaged", "damaged", "the piece was damaged"), ("restored", "restored", "the piece was restored"),
               ("verified", "checked", "the piece was checked against its Glass ID"),
               ("claimed", "added to a collection", "a collector added the piece to their collection"),
               ("entered", "entered", "the piece was entered in a collecting event"), ("other", "other event", "")]

STUDIO_TYPES = {"hot shop": "hot-shop", "hot": "hot-shop", "furnace": "hot-shop", "hotshop": "hot-shop",
                "glassblowing": "hot-shop", "flameworking": "flame-shop", "flame": "flame-shop", "torch": "flame-shop",
                "lampworking": "flame-shop", "kiln / fusing": "kiln-studio", "kiln": "kiln-studio", "fusing": "kiln-studio",
                "casting": "kiln-studio", "cold shop": "cold-shop", "cold": "cold-shop", "coldworking": "cold-shop",
                "teaching": "teaching-studio", "school": "teaching-studio", "access": "teaching-studio",
                "mixed": "mixed-studio", "studio": "studio", "other": "studio"}

# techniques that are exactly a Wikidata item, by technique id (central.techniques)
TECHNIQUE_WIKIDATA = {"offhand-blown": "Q12185286"}
TECHNIQUE_PARENTS = {"borosilicate-flameworking": "flameworking", "soft-glass-beadmaking": "flameworking",
                     "scientific-apparatus": "flameworking"}


def vocab_items() -> list[Term]:
    """Every vocabulary term, in write order."""
    from central import glass_traits, techniques
    out: list[Term] = [
        Term("unit", "mm", "millimetre", "a thousandth of a metre", ("millimeter", "mm"), "Q174789"),
        Term("source", "glass-database", "Glass Database",
             "glassdatabase.org, the community record of glass and the people who make it; statements here were "
             "taken from it", ("The Glass Database", "glassdatabase.org")),
        Term("occupation", "glass-artist", "glass artist", "an artist who works in glass", ("glassblower", "glass maker"),
             "Q2865798"),
    ]
    for role in techniques.ROLES:
        if role.startswith("Other") or role.startswith("Prefer") or role == "Artist or Maker":
            continue
        out.append(Term("occupation", slug(role), role.lower(), f"{role.lower()} in the glass field"))
    for code, label, desc in EVENT_TYPES:
        out.append(Term("event-type", code, label, desc))
    out.append(Term("technique", "flameworking", "flameworking", "shaping glass in a torch flame",
                    ("lampworking", "torchwork"), "Q2666550"))
    for tid, label, group, gbo in techniques.TECHNIQUES:
        t = techniques.resolve(label) or {}
        out.append(Term("technique", tid, label, f"{group.lower()} technique", (), TECHNIQUE_WIKIDATA.get(tid),
                        None, (t["gbo"],) if t.get("gbo") else (),
                        key_for("technique", TECHNIQUE_PARENTS[tid]) if tid in TECHNIQUE_PARENTS else None))
    for fid, flabel, fdesc in glass_traits.FACETS:
        out.append(Term("trait", "facet-" + fid, flabel.lower(), fdesc, cls="trait"))
    for c in glass_traits.CONCEPTS:
        out.append(Term("trait", c["id"], c["label"], c["definition"], tuple(c.get("alt") or ()), c.get("wikidata"),
                        c.get("aat"), (), key_for("trait", "facet-" + c["facet"]), None, c.get("cmog")))
    for name, qid in COLOURS:
        out.append(Term("colour", name, name, f"{name} glass", COLOUR_ALIASES.get(name, ()), qid))
    for fslug, flabel, fdesc, qid in FORMS:
        out.append(Term("form", fslug, flabel, fdesc, (), qid))
    seen = set()
    for label, qid in COUNTRIES.values():
        if label in seen:
            continue
        seen.add(label)
        out.append(Term("country", slug(label), label, "country", (), qid))
    return out


def class_items() -> list[Item]:
    items = []
    for c in CLASSES:
        it = Item(key_for("class", c.slug), "class", c.label, c.description, wikidata=c.wikidata)
        if c.parent:
            it.add(SUBCLASS_OF, item(key_for("class", c.parent)))
        if c.wikidata:
            it.add(WIKIDATA_PROP, ext(c.wikidata))
        items.append(it)
    return items


def term_item(t: Term) -> Item:
    it = Item(key_for(t.kind, t.slug), t.kind, t.label, t.description, list(t.aliases), key_for("class", t.cls or t.kind),
              wikidata=t.wikidata)
    if t.parent:
        it.add(SUBCLASS_OF, item(t.parent))
    if t.wikidata:
        it.add(WIKIDATA_PROP, ext(t.wikidata))
    if t.aat:
        it.add("AAT ID", ext(t.aat))
    for iri in t.exact:
        it.add("exact match", url(iri))
    if t.described_at:
        it.add("described at URL", url(t.described_at))
    return it


def fixed_items() -> list[Item]:
    """The classes and vocabularies: items that come from code, not from the tables."""
    return class_items() + [term_item(t) for t in vocab_items()]


def country_key(text: str) -> str | None:
    c = COUNTRIES.get(norm(text))
    return key_for("country", slug(c[0])) if c else None


def country_label(text: str) -> str | None:
    c = COUNTRIES.get(norm(text))
    return c[0] if c else None
