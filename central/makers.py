"""
central.makers
==============
The maker's card: who the record knows, how well it knows them, and what they have done.

A maker shows up in three places that were never joined: a Directory row (``artists``), an
account (the Google sign-in behind Glowtbook, ``object_submissions.submitted_uid`` and
``season_entries.artist_uid``), and the wiki item the migration staged (``wb_items``, key
``gdb:artists:<row id>`` or ``gdb:maker:<slug>`` for a name that only ever appeared on a piece).
``find`` joins them by key, account and name; ``card`` turns the join into the trading card
the site shows: traits (pieces in the record, pieces with a Glass ID, collecting events,
votes, collections, honours), a **bio completeness** percentage with the fields still empty
and where to fill each one, a **knowledge score** out of 100 for how much the record knows,
and the **career** across every collecting event (entries, votes, best rank, awards, prizes,
badges, season by season), so a maker who enters year after year accumulates a record.

``leaderboards`` scores every maker at once for the stats page (cached for fifteen minutes).
Nothing here writes; account ids never leave through the public API (``api/makers.py``).
"""
from __future__ import annotations

import json
import math
import os
import re
import time

from central import approvals, records

# (field, label, weight, Directory-row columns, Glowtbook profile columns, where to fill it)
PROFILE_FIELDS: list[tuple[str, str, int, tuple, tuple, str]] = [
    ("name", "Name", 1, ("artist_name", "name", "display_name"), ("display_name",), "glowtbook"),
    ("studio", "Studio, or where you are based", 1,
     ("studio", "based_in", "location", "city", "base", "hometown"), ("nationality_base",), "directory"),
    ("technique", "Techniques", 1, ("primary_technique", "techniques", "technique", "medium"), ("techniques",), "glowtbook"),
    ("bio", "A few lines about you", 2,
     ("bio", "biography", "statement", "about", "description", "career_highlights"),
     ("career_highlights", "contributions"), "glowtbook"),
    ("website", "Website", 1, ("website", "url", "web", "link"), ("website",), "glowtbook"),
    ("instagram", "Instagram", 1, ("instagram", "social", "handle"), (), "directory"),
    ("since", "Born, or active since", 1, ("birth_year", "born", "active_since", "since", "year_started"), (), "directory"),
    ("lineage", "Who taught you", 1, ("studied_under", "mentor", "mentors", "education", "teacher"), (), "lineage"),
    ("wikidata", "A Wikidata item", 1, (), (), "admin"),
]
WHERE = {
    "glowtbook": {"label": "your Glowtbook profile", "url": "/glowtbook/"},
    "directory": {"label": "the Directory form", "url": "/explore/?view=submit"},
    "lineage": {"label": "the lineage form (who taught whom)", "url": "/explore/?view=submit"},
    "admin": {"label": "the Wikidata lookup, run by an administrator", "url": "/wikibase.html"},
}
LEVELS = ((80, "on the record"), (60, "well known"), (40, "known"), (20, "a sketch"), (0, "barely known"))
TTL = 900                                  # seconds the leaderboards are kept
_CACHE: dict = {"at": 0.0, "data": None}


# --- helpers -----------------------------------------------------------------
def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r is not None else None


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _first(d: dict | None, cols) -> str:
    for c in cols:
        v = (d or {}).get(c)
        if v not in (None, "") and str(v).strip():
            return str(v).strip()
    return ""


def _glowtbook_profile(uid: str) -> dict | None:
    if not uid:
        return None
    try:
        from glowtbook import store as gstore
        path = os.environ.get("GLOWTBOOK_DB_PATH") or None
        s = gstore.open_store(path)
        try:
            r = s.execute("SELECT * FROM profile WHERE user_id=?", (uid,)).fetchone()
            return dict(r) if r is not None else None
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        return None


def _my_hashes(conn, uid: str) -> set[str]:
    try:
        from central import seasons
        return seasons._my_hashes(conn, uid)  # noqa: SLF001  (the one definition of "what I published")
    except Exception:  # noqa: BLE001
        return set()


