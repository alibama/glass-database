"""
central.seasons
===============
Seasonal games — a friendly call for artists, a shelf for collectors.

A **season** is a themed, time-boxed call ("Pumpkin Patch 2026": blown-glass
pumpkins from every shop). Makers **enter** a piece they've already published
through Glowtbook (so it is fingerprinted, credentialed and approved). Collectors
buy pieces from the shops, **scan** them with the verify app, and **claim** them —
a claim with a fingerprint match of 70+ is *verified* on the spot, anything less
waits for the **maker** (Glowtbook → Objects → Seasons) or an admin to confirm
it. Signed-in visitors **vote** for their favourites (a handful of votes per
season), and the whole thing rolls up into leaderboards (people's choice, most
collected, studios, collectors) and a small set of **badges**. Nothing here
needs cash: recognition, a homepage feature and the Crowd Favourite badge are
the prize.

Why it serves the mission: every claim is an authenticated piece in a real
collection (provenance that holds), every entry is a sale opportunity for an
independent maker, and every season is a voluntary census of who made what.

Discord nudges (``central.notify``, best effort — never raise) go out when a
claim lands on someone's piece and when a season opens or starts voting.

**Awards, rarity and rules** (a first, small draft). Every season has the implicit
People's choice board (``season_votes``) plus a handful of **award categories**
(``season_categories``): *vote* kinds ("Best stem") take one pick per voter per
category (``season_award_votes``; pick again to swap, pick the same to remove,
never your own piece); *measured* kinds ("Biggest pumpkin") rank entries by the
height the fingerprint mat measured onto the object. Entries carry **rarity**:
common (1 pt) → uncommon (2, fingerprinted) → rare (5, fingerprinted and an
edition of ≤ 3) → legendary (20): a maker's one edition-of-one per season with a
legend written, which only shows as legendary once a collector has claimed it.
Collectors score the points of what they hold (the ``points`` board).

**Prizes** (``season_prizes``) are what is on the table, written by an admin and edited
any time: a title, what it is awarded for (``PRIZE_FOR``: the most-voted piece, the
studio with the most makers entering, the maker with the most registered pieces, the
collector with the most points, an award category, or a judges' pick named by hand), a
line of detail, a sponsor. Standings come straight off the boards (``prize_standings``);
closing the season writes the leader into ``winner_text`` and an admin can overwrite it.
The boards also count **makers** (who entered the most registered pieces) and group
**studios** by name regardless of case, with the number of distinct makers per shop.

Tables (names are a contract with the API, Glowtbook and the verify app):
  seasons, season_entries, season_votes, season_claims, season_badges,
  season_categories, season_award_votes, season_prizes
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from central import approvals

STATUSES = ("draft", "open", "voting", "closed")
ENTRY_STATUSES = ("pending", "approved", "rejected")


def entry_review() -> bool:
    """Do new entries wait for an admin? Off by default: a public piece was already reviewed
    once, when it was published, so entering it in a season goes live at once. Set
    GLASSDB_SEASON_ENTRY_REVIEW=1 to hold entries in the queue instead (Admin → Seasons
    approves them; so does the Discord link)."""
    return os.environ.get("GLASSDB_SEASON_ENTRY_REVIEW", "0").lower() in ("1", "true", "yes")
CLAIM_STATUSES = ("verified", "pending", "approved", "rejected")
CLAIM_OK = ("verified", "approved")          # the claims that count on the boards
VERIFIED_CONFIDENCE = 70.0                   # verify.html: >= 70 with >= 3 views = "Match likely"
IMAGE_URL = "/api/objects/{row_id}/image"
GLOBAL = 0                                   # season_id for badges that aren't per-season

# awards + rarity
CATEGORY_KINDS = ("vote", "measured")
MEASURES = ("height",)                       # mm, from the fingerprint mat (matdetect)
AWARD_TOP = 5                                # standings shown per vote category
RARITY_POINTS = {"common": 1, "uncommon": 2, "rare": 5, "legendary": 20}
RARE_EDITION = 3                             # fingerprinted + edition_size <= this = rare
LEGEND_MIN = 40                              # characters a legend needs
MAX_ENTRIES = 5                              # per maker per season

# prizes: what a prize is awarded for → where its standings come from
PRIZE_TOP = 3                                # standings shown per prize
PRIZE_FOR = {
    "peoples_choice": {"label": "People's choice", "who": "piece", "unit": "votes",
                       "blurb": "the most-voted piece"},
    "most_collected": {"label": "Most collected piece", "who": "piece", "unit": "collections",
                       "blurb": "the piece in the most collections"},
    "first_in": {"label": "First piece in", "who": "piece", "unit": "",
                 "blurb": "the first piece entered"},
    "studio_most_makers": {"label": "Studio with the most makers", "who": "studio", "unit": "makers",
                           "blurb": "the shop with the most different makers entering a piece"},
    "studio_most_pieces": {"label": "Studio with the most pieces", "who": "studio", "unit": "pieces",
                           "blurb": "the shop with the most pieces entered"},
    "studio_most_collected": {"label": "Studio collected most", "who": "studio", "unit": "collections",
                              "blurb": "the shop whose pieces are in the most collections"},
    "maker_most_registered": {"label": "Maker with the most registered pieces", "who": "maker",
                              "unit": "registered pieces",
                              "blurb": "the maker who entered the most pieces with a Glass ID"},
    "maker_most_collected": {"label": "Maker collected most", "who": "maker", "unit": "collections",
                             "blurb": "the maker whose pieces are in the most collections"},
    "maker_most_votes": {"label": "Maker with the most votes", "who": "maker", "unit": "votes",
                         "blurb": "the maker with the most votes across their pieces"},
    "rookie": {"label": "Best first-time maker", "who": "maker", "unit": "votes",
               "blurb": "the most-voted maker whose first collecting event this is"},
    "collector_most_points": {"label": "Collector with the most points", "who": "collector", "unit": "points",
                              "blurb": "the collector holding the most points from this event"},
    "collector_most_pieces": {"label": "Collector with the most pieces", "who": "collector", "unit": "pieces",
                              "blurb": "the collector who added the most pieces from this event"},
    "collector_most_studios": {"label": "Collector with the most studios", "who": "collector", "unit": "studios",
                               "blurb": "the collector holding pieces from the most studios"},
    "judges": {"label": "Judges' pick", "who": "hand", "unit": "",
               "blurb": "named by hand; no standings"},
}
PRIZE_METRIC = {"studio_most_makers": "makers", "studio_most_pieces": "entries",
                "studio_most_collected": "claims", "maker_most_registered": "registered",
                "maker_most_collected": "claims", "maker_most_votes": "votes", "rookie": "votes",
                "collector_most_points": "points", "collector_most_pieces": "pieces",
                "collector_most_studios": "studios"}

BADGES = {
    "first-scan": {
        "label": "First scan",
        "blurb": "You pointed a phone at a piece of glass and the registry said: yes, that's the one."},
    "maker-debut": {
        "label": "Maker debut",
        "blurb": "Your first approved entry. The crowd can see you now — and vote."},
    "five-shops": {
        "label": "Five shops",
        "blurb": "Verified pieces from five different studios. That's a route, not a shelf."},
    "three-seasons": {
        "label": "Three seasons",
        "blurb": "You've collected across three seasons. At this point it's a tradition."},
    "set-complete": {
        "label": "Set complete",
        "blurb": "One piece from every studio in a season. The hard one. Frame the shelf."},
    "crowd-favourite": {
        "label": "Crowd favourite",
        "blurb": "The most-voted piece of the season. Hang this somewhere people will ask about it."},
    "legend_hunter": {
        "label": "Legend hunter",
        "blurb": "You found a legendary — the one-of-one the maker never pointed at. Now you know its story."},
}


# --- helpers -----------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_table(conn, name: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (name,)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r is not None else None


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "season"


def _studio_key(name: str) -> str:
    """Studios are grouped by this: the name without regard to case or spacing."""
    return re.sub(r"\s+", " ", (name or "")).strip().lower()


def object_image_url(row_id: str) -> str:
    return IMAGE_URL.format(row_id=row_id)


def _games_link(slug: str) -> str:
    return f"{os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')}/games.html?season={slug or ''}"


def _nudge(title: str, fields: dict, color: int = 0x7C3AED) -> None:
    """A best-effort Discord note through central.notify. Never raises: a
    missing webhook or a network hiccup must not fail a claim or a status change."""
    try:
        from central import notify
        notify.notify_message(title, fields, color)
    except Exception:  # noqa: BLE001
        pass


# --- schema --------------------------------------------------------------------
def ensure(conn) -> None:
    approvals.ensure_approvals(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS seasons (
        id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT, theme TEXT DEFAULT '',
        description TEXT DEFAULT '', starts TEXT DEFAULT '', entries_close TEXT DEFAULT '',
        voting_closes TEXT DEFAULT '', ends TEXT DEFAULT '',
        status TEXT DEFAULT 'draft',          -- draft|open|voting|closed
        rules TEXT DEFAULT '', prize_note TEXT DEFAULT '',
        votes_per_voter INTEGER DEFAULT 3, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_entries (
        id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, object_row_id TEXT NOT NULL,
        artist_uid TEXT, artist_name TEXT DEFAULT '', studio TEXT DEFAULT '',
        title TEXT DEFAULT '', edition_size INTEGER DEFAULT 1, price_note TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',        -- pending|approved|rejected
        created_at TEXT, UNIQUE(season_id, object_row_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_votes (
        id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, entry_id INTEGER NOT NULL,
        voter_uid TEXT NOT NULL, created_at TEXT,
        UNIQUE(season_id, entry_id, voter_uid))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_claims (
        id INTEGER PRIMARY KEY, season_id INTEGER, entry_id INTEGER, object_row_id TEXT NOT NULL,
        collector_uid TEXT NOT NULL, collector_name TEXT DEFAULT '', confidence REAL,
        report_json TEXT, location TEXT DEFAULT '',
        status TEXT,                          -- verified|pending|approved|rejected
        created_at TEXT, UNIQUE(object_row_id, collector_uid))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_badges (
        id INTEGER PRIMARY KEY, uid TEXT NOT NULL, badge TEXT NOT NULL,
        season_id INTEGER DEFAULT 0, awarded_at TEXT, UNIQUE(uid, badge, season_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_categories (
        id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, slug TEXT NOT NULL, title TEXT NOT NULL,
        kind TEXT DEFAULT 'vote',             -- vote|measured
        measure TEXT DEFAULT '',              -- 'height' (mm) for measured kinds
        solo_only INTEGER DEFAULT 0, sort INTEGER DEFAULT 0,
        UNIQUE(season_id, slug))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_award_votes (
        id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, category_id INTEGER NOT NULL,
        entry_id INTEGER NOT NULL, voter_uid TEXT NOT NULL, created_at TEXT,
        UNIQUE(season_id, category_id, voter_uid))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS season_prizes (
        id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, slug TEXT NOT NULL,
        title TEXT NOT NULL,
        awarded_for TEXT NOT NULL DEFAULT 'peoples_choice',   -- a PRIZE_FOR key or award:<slug>
        detail TEXT DEFAULT '', sponsor TEXT DEFAULT '', sponsor_url TEXT DEFAULT '',
        winner_text TEXT DEFAULT '',          -- written at close-out, or by an admin, by hand
        sort INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
        UNIQUE(season_id, slug))""")
    # entry flags added after the first deploy: guarded, so re-running is a no-op
    have = {r[1] for r in conn.execute("PRAGMA table_info(season_entries)").fetchall()}
    for col, decl in (("solo", "INTEGER DEFAULT 0"), ("legendary", "INTEGER DEFAULT 0"),
                      ("legend", "TEXT DEFAULT ''")):
        if col not in have:
            conn.execute(f"ALTER TABLE season_entries ADD COLUMN {col} {decl}")
    # who won a prize, as data (the maker's career record reads it), beside the words
    have = {r[1] for r in conn.execute("PRAGMA table_info(season_prizes)").fetchall()}
    if "winner_json" not in have:
        conn.execute("ALTER TABLE season_prizes ADD COLUMN winner_json TEXT DEFAULT ''")
    for ddl in ("CREATE INDEX IF NOT EXISTS ix_sentries_season ON season_entries(season_id, status)",
                "CREATE INDEX IF NOT EXISTS ix_sentries_artist ON season_entries(artist_uid)",
                "CREATE INDEX IF NOT EXISTS ix_svotes_entry ON season_votes(entry_id)",
                "CREATE INDEX IF NOT EXISTS ix_svotes_voter ON season_votes(season_id, voter_uid)",
                "CREATE INDEX IF NOT EXISTS ix_sclaims_obj ON season_claims(object_row_id, status)",
                "CREATE INDEX IF NOT EXISTS ix_sclaims_uid ON season_claims(collector_uid)",
                "CREATE INDEX IF NOT EXISTS ix_sbadges_uid ON season_badges(uid)",
                "CREATE INDEX IF NOT EXISTS ix_scats_season ON season_categories(season_id, sort)",
                "CREATE INDEX IF NOT EXISTS ix_sawards_cat ON season_award_votes(category_id, entry_id)",
                "CREATE INDEX IF NOT EXISTS ix_sprizes_season ON season_prizes(season_id, sort)"):
        conn.execute(ddl)
    conn.commit()


