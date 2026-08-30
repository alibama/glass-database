"""
Glass Database — admin (content, moderation, de-duplication)
============================================================
Behind HTTP basic auth at the proxy. Three sections:

  • Datasets    — browse any dataset and add rows (bulk stays via CLI ingest).
  • Review queue — approve/reject Glowtbook submissions. Approving PROMOTES a
                   submission into a public `profiles` table (auto-registered so
                   it shows up in the API and explorer immediately).
  • Duplicates  — find likely duplicate rows in a dataset and delete the extras.

Writes go to whatever central.dbconn points at (local file or Turso).

Run:  streamlit run admin/app.py --server.baseUrlPath admin
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals  # noqa: E402
from central.dbconn import connect, using_turso  # noqa: E402

st.set_page_config(page_title="Glass Database — admin", page_icon="🛠️", layout="wide")


@st.cache_resource
def _c():
    c = connect()
    approvals.ensure_approvals(c)
    return c

conn = _c()

st.sidebar.title("🛠️ Admin")
st.sidebar.caption(f"Target: **{'Turso cloud' if using_turso() else 'local file'}**")
section = st.sidebar.radio("Section", ["📋 Datasets", "✅ Approvals", "🛡️ Review queue", "🧹 Duplicates"],
                           label_visibility="collapsed")


def datasets(vis_all=True):
    where = "" if vis_all else "WHERE visibility='public'"
    return conn.execute(
        f"SELECT tbl, domain, visibility, row_count, description FROM _datasets {where} "
        f"ORDER BY domain, tbl").fetchall()


def columns(tbl):
    return conn.execute(
        "SELECT column, label, is_public FROM _columns WHERE tbl=? ORDER BY ordinal", (tbl,)
    ).fetchall()


def ensure_submissions():
    conn.execute("""CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT UNIQUE,
        display_name TEXT, payload_json TEXT NOT NULL, status TEXT DEFAULT 'pending_review',
        sourcing TEXT DEFAULT 'self-submitted', received_at TEXT DEFAULT (datetime('now')))""")
    conn.commit()


PROFILE_COLS = ["display_name", "website", "nationality_base", "status",
                "techniques", "contributions", "career_highlights", "approved_at"]


def ensure_profiles_registered():
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        _row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, _imported_at TEXT,
        display_name TEXT, website TEXT, nationality_base TEXT, status TEXT,
        techniques TEXT, contributions TEXT, career_highlights TEXT, approved_at TEXT)""")
    n = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    conn.execute("""INSERT OR REPLACE INTO _datasets
        (tbl, domain, source_file, source_sheet, visibility, row_count, description, updated_at)
        VALUES ('profiles','artists','glowtbook','contributions','public',?,
                'Artist-contributed profiles, approved from Glowtbook submissions',?)""",
        (n, datetime.now(timezone.utc).isoformat()))
    for i, col in enumerate(PROFILE_COLS):
        conn.execute("""INSERT OR REPLACE INTO _columns (tbl, column, label, ordinal, is_public)
                        VALUES ('profiles',?,?,?,1)""", (col, col.replace("_", " ").title(), i))
    conn.commit()


def ensure_object_staging():
    conn.execute("""CREATE TABLE IF NOT EXISTS object_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT UNIQUE,
        submitted_uid TEXT, submitted_by TEXT,
        title TEXT, maker TEXT, year TEXT, techniques TEXT, materials TEXT, dimensions TEXT,
        description TEXT, sourcing TEXT, value_display TEXT DEFAULT '',
        has_credentials INTEGER DEFAULT 0, manifest_json TEXT,
        status TEXT DEFAULT 'pending', review_note TEXT,
        received_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS object_submission_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER, role TEXT, caption TEXT,
        image_b64 TEXT)""")
    conn.commit()


