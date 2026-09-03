"""
central.opportunities
=====================
Artist opportunities — open calls, residencies, grants, shows, deadlines. A
public intake writes a *pending* row (the same publication gate as everything
else, so nothing shows until an admin approves it in ✅ Approvals), and approved
rows drive a calendar: a subscribable/downloadable **.ics** feed and per-item
**Add to Google Calendar** links, plus a display list.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote

from central import approvals

TABLE = "opportunities"
TYPES = ["Open call", "Residency", "Grant / funding", "Exhibition",
         "Competition", "Workshop", "Job", "Other"]

# (column, label, is_public)
COLUMNS = [
    ("title", "Title", 1), ("organization", "Organization", 1),
    ("opp_type", "Type", 1), ("url", "Link", 1), ("location", "Location", 1),
    ("deadline", "Deadline", 1), ("event_start", "Starts", 1),
    ("event_end", "Ends", 1), ("fee", "Fee", 1), ("eligibility", "Eligibility", 1),
    ("description", "Description", 1),
    ("contact_email", "Contact", 0), ("submitted_by", "Submitted by", 0),
]


def ensure_opportunities(conn) -> None:
    approvals.ensure_approvals(conn)
    cols = ", ".join(f'"{c}" TEXT' for c, _, _ in COLUMNS)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{TABLE}" ('
                 "_row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, "
                 f"_imported_at TEXT, {cols})")
    # A table created by an earlier version may be missing columns — CREATE TABLE
    # IF NOT EXISTS won't add them, so ALTER any that are absent.
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{TABLE}")')}
    for c, _, _ in COLUMNS:
        if c not in have:
            conn.execute(f'ALTER TABLE "{TABLE}" ADD COLUMN "{c}" TEXT')
    now = datetime.now(timezone.utc).isoformat()
    n = conn.execute(f'SELECT COUNT(*) FROM "{TABLE}"').fetchone()[0]
    conn.execute("""INSERT OR REPLACE INTO _datasets
        (tbl,domain,source_file,source_sheet,visibility,row_count,description,updated_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (TABLE, "opportunities", "intake", "form", "public", n,
         "Artist opportunities — open calls, residencies, grants, shows", now))
    for i, (c, label, pub) in enumerate(COLUMNS):
        conn.execute("""INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public)
                        VALUES (?,?,?,?,?)""", (TABLE, c, label, i, pub))
    conn.commit()


def submit(conn, fields: dict) -> str:
    """Insert a pending opportunity from the public intake form. Returns row id."""
    ensure_opportunities(conn)
    now = datetime.now(timezone.utc).isoformat()
    rid = hashlib.sha1(("opp|" + (fields.get("title", "") + fields.get("url", "")
                                  + fields.get("deadline", "") + now)).encode()).hexdigest()[:16]
    names = [c for c, _, _ in COLUMNS]
    allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *names]
    vals = [rid, "intake", "form", now, *[(fields.get(c) or "").strip() for c in names]]
    conn.execute(f'INSERT INTO "{TABLE}" ({", ".join(chr(34)+c+chr(34) for c in allc)}) '
                 f'VALUES ({", ".join("?" for _ in allc)})', vals)
    conn.commit()
    return rid   # pending: no _approvals row yet


def approved(conn) -> list[dict]:
    """Approved opportunities, public columns only, soonest deadline first."""
    pub = [c for c, _, p in COLUMNS if p]
    collist = ", ".join(f'"{c}"' for c in ["_row_id", *pub])
    rows = conn.execute(
        f'SELECT {collist} FROM "{TABLE}" WHERE {approvals.approved_subquery()} '
        'ORDER BY CASE WHEN deadline="" THEN 1 ELSE 0 END, deadline', (TABLE,)).fetchall()
    return [dict(r) for r in rows]


# --- calendar ------------------------------------------------------------
def _esc(v: str) -> str:
    return (str(v or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    out, s = [], line
    while len(s.encode()) > 73:
        cut = 72
        while len(s[:cut].encode()) > 73:
            cut -= 1
        out.append(s[:cut]); s = " " + s[cut:]
    out.append(s)
    return "\r\n".join(out)


def _date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m%d")
        except Exception:
            continue
    return None


def _plus_day(yyyymmdd: str) -> str:
    from datetime import timedelta
    d = datetime.strptime(yyyymmdd, "%Y%m%d")
    return (d + timedelta(days=1)).strftime("%Y%m%d")


def build_ics(rows: list[dict], base_url: str = "https://glassdatabase.org") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0",
           "PRODID:-//Glass Database//Opportunities//EN",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
           "X-WR-CALNAME:Glass Database — opportunities",
           "X-WR-CALDESC:Open calls, residencies, grants and shows"]
    for r in rows:
        d = _date(r.get("deadline"))
        if not d:
            continue
        uid = f'{r.get("_row_id","")}@glassdatabase.org'
        title = r.get("title") or "Opportunity"
        typ = r.get("opp_type") or ""
        org = r.get("organization") or ""
        summary = " — ".join(x for x in [f"{typ}: {title}".strip(": "), org] if x)
        desc_bits = [b for b in [r.get("description"), r.get("eligibility"),
                                 (f"Fee: {r['fee']}" if r.get("fee") else ""), r.get("url")] if b]
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{_plus_day(d)}",
                _fold(f"SUMMARY:Deadline — {_esc(summary)}"),
                _fold(f"DESCRIPTION:{_esc(chr(10).join(desc_bits))}")]
        if r.get("url"):
            out.append(_fold(f"URL:{_esc(r['url'])}"))
        if r.get("location"):
            out.append(_fold(f"LOCATION:{_esc(r['location'])}"))
        out += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def gcal_link(row: dict) -> str | None:
    """A one-click 'Add to Google Calendar' link for this opportunity's deadline."""
    d = _date(row.get("deadline"))
    if not d:
        return None
    title = f'Deadline — {row.get("opp_type","")}: {row.get("title","Opportunity")}'.replace(": ", ": ", 1)
    details = "\n".join(x for x in [row.get("description"), row.get("url")] if x)
    q = {"action": "TEMPLATE", "text": title.strip(),
         "dates": f"{d}/{_plus_day(d)}", "details": details or "",
         "location": row.get("location") or ""}
    return "https://calendar.google.com/calendar/render?" + "&".join(
        f"{k}={quote(str(v))}" for k, v in q.items())


def days_until(deadline: str) -> int | None:
    d = _date(deadline)
    if not d:
        return None
    return (datetime.strptime(d, "%Y%m%d").date() - datetime.now(timezone.utc).date()).days