# --- seasons -------------------------------------------------------------------
_SEASON_SQL = """SELECT s.*,
    (SELECT COUNT(*) FROM season_entries e WHERE e.season_id=s.id AND e.status='approved')
        AS entry_count,
    (SELECT COUNT(*) FROM season_votes v WHERE v.season_id=s.id) AS vote_count,
    (SELECT COUNT(*) FROM season_claims c WHERE c.season_id=s.id
        AND c.status IN ('verified','approved')) AS claim_count,
    (SELECT COUNT(*) FROM season_entries e WHERE e.season_id=s.id AND e.status='approved'
        AND e.legendary=1 AND NOT EXISTS (SELECT 1 FROM season_claims c
            WHERE c.object_row_id=e.object_row_id AND c.status IN ('verified','approved')))
        AS legendaries_out
    FROM seasons s"""


def list_seasons(conn, status: str | None = None, include_draft: bool = False) -> list[dict]:
    where, params = [], []
    if status:
        where.append("s.status=?"); params.append(status)
    elif not include_draft:
        where.append("s.status<>'draft'")
    sql = _SEASON_SQL + ((" WHERE " + " AND ".join(where)) if where else "")
    sql += " ORDER BY s.starts DESC, s.id DESC"
    return _rows(conn.execute(sql, params))


def get_season(conn, slug_or_id) -> dict | None:
    if isinstance(slug_or_id, int) or (isinstance(slug_or_id, str) and slug_or_id.isdigit()):
        return _one(conn.execute(_SEASON_SQL + " WHERE s.id=?", (int(slug_or_id),)))
    return _one(conn.execute(_SEASON_SQL + " WHERE s.slug=?", (str(slug_or_id or ""),)))


def current_season(conn) -> dict | None:
    """The season visitors should see: an open one (latest by start date), else a
    season in voting, else None (between seasons)."""
    for status in ("open", "voting"):
        rows = list_seasons(conn, status=status)
        if rows:
            return rows[0]
    return None