def _artists_table(conn) -> str | None:
    """The Directory table that holds people, if the registry has one."""
    for t in ("artists", "people", "makers"):
        if t in records.public_tables(conn) and _has_table(conn, t):
            return t
    return None


def _row_by_name(conn, name: str) -> tuple[str, dict] | None:
    t = _artists_table(conn)
    if not t or not name:
        return None
    cols = [c["column"] for c in records.public_columns(conn, t) if c["column"] in records.NAME_COLS]
    for c in cols:
        try:
            r = _one(conn.execute(f'SELECT * FROM "{t}" WHERE lower(trim("{c}"))=? AND {approvals.approved_subquery()} '
                                  f'LIMIT 1', (norm(name), t)))
        except Exception:  # noqa: BLE001
            r = None
        if r:
            return t, r
    return None


def _evidence(conn, names: list[str]) -> bool:
    """Has the record seen any of these names on a published piece or an approved entry?"""
    for n in names:
        key = norm(n)
        if not key:
            continue
        if _has_table(conn, "objects") and conn.execute(
                f"SELECT 1 FROM objects WHERE lower(trim(maker))=? AND {approvals.approved_subquery()} LIMIT 1",
                (key, "objects")).fetchone():
            return True
        if _has_table(conn, "season_entries") and conn.execute(
                "SELECT 1 FROM season_entries WHERE lower(trim(artist_name))=? AND status='approved' LIMIT 1",
                (key,)).fetchone():
            return True
    return False


def _wiki_by_name(conn, name: str) -> dict | None:
    if not name or not _has_table(conn, "wb_items"):
        return None
    return _one(conn.execute(
        "SELECT * FROM wb_items WHERE kind='person' AND lower(trim(label))=? AND COALESCE(canonical,'')='' "
        "ORDER BY CASE WHEN key LIKE 'gdb:artists:%' THEN 0 ELSE 1 END, key LIMIT 1", (norm(name),)))


# --- finding a maker -------------------------------------------------------------
def find(conn, *, gid: str = "", uid: str = "", name: str = "", email: str = "") -> dict | None:
    """Join the record's three views of one maker. By key (``gdb:artists:12``, ``gdb:maker:…``),
    by account (``uid``, with the sign-in name) or by name. None when nothing is known."""
    row, table, wiki, profile = None, "", None, None
    names: list[str] = []
    if gid:
        parsed = records.parse_gid(gid)
        if not parsed:
            return None
        table, ident = parsed
        wiki = records.wiki_item(conn, gid)
        if table in records.public_tables(conn):
            row = records._approved_row(conn, table, ident)  # noqa: SLF001
        if row is None and wiki is None:
            return None
        if wiki and wiki.get("label"):
            names.append(wiki["label"])
        if row:
            n = records._name_of(row)  # noqa: SLF001
            if n:
                names.append(n)
    if uid:
        profile = _glowtbook_profile(uid)
        if profile and profile.get("display_name"):
            names.append(profile["display_name"])
        if name:
            names.append(name)
        if _has_table(conn, "objects"):
            hs = sorted(_my_hashes(conn, uid))
            for i in range(0, len(hs), 400):
                chunk = hs[i:i + 400]
                q = ",".join("?" for _ in chunk)
                for (mk,) in conn.execute(f"SELECT DISTINCT maker FROM objects WHERE content_hash IN ({q}) "
                                          f"AND COALESCE(maker,'')<>''", chunk).fetchall():
                    names.append(mk)
        if _has_table(conn, "season_entries"):
            for (an,) in conn.execute("SELECT DISTINCT artist_name FROM season_entries WHERE artist_uid=? "
                                      "AND COALESCE(artist_name,'')<>''", (uid,)).fetchall():
                names.append(an)
    elif name and not gid:
        names.append(name)
    seen: set[str] = set()
    names = [n for n in names if n and not (norm(n) in seen or seen.add(norm(n)))]
    if not names and not uid:
        return None
    if row is None:
        for n in names:
            hit = _row_by_name(conn, n)
            if hit:
                table, row = hit
                break
    if wiki is None:
        for n in names:
            wiki = _wiki_by_name(conn, n)
            if wiki:
                break
    if row is not None and wiki is None and table:
        wiki = records.wiki_item(conn, f"gdb:{table}:{row.get('_row_id')}")
    if not uid and row is None and wiki is None and not _evidence(conn, names):
        return None                                           # a name the record has never seen
    display = (wiki or {}).get("label") or (records._name_of(row) if row else "") or (names[0] if names else "")  # noqa: SLF001
    if not display and not uid:
        return None
    key = (wiki or {}).get("key") or (f"gdb:{table}:{row.get('_row_id')}" if row is not None and table else "")
    return {"name": display or (email.split("@")[0] if email else "A maker"), "names": names or [display],
            "gid": key, "uid": uid or "", "email": email or "", "row": row, "table": table, "wiki": wiki,
            "profile": profile}


