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
import os
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central import approvals  # noqa: E402
from central.dbconn import connect, using_turso  # noqa: E402

st.set_page_config(page_title="Glass Database — admin", page_icon="🛠️", layout="wide", initial_sidebar_state="expanded")

from brand import apply_theme  # noqa: E402

apply_theme("admin")


@st.cache_resource
def _c():
    c = connect()
    approvals.ensure_approvals(c)
    return c

conn = _c()

# Optional Google-role gate: set GLASSDB_ADMIN_OIDC=1 (once the admin URL is a
# registered OAuth redirect) to gate the console on Google admins instead of / in
# addition to the proxy's basic auth. Default off, so existing setups keep working.
if os.environ.get("GLASSDB_ADMIN_OIDC") == "1":
    from central import users
    try:
        logged_in = st.user.is_logged_in
    except Exception:
        logged_in = False
    if not logged_in:
        st.title("🛠️ Admin")
        st.write("Sign in with Google to continue.")
        if st.button("Log in", type="primary"):
            st.login()
        st.stop()
    _email = getattr(st.user, "email", "") or ""
    users.record_login(conn, _email, getattr(st.user, "name", "") or "")
    if not users.is_admin(conn, _email):
        st.title("🛠️ Admin")
        st.error(f"You're signed in as **{_email}**, but you're not an administrator. "
                 "Ask an existing admin to promote you (Admin → Users), then reload.")
        if st.button("Log out"):
            st.logout()
        st.stop()

st.sidebar.title("🛠️ Admin")
st.sidebar.caption(f"Target: **{'Turso cloud' if using_turso() else 'local file'}**")
section = st.sidebar.radio("Section", ["📋 Datasets", "✅ Approvals", "🛡️ Review queue",
                                       "🧹 Duplicates", "💬 Discord", "📮 Feedback", "📊 Analytics",
                                       "👥 Users"],
                           label_visibility="collapsed")
from brand import track as _track

_track("admin", section)


def datasets(vis_all=True):
    where = "" if vis_all else "WHERE visibility='public'"
    return conn.execute(
        f"SELECT tbl, domain, visibility, row_count, description FROM _datasets {where} "
        f"ORDER BY domain, tbl").fetchall()


def columns(tbl):
    return conn.execute(
        "SELECT column, label, is_public FROM _columns WHERE tbl=? ORDER BY ordinal", (tbl,)
    ).fetchall()


def _cell(v):
    import pandas as _pd
    return "" if v is None or (isinstance(v, float) and _pd.isna(v)) else str(v)


def _apply_edits(tbl, orig_df, edited_df):
    """Diff the editor grid against what was loaded and write updates/inserts/deletes.
    Admin edits publish immediately (approved through the gate)."""
    editable = [c for c in orig_df.columns if c != "_row_id"]
    orig_ids = {str(x) for x in orig_df["_row_id"].dropna().tolist()}
    edited_ids = {str(x) for x in edited_df["_row_id"].dropna().tolist() if str(x).strip()}
    now = datetime.now(timezone.utc).isoformat()
    updated = added = deleted = 0
    for rid in orig_ids - edited_ids:          # deletions
        conn.execute(f'DELETE FROM "{tbl}" WHERE _row_id=?', (rid,))
        try:
            conn.execute('DELETE FROM _approvals WHERE tbl=? AND row_id=?', (tbl, rid))
        except Exception:
            pass
        deleted += 1
    omap = {str(r["_row_id"]): r for _, r in orig_df.iterrows()}
    for _, r in edited_df.iterrows():
        rid = r.get("_row_id")
        if rid is None or str(rid).strip() == "" or str(rid) == "nan":
            vals = {c: _cell(r[c]) for c in editable}
            if not any(v.strip() for v in vals.values()):
                continue
            nid = hashlib.sha1((tbl + "|edit|" + json.dumps(vals, sort_keys=True) + now
                                + str(added)).encode()).hexdigest()[:16]
            allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *editable]
            conn.execute(f'INSERT INTO "{tbl}" ({", ".join(chr(34)+x+chr(34) for x in allc)}) '
                         f'VALUES ({", ".join("?" for _ in allc)})',
                         [nid, "admin-ui", "admin-ui", now, *[vals[c] for c in editable]])
            approvals.set_status(conn, tbl, [nid], "approved")
            added += 1
        else:
            rid = str(rid); o = omap.get(rid)
            if o is None:
                continue
            changed = {c: _cell(r[c]) for c in editable if _cell(o[c]) != _cell(r[c])}
            if changed:
                sets = ", ".join(f'"{c}"=?' for c in changed)
                conn.execute(f'UPDATE "{tbl}" SET {sets} WHERE _row_id=?', [*changed.values(), rid])
                approvals.set_status(conn, tbl, [rid], "approved")
                updated += 1
    conn.execute(f'UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM "{tbl}") WHERE tbl=?', (tbl,))
    conn.commit()
    return {"updated": updated, "added": added, "deleted": deleted}