def create_season(conn, *, title: str, slug: str | None = None, theme: str = "",
                  description: str = "", starts: str = "", entries_close: str = "",
                  voting_closes: str = "", ends: str = "", rules: str = "", prize_note: str = "",
                  votes_per_voter: int = 3, status: str = "draft",
                  categories: list[dict] | None = None,
                  prizes: list[dict] | None = None) -> int:
    """Create a season. `categories` is a list of {title, kind, measure, solo_only};
    when none are given the theme picks the defaults (``default_categories``). `prizes`
    is a list of {title, awarded_for, detail, sponsor, sponsor_url}; None means the
    theme's defaults (``default_prizes``), an empty list means none."""
    title = (title or "").strip()
    if not title:
        raise ValueError("a season needs a title")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    base = slugify(slug or title)
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM seasons WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{n}"; n += 1
    cur = conn.execute(
        """INSERT INTO seasons (slug,title,theme,description,starts,entries_close,voting_closes,
           ends,status,rules,prize_note,votes_per_voter,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (slug, title, theme or "", description or "", starts or "", entries_close or "",
         voting_closes or "", ends or "", status, rules or "", prize_note or "",
         max(1, int(votes_per_voter or 3)), _now()))
    sid = int(cur.lastrowid)
    add_categories(conn, sid, categories or default_categories(theme))
    try:
        add_prizes(conn, sid, default_prizes(theme) if prizes is None else prizes)
    except ValueError:
        # a prize line that names no known kind: leave nothing half-made behind
        for t in ("season_prizes", "season_categories"):
            conn.execute(f"DELETE FROM {t} WHERE season_id=?", (sid,))
        conn.execute("DELETE FROM seasons WHERE id=?", (sid,))
        conn.commit()
        raise
    conn.commit()
    return sid


# --- award categories -----------------------------------------------------------
def default_categories(theme: str) -> list[dict]:
    """The award categories a theme gets when the admin doesn't say otherwise.
    People's choice is implicit (season_votes) and never listed here."""
    t = (theme or "").lower()
    if "pumpkin" in t:
        return [{"title": "Best stem", "kind": "vote"},
                {"title": "Best colour", "kind": "vote"},
                {"title": "Biggest pumpkin", "kind": "measured", "measure": "height"},
                {"title": "Biggest pumpkin, solo", "kind": "measured", "measure": "height",
                 "solo_only": 1}]
    if "ornament" in t:
        return [{"title": "Best hook", "kind": "vote"},
                {"title": "Best colour", "kind": "vote"},
                {"title": "Most likely to survive the cat", "kind": "vote"}]
    return [{"title": "Best in show", "kind": "vote"}, {"title": "Best colour", "kind": "vote"}]


def parse_categories(text: str) -> list[dict]:
    """One category per line: ``title | vote`` or ``title | measured [height] [| solo]``.
    Anything unrecognised in the kind column means a vote category."""
    out: list[dict] = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        title = parts[0] if parts else ""
        if not title:
            continue
        rest = [p.lower() for p in parts[1:]]
        kind = "measured" if any(p.startswith("measured") for p in rest) else "vote"
        measure = "height" if kind == "measured" else ""
        solo = int(any(p == "solo" or p.startswith("solo") for p in rest))
        out.append({"title": title[:120], "kind": kind, "measure": measure, "solo_only": solo})
    return out


def categories_text(cats: list[dict]) -> str:
    """The inverse of parse_categories — what the admin textarea is prefilled with."""
    lines = []
    for c in cats or []:
        bits = [c["title"], c.get("kind") or "vote"]
        if c.get("solo_only"):
            bits.append("solo")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def add_categories(conn, season_id: int, cats: list[dict]) -> list[int]:
    """Append award categories to a season (slugs made unique within it)."""
    ids: list[int] = []
    taken = {r[0] for r in conn.execute("SELECT slug FROM season_categories WHERE season_id=?",
                                        (int(season_id),)).fetchall()}
    n0 = conn.execute("SELECT COALESCE(MAX(sort),0) FROM season_categories WHERE season_id=?",
                      (int(season_id),)).fetchone()[0]
    for i, c in enumerate(cats or [], 1):
        title = (c.get("title") or "").strip()
        if not title:
            continue
        kind = c.get("kind") if c.get("kind") in CATEGORY_KINDS else "vote"
        measure = (c.get("measure") or ("height" if kind == "measured" else "")) if kind == "measured" else ""
        if kind == "measured" and measure not in MEASURES:
            raise ValueError(f"measure must be one of {MEASURES}")
        base = slugify(title)
        slug, k = base, 2
        while slug in taken:
            slug = f"{base}-{k}"; k += 1
        taken.add(slug)
        cur = conn.execute(
            "INSERT INTO season_categories (season_id, slug, title, kind, measure, solo_only, sort) "
            "VALUES (?,?,?,?,?,?,?)",
            (int(season_id), slug, title[:120], kind, measure, int(bool(c.get("solo_only"))),
             int(n0) + i))
        ids.append(int(cur.lastrowid))
    conn.commit()
    return ids


def categories(conn, season_id: int, backfill: bool = True) -> list[dict]:
    """A season's award categories. A season created before awards existed has none;
    unless it is closed, give it the defaults for its theme the first time anyone asks."""
    q = "SELECT * FROM season_categories WHERE season_id=? ORDER BY sort, id"
    rows = _rows(conn.execute(q, (int(season_id),)))
    if not rows and backfill:
        s = get_season(conn, int(season_id))
        if s and s.get("status") != "closed":
            cats = default_categories(f"{s.get('theme') or ''} {s.get('title') or ''}")
            if cats:
                add_categories(conn, int(season_id), cats)
                conn.commit()
                rows = _rows(conn.execute(q, (int(season_id),)))
    return rows


def get_category(conn, season_id: int, slug_or_id) -> dict | None:
    if isinstance(slug_or_id, int) or (isinstance(slug_or_id, str) and slug_or_id.isdigit()):
        return _one(conn.execute("SELECT * FROM season_categories WHERE season_id=? AND id=?",
                                 (int(season_id), int(slug_or_id))))
    return _one(conn.execute("SELECT * FROM season_categories WHERE season_id=? AND slug=?",
                             (int(season_id), str(slug_or_id or ""))))


# --- prizes ---------------------------------------------------------------------------
def _noun(theme: str) -> str:
    t = (theme or "").lower()
    return "pumpkin" if "pumpkin" in t else "ornament" if "ornament" in t else "piece"


def default_prizes(theme: str) -> list[dict]:
    """What is on the table when the admin doesn't say otherwise. No cash: a homepage
    feature, an Instagram post, the name at the top of the board. Edited any time."""
    n = _noun(theme)
    return [
        {"title": "Most makers from one shop", "awarded_for": "studio_most_makers",
         "detail": f"The studio whose people entered the most: the most different makers with a {n} "
                   "in. A feature on the homepage and the Instagram, and the shop's name at the top "
                   "of the studio board."},
        {"title": f"Most registered {n}s", "awarded_for": "maker_most_registered",
         "detail": f"The maker who entered the most {n}s with a Glass ID. A homepage feature."},
        {"title": "Crowd favourite", "awarded_for": "peoples_choice",
         "detail": f"The most-voted {n}: the Crowd Favourite badge and a homepage feature."},
        {"title": "Collector of the event", "awarded_for": "collector_most_points",
         "detail": "The collector holding the most points from this event: a feature on the homepage."},
    ]


def _prize_for_key(raw: str, cats: list[dict] | None) -> str:
    """Resolve what a prize is awarded for — a PRIZE_FOR key, its label, ``award:<slug>``,
    or an award category's title or slug — to the stored key. ValueError when unknown."""
    w = re.sub(r"\s+", " ", (raw or "")).strip().lower()
    if not w:
        return "peoples_choice"
    if w in PRIZE_FOR:
        return w
    for k, meta in PRIZE_FOR.items():
        if w == meta["label"].lower():
            return k
    if w.startswith("award:"):
        w = w[6:].strip()
    for c in cats or []:
        if w in ((c.get("slug") or "").lower(), (c.get("title") or "").lower()):
            return f"award:{c['slug']}"
    raise ValueError(f"“{raw}” is not something a prize can be awarded for — use one of: "
                     + ", ".join(m["label"] for m in PRIZE_FOR.values()) + ", or an award's title")


def parse_prizes(text: str) -> list[dict]:
    """One prize per line: ``title | awarded for | detail | sponsor | sponsor url``. The
    second column takes a kind's label ("Studio with the most makers"), its key, or an
    award's title; it is checked against the season when the prizes are added."""
    out: list[dict] = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        title = parts[0] if parts else ""
        if not title:
            continue
        parts += [""] * 5
        out.append({"title": title[:120], "awarded_for": parts[1], "detail": parts[2][:500],
                    "sponsor": parts[3][:120], "sponsor_url": parts[4][:300]})
    return out


def prizes_text(rows: list[dict]) -> str:
    """The inverse of parse_prizes — what the admin textarea is prefilled with."""
    lines = []
    for p in rows or []:
        k = p.get("awarded_for") or "peoples_choice"
        word = PRIZE_FOR[k]["label"] if k in PRIZE_FOR else k
        bits = [p.get("title") or "", word, p.get("detail") or "", p.get("sponsor") or "",
                p.get("sponsor_url") or ""]
        while len(bits) > 2 and not bits[-1]:
            bits.pop()
        lines.append(" | ".join(bits))
    return "\n".join(lines)


_URL_RE = re.compile(r"https?://\S+$")


def _prize_fields(p: dict, cats: list[dict] | None, partial: bool = False) -> dict:
    """Validated columns for a prize row: all of them, or (partial) only the ones given."""
    out: dict = {}
    if "title" in p or not partial:
        title = re.sub(r"\s+", " ", (p.get("title") or "")).strip()
        if not title:
            raise ValueError("a prize needs a title")
        out["title"] = title[:120]
    if "awarded_for" in p or not partial:
        out["awarded_for"] = _prize_for_key(p.get("awarded_for") or "", cats)
    for k, n in (("detail", 500), ("sponsor", 120), ("winner_text", 300)):
        if k in p or not partial:
            out[k] = (p.get(k) or "").strip()[:n]
    if "sponsor_url" in p or not partial:
        u = (p.get("sponsor_url") or "").strip()
        if u and not _URL_RE.match(u):
            raise ValueError("the sponsor's link has to start with http:// or https://")
        out["sponsor_url"] = u[:300]
    return out


def add_prizes(conn, season_id: int, rows: list[dict]) -> list[int]:
    """Append prizes to a season (slugs made unique within it). A line whose kind is
    not known raises ValueError before anything is written."""
    cats = categories(conn, int(season_id), backfill=False)
    fields = [_prize_fields(p, cats) for p in rows or [] if (p.get("title") or "").strip()]
    ids: list[int] = []
    taken = {r[0] for r in conn.execute("SELECT slug FROM season_prizes WHERE season_id=?",
                                        (int(season_id),)).fetchall()}
    n0 = conn.execute("SELECT COALESCE(MAX(sort),0) FROM season_prizes WHERE season_id=?",
                      (int(season_id),)).fetchone()[0]
    for i, f in enumerate(fields, 1):
        base = slugify(f["title"])
        slug, k = base, 2
        while slug in taken:
            slug = f"{base}-{k}"; k += 1
        taken.add(slug)
        cur = conn.execute(
            "INSERT INTO season_prizes (season_id, slug, title, awarded_for, detail, sponsor, "
            "sponsor_url, winner_text, sort, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (int(season_id), slug, f["title"], f["awarded_for"], f["detail"], f["sponsor"],
             f["sponsor_url"], f["winner_text"], int(n0) + i, _now(), _now()))
        ids.append(int(cur.lastrowid))
    conn.commit()
    return ids


def prizes(conn, season_id: int) -> list[dict]:
    """A season's prizes in order, each with `for_label`, `who` and `unit` for the page."""
    rows = _rows(conn.execute("SELECT * FROM season_prizes WHERE season_id=? ORDER BY sort, id",
                              (int(season_id),)))
    cats = ({c["slug"]: c for c in categories(conn, int(season_id), backfill=False)}
            if any((p.get("awarded_for") or "").startswith("award:") for p in rows) else {})
    for p in rows:
        k = p.get("awarded_for") or ""
        meta = PRIZE_FOR.get(k)
        if meta:
            p["who"], p["unit"], p["for_label"] = meta["who"], meta["unit"], meta["label"]
        else:
            c = cats.get(k[6:]) if k.startswith("award:") else None
            p["who"] = "piece"
            p["unit"] = "mm" if c and c.get("kind") == "measured" else "picks"
            p["for_label"] = c["title"] if c else "an award that was removed"
    return rows


def get_prize(conn, prize_id: int) -> dict | None:
    row = _one(conn.execute("SELECT season_id FROM season_prizes WHERE id=?", (int(prize_id),)))
    if not row:
        return None
    return next((p for p in prizes(conn, int(row["season_id"])) if int(p["id"]) == int(prize_id)), None)


def add_prize(conn, season_id: int, **fields) -> int:
    ids = add_prizes(conn, season_id, [fields])
    if not ids:
        raise ValueError("a prize needs a title")
    return ids[0]


def update_prize(conn, prize_id: int, **fields) -> dict:
    """Change any of title, awarded_for, detail, sponsor, sponsor_url, winner_text."""
    p = get_prize(conn, prize_id)
    if not p:
        raise ValueError("No such prize")
    f = _prize_fields(fields, categories(conn, int(p["season_id"]), backfill=False), partial=True)
    if f:
        sets = ", ".join(f"{k}=?" for k in f)
        conn.execute(f"UPDATE season_prizes SET {sets}, updated_at=? WHERE id=?",
                     [*f.values(), _now(), int(prize_id)])
        conn.commit()
    return get_prize(conn, prize_id) or p


def delete_prize(conn, prize_id: int) -> None:
    if not get_prize(conn, prize_id):
        raise ValueError("No such prize")
    conn.execute("DELETE FROM season_prizes WHERE id=?", (int(prize_id),))
    conn.commit()


