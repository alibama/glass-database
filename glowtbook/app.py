"""
Glowtbook
=========
A journal + object-provenance registry for people who work in glass.

Per-user isolation: when signed in (OIDC), everything is keyed to the user's
identity and each user's media lives in its own directory. Demo mode (no auth
configured) shares one space, clearly labeled.

Objects are the provenance-bearing, contributable entities: metadata, images,
and an event timeline (acquired / exhibited / appraised / sold / restored ...).
Contributing an object keeps the full-fidelity AIP local and sends a condensed
DIP (downscaled images + a C2PA-shaped manifest) to the central registry.

Run:  streamlit run glowtbook/app.py --server.baseUrlPath glowtbook
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from central.dbconn import connect as central_connect  # noqa: E402
from glowtbook import (
    aip,  # noqa: E402  (BagIt AIP + MinIO)
    c2pa_sign,  # noqa: E402  (Content Credentials signing)
    media,  # noqa: E402  (media.py sits beside this file)
    video,  # noqa: E402  (video transcode to condensed DIP)
)
from glowtbook import store as gstore  # noqa: E402  (per-user store, shared with the API)


def _central():
    """The central registry (publication state, seasons). None if unavailable."""
    try:
        return central_connect()
    except Exception:  # noqa: BLE001
        return None
from glowtbook.contribute import (  # noqa: E402
    DATA,
    MEDIA,
    MODERATION,
    PUBLIC_BASE,
    add_event,
    add_image,
    aip_dir,
    contribute_object,
    create_object,
    uid_slug,
    update_object,
)

DEMO_DB = DATA / "glowtbook_demo.db"

TECHNIQUES = list(media.TECHNIQUE_GBO.keys()) + ["Stained glass", "Functional / pipe art"]
CONTRIBUTIONS = [
    "Making / studio practice", "Fabrication & technical support",
    "Grassroots mentorship & teaching", "Material & tool innovation",
    "Community organizing & mutual aid", "Infrastructure / studio-building",
]
EVENT_TYPES = ["created", "acquired", "exhibited", "appraised", "sold",
               "loaned", "damaged", "restored", "other"]

st.set_page_config(page_title="Glowtbook — your studio notebook", page_icon="🔥", layout="centered")

from brand import apply_theme  # noqa: E402

apply_theme("glowtbook")
from brand import track as _track

_track("glowtbook")

# Mobile polish: tighter padding, comfortable tap targets when installed as a PWA.
st.html("""<style>
@media (max-width: 640px) {
  .block-container {padding: 1rem 0.9rem 3rem !important;}
  .stButton>button, .stDownloadButton>button {width: 100%; padding: 0.6rem 1rem;}
  [data-testid="stFileUploaderDropzone"] {padding: 0.75rem;}
}
</style>""")


# --- per-user store (schema lives in glowtbook/store.py, shared with the API) --
@st.cache_resource
def store() -> sqlite3.Connection:
    return gstore.open_store()


# --- identity (OIDC if configured, else shared demo) -----------------------
def auth_config() -> dict:
    try:
        cfg = dict(st.secrets.get("auth", {}))
    except Exception:
        return {}
    if not cfg:
        return {}
    try:
        import authlib  # noqa: F401
    except Exception:
        return {}
    return cfg


AUTH_CFG = auth_config()
AUTH_ON = bool(AUTH_CFG)


def resolve_user():
    if not AUTH_ON:
        return "__demo__", "demo", True
    try:
        logged_in = st.user.is_logged_in
    except Exception:
        st.warning("Login is enabled but not fully configured — running in demo mode.")
        return "__demo__", "demo", True
    if not logged_in:
        st.header("🔥 Glowtbook")
        st.caption("your studio notebook")
        st.write("Sign in to start your **private** notebook: a journal and the pieces you register.")
        names = [k for k, v in AUTH_CFG.items() if isinstance(v, Mapping)]
        has_default = all(k in AUTH_CFG for k in ("client_id", "client_secret", "server_metadata_url"))
        try:
            if names:
                for p in names:
                    if st.button(f"Continue with {p.title()}", type="primary", key=f"login_{p}"):
                        st.login(p)
            elif has_default:
                if st.button("Log in", type="primary"):
                    st.login()
            else:
                st.error("Auth is enabled but no provider is configured in secrets.toml.")
        except Exception as ex:  # noqa: BLE001
            st.error(f"Login isn't working yet: {ex}")
        st.stop()
    uid = getattr(st.user, "sub", None) or getattr(st.user, "email", None) or "user"
    display = getattr(st.user, "name", None) or getattr(st.user, "email", None) or "you"
    email = getattr(st.user, "email", None) or ""
    if email and not st.session_state.get("_gdb_login_logged"):
        st.session_state["_gdb_login_logged"] = True
        try:
            from central import users
            users.record_login(store(), email, getattr(st.user, "name", "") or "")
        except Exception:
            pass
    return uid, display, False


uid, display, is_demo = resolve_user()
conn = store()


def profile_row():
    r = conn.execute("SELECT * FROM profile WHERE user_id=?", (uid,)).fetchone()
    if not r:
        conn.execute("INSERT INTO profile (user_id) VALUES (?)", (uid,)); conn.commit()
        r = conn.execute("SELECT * FROM profile WHERE user_id=?", (uid,)).fetchone()
    return r


st.sidebar.title("🔥 Glowtbook")
st.sidebar.caption("your studio notebook")
if is_demo:
    st.sidebar.warning("Shared demo — not private.")
else:
    st.sidebar.success(f"Signed in as {display}")
    if st.sidebar.button("Log out"):
        st.logout()
    st.sidebar.caption("Signs you out; your notebook stays saved.")
try:
    user_email = (getattr(st.user, "email", None) or "") if not is_demo else ""
except Exception:  # noqa: BLE001
    user_email = ""
# Deep links from the capture app / Collecting Events page: /glowtbook/?page=objects&object=12
# The page keys are stable (`?page=objects`, `?page=events`); the labels are what people read
# (docs/NAMING.md).
_PAGE_KEYS = ["home", "journal", "objects", "events", "profile"]
_PAGE_LABELS = {"home": "Home", "journal": "Journal", "objects": "My pieces",
                "events": "Shows & Sales", "profile": "My profile"}
try:
    _qp = (st.query_params.get("page") or "").lower()
except Exception:  # noqa: BLE001
    _qp = ""
_pidx = {"home": 0, "journal": 1, "objects": 2, "events": 3, "profile": 4, "my profile": 4}.get(_qp, 0)
page = st.sidebar.radio("Go to", _PAGE_KEYS, index=_pidx, label_visibility="collapsed",
                        format_func=lambda k: _PAGE_LABELS[k])
st.sidebar.link_button("📷 Register Your Piece", "/fingerprint/enroll.html", use_container_width=True)
st.sidebar.caption("Walk around it with your phone; it gets its Glass ID.")
st.sidebar.link_button("🎃 Collecting Events", "/games.html", use_container_width=True)
st.sidebar.caption("Time-boxed collecting games with awards and a leaderboard.")
st.sidebar.link_button("🎟 Shows & Sales", "/events.html", use_container_width=True)
st.sidebar.caption("Where makers sell in person.")


# ===========================================================================
if page == "home":
    st.header("Glowtbook")
    st.caption("your studio notebook")
    st.markdown(
        "A notebook for people who work in glass: a **journal**, and the **pieces** you register "
        "with their stories.\n\n"
        "Your journal and your pieces live here privately. Publishing a piece keeps your "
        "full-resolution originals and private values on this machine (the archival copy) and "
        "shares only a **condensed** version with its story and its Glass ID."
    )
    if is_demo:
        st.warning("Shared demo — sign-in gives each person a private space.")

    # --- your pieces at a glance: what's captured, what's published, what's next ---
    _cards = gstore.list_objects(conn, _central(), uid)
    if _cards:
        _season, _claims = None, {}
        try:
            from central import seasons as _sea
            _cc = _central()
            if _cc is not None:
                _sea.ensure(_cc)
                _season = _sea.current_season(_cc)
                # collectors' claims on the published ones (verified/confirmed + waiting on you)
                _claims = _sea.claim_counts(_cc, [c["publication"].get("row_id") for c in _cards])
        except Exception:  # noqa: BLE001
            _season, _claims = None, {}
        st.subheader("Your pieces at a glance")
        n_fp = sum(1 for c in _cards if c["fingerprint"])
        n_pub = sum(1 for c in _cards if c["publication"]["state"] == "published")
        n_pend = sum(1 for c in _cards if c["publication"]["state"] == "pending")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Pieces", len(_cards)); m2.metric("With a Glass ID", n_fp)
        m3.metric("Published", n_pub); m4.metric("In review", n_pend)
        n_wait = sum(v["pending"] for v in _claims.values())
        if n_wait:
            st.info(f"🧾 **{n_wait} collection request(s) waiting for you** — a collector scanned a "
                    "piece of yours but the match was under 70. Confirm or reject in "
                    "My pieces → Collecting Events.")
        rows = ["| Piece | Glass ID | Registry | Collection requests | Next step |", "|---|---|---|---|---|"]
        for c in _cards[:25]:
            fp = c["fingerprint"]
            fp_txt = f"{fp['rating']}/100 {fp['tier']}" if fp else "—"
            stt = c["publication"]["state"]
            reg = {"published": "✅ published", "pending": "🕐 in review", "rejected": "✖ rejected"}.get(stt, "private")
            cc = _claims.get(c["publication"].get("row_id")) or {"ok": 0, "pending": 0}
            cl_txt = " · ".join(x for x in [f"{cc['ok']} ✓" if cc["ok"] else "",
                                           f"{cc['pending']} waiting" if cc["pending"] else ""] if x) or "—"
            if not fp:
                nxt = "📷 register it to get its Glass ID"
            elif stt == "private":
                nxt = "📤 publish it (My pieces → Publish)"
            elif stt == "pending":
                nxt = "waiting for review"
            elif cc["pending"]:
                nxt = f"🧾 confirm {cc['pending']} collection request(s) (My pieces → Collecting Events)"
            elif stt == "published" and _season:
                nxt = f"🎃 enter it in {_season['title']}"
            else:
                nxt = "✓ all set"
            rows.append(f"| **{c['title']}** | {fp_txt} | {reg} | {cl_txt} | {nxt} |")
        st.markdown("\n".join(rows))
        c1, c2 = st.columns(2)
        c1.link_button("📷 Register Your Piece", "/fingerprint/enroll.html", use_container_width=True)
        c1.caption("Walk around it with your phone; it gets its Glass ID.")
        c2.link_button("🎃 Collecting Events", "/games.html", use_container_width=True)
        c2.caption("See this season's collecting event and enter a published piece.")
    else:
        st.info("No pieces yet. Start in **My pieces** — or open **Register Your Piece** and add one "
                "from your phone.")

# ===========================================================================
elif page == "journal":
    st.header("Journal")
    if is_demo:
        st.warning("Shared demo — not private.")
    with st.form("j", clear_on_submit=True):
        d = st.date_input("Date", value=date.today())
        title = st.text_input("Title")
        body = st.text_area("Entry", height=140)
        c1, c2 = st.columns(2)
        loc = c1.text_input("Location", placeholder="studio, kiln, gallery…")
        tech = c2.text_input("Technique / focus")
        img = st.file_uploader("Attach a photo (kept local)", type=["jpg", "jpeg", "png"])
        saved = st.form_submit_button("Save entry")
        st.caption("Adds this entry to your journal; it stays on this machine.")
        if saved and (title or body):
            cur = conn.execute(
                "INSERT INTO journal (user_id,entry_date,title,body,location,technique) VALUES (?,?,?,?,?,?)",
                (uid, d.isoformat(), title, body, loc, tech))
            if img is not None:
                p = MEDIA / uid_slug(uid) / "journal"; p.mkdir(parents=True, exist_ok=True)
                fp = p / f"{cur.lastrowid}_{img.name}"; fp.write_bytes(img.getvalue())
                conn.execute("UPDATE journal SET image_path=? WHERE id=?", (str(fp), cur.lastrowid))
            conn.commit(); st.success("Saved.")
    for e in conn.execute("SELECT * FROM journal WHERE user_id=? ORDER BY id DESC LIMIT 50", (uid,)):
        with st.expander(f"{e['entry_date']} · {e['title'] or '(untitled)'}"):
            meta = " · ".join(x for x in [e["location"], e["technique"]] if x)
            if meta: st.caption(meta)
            st.write(e["body"] or "")
            if e["image_path"] and Path(e["image_path"]).exists():
                st.image(e["image_path"], width=280)

# ===========================================================================
elif page == "objects":
    st.header("My pieces")
    st.caption("Keep each piece with its story, value, insurance and history. "
               "Full-resolution originals and any values stay on this machine.")

    with st.expander("＋ New piece", expanded=False):
        with st.form("newobj", clear_on_submit=True):
            title = st.text_input("Title *")
            c1, c2 = st.columns(2)
            maker = c1.text_input("Maker")
            year = c2.text_input("Year")
            st.caption("**Techniques**")
            techs = st.pills("Techniques", TECHNIQUES, selection_mode="multi",
                             label_visibility="collapsed", key="nt_techs") or []
            c3, c4 = st.columns(2)
            materials = c3.text_input("Materials")
            dims = c4.text_input("Dimensions")
            desc = st.text_area("Description")
            created = st.form_submit_button("Add this piece", type="primary")
            st.caption("Starts a page for the piece in your notebook; nothing is published.")
            if created and title.strip():
                create_object(conn, uid, title=title, maker=maker, year=year,
                              techniques="|".join(techs), materials=materials,
                              dimensions=dims, description=desc)
                st.success("Created."); st.rerun()

    objs = conn.execute("SELECT * FROM object WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    if not objs:
        st.info("No pieces yet — add one above.")
    labels = {o["id"]: f'{o["title"]} ({o["year"] or "—"})' for o in objs}
    if objs:
        _ids = [o["id"] for o in objs]
        try:
            _qo = st.query_params.get("object")
        except Exception:  # noqa: BLE001
            _qo = None
        _oidx = _ids.index(int(_qo)) if (_qo and str(_qo).isdigit() and int(_qo) in _ids) else 0
        oid = st.selectbox("Open a piece", _ids, index=_oidx, format_func=lambda i: labels[i])
        o = conn.execute("SELECT * FROM object WHERE id=? AND user_id=?", (oid, uid)).fetchone()
        try:
            _qtab = (st.query_params.get("tab") or "").lower()
        except Exception:  # noqa: BLE001
            _qtab = ""
        # `?tab=` deep links keep the old words; show the name people see
        _TAB_NAMES = {"fingerprint": "Glass ID", "seasons": "Collecting Events", "contribute": "Publish",
                      "provenance": "Story"}
        if _qtab:
            st.caption(f"👉 Open the **{_TAB_NAMES.get(_qtab, _qtab.title())}** tab below.")

        tab_meta, tab_media, tab_prov, tab_fp, tab_style, tab_season, tab_contrib = st.tabs(
            ["Details", "Images", "Story", "Glass ID", "Style", "Collecting Events", "Publish"])

        with tab_meta:
            title = st.text_input("Title", value=o["title"])
            c1, c2 = st.columns(2)
            maker = c1.text_input("Maker", value=o["maker"])
            year = c2.text_input("Year", value=o["year"])
            st.caption("**Techniques**")
            techs = st.pills("Techniques", TECHNIQUES, selection_mode="multi",
                             default=[t for t in (o["techniques"] or "").split("|") if t],
                             label_visibility="collapsed", key=f"et_techs_{oid}") or []
            materials = st.text_input("Materials", value=o["materials"])
            dims = st.text_input("Dimensions", value=o["dimensions"])
            desc = st.text_area("Description", value=o["description"])
            colour_src = st.text_input(
                "Colour rods / sources used (optional)",
                value=(o["colour_sources"] if "colour_sources" in o.keys() else "") or "",
                placeholder="e.g. Reichenbach 2017 (amber), Gaffer G-089 (teal), Kugler K-001",
                help="Which colour bars/frit you used, by maker and code. Over time this is what lets the "
                     "registry learn to tell colours apart by where they were made — see docs/GLASS-VISION.md.")
            st.markdown("**Value & insurance** 🔒 _private — never contributed unless you opt in_")
            c5, c6, c7 = st.columns(3)
            vamt = c5.text_input("Value", value=o["value_amount"])
            vcur = c6.text_input("Currency", value=o["value_currency"] or "USD")
            insured = c7.checkbox("Insured", value=bool(o["insured"]))
            save_details = st.button("Save details", type="primary")
            st.caption("Updates this piece in your notebook; values stay private.")
            if save_details:
                update_object(conn, oid, uid, title=title, maker=maker, year=year,
                              techniques="|".join(techs), materials=materials, dimensions=dims,
                              description=desc, value_amount=vamt, value_currency=vcur, insured=insured)
                conn.execute("UPDATE object SET colour_sources=? WHERE id=? AND user_id=?",
                             (colour_src.strip(), oid, uid))
                conn.commit()
                st.success("Saved.")

        with tab_media:
            ups = st.file_uploader("Add images (originals kept local as the archival copy)",
                                   type=["jpg", "jpeg", "png"], accept_multiple_files=True)
            st.caption("On a phone, the file picker offers **Take Photo** directly.")
            role = st.pills("Role", ["primary", "photo", "detail"], selection_mode="single",
                            default="primary", key="img_role") or "primary"
            cap = st.text_input("Caption")
            save_images = st.button("Save images", disabled=not ups)
            st.caption("Keeps the originals on this machine as the archival copy.")
            if save_images:
                d = aip_dir(uid, oid)
                saved = 0
                for u in (ups or []):
                    fp = d / u.name; fp.write_bytes(u.getvalue())
                    add_image(conn, oid, uid, role=role, aip_path=fp, caption=cap)
                    saved += 1
                conn.commit(); st.success(f"Saved {saved} image(s)."); st.rerun()
            imgs = conn.execute("SELECT * FROM object_image WHERE object_id=? AND user_id=?", (oid, uid)).fetchall()
            cols = st.columns(3)
            for i, im in enumerate(imgs):
                if Path(im["aip_path"]).exists():
                    cols[i % 3].image(im["aip_path"], caption=f'{im["role"]}: {im["caption"]}', width=180)

        with tab_prov:
            with st.form("ev", clear_on_submit=True):
                et = st.selectbox("Event", EVENT_TYPES)
                c1, c2 = st.columns(2)
                ed = c1.text_input("Date", placeholder="2024 or 2024-06-01")
                actor = c2.text_input("Actor", placeholder="who")
                eloc = st.text_input("Location")
                note = st.text_area("Note")
                c3, c4 = st.columns(2)
                eva = c3.text_input("Value (optional, private)")
                evc = c4.text_input("Currency", value="USD")
                add_ev = st.form_submit_button("Add to the story", type="primary")
                st.caption("Adds a dated moment (made, sold, shown, repaired…) to this piece's story.")
                if add_ev:
                    add_event(conn, oid, uid, event_type=et, event_date=ed, actor=actor,
                              location=eloc, note=note, value_amount=eva, value_currency=evc)
                    st.success("Added."); st.rerun()
            evs = conn.execute("SELECT * FROM prov_event WHERE object_id=? AND user_id=? ORDER BY event_date, id", (oid, uid)).fetchall()
            for e in evs:
                st.markdown(f"**{e['event_type']}** — {e['event_date'] or '?'}  ·  {e['actor']}")
                if e["note"]: st.caption(e["note"])

        with tab_fp:
            from central import sessions as _sess
            from glowtbook import fingerprint as _fp
            st.caption("🧪 **Alpha — for testers.** Registering and checking work; thresholds are still "
                       "being calibrated on real pieces. A verdict is evidence, not proof. Tell us what you see.")
            st.write("A **Glass ID** is a one-of-a-kind ID for your piece, like a serial number for "
                     "handmade glass — read off the glass itself from many angles, so anyone can later "
                     "confirm *this is the same physical piece*. It goes with the piece when you publish it.")
            st.caption("Technically: the Glass ID is stored as a fingerprint of the glass (colour + shape "
                       "descriptors and, with AI on, a DINOv2 vector) and signed into the photo's Content "
                       "Credentials (C2PA).")
            cur_fp = o["fingerprint_json"] if "fingerprint_json" in o.keys() else None
            fp_at = (o["fingerprint_at"] if "fingerprint_at" in o.keys() else None) or ""
            pub = gstore.publication_status(_central(), conn, uid, oid)
            if cur_fp:
                s = _fp.summary(cur_fp)
                views = len(_fp._as_obj(cur_fp).get("frames") or [])
                extra = " · DINOv2" if s.get("has_embeddings") else ""
                when = f" · saved {fp_at[:10]}" if fp_at else ""
                st.success(f"**On file:** {s['rating']}/100 ({s['tier']}) · {views} views{extra}{when}")
                if pub["state"] == "published":
                    st.caption("This piece is published — if you register it again, publish it again so "
                               "the new Glass ID goes with it.")
            else:
                st.info("**No Glass ID yet.** Register the piece — it takes about a minute with the phone.")

            # Pairing: the capture app opens already signed in as you, with THIS piece chosen.
            # (The token rides in the URL fragment — it never reaches server logs.)
            pair = _sess.pairing_link(PUBLIC_BASE, uid, user_email, display, object_id=oid)
            c1, c2 = st.columns(2)
            c1.link_button("📷 Register this piece here", pair, type="primary", use_container_width=True)
            c1.caption("Walk around it with your phone; it gets its Glass ID.")
            c2.link_button("🔍 Check it", f"/fingerprint/verify.html?mine={oid}",
                           disabled=not cur_fp, use_container_width=True)
            c2.caption("Point your phone at it to see who made it.")
            with st.expander("📱 Pair your phone", expanded=not cur_fp):
                st.caption("Scan the code to register with your phone.")
                st.caption("It opens the camera page signed in as you, with **" + o["title"]
                           + "** chosen, and saves straight back here. The code is good for 2 hours.")
                try:
                    import io as _io

                    import segno
                    buf = _io.BytesIO()
                    segno.make(pair, error="m").save(buf, kind="png", scale=6, border=2,
                                                     dark="#1F1A16", light="#F4EEE4")
                    st.image(buf.getvalue(), width=220)
                except Exception:  # noqa: BLE001
                    st.code(pair, language=None)
                    st.caption("Install `segno` on the server to show this as a QR code.")
                if is_demo:
                    st.caption("Shared demo: the phone will save into the shared demo space.")
            st.caption("Shooting on the [Photo Mat](/fingerprint/capture-mat.pdf) "
                       "(keep all four markers in frame) also measures real dimensions.")
            with st.expander("Import a Glass ID export (.zip) instead"):
                up = st.file_uploader("Export from the camera page", type=["zip"], key=f"fp_up_{oid}")
                save_fp = bool(up) and st.button("Save Glass ID", key=f"fp_save_{oid}")
                if up:
                    st.caption("Reads the export and attaches its Glass ID to this piece.")
                if save_fp:
                    r = _fp.load_enrollment(up.getvalue())
                    if r.get("ok"):
                        gstore.set_fingerprint(conn, uid, oid, r["fingerprint"])
                        msg = (f"Glass ID saved — {r['rating']}/100 ({r['tier']}), "
                               f"{r['n_frames']} views.")
                        dims = r.get("dimensions")
                        if dims and dims.get("ok"):
                            st.session_state[f"fp_dims_{oid}"] = dims["dimensions"]
                            msg += f" Measured from the mat: **{dims['dimensions']}**."
                        else:
                            msg += " (No mat detected — dimensions not measured.)"
                        st.success(msg + " It goes with the piece when you publish it."); st.rerun()
                    else:
                        st.error(f"Couldn't read that export: {r.get('error')}")
                pending_dims = st.session_state.get(f"fp_dims_{oid}")
                if pending_dims:
                    set_dims = st.button(f"Set the piece's dimensions to “{pending_dims}”",
                                         key=f"fp_setdim_{oid}")
                    st.caption("Writes the size measured from the Photo Mat into the piece's details.")
                    if set_dims:
                        gstore.set_dimensions(conn, uid, oid, pending_dims)
                        del st.session_state[f"fp_dims_{oid}"]
                        st.success("Dimensions saved to the piece."); st.rerun()
            # checks recorded from the Check a Piece page land in the piece's story
            n_ver = conn.execute("SELECT COUNT(*) FROM prov_event WHERE object_id=? AND user_id=? "
                                 "AND event_type='verified'", (oid, uid)).fetchone()[0]
            if n_ver:
                st.caption(f"✓ {n_ver} check(s) recorded in this piece's story.")

        with tab_style:
            import json as _json2

            from central import glass_traits as _gt
            st.write("Tag observable **style traits** from the Venetian / façon-de-Venise "
                     "thesaurus. These describe *features*, not an attribution of origin.")
            cur = {}
            try:
                cur = _json2.loads(o["traits_json"]) if ("traits_json" in o.keys() and o["traits_json"]) else {}
            except Exception:
                cur = {}
            picked = {}
            # suggestions applied on the previous run are merged into the pick-lists before
            # they are drawn (Streamlit forbids touching a widget's state after that)
            _pending = st.session_state.pop(f"tr_apply_{oid}", None) or {}
            for fid, flabel, _fdef in _gt.FACETS:
                opts = _gt.labels_by_facet(fid)
                _key = f"tr_{fid}_{oid}"
                if _pending.get(fid):
                    st.session_state[_key] = sorted(
                        set(st.session_state.get(_key, []) or []) | {x for x in _pending[fid] if x in opts})
                picked[fid] = st.multiselect(
                    flabel, opts, key=_key,
                    default=None if _key in st.session_state else [x for x in cur.get(fid, []) if x in opts])
            save_traits = st.button("Save traits", key=f"tr_save_{oid}")
            st.caption("Keeps the tags with the piece; they travel with it when you publish.")
            if save_traits:
                traits = {k: v for k, v in picked.items() if v}
                conn.execute("UPDATE object SET traits_json=? WHERE id=? AND user_id=?",
                             (_json2.dumps(traits), oid, uid))
                conn.commit()
                n = sum(len(v) for v in traits.values())
                st.success(f"Saved {n} trait(s). They travel with the piece to the registry, "
                           "linked to the SKOS vocabulary."); st.rerun()
            st.caption("Vocabulary: [SKOS/Turtle](/api/vocab/glass-traits.ttl) · "
                       "[JSON](/api/vocab/glass-traits.json) — aligns to Getty AAT / Wikidata / "
                       "the Corning Glass Dictionary.")

            # --- optional: a local glass-aware vision model suggests traits (never silently) ---
            try:
                from glowtbook import vision as _vision
            except Exception:  # noqa: BLE001
                _vision = None

            def _apply_trait_suggestions(sugg: dict, min_score: float = 0.0) -> None:
                """Queue suggested labels for the pick-lists above; they are merged in on
                the rerun, and nothing is saved until the person presses *Save traits*."""
                pend = dict(st.session_state.get(f"tr_apply_{oid}") or {})
                for fid, items in (sugg or {}).items():
                    opts = set(_gt.labels_by_facet(fid))
                    labs = {i["label"] for i in items if i["label"] in opts
                            and float(i.get("score", i.get("confidence", 1.0))) >= min_score}
                    if labs:
                        pend[fid] = sorted(set(pend.get(fid, [])) | labs)
                st.session_state[f"tr_apply_{oid}"] = pend

            if _vision is not None and _vision.available():
                st.divider()
                st.markdown("**AI suggestions** — a local vision model (Ollama) proposes observable "
                            "features from the primary image. Suggestions, never verdicts: nothing is "
                            "saved until you apply and confirm them.")
                prim = conn.execute(
                    "SELECT aip_path FROM object_image WHERE object_id=? AND user_id=? AND role!='video' "
                    "ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id LIMIT 1", (oid, uid)).fetchone()
                if not prim or not Path(prim["aip_path"]).exists():
                    st.caption("Add an image first (Images tab).")
                else:
                    ai_go = st.button("✨ Suggest traits from the primary image", key=f"ai_go_{oid}")
                    st.caption("Asks the local model; you still choose what to keep.")
                    if ai_go:
                        with st.spinner("Asking the glass vision model…"):
                            st.session_state[f"ai_sugg_{oid}"] = _vision.suggest(
                                Path(prim["aip_path"]).read_bytes(),
                                techniques_hint=[t for t in (o["techniques"] or "").split("|") if t])
                res = st.session_state.get(f"ai_sugg_{oid}")
                if res:
                    if not res.get("ok"):
                        st.warning(f"No suggestion: {res.get('error')}")
                    else:
                        st.caption(f"Model: {res.get('model')} · {res.get('latency_s', 0):.1f}s")
                        for ev in (res.get("evidence") or [])[:6]:
                            st.markdown(f"- {ev}")
                        sugg = res.get("traits") or {}
                        for fid, items in sugg.items():
                            if items:
                                st.markdown(f"**{fid}**: " + ", ".join(
                                    f"{i['label']} ({int(i.get('confidence', 0) * 100)}%)" for i in items))
                        if res.get("caveats"):
                            st.caption("Caveats: " + str(res["caveats"]))
                        ai_apply = st.button("Apply suggestions to the pick-lists above", key=f"ai_apply_{oid}")
                        st.caption("Ticks them in the lists above; press Save traits to keep them.")
                        if ai_apply:
                            _apply_trait_suggestions(sugg)
                            st.rerun()

            # --- Stage 1: look-alike public pieces vote on labels (no model, no Ollama) ---
            try:
                from central import vision_index as _vidx
            except Exception:  # noqa: BLE001
                _vidx = None
            _fp_json = o["fingerprint_json"] if "fingerprint_json" in o.keys() else None
            _has_emb = bool(_vidx is not None and _vision is not None and _fp_json
                            and _vision.centroid(_fp_json))
            if _vidx is not None:
                st.divider()
                st.markdown("**Suggest from similar pieces** — the public pieces whose fingerprints "
                            "look most like this one vote on techniques and traits. No vision model, "
                            "nothing leaves the server. Suggestions, never verdicts: nothing is saved "
                            "until you apply and confirm them.")
                if not _has_emb:
                    st.caption("Needs a Glass ID registered with the AI descriptor on "
                               "(Glass ID tab → Register this piece here).")
                else:
                    knn_go = st.button("🔎 Suggest from similar pieces", key=f"knn_go_{oid}")
                    st.caption("Compares this piece's Glass ID with the public ones; nothing leaves the server.")
                    if knn_go:
                        _pub = gstore.publication_status(_central(), conn, uid, oid)
                        with st.spinner("Comparing with the public registry…"):
                            st.session_state[f"knn_sugg_{oid}"] = _vidx.suggest_for_fingerprint(
                                _fp_json, k=5,
                                exclude=[_pub["row_id"]] if _pub.get("row_id") else None)
                    kres = st.session_state.get(f"knn_sugg_{oid}")
                    if kres:
                        if not kres.get("ok"):
                            st.warning(f"No suggestion: {kres.get('error')}")
                        else:
                            import html as _html
                            kk = kres.get("k", len(kres["neighbours"]))
                            st.caption(f"{kk} nearest public piece(s) · "
                                       f"{kres.get('corpus_size', 0)} with embeddings in the registry")
                            cards = []
                            for n in kres["neighbours"]:
                                t = _html.escape(n.get("title") or n["object_row_id"], quote=True)
                                techs = _html.escape(", ".join(n.get("techniques") or []) or "—")
                                src = _html.escape(n["image"], quote=True)
                                href = _html.escape(n["href"], quote=True)
                                cards.append(
                                    f'<a href="{href}" target="_blank" rel="noopener" '
                                    f'style="text-decoration:none;color:inherit"><div style="width:128px">'
                                    f'<img src="{src}" alt="{t}" loading="lazy" style="width:128px;'
                                    f'height:128px;object-fit:cover;border-radius:0;border:1px solid #CFC2AE;background:#E3D7C4">'
                                    f'<div style="font-size:0.8rem;margin-top:4px;line-height:1.25">'
                                    f'<b>{t}</b><br>similarity {n["score"]:.2f}<br>'
                                    f'<span style="opacity:.75">{techs}</span></div></div></a>')
                            st.html('<div style="display:flex;gap:12px;flex-wrap:wrap;margin:4px 0 10px">'
                                    + "".join(cards) + "</div>")
                            if kres.get("techniques"):
                                st.markdown("**Techniques** (neighbours agreeing · weighted share): "
                                            + ", ".join(f"{t['label']} ({t['support']}/{kk} · "
                                                        f"{int(t['score'] * 100)}%)"
                                                        for t in kres["techniques"]))
                            ksugg = kres.get("traits") or {}
                            for fid, items in ksugg.items():
                                if items:
                                    st.markdown(f"**{_gt.facet_label(fid)}**: " + ", ".join(
                                        f"{i['label']} ({i['support']}/{kk} · {int(i['score'] * 100)}%)"
                                        for i in items))
                            if not ksugg:
                                st.caption("The nearest pieces carry no style traits yet — tag yours "
                                           "and the next person benefits.")
                            if kres.get("caveats"):
                                st.caption("Caveats: " + str(kres["caveats"]))
                            if ksugg:
                                knn_apply = st.button("Apply majority traits to the pick-lists above",
                                                      key=f"knn_apply_{oid}")
                                st.caption("Ticks the traits at least half the neighbours agree on; "
                                           "press Save traits to keep them.")
                                if knn_apply:
                                    _apply_trait_suggestions(ksugg, min_score=0.5)
                                    st.rerun()
            if _vision is not None and not _vision.available():
                st.caption("Set `OLLAMA_URL` on the server to also get suggestions from a local "
                           "vision model (docs/GLASS-VISION.md).")

        with tab_season:
            try:
                from central import seasons as _seasons
            except Exception:  # noqa: BLE001
                _seasons = None
            central = _central()
            if _seasons is None or central is None:
                st.info("Collecting events aren't available on this server yet.")
            else:
                _seasons.ensure(central)
                cur = _seasons.current_season(central)
                pub = gstore.publication_status(central, conn, uid, oid)
                st.write("**Collecting events** are friendly, time-boxed collecting games — pumpkins in "
                         "October, ornaments in December. Enter a published piece, collectors scan what "
                         "they buy to add it to their collection, everyone votes.")
                if not cur:
                    st.info("No collecting event is open right now — the next call for pieces goes out "
                            "on the Discord. [See past collecting events](/games.html)")
                else:
                    st.markdown(f"### {cur['title']}\n*{cur.get('theme') or ''}*  \n"
                                f"Entries close **{cur.get('entries_close') or '—'}** · voting closes "
                                f"**{cur.get('voting_closes') or '—'}** · status **{cur.get('status')}**")
                    if pub["state"] != "published":
                        why = {"pending": "it's in the review queue — once approved you can enter it.",
                               "rejected": "it was not approved for the public registry.",
                               "private": "publish it first from the **Publish** tab."}.get(pub["state"])
                        st.warning(f"Only published pieces can be entered — {why}")
                        if not cur_fp:
                            st.caption("Tip: register the piece for its Glass ID before you publish, so "
                                       "collectors can scan it and add it to their collection.")
                    else:
                        mine = [e for e in _seasons.my_entries(central, uid)
                                if e.get("object_row_id") == pub["row_id"] and e.get("season_id") == cur["id"]]
                        if mine:
                            e = mine[0]
                            st.success(f"Entered — status **{e.get('status')}** · "
                                       f"{(e.get('rarity') or 'common').upper()}"
                                       + (" · solo" if e.get("solo") else "")
                                       + (" · ◆ **your legendary** (only you can see that)"
                                          if e.get("legendary") else "")
                                       + (f" · {e.get('vote_count', 0)} votes" if e.get("vote_count") is not None else ""))
                        else:
                            if not cur_fp:
                                st.caption("Tip: a piece **with a Glass ID** enters as Uncommon or better "
                                           "(Rare at an edition of three or fewer) and can rank in the "
                                           "measured awards. Register it in the Glass ID tab first.")
                            legendary_on = st.checkbox(
                                "This is my legendary for this collecting event", key=f"season_leg_{oid}",
                                help="One per maker per collecting event: an edition of one, with a Glass ID "
                                     "and its legend written. Nobody is told which piece it is — the "
                                     "collector who adds it to their collection finds out, and it's worth "
                                     "20 points to them.")
                            with st.form(f"season_enter_{oid}"):
                                studio = st.text_input("Studio / shop it's sold from",
                                                       value=(profile_row()["nationality_base"] or "")[:80])
                                edition = st.number_input("Edition size (how many of this piece exist)",
                                                          min_value=1, max_value=10000, value=1)
                                price_note = st.text_input("Price / where to buy (optional)",
                                                           placeholder="$85 at the studio shop · ships")
                                solo = st.checkbox("Made solo (no assistant)",
                                                   help="Made without an assistant on the pipe — on your "
                                                        "honour. Solo-only awards count it.")
                                legend = ""
                                if legendary_on:
                                    legend = st.text_area(
                                        f"Its legend (at least {_seasons.LEGEND_MIN} characters)", height=100,
                                        placeholder="Where it was made, what nearly went wrong, why this is "
                                                    "the one. The collector who adds it to their collection "
                                                    "reads this.")
                                ok = st.checkbox("This piece is mine (or I'm entering it for its maker) "
                                                 "and it may be shown on the Collecting Events page.")
                                entered = st.form_submit_button("Enter a piece", type="primary")
                                st.caption("Put one of your registered pieces into this collecting event.")
                                if entered and ok:
                                    try:
                                        _seasons.enter(central, cur["id"], object_row_id=pub["row_id"],
                                                       artist_uid=uid, artist_name=o["maker"] or display,
                                                       title=o["title"], studio=studio.strip(),
                                                       edition_size=int(edition), price_note=price_note.strip(),
                                                       image_url=f"/api/objects/{pub['row_id']}/image",
                                                       solo=bool(solo), legendary=bool(legendary_on),
                                                       legend=legend.strip())
                                        st.success("Entered — an admin approves entries before they show.")
                                        st.rerun()
                                    except ValueError as ex:
                                        st.error(str(ex))
                    st.link_button("Open the collecting event", f"/games.html?season={cur.get('slug', '')}")
                    st.caption("The public page: entries, votes and the leaderboard.")

                # --- collection requests on this piece: who scanned it, and the ones waiting on you ---
                if pub["state"] == "published" and pub.get("row_id"):
                    st.divider()
                    _msg = st.session_state.pop(f"claim_msg_{oid}", None)
                    if _msg:
                        st.success(_msg)
                    _claims = _seasons.claims_on(central, pub["row_id"])
                    _n_wait = sum(1 for cl in _claims if cl["status"] == "pending")
                    st.markdown(f"**People adding this piece to their collections** — {len(_claims)}"
                                + (f" · **{_n_wait} waiting for you**" if _n_wait else ""))
                    if not _claims:
                        st.caption("Nobody has scanned this piece to add it to their collection yet. Tell "
                                   "buyers *scan it to add it to your collection* — Check a Piece does "
                                   "the rest.")
                    elif _n_wait:
                        st.caption("A scan under 70 confidence waits for you: confirm if they bought it "
                                   "(it joins their collection and counts on the boards), reject if not.")
                    _label = {"verified": "✅ verified by scan", "approved": "✅ confirmed",
                              "pending": "🕐 waiting for you", "rejected": "✖ rejected"}
                    for cl in _claims:
                        with st.container(border=True):
                            cm, cb = st.columns([4, 1])
                            cm.markdown(f"**{cl.get('collector_name') or 'A collector'}** · "
                                        f"{_label.get(cl['status'], cl['status'])}")
                            cm.caption(" · ".join(x for x in [
                                f"scan confidence {float(cl.get('confidence') or 0):.0f}/100",
                                str(cl.get("created_at") or "")[:10], cl.get("location") or "",
                                cl.get("season_title") or ""] if x))
                            if cl["status"] == "pending":
                                cb.caption("Confirm: it joins their collection. Reject: it doesn't.")
                                if cb.button("✅ Confirm", key=f"clc_{cl['id']}", type="primary",
                                             help="They bought it — count it in their collection"):
                                    try:
                                        _seasons.confirm_claim(central, cl["id"], uid, True)
                                        st.session_state[f"claim_msg_{oid}"] = (
                                            f"Confirmed — it's in {cl.get('collector_name') or 'their'} "
                                            "collection now.")
                                        st.rerun()
                                    except ValueError as ex:
                                        st.error(str(ex))
                                if cb.button("⛔ Reject", key=f"clr_{cl['id']}",
                                             help="Not this piece, or not theirs"):
                                    try:
                                        _seasons.confirm_claim(central, cl["id"], uid, False)
                                        st.session_state[f"claim_msg_{oid}"] = "Rejected."
                                        st.rerun()
                                    except ValueError as ex:
                                        st.error(str(ex))

        with tab_contrib:
            st.write("Publish a **condensed** version of this piece and its story to the public "
                     "registry; its Glass ID goes with it. Your originals and any values stay here."
                     + ("  Submissions are **reviewed by an admin** before they appear publicly."
                        if MODERATION else ""))
            st.caption("Technically: the Glass ID is stored as a fingerprint of the glass itself and "
                       "signed into the photo's Content Credentials (C2PA).")
            imgs = conn.execute("SELECT * FROM object_image WHERE object_id=? AND user_id=?", (oid, uid)).fetchall()
            evs = conn.execute("SELECT * FROM prov_event WHERE object_id=? AND user_id=? ORDER BY event_date, id", (oid, uid)).fetchall()
            incl_val = st.checkbox("Include value/insurance in the public record", value=False)
            can_sign = c2pa_sign.available()
            sign = st.checkbox("Sign the Glass ID credential into each image", value=can_sign,
                               disabled=not can_sign,
                               help="Cryptographically signs the piece's record into every image "
                                    "(Content Credentials, C2PA) so it travels with the file.")
            if can_sign and sign:
                st.caption("Signed with a self-signed **test** certificate — readable everywhere, "
                           "but flagged 'untrusted' until you swap in a C2PA Trust List cert.")
            elif not can_sign:
                st.caption("Install `c2pa-python` + `cryptography` on the server to enable signing.")
            nvid = sum(1 for im in imgs if im["role"] == "video")
            vid_note = ""
            if nvid:
                vid_note = (f" · {nvid} video → transcoded to a web MP4 + poster"
                            if video.available() else
                            f" · {nvid} video (ffmpeg not installed → kept AIP-only)")
            st.caption(f"{len(imgs)} file(s) will be downscaled · "
                       f"{len(evs)} moment(s) of its story included{vid_note}")

            minio_on = aip.minio_config() is not None
            with st.expander("Archive full-fidelity AIP (BagIt" + (" → MinIO" if minio_on else "") + ")"):
                archive_aip = st.checkbox("Bag the originals as a BagIt AIP (sha256 + sha512 fixity)",
                                          value=False)
                push_minio = st.checkbox("Also push the bag to MinIO",
                                         value=False, disabled=not minio_on,
                                         help=None if minio_on else "Set MINIO_* env vars to enable.")
                if not minio_on:
                    st.caption("MinIO not configured — bags are written locally under data/aip_bags/.")

            # Bluesky: posts with the account connected in My profile (never asks for a password here)
            _sc = _central()
            try:
                from central import social as _social
                _bsky_connected = bool(_sc is not None and
                                       (_social.status(_sc, uid).get("bluesky") or {}).get("connected"))
            except Exception:  # noqa: BLE001
                _social, _bsky_connected = None, False
            if _bsky_connected:
                bsky_on = st.checkbox("Post to Bluesky when I publish")
                st.caption("Uses your connected account; the post carries the share card.")
            else:
                bsky_on = False
                st.caption("Connect Bluesky in [My profile](?page=profile) to post when you publish.")

            a = st.checkbox("I want this piece published to the public registry (data under CC BY 4.0).")
            b = st.checkbox("The story I've recorded is accurate to the best of my knowledge.")
            btn_label = "Submit for review" if MODERATION else "Publish condensed version"
            publish_now = st.button(btn_label, type="primary", disabled=not (a and b and o["title"]))
            st.caption("Sends the condensed version, its story and its Glass ID to the registry"
                       + ("; an admin approves it before it appears." if MODERATION else "."))
            if publish_now:
                try:
                    manifest, primary_bytes = contribute_object(
                        uid, display, o, evs,
                        [{"aip_path": im["aip_path"], "role": im["role"], "caption": im["caption"]}
                         for im in imgs if Path(im["aip_path"]).exists()],
                        incl_val, sign=sign, object_id=oid,
                        archive_aip=archive_aip, push_minio=push_minio,
                        base_url=os.environ.get("PUBLIC_BASE_URL", "https://glassdatabase.org"))
                    conn.execute("INSERT INTO contribution (user_id,object_id,content_hash,manifest_json) VALUES (?,?,?,?)",
                                 (uid, oid, manifest["content_hash"], json.dumps(manifest)))
                    conn.commit()
                    signed = manifest.get("_signed")
                    cred = " · Glass ID credential embedded 🔐" if signed else ""
                    if manifest.get("_sign_error"):
                        st.error("⚠ The Glass ID credential was **not** attached — signing failed and "
                                 "the image was published unsigned.\n\n"
                                 f"Reason: `{manifest['_sign_error']}`\n\n"
                                 "Check that Vault is unsealed and the token is valid, and that "
                                 "`C2PA_TSA_URL` is reachable, then re-contribute this piece.")
                    if manifest.get("_pending"):
                        st.success(f"Submitted for review (ref {manifest['content_hash']}){cred}. "
                                   "An admin will approve it before it appears publicly.")
                    else:
                        st.success(f"Published (ref {manifest['content_hash']}){cred}. "
                                   "Visible under Find Artists & Studios → Pieces.")
                    if manifest.get("has_video"):
                        st.caption("Video transcoded to a condensed MP4 (+ poster) for the public rendition.")
                    st.download_button("⬇ Download the piece's record",
                                       json.dumps({k: v for k, v in manifest.items() if not k.startswith("_")},
                                                  indent=2, ensure_ascii=False),
                                       file_name=f"{manifest['content_hash']}.manifest.json",
                                       mime="application/json")
                    st.caption("Saves the published record (JSON) to keep with your own files.")
                    rec = manifest.get("_aip")
                    if rec and not rec.get("error"):
                        where = ("MinIO: " + rec["minio"]["url"]) if rec.get("minio") else \
                                ("local: " + rec["tar_path"])
                        st.success(f"AIP bagged 🗄️ — {rec['bag_id']} "
                                   f"({rec['tar_bytes']:,} bytes, sha256+sha512 fixity) · {where}")
                        with st.expander("AIP receipt (BagIt fixity)"):
                            st.json(rec, expanded=False)
                    elif rec and rec.get("error"):
                        st.warning(f"AIP archiving failed: {rec['error']}")
                    # --- share: the public row id once it's published, the share card, the links ---
                    _rid = None
                    if not manifest.get("_pending") and _sc is not None:
                        try:
                            _rid = gstore.publication_status(_sc, conn, uid, oid).get("row_id")
                        except Exception:  # noqa: BLE001
                            _rid = None
                    _share_link = (f"{PUBLIC_BASE}/api/social/share/object/{_rid}" if _rid
                                   else f"{PUBLIC_BASE}/explore/")
                    if _rid:
                        st.markdown("**Share**")
                        st.code(_share_link, language=None)
                        st.caption("Copy the link; wherever you paste it, the preview shows the card. "
                                   f"[The share card]({PUBLIC_BASE}/api/social/objects/{_rid}/card.png) · "
                                   f"[The piece]({PUBLIC_BASE}/explore/?view=objects#obj-{_rid})")
                    if bsky_on and _social is not None:
                        from glowtbook import bluesky
                        _creds = _social.credentials(_sc, uid, "bluesky")
                        try:
                            if not _creds:
                                raise ValueError("Bluesky isn't connected any more (My profile)")
                            _card = None
                            if _rid:
                                try:
                                    from api.social import object_card_png as _ocard
                                    _card = _ocard(_sc, _rid, PUBLIC_BASE)
                                except Exception:  # noqa: BLE001
                                    _card = None
                            from central import cards as _cards
                            _img = _cards.to_jpeg(_card) if _card else primary_bytes
                            _mime = "image/png" if (_img or b"")[:4] == b"\x89PNG" else "image/jpeg"
                            _text = (f"{o['title']} · made by {o['maker'] or display} · on the Glass Database"
                                     f"\n{_share_link}")
                            url = bluesky.post(_creds[0], _creds[1], _text, _img, _mime,
                                               alt=o["title"] or "A piece of glass", links=[_share_link])
                            _social.mark_used(_sc, uid, "bluesky")
                            _social.log_share(_sc, uid, "bluesky", "object",
                                              _rid or manifest["content_hash"], url)
                            st.success(f"Posted to Bluesky: {url}")
                            if signed and not _card:
                                st.caption("Note: Bluesky re-encodes images, which usually strips the "
                                           "Glass ID credential from the copy it hosts. The checkable "
                                           "original stays in the registry.")
                        except Exception as bex:  # noqa: BLE001
                            try:
                                _social.mark_used(_sc, uid, "bluesky", str(bex))
                            except Exception:  # noqa: BLE001
                                pass
                            st.warning(f"Saved here, but Bluesky post failed: {bex}")
                except Exception as ex:  # noqa: BLE001
                    st.error(f"Couldn't submit: {ex}")

# ===========================================================================
elif page == "events":
    import base64 as _b64
    from datetime import timedelta as _td

    from central import events as _ev
    central = _central()
    st.header("Shows & Sales")
    st.caption("Host a sale, a demo or an ornament day, and let the people who buy a piece add it to "
               "their collection at the table. It lands in their Glowtbook, you approve it, and the site "
               "publishes it with its story: made at your show, bought by them.")
    if central is None:
        st.info("The central registry isn't reachable, so shows and sales are unavailable right now.")
    else:
        _ev.ensure(central)
        tab_host, tab_mine = st.tabs(["🎟 Shows & sales I host", "🧾 My collection"])

        with tab_host:
            with st.expander("＋ Host a show or sale", expanded=False):
                with st.form("newevent", clear_on_submit=True):
                    t = st.text_input("Show or sale title *", placeholder="Ornament day")
                    c1, c2 = st.columns(2)
                    studio = c1.text_input("Studio / maker (shown as the maker)",
                                           value=profile_row()["display_name"] or display)
                    venue = c2.text_input("Venue (optional)", placeholder="2nd St Gallery")
                    c3, c4, c5 = st.columns(3)
                    city = c3.text_input("City"); region = c4.text_input("State / region")
                    country = c5.text_input("Country")
                    c6, c7 = st.columns(2)
                    starts = c6.date_input("Starts", value=date.today())
                    ends = c7.date_input("Ends", value=date.today())
                    c8, c9 = st.columns(2)
                    piece_type = c8.text_input("What people take home", placeholder="ornament, pumpkin, cup…")
                    price_note = c9.text_input("Price note (optional)", placeholder="$45–85")
                    st.caption("**Glass IDs at the table** — none, optional, or every piece")
                    fp_mode = st.pills("Glass IDs", ["optional", "all", "none"], default="optional",
                                       selection_mode="single", label_visibility="collapsed") or "optional"
                    closes = st.date_input("Adding to collections closes", value=date.today() + _td(days=30))
                    show = st.checkbox("Show approved pieces on the public page for this show", value=True)
                    desc = st.text_area("Description (optional)")
                    created_ev = st.form_submit_button("Host a show or sale", type="primary")
                    st.caption("Creates it and gives you a link and a QR code for the table.")
                    if created_ev and t.strip():
                        try:
                            ev = _ev.create_event(
                                central, host_uid=uid, host_name=(studio.strip() or display),
                                host_email=user_email, title=t, studio=studio.strip(), venue=venue.strip(),
                                city=city.strip(), region=region.strip(), country=country.strip(),
                                starts=starts.isoformat(), ends=ends.isoformat(),
                                piece_type=piece_type.strip(), price_note=price_note.strip(),
                                fingerprinting=fp_mode, registration_closes=closes.isoformat(),
                                show_registrations=show, description=desc.strip())
                            st.success(f"Created. Buyers' link: {PUBLIC_BASE}/register.html?event={ev['slug']}")
                            st.rerun()
                        except ValueError as ex:
                            st.error(str(ex))

            evs = _ev.host_events(central, uid)
            if not evs:
                st.info("No shows or sales yet. Host one above — you get a link and a QR code for the "
                        "table, and what buyers add to their collections shows up here for you to approve.")
            for ev in evs:
                c = ev["counts"]
                head = (f"{ev['title']} · {ev['starts'] or '—'} · {c['pending_host']} to review · "
                        f"{c['approved']} published · {ev['status']}")
                with st.expander(head, expanded=bool(c["pending_host"])):
                    url = f"{PUBLIC_BASE}/register.html?event={ev['slug']}"
                    qcol, icol = st.columns([1, 2])
                    with qcol:
                        try:
                            import io as _io

                            import segno
                            buf = _io.BytesIO()
                            segno.make(url, error="m").save(buf, kind="png", scale=6, border=2,
                                                            dark="#1F1A16", light="#F4EEE4")
                            st.image(buf.getvalue(), width=200)
                        except Exception:  # noqa: BLE001
                            st.code(url, language=None)
                    with icol:
                        st.markdown(f"**Add to My Collection link** — print the QR for the table, or share:\n\n`{url}`")
                        listing = {"approved": "✅ listed under Shows & Sales",
                                   "rejected": "✖ listing not approved (the link still works)"}.get(
                            ev["listing"], "🕐 listing waiting for site approval — the link works now")
                        st.caption(f"{listing} · Glass IDs: {ev['fingerprinting']} · "
                                   f"adding to collections {'open' if ev['can_register'] else 'closed'}"
                                   + (f" · closes {ev['registration_closes']}" if ev["registration_closes"] else ""))
                        b1, b2, b3 = st.columns(3)
                        if ev["status"] == "open" and b1.button("Close", key=f"ev_close_{ev['id']}"):
                            _ev.update_event(central, ev["slug"], uid, status="closed"); st.rerun()
                        if ev["status"] != "open" and b1.button("Reopen", key=f"ev_open_{ev['id']}"):
                            _ev.update_event(central, ev["slug"], uid, status="open"); st.rerun()
                        b1.caption("Stops or restarts buyers adding pieces from this show.")
                        b2.link_button("Public page", f"/events.html?event={ev['slug']}", use_container_width=True)
                        b2.caption("What everyone sees for this show.")
                        b3.link_button("Buyer page", f"/register.html?event={ev['slug']}", use_container_width=True)
                        b3.caption("Where buyers add what they bought to their collection.")

                    pending = _ev.registrations(central, ev["id"], "pending_host")
                    if pending:
                        st.markdown(f"**{len(pending)} waiting for you** — tick the ones that are real "
                                    "purchases from your show, then approve. Approved pieces go to the "
                                    "site's review queue before they're published.")
                        picked = []
                        for r in pending:
                            rc1, rc2, rc3 = st.columns([0.5, 3, 1])
                            if rc1.checkbox("", key=f"evsel_{r['id']}", label_visibility="collapsed"):
                                picked.append(r["id"])
                            when = (r["created_at"] or "")[:16].replace("T", " ")
                            rc2.markdown(f"**{r['title']}**" + (f" · #{r['piece_ref']}" if r["piece_ref"] else "")
                                         + f"  \n{r['buyer']} · {r.get('buyer_email') or ''} · {when}"
                                         + (f"  \n_{r['description']}_" if r.get("description") else "")
                                         + (f"  \npaid: {r['price_paid']}" if r.get("price_paid") else ""))
                            if r.get("image_b64"):
                                try:
                                    rc3.image(_b64.b64decode(r["image_b64"]), width=96)
                                except Exception:  # noqa: BLE001
                                    pass
                        a1, a2, a3 = st.columns(3)
                        if a1.button(f"✅ Approve selected ({len(picked)})", key=f"ev_ap_{ev['id']}",
                                     disabled=not picked, type="primary"):
                            n = _ev.host_review(central, ev["id"], uid, picked, True)
                            st.success(f"Approved {n} — sent to the site's review queue."); st.rerun()
                        a1.caption("The ticked pieces go on to the site's review queue.")
                        if a2.button(f"Approve all {len(pending)}", key=f"ev_apall_{ev['id']}"):
                            n = _ev.host_approve_all(central, ev["id"], uid)
                            st.success(f"Approved {n} — sent to the site's review queue."); st.rerun()
                        a2.caption("Every piece waiting here goes on to the review queue.")
                        if a3.button("✖ Reject selected", key=f"ev_rj_{ev['id']}", disabled=not picked):
                            _ev.host_review(central, ev["id"], uid, picked, False); st.rerun()
                        a3.caption("The ticked pieces are not confirmed and won't be published.")
                    else:
                        st.caption("Nothing waiting for you.")
                    tail = [f"{c['host_approved']} waiting for the site" if c["host_approved"] else "",
                            f"{c['approved']} published" if c["approved"] else "",
                            f"{c['rejected_host'] + c['rejected']} rejected" if (c["rejected_host"] + c["rejected"]) else ""]
                    tail = [x for x in tail if x]
                    if tail:
                        st.caption(" · ".join(tail))
                    if c["approved"]:
                        with st.expander("Published pieces"):
                            for r in _ev.registrations(central, ev["id"], "approved"):
                                st.markdown(f"- **{r['title']}**" + (f" · #{r['piece_ref']}" if r["piece_ref"] else "")
                                            + f" · {r['buyer']}"
                                            + (f" · [view]({r['object_url']})" if r.get("object_url") else ""))

        with tab_mine:
            regs = _ev.my_registrations(central, uid)
            if not regs:
                st.info("Nothing in your collection yet. Bought a piece at a show or sale? "
                        "[Add it to your collection](/register.html) and it shows up here and in My pieces.")
            for r in regs:
                label = {"pending_host": "🕐 waiting for the host", "host_approved": "🕐 host approved — waiting for the site",
                         "approved": "✅ published", "rejected_host": "✖ not confirmed by the host",
                         "rejected": "✖ not published"}.get(r["status"], r["status"])
                st.markdown(f"**{r['title']}**" + (f" · #{r['piece_ref']}" if r["piece_ref"] else "")
                            + f" — {r['event_title']} ({r['host_name']}) · {label}")
                links = []
                if r.get("object_id"):
                    links.append(f"[Register it for a Glass ID](/fingerprint/enroll.html?object={r['object_id']})")
                    links.append(f"[Open the piece](/glowtbook/?page=objects&object={r['object_id']})")
                if r.get("object_url"):
                    links.append(f"[View in the registry]({r['object_url']})")
                if links:
                    st.caption(" · ".join(links))

# ===========================================================================
else:  # My profile
    st.header("My profile")
    st.caption("How you appear under Find Artists & Studios once an admin approves it.")
    p = profile_row()
    name = st.text_input("Display name", value=p["display_name"])
    web = st.text_input("Website / social", value=p["website"])
    nat = st.text_area("Nationality / base", value=p["nationality_base"])
    techs = st.multiselect("Techniques", TECHNIQUES,
                           default=[t for t in (p["techniques"] or "").split("|") if t])
    contribs = st.multiselect("Kinds of work", CONTRIBUTIONS,
                              default=[t for t in (p["contributions"] or "").split("|") if t])
    hi = st.text_area("Career highlights", value=p["career_highlights"])
    save_profile = st.button("Save profile", type="primary")
    st.caption("Keeps your profile in your notebook.")
    if save_profile:
        conn.execute("""UPDATE profile SET display_name=?,website=?,nationality_base=?,
                        techniques=?,contributions=?,career_highlights=? WHERE user_id=?""",
                     (name, web, nat, "|".join(techs), "|".join(contribs), hi, uid))
        conn.commit(); st.success("Saved.")

    # --- connected accounts: Bluesky posts for you from the Share buttons and on publish ---
    st.divider()
    st.subheader("Connected accounts")
    st.caption("Connect Bluesky once and every Share button on the site can post for you; "
               "the post carries the share card and a link back to the piece.")
    _sc = _central()
    try:
        from central import social as _social
    except Exception:  # noqa: BLE001
        _social = None
    if _sc is None or _social is None:
        st.info("Connected accounts aren't available on this server yet.")
    else:
        _bsky = _social.status(_sc, uid).get("bluesky") or {"connected": False}
        _msg = st.session_state.pop("social_msg", None)
        if _msg:
            st.success(_msg)
        if not _bsky.get("connected"):
            with st.form("bsky_connect"):
                st.markdown("**Bluesky**")
                bsky_handle = st.text_input("Handle", placeholder="you.bsky.social")
                bsky_pw = st.text_input("App password", type="password")
                st.caption("Create an app password in Bluesky → Settings → App Passwords. It is stored "
                           "encrypted and only used to post for you; revoke it there any time.")
                if st.form_submit_button("Connect Bluesky", type="primary"):
                    try:
                        _social.connect_bluesky(_sc, uid, bsky_handle, bsky_pw)
                        st.session_state["social_msg"] = "Bluesky connected."
                        st.rerun()
                    except ValueError as ex:
                        st.error(str(ex))
                st.caption("Checks the handle and app password with Bluesky, then keeps them encrypted here.")
        else:
            st.markdown(f"**Bluesky** · @{_bsky.get('handle', '')}")
            _last = _bsky.get("last_used")
            st.caption(("Last used " + str(_last)[:16].replace("T", " ")) if _last else "Not used yet.")
            if _bsky.get("last_error"):
                st.warning(f"The last post failed: {_bsky['last_error']}")
            b1, b2 = st.columns(2)
            if b1.button("Post a test"):
                _creds = _social.credentials(_sc, uid, "bluesky")
                try:
                    from glowtbook import bluesky as _bsky_mod
                    _url = _bsky_mod.post(_creds[0], _creds[1], "Testing my Glass Database connection")
                    _social.mark_used(_sc, uid, "bluesky")
                    _social.log_share(_sc, uid, "bluesky", "test", "", _url)
                    st.session_state["social_msg"] = f"Posted: {_url}"
                    st.rerun()
                except Exception as ex:  # noqa: BLE001
                    _social.mark_used(_sc, uid, "bluesky", str(ex))
                    st.error(f"Bluesky didn't take the post: {ex}")
            b1.caption("Posts “Testing my Glass Database connection” from your account.")
            if b2.button("Disconnect"):
                _social.disconnect(_sc, uid, "bluesky")
                st.session_state["social_msg"] = "Bluesky disconnected."
                st.rerun()
            b2.caption("Forgets the app password here; revoke it in Bluesky too if you like.")

        # --- share my collection: a public page at an unguessable link ---
        st.divider()
        st.subheader("Share my collection")
        _sh = _social.my_collection_share(_sc, uid)
        if not _sh:
            st.write("Your collection is private. Make it public and you get a page anyone can open: "
                     "your pieces, their makers and the collecting events, with your name on it and nothing else.")
            if st.button("Make my collection public"):
                _social.share_collection(_sc, uid, (p["display_name"] or display or "").strip())
                st.session_state["social_msg"] = "Your collection is public."
                st.rerun()
            st.caption("Creates a link anyone can open; you can withdraw it any time.")
        else:
            _curl = f"{PUBLIC_BASE}/collection.html?c={_sh['share_id']}"
            st.write("Your collection is public at")
            st.code(_curl, language=None)
            st.caption("Copy the link; wherever you paste it, the preview shows the card. "
                       f"[Open the page]({_curl}) · "
                       f"[The share card]({PUBLIC_BASE}/api/social/collections/{_sh['share_id']}/card.png)")
            if st.button("Withdraw the link"):
                _social.unshare_collection(_sc, uid)
                st.session_state["social_msg"] = "Link withdrawn."
                st.rerun()
            st.caption("Takes the page down; the link stops working.")
