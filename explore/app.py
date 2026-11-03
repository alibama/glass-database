"""
Glass Database — data explorer (public, read-only, interactive)
===============================================================
Lets anyone "play with" the larger tables: pick a dataset, break it down by any
column, see distributions, filter, map studios, and download what they've
filtered. Reads the central DB through the shared connection factory and shows
PUBLIC datasets/columns only (private fields never reach this surface).

Run:  streamlit run explore/app.py  --server.baseUrlPath explore
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals  # noqa: E402
from central.dbconn import connect  # noqa: E402

PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://glassdatabase.org").rstrip("/")


@st.cache_data(ttl=600, show_spinner=False)
def _read_creds_cached(image_b64: str):
    """C2PA parsing is expensive; cache it per image so it isn't redone every rerun."""
    import base64 as _b

    from glowtbook import c2pa_sign
    return c2pa_sign.read_credentials(_b.b64decode(image_b64))


def _render_intake(key):
    from central import intake
    f = intake.form(key)
    if f.get("intro"):
        st.write(f["intro"])
    with st.form(f"intake_{key}", clear_on_submit=True):
        vals = {}
        for fd in f["fields"]:
            kind = fd["kind"]
            if kind == "section":
                st.markdown(f"##### {fd['label']}")
                continue
            lbl = fd["label"] + (" *" if fd["required"] else "") + \
                ("  🔒 kept private" if fd["private"] else "")
            help_ = fd.get("help")
            name = fd["name"]
            if kind == "textarea":
                vals[name] = st.text_area(lbl, help=help_)
            elif kind == "select":
                # button pills instead of a dropdown (single choice)
                if help_:
                    st.caption(f"**{lbl}**  \n{help_}")
                else:
                    st.caption(f"**{lbl}**")
                vals[name] = st.pills(lbl, intake.options_for(fd), selection_mode="single",
                                      label_visibility="collapsed", key=f"pill_{key}_{name}") or ""
            elif kind == "multiselect":
                if help_:
                    st.caption(f"**{lbl}**  \n{help_}")
                else:
                    st.caption(f"**{lbl}**")
                vals[name] = st.pills(lbl, intake.options_for(fd), selection_mode="multi",
                                      label_visibility="collapsed", key=f"pill_{key}_{name}") or []
            elif kind == "checkbox":
                vals[name] = st.checkbox(fd["label"], help=help_)
            elif kind == "date":
                d = st.date_input(lbl, value=None, format="YYYY-MM-DD", help=help_)
                vals[name] = str(d) if d else ""
            else:
                vals[name] = st.text_input(lbl, help=help_)
        submitted = st.form_submit_button("Submit for review", type="primary")
        st.caption("An admin reads it first; it appears once approved.")
        if submitted:
            missing = [fd["label"] for fd in intake.data_fields(key)
                       if fd["required"] and not (vals.get(fd["name"]) if isinstance(vals.get(fd["name"]), list)
                                                  else (vals.get(fd["name"]) or "").strip())]
            if missing:
                st.error("Required: " + ", ".join(missing))
            else:
                intake.submit(_conn(), key, vals, PUBLIC_BASE)
                st.success("Thanks — submitted for review. It'll appear once an admin approves it.")


def _render_opp_list(rows, _opp):
    for r in rows:
        du = _opp.days_until(r.get("deadline"))
        when = ""
        if du is not None:
            when = f"deadline {r['deadline']} · " + (f"{du} days left" if du >= 0 else "closed")
        st.markdown(f"### {r['title']}")
        meta = " · ".join(x for x in [r.get("opp_type"), r.get("organization"),
                                      r.get("location"), when] if x)
        if meta:
            st.caption(meta)
        if r.get("fee"):
            st.caption(f"Fee: {r['fee']}")
        if r.get("description"):
            st.write(r["description"])
        links = []
        if r.get("url"):
            links.append(f"[Details ↗]({r['url']})")
        g = _opp.gcal_link(r)
        if g:
            links.append(f"[Add to Google Calendar]({g})")
        if links:
            st.markdown(" · ".join(links))
        st.divider()

