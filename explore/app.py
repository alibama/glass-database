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

st.set_page_config(page_title="Explore · Glass Database", page_icon="📊", layout="wide")


@st.cache_resource
def _conn():
    c = connect()
    approvals.ensure_approvals(c)
    return c


@st.cache_data(ttl=300)
def registry() -> list[dict]:
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

mode = st.sidebar.radio("View", ["Datasets", "Objects (provenance)"], label_visibility="collapsed")

# ===========================================================================
# OBJECTS — render contributed objects with images + provenance timeline
# ===========================================================================
if mode == "Objects (provenance)":
    import base64 as _b64
    import json as _json
    st.title("Glass objects — provenance")
    st.caption("Pieces contributed through Glowtbook. Images and provenance are a "
               "condensed public rendition; originals stay with the contributor.")
    try:
        rows = _conn().execute(
            "SELECT * FROM objects ORDER BY published_at DESC").fetchall()
    except Exception:
        rows = []
    if not rows:
        st.info("No objects contributed yet. Add one in Glowtbook → Objects → Contribute.")
        st.stop()
    for o in rows:
        st.markdown(f"### {o['title']}  \n"
                    f"*{o['maker'] or 'maker unknown'} · {o['year'] or '—'}*")
        cimg, cmeta = st.columns([1, 1])
        with cimg:
            imgs = _conn().execute(
                "SELECT role, caption, image_b64 FROM object_images WHERE object_row_id=? ORDER BY id",
                (o["_row_id"],)).fetchall()
            shown = 0
            has_video = False
            for im in imgs:
                if im["role"] == "video-poster":
                    has_video = True
                try:
                    cimg.image(_b64.b64decode(im["image_b64"]),
                               caption=f'{im["role"]}: {im["caption"]}'.strip(": "), width=260)
                    shown += 1
                except Exception:
                    pass
            if has_video:
                vurl = f"{PUBLIC_BASE}/api/objects/{o['_row_id']}/video"
                try:
                    cimg.video(vurl)
                except Exception:
                    cimg.markdown(f"[▶ Play condensed video]({vurl})")
            if not shown and not has_video:
                cimg.caption("(no image)")
        with cmeta:
            if o["techniques"]:
                cmeta.write("**Techniques:** " + o["techniques"])
            if o["materials"]:
                cmeta.write("**Materials:** " + o["materials"])
            if o["dimensions"]:
                cmeta.write("**Dimensions:** " + o["dimensions"])
            if o["value_display"]:
                cmeta.write("**Stated value:** " + o["value_display"])
            cmeta.caption(f"Contributed by {o['contributor'] or 'unknown'} · "
                          f"⚠ {o['sourcing']} provenance (unverified)")
            if o["description"]:
                cmeta.write(o["description"])
            if ("has_credentials" in o.keys()) and o["has_credentials"]:
                cmeta.markdown("🔐 **Content Credentials embedded** (C2PA)")
                first = _conn().execute(
                    "SELECT image_b64 FROM object_images WHERE object_row_id=? ORDER BY "
                    "CASE role WHEN 'primary' THEN 0 ELSE 1 END, id LIMIT 1",
                    (o["_row_id"],)).fetchone()
                img_url = f"{PUBLIC_BASE}/api/objects/{o['_row_id']}/image"
                if first:
                    cmeta.download_button(
                        "⬇ Signed image (credentials intact)", _b64.b64decode(first["image_b64"]),
                        file_name=f"{o['content_hash'] or o['_row_id']}.jpg", mime="image/jpeg",
                        key=f"dl_{o['_row_id']}")
                cc = "https://contentcredentials.org/verify?source=" + quote(img_url, safe="")
                cmeta.markdown(
                    f"Verify externally: [Content Credentials]({cc}) · "
                    "[c2paviewer.com](https://c2paviewer.com) — drop the file in, or paste this URL:")
                cmeta.code(img_url, language=None)
                cmeta.caption("Re-encoding (e.g. by social platforms) strips the credential — "
                              "use this original file/URL to verify. A self-signed test cert reads "
                              "as *untrusted* until a Trust-List cert is installed.")
                with cmeta.expander("Read credential here"):
                    try:
                        from glowtbook import c2pa_sign
                        creds = c2pa_sign.read_credentials(_b64.b64decode(first["image_b64"])) if first else None
                        if creds:
                            st.write(f"**Signed by:** {creds.get('issuer') or 'unknown'}")
                            st.write(f"**Assertions:** {', '.join(creds.get('assertions') or [])}")
                            st.caption(f"Validation: {creds.get('validation_state')} "
                                       "— self-signed test cert, not yet trust-list verified.")
                        else:
                            st.caption("No readable credential found.")
                    except Exception as ex:  # noqa: BLE001
                        st.caption(f"Couldn't read credential: {ex}")
        # provenance timeline from the manifest
        try:
            man = _json.loads(o["manifest_json"] or "{}")
            events = next((a["data"] for a in man.get("assertions", [])
                           if a["label"] == "glassdb.provenance.events"), [])
        except Exception:
            man, events = {}, []
        if events:
            st.markdown("**Provenance**")
            for e in events:
                line = f"- **{e.get('event_type','?')}** — {e.get('event_date','?')}"
                who = e.get("actor") or e.get("location")
                if who:
                    line += f" · {who}"
                st.markdown(line)
                if e.get("note"):
                    st.caption("  " + e["note"])
        if man:
            st.download_button("⬇ Provenance manifest (C2PA-ready)",
                               _json.dumps(man, indent=2, ensure_ascii=False),
                               file_name=f"{o['content_hash']}.manifest.json",
                               mime="application/json", key=f"man_{o['_row_id']}")
        st.divider()
    st.stop()

# ===========================================================================
# DATASETS (default)
# ===========================================================================
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