def set_status(conn, season_id: int, status: str) -> None:
    """Move a season along draft → open → voting → closed (any transition is
    allowed). Opening or starting voting posts a Discord nudge, once per change."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    prev = _one(conn.execute("SELECT * FROM seasons WHERE id=?", (int(season_id),)))
    conn.execute("UPDATE seasons SET status=? WHERE id=?", (status, int(season_id)))
    conn.commit()
    if not prev or prev.get("status") == status:
        return
    if status == "open":
        _nudge(f"🎃 Season open: {prev['title']}",
               {"Theme": prev.get("theme"), "Entries close": prev.get("entries_close"),
                "Voting closes": prev.get("voting_closes"), "Prize": prev.get("prize_note"),
                "Enter a piece": "Glowtbook → Objects → Seasons",
                "Page": _games_link(prev.get("slug"))}, color=0xEA580C)
    elif status == "voting":
        _nudge(f"🗳️ Voting is open: {prev['title']}",
               {"Theme": prev.get("theme"), "Voting closes": prev.get("voting_closes"),
                "Votes per person": prev.get("votes_per_voter"),
                "Vote": _games_link(prev.get("slug"))})


def close_season(conn, season_id: int) -> dict:
    """Close the season and hand out the end-of-season badges: Crowd Favourite to
    the maker of the top-voted entry, and Set Complete to any collector who holds
    a piece from every studio that entered. Each prize's leader is written in as its
    winner (``settle_prizes``) unless an admin named one already."""
    set_status(conn, season_id, "closed")
    board = leaderboard(conn, season_id)["peoples_choice"]
    out = {"crowd_favourite": None, "badges": {}}
    if board and board[0].get("vote_count", 0) > 0 and board[0].get("artist_uid"):
        top = board[0]
        if _grant(conn, top["artist_uid"], "crowd-favourite", season_id):
            out["badges"].setdefault(top["artist_uid"], []).append("crowd-favourite")
        out["crowd_favourite"] = {"entry_id": top["id"], "title": top["title"],
                                  "artist_name": top["artist_name"], "votes": top["vote_count"]}
    uids = [r[0] for r in conn.execute(
        "SELECT DISTINCT collector_uid FROM season_claims WHERE season_id=? AND status IN ('verified','approved')",
        (int(season_id),)).fetchall()]
    for uid in uids:
        new = award_badges(conn, uid)
        if new:
            out["badges"].setdefault(uid, []).extend(new)
    out["prizes"] = settle_prizes(conn, season_id)
    conn.commit()
    return out


# --- what a maker may enter -------------------------------------------------------
def _public_object(conn, row_id: str) -> dict | None:
    """The public, approved object row — or None if missing / not yet approved."""
    if not _has_table(conn, "objects"):
        return None
    return _one(conn.execute(
        f"SELECT _row_id, title, maker, year, techniques, content_hash, contributor FROM objects "
        f"WHERE _row_id=? AND {approvals.approved_subquery()}", (row_id, "objects")))


def _store_hashes(uid: str) -> set[str]:
    """content_hashes of the pieces this user contributed from their Glowtbook."""
    try:
        from glowtbook import store as gstore
        path = os.environ.get("GLOWTBOOK_DB_PATH") or None
        s = gstore.open_store(path)
        try:
            return {r[0] for r in s.execute(
                "SELECT DISTINCT content_hash FROM contribution WHERE user_id=? AND content_hash<>''",
                (uid,)).fetchall() if r[0]}
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        return set()


def _owns(conn, uid: str, content_hash: str | None) -> bool:
    """Did `uid` publish the object with this content_hash? The review queue is
    checked first; the Glowtbook store only if that says no."""
    if not uid or not content_hash:
        return False
    if _has_table(conn, "object_submissions") and conn.execute(
            "SELECT 1 FROM object_submissions WHERE content_hash=? AND submitted_uid=? LIMIT 1",
            (content_hash, uid)).fetchone():
        return True
    return content_hash in _store_hashes(uid)


def _my_hashes(conn, uid: str) -> set[str]:
    """content_hashes of everything `uid` published: the review queue
    (object_submissions.submitted_uid) plus their Glowtbook contributions."""
    if not uid:
        return set()
    hashes: set[str] = set()
    if _has_table(conn, "object_submissions"):
        hashes |= {r[0] for r in conn.execute(
            "SELECT DISTINCT content_hash FROM object_submissions WHERE submitted_uid=?",
            (uid,)).fetchall() if r[0]}
    return hashes | _store_hashes(uid)


def owns_object(conn, uid: str, object_row_id: str) -> bool:
    """Did `uid` publish this public object? The maker's side of a claim."""
    if not uid or not object_row_id or not _has_table(conn, "objects"):
        return False
    row = _one(conn.execute("SELECT content_hash FROM objects WHERE _row_id=?", (object_row_id,)))
    return bool(row) and _owns(conn, uid, row.get("content_hash"))


def season_object_rows(conn, uid: str) -> list[dict]:
    """Public, approved objects this person may enter: the ones they submitted
    (object_submissions.submitted_uid) plus the ones their Glowtbook says they
    contributed (contribution.content_hash) — both resolved through content_hash."""
    if not uid or not _has_table(conn, "objects"):
        return []
    hashes = _my_hashes(conn, uid)
    if not hashes:
        return []
    out, hs = [], sorted(hashes)
    for i in range(0, len(hs), 400):          # keep well under SQLite's bind limit
        chunk = hs[i:i + 400]
        q = ",".join("?" for _ in chunk)
        out += _rows(conn.execute(
            f"SELECT _row_id, title, maker, year FROM objects WHERE content_hash IN ({q}) "
            f"AND {approvals.approved_subquery()} ORDER BY title", (*chunk, "objects")))
    seen, uniq = set(), []
    for o in out:
        if o["_row_id"] in seen:
            continue
        seen.add(o["_row_id"])
        uniq.append({"row_id": o["_row_id"], "title": o.get("title") or "Untitled",
                     "maker": o.get("maker") or "", "year": o.get("year") or "",
                     "image_url": object_image_url(o["_row_id"])})
    return uniq


# --- entries ----------------------------------------------------------------------
_ENTRY_SQL = """SELECT e.*, s.slug AS season_slug, s.title AS season_title, s.theme AS season_theme,
    (SELECT COUNT(*) FROM season_votes v WHERE v.entry_id=e.id) AS vote_count,
    (SELECT COUNT(*) FROM season_claims c WHERE c.object_row_id=e.object_row_id
        AND c.status IN ('verified','approved')) AS claim_count
    FROM season_entries e JOIN seasons s ON s.id=e.season_id"""


_DIMS_RE = re.compile(r"^\s*([\d.]+)\s*[×x*]\s*([\d.]+)\s*[×x*]\s*([\d.]+)\s*mm", re.I)


def parse_height_mm(dimensions: str | None) -> float | None:
    """Height from the string the fingerprint mat writes onto an object
    (``glowtbook.matdetect``: ``"118 × 112 × 134 mm (W×D×H)"``). None when the
    piece was never measured, or the text isn't in that shape."""
    m = _DIMS_RE.match(dimensions or "")
    if not m:
        return None
    try:
        h = float(m.group(3))
    except ValueError:
        return None
    return h if h > 0 else None


def _fp_present(fp_json, manifest_json) -> bool:
    return bool(fp_json) or '"fingerprint"' in (manifest_json or "")


def _object_facts(conn, row_ids) -> dict[str, dict]:
    """{row_id: {fingerprint, height_mm, dimensions}} for public objects: whether
    a fingerprint is on file and what the mat measured."""
    ids = sorted({r for r in (row_ids or []) if r})
    out = {rid: {"fingerprint": False, "height_mm": None, "dimensions": ""} for rid in ids}
    if not ids or not _has_table(conn, "objects"):
        return out
    try:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            q = ",".join("?" for _ in chunk)
            for r in conn.execute(f"SELECT _row_id, fingerprint_json, manifest_json, dimensions "
                                  f"FROM objects WHERE _row_id IN ({q})", chunk).fetchall():
                out[r[0]] = {"fingerprint": _fp_present(r[1], r[2]),
                             "height_mm": parse_height_mm(r[3]), "dimensions": r[3] or ""}
    except Exception:  # noqa: BLE001  (an older objects table without the columns)
        pass
    return out


def has_fingerprint(conn, object_row_id: str) -> bool:
    """Is a re-identification fingerprint on file for this public piece?"""
    return bool(_object_facts(conn, [object_row_id]).get(object_row_id, {}).get("fingerprint"))


def rarity_of(entry: dict, has_fingerprint: bool, claimed: bool) -> str:
    """common → uncommon (fingerprinted) → rare (fingerprinted, edition ≤ 3) →
    legendary (flagged *and* claimed — until then it reads as its base rarity)."""
    if entry.get("legendary") and claimed:
        return "legendary"
    if has_fingerprint and int(entry.get("edition_size") or 1) <= RARE_EDITION:
        return "rare"
    if has_fingerprint:
        return "uncommon"
    return "common"


def _entry_shape(e: dict, fact: dict | None = None) -> dict:
    fact = fact or {}
    e["image_url"] = e.get("image_url") or object_image_url(e["object_row_id"])
    e["edition_size"] = int(e.get("edition_size") or 1)
    e["solo"] = int(e.get("solo") or 0)
    e["legendary"] = int(e.get("legendary") or 0)
    e["legend"] = e.get("legend") or ""
    e["fingerprinted"] = bool(fact.get("fingerprint"))
    e["height_mm"] = fact.get("height_mm")
    e["rarity"] = rarity_of(e, e["fingerprinted"], int(e.get("claim_count") or 0) > 0)
    e["points"] = RARITY_POINTS[e["rarity"]]
    return e


def _shaped(conn, rows: list[dict]) -> list[dict]:
    facts = _object_facts(conn, [r["object_row_id"] for r in rows])
    return [_entry_shape(e, facts.get(e["object_row_id"])) for e in rows]


def get_entry(conn, entry_id: int) -> dict | None:
    e = _one(conn.execute(_ENTRY_SQL + " WHERE e.id=?", (int(entry_id),)))
    return _shaped(conn, [e])[0] if e else None


