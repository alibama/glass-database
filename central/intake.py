"""
central.intake
==============
Config-driven public intake "sheets" for growing the database. Each form writes a
**pending** row through the publication gate (nothing shows until an admin
approves it) and fires a Discord notification with one-click approve/reject
links. Adding a content type is one entry in FORMS.

Fields are dicts. `kind` ∈ text | textarea | url | email | date | select |
multiselect | section. `options` is a list, or a source token: "@techniques",
"@primary_focus", "@ethnicity", "@status" (resolved from central.techniques).
A `section` field renders a heading only (no column, no data).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from central import approvals, notify, techniques

MULTI_SEP = " | "


def _f(name, label, kind="text", required=False, private=False, options=None, help=None):
    return {"name": name, "label": label, "kind": kind, "required": required,
            "private": private, "options": options, "help": help}


def _sec(label):
    return {"name": None, "label": label, "kind": "section"}


def options_for(field: dict) -> list[str]:
    o = field.get("options")
    if o == "@techniques":
        return techniques.LABELS
    if o == "@primary_focus":
        return techniques.PRIMARY_FOCUS
    if o == "@ethnicity":
        return techniques.ETHNICITY
    if o == "@status":
        return techniques.STATUS
    return list(o or [])


FORMS = {
    "artist": {
        "title": "Artist", "table": "artist_submissions", "domain": "artists",
        "desc": "Community-submitted artists working with glass (pending review)",
        "intro": ("Add your (or a friend's) glass credentials to the directory. Be honest — "
                  "submissions are vetted before they appear. You must be an artist, "
                  "craftsperson, hobbyist, or designer working with glass."),
        "fields": [
            _sec("About you"),
            _f("artist_name", "Artist name", required=True),
            _f("email", "Email", "email", required=True, private=True),
            _f("nationality_base", "Nationality / base (where born & where you live now)", required=True),
            _f("website", "Website or social (Instagram, TikTok, …)", "url"),
            _sec("Details"),
            _f("status", "Status", "select", required=True, options="@status"),
            _f("birth_year", "Birth year"),
            _f("death_year", "Year of death (leave blank if living)"),
            _f("ethnicity", "Ethnicity (optional)", "select", options="@ethnicity"),
            _f("gender", "Gender (optional)"),
            _sec("Practice"),
            _f("primary_focus", "Primary discipline (50%+ of your work)", "select",
               required=True, options="@primary_focus"),
            _f("tech_primary", "Primary techniques", "multiselect", options="@techniques"),
            _f("tech_secondary", "Secondary techniques", "multiselect", options="@techniques"),
            _f("tech_occasional", "Occasional techniques", "multiselect", options="@techniques"),
            _f("training_education", "Training / education (where you studied, degrees, classes, "
               "teachers, books/channels you learn from)", "textarea", required=True),
            _f("inspiration", "Inspiration (who or what keeps you going)", "textarea"),
            _sec("Mentorship"),
            _f("studied_under", "Who did you study or work under? Anyone you'd call a mentor?",
               "textarea", help="Name people — these become links once the artist is in the directory."),
            _f("work_experience", "Where have you gained experience / worked?", "textarea"),
            _sec("Recognition"),
            _f("notable_collections", "Notable collections (museums, collectors)", "textarea"),
            _f("awards", "Awards / honors", "textarea"),
            _f("career_highlights", "Career highlights — what you're most proud of", "textarea"),
            _f("submitted_by", "Your name or email (if submitting for someone else)",
               private=True),
        ],
    },
    "studio": {
        "title": "Studio", "table": "studio_submissions", "domain": "studios",
        "desc": "Community-submitted studios (pending review)",
        "intro": "Add a glass studio, hot shop, or access space to the directory.",
        "fields": [
            _f("name", "Studio name", required=True),
            _f("city", "City"), _f("region", "State / region"), _f("country", "Country"),
            _f("studio_type", "Type", "select",
               options=["Hot shop", "Flameworking", "Kiln / fusing", "Cold shop", "Mixed", "Other"]),
            _f("website", "Website", "url"),
            _f("access", "Public access / rentals / classes?"),
            _f("description", "Description", "textarea"),
            _f("email", "Contact email", "email", private=True),
            _f("submitted_by", "Your name or email", private=True),
        ],
    },
    "resource": {
        "title": "Resource", "table": "resource_submissions", "domain": "resources",
        "desc": "Community-submitted glass resources (pending review)",
        "intro": "Share a glass resource — a supplier, service, equipment source, or class.",
        "fields": [
            _f("category", "Category", "select", required=True,
               options=["Materials / supplies", "Equipment", "Services (repair, coldwork, …)",
                        "Education / classes", "Studio rental / access", "Community / org", "Other"]),
            _f("name", "Name", required=True),
            _f("location", "Location", required=True),
            _f("website", "Website / link", "url", required=True),
            _f("offer", "What you offer", "textarea", required=True),
            _f("email", "Contact email", "email", required=True, private=True),
            _f("submitted_by", "Your name (if different)", private=True),
        ],
    },
    "exchange": {
        "title": "Exchange listing", "table": "exchange_submissions", "domain": "exchange",
        "desc": "Community exchange — want-to-buy / want-to-sell / trade (pending review)",
        "intro": "Buy, sell, or trade glass, tools, and materials with the community.",
        "fields": [
            _f("listing_type", "Listing", "select", required=True,
               options=["Want to sell (WTS)", "Want to buy (WTB)", "Want to trade (WTT)"]),
            _f("title", "Title", required=True),
            _f("description", "Description", "textarea", required=True),
            _f("price", "Asking price / offer"),
            _f("location", "Location"),
            # Seed for a future community-exchange/trade mechanism — a togglable flag.
            _f("will_accept_trade", "Open to trade / partial trade", "checkbox"),
            _f("trade_notes", "What would you trade for? (optional)", "textarea"),
            _f("website", "Photos / link", "url"),
            _f("email", "Contact email", "email", required=True, private=True),
            _f("submitted_by", "Your name", private=True),
        ],
    },
    "job": {
        "title": "Job", "table": "job_submissions", "domain": "jobs",
        "desc": "Community job board — glass positions & gigs (pending review)",
        "intro": "Post a glass job, apprenticeship, or gig.",
        "fields": [
            _f("title", "Position title", required=True),
            _f("organization", "Studio / employer", required=True),
            _f("location", "Location (or Remote)"),
            _f("job_type", "Type", "select",
               options=["Full-time", "Part-time", "Apprenticeship", "Gig / contract",
                        "Seasonal", "Internship", "Other"]),
            _f("compensation", "Compensation (optional)"),
            _f("description", "Description", "textarea", required=True),
            _f("how_to_apply", "How to apply", "textarea"),
            _f("url", "Link", "url"),
            _f("email", "Contact email", "email", private=True),
            _f("submitted_by", "Your name", private=True),
        ],
    },
}


def form(key: str) -> dict:
    return FORMS[key]


def data_fields(key: str) -> list[dict]:
    return [f for f in FORMS[key]["fields"] if f["kind"] != "section"]


def ensure(conn, key: str) -> None:
    f = FORMS[key]
    tbl = f["table"]
    approvals.ensure_approvals(conn)
    dfs = data_fields(key)
    cols = ", ".join(f'"{fd["name"]}" TEXT' for fd in dfs)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{tbl}" (_row_id TEXT PRIMARY KEY, '
                 f'_source_file TEXT, _source_sheet TEXT, _imported_at TEXT, {cols})')
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")')}
    for fd in dfs:
        if fd["name"] not in have:
            conn.execute(f'ALTER TABLE "{tbl}" ADD COLUMN "{fd["name"]}" TEXT')
    now = datetime.now(timezone.utc).isoformat()
    n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
    conn.execute("""INSERT OR REPLACE INTO _datasets
        (tbl,domain,source_file,source_sheet,visibility,row_count,description,updated_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (tbl, f["domain"], "intake", "form", "public", n, f["desc"], now))
    for i, fd in enumerate(dfs):
        conn.execute("""INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public)
                        VALUES (?,?,?,?,?)""",
                     (tbl, fd["name"], fd["label"], i, 0 if fd["private"] else 1))
    conn.commit()