st.set_page_config(page_title="Find Artists & Studios · Glass Database", page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

from brand import apply_theme  # noqa: E402

apply_theme("explore")


@st.cache_resource
def _conn():
    c = connect()
    approvals.ensure_approvals(c)
    return c


def registry() -> list[dict]:
    # not cached: it's a tiny query, and caching it made newly-approved datasets
    # appear only after the cache expired.
    rows = _conn().execute(
        "SELECT d.tbl, d.domain, d.description, "
        "  (SELECT COUNT(*) FROM _approvals a WHERE a.tbl=d.tbl AND a.status='approved') AS row_count "
        "FROM _datasets d WHERE d.visibility='public' ORDER BY d.domain, d.tbl"
    ).fetchall()
    # only surface datasets that actually have approved (published) rows
    return [dict(r) for r in rows if r["row_count"] > 0]


@st.cache_data(ttl=300)
def public_columns(tbl: str) -> list[tuple[str, str]]:
    rows = _conn().execute(
        "SELECT column, label FROM _columns WHERE tbl=? AND is_public=1 ORDER BY ordinal", (tbl,)
    ).fetchall()
    return [(r["column"], r["label"]) for r in rows]


@st.cache_data(ttl=300)
def load(tbl: str, cols: tuple[str, ...]) -> pd.DataFrame:
    collist = ", ".join(f'"{c}"' for c in cols)
    return pd.read_sql_query(
        f'SELECT {collist} FROM "{tbl}" WHERE {approvals.approved_subquery()}',
        _conn(), params=[tbl])



from explore.cards import CSS as _CARD_CSS  # noqa: E402
from explore.cards import PAGE, PER_ROW, TITLE_COLS, card_html, facet_columns, matches  # noqa: E402
from explore.dataclean import (  # noqa: E402,F401
    GEO_COLS,
    classify,
    clean_label,
    clean_numeric,
    is_demographic,
    is_excluded,
    is_person_name,
    person_identifying,
)
from explore.pieces import filter_pieces, makers_of  # noqa: E402


def _param(name: str) -> str:
    """A query-string value (deep links from the wiki, the record pages and the graph)."""
    try:
        return (st.query_params.get(name) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


@st.cache_data(ttl=300)
def table_with(q: str, tables: tuple[str, ...]) -> str:
    """The first public table with an approved row whose name contains `q`: where a
    ``?q=`` link with no table named should open."""
    like = f"%{(q or '').strip().lower()}%"
    if like == "%%":
        return ""
    for t in tables:
        cols = [c for c, _ in public_columns(t) if c in TITLE_COLS or is_person_name(c)]
        if not cols:
            continue
        where = " OR ".join(f'lower("{c}") LIKE ?' for c in cols)
        try:
            hit = _conn().execute(
                f'SELECT 1 FROM "{t}" WHERE ({where}) AND {approvals.approved_subquery()} LIMIT 1',
                [*([like] * len(cols)), t]).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if hit:
            return t
    return ""

# --- sidebar: dataset picker ----------------------------------------------
reg = registry()
st.sidebar.title("Find Artists & Studios")
st.sidebar.caption("Who makes glass, where, and how to reach them.")
if st.sidebar.button("↻ Refresh data"):
    st.cache_data.clear(); st.rerun()
st.sidebar.caption("Reloads the tables from the database.")

# The view keys are what `?view=` deep links use and what the code branches on; the
# labels and captions are what people read (docs/NAMING.md). Keys never change.
_VIEW_KEYS = ["datasets", "objects", "opportunities", "community", "submit"]
_VIEW_LABELS = {
    "datasets": "The Directory",
    "objects": "Pieces",
    "opportunities": "Jobs, Calls & Classes",
    "community": "Community boards",
    "submit": "Add yourself or your studio",
}
_VIEW_CAPTIONS = {
    "datasets": "Artists, studios and more: browse, filter, export.",
    "objects": "Registered pieces, their stories and who made them.",
    "opportunities": "Open calls, residencies, grants and shows, on a calendar.",
    "community": "Buy, sell and trade · jobs · suppliers and services.",
    "submit": "Everything is reviewed before it appears.",
}
_qp = _param("view").lower()
_vdefault = _VIEW_KEYS.index(_qp) if _qp in _VIEW_KEYS else 0
mode = st.sidebar.radio("View", _VIEW_KEYS, index=_vdefault, label_visibility="collapsed",
                        format_func=lambda k: _VIEW_LABELS[k],
                        captions=[_VIEW_CAPTIONS[k] for k in _VIEW_KEYS])
st.sidebar.link_button("Who Worked With Whom", "/graph.html", use_container_width=True)
st.sidebar.caption("Who taught whom, who shares a studio, what touches what.")
from brand import track as _track

_track("explore", mode)

with st.sidebar.expander("💬 Send feedback"):
    with st.form("site_feedback", clear_on_submit=True):
        fb_msg = st.text_area("What's on your mind?", label_visibility="collapsed",
                              placeholder="Bug, idea, correction…")
        fb_email = st.text_input("Email (optional, if you'd like a reply)")
        fb_sent = st.form_submit_button("Send")
        st.caption("Goes to the team; your email is only used to reply.")
        if fb_sent and fb_msg.strip():
            from central import feedback as _fb
            _fb.submit(_conn(), fb_msg, fb_email, page=mode)
            st.success("Thanks — sent to the team.")

# ===========================================================================
# SUBMIT — public intake sheets (artist / studio / event) → pending + Discord
# ===========================================================================
if mode == "submit":
    st.title("Add yourself or your studio")
    st.caption("Everything is reviewed before it appears publicly. Contact details stay private.")
    labels = {"Artist": "artist", "Studio": "studio", "Event / exhibition": "event",
              "Lineage (who taught whom)": "lineage",
              "Resource (supplier, service, class)": "resource",
              "Exchange (buy / sell / trade)": "exchange", "Job / gig": "job",
              "Shop (document your studio)": "shop"}
    st.markdown("**What are you submitting?**")
    pick = st.pills("What are you submitting?", list(labels), selection_mode="single",
                    default="Artist", label_visibility="collapsed", key="submit_kind") or "Artist"
    _render_intake(labels[pick])
    st.caption("Open calls, residencies, or grants with a deadline go under **Jobs, Calls & Classes** "
               "so they land on the calendar.")
    st.stop()

# ===========================================================================
# COMMUNITY — exchange (buy/sell/trade), jobs, and resources
# ===========================================================================
if mode == "community":
    from central import approvals as _appr
    from central import intake as _intake
    st.title("Community boards")
    st.caption("Buy, sell and trade; gaffer and assistant openings; suppliers, services and classes.")
    tab = st.radio("Board", ["Exchange", "Jobs", "Resources"], horizontal=True,
                   label_visibility="collapsed")
    key = {"Exchange": "exchange", "Jobs": "job", "Resources": "resource"}[tab]
    _intake.ensure(_conn(), key)
    tbl = _intake.form(key)["table"]
    pub = [f["name"] for f in _intake.data_fields(key) if not f["private"]]
    collist = ", ".join(f'"{c}"' for c in ["_row_id", *pub])
    rows = _conn().execute(
        f'SELECT {collist} FROM "{tbl}" WHERE {_appr.approved_subquery()} '
        'ORDER BY _imported_at DESC', (tbl,)).fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        st.info(f"No {tab.lower()} listings yet — post one under **Add yourself or your studio**.")
    for r in rows:
        if key == "exchange":
            badge = r.get("listing_type", "")
            trade = " · 🤝 open to trade" if r.get("will_accept_trade") else ""
            st.markdown(f"### {r.get('title', '')}")
            st.caption(" · ".join(x for x in [badge, r.get("price"), r.get("location")] if x) + trade)
            if r.get("description"):
                st.write(r["description"])
            if r.get("trade_notes"):
                st.caption("Would trade for: " + r["trade_notes"])
            if r.get("website"):
                st.markdown(f"[Photos / link ↗]({r['website']})")
        elif key == "job":
            st.markdown(f"### {r.get('title', '')}")
            st.caption(" · ".join(x for x in [r.get("organization"), r.get("job_type"),
                                              r.get("location"), r.get("compensation")] if x))
            if r.get("description"):
                st.write(r["description"])
            if r.get("how_to_apply"):
                st.caption("Apply: " + r["how_to_apply"])
            if r.get("url"):
                st.markdown(f"[Details ↗]({r['url']})")
        else:  # resource
            st.markdown(f"### {r.get('name', '')}")
            st.caption(" · ".join(x for x in [r.get("category"), r.get("location")] if x))
            if r.get("offer"):
                st.write(r["offer"])
            if r.get("website"):
                st.markdown(f"[Visit ↗]({r['website']})")
        st.divider()
    st.stop()

# ===========================================================================
# OPPORTUNITIES — calendar (display + .ics + Google Calendar) and public intake
# ===========================================================================
if mode == "opportunities":
    from central import opportunities as _opp
    _opp.ensure_opportunities(_conn())
    st.title("Jobs, Calls & Classes")
    st.caption("Open calls, residencies, grants, teaching and shows. Subscribe once and new "
               "approved listings appear in your calendar automatically.")
    rows = _opp.approved(_conn())

    ics = _opp.build_ics(rows, PUBLIC_BASE)
    ics_url = f"{PUBLIC_BASE}/api/opportunities.ics"
    webcal = ics_url.replace("https://", "webcal://").replace("http://", "webcal://")
    c1, c2 = st.columns([1, 2])
    c1.download_button("⬇ Download calendar (.ics)", ics, file_name="glass-opportunities.ics",
                       mime="text/calendar", width="stretch")
    c1.caption("Saves a calendar file you can open in any calendar app.")
    c2.markdown(f"**Subscribe:** [add to Apple/Outlook calendar]({webcal}) · in Google Calendar, "
                f"*Other calendars → From URL* → `{ics_url}`")
    st.divider()

    if not rows:
        st.info("Nothing listed yet — be the first to post one below.")
    else:
        view = st.radio("Show", ["Calendar", "List"], horizontal=True, label_visibility="collapsed")
        if view == "Calendar":
            import datetime as _dt
            if "opp_ym" not in st.session_state:
                st.session_state.opp_ym = list(_opp.initial_month(rows))
            y, m = st.session_state.opp_ym
            nav1, nav2, nav3, nav4 = st.columns([1, 1, 4, 1])
            if nav1.button("◀", help="Previous month"):
                st.session_state.opp_ym = [y - 1, 12] if m == 1 else [y, m - 1]; st.rerun()
            if nav2.button("Today"):
                st.session_state.opp_ym = [_dt.date.today().year, _dt.date.today().month]; st.rerun()
            if nav4.button("▶", help="Next month"):
                st.session_state.opp_ym = [y + 1, 1] if m == 12 else [y, m + 1]; st.rerun()
            st.caption("◀ and ▶ move a month at a time; Today jumps back to this month.")
            st.html(_opp.month_grid_html(rows, y, m))
            st.caption("Amber = deadlines · violet = residencies/grants. Tap an entry for details.")
        else:
            _render_opp_list(rows, _opp)

    with st.expander("➕ Post a job, call or class  (reviewed before it appears)"):
        with st.form("opp_intake", clear_on_submit=True):
            title = st.text_input("Title *")
            a, b = st.columns(2)
            org = a.text_input("Organization")
            typ = b.selectbox("Type", _opp.TYPES)
            url = st.text_input("Link (URL)")
            loc, dl = st.columns(2)
            location = loc.text_input("Location (or ‘Online’)")
            deadline = dl.date_input("Application deadline *", value=None, format="YYYY-MM-DD")
            f1, f2 = st.columns(2)
            fee = f1.text_input("Entry fee (if any)")
            elig = f2.text_input("Eligibility")
            desc = st.text_area("Description")
            contact = st.text_input("Contact email  🔒 kept private")
            by = st.text_input("Your name or email  🔒 kept private")
            opp_sent = st.form_submit_button("Submit for review", type="primary")
            st.caption("An admin reads it first; it appears here and on the calendar once approved.")
            if opp_sent:
                if not title.strip() or deadline is None:
                    st.error("Title and application deadline are required.")
                else:
                    _opp.submit(_conn(), {"title": title, "organization": org, "opp_type": typ,
                                          "url": url, "location": location, "deadline": str(deadline),
                                          "fee": fee, "eligibility": elig, "description": desc,
                                          "contact_email": contact, "submitted_by": by}, PUBLIC_BASE)
                    st.success("Thanks — submitted for review. It'll appear here once an admin "
                               "approves it.")
    st.stop()

# ===========================================================================
# OBJECTS — render contributed objects with images + provenance timeline
# ===========================================================================
if mode == "objects":
    import json as _json

    from explore.objects_a11y import build_objects_html
    st.title("Pieces")
    st.caption("Registered pieces with their stories: who made each one and where it has been.")
    try:
        rows = [dict(r) for r in _conn().execute("SELECT * FROM objects ORDER BY published_at DESC").fetchall()]
    except Exception:  # noqa: BLE001
        rows = []

    # Find a piece: a search box and a maker filter. ?maker=, ?q= and ?piece= deep-link here
    # (the record pages, the graph and the wiki); the cards are built for the page shown only.
    _makers = makers_of(rows)
    _m0, _p0 = _param("maker"), _param("piece")
    _c1, _c2 = st.columns([2, 1])
    pq = _c1.text_input("Search pieces", value=_param("q"), placeholder="A title, a maker, a technique…",
                        label_visibility="collapsed", key="pieces_q")
    _mi = next((i + 1 for i, m in enumerate(_makers) if m.lower() == _m0.lower()), 0) if _m0 else 0
    pm = _c2.selectbox("Maker", ["Any maker", *_makers], index=_mi, label_visibility="collapsed", key="pieces_maker")
    pm = "" if pm == "Any maker" else pm
    hits = filter_pieces(rows, q=pq, maker=pm, piece=_p0)
    _narrowed = bool(pq.strip() or pm or _p0)
    if _p0:
        st.caption("One piece, by its link. [See every piece](/explore/?view=objects)")
    st.session_state.setdefault("pieces_shown", PAGE)
    _pshown = int(st.session_state["pieces_shown"])
    page_rows = hits[:_pshown]

    objects = []
    for o in page_rows:
        imgs = _conn().execute(
            "SELECT role, caption, image_b64 FROM object_images WHERE object_row_id=? ORDER BY "
            "CASE role WHEN 'primary' THEN 0 ELSE 1 END, id", (o["_row_id"],)).fetchall()
        images = [(im["role"], im["caption"], im["image_b64"]) for im in imgs]
        try:
            man = _json.loads(o["manifest_json"] or "{}")
            events = next((a["data"] for a in man.get("assertions", [])
                           if a["label"] in ("org.glassdatabase.provenance.events",
                                              "glassdb.provenance.events")), [])
        except Exception:
            man, events = {}, []
        creds = None
        if o.get("has_credentials") and images:
            try:
                creds = _read_creds_cached(images[0][2])
            except Exception:
                creds = None
        has_video = any(r == "video-poster" for r, _, _ in images)
        objects.append({
            "id": o["_row_id"], "title": o["title"], "maker": o["maker"], "year": o["year"],
            "techniques": o["techniques"], "materials": o["materials"], "dimensions": o["dimensions"],
            "description": o["description"], "contributor": o["contributor"], "sourcing": o["sourcing"],
            "value_display": o["value_display"], "content_hash": o["content_hash"],
            "has_credentials": bool(o.get("has_credentials")),
            "manifest_json": _json.dumps(man, ensure_ascii=False) if man else "",
            "images": images, "events": events, "creds": creds,
            "video_url": f"{PUBLIC_BASE}/api/objects/{o['_row_id']}/video" if has_video else None,
            "fingerprint": man.get("fingerprint"),
        })

    st.html(build_objects_html(objects, verify_base=PUBLIC_BASE, total=len(hits), filtered=_narrowed))
    if len(hits) > _pshown:
        if st.button(f"Show {min(PAGE, len(hits) - _pshown)} more", key="pieces_more"):
            st.session_state["pieces_shown"] = _pshown + PAGE
            st.rerun()
    st.stop()


# ===========================================================================
# DATASETS (default)
# ===========================================================================
if not reg:
    # nothing has been approved yet — explain, and show whether data exists but is pending
    c = _conn()
    pub = c.execute("SELECT COUNT(*) FROM _datasets WHERE visibility='public'").fetchone()[0]
    try:
        appr = c.execute("SELECT COUNT(*) FROM _approvals WHERE status='approved'").fetchone()[0]
    except Exception:
        appr = 0
    st.title("Find artists & studios")
    st.info("No published datasets yet. Everything stays private until it's approved.")
    st.markdown("**To publish:** open the admin console → **✅ Approvals** → "
                "**“Approve ALL pending content”** (or approve per dataset), then hit "
                "**↻ Refresh data** here.")
    st.caption(f"{pub} public dataset(s) configured · {appr} row(s) approved in this database so far. "
               "If that says 0 after you approved, the admin and this app may be pointing at "
               "different database files (check GLASSDB_PATH).")
    st.stop()

by_domain: dict[str, list[dict]] = {}
for d in reg:
    by_domain.setdefault(d["domain"], []).append(d)
# Deep links: ?table= picks the table; ?q= fills the search and, with no table named, opens
# the first table that has a row by that name (the wiki's older item pages, the record pages
# and the graph all link here with a name).
_q0, _t0 = _param("q"), _param("table").lower()
if _q0 and _t0 not in {d["tbl"] for d in reg}:
    _t0 = table_with(_q0, tuple(d["tbl"] for d in reg))
_domains = sorted(by_domain)
_d0 = next((d["domain"] for d in reg if d["tbl"] == _t0), None)
domain = st.sidebar.selectbox("Category", _domains, index=_domains.index(_d0) if _d0 in _domains else 0)
_dsets = by_domain[domain]
ds = st.sidebar.selectbox(
    "Dataset", _dsets, index=next((i for i, d in enumerate(_dsets) if d["tbl"] == _t0), 0),
    format_func=lambda d: f"{d['tbl']} ({d['row_count']})",
)
tbl = ds["tbl"]

# --- header metrics --------------------------------------------------------
total_rows = sum(d["row_count"] for d in reg)
st.title("Find artists & studios")
st.caption("Who makes glass, where, and how to reach them. Pick a table on the left, filter it, "
           "take it with you.")
m1, m2, m3 = st.columns(3)
m1.metric("Public datasets", len(reg))
m2.metric("Total public rows", f"{total_rows:,}")
m3.metric("This dataset", f"{ds['row_count']:,} rows")
st.caption(ds["description"])

cols = public_columns(tbl)
labels = {c: clean_label(c, lbl) for c, lbl in cols}      # nouns to filter by, not the form's questions
try:
    df = load(tbl, ("_row_id", *(c for c, _ in cols))).copy()      # the row id links each card to its record page
    cats, nums = classify(df)
except Exception as ex:  # noqa: BLE001
    st.error(f"Couldn't load this dataset: {ex}")
    st.stop()

# Per-row privacy: dates of birth and demographics (ethnicity, gender, age) never appear per
# row (cards, table, filters, download); they may still be counted in the aggregate charts.
# Names appear on the cards, because every row now has a public record page and a wiki item
# of its own under the same name; the table and the download keep the older rule and leave
# names out.
_name_cols, _dob_cols = person_identifying(c for c, _ in cols)
_demo_cols = [c for c, lbl in cols if is_demographic(c, lbl) and c not in _dob_cols]
_counted_only = [c for c, _ in cols if c in _dob_cols or c in _demo_cols]
_hidden_per_row = set(_name_cols) | set(_counted_only)
cats = [c for c in cats if c not in _name_cols]
if _counted_only:
    st.caption("🔒 " + ", ".join(labels.get(c, c) for c in _counted_only)
               + ": only ever counted in the charts, never shown for a person.")

# --- the Directory as cards: a search box, a few filters, a few rows at a time ---------
st.html(_CARD_CSS)
q = st.text_input("Search", value=_q0, placeholder="A name, a town, a technique…", label_visibility="collapsed",
                  key=f"q_{tbl}")
facets = facet_columns(df, cats, labels=labels)
chosen: dict[str, str] = {}
if facets:
    fcols = st.columns(len(facets))
    for i, fc in enumerate(facets):
        vals = sorted({str(v) for v in df[fc].replace("", pd.NA).dropna().unique()})
        chosen[fc] = fcols[i].selectbox(labels.get(fc, fc), ["Any"] + vals, key=f"facet_{tbl}_{fc}")
view = df
for fc, val in chosen.items():
    if val and val != "Any":
        view = view[view[fc].astype(str) == val]
if q.strip():
    view = view[matches(view, q)]
filtered = bool(q.strip()) or any(v != "Any" for v in chosen.values())
st.caption(f"{len(view):,} of {len(df):,} " + ("match" if filtered else "in this table") + ".")

_shown_key = f"shown_{tbl}"
st.session_state.setdefault(_shown_key, PAGE)
_shown = int(st.session_state[_shown_key])
_rows = view.head(_shown).to_dict("records")
if not _rows:
    st.info("Nothing matches. Clear the search or a filter, or add the listing yourself from "
            "“Add yourself or your studio” in the sidebar.")
for _start in range(0, len(_rows), PER_ROW):
    _cs = st.columns(PER_ROW)
    for _j, _r in enumerate(_rows[_start:_start + PER_ROW]):
        with _cs[_j]:
            st.html(card_html(_r, tbl, labels, hidden=_counted_only, base=""))
if len(view) > _shown:
    if st.button(f"Show {min(PAGE, len(view) - _shown)} more", key=f"more_{tbl}"):
        st.session_state[_shown_key] = _shown + PAGE
        st.rerun()
elif len(view) > PAGE:
    st.caption("That is all of them.")

# --- the charts and the map, out of the way -------------------------------------------
with st.expander("Charts: count by a column, the spread of a number"):
    left, right = st.columns(2)
    with left:
        st.subheader("Break it down")
        try:
            if cats:
                gb = st.selectbox("Count by", cats, format_func=lambda c: labels.get(c, c), key="gb")
                counts = (df[gb].replace("", pd.NA).dropna().value_counts()
                          .head(25).rename_axis(gb).reset_index(name="count"))
                chart = (alt.Chart(counts)
                         .mark_bar(color="#B3261E")
                         .encode(x=alt.X("count:Q", title="count"),
                                 y=alt.Y(f"{gb}:N", sort="-x", title=labels.get(gb, gb)),
                                 tooltip=[alt.Tooltip(f"{gb}:N", title=labels.get(gb, gb)), "count:Q"])
                         .properties(width="container", height=430))
                st.altair_chart(chart)
            else:
                st.info("No good categorical column to group by in this dataset.")
        except Exception as ex:  # noqa: BLE001
            st.warning(f"Couldn't draw the breakdown: {ex}")
    with right:
        st.subheader("Distribution")
        try:
            if nums:
                nb = st.selectbox("Of", nums, format_func=lambda c: labels.get(c, c), key="nb")
                series, is_year = clean_numeric(df[nb])
                if len(series):
                    if is_year:
                        binspec = alt.Bin(step=10)          # decade bins, no giant gaps
                        xfield = alt.X(f"{nb}:Q", bin=binspec, title=labels.get(nb, nb),
                                       axis=alt.Axis(format="d"))
                    else:
                        xfield = alt.X(f"{nb}:Q", bin=alt.Bin(maxbins=30), title=labels.get(nb, nb))
                    hist = (alt.Chart(pd.DataFrame({nb: series}))
                            .mark_bar(color="#4A423A")
                            .encode(x=xfield, y=alt.Y("count():Q", title="count"),
                                    tooltip=[alt.Tooltip(f"{nb}:Q", bin=True, title=labels.get(nb, nb)),
                                             "count():Q"])
                            .properties(width="container", height=430))
                    st.altair_chart(hist)
                    if is_year:
                        st.caption("Decade bins; implausible years trimmed.")
                else:
                    st.info("No usable numeric values after cleaning.")
            else:
                st.info("No numeric column to chart in this dataset.")
        except Exception as ex:  # noqa: BLE001
            st.warning(f"Couldn't draw the distribution: {ex}")

if {"lat", "lng"}.issubset(df.columns):
    with st.expander("Map"):
        try:
            geo = view.copy()
            geo["lat"] = pd.to_numeric(geo["lat"], errors="coerce")
            geo["lng"] = pd.to_numeric(geo["lng"], errors="coerce")
            geo = geo.dropna(subset=["lat", "lng"]).rename(columns={"lat": "latitude", "lng": "longitude"})
            if len(geo):
                st.map(geo[["latitude", "longitude"]], size=30)
            else:
                st.info("No coordinates among the rows shown.")
        except Exception as ex:  # noqa: BLE001
            st.warning(f"Couldn't draw the map: {ex}")

# --- the table and the download, for the spreadsheet-minded ----------------------------
with st.expander("The table, and a download"):
    pretty = view.drop(columns=[c for c in (*_hidden_per_row, "_row_id") if c in view.columns]).rename(columns=labels)
    st.dataframe(pretty, width="stretch", hide_index=True)
    st.download_button("⬇ Download this view (CSV)", pretty.to_csv(index=False),
                       file_name=f"{tbl}.csv", mime="text/csv")
    st.caption("Saves the rows shown here, with your search and filters applied, as a spreadsheet file; "
               "names and dates of birth are left out of the file.")
st.caption("For researchers: the same tables are available through the read API (/api/docs). "
           "Data is published under CC BY 4.0. "
           "To correct or remove a listing, see the Removal & Correction Policy.")
