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
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals  # noqa: E402
from central.dbconn import connect  # noqa: E402

PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://glassdatabase.org").rstrip("/")


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
                vals[name] = st.selectbox(lbl, ["—", *intake.options_for(fd)], help=help_)
                if vals[name] == "—":
                    vals[name] = ""
            elif kind == "multiselect":
                vals[name] = st.multiselect(lbl, intake.options_for(fd), help=help_)
            elif kind == "checkbox":
                vals[name] = st.checkbox(fd["label"], help=help_)
            elif kind == "date":
                d = st.date_input(lbl, value=None, format="YYYY-MM-DD", help=help_)
                vals[name] = str(d) if d else ""
            else:
                vals[name] = st.text_input(lbl, help=help_)
        if st.form_submit_button("Submit for review", type="primary"):
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

st.set_page_config(page_title="Explore · Glass Database", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

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



from explore.dataclean import GEO_COLS, classify, clean_numeric, is_excluded  # noqa: E402,F401

# --- sidebar: dataset picker ----------------------------------------------
reg = registry()
st.sidebar.title("📊 Explore")
st.sidebar.caption("Public data from the Glass Database. Play with it, filter it, take it.")
if st.sidebar.button("↻ Refresh data"):
    st.cache_data.clear(); st.rerun()

mode = st.sidebar.radio("View",
                        ["Datasets", "Objects (provenance)", "Opportunities", "Community", "Submit"],
                        label_visibility="collapsed")

with st.sidebar.expander("💬 Send feedback"):
    with st.form("site_feedback", clear_on_submit=True):
        fb_msg = st.text_area("What's on your mind?", label_visibility="collapsed",
                              placeholder="Bug, idea, correction…")
        fb_email = st.text_input("Email (optional, if you'd like a reply)")
        if st.form_submit_button("Send") and fb_msg.strip():
            from central import feedback as _fb
            _fb.submit(_conn(), fb_msg, fb_email, page=mode)
            st.success("Thanks — sent to the team.")

# ===========================================================================
# SUBMIT — public intake sheets (artist / studio / event) → pending + Discord
# ===========================================================================
if mode == "Submit":
    st.title("Add to the database")
    st.caption("Everything is reviewed before it appears publicly. Contact details stay private.")
    labels = {"Artist": "artist", "Studio": "studio", "Event / exhibition": "event",
              "Resource (supplier, service, class)": "resource",
              "Exchange (buy / sell / trade)": "exchange", "Job / gig": "job"}
    pick = st.selectbox("What are you submitting?", list(labels))
    _render_intake(labels[pick])
    st.caption("Open calls, residencies, or grants with a deadline go under **Opportunities** so "
               "they land on the calendar.")
    st.stop()

# ===========================================================================
# COMMUNITY — exchange (buy/sell/trade), jobs, and resources
# ===========================================================================
if mode == "Community":
    from central import approvals as _appr
    from central import intake as _intake
    st.title("Community")
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
        st.info(f"No {tab.lower()} listings yet — post one under **Submit**.")
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
if mode == "Opportunities":
    from central import opportunities as _opp
    _opp.ensure_opportunities(_conn())
    st.title("Artist opportunities")
    st.caption("Open calls, residencies, grants, and shows. Subscribe once and new "
               "approved listings appear in your calendar automatically.")
    rows = _opp.approved(_conn())

    ics = _opp.build_ics(rows, PUBLIC_BASE)
    ics_url = f"{PUBLIC_BASE}/api/opportunities.ics"
    webcal = ics_url.replace("https://", "webcal://").replace("http://", "webcal://")
    c1, c2 = st.columns([1, 2])
    c1.download_button("⬇ Download calendar (.ics)", ics, file_name="glass-opportunities.ics",
                       mime="text/calendar", width="stretch")
    c2.markdown(f"**Subscribe:** [add to Apple/Outlook calendar]({webcal}) · in Google Calendar, "
                f"*Other calendars → From URL* → `{ics_url}`")
    st.divider()

    if not rows:
        st.info("No opportunities published yet — be the first to submit one below.")
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
            st.html(_opp.month_grid_html(rows, y, m))
            st.caption("Amber = deadlines · violet = residencies/grants. Tap an entry for details.")
        else:
            _render_opp_list(rows, _opp)

    with st.expander("➕ Submit an opportunity  (reviewed before it appears)"):
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
            if st.form_submit_button("Submit for review", type="primary"):
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
if mode == "Objects (provenance)":
    import base64 as _b64
    import json as _json

    from explore.objects_a11y import build_objects_html
    st.title("Glass objects — provenance")
    try:
        rows = _conn().execute("SELECT * FROM objects ORDER BY published_at DESC").fetchall()
    except Exception:
        rows = []

    objects = []
    for o in rows:
        imgs = _conn().execute(
            "SELECT role, caption, image_b64 FROM object_images WHERE object_row_id=? ORDER BY "
            "CASE role WHEN 'primary' THEN 0 ELSE 1 END, id", (o["_row_id"],)).fetchall()
        images = [(im["role"], im["caption"], im["image_b64"]) for im in imgs]
        try:
            man = _json.loads(o["manifest_json"] or "{}")
            events = next((a["data"] for a in man.get("assertions", [])
                           if a["label"] == "glassdb.provenance.events"), [])
        except Exception:
            man, events = {}, []
        creds = None
        if ("has_credentials" in o.keys()) and o["has_credentials"] and images:
            try:
                from glowtbook import c2pa_sign
                creds = c2pa_sign.read_credentials(_b64.b64decode(images[0][2]))
            except Exception:
                creds = None
        has_video = any(r == "video-poster" for r, _, _ in images)
        objects.append({
            "id": o["_row_id"], "title": o["title"], "maker": o["maker"], "year": o["year"],
            "techniques": o["techniques"], "materials": o["materials"], "dimensions": o["dimensions"],
            "description": o["description"], "contributor": o["contributor"], "sourcing": o["sourcing"],
            "value_display": o["value_display"], "content_hash": o["content_hash"],
            "has_credentials": bool(o["has_credentials"]) if "has_credentials" in o.keys() else False,
            "manifest_json": _json.dumps(man, ensure_ascii=False) if man else "",
            "images": images, "events": events, "creds": creds,
            "verify_url": ("https://contentcredentials.org/verify?source="
                           + quote(f"{PUBLIC_BASE}/api/objects/{o['_row_id']}/image", safe="")),
            "video_url": f"{PUBLIC_BASE}/api/objects/{o['_row_id']}/video" if has_video else None,
            "fingerprint": man.get("fingerprint"),
        })

    st.html(build_objects_html(objects, verify_base=PUBLIC_BASE))

    with_fp = [o for o in objects if o.get("fingerprint")
               and o["fingerprint"].get("rating") is not None]
    if with_fp:
        st.divider()
        st.subheader("Verify a physical piece")
        st.caption("Confirm a physical object is the same one registered here — the fingerprint "
                   "loads from the registry and matching runs in your browser (camera capture, "
                   "colour + optional DINOv2). No upload of your capture leaves the device.")
        for o in with_fp:
            fp = o["fingerprint"]
            st.markdown(f'- **{o["title"]}** — {o["maker"] or "?"} · {fp["rating"]}/100 '
                        f'({fp["tier"]}) · [Verify this piece](/fingerprint/verify.html?object={o["_row_id"]}) '
                        "(opens the camera capture app)")
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
    st.title("Glass Database — explore the data")
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
domain = st.sidebar.selectbox("Category", sorted(by_domain))
ds = st.sidebar.selectbox(
    "Dataset", by_domain[domain],
    format_func=lambda d: f"{d['tbl']} ({d['row_count']})",
)
tbl = ds["tbl"]

# --- header metrics --------------------------------------------------------
total_rows = sum(d["row_count"] for d in reg)
st.title("Glass Database — explore the data")
m1, m2, m3 = st.columns(3)
m1.metric("Public datasets", len(reg))
m2.metric("Total public rows", f"{total_rows:,}")
m3.metric("This dataset", f"{ds['row_count']:,} rows")
st.caption(ds["description"])

cols = public_columns(tbl)
labels = {c: lbl for c, lbl in cols}
try:
    df = load(tbl, tuple(c for c, _ in cols)).copy()
    cats, nums = classify(df)
except Exception as ex:  # noqa: BLE001
    st.error(f"Couldn't load this dataset: {ex}")
    st.stop()

# --- filters ---------------------------------------------------------------
with st.expander("Filters", expanded=False):
    fcols = st.multiselect("Filter by", cats, format_func=lambda c: labels.get(c, c),
                           max_selections=3)
    for fc in fcols:
        vals = sorted(v for v in df[fc].replace("", pd.NA).dropna().unique())
        chosen = st.multiselect(labels.get(fc, fc), vals, key=f"flt_{fc}")
        if chosen:
            df = df[df[fc].isin(chosen)]
st.caption(f"Showing {len(df):,} rows after filters.")

# --- break-down (categorical) ---------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader("Break it down")
    try:
        if cats:
            gb = st.selectbox("Count by", cats, format_func=lambda c: labels.get(c, c), key="gb")
            counts = (df[gb].replace("", pd.NA).dropna().value_counts()
                      .head(25).rename_axis(gb).reset_index(name="count"))
            chart = (alt.Chart(counts)
                     .mark_bar(color="#e2571e")
                     .encode(x=alt.X("count:Q", title="count"),
                             y=alt.Y(f"{gb}:N", sort="-x", title=labels.get(gb, gb)),
                             tooltip=[alt.Tooltip(f"{gb}:N", title=labels.get(gb, gb)), "count:Q"])
                     .properties(width="container", height=430))
            st.altair_chart(chart)
        else:
            st.info("No good categorical column to group by in this dataset.")
    except Exception as ex:  # noqa: BLE001
        st.warning(f"Couldn't draw the breakdown: {ex}")

# --- distribution (numeric) -----------------------------------------------
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
                        .mark_bar(color="#3b7dd8")
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

# --- map (if geocoded) -----------------------------------------------------
if {"lat", "lng"}.issubset(df.columns):
    st.subheader("Map")
    try:
        geo = df.copy()
        geo["lat"] = pd.to_numeric(geo["lat"], errors="coerce")
        geo["lng"] = pd.to_numeric(geo["lng"], errors="coerce")
        geo = geo.dropna(subset=["lat", "lng"]).rename(columns={"lat": "latitude", "lng": "longitude"})
        if len(geo):
            st.map(geo[["latitude", "longitude"]], size=30)
    except Exception as ex:  # noqa: BLE001
        st.warning(f"Couldn't draw the map: {ex}")

# --- table + download ------------------------------------------------------
st.subheader("Rows")
pretty = df.rename(columns=labels)
st.dataframe(pretty, width="stretch", hide_index=True)
st.download_button("⬇ Download this view (CSV)", pretty.to_csv(index=False),
                   file_name=f"{tbl}.csv", mime="text/csv")
st.caption("Data is published under a Creative Commons license. "
           "To correct or remove a listing, see the Removal & Correction Policy.")