def _import_csv(tbl, df):
    """Upsert rows from a CSV. Rows with an existing _row_id update in place; rows
    without one are inserted (with a fresh id) and published."""
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")')}
    data_cols = [c for c in df.columns if c in have and c != "_row_id"]
    now = datetime.now(timezone.utc).isoformat()
    updated = added = 0
    for _, r in df.iterrows():
        rid = _cell(r["_row_id"]) if "_row_id" in df.columns else ""
        vals = {c: _cell(r[c]) for c in data_cols}
        if rid and conn.execute(f'SELECT 1 FROM "{tbl}" WHERE _row_id=?', (rid,)).fetchone():
            sets = ", ".join(f'"{c}"=?' for c in data_cols)
            conn.execute(f'UPDATE "{tbl}" SET {sets} WHERE _row_id=?', [*vals.values(), rid])
            approvals.set_status(conn, tbl, [rid], "approved"); updated += 1
        else:
            if not any(v.strip() for v in vals.values()):
                continue
            nid = rid or hashlib.sha1((tbl + "|csv|" + json.dumps(vals, sort_keys=True)
                                       + now + str(added)).encode()).hexdigest()[:16]
            allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *data_cols]
            conn.execute(f'INSERT INTO "{tbl}" ({", ".join(chr(34)+x+chr(34) for x in allc)}) '
                         f'VALUES ({", ".join("?" for _ in allc)})',
                         [nid, "csv-import", "csv-import", now, *[vals[c] for c in data_cols]])
            approvals.set_status(conn, tbl, [nid], "approved"); added += 1
    conn.execute(f'UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM "{tbl}") WHERE tbl=?', (tbl,))
    conn.commit()
    return {"updated": updated, "added": added}


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
    from glowtbook.contribute import promote_object_submission
    return promote_object_submission(conn, sub["id"])


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
    t_browse, t_edit, t_add, t_batch = st.tabs(
        ["Browse", "✏️ Edit", "➕ Add a row", "⚙️ Batch tools"])
    with t_browse:
        n = st.slider("Rows", 5, 200, 25)
        st.dataframe(pd.read_sql_query(f'SELECT * FROM "{tbl}" LIMIT {n}', conn),
                     width="stretch", hide_index=True)

    with t_edit:
        st.caption("Edit cells directly, add rows (＋ at the bottom), or tick rows and press "
                   "delete. Saving publishes admin edits immediately.")
        n2 = st.slider("Rows to load", 10, 1000, 200, key=f"editn_{tbl}")
        orig = pd.read_sql_query(f'SELECT * FROM "{tbl}" LIMIT {n2}', conn)
        internal = [c for c in orig.columns if c.startswith("_") and c != "_row_id"]
        show = orig.drop(columns=internal)
        edited = st.data_editor(show, num_rows="dynamic", width="stretch", hide_index=True,
                                disabled=["_row_id"], key=f"editor_{tbl}")
        if st.button("💾 Save changes", type="primary", key=f"save_{tbl}"):
            res = _apply_edits(tbl, show, edited)
            st.success(f"Saved — {res['updated']} updated, {res['added']} added, "
                       f"{res['deleted']} deleted."); st.rerun()

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

    with t_batch:
        from central import snapshots
        st.subheader("CSV round-trip")
        st.caption("Export, edit in a spreadsheet, re-import. Keep the `_row_id` column to update "
                   "existing rows; blank it (or add new rows) to insert. Imports publish.")
        full = pd.read_sql_query(f'SELECT * FROM "{tbl}"', conn)
        st.download_button("⬇ Export CSV", full.to_csv(index=False),
                           file_name=f"{tbl}.csv", mime="text/csv", key=f"exp_{tbl}")
        up = st.file_uploader("Import CSV", type=["csv"], key=f"imp_{tbl}")
        if up and st.button("Import CSV", type="primary", key=f"impbtn_{tbl}"):
            snapshots.snapshot(conn, tbl, "before CSV import")
            newdf = pd.read_csv(up, dtype=str).fillna("")
            res = _import_csv(tbl, newdf)
            st.success(f"Imported — {res['updated']} updated, {res['added']} added. "
                       "A snapshot was saved (Undo below)."); st.rerun()

        st.divider()
        st.subheader("Find & replace in a column")
        colnames = [c["column"] for c in cols] or [c for c in full.columns if not c.startswith("_")]
        fr_col = st.selectbox("Column", colnames, key=f"frc_{tbl}")
        a, b = st.columns(2)
        find = a.text_input("Find", key=f"frf_{tbl}")
        repl = b.text_input("Replace with", key=f"frr_{tbl}")
        sub = st.checkbox("Substring (replace within cells, not whole-cell match)", key=f"frs_{tbl}")
        if st.button("Apply replace", key=f"frg_{tbl}", disabled=not find):
            snapshots.snapshot(conn, tbl, f"before replace in {fr_col}")
            if sub:
                cur = conn.execute(f'UPDATE "{tbl}" SET "{fr_col}"=REPLACE("{fr_col}",?,?) '
                                   f'WHERE "{fr_col}" LIKE ?', (find, repl, f"%{find}%"))
            else:
                cur = conn.execute(f'UPDATE "{tbl}" SET "{fr_col}"=? WHERE "{fr_col}"=?', (repl, find))
            conn.commit(); st.success(f"Replaced in {cur.rowcount} row(s). Snapshot saved (Undo below).")

        st.divider()
        st.subheader("Bulk set a column")
        st.caption("Set a column to a value for rows matching a condition — e.g. set "
                   "*region* = 'Virginia' where *city* = 'Crozet'.")
        s1, s2 = st.columns(2)
        set_col = s1.selectbox("Set column", colnames, key=f"bsc_{tbl}")
        set_val = s2.text_input("to value", key=f"bsv_{tbl}")
        w1, w2, w3 = st.columns(3)
        where_col = w1.selectbox("where", ["(all rows)"] + colnames, key=f"bwc_{tbl}")
        match = w2.selectbox("", ["equals", "contains", "is blank"], key=f"bwm_{tbl}",
                             label_visibility="collapsed")
        where_val = w3.text_input("value", key=f"bwv_{tbl}", disabled=(match == "is blank"))
        # live preview of how many rows match
        if where_col == "(all rows)":
            clause, args = "1=1", []
        elif match == "equals":
            clause, args = f'"{where_col}"=?', [where_val]
        elif match == "contains":
            clause, args = f'"{where_col}" LIKE ?', [f"%{where_val}%"]
        else:
            clause, args = f'("{where_col}" IS NULL OR "{where_col}"="")', []
        try:
            n_match = conn.execute(f'SELECT COUNT(*) FROM "{tbl}" WHERE {clause}', args).fetchone()[0]
        except Exception:
            n_match = 0
        st.caption(f"Matches **{n_match}** row(s).")
        if st.button(f"Set {set_col} = “{set_val}” on {n_match} row(s)", key=f"bsgo_{tbl}",
                     disabled=n_match == 0):
            snapshots.snapshot(conn, tbl, f"before bulk set {set_col}")
            cur = conn.execute(f'UPDATE "{tbl}" SET "{set_col}"=? WHERE {clause}', [set_val, *args])
            conn.commit(); st.success(f"Set {cur.rowcount} row(s). Snapshot saved (Undo below).")

        st.divider()
        st.subheader("↩️ Undo / snapshots")
        snaps = snapshots.list_snapshots(conn, tbl)
        if not snaps:
            st.caption("Snapshots are saved automatically before each batch operation.")
        for sp in snaps:
            cc1, cc2 = st.columns([3, 1])
            cc1.caption(f"{sp['created_at'][:16].replace('T', ' ')} · {sp['rows']} rows · {sp['note']}")
            if cc2.button("Restore", key=f"rest_{sp['id']}"):
                snapshots.snapshot(conn, tbl, "before restore")   # so restore is itself undoable
                r = snapshots.restore(conn, sp["id"])
                st.success(f"Restored {r['rows']} rows." if r else "Restore failed."); st.rerun()

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
# DISCORD
# ===========================================================================
elif section == "💬 Discord":
    import os

    from central import notify, settings
    st.header("Discord notifications")
    st.caption("New submissions post to your channel with one-click approve/reject links.")
    cur = settings.get(conn, "discord_webhook_url", os.environ.get("DISCORD_WEBHOOK_URL", ""))
    wh = st.text_input("Channel webhook URL", value=cur, type="password",
                       help="Discord → Server Settings → Integrations → Webhooks → New Webhook")
    on = st.toggle("Notify on new submissions", value=settings.get(conn, "discord_enabled", "1") != "0")
    c1, c2 = st.columns(2)
    if c1.button("Save", type="primary"):
        settings.set(conn, "discord_webhook_url", wh.strip())
        settings.set(conn, "discord_enabled", "1" if on else "0")
        st.success("Saved.")
    if c2.button("Send test message"):
        ok, msg = notify.send_test(wh.strip() or cur)
        (st.success("Test sent — check the channel.") if ok else st.error(f"Failed: {msg}"))
    st.info("Approve/reject links are signed with your GLASSDB_ADMIN_TOKEN. Anyone who can **see** "
            "the channel can act on them, so keep it restricted to reviewers. Rotating that token "
            "invalidates any outstanding links.")