def ensure_public_objects():
    """The public objects registry (approve target). Mirrors Glowtbook's schema."""
    conn.execute("""CREATE TABLE IF NOT EXISTS objects (
        _row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, _imported_at TEXT,
        title TEXT, maker TEXT, year TEXT, techniques TEXT, materials TEXT, dimensions TEXT,
        description TEXT, contributor TEXT, sourcing TEXT, value_display TEXT DEFAULT '',
        has_credentials INTEGER DEFAULT 0, manifest_json TEXT, content_hash TEXT, published_at TEXT)""")
    if "has_credentials" not in {r[1] for r in conn.execute("PRAGMA table_info(objects)")}:
        conn.execute("ALTER TABLE objects ADD COLUMN has_credentials INTEGER DEFAULT 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS object_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, object_row_id TEXT, role TEXT, caption TEXT,
        image_b64 TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    now = datetime.now(timezone.utc).isoformat()
    n = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    conn.execute("""INSERT OR REPLACE INTO _datasets
        (tbl,domain,source_file,source_sheet,visibility,row_count,description,updated_at)
        VALUES ('objects','objects','glowtbook','contributions','public',?,
                'Provenance-tracked glass objects contributed via Glowtbook (condensed DIP)',?)""",
        (n, now))
    for i, col in enumerate(["title", "maker", "year", "techniques", "materials", "dimensions",
                             "description", "contributor", "sourcing", "value_display", "content_hash"]):
        conn.execute("""INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public)
                        VALUES ('objects',?,?,?,1)""", (col, col.replace("_", " ").title(), i))
    conn.commit()


def approve_object_submission(sub):
    """Promote a pending object submission into the public objects + object_images tables."""
    ensure_public_objects()
    now = datetime.now(timezone.utc).isoformat()
    rid = hashlib.sha1(("obj|" + sub["content_hash"]).encode()).hexdigest()[:16]
    conn.execute("""INSERT OR REPLACE INTO objects
        (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,techniques,materials,
         dimensions,description,contributor,sourcing,value_display,has_credentials,manifest_json,content_hash,published_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, "glowtbook", "contributions", now, sub["title"], sub["maker"], sub["year"],
         sub["techniques"], sub["materials"], sub["dimensions"], sub["description"],
         sub["submitted_by"], sub["sourcing"], sub["value_display"], sub["has_credentials"],
         sub["manifest_json"], sub["content_hash"], now))
    conn.execute("DELETE FROM object_images WHERE object_row_id=?", (rid,))
    for im in conn.execute("SELECT role,caption,image_b64 FROM object_submission_images WHERE submission_id=?",
                           (sub["id"],)).fetchall():
        conn.execute("INSERT INTO object_images (object_row_id,role,caption,image_b64) VALUES (?,?,?,?)",
                     (rid, im["role"], im["caption"], im["image_b64"]))
    conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM objects) WHERE tbl='objects'")
    conn.execute("UPDATE object_submissions SET status='approved' WHERE id=?", (sub["id"],))
    conn.commit()
    approvals.set_status(conn, "objects", [rid], "approved")   # publish through the gate
    return rid


# ===========================================================================
# DATASETS
# ===========================================================================
if section == "📋 Datasets":
    ds = datasets()
    labels = [f"{d['tbl']} ({d['row_count']})" + ("  🔒" if d['visibility'] == 'restricted' else "")
              for d in ds]
    i = st.sidebar.selectbox("Dataset", range(len(ds)), format_func=lambda i: labels[i])
    d = ds[i]; tbl = d["tbl"]; cols = columns(tbl)
    st.header(tbl)
    st.caption(f"{d['description']}  ·  {d['domain']}  ·  {d['visibility']}")
    t_browse, t_add = st.tabs(["Browse", "➕ Add a row"])
    with t_browse:
        n = st.slider("Rows", 5, 200, 25)
        st.dataframe(pd.read_sql_query(f'SELECT * FROM "{tbl}" LIMIT {n}', conn),
                     width="stretch", hide_index=True)
    with t_add:
        with st.form("add", clear_on_submit=True):
            vals, grid = {}, st.columns(2)
            for j, c in enumerate(cols):
                lock = "  🔒" if not c["is_public"] else ""
                vals[c["column"]] = grid[j % 2].text_input(f"{c['label']}{lock}", key=f"f_{c['column']}")
            if st.form_submit_button("Add row", type="primary"):
                vals = {k: v for k, v in vals.items() if v.strip()}
                if vals:
                    now = datetime.now(timezone.utc).isoformat()
                    rid = hashlib.sha1((tbl + "|admin|" + json.dumps(vals, sort_keys=True) + now).encode()).hexdigest()[:16]
                    allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *vals]
                    row = [rid, "admin-ui", "admin-ui", now, *vals.values()]
                    conn.execute(f'INSERT INTO "{tbl}" ({", ".join(chr(34)+x+chr(34) for x in allc)}) '
                                 f'VALUES ({", ".join("?" for _ in allc)})', row)
                    conn.execute("UPDATE _datasets SET row_count=row_count+1 WHERE tbl=?", (tbl,))
                    conn.commit()
                    approvals.set_status(conn, tbl, [rid], "approved")   # admin add = published
                    st.success(f"Added {rid} (published).")
                else:
                    st.error("Fill at least one field.")

# ===========================================================================
# APPROVALS — the publication gate (nothing is public until approved)
# ===========================================================================
elif section == "✅ Approvals":
    st.header("Publication gate")
    st.caption("Nothing in a dataset is served publicly until it's approved here. "
               "New imports and contributions arrive **pending** by default.")

    public = [d for d in datasets() if d["visibility"] == "public"]
    totals = {d["tbl"]: approvals.counts(conn, d["tbl"]) for d in public}
    tot_pending = sum(c["pending"] for c in totals.values())
    tot_appr = sum(c["approved"] for c in totals.values())

    m1, m2, m3 = st.columns(3)
    m1.metric("Public datasets", len(public))
    m2.metric("Approved (published)", tot_appr)
    m3.metric("Pending review", tot_pending)

    with st.expander("⚡ First-time setup — approve everything already in the database"):
        st.write("Use this once to publish the content that was already loaded. "
                 "After that, review new material per dataset below.")
        ok = st.checkbox("Yes, approve every pending row in every public dataset.")
        if st.button("Approve ALL pending content", type="primary", disabled=not ok):
            n = sum(approvals.approve_all(conn, d["tbl"]) for d in public)
            st.success(f"Approved {n} row(s) across {len(public)} dataset(s)."); st.rerun()

    st.divider()
    st.subheader("By dataset")
    for d in public:
        c = totals[d["tbl"]]
        head = f"**{d['tbl']}** — {c['approved']} approved · {c['pending']} pending"
        if c["rejected"]:
            head += f" · {c['rejected']} rejected"
        cc = st.container(border=True)
        cc.markdown(head)
        b1, b2, b3 = cc.columns([1, 1, 3])
        if b1.button("✅ Approve all pending", key=f"apall_{d['tbl']}", disabled=not c["pending"]):
            n = approvals.approve_all(conn, d["tbl"]); st.toast(f"Approved {n} in {d['tbl']}"); st.rerun()
        if b2.button("✖ Reject all pending", key=f"rjall_{d['tbl']}", disabled=not c["pending"]):
            n = approvals.reject_all_pending(conn, d["tbl"]); st.toast(f"Rejected {n} in {d['tbl']}"); st.rerun()
        if c["pending"]:
            with cc.expander(f"Review individual pending rows ({c['pending']})"):
                pubcols = [col["column"] for col in columns(d["tbl"]) if col["is_public"]][:6]
                rows = approvals.pending_rows(conn, d["tbl"], pubcols, limit=50)
                if rows:
                    df = pd.DataFrame([dict(r) for r in rows])
                    df.insert(0, "✓ approve", False)
                    edited = st.data_editor(df, hide_index=True, width="stretch",
                                            disabled=[c for c in df.columns if c != "✓ approve"],
                                            key=f"ed_{d['tbl']}")
                    sel = edited[edited["✓ approve"]]["_row_id"].tolist()
                    s1, s2 = st.columns(2)
                    if s1.button(f"Approve selected ({len(sel)})", key=f"apsel_{d['tbl']}", disabled=not sel):
                        approvals.set_status(conn, d["tbl"], sel, "approved")
                        st.toast(f"Approved {len(sel)}"); st.rerun()
                    if s2.button(f"Reject selected ({len(sel)})", key=f"rjsel_{d['tbl']}", disabled=not sel):
                        approvals.set_status(conn, d["tbl"], sel, "rejected")
                        st.toast(f"Rejected {len(sel)}"); st.rerun()
                    if c["pending"] > 50:
                        st.caption("Showing the first 50 — use “Approve all pending” for the rest.")

# ===========================================================================
# REVIEW QUEUE
# ===========================================================================
elif section == "🛡️ Review queue":
    st.header("Review queue")
    tab_obj, tab_prof = st.tabs(["🪟 Objects", "🧑\u200d🎨 Artist profiles"])

    # ---- Objects -----------------------------------------------------------
    with tab_obj:
        ensure_object_staging()
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM object_submissions GROUP BY status").fetchall())
        c1, c2, c3 = st.columns(3)
        c1.metric("Pending", counts.get("pending", 0))
        c2.metric("Approved", counts.get("approved", 0))
        c3.metric("Rejected", counts.get("rejected", 0))
        ostatus = st.selectbox("Show", ["pending", "approved", "rejected"], key="obj_status")
        subs = conn.execute("SELECT * FROM object_submissions WHERE status=? ORDER BY received_at DESC",
                            (ostatus,)).fetchall()
        if not subs:
            st.info("Nothing here.")
        for s in subs:
            cred = " · 🔐 Content Credentials" if s["has_credentials"] else ""
            head = f"{s['title'] or '(untitled)'} — {s['maker'] or s['submitted_by'] or 'unknown'}{cred}"
            with st.expander(f"{head} · {s['received_at']} · {s['status']}"):
                imgs = conn.execute("SELECT role,caption,image_b64 FROM object_submission_images WHERE submission_id=?",
                                    (s["id"],)).fetchall()
                if imgs:
                    cols = st.columns(min(4, len(imgs)))
                    for j, im in enumerate(imgs):
                        try:
                            cols[j % len(cols)].image(base64.b64decode(im["image_b64"]),
                                                      caption=im["caption"] or im["role"], width="stretch")
                        except Exception:
                            pass
                meta = {k: s[k] for k in ("year", "techniques", "materials", "dimensions",
                                          "description", "sourcing", "value_display", "content_hash")}
                st.write({k: v for k, v in meta.items() if v})
                st.caption(f"Submitted by {s['submitted_by'] or 'unknown'} · sourcing: {s['sourcing']} "
                           "(self-reported — approving asserts you've checked it)")
                with st.expander("Provenance manifest"):
                    st.json(json.loads(s["manifest_json"]), expanded=False)
                if ostatus == "pending":
                    a, r = st.columns(2)
                    if a.button("✅ Approve & publish", key=f"oap_{s['id']}"):
                        rid = approve_object_submission(s)
                        st.success(f"Published to objects ({rid}). Live in the API and Explore.")
                        st.rerun()
                    if r.button("✖ Reject", key=f"orj_{s['id']}"):
                        conn.execute("UPDATE object_submissions SET status='rejected' WHERE id=?", (s["id"],))
                        conn.commit(); st.rerun()

    # ---- Artist profiles ---------------------------------------------------
    with tab_prof:
        ensure_submissions()
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM submissions GROUP BY status").fetchall())
        c1, c2, c3 = st.columns(3)
        c1.metric("Pending", counts.get("pending_review", 0))
        c2.metric("Approved", counts.get("approved", 0))
        c3.metric("Rejected", counts.get("rejected", 0))
        status = st.selectbox("Show", ["pending_review", "approved", "rejected"], key="prof_status")
        subs = conn.execute("SELECT * FROM submissions WHERE status=? ORDER BY received_at DESC", (status,)).fetchall()
        if not subs:
            st.info("Nothing here.")
        for s in subs:
            payload = json.loads(s["payload_json"])
            with st.expander(f"{s['display_name'] or '(no name)'} · {s['received_at']} · {s['status']}"):
                st.json(payload, expanded=False)
                if status == "pending_review":
                    a, r = st.columns(2)
                    if a.button("✅ Approve & publish", key=f"ap_{s['id']}"):
                        ensure_profiles_registered()
                        now = datetime.now(timezone.utc).isoformat()
                        rid = hashlib.sha1(("profiles|" + s["content_hash"]).encode()).hexdigest()[:16]
                        conn.execute("""INSERT OR REPLACE INTO profiles
                            (_row_id,_source_file,_source_sheet,_imported_at,display_name,website,
                             nationality_base,status,techniques,contributions,career_highlights,approved_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (rid, "glowtbook", "contributions", now,
                             payload.get("display_name", ""), payload.get("website", ""),
                             payload.get("nationality_base", ""), payload.get("status", ""),
                             ", ".join(payload.get("techniques", [])),
                             ", ".join(payload.get("contributions", [])),
                             payload.get("career_highlights", ""), now))
                        conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM profiles) WHERE tbl='profiles'")
                        conn.execute("UPDATE submissions SET status='approved' WHERE id=?", (s["id"],))
                        conn.commit()
                        approvals.set_status(conn, "profiles", [rid], "approved")
                        st.success("Published to profiles."); st.rerun()
                    if r.button("✖ Reject", key=f"rj_{s['id']}"):
                        conn.execute("UPDATE submissions SET status='rejected' WHERE id=?", (s["id"],))
                        conn.commit(); st.rerun()

# ===========================================================================
# DUPLICATES
# ===========================================================================
else:
    st.header("Find & remove duplicates")
    ds = datasets()
    tbl = st.selectbox("Dataset", [d["tbl"] for d in ds],
                       index=[d["tbl"] for d in ds].index("artists") if any(d["tbl"] == "artists" for d in ds) else 0)
    cols = [c["column"] for c in columns(tbl)]
    # sensible default key column
    prefer = next((k for k in ["artist_name", "name", "display_name", "show", "fair",
                               "institution", "museum", "event_show_name"] if k in cols), cols[0] if cols else None)
    keycol = st.selectbox("Match on", cols, index=cols.index(prefer) if prefer in cols else 0)
    fuzzy = st.checkbox("Also flag near-duplicates (fuzzy)", value=False)
    thresh = st.slider("Fuzzy similarity", 0.80, 0.99, 0.90, 0.01, disabled=not fuzzy)

    df = pd.read_sql_query(f'SELECT _row_id, "{keycol}" FROM "{tbl}"', conn)
    df["_norm"] = (df[keycol].fillna("").astype(str).str.lower()
                   .str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip())
    df = df[df["_norm"] != ""]

    groups = {}
    for norm, sub in df.groupby("_norm"):
        if len(sub) > 1:
            groups[norm] = list(sub["_row_id"])

    if fuzzy:
        uniq = df.drop_duplicates("_norm")["_norm"].tolist()
        seen = set(groups)
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                if a in seen or b in seen:
                    continue
                if SequenceMatcher(None, a, b).ratio() >= thresh:
                    ids = list(df[df["_norm"].isin([a, b])]["_row_id"])
                    groups[f"{a} ≈ {b}"] = ids
                    seen.add(a); seen.add(b)

    st.write(f"**{len(groups)}** duplicate cluster(s) found on `{keycol}`.")
    to_delete = []
    for norm, ids in list(groups.items())[:100]:
        rows = pd.read_sql_query(
            f'SELECT _row_id, "{keycol}", _source_file FROM "{tbl}" WHERE _row_id IN ({",".join("?"*len(ids))})',
            conn, params=ids)
        with st.expander(f"“{norm}” — {len(ids)} rows"):
            st.dataframe(rows, width="stretch", hide_index=True)
            keep = st.selectbox("Keep which row?", rows["_row_id"].tolist(), key=f"keep_{norm}")
            drop = [r for r in ids if r != keep]
            if st.checkbox(f"Delete the other {len(drop)} row(s)", key=f"del_{norm}"):
                to_delete += drop

    if to_delete:
        st.warning(f"{len(to_delete)} row(s) marked for deletion.")
        if st.button("🗑️ Delete marked rows", type="primary"):
            conn.execute(f'DELETE FROM "{tbl}" WHERE _row_id IN ({",".join("?"*len(to_delete))})', to_delete)
            conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM \"%s\") WHERE tbl=?" % tbl, (tbl,))
            conn.commit(); st.success(f"Deleted {len(to_delete)} rows."); st.rerun()
    st.caption("Tip: legacy vs current tables (e.g. artists vs artists_legacy) overlap by design — "
               "de-dupe within a table here, and drop a whole legacy table via the CLI when you're ready.")