def enter(conn, season_id: int, *, object_row_id: str, artist_uid: str, artist_name: str,
          title: str = "", studio: str = "", edition_size: int = 1, price_note: str = "",
          image_url: str = "", solo: bool = False, legendary: bool = False,
          legend: str = "") -> int:
    """Enter a public piece into a season. Pending until an admin approves it.
    Up to MAX_ENTRIES per maker per season. A *legendary* is the maker's one
    edition-of-one per season: fingerprinted, with a legend of LEGEND_MIN+ chars."""
    season = get_season(conn, int(season_id))
    if not season:
        raise ValueError("No such season")
    if season["status"] != "open":
        raise ValueError("This season isn't taking entries right now")
    obj = _public_object(conn, object_row_id)
    if not obj:
        raise ValueError("That piece isn't published yet — publish it from Glowtbook first")
    if not _owns(conn, artist_uid, obj.get("content_hash")):
        raise ValueError("Only the person who published a piece can enter it")
    if conn.execute("SELECT 1 FROM season_entries WHERE season_id=? AND object_row_id=?",
                    (season["id"], object_row_id)).fetchone():
        raise ValueError("That piece is already entered in this season")
    mine = _rows(conn.execute(
        "SELECT legendary FROM season_entries WHERE season_id=? AND artist_uid=? AND status<>'rejected'",
        (season["id"], artist_uid)))
    if len(mine) >= MAX_ENTRIES:
        raise ValueError(f"Up to {MAX_ENTRIES} entries per maker per season — that's the lot")
    ed = max(1, int(edition_size or 1))
    legend = (legend or "").strip()
    if legendary:
        if ed != 1:
            raise ValueError("A legendary has to be an edition of one")
        if not has_fingerprint(conn, object_row_id):
            raise ValueError("Fingerprint the piece before you make it your legendary — "
                             "that's how its collector proves they found it")
        if len(legend) < LEGEND_MIN:
            raise ValueError(f"Write its legend — at least {LEGEND_MIN} characters; the collector "
                             "who claims it reads this")
        if any(int(m.get("legendary") or 0) for m in mine):
            raise ValueError("One legendary per maker per season — you've already placed yours")
    else:
        legend = ""
    cur = conn.execute(
        """INSERT INTO season_entries (season_id,object_row_id,artist_uid,artist_name,studio,title,
           edition_size,price_note,image_url,status,created_at,solo,legendary,legend)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (season["id"], object_row_id, artist_uid, (artist_name or obj.get("maker") or "")[:200],
         (studio or "")[:200], (title or obj.get("title") or "Untitled")[:200],
         ed, (price_note or "")[:200], image_url or object_image_url(object_row_id), "pending",
         _now(), int(bool(solo)), int(bool(legendary)), legend[:2000]))
    conn.commit()
    eid = int(cur.lastrowid)
    if not entry_review():
        approve_entry(conn, eid, True)      # the piece is public already: live, claims attached, badges
    return eid


def release_backlog(conn) -> int:
    """Default mode only: entries still ``pending`` from when entries waited for an admin go
    live now; their pieces were public when they were entered. Returns how many it approved."""
    if entry_review():
        return 0
    rows = _rows(conn.execute("SELECT id FROM season_entries WHERE status='pending'"))
    for r in rows:
        try:
            approve_entry(conn, r["id"], True)
        except ValueError:
            pass
    return len(rows)


def my_entries(conn, uid: str) -> list[dict]:
    rows = _rows(conn.execute(_ENTRY_SQL + " WHERE e.artist_uid=? ORDER BY e.created_at DESC, e.id DESC",
                              (uid,)))
    return _shaped(conn, rows)


def entries(conn, season_id: int, status: str | None = "approved") -> list[dict]:
    """Entries of a season (approved by default; None = every status), each with
    vote_count, claim_count, image_url, rarity and points."""
    where, params = ["e.season_id=?"], [int(season_id)]
    if status:
        where.append("e.status=?"); params.append(status)
    rows = _rows(conn.execute(_ENTRY_SQL + " WHERE " + " AND ".join(where)
                              + " ORDER BY e.created_at, e.id", params))
    return _shaped(conn, rows)


def pending_entries(conn) -> list[dict]:
    rows = _rows(conn.execute(_ENTRY_SQL + " WHERE e.status='pending' ORDER BY e.created_at, e.id"))
    return _shaped(conn, rows)


def legendaries_out(conn, season_id: int | None = None) -> int:
    """Approved legendaries nobody has claimed yet — "still out there"."""
    sql = """SELECT COUNT(*) FROM season_entries e WHERE e.status='approved' AND e.legendary=1
             AND NOT EXISTS (SELECT 1 FROM season_claims c WHERE c.object_row_id=e.object_row_id
                             AND c.status IN ('verified','approved'))"""
    params: tuple = ()
    if season_id is not None:
        sql += " AND e.season_id=?"; params = (int(season_id),)
    return int(conn.execute(sql, params).fetchone()[0])


def approve_entry(conn, entry_id: int, ok: bool = True) -> None:
    e = get_entry(conn, entry_id)
    if not e:
        raise ValueError("No such entry")
    conn.execute("UPDATE season_entries SET status=? WHERE id=?",
                 ("approved" if ok else "rejected", int(entry_id)))
    if ok:
        # claims made before the entry was approved now belong to this season
        conn.execute("""UPDATE season_claims SET season_id=?, entry_id=? WHERE object_row_id=?
                        AND entry_id IS NULL""", (e["season_id"], int(entry_id), e["object_row_id"]))
        conn.commit()
        if e.get("artist_uid"):
            award_badges(conn, e["artist_uid"])
    conn.commit()


def set_entry_studio(conn, entry_id: int, studio: str) -> dict:
    """An admin tidies the shop an entry names: the studio board and the studio prizes
    group by it, so two spellings of one shop should not be two shops."""
    e = get_entry(conn, entry_id)
    if not e:
        raise ValueError("No such entry")
    conn.execute("UPDATE season_entries SET studio=? WHERE id=?",
                 (re.sub(r"\s+", " ", (studio or "")).strip()[:200], int(entry_id)))
    conn.commit()
    return get_entry(conn, entry_id) or e


def known_studios(conn) -> list[dict]:
    """Studio names worth offering when a maker enters a piece: the Directory's approved
    studios (name, city, country), then any other name already on an approved entry."""
    out: list[dict] = []
    seen: set[str] = set()

    def take(name, city="", country="", listed=False):
        key = _studio_key(name)
        if not key or key in seen:
            return
        seen.add(key)
        out.append({"name": re.sub(r"\s+", " ", name).strip(), "city": city or "",
                    "country": country or "", "listed": listed})

    if _has_table(conn, "studios"):
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(studios)").fetchall()}
            pick = ["name"] + [c for c in ("city", "country") if c in cols]
            for r in conn.execute(f"SELECT {', '.join(pick)} FROM studios WHERE "
                                  f"{approvals.approved_subquery()} ORDER BY name", ("studios",)).fetchall():
                row = dict(zip(pick, r))
                if row.get("name"):
                    take(str(row["name"]), row.get("city"), row.get("country"), True)
        except Exception:  # noqa: BLE001  (an older studios table)
            pass
    for (name,) in conn.execute("SELECT DISTINCT studio FROM season_entries WHERE status='approved' "
                                "AND COALESCE(studio,'')<>'' ORDER BY studio").fetchall():
        take(name)
    return out


# --- votes ------------------------------------------------------------------------
def my_votes(conn, season_id: int, uid: str) -> list[int]:
    if not uid:
        return []
    return [int(r[0]) for r in conn.execute(
        "SELECT entry_id FROM season_votes WHERE season_id=? AND voter_uid=? ORDER BY id",
        (int(season_id), uid)).fetchall()]


def vote(conn, season_id: int, entry_id: int, voter_uid: str) -> dict:
    """Toggle a vote. Returns {"voted": bool, "votes": <entry total>, "remaining": <mine left>}."""
    if not voter_uid:
        raise ValueError("Sign in to vote")
    season = get_season(conn, int(season_id))
    if not season:
        raise ValueError("No such season")
    if season["status"] not in ("open", "voting"):
        raise ValueError("Voting is closed for this season")
    e = _one(conn.execute("SELECT id, status FROM season_entries WHERE id=? AND season_id=?",
                          (int(entry_id), season["id"])))
    if not e or e["status"] != "approved":
        raise ValueError("That entry isn't open for votes")
    cap = int(season.get("votes_per_voter") or 3)
    mine = my_votes(conn, season["id"], voter_uid)
    if int(entry_id) in mine:
        conn.execute("DELETE FROM season_votes WHERE season_id=? AND entry_id=? AND voter_uid=?",
                     (season["id"], int(entry_id), voter_uid))
        voted = False
        remaining = cap - (len(mine) - 1)
    else:
        if len(mine) >= cap:
            raise ValueError(f"You've used all {cap} votes for this season — un-vote one to swap")
        conn.execute("INSERT INTO season_votes (season_id, entry_id, voter_uid, created_at) "
                     "VALUES (?,?,?,?)", (season["id"], int(entry_id), voter_uid, _now()))
        voted = True
        remaining = cap - (len(mine) + 1)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM season_votes WHERE entry_id=?",
                         (int(entry_id),)).fetchone()[0]
    return {"voted": voted, "votes": int(total), "remaining": max(0, remaining)}


# --- award votes --------------------------------------------------------------------
def my_award_votes(conn, season_id: int, uid: str) -> dict[int, int]:
    """{category_id: entry_id} — the caller's one pick per category."""
    if not uid:
        return {}
    return {int(r[0]): int(r[1]) for r in conn.execute(
        "SELECT category_id, entry_id FROM season_award_votes WHERE season_id=? AND voter_uid=?",
        (int(season_id), uid)).fetchall()}


def award_vote(conn, season_id: int, category_id: int, entry_id: int, voter_uid: str) -> dict:
    """One pick per category per voter. Picking again swaps; picking the same entry
    again removes the pick. Never your own piece. Returns
    {"picked": bool, "entry_id", "votes": <that entry's picks in the category>}."""
    if not voter_uid:
        raise ValueError("Sign in to pick")
    season = get_season(conn, int(season_id))
    if not season:
        raise ValueError("No such season")
    if season["status"] not in ("open", "voting"):
        raise ValueError("Voting is closed for this season")
    cat = get_category(conn, season["id"], int(category_id))
    if not cat:
        raise ValueError("No such award")
    if cat["kind"] != "vote":
        raise ValueError(f"“{cat['title']}” is measured, not voted — the mat decides")
    e = _one(conn.execute("SELECT id, status, artist_uid, solo FROM season_entries WHERE id=? AND season_id=?",
                          (int(entry_id), season["id"])))
    if not e or e["status"] != "approved":
        raise ValueError("That entry isn't open for votes")
    if e.get("artist_uid") and e["artist_uid"] == voter_uid:
        raise ValueError("You can't pick your own piece — rule 5")
    if cat.get("solo_only") and not int(e.get("solo") or 0):
        raise ValueError(f"“{cat['title']}” is for pieces made solo")
    prev = _one(conn.execute(
        "SELECT id, entry_id FROM season_award_votes WHERE season_id=? AND category_id=? AND voter_uid=?",
        (season["id"], cat["id"], voter_uid)))
    if prev and int(prev["entry_id"]) == int(entry_id):
        conn.execute("DELETE FROM season_award_votes WHERE id=?", (prev["id"],))
        picked = False
    elif prev:
        conn.execute("UPDATE season_award_votes SET entry_id=?, created_at=? WHERE id=?",
                     (int(entry_id), _now(), prev["id"]))
        picked = True
    else:
        conn.execute("INSERT INTO season_award_votes (season_id, category_id, entry_id, voter_uid, "
                     "created_at) VALUES (?,?,?,?,?)",
                     (season["id"], cat["id"], int(entry_id), voter_uid, _now()))
        picked = True
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM season_award_votes WHERE category_id=? AND entry_id=?",
                     (cat["id"], int(entry_id))).fetchone()[0]
    return {"picked": picked, "entry_id": int(entry_id), "category_id": int(cat["id"]),
            "category_slug": cat["slug"], "votes": int(n)}