# ===========================================================================
# FEEDBACK
# ===========================================================================
elif section == "📮 Feedback":
    from central import feedback
    st.header("Site feedback")
    items = feedback.open_items(conn)
    open_n = sum(1 for i in items if not i["resolved"])
    st.caption(f"{open_n} open · {len(items)} total")
    if not items:
        st.info("No feedback yet.")
    for it in items:
        with st.container(border=True):
            head = ("✅ " if it["resolved"] else "") + (it["name"] or it["email"] or "anonymous")
            st.markdown(f"**{head}** · {it['created_at'][:16].replace('T', ' ')}"
                        + (f" · {it['page']}" if it["page"] else ""))
            st.write(it["message"])
            if it["email"]:
                st.caption(f"Reply to: {it['email']}")
            if not it["resolved"]:
                if st.button("Mark resolved", key=f"fb_{it['id']}"):
                    feedback.resolve(conn, it["id"]); st.rerun()

# ===========================================================================
# ANALYTICS
# ===========================================================================
elif section == "📊 Analytics":
    import pandas as _pd

    from central import analytics
    st.header("Usage analytics")
    st.caption("Self-hosted, cookieless, no raw IPs stored. Views are counted once per "
               "session; visitors are a per-day rotating hash. Do-Not-Track is honoured.")
    days = st.radio("Window", [7, 30, 90], index=1, horizontal=True, format_func=lambda d: f"{d} days")
    s = analytics.summary(conn, days)
    m1, m2, m3 = st.columns(3)
    m1.metric("Page views", f"{s['views']:,}")
    m2.metric("Visitors (approx.)", f"{s['visitors']:,}")
    m3.metric("Submissions", f"{sum(n for e, n in s['by_event'] if str(e).startswith('submit')):,}")

    conn.execute("CREATE TABLE IF NOT EXISTS newsletter "
                 "(email TEXT PRIMARY KEY, name TEXT, created_at TEXT, source TEXT)")
    subs = conn.execute("SELECT email,name,created_at,source FROM newsletter ORDER BY created_at DESC").fetchall()
    sc1, sc2 = st.columns([1, 3])
    sc1.metric("Newsletter subscribers", f"{len(subs):,}")
    if subs:
        sc2.download_button("⬇ Export subscribers (CSV)",
                            "email,name,created_at,source\n" + "\n".join(
                                f'{r["email"]},{(r["name"] or "")},{r["created_at"]},{r["source"]}'
                                for r in subs),
                            file_name="subscribers.csv", mime="text/csv")

    if s["by_day"]:
        df = _pd.DataFrame(s["by_day"], columns=["day", "views", "visitors"]).set_index("day")
        st.line_chart(df)
    else:
        st.info("No analytics yet — data appears as people use the site.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("By surface")
        if s["by_surface"]:
            st.dataframe(_pd.DataFrame(s["by_surface"], columns=["surface", "views", "visitors"]),
                         hide_index=True, use_container_width=True)
        st.subheader("Most-used views")
        if s["by_view"]:
            st.dataframe(_pd.DataFrame(s["by_view"], columns=["view", "views"]),
                         hide_index=True, use_container_width=True)
    with c2:
        st.subheader("Events")
        if s["by_event"]:
            st.dataframe(_pd.DataFrame(s["by_event"], columns=["event", "count"]),
                         hide_index=True, use_container_width=True)
        st.subheader("Countries")
        if s["by_country"]:
            st.dataframe(_pd.DataFrame(s["by_country"], columns=["country", "visitors"]),
                         hide_index=True, use_container_width=True)
        else:
            st.caption("Country needs a GeoLite2 DB (set GEOIP_DB) — or use GoAccess on the "
                       "Apache logs for rich geo. See deploy/ANALYTICS.md.")

# ===========================================================================
# USERS
# ===========================================================================
elif section == "👥 Users":
    import os

    from central import users
    st.header("Users & administrators")
    st.caption("Everyone who's signed in with Google (name + email only). Promote "
               "someone to administrator, or add an admin by email before their first login.")
    ppl = users.list_users(conn)
    st.metric("Signed-in users", len(ppl))

    with st.expander("➕ Grant admin by email (e.g. before they've logged in)"):
        em = st.text_input("Email")
        if st.button("Make administrator", disabled=not em.strip()):
            users.set_admin(conn, em, True); st.success(f"{em.strip().lower()} is now an admin."); st.rerun()

    for u in ppl:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            role = "🛡️ admin (via config)" if u["via_config"] else ("🛡️ admin" if u["is_admin"] else "member")
            c1.markdown(f"**{u['name'] or u['email']}** — {u['email']}  ·  {role}")
            c1.caption(f"{u['logins']} logins · last {u['last_seen'][:16].replace('T', ' ')} · "
                       f"since {u['first_seen'][:10]}")
            if u["via_config"]:
                c2.caption("set in GLASSDB_ADMIN_EMAILS")
            elif u["is_admin"]:
                if c2.button("Revoke admin", key=f"rv_{u['email']}"):
                    users.set_admin(conn, u["email"], False); st.rerun()
            else:
                if c2.button("Make admin", key=f"mk_{u['email']}", type="primary"):
                    users.set_admin(conn, u["email"], True); st.rerun()

    if os.environ.get("GLASSDB_ADMIN_OIDC") != "1":
        st.info("Admin access is currently gated by the proxy's basic auth. To gate it by "
                "Google admin role instead, register the admin URL as an OAuth redirect and set "
                "`GLASSDB_ADMIN_OIDC=1` (keep at least one email in `GLASSDB_ADMIN_EMAILS` so you "
                "can't lock yourself out). See deploy/ADMIN-ROLES.md.")

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