# --- what the record holds about them ------------------------------------------------
def pieces(conn, maker: dict, limit: int = 60) -> dict:
    """Their pieces in the public record: by account first, then by the names they go by."""
    out: dict[str, dict] = {}
    if not _has_table(conn, "objects"):
        return {"items": [], "pieces": 0, "registered": 0, "credentialed": 0}
    sel = ("SELECT _row_id, title, maker, year, techniques, content_hash, fingerprint_json, manifest_json, "
           "has_credentials, published_at FROM objects WHERE ")
    gate = f" AND {approvals.approved_subquery()}"
    if maker.get("uid"):
        hs = sorted(_my_hashes(conn, maker["uid"]))
        for i in range(0, len(hs), 400):
            chunk = hs[i:i + 400]
            q = ",".join("?" for _ in chunk)
            for r in _rows(conn.execute(sel + f"content_hash IN ({q})" + gate, (*chunk, "objects"))):
                out[r["_row_id"]] = r
    for n in maker.get("names") or []:
        for r in _rows(conn.execute(sel + "lower(trim(maker))=?" + gate, (norm(n), "objects"))):
            out.setdefault(r["_row_id"], r)
    rows = sorted(out.values(), key=lambda r: (r.get("published_at") or "", r["_row_id"]), reverse=True)
    cards = [records._object_card(r) for r in rows]  # noqa: SLF001
    return {"items": cards[:limit], "pieces": len(cards),
            "registered": sum(1 for c in cards if c["fingerprinted"]),
            "credentialed": sum(1 for c in cards if c["credentialed"])}