def _standing(e: dict) -> dict:
    return {"entry_id": e["id"], "title": e["title"], "artist_name": e.get("artist_name") or "",
            "studio": e.get("studio") or "", "image_url": e["image_url"], "rarity": e["rarity"]}


def awards(conn, season_id: int) -> list[dict]:
    """Every award category with its standings. Vote kinds: the top AWARD_TOP
    entries by picks. Measured kinds: every eligible entry whose object carries a
    mat-measured height, tallest first (unmeasured pieces simply aren't ranked)."""
    rows = entries(conn, int(season_id), "approved")
    counts: dict[tuple[int, int], int] = {}
    for cid, eid, n in conn.execute(
            "SELECT category_id, entry_id, COUNT(*) FROM season_award_votes WHERE season_id=? "
            "GROUP BY category_id, entry_id", (int(season_id),)).fetchall():
        counts[(int(cid), int(eid))] = int(n)
    out = []
    for c in categories(conn, season_id):
        pool = [e for e in rows if not c.get("solo_only") or e["solo"]]
        if c["kind"] == "measured":
            ranked = sorted((e for e in pool if e.get("height_mm") is not None),
                            key=lambda e: (-e["height_mm"], e["created_at"] or "", e["id"]))
            st = [{**_standing(e), "height_mm": e["height_mm"]} for e in ranked]
        else:
            ranked = sorted((e for e in pool if counts.get((c["id"], e["id"]), 0) > 0),
                            key=lambda e: (-counts[(c["id"], e["id"])], e["created_at"] or "", e["id"]))
            st = [{**_standing(e), "votes": counts[(c["id"], e["id"])]} for e in ranked[:AWARD_TOP]]
        for i, s in enumerate(st, 1):
            s["rank"] = i
        c = dict(c)
        c["solo_only"] = int(c.get("solo_only") or 0)
        c["standings"] = st
        c["leader"] = st[0] if st else None
        c["eligible"] = [e["id"] for e in pool]
        out.append(c)
    return out


# --- claims -------------------------------------------------------------------------
_CLAIM_SQL = """SELECT c.*, o.title AS object_title, o.maker, o.year, o.techniques,
    e.title AS entry_title, e.studio, e.artist_name, e.edition_size,
    s.slug AS season_slug, s.title AS season_title, s.theme AS season_theme
    FROM season_claims c
    LEFT JOIN objects o ON o._row_id=c.object_row_id
    LEFT JOIN season_entries e ON e.id=c.entry_id
    LEFT JOIN seasons s ON s.id=c.season_id"""


def _claim_shape(c: dict) -> dict:
    c["image_url"] = object_image_url(c["object_row_id"])
    c["title"] = c.get("entry_title") or c.get("object_title") or "Untitled"
    c["verified"] = c.get("status") in CLAIM_OK
    return c


def get_claim(conn, claim_id: int) -> dict | None:
    if not _has_table(conn, "objects"):
        return None
    c = _one(conn.execute(_CLAIM_SQL + " WHERE c.id=?", (int(claim_id),)))
    return _claim_shape(c) if c else None


def claim(conn, *, object_row_id: str, collector_uid: str, collector_name: str,
          confidence, report: dict | None = None, location: str = "") -> dict:
    """Record that `collector_uid` holds this piece. A fingerprint confidence of
    70+ verifies it immediately; lower waits for an admin / the artist. Returns
    the claim row plus `badges` — any badges this claim just earned."""
    if not collector_uid:
        raise ValueError("Sign in to claim a piece")
    obj = _public_object(conn, object_row_id)
    if not obj:
        raise ValueError("That piece isn't in the public registry (yet)")
    try:
        conf = float(confidence if confidence is not None else 0)
    except (TypeError, ValueError):
        raise ValueError("confidence must be a number 0–100")
    conf = max(0.0, min(100.0, conf))
    status = "verified" if conf >= VERIFIED_CONFIDENCE else "pending"
    ent = _one(conn.execute(
        """SELECT e.id, e.season_id FROM season_entries e JOIN seasons s ON s.id=e.season_id
           WHERE e.object_row_id=? AND e.status='approved' ORDER BY s.starts DESC, e.id DESC LIMIT 1""",
        (object_row_id,)))
    rep = json.dumps(report, ensure_ascii=False) if report else None
    existing = _one(conn.execute(
        "SELECT id, status, confidence FROM season_claims WHERE object_row_id=? AND collector_uid=?",
        (object_row_id, collector_uid)))
    if existing:
        if existing["status"] in CLAIM_OK or status != "verified":
            raise ValueError("You've already claimed this piece")
        # a better scan upgrades a pending claim
        conn.execute("""UPDATE season_claims SET confidence=?, report_json=COALESCE(?, report_json),
                        location=COALESCE(NULLIF(?, ''), location), status='verified',
                        season_id=COALESCE(season_id, ?), entry_id=COALESCE(entry_id, ?)
                        WHERE id=?""",
                     (conf, rep, (location or "")[:200], ent["season_id"] if ent else None,
                      ent["id"] if ent else None, existing["id"]))
        cid = existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO season_claims (season_id,entry_id,object_row_id,collector_uid,collector_name,
               confidence,report_json,location,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ent["season_id"] if ent else None, ent["id"] if ent else None, object_row_id,
             collector_uid, (collector_name or "")[:200], conf, rep, (location or "")[:200],
             status, _now()))
        cid = int(cur.lastrowid)
    conn.commit()
    badges = award_badges(conn, collector_uid) if status == "verified" else []
    row = get_claim(conn, cid) or {"id": cid, "status": status}
    row["badges"] = badges
    row["badge_details"] = [{"badge": b, **BADGES.get(b, {})} for b in badges]
    # tell the room — the maker gets the heads-up, pending ones say who can confirm
    title = row.get("title") or obj.get("title") or "Untitled"
    who = row.get("collector_name") or "A collector"
    _nudge((f"🎃 {who} claimed “{title}” — verified by scan" if status == "verified"
            else f"🎃 Claim on “{title}” needs a look (low-confidence scan)"),
           {"Maker": row.get("maker") or row.get("artist_name") or obj.get("maker"),
            "Studio": row.get("studio"), "Collector": who, "Confidence": f"{conf:.0f}/100",
            "Where": location, "Season": row.get("season_title"),
            "Confirm": ("The maker in Glowtbook → Objects → Seasons, or a reviewer at /review.html"
                        if status == "pending" else None),
            "Object": object_row_id},
           color=0x16A34A if status == "verified" else 0xEA580C)
    return row


def set_claim_status(conn, claim_id: int, status: str) -> None:
    if status not in CLAIM_STATUSES:
        raise ValueError(f"status must be one of {CLAIM_STATUSES}")
    c = _one(conn.execute("SELECT collector_uid FROM season_claims WHERE id=?", (int(claim_id),)))
    if not c:
        raise ValueError("No such claim")
    conn.execute("UPDATE season_claims SET status=? WHERE id=?", (status, int(claim_id)))
    conn.commit()
    if status in CLAIM_OK:
        award_badges(conn, c["collector_uid"])


def pending_claims(conn) -> list[dict]:
    if not _has_table(conn, "objects"):
        return []
    rows = _rows(conn.execute(_CLAIM_SQL + " WHERE c.status='pending' ORDER BY c.created_at, c.id"))
    return [_claim_shape(c) for c in rows]


# --- the maker's side of a claim ------------------------------------------------------
_PENDING_FIRST = "CASE c.status WHEN 'pending' THEN 0 ELSE 1 END, c.created_at DESC, c.id DESC"


def claims_on(conn, object_row_id: str) -> list[dict]:
    """Every claim on one public piece — pending first, then newest first. What
    the piece's maker sees in Glowtbook."""
    if not object_row_id or not _has_table(conn, "objects"):
        return []
    rows = _rows(conn.execute(_CLAIM_SQL + f" WHERE c.object_row_id=? ORDER BY {_PENDING_FIRST}",
                              (object_row_id,)))
    return [_claim_shape(c) for c in rows]


def claims_on_my_pieces(conn, uid: str) -> list[dict]:
    """Every claim on the pieces `uid` published (pending first, newest first):
    the maker's inbox behind Glowtbook's Seasons tab and GET /api/me/claims."""
    if not uid or not _has_table(conn, "objects"):
        return []
    hs = sorted(_my_hashes(conn, uid))
    out: list[dict] = []
    for i in range(0, len(hs), 400):          # keep well under SQLite's bind limit
        chunk = hs[i:i + 400]
        q = ",".join("?" for _ in chunk)
        out += _rows(conn.execute(_CLAIM_SQL + f" WHERE o.content_hash IN ({q})", chunk))
    out.sort(key=lambda c: (c.get("created_at") or "", c["id"]), reverse=True)
    out.sort(key=lambda c: c.get("status") != "pending")          # stable: pending first
    return [_claim_shape(c) for c in out]


def claim_counts(conn, row_ids) -> dict[str, dict]:
    """{object_row_id: {"ok": verified+approved, "pending": waiting}} for the
    given public rows — the Glowtbook dashboard column."""
    ids = sorted({r for r in (row_ids or []) if r})
    out = {rid: {"ok": 0, "pending": 0} for rid in ids}
    if not ids or not _has_table(conn, "season_claims"):
        return out
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" for _ in chunk)
        for rid, status, n in conn.execute(
                f"SELECT object_row_id, status, COUNT(*) FROM season_claims "
                f"WHERE object_row_id IN ({q}) GROUP BY object_row_id, status", chunk).fetchall():
            if status in CLAIM_OK:
                out[rid]["ok"] += int(n)
            elif status == "pending":
                out[rid]["pending"] += int(n)
    return out


