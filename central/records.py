"""
central.records
===============
One resolvable page per Glass Database ID.

Every item the Wikibase migration stages carries a key, ``gdb:<kind>:<id>`` (``gdb:artists:12``
for a Directory row, ``gdb:object:<content hash>`` for a piece, ``gdb:technique:murrine`` for a
vocabulary term). The wiki cites that key's page on the site as the reference on every
statement, the Glass Database ID property's formatter URL points at it, and the Resolve tool
links to it, so it has to land on an actual record rather than a search. This module turns a
key into that record: the public columns of the source row (through the publication gate),
what the wiki item says, where it is on the wiki and on Wikidata, and what else here is tied
to it (a maker's pieces and collecting-event entries, a studio's makers, an object's maker).

``resolve(conn, gid, base)`` is the whole of it; ``search(conn, q)`` finds a record by name.
Served by ``api/records.py`` at ``GET /api/records/{gid}`` and shown by ``deploy/record.html``
at ``/record/<gid>``.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from central import approvals
from central import links as site_links
from central.fieldnames import grid_summary, is_demographic, is_dob, is_excluded, labelled_columns

GID_RE = re.compile(r"^gdb:([A-Za-z0-9_\-]+):(.+)$")
NAME_COLS = ("artist_name", "name", "title", "display_name", "studio_name", "organization", "label")
SKIP_PROPS = {"Glass Database ID", "described at URL"}
KIND_WORDS = {"person": "person", "studio": "studio", "object": "glass piece", "place": "place",
              "country": "country", "museum": "museum", "school": "school", "funder": "funder",
              "award": "award", "show": "show", "event": "event", "season": "collecting event",
              "technique": "technique", "colour": "colour", "color": "colour", "occupation": "occupation",
              "trait": "trait", "class": "class", "unit": "unit", "event-type": "event type",
              "opportunity": "opportunity", "maker": "person"}
TABLE_KINDS = {"artists": "person", "studios": "studio", "objects": "object", "opportunities": "opportunity",
               "events": "event", "seasons": "season"}


# --- helpers -----------------------------------------------------------------
def parse_gid(gid: str) -> tuple[str, str] | None:
    m = GID_RE.match((gid or "").strip())
    return (m.group(1), m.group(2)) if m else None


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r is not None else None


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def public_tables(conn) -> set[str]:
    if not _has_table(conn, "_datasets"):
        return set()
    return {r[0] for r in conn.execute("SELECT tbl FROM _datasets WHERE visibility='public'").fetchall()}


def public_columns(conn, table: str) -> list[dict]:
    """The columns the public API shows for a table, in order: {column, label}."""
    if not _has_table(conn, "_columns"):
        return []
    return _rows(conn.execute("SELECT column, label FROM _columns WHERE tbl=? AND is_public=1 ORDER BY ordinal",
                              (table,)))


def _approved_row(conn, table: str, row_id: str) -> dict | None:
    if not _has_table(conn, table):
        return None
    try:
        return _one(conn.execute(f'SELECT * FROM "{table}" WHERE _row_id=? AND {approvals.approved_subquery()}',
                                 (row_id, table)))
    except Exception:  # noqa: BLE001  (a table without _row_id)
        return None


def _fields(conn, table: str, row: dict) -> list[dict]:
    """The public columns of a row that have a value, labelled as nouns; never a date of
    birth, a demographic, an identifier or a coordinate (the Directory keeps the same rule).
    A form's grid question (sixteen "Techniques [row]" columns) is one field."""
    cols = public_columns(conn, table)
    lc = labelled_columns((c["column"], c.get("label") or "") for c in cols)
    out = []
    for c in cols:
        col, label = c["column"], c.get("label") or ""
        if col in lc.grid_cols or is_excluded(col) or is_dob(col) or is_demographic(col, label):
            continue
        v = row.get(col)
        if v in (None, "") or str(v).strip() == "":
            continue
        out.append({"column": col, "label": lc.labels[col],
                    "value": v if isinstance(v, (int, float)) else str(v).strip()})
    for g, members in lc.groups.items():
        summary = grid_summary(row, members)
        if summary:
            out.append({"column": f"group:{g}", "label": g, "value": summary})
    return out


def _name_of(row: dict) -> str:
    for c in NAME_COLS:
        if row.get(c):
            return str(row[c]).strip()
    return ""


def _pick(row: dict | None, *cols) -> str:
    for c in cols:
        v = (row or {}).get(c)
        if v not in (None, "") and str(v).strip():
            return str(v).strip()
    return ""