def career(conn, maker: dict) -> dict:
    """Every collecting event they entered, newest first, with what came of each: entries,
    votes, collections, best People's choice rank, awards led at the close, prizes, badges."""
    from central import seasons
    empty = {"seasons": [], "totals": {"events": 0, "entries": 0, "votes": 0, "collected": 0, "awards": 0,
                                       "prizes": 0, "badges": 0, "crowd_favourites": 0, "best_rank": None}}
    if not _has_table(conn, "season_entries"):
        return empty
    seasons.ensure(conn)
    where, params = [], []
    if maker.get("uid"):
        where.append("e.artist_uid=?"); params.append(maker["uid"])
    for n in maker.get("names") or []:
        where.append("lower(trim(e.artist_name))=?"); params.append(norm(n))
    if not where:
        return empty
    mine = _rows(conn.execute(
        f"""SELECT e.id, e.season_id, e.title, e.studio, e.artist_uid, e.created_at, s.slug, s.title AS season_title,
                   s.status, s.starts,
                   (SELECT COUNT(*) FROM season_votes v WHERE v.entry_id=e.id) AS votes,
                   (SELECT COUNT(*) FROM season_claims c WHERE c.object_row_id=e.object_row_id
                        AND c.status IN ('verified','approved')) AS collected
            FROM season_entries e JOIN seasons s ON s.id=e.season_id
            WHERE e.status='approved' AND s.status<>'draft' AND ({' OR '.join(where)})
            ORDER BY s.starts DESC, e.id""", params))
    by_season: dict[int, dict] = {}
    for e in mine:
        s = by_season.setdefault(int(e["season_id"]), {
            "season_id": int(e["season_id"]), "slug": e["slug"], "title": e["season_title"], "status": e["status"],
            "starts": e["starts"] or "", "entries": 0, "votes": 0, "collected": 0, "best_rank": None,
            "awards": [], "prizes": [], "pieces": [], "studio": e.get("studio") or ""})
        s["entries"] += 1; s["votes"] += int(e["votes"] or 0); s["collected"] += int(e["collected"] or 0)
        s["pieces"].append({"entry_id": e["id"], "title": e["title"], "votes": int(e["votes"] or 0),
                            "collected": int(e["collected"] or 0)})
    my_ids = {int(e["id"]) for e in mine}
    for sid, s in by_season.items():
        lb = seasons.leaderboard(conn, sid)
        ranks = [int(e["rank"]) for e in lb["peoples_choice"] if int(e["id"]) in my_ids and int(e.get("vote_count") or 0) > 0]
        s["best_rank"] = min(ranks) if ranks else None
        if s["status"] == "closed":
            for c in seasons.awards(conn, sid):
                if c.get("leader") and int(c["leader"]["entry_id"]) in my_ids:
                    s["awards"].append(c["title"])
    uid = maker.get("uid") or ""
    won = seasons.prizes_won(conn, uid=uid, name=(maker.get("names") or [""])[0])
    for p in won:
        s = by_season.get(int(p["season_id"]))
        if s is not None:
            s["prizes"].append(p["title"])
    badges = seasons.badges_for(conn, uid) if uid else []
    out_seasons = sorted(by_season.values(), key=lambda s: (s["starts"], s["season_id"]), reverse=True)
    totals = {"events": len(out_seasons), "entries": sum(s["entries"] for s in out_seasons),
              "votes": sum(s["votes"] for s in out_seasons), "collected": sum(s["collected"] for s in out_seasons),
              "awards": sum(len(s["awards"]) for s in out_seasons), "prizes": len(won),
              "badges": len(badges), "crowd_favourites": sum(1 for b in badges if b["badge"] == "crowd-favourite"),
              "best_rank": min((s["best_rank"] for s in out_seasons if s["best_rank"]), default=None)}
    return {"seasons": out_seasons, "totals": totals,
            "badges": [{"badge": b["badge"], "label": b.get("label"), "season_title": b.get("season_title")} for b in badges]}


# --- the scores ----------------------------------------------------------------------
def completeness(maker: dict, lineage: dict | None = None, studio_hint: str = "", techniques_hint: str = "") -> dict:
    """How much of the bio is filled in: {percent, filled[], missing[{field, label, where}]}."""
    row, profile, wiki = maker.get("row") or {}, maker.get("profile") or {}, maker.get("wiki") or {}
    filled, missing, got, total = [], [], 0, 0
    for field, label, weight, row_cols, prof_cols, where in PROFILE_FIELDS:
        total += weight
        have = bool(_first(row, row_cols) or _first(profile, prof_cols))
        if field == "name":
            have = have or bool(maker.get("name"))
        elif field == "studio":
            have = have or bool(studio_hint)
        elif field == "technique":
            have = have or bool(techniques_hint)
        elif field == "lineage":
            have = have or bool((lineage or {}).get("mentors"))
        elif field == "wikidata":
            have = have or bool(wiki.get("wikidata_qid"))
        if have:
            got += weight; filled.append(label)
        else:
            missing.append({"field": field, "label": label, "weight": weight, "where": WHERE[where]})
    return {"percent": int(round(100 * got / total)) if total else 0, "filled": filled, "missing": missing}