def confirm_claim(conn, claim_id: int, uid: str, ok: bool = True) -> dict:
    """The maker's verdict on a *pending* claim against one of their pieces:
    confirm ("they bought it") or reject. Only the person who published the
    piece may do this — admins use set_claim_status. Returns the updated claim."""
    c = get_claim(conn, claim_id)
    if not c:
        raise ValueError("No such claim")
    if not owns_object(conn, uid, c["object_row_id"]):
        raise ValueError("Only the person who published this piece can confirm claims on it")
    if c["status"] != "pending":
        raise ValueError("That claim has already been decided")
    set_claim_status(conn, int(claim_id), "approved" if ok else "rejected")
    return get_claim(conn, claim_id) or {**c, "status": "approved" if ok else "rejected"}


def collection(conn, uid: str) -> list[dict]:
    """Everything this person has claimed (verified first, newest first), joined
    to the object, its entry/studio and season."""
    if not uid or not _has_table(conn, "objects"):
        return []
    rows = _rows(conn.execute(
        _CLAIM_SQL + """ WHERE c.collector_uid=? AND c.status<>'rejected'
        ORDER BY c.created_at DESC, c.id DESC""", (uid,)))
    return [_claim_shape(c) for c in rows]


# --- boards ---------------------------------------------------------------------------
def leaderboard(conn, season_id: int) -> dict:
    rows = entries(conn, season_id, "approved")
    # each board gets its own copies: rank is per board
    peoples = [dict(e) for e in sorted(rows, key=lambda e: (-e["vote_count"],
                                                           e["created_at"] or "", e["id"]))]
    collected = [dict(e) for e in sorted(rows, key=lambda e: (-e["claim_count"], -e["vote_count"],
                                                             e["created_at"] or "", e["id"]))]
    # studios: grouped by name regardless of case and spacing (the most-used spelling is
    # shown), with the distinct makers behind each; Independent (no shop given) is listed
    # last and never wins a studio prize
    studios: dict[str, dict] = {}
    spellings: dict[str, dict[str, int]] = {}
    for e in rows:
        raw = re.sub(r"\s+", " ", (e.get("studio") or "")).strip()
        key = _studio_key(raw) or "independent"
        s = studios.setdefault(key, {"studio": raw or "Independent", "entries": 0, "claims": 0, "votes": 0,
                                     "registered": 0, "makers": 0, "independent": not raw, "_makers": set()})
        if raw:
            sp = spellings.setdefault(key, {})
            sp[raw] = sp.get(raw, 0) + 1
        s["entries"] += 1; s["claims"] += e["claim_count"]; s["votes"] += e["vote_count"]
        s["registered"] += int(bool(e.get("fingerprinted")))
        s["_makers"].add(e.get("artist_uid") or f"name:{e.get('artist_name') or e['id']}")
    for key, s in studios.items():
        s["makers"] = len(s.pop("_makers"))
        if key in spellings:
            s["studio"] = max(spellings[key].items(), key=lambda kv: kv[1])[0]
    board_studios = sorted(studios.values(),
                           key=lambda s: (s["independent"], -s["makers"], -s["entries"], -s["claims"],
                                          -s["votes"], s["studio"].lower()))
    # makers: who entered the most registered pieces (then pieces, then what came of them)
    makers: dict[str, dict] = {}
    for e in rows:
        mk = e.get("artist_uid") or f"name:{e.get('artist_name') or e['id']}"
        m = makers.setdefault(mk, {"artist_uid": e.get("artist_uid") or "",
                                   "artist_name": e.get("artist_name") or "A maker", "studio": "",
                                   "entries": 0, "registered": 0, "claims": 0, "votes": 0, "_studios": {}})
        m["entries"] += 1; m["registered"] += int(bool(e.get("fingerprinted")))
        m["claims"] += e["claim_count"]; m["votes"] += e["vote_count"]
        st = re.sub(r"\s+", " ", (e.get("studio") or "")).strip()
        if st:
            m["_studios"][st] = m["_studios"].get(st, 0) + 1
    for m in makers.values():
        sts = m.pop("_studios")
        m["studio"] = max(sts.items(), key=lambda kv: kv[1])[0] if sts else ""
    board_makers = sorted(makers.values(),
                          key=lambda m: (-m["registered"], -m["entries"], -m["claims"], -m["votes"],
                                         m["artist_name"].lower()))
    for board in (peoples, collected):
        for i, e in enumerate(board, 1):
            e["rank"] = i
    for board in (board_studios, board_makers):
        for i, s in enumerate(board, 1):
            s["rank"] = i
    # points: what each collector holds from this season, scored by rarity
    by_id = {e["id"]: e for e in rows}
    holders: dict[str, dict] = {}
    for c in _rows(conn.execute(
            """SELECT collector_uid, collector_name, entry_id FROM season_claims
               WHERE season_id=? AND status IN ('verified','approved') AND entry_id IS NOT NULL""",
            (int(season_id),))):
        e = by_id.get(int(c["entry_id"]))
        if not e:
            continue
        h = holders.setdefault(c["collector_uid"], {"collector_uid": c["collector_uid"],
                                                    "collector_name": c.get("collector_name") or "A collector",
                                                    "points": 0, "pieces": 0, "legendaries": 0,
                                                    "_studios": set()})
        h["points"] += e["points"]; h["pieces"] += 1
        h["legendaries"] += int(e["rarity"] == "legendary")
        if _studio_key(e.get("studio") or ""):
            h["_studios"].add(_studio_key(e["studio"]))
    for h in holders.values():
        h["studios"] = len(h.pop("_studios"))
    board_points = sorted(holders.values(),
                          key=lambda h: (-h["points"], -h["pieces"], h["collector_name"]))
    for i, h in enumerate(board_points, 1):
        h["rank"] = i
    return {"peoples_choice": peoples, "most_collected": collected, "studios": board_studios,
            "makers": board_makers, "points": board_points}


def collectors(conn) -> list[dict]:
    """The collectors' board across every season — verified/approved claims only."""
    rows = _rows(conn.execute("""
        SELECT c.collector_uid, MAX(c.collector_name) AS collector_name,
               COUNT(DISTINCT c.object_row_id) AS pieces,
               COUNT(DISTINCT CASE WHEN COALESCE(e.studio,'')<>'' THEN e.studio END) AS studios,
               COUNT(DISTINCT c.season_id) AS seasons,
               (SELECT COUNT(*) FROM season_badges b WHERE b.uid=c.collector_uid) AS badges,
               MIN(c.created_at) AS since
        FROM season_claims c LEFT JOIN season_entries e ON e.id=c.entry_id
        WHERE c.status IN ('verified','approved')
        GROUP BY c.collector_uid
        ORDER BY pieces DESC, studios DESC, badges DESC, since"""))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["collector_name"] = r.get("collector_name") or "A collector"
    return rows


# --- prizes: standings off the boards ------------------------------------------------------
def _piece_standing(e: dict, value, unit: str) -> dict:
    return {"name": e.get("title") or "Untitled",
            "sub": " · ".join(x for x in (e.get("artist_name"), e.get("studio")) if x),
            "value": value, "unit": unit, "entry_id": e["id"]}


def prize_board(conn, season_id: int, awarded_for: str, lb: dict | None = None,
                award_list: list[dict] | None = None) -> list[dict]:
    """Who is in line for a prize awarded for `awarded_for`, best first: a list of
    {name, sub, value, unit, entry_id?} with no account ids in it. Empty for a judges'
    pick, and until the board behind it has anything to say."""
    k = awarded_for or ""
    lb = lb if lb is not None else leaderboard(conn, season_id)
    if k.startswith("award:"):
        slug = k[6:]
        for c in (award_list if award_list is not None else awards(conn, season_id)):
            if c["slug"] != slug:
                continue
            measured = c["kind"] == "measured"
            return [{"name": s["title"],
                     "sub": " · ".join(x for x in (s.get("artist_name"), s.get("studio")) if x),
                     "value": s.get("height_mm") if measured else s.get("votes"),
                     "unit": "mm" if measured else "picks", "entry_id": s["entry_id"]}
                    for s in c["standings"]]
        return []
    if k == "peoples_choice":
        return [_piece_standing(e, e["vote_count"], "votes") for e in lb["peoples_choice"]
                if e["vote_count"] > 0]
    if k == "most_collected":
        return [_piece_standing(e, e["claim_count"], "collections") for e in lb["most_collected"]
                if e["claim_count"] > 0]
    if k == "first_in":
        first = sorted(lb["peoples_choice"], key=lambda e: (e.get("created_at") or "", e["id"]))
        return [_piece_standing(e, (e.get("created_at") or "")[:10], "") for e in first[:PRIZE_TOP]]
    metric = PRIZE_METRIC.get(k)
    if not metric:
        return []                                    # a judges' pick, or a kind we don't know
    unit = PRIZE_FOR[k]["unit"]
    if k.startswith("studio_"):
        pool = [s for s in lb["studios"] if not s.get("independent") and s[metric] > 0]
        pool.sort(key=lambda s: (-s[metric], -s["entries"], -s["claims"], -s["votes"], s["studio"].lower()))
        return [{"name": s["studio"],
                 "sub": f"{s['makers']} maker{'s' if s['makers'] != 1 else ''} · "
                        f"{s['entries']} piece{'s' if s['entries'] != 1 else ''}",
                 "value": s[metric], "unit": unit} for s in pool]
    if k.startswith("maker_") or k == "rookie":
        pool = list(lb["makers"])
        if k == "rookie":
            veterans = {r[0] for r in conn.execute(
                "SELECT DISTINCT artist_uid FROM season_entries WHERE season_id<>? AND status='approved' "
                "AND COALESCE(artist_uid,'')<>''", (int(season_id),)).fetchall()}
            pool = [m for m in pool if m.get("artist_uid") and m["artist_uid"] not in veterans]
        pool = [m for m in pool if m[metric] > 0]
        pool.sort(key=lambda m: (-m[metric], -m["registered"], -m["entries"], -m["votes"],
                                 m["artist_name"].lower()))
        return [{"name": m["artist_name"], "sub": m.get("studio") or "Independent",
                 "value": m[metric], "unit": unit} for m in pool]
    pool = [h for h in lb["points"] if h[metric] > 0]
    pool.sort(key=lambda h: (-h[metric], -h["points"], -h["pieces"], h["collector_name"].lower()))
    return [{"name": h["collector_name"],
             "sub": f"{h['pieces']} piece{'s' if h['pieces'] != 1 else ''}",
             "value": h[metric], "unit": unit} for h in pool]