def _describe(kind: str, row: dict | None) -> str:
    """A one-line description from the source row when the wiki has none yet."""
    if kind == "person":
        tech = _pick(row, "primary_technique", "technique", "techniques").split(",")[0].split("/")[0].strip().lower()
        where = _pick(row, "based_in", "location", "city", "studio")
        head = f"{tech} glass artist" if tech and "glass" not in tech else (tech or "glass artist")
        return f"{head}, {where}" if where else head
    if kind == "studio":
        where = ", ".join(x for x in (_pick(row, "city"), _pick(row, "region") or _pick(row, "country")) if x)
        what = _pick(row, "type").lower() or "glass studio"
        return f"{what} in {where}" if where else what
    if kind == "object":
        maker, year = _pick(row, "maker"), _pick(row, "year")
        return "glass piece" + (f" by {maker}" if maker else "") + (f", {year}" if year else "")
    if kind == "opportunity":
        return " · ".join(x for x in (_pick(row, "opp_type"), _pick(row, "organization")) if x) or "opportunity"
    return KIND_WORDS.get(kind, kind)


def _wiki_settings(conn) -> dict:
    try:
        from central.wikibase import store
        return store.get_settings(conn)
    except Exception:  # noqa: BLE001
        return {}


def wiki_item(conn, key: str) -> dict | None:
    if not _has_table(conn, "wb_items"):
        return None
    return _one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))


def _labels(conn, keys: list[str]) -> dict[str, str]:
    keys = sorted({k for k in keys if k})
    out: dict[str, str] = {}
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        q = ",".join("?" for _ in chunk)
        for k, label in conn.execute(f"SELECT key, label FROM wb_items WHERE key IN ({q})", chunk).fetchall():
            out[k] = label
    return out


def facts(conn, item_json: str | dict | None, limit: int = 60) -> list[dict]:
    """A readable list of what the staged item says: {prop, value, kind, key?, url?}."""
    try:
        it = json.loads(item_json) if isinstance(item_json, str) else (item_json or {})
    except ValueError:
        return []
    claims = it.get("claims") or []
    keys = [c.get("v", {}).get("v") for c in claims if (c.get("v") or {}).get("k") == "item"]
    units = [c.get("v", {}).get("unit") for c in claims if (c.get("v") or {}).get("unit")]
    labels = _labels(conn, [k for k in keys + units if isinstance(k, str)])
    out = []
    for c in claims:
        p, v = c.get("p") or "", c.get("v") or {}
        if p in SKIP_PROPS:
            continue
        kind, val = v.get("k") or "string", v.get("v")
        if val in (None, ""):
            continue
        f = {"prop": p, "kind": kind}
        if kind == "item":
            f["key"] = val
            f["value"] = labels.get(val, val)
        elif kind == "quantity":
            unit = v.get("unit")
            f["value"] = f"{val} {labels.get(unit, unit or '')}".strip()
        elif kind == "url":
            f["value"], f["url"] = val, val
        elif kind == "time":
            f["value"] = str(val).lstrip("+").split("T")[0]
        else:
            f["value"] = str(val)
        quals = []
        for qp, qv in c.get("q") or []:
            qval = (qv or {}).get("v")
            if (qv or {}).get("k") == "item":
                qval = labels.get(qval, qval)
            if qval not in (None, ""):
                quals.append(f"{qp}: {qval}")
        if quals:
            f["qualifiers"] = quals
        out.append(f)
        if len(out) >= limit:
            break
    return out


# --- what else is tied to a record ----------------------------------------------
def _objects_by_maker(conn, name: str, limit: int = 24) -> list[dict]:
    if not name or not _has_table(conn, "objects"):
        return []
    rows = _rows(conn.execute(
        f"SELECT _row_id, title, maker, year, techniques, content_hash, fingerprint_json, manifest_json, "
        f"has_credentials FROM objects WHERE lower(trim(maker))=? AND {approvals.approved_subquery()} "
        f"ORDER BY published_at DESC, _row_id LIMIT ?", (norm(name), "objects", int(limit))))
    return [_object_card(r) for r in rows]


def _object_card(r: dict) -> dict:
    fp = bool(r.get("fingerprint_json")) or '"fingerprint"' in (r.get("manifest_json") or "")
    return {"row_id": r["_row_id"], "gid": f"gdb:object:{r.get('content_hash') or r['_row_id']}",
            "title": r.get("title") or "Untitled", "maker": r.get("maker") or "", "year": r.get("year") or "",
            "techniques": r.get("techniques") or "", "fingerprinted": fp,
            "credentialed": bool(r.get("has_credentials")),
            "image_url": f"/api/objects/{r['_row_id']}/image",
            "url": site_links.piece_url(r["_row_id"])}