def score_parts(*, completeness_pct: int, pieces_n: int, registered: int, events: int, votes: int, collected: int,
                awards: int, prizes: int, badges: int, external: int) -> dict:
    """The knowledge score, out of 100, from its parts; the same formula everywhere."""
    profile = round(0.40 * completeness_pct)
    pieces_pts = min(20, round(20 * math.log1p(pieces_n) / math.log(31))) if pieces_n > 0 else 0
    registered_pts = round(10 * registered / pieces_n) if pieces_n else 0
    events_pts = min(10, 4 * events)
    recognition = min(10, round(votes / 5 + collected * 2 + awards * 3 + prizes * 4 + badges))
    external_pts = min(10, external)
    score = min(100, profile + pieces_pts + registered_pts + events_pts + recognition + external_pts)
    level = next(word for floor, word in LEVELS if score >= floor)
    return {"score": score, "level": level,
            "parts": {"profile": profile, "pieces": pieces_pts, "registered": registered_pts, "events": events_pts,
                      "recognition": recognition, "external": external_pts},
            "max": {"profile": 40, "pieces": 20, "registered": 10, "events": 10, "recognition": 10, "external": 10}}


def _external(links: dict, wiki: dict | None) -> int:
    pts = 0
    if (wiki or {}).get("wikidata_qid"):
        pts += 4
    if (wiki or {}).get("qid"):
        pts += 2
    if links.get("website"):
        pts += 2
    if links.get("instagram"):
        pts += 2
    return pts


def _next_steps(comp: dict, traits: dict, links: dict) -> list[dict]:
    steps = []
    for m in sorted(comp["missing"], key=lambda m: -m["weight"]):
        steps.append({"text": f"Fill in: {m['label'].lower()}", "where": m["where"]})
    if traits["pieces"] == 0:
        steps.insert(0, {"text": "Publish a piece from Glowtbook: it is the first thing the record counts",
                         "where": {"label": "Glowtbook", "url": "/glowtbook/"}})
    elif traits["registered"] < traits["pieces"]:
        steps.insert(0, {"text": f"Register the {traits['pieces'] - traits['registered']} piece"
                                 f"{'s' if traits['pieces'] - traits['registered'] != 1 else ''} without a Glass ID",
                         "where": {"label": "Register Your Piece", "url": "/fingerprint/enroll.html"}})
    if traits["events"] == 0:
        steps.append({"text": "Enter a collecting event; every event adds to the career record",
                      "where": {"label": "Collecting Events", "url": "/games.html"}})
    return steps[:4]