def prize_standings(conn, season_id: int, lb: dict | None = None,
                    award_list: list[dict] | None = None, top: int = PRIZE_TOP) -> list[dict]:
    """Every prize of the season with its top standings, its leader, and — once an
    admin has named one or the season has closed — its winner."""
    rows = prizes(conn, season_id)
    if not rows:
        return []
    season = get_season(conn, int(season_id)) or {}
    closed = season.get("status") == "closed"
    lb = lb if lb is not None else leaderboard(conn, season_id)
    if award_list is None and any(p["awarded_for"].startswith("award:") for p in rows):
        award_list = awards(conn, season_id)
    for p in rows:
        st = prize_board(conn, season_id, p["awarded_for"], lb, award_list)
        p["standings"] = st[:top]
        p["leader"] = st[0] if st else None
        p["winner"] = p.get("winner_text") or (st[0]["name"] if closed and st else "")
        p["settled"] = bool(p["winner"])
    return rows


def _winner_ref(conn, season_id: int, prize: dict, lead: dict, lb: dict) -> dict:
    """Who the leader is, as data: the maker's account and studio behind a piece or a maker
    standing, the studio's name, the collector's account. The career record reads this."""
    who = prize.get("who") or "piece"
    ref: dict = {"who": who, "name": lead.get("name") or "", "value": lead.get("value"), "unit": lead.get("unit") or ""}
    if lead.get("entry_id"):
        e = _one(conn.execute("SELECT artist_uid, artist_name, studio FROM season_entries WHERE id=?",
                              (int(lead["entry_id"]),)))
        if e:
            ref.update({"entry_id": int(lead["entry_id"]), "artist_uid": e.get("artist_uid") or "",
                        "artist_name": e.get("artist_name") or "", "studio": e.get("studio") or ""})
    elif who == "maker":
        m = next((m for m in lb.get("makers", []) if m.get("artist_name") == lead.get("name")), None)
        if m:
            ref.update({"artist_uid": m.get("artist_uid") or "", "artist_name": m.get("artist_name") or "",
                        "studio": m.get("studio") or ""})
    elif who == "studio":
        ref["studio"] = lead.get("name") or ""
    elif who == "collector":
        h = next((h for h in lb.get("points", []) if h.get("collector_name") == lead.get("name")), None)
        if h:
            ref["collector_uid"] = h.get("collector_uid") or ""
    return ref


def settle_prizes(conn, season_id: int) -> list[dict]:
    """At close-out: write each prize's leader into winner_text (and who they are into
    winner_json) unless an admin already named a winner. Returns what was written."""
    out = []
    lb = leaderboard(conn, season_id)
    for p in prize_standings(conn, season_id, lb=lb):
        if p.get("winner_text") or not p.get("leader"):
            continue
        lead = p["leader"]
        text = lead["name"]
        if lead.get("value") not in (None, ""):
            unit = lead.get("unit") or ""
            if lead["value"] == 1 and unit.endswith("s") and unit != "mm":
                unit = unit[:-1]
            text += f" · {lead['value']} {unit}".rstrip()
        ref = _winner_ref(conn, season_id, p, lead, lb)
        conn.execute("UPDATE season_prizes SET winner_text=?, winner_json=?, updated_at=? WHERE id=?",
                     (text[:300], json.dumps(ref, ensure_ascii=False), _now(), p["id"]))
        out.append({"prize_id": p["id"], "title": p["title"], "winner": text, "ref": ref})
    conn.commit()
    return out


def prizes_won(conn, *, uid: str = "", name: str = "", studio: str = "") -> list[dict]:
    """Prizes settled to this maker (by account, else by name) or to this studio, across
    every collecting event: {prize_id, title, season_id, season_title, season_slug, who, value, unit}."""
    if not _has_table(conn, "season_prizes") or not (uid or name or studio):
        return []
    have = {r[1] for r in conn.execute("PRAGMA table_info(season_prizes)").fetchall()}
    if "winner_json" not in have:
        return []
    rows = _rows(conn.execute(
        """SELECT p.id, p.title, p.awarded_for, p.winner_json, p.season_id, s.title AS season_title, s.slug AS season_slug
           FROM season_prizes p JOIN seasons s ON s.id=p.season_id WHERE COALESCE(p.winner_json,'')<>''
           ORDER BY s.starts DESC, p.sort, p.id"""))
    want_name, want_studio = _studio_key(name), _studio_key(studio)
    out = []
    for r in rows:
        try:
            ref = json.loads(r["winner_json"] or "{}")
        except ValueError:
            continue
        hit = ((uid and ref.get("artist_uid") == uid)
               or (want_name and _studio_key(ref.get("artist_name") or "") == want_name)
               or (want_name and ref.get("who") == "maker" and _studio_key(ref.get("name") or "") == want_name)
               or (want_studio and ref.get("who") == "studio" and _studio_key(ref.get("name") or "") == want_studio))
        if hit:
            out.append({"prize_id": r["id"], "title": r["title"], "awarded_for": r["awarded_for"],
                        "season_id": r["season_id"], "season_title": r["season_title"],
                        "season_slug": r["season_slug"], "who": ref.get("who"), "value": ref.get("value"),
                        "unit": ref.get("unit") or ""})
    return out


# --- badges ------------------------------------------------------------------------------
def _grant(conn, uid: str, badge: str, season_id: int | None = None) -> bool:
    """Award once. Returns True if it was newly awarded."""
    if not uid or badge not in BADGES:
        return False
    cur = conn.execute("INSERT OR IGNORE INTO season_badges (uid, badge, season_id, awarded_at) "
                       "VALUES (?,?,?,?)", (uid, badge, int(season_id or GLOBAL), _now()))
    conn.commit()
    return cur.rowcount > 0


def badges_for(conn, uid: str) -> list[dict]:
    if not uid:
        return []
    rows = _rows(conn.execute(
        """SELECT b.badge, b.season_id, b.awarded_at, s.title AS season_title, s.slug AS season_slug
           FROM season_badges b LEFT JOIN seasons s ON s.id=b.season_id
           WHERE b.uid=? ORDER BY b.awarded_at, b.id""", (uid,)))
    for r in rows:
        meta = BADGES.get(r["badge"], {})
        r["label"] = meta.get("label", r["badge"]); r["blurb"] = meta.get("blurb", "")
    return rows


def award_badges(conn, uid: str) -> list[str]:
    """Compute every badge `uid` has earned and award the missing ones. Returns
    the keys newly awarded (Crowd Favourite is handed out by close_season)."""
    if not uid:
        return []
    new: list[str] = []
    claims = _rows(conn.execute(
        """SELECT c.season_id, c.object_row_id, COALESCE(e.studio,'') AS studio,
                  COALESCE(e.legendary,0) AS legendary
           FROM season_claims c LEFT JOIN season_entries e ON e.id=c.entry_id
           WHERE c.collector_uid=? AND c.status IN ('verified','approved')""", (uid,)))
    if claims and _grant(conn, uid, "first-scan"):
        new.append("first-scan")
    if any(int(c["legendary"] or 0) for c in claims) and _grant(conn, uid, "legend_hunter"):
        new.append("legend_hunter")
    if len({c["studio"] for c in claims if c["studio"]}) >= 5 and _grant(conn, uid, "five-shops"):
        new.append("five-shops")
    if (len({c["season_id"] for c in claims if c["season_id"]}) >= 3
            and _grant(conn, uid, "three-seasons")):
        new.append("three-seasons")
    debut = conn.execute("SELECT 1 FROM season_entries WHERE artist_uid=? AND status='approved' LIMIT 1",
                         (uid,)).fetchone()
    if debut and _grant(conn, uid, "maker-debut"):
        new.append("maker-debut")
    for sid in sorted({c["season_id"] for c in claims if c["season_id"]}):
        shops = {r[0] for r in conn.execute(
            "SELECT DISTINCT studio FROM season_entries WHERE season_id=? AND status='approved' "
            "AND COALESCE(studio,'')<>''", (sid,)).fetchall()}
        mine = {c["studio"] for c in claims if c["season_id"] == sid and c["studio"]}
        if len(shops) >= 2 and shops <= mine and _grant(conn, uid, "set-complete", sid):
            new.append("set-complete")
    return new


# --- admin -------------------------------------------------------------------------------
def summary(conn) -> dict:
    def n(sql, *p):
        try:
            return int(conn.execute(sql, p).fetchone()[0])
        except Exception:  # noqa: BLE001
            return 0
    return {"seasons": n("SELECT COUNT(*) FROM seasons"),
            "live": n("SELECT COUNT(*) FROM seasons WHERE status IN ('open','voting')"),
            "entries": n("SELECT COUNT(*) FROM season_entries WHERE status='approved'"),
            "pending_entries": n("SELECT COUNT(*) FROM season_entries WHERE status='pending'"),
            "votes": n("SELECT COUNT(*) FROM season_votes"),
            "claims": n("SELECT COUNT(*) FROM season_claims WHERE status IN ('verified','approved')"),
            "pending_claims": n("SELECT COUNT(*) FROM season_claims WHERE status='pending'"),
            "badges": n("SELECT COUNT(*) FROM season_badges"),
            "award_votes": n("SELECT COUNT(*) FROM season_award_votes"),
            "prizes": n("SELECT COUNT(*) FROM season_prizes"),
            "legendaries_out": n(
                "SELECT COUNT(*) FROM season_entries e WHERE e.status='approved' AND e.legendary=1 "
                "AND NOT EXISTS (SELECT 1 FROM season_claims c WHERE c.object_row_id=e.object_row_id "
                "AND c.status IN ('verified','approved'))")}