def _entries_by_maker(conn, name: str, limit: int = 30) -> list[dict]:
    if not name or not _has_table(conn, "season_entries"):
        return []
    return _rows(conn.execute(
        """SELECT e.id, e.title, e.studio, e.created_at, s.slug AS season_slug, s.title AS season_title, s.status,
                  (SELECT COUNT(*) FROM season_votes v WHERE v.entry_id=e.id) AS votes,
                  (SELECT COUNT(*) FROM season_claims c WHERE c.object_row_id=e.object_row_id
                      AND c.status IN ('verified','approved')) AS collected
           FROM season_entries e JOIN seasons s ON s.id=e.season_id
           WHERE e.status='approved' AND lower(trim(e.artist_name))=? AND s.status<>'draft'
           ORDER BY s.starts DESC, e.id DESC LIMIT ?""", (norm(name), int(limit))))


def _makers_of_studio(conn, name: str, limit: int = 40) -> list[dict]:
    if not name or not _has_table(conn, "season_entries"):
        return []
    return _rows(conn.execute(
        """SELECT artist_name AS name, COUNT(*) AS entries, COUNT(DISTINCT season_id) AS events
           FROM season_entries WHERE status='approved' AND lower(trim(studio))=? AND COALESCE(artist_name,'')<>''
           GROUP BY lower(trim(artist_name)) ORDER BY entries DESC, name LIMIT ?""", (norm(name), int(limit))))


def _lineage(conn, name: str) -> dict:
    out = {"mentors": [], "students": []}
    if not name or not _has_table(conn, "lineage_submissions"):
        return out
    try:
        for r in conn.execute(
                f"SELECT student_name, mentor_name FROM lineage_submissions WHERE {approvals.approved_subquery()} "
                f"AND (lower(trim(student_name))=? OR lower(trim(mentor_name))=?)",
                ("lineage_submissions", norm(name), norm(name))).fetchall():
            student, mentor = (r[0] or "").strip(), (r[1] or "").strip()
            if norm(student) == norm(name) and mentor and mentor not in out["mentors"]:
                out["mentors"].append(mentor)
            if norm(mentor) == norm(name) and student and student not in out["students"]:
                out["students"].append(student)
    except Exception:  # noqa: BLE001  (an older table without those columns)
        pass
    return out


def _person_key(conn, name: str) -> str | None:
    if not name or not _has_table(conn, "wb_items"):
        return None
    r = conn.execute("SELECT key FROM wb_items WHERE kind='person' AND lower(trim(label))=? "
                     "ORDER BY CASE WHEN key LIKE 'gdb:artists:%' THEN 0 ELSE 1 END, key LIMIT 1",
                     (norm(name),)).fetchone()
    return r[0] if r else None


def _season_link(conn, ident: str) -> str:
    if _has_table(conn, "seasons") and ident.isdigit():
        r = conn.execute("SELECT slug FROM seasons WHERE id=?", (int(ident),)).fetchone()
        if r:
            return f"/games.html?season={quote(r[0] or '')}"
    return "/games.html"