# --- the card -----------------------------------------------------------------------------
def card(conn, maker: dict, *, private: bool = False) -> dict:
    """The maker's card. ``private`` keeps the account id and the where-to-fill list, for the
    maker's own view; the public view carries neither id nor email."""
    row, wiki, profile = maker.get("row") or {}, maker.get("wiki") or {}, maker.get("profile") or {}
    p = pieces(conn, maker)
    c = career(conn, maker)
    lineage = records._lineage(conn, maker["name"])  # noqa: SLF001
    studio = _first(row, ("studio",)) or (c["seasons"][0]["studio"] if c["seasons"] else "") or ""
    based = _first(row, ("based_in", "location", "city", "base", "hometown")) or _first(profile, ("nationality_base",))
    techniques = _first(row, ("primary_technique", "techniques", "technique", "medium")) or _first(profile, ("techniques",))
    if not techniques:
        seen = []
        for it in p["items"]:
            for t in re.split(r"[;,/]", it.get("techniques") or ""):
                t = t.strip()
                if t and t.lower() not in (x.lower() for x in seen):
                    seen.append(t)
        techniques = ", ".join(seen[:4])
    since = _first(row, ("birth_year", "born", "active_since", "since", "year_started"))
    bio = _first(row, ("bio", "biography", "statement", "about", "description")) or _first(
        profile, ("career_highlights", "contributions"))
    links: dict = {}
    web = _first(row, ("website", "url", "web", "link")) or _first(profile, ("website",))
    if web:
        links["website"] = web if web.startswith("http") else "https://" + web
    insta = _first(row, ("instagram", "social", "handle"))
    if insta and ("instagram" in insta.lower() or not insta.startswith("http")):
        h = insta.strip().lstrip("@")
        links["instagram"] = h if h.startswith("http") else f"https://www.instagram.com/{h}/"
    try:
        enrich = json.loads(wiki.get("enrich_json") or "") or {}
    except (ValueError, TypeError):
        enrich = {}
    for field, key in (("website", "website"), ("instagram", "instagram")):
        v = (enrich.get(field) or {}).get("value") if isinstance(enrich.get(field), dict) else enrich.get(field)
        if v and not links.get(key):
            links[key] = v if str(v).startswith("http") else (f"https://www.instagram.com/{str(v).lstrip('@')}/"
                                                               if key == "instagram" else f"https://{v}")
    if wiki.get("wikidata_qid"):
        links["wikidata"] = f"https://www.wikidata.org/wiki/{wiki['wikidata_qid']}"
    if wiki.get("qid"):
        wurl = (records._wiki_settings(conn).get("url") or "").rstrip("/")  # noqa: SLF001
        if wurl:
            links["wikibase"] = f"{wurl}/wiki/Item:{wiki['qid']}"
    if maker.get("gid"):
        links["record"] = f"/record/{maker['gid']}"
    links["directory"] = f"/explore/?view=datasets&q={maker['name']}"
    comp = completeness(maker, lineage, studio_hint=studio or based, techniques_hint=techniques)
    t = c["totals"]
    traits = {"pieces": p["pieces"], "registered": p["registered"], "credentialed": p["credentialed"],
              "events": t["events"], "entries": t["entries"], "votes": t["votes"], "collected": t["collected"],
              "awards": t["awards"], "prizes": t["prizes"], "badges": t["badges"],
              "crowd_favourites": t["crowd_favourites"], "best_rank": t["best_rank"],
              "mentors": len(lineage.get("mentors") or []), "students": len(lineage.get("students") or [])}
    know = score_parts(completeness_pct=comp["percent"], pieces_n=p["pieces"], registered=p["registered"],
                       events=t["events"], votes=t["votes"], collected=t["collected"], awards=t["awards"],
                       prizes=t["prizes"], badges=t["badges"], external=_external(links, wiki))
    out = {"name": maker["name"], "gid": maker.get("gid") or "", "studio": studio, "based": based,
           "techniques": techniques, "since": since, "bio": bio[:600], "links": links,
           "url": f"/maker.html?m={maker['gid']}" if maker.get("gid") else f"/maker.html?name={maker['name']}",
           "traits": traits, "completeness": comp, "knowledge": know, "career": c, "lineage": lineage,
           "pieces": p["items"][:12], "has_account": bool(maker.get("uid")),
           "wiki": {"qid": wiki.get("qid") or "", "wikidata_qid": wiki.get("wikidata_qid") or ""} if wiki else None}
    if private:
        out["next_steps"] = _next_steps(comp, traits, links)
    else:
        out["completeness"] = {"percent": comp["percent"], "filled": comp["filled"],
                               "missing": [m["label"] for m in comp["missing"]]}
    return out