def _flatten(field: dict, value):
    if field["kind"] == "multiselect":
        return MULTI_SEP.join(value or [])
    if field["kind"] == "checkbox":
        return "yes" if value else ""
    return (value or "").strip() if isinstance(value, str) else (str(value) if value else "")


def submit(conn, key: str, values: dict, base_url: str = "") -> str:
    """Write a pending submission and fire the Discord notification. Returns row id."""
    f = FORMS[key]
    ensure(conn, key)
    dfs = data_fields(key)
    now = datetime.now(timezone.utc).isoformat()
    seed = key + str(values.get(dfs[0]["name"], "")) + now
    rid = hashlib.sha1(seed.encode()).hexdigest()[:16]
    names = [fd["name"] for fd in dfs]
    allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *names]
    row = [rid, "intake", "form", now, *[_flatten(fd, values.get(fd["name"])) for fd in dfs]]
    conn.execute(f'INSERT INTO "{f["table"]}" ({", ".join(chr(34)+c+chr(34) for c in allc)}) '
                 f'VALUES ({", ".join("?" for _ in allc)})', row)
    conn.commit()   # pending: no _approvals row yet

    public = {fd["label"]: _flatten(fd, values.get(fd["name"]))
              for fd in dfs if not fd["private"]}
    title = str(values.get(dfs[0]["name"]) or f["title"]).strip()
    notify.notify_submission(f["title"], title,
                             {k: v for k, v in public.items() if v}, f["table"], rid, base_url)
    return rid
