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


def submit(conn, fields: dict, base_url: str = "") -> str:
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
    try:
        from central import analytics
        analytics.log(conn, "opportunities", "", "submit:opportunity")
    except Exception:
        pass
    from central import notify
    notify.notify_submission("opportunity", fields.get("title") or "Opportunity",
                             {"Type": fields.get("opp_type"), "Org": fields.get("organization"),
                              "Deadline": fields.get("deadline"), "Link": fields.get("url")},
                             TABLE, rid, base_url)
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


def initial_month(rows: list[dict]):
    """The month to open the calendar on: the nearest upcoming deadline, else now."""
    import datetime as _dt
    today = _dt.date.today()
    ds = sorted(d for d in (_date(r.get("deadline")) for r in rows) if d)
    upcoming = [d for d in ds if _dt.datetime.strptime(d, "%Y%m%d").date() >= today]
    pick = upcoming[0] if upcoming else (ds[-1] if ds else today.strftime("%Y%m%d"))
    dt = _dt.datetime.strptime(pick, "%Y%m%d").date() if isinstance(pick, str) else pick
    return dt.year, dt.month


_CAL_CSS = """
<style>
.gdb-cal{border-collapse:collapse;width:100%;table-layout:fixed;font-size:.9rem;
  font-family:'Inter',system-ui,sans-serif;color:#1F1A16}
.gdb-cal caption{font-family:'Fraunces',Georgia,serif;font-weight:350;font-size:1.35rem;
  text-align:left;margin-bottom:.5rem;color:#1F1A16}
.gdb-cal th{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:.68rem;text-transform:uppercase;
  letter-spacing:.08em;color:#6B6157;font-weight:500;padding:.3rem .2rem;text-align:center;
  border-bottom:1px solid #1F1A16}
.gdb-cal td{vertical-align:top;height:88px;border:1px solid #CFC2AE;border-radius:0;
  padding:.3rem;background:#F4EEE4;overflow:hidden}
.gdb-cal td.out{background:#EDE4D6;color:#CFC2AE}
.gdb-cal td.today{border:1px solid #B3261E;box-shadow:inset 0 0 0 1px #B3261E}
.gdb-cal td.past .dn{color:#CFC2AE}
.gdb-cal .dn{display:block;font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:.72rem;
  color:#6B6157;text-align:right;margin-bottom:2px}
.gdb-cal td.today .dn{color:#B3261E;font-weight:600}
.gdb-cal .chip{display:block;text-decoration:none;font-size:.74rem;line-height:1.25;
  background:#B3261E;color:#F4EEE4;font-weight:500;
  border-radius:0;padding:2px 6px;margin-top:3px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.gdb-cal .chip:hover{background:#8E1D17;color:#F4EEE4}
.gdb-cal .chip.res{background:transparent;color:#1F1A16;border:1px solid #1F1A16}
.gdb-cal .chip.res:hover{background:#1F1A16;color:#F4EEE4}
</style>
"""


def month_grid_html(rows: list[dict], year: int, month: int) -> str:
    """An accessible month calendar with each opportunity on its deadline day."""
    import calendar as _cal
    import datetime as _dt
    import html as _html
    from collections import defaultdict
    byday = defaultdict(list)
    for r in rows:
        d = _date(r.get("deadline"))
        if d:
            byday[_dt.datetime.strptime(d, "%Y%m%d").date()].append(r)
    today = _dt.date.today()
    dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(year, month)
    out = [_CAL_CSS, '<table class="gdb-cal">',
           f'<caption>{_cal.month_name[month]} {year}</caption>',
           "<thead><tr>" + "".join(f'<th scope="col">{d}</th>' for d in dow) + "</tr></thead><tbody>"]
    for wk in weeks:
        out.append("<tr>")
        for day in wk:
            cls = "day"
            if day.month != month:
                cls += " out"
            if day == today:
                cls += " today"
            elif day < today:
                cls += " past"
            chips = ""
            for r in byday.get(day, []):
                url = r.get("url") or gcal_link(r) or "#"
                title = r.get("title") or "Opportunity"
                typ = (r.get("opp_type") or "").lower()
                chip_cls = "chip res" if ("resid" in typ or "grant" in typ) else "chip"
                chips += (f'<a class="{chip_cls}" href="{_html.escape(url, True)}" '
                          f'title="{_html.escape(title)} — deadline {day.isoformat()}">'
                          f'{_html.escape(title)}</a>')
            out.append(f'<td class="{cls}"><span class="dn">{day.day}</span>{chips}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)