# --- everyone at once, for the stats page ------------------------------------------------
def leaderboards(conn, limit: int = 10, force: bool = False) -> dict:
    """Every named maker scored with the same formula, from a handful of grouped queries:
    the most knowledgeable, the most complete bios, the most pieces, and the counts behind
    the stats page. Cached for TTL seconds."""
    now = time.time()
    if force or _CACHE["data"] is None or now - _CACHE["at"] >= TTL:
        _CACHE.update({"at": now, "data": _score_everyone(conn, now)})
    d = _CACHE["data"]
    scored = d["scored"]
    return {
        "counts": d["counts"],
        "knowledge": sorted(scored, key=lambda s: (-s["knowledge"], -s["pieces"], s["name"]))[:limit],
        "completeness": sorted(scored, key=lambda s: (-s["completeness"], -s["knowledge"], s["name"]))[:limit],
        "pieces": sorted((s for s in scored if s["pieces"]), key=lambda s: (-s["pieces"], -s["registered"], s["name"]))[:limit],
        "events": sorted((s for s in scored if s["events"]), key=lambda s: (-s["events"], -s["votes"], s["name"]))[:limit],
        "computed_at": d["computed_at"],
    }


def _score_everyone(conn, now: float) -> dict:
    makers: dict[str, dict] = {}

    def slot(name: str) -> dict:
        return makers.setdefault(norm(name), {"name": name.strip(), "gid": "", "studio": "", "based": "", "pieces": 0,
                                              "registered": 0, "events": 0, "entries": 0, "votes": 0,
                                              "collected": 0, "awards": 0, "prizes": 0, "badges": 0,
                                              "filled": 0, "total": 0, "wikidata": "", "qid": "", "website": "",
                                              "instagram": "", "mentors": 0})
    t = _artists_table(conn)
    total_weight = sum(w for _, _, w, _, _, _ in PROFILE_FIELDS)
    if t:
        try:
            for r in _rows(conn.execute(f'SELECT * FROM "{t}" WHERE {approvals.approved_subquery()}', (t,))):
                n = records._name_of(r)  # noqa: SLF001
                if not n:
                    continue
                m = slot(n)
                m["gid"] = m["gid"] or f"gdb:{t}:{r.get('_row_id')}"
                m["studio"] = m["studio"] or _first(r, ("studio",))
                m["based"] = m["based"] or _first(r, ("based_in", "location", "city"))
                m["website"] = m["website"] or _first(r, ("website", "url", "web"))
                m["instagram"] = m["instagram"] or _first(r, ("instagram",))
                got = 0
                for field, _label, weight, cols, _p, _w in PROFILE_FIELDS:
                    if field == "name" or _first(r, cols):
                        got += weight
                m["filled"] = max(m["filled"], got)
        except Exception:  # noqa: BLE001
            pass
    if _has_table(conn, "wb_items"):
        for r in conn.execute("SELECT key, label, qid, wikidata_qid FROM wb_items WHERE kind='person' "
                              "AND COALESCE(canonical,'')=''").fetchall():
            if not r[1]:
                continue
            m = slot(r[1])
            m["gid"] = m["gid"] or r[0]
            m["qid"] = m["qid"] or (r[2] or "")
            m["wikidata"] = m["wikidata"] or (r[3] or "")
    if _has_table(conn, "objects"):
        for r in conn.execute(
                f"SELECT maker, COUNT(*), SUM(CASE WHEN fingerprint_json IS NOT NULL AND fingerprint_json<>'' "
                f"OR manifest_json LIKE '%\"fingerprint\"%' THEN 1 ELSE 0 END) FROM objects "
                f"WHERE COALESCE(maker,'')<>'' AND {approvals.approved_subquery()} GROUP BY lower(trim(maker))",
                ("objects",)).fetchall():
            m = slot(r[0])
            m["pieces"] += int(r[1] or 0); m["registered"] += int(r[2] or 0)
    if _has_table(conn, "season_entries"):
        for r in conn.execute(
                """SELECT e.artist_name, COUNT(*), COUNT(DISTINCT e.season_id),
                          (SELECT COUNT(*) FROM season_votes v JOIN season_entries x ON x.id=v.entry_id
                              WHERE lower(trim(x.artist_name))=lower(trim(e.artist_name)) AND x.status='approved'),
                          (SELECT COUNT(*) FROM season_claims c JOIN season_entries x ON x.object_row_id=c.object_row_id
                              WHERE lower(trim(x.artist_name))=lower(trim(e.artist_name)) AND x.status='approved'
                              AND c.status IN ('verified','approved'))
                   FROM season_entries e JOIN seasons s ON s.id=e.season_id
                   WHERE e.status='approved' AND s.status<>'draft' AND COALESCE(e.artist_name,'')<>''
                   GROUP BY lower(trim(e.artist_name))""").fetchall():
            m = slot(r[0])
            m["entries"] += int(r[1] or 0); m["events"] = max(m["events"], int(r[2] or 0))
            m["votes"] += int(r[3] or 0); m["collected"] += int(r[4] or 0)
        for r in conn.execute("SELECT artist_name, studio FROM season_entries WHERE status='approved' "
                              "AND COALESCE(studio,'')<>'' AND COALESCE(artist_name,'')<>'' ORDER BY id").fetchall():
            slot(r[0])["studio"] = r[1].strip()                  # the latest shop they entered from
        if _has_table(conn, "season_badges"):
            for r in conn.execute(
                    "SELECT e.artist_name, COUNT(DISTINCT b.id) FROM season_badges b "
                    "JOIN season_entries e ON e.artist_uid=b.uid WHERE COALESCE(e.artist_name,'')<>'' "
                    "GROUP BY lower(trim(e.artist_name))").fetchall():
                slot(r[0])["badges"] = int(r[1] or 0)
    if _has_table(conn, "lineage_submissions"):
        try:
            for r in conn.execute(
                    f"SELECT student_name, COUNT(*) FROM lineage_submissions WHERE {approvals.approved_subquery()} "
                    f"AND COALESCE(student_name,'')<>'' GROUP BY lower(trim(student_name))",
                    ("lineage_submissions",)).fetchall():
                slot(r[0])["mentors"] = int(r[1] or 0)
        except Exception:  # noqa: BLE001
            pass
    scored = []
    for m in makers.values():
        got = m["filled"] + (1 if m["wikidata"] else 0) + (1 if m["mentors"] else 0) + (1 if (m["studio"] or m["based"]) and not m["filled"] else 0)
        pct = int(round(100 * min(got, total_weight) / total_weight))
        ext = (4 if m["wikidata"] else 0) + (2 if m["qid"] else 0) + (2 if m["website"] else 0) + (2 if m["instagram"] else 0)
        k = score_parts(completeness_pct=pct, pieces_n=m["pieces"], registered=m["registered"], events=m["events"],
                        votes=m["votes"], collected=m["collected"], awards=0, prizes=0, badges=m["badges"], external=ext)
        scored.append({"name": m["name"], "gid": m["gid"], "studio": m["studio"] or m["based"], "pieces": m["pieces"],
                       "registered": m["registered"], "events": m["events"], "votes": m["votes"],
                       "collected": m["collected"], "completeness": pct, "knowledge": k["score"],
                       "level": k["level"], "wikidata": bool(m["wikidata"]), "on_wiki": bool(m["qid"]),
                       "url": f"/maker.html?m={m['gid']}" if m["gid"] else f"/maker.html?name={m['name']}"})
    n = len(scored)
    return {
        "scored": scored,
        "counts": {"makers": n,
                   "with_pieces": sum(1 for s in scored if s["pieces"]),
                   "with_registered_pieces": sum(1 for s in scored if s["registered"]),
                   "in_events": sum(1 for s in scored if s["events"]),
                   "on_wikidata": sum(1 for s in scored if s["wikidata"]),
                   "on_wiki": sum(1 for s in scored if s["on_wiki"]),
                   "bios_half_complete": sum(1 for s in scored if s["completeness"] >= 50),
                   "average_knowledge": round(sum(s["knowledge"] for s in scored) / n) if n else 0,
                   "average_completeness": round(sum(s["completeness"] for s in scored) / n) if n else 0},
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }


def forget() -> None:
    """Drop the cached leaderboards (tests, and after a big import)."""
    _CACHE.update({"at": 0.0, "data": None})