# --- the record -------------------------------------------------------------------
def resolve(conn, gid: str, base: str = "") -> dict | None:
    """Everything the site knows about one Glass Database ID, or None when there is no such
    public record. ``base`` is the public origin for absolute links (may be empty)."""
    parsed = parse_gid(gid)
    if not parsed:
        return None
    table, ident = parsed
    gid = f"gdb:{table}:{ident}"
    row, fields, source_table = None, [], ""
    if table == "object":
        if _has_table(conn, "objects"):
            row = _one(conn.execute(
                f"SELECT * FROM objects WHERE (content_hash=? OR _row_id=?) AND {approvals.approved_subquery()}",
                (ident, ident, "objects")))
            source_table = "objects"
        if row is not None:                                   # the key is the content hash; a row id resolves to it
            gid = f"gdb:object:{row.get('content_hash') or row['_row_id']}"
    elif table in public_tables(conn):
        row = _approved_row(conn, table, ident)
        source_table = table
    wiki = wiki_item(conn, gid)
    if row is None and wiki is None:
        return None
    if row is not None:
        fields = _fields(conn, source_table, row)
    kind = (wiki or {}).get("kind") or TABLE_KINDS.get(table) or table
    label = (wiki or {}).get("label") or (_name_of(row) if row else "") or gid
    description = (wiki or {}).get("description") or _describe(kind, row)
    settings = _wiki_settings(conn)
    wiki_url = (settings.get("url") or "").rstrip("/")
    out = {
        "gid": gid, "kind": kind, "kind_word": KIND_WORDS.get(kind, kind), "label": label,
        "description": description, "url": f"{base}/record/{gid}",
        "source": {"table": source_table, "row_id": (row or {}).get("_row_id") or "", "fields": fields},
        "wiki": None, "facts": [], "aliases": [], "enrich": None, "links": {}, "related": {},
    }
    if wiki:
        qid = wiki.get("qid") or ""
        wd = wiki.get("wikidata_qid") or ""
        out["wiki"] = {"key": wiki["key"], "status": wiki.get("status"), "qid": qid,
                       "item_url": f"{wiki_url}/wiki/Item:{qid}" if qid and wiki_url else "",
                       "wikidata_qid": wd, "wikidata_url": f"https://www.wikidata.org/wiki/{wd}" if wd else "",
                       "pushed_at": wiki.get("pushed_at") or "", "canonical": wiki.get("canonical") or ""}
        try:
            it = json.loads(wiki.get("item_json") or "{}")
        except ValueError:
            it = {}
        out["aliases"] = list(it.get("aliases") or [])
        out["facts"] = facts(conn, it)
        try:
            out["enrich"] = json.loads(wiki.get("enrich_json") or "") or None
        except (ValueError, TypeError):
            out["enrich"] = None
    # links onward: the Directory search, the map, the verify app, the maker card
    links = out["links"]
    if kind in ("person", "maker"):
        links["directory"] = site_links.directory_url(label, source_table)
        links["maker"] = f"/maker.html?m={quote(gid)}"
    elif kind == "studio":
        links["map"] = f"/studios.html?q={quote(label)}"
        links["directory"] = site_links.directory_url(label, source_table)
    elif kind == "object" and row is not None:
        links["piece"] = site_links.piece_url(row["_row_id"])
        links["verify"] = f"/fingerprint/verify.html?object={quote(str(row['_row_id']))}"
        links["image"] = f"/api/objects/{row['_row_id']}/image"
    elif kind == "season":
        links["event"] = _season_link(conn, ident)
    elif kind in ("show", "event"):
        links["events"] = "/events.html"
    elif kind == "opportunity":
        links["directory"] = "/explore/?view=opportunities"
    for f in fields:
        col = f["column"].lower()
        if "website" in col or col in ("url", "web", "link"):
            links.setdefault("website", str(f["value"]))
        if "instagram" in col:
            handle = str(f["value"]).strip().lstrip("@")
            links.setdefault("instagram", handle if handle.startswith("http") else f"https://www.instagram.com/{handle}/")
    for f in out["facts"]:
        if f["prop"] == "official website" and f.get("url"):
            links.setdefault("website", f["url"])
        if f["prop"] == "Instagram username":
            links.setdefault("instagram", f"https://www.instagram.com/{f['value'].lstrip('@')}/")
    # what else is here
    rel = out["related"]
    if kind in ("person", "maker"):
        rel["objects"] = _objects_by_maker(conn, label)
        rel["entries"] = _entries_by_maker(conn, label)
        rel["lineage"] = _lineage(conn, label)
    elif kind == "studio":
        rel["makers"] = _makers_of_studio(conn, label)
    elif kind == "object" and row is not None:
        card = _object_card(row)
        rel["object"] = card
        mk = row.get("maker") or ""
        key = _person_key(conn, mk)
        rel["maker"] = {"name": mk, "gid": key, "url": f"/record/{key}" if key else links.get("directory", "")}
        if _has_table(conn, "season_claims"):
            rel["collected"] = int(conn.execute(
                "SELECT COUNT(*) FROM season_claims WHERE object_row_id=? AND status IN ('verified','approved')",
                (row["_row_id"],)).fetchone()[0])
    return out


def search(conn, q: str, limit: int = 20) -> list[dict]:
    """Records whose name contains ``q``: wiki items first, then Directory rows that were
    never staged. {gid, kind, label, description}."""
    q = norm(q)
    if len(q) < 2:
        return []
    like = f"%{q}%"
    out: list[dict] = []
    seen: set[str] = set()
    if _has_table(conn, "wb_items"):
        for r in conn.execute(
                "SELECT key, kind, label, description FROM wb_items WHERE lower(label) LIKE ? "
                "AND COALESCE(canonical,'')='' ORDER BY CASE WHEN lower(label)=? THEN 0 ELSE 1 END, "
                "length(label), label LIMIT ?", (like, q, int(limit))).fetchall():
            seen.add(r[0])
            out.append({"gid": r[0], "kind": r[1], "label": r[2], "description": r[3] or ""})
    for table in sorted(public_tables(conn)):
        if len(out) >= limit or not _has_table(conn, table):
            break
        cols = [c["column"] for c in public_columns(conn, table) if c["column"] in NAME_COLS]
        if not cols:
            continue
        where = " OR ".join(f'lower("{c}") LIKE ?' for c in cols)
        try:
            rows = conn.execute(f'SELECT * FROM "{table}" WHERE ({where}) AND {approvals.approved_subquery()} LIMIT ?',
                                (*[like] * len(cols), table, int(limit))).fetchall()
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            d = dict(r)
            gid = f"gdb:{table}:{d.get('_row_id')}"
            if gid in seen:
                continue
            seen.add(gid)
            out.append({"gid": gid, "kind": TABLE_KINDS.get(table, table), "label": _name_of(d) or gid,
                        "description": KIND_WORDS.get(TABLE_KINDS.get(table, table), table)})
            if len(out) >= limit:
                break
    return out[:limit]
