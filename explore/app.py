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

mode = st.sidebar.radio("View", ["Datasets", "Objects (provenance)"], label_visibility="collapsed")

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

    # Verify a physical piece against a registered fingerprint
    with_fp = [o for o in objects if o.get("fingerprint")
               and o["fingerprint"].get("rating") is not None]
    if with_fp:
        st.divider()
        st.subheader("Verify a physical piece")
        st.caption("Confirm a physical object is the same one registered here — match a fresh "
                   "camera capture against its re-identification fingerprint.")
        labels = {f'{o["title"]} — {o["maker"] or "?"} '
                  f'({o["fingerprint"]["rating"]}/100 {o["fingerprint"]["tier"]})': o for o in with_fp}
        pick = st.selectbox("Object", list(labels), key="fp_pick")
        chosen = labels[pick]
        st.markdown("**1.** [Open the capture app](/fingerprint/verify.html) (new tab — needs the "
                    "camera). Capture the piece from several angles, then export the `.zip`.")
        cand = st.file_uploader("**2.** Upload your capture (.zip) to match", type=["zip"], key="fp_cand")
        if cand and st.button("Check the match", type="primary"):
            try:
                from glowtbook import fingerprint as _fp
                res = _fp.verify_zip(chosen["fingerprint"]["data"], cand.getvalue())
                verdict = res.get("verdict")
                icon = {"match-likely": "✅", "no-match": "⛔", "inconclusive": "⚠️"}.get(verdict, "•")
                st.metric("Confidence", f'{res.get("confidence")}/100', res.get("label"))
                st.write(f"{icon} **{res.get('label')}** — {res.get('reference_views_matched', 0)} "
                         f"reference views matched across {res.get('n_candidates', 0)} captured frames.")
                st.caption("Decision support, not a forensic guarantee — capturing distinctive, "
                           "hard-to-forge detail (pontil marks, backlit internal bubbles) matters most.")
            except Exception as ex:  # noqa: BLE001
                st.error(f"Couldn't run the match: {ex}")
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
