"""
explore.cards
=============
The Directory as cards instead of a table: pure, UI-free helpers the explorer renders
with ``st.html``. A card is one row of a public table (an artist, a studio, an
opportunity) with its name, what kind of thing it is and where, up to four of its public
columns, and the ways on: its record page (``/record/gdb:<table>:<row id>``), its maker
card, its website, the map. ``facet_columns`` picks the two or three columns worth a
filter; ``matches`` is the search box. Kept out of app.py so it imports without
Streamlit and can be unit-tested.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

import pandas as pd

from explore.dataclean import is_dob, is_excluded, is_person_name

TITLE_COLS = ("artist_name", "name", "title", "display_name", "studio_name", "organization", "label")
PLACE_COLS = ("based_in", "city", "location", "region", "country", "hometown")
# the columns a card shows first when it has them, in this order
PREFERRED = ("based_in", "city", "region", "country", "primary_technique", "techniques", "technique", "type",
             "opp_type", "organization", "founded", "deadline", "event_start", "location", "medium",
             "email_address", "phone")
LINK_COLS = ("website", "url", "web", "link")      # shown as the Website button, not as a field
# the columns worth a filter, in order of preference
FACET_PREFERRED = ("country", "region", "state", "type", "opp_type", "primary_technique", "technique", "city",
                   "based_in", "domain", "category", "status")
KIND_WORDS = {"artists": "artist", "studios": "studio", "opportunities": "opportunity", "objects": "piece",
              "events": "event", "people": "person", "makers": "maker"}
URL_RE = re.compile(r"^(https?://|www\.)", re.I)
LONG = 150
PAGE = 12
PER_ROW = 3

CSS = """<style>
.gdb-card{border:2px solid var(--ink,#1F1A16);border-radius:14px;padding:.55rem;background:var(--paper-2,#EDE4D6);
  margin:0 0 1.2rem;box-shadow:0 10px 30px rgba(31,26,22,.08)}
.gdb-card .in{background:var(--paper,#F4EEE4);border:1px solid var(--rule,#CFC2AE);border-radius:9px;
  padding:.9rem .9rem .8rem;min-height:230px;display:flex;flex-direction:column}
.gdb-card h3{font-family:var(--serif,Georgia,serif)!important;font-weight:350!important;font-size:1.3rem!important;
  line-height:1.15;margin:0 0 .35rem;color:var(--ink,#1F1A16)!important;overflow-wrap:anywhere}
.gdb-card .kind{font:500 10px/1.5 var(--mono,monospace);letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted,#6B6157);margin:0 0 .6rem;border-bottom:1px solid var(--rule,#CFC2AE);padding-bottom:.5rem}
.gdb-card dl{margin:0;padding:0}
.gdb-card dl div{display:grid;grid-template-columns:5.4rem 1fr;gap:.5rem;padding:.35rem 0;
  border-bottom:1px solid var(--rule,#CFC2AE);font-size:13px;align-items:baseline}
.gdb-card dt{font:500 10px/1.6 var(--mono,monospace);letter-spacing:.1em;text-transform:uppercase;color:var(--muted,#6B6157)}
.gdb-card dd{margin:0;color:var(--ink,#1F1A16);overflow-wrap:anywhere}
.gdb-card dd a{color:var(--ink,#1F1A16)}
.gdb-card .acts{display:flex;gap:.45rem;flex-wrap:wrap;margin-top:auto;padding-top:.8rem}
.gdb-card .acts a{font:500 10px/1 var(--mono,monospace);letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink,#1F1A16)!important;border:1px solid var(--ink,#1F1A16);padding:.45rem .6rem;text-decoration:none}
.gdb-card .acts a:hover{background:var(--ink,#1F1A16);color:var(--paper,#F4EEE4)!important}
.gdb-card .acts a.hot{border-color:var(--red,#B3261E);color:var(--red,#B3261E)!important}
.gdb-card .acts a.hot:hover{background:var(--red,#B3261E);color:#fff!important}
</style>"""


def _s(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def title_of(row: dict) -> str:
    for c in TITLE_COLS:
        if _s(row.get(c)):
            return _s(row[c])
    for c, v in row.items():
        if not str(c).startswith("_") and _s(v) and not is_excluded(str(c)) and not URL_RE.match(_s(v)):
            return _s(v)
    return "Untitled"


def kind_word(table: str) -> str:
    t = (table or "").lower()
    return KIND_WORDS.get(t, t[:-1] if t.endswith("s") else t or "record")


def place_of(row: dict) -> str:
    parts = []
    for c in PLACE_COLS:
        v = _s(row.get(c))
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts[:2])


def pick_fields(row: dict, labels: dict, hidden=(), limit: int = 4) -> list[tuple[str, str, str]]:
    """Up to `limit` (column, label, value) pairs worth showing: preferred columns first, then
    the rest in table order; never the title, the hidden columns, ids or coordinates."""
    title_col = next((c for c in TITLE_COLS if _s(row.get(c))), None)
    hidden = set(hidden)
    order = [c for c in PREFERRED if c in row] + [c for c in row if c not in PREFERRED]
    out, seen = [], set()
    for c in order:
        c = str(c)
        if c in seen or c == title_col or c in hidden or c.startswith("_") or is_excluded(c) or is_dob(c):
            continue
        if c in LINK_COLS and URL_RE.match(_s(row.get(c))):
            continue
        v = _s(row.get(c))
        if not v:
            continue
        seen.add(c)
        if len(v) > LONG:
            v = v[:LONG].rsplit(" ", 1)[0] + "…"
        out.append((c, labels.get(c, c.replace("_", " ")), v))
        if len(out) >= limit:
            break
    return out


def links_of(row: dict, table: str, base: str = "") -> list[tuple[str, str, bool]]:
    """(label, href, hot) for the card's buttons: the record page first, then the maker
    card for a person, the map for a studio, the website when the row has one."""
    rid = _s(row.get("_row_id"))
    out: list[tuple[str, str, bool]] = []
    if rid:
        out.append(("Record", f"{base}/record/gdb:{table}:{rid}", True))
        if (table or "").lower() in ("artists", "people", "makers"):
            out.append(("Maker card", f"{base}/maker.html?m={quote(f'gdb:{table}:{rid}', safe='')}", False))
    name = title_of(row)
    if (table or "").lower() == "studios" and name != "Untitled":
        out.append(("Map", f"{base}/studios.html?q={quote(name)}", False))
    for c in ("website", "url", "web", "link"):
        v = _s(row.get(c))
        if v and URL_RE.match(v):
            out.append(("Website", v if v.lower().startswith("http") else "https://" + v, False))
            break
    return out


def card_html(row: dict, table: str, labels: dict, hidden=(), base: str = "") -> str:
    """One row as a card. Values are escaped; URL-looking values become links."""
    name = title_of(row)
    kind = kind_word(table)
    place = place_of(row)
    head = html.escape(kind) + (f" · {html.escape(place)}" if place else "")
    fields = []
    for c, label, v in pick_fields(row, labels, hidden):
        shown = (f'<a href="{html.escape(v if v.lower().startswith("http") else "https://" + v, quote=True)}" '
                 f'rel="noopener" target="_blank">{html.escape(v)}</a>' if URL_RE.match(v) else html.escape(v))
        fields.append(f"<div><dt>{html.escape(label)}</dt><dd>{shown}</dd></div>")
    acts = "".join(f'<a href="{html.escape(h, quote=True)}" target="_self"{" class=hot" if hot else ""}>'
                   f"{html.escape(label)}</a>" for label, h, hot in links_of(row, table, base))
    return (f'<article class="gdb-card"><div class="in"><h3>{html.escape(name)}</h3><p class="kind">{head}</p>'
            f"<dl>{''.join(fields)}</dl>" + (f'<div class="acts">{acts}</div>' if acts else "") + "</div></article>")


def facet_columns(df: pd.DataFrame, cats, max_facets: int = 3, max_values: int = 40) -> list[str]:
    """The categorical columns worth a dropdown: preferred names first, never a person's
    name, and only columns with two to `max_values` distinct values."""
    ok = []
    for c in cats:
        if is_person_name(c) or is_dob(c) or is_excluded(c):
            continue
        try:
            n = df[c].replace("", pd.NA).dropna().nunique()
        except Exception:  # noqa: BLE001
            continue
        if 2 <= n <= max_values:
            ok.append(c)
    pref = [c for c in FACET_PREFERRED if c in ok]
    rest = [c for c in ok if c not in pref]
    return (pref + rest)[:max_facets]


def matches(df: pd.DataFrame, q: str) -> pd.Series:
    """A row matches when any text column contains the query, case-insensitively."""
    q = (q or "").strip().lower()
    if not q:
        return pd.Series(True, index=df.index)
    mask = pd.Series(False, index=df.index)
    for c in df.columns:
        if str(c).startswith("_"):
            continue
        try:
            mask |= df[c].astype(str).str.lower().str.contains(q, regex=False, na=False)
        except Exception:  # noqa: BLE001
            continue
    return mask
