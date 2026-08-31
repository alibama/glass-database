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
import sqlite3
import sys
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from glowtbook import (
    aip,  # noqa: E402  (BagIt AIP + MinIO)
    c2pa_sign,  # noqa: E402  (Content Credentials signing)
    media,  # noqa: E402  (media.py sits beside this file)
    video,  # noqa: E402  (video transcode to condensed DIP)
)
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

st.set_page_config(page_title="Glowtbook", page_icon="🔥", layout="centered")

# Mobile polish: tighter padding, comfortable tap targets when installed as a PWA.
st.markdown("""<style>
@media (max-width: 640px) {
  .block-container {padding: 1rem 0.9rem 3rem !important;}
  .stButton>button, .stDownloadButton>button {width: 100%; padding: 0.6rem 1rem;}
  [data-testid="stFileUploaderDropzone"] {padding: 0.75rem;}
}
</style>""", unsafe_allow_html=True)


# --- per-user store --------------------------------------------------------
@st.cache_resource
def store() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DEMO_DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE IF NOT EXISTS profile (
        user_id TEXT PRIMARY KEY, display_name TEXT DEFAULT '', website TEXT DEFAULT '',
        nationality_base TEXT DEFAULT '', status TEXT DEFAULT 'active',
        techniques TEXT DEFAULT '', contributions TEXT DEFAULT '',
        career_highlights TEXT DEFAULT '');
      CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY, user_id TEXT, entry_date TEXT, title TEXT, body TEXT,
        tags TEXT DEFAULT '', location TEXT DEFAULT '', technique TEXT DEFAULT '',
        image_path TEXT DEFAULT '', object_id INTEGER,
        created_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE IF NOT EXISTS object (
        id INTEGER PRIMARY KEY, user_id TEXT, title TEXT, maker TEXT DEFAULT '',
        year TEXT DEFAULT '', techniques TEXT DEFAULT '', materials TEXT DEFAULT '',
        dimensions TEXT DEFAULT '', description TEXT DEFAULT '',
        acquired TEXT DEFAULT '', current_location TEXT DEFAULT '',
        value_amount TEXT DEFAULT '', value_currency TEXT DEFAULT 'USD', insured INTEGER DEFAULT 0,
        status TEXT DEFAULT 'in collection',
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE IF NOT EXISTS object_image (
        id INTEGER PRIMARY KEY, object_id INTEGER, user_id TEXT, role TEXT DEFAULT 'photo',
        aip_path TEXT, caption TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE IF NOT EXISTS prov_event (
        id INTEGER PRIMARY KEY, object_id INTEGER, user_id TEXT, event_type TEXT,
        event_date TEXT, actor TEXT DEFAULT '', location TEXT DEFAULT '', note TEXT DEFAULT '',
        value_amount TEXT DEFAULT '', value_currency TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE IF NOT EXISTS contribution (
        id INTEGER PRIMARY KEY, user_id TEXT, object_id INTEGER, content_hash TEXT,
        manifest_json TEXT, created_at TEXT DEFAULT (datetime('now')));
    """)
    c.commit()
    return c






# --- central objects registry (the public DIP side) ------------------------






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
        st.write("Sign in to start your **private** journal and object registry.")
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
if is_demo:
    st.sidebar.warning("Shared demo — not private.")
else:
    st.sidebar.success(f"Signed in as {display}")
    if st.sidebar.button("Log out"):
        st.logout()
page = st.sidebar.radio("Go to", ["Home", "Journal", "Objects", "My profile"],
                        label_visibility="collapsed")


# ===========================================================================
if page == "Home":
    st.header("Glowtbook")
    st.markdown(
        "A journal and **object-provenance** registry for people who work in glass.\n\n"
        "Your **journal** and **objects** live here privately. Contributing an object "
        "keeps your full-resolution originals and private values on this machine (the "
        "archival copy) and shares only a **condensed** version with a provenance record."
    )
    if is_demo:
        st.warning("Shared demo — sign-in gives each person a private space.")

# ===========================================================================
elif page == "Journal":
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
        if st.form_submit_button("Save entry") and (title or body):
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
elif page == "Objects":
    st.header("Objects & provenance")
    st.caption("Track pieces for provenance, value, insurance and history. "
               "Full-resolution originals and any values stay on this machine.")

    with st.expander("＋ New object", expanded=False):
        with st.form("newobj", clear_on_submit=True):
            title = st.text_input("Title *")
            c1, c2 = st.columns(2)
            maker = c1.text_input("Maker")
            year = c2.text_input("Year")
            techs = st.multiselect("Techniques", TECHNIQUES)
            c3, c4 = st.columns(2)
            materials = c3.text_input("Materials")
            dims = c4.text_input("Dimensions")
            desc = st.text_area("Description")
            if st.form_submit_button("Create object", type="primary") and title.strip():
                create_object(conn, uid, title=title, maker=maker, year=year,
                              techniques="|".join(techs), materials=materials,
                              dimensions=dims, description=desc)
                st.success("Created."); st.rerun()

    objs = conn.execute("SELECT * FROM object WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    if not objs:
        st.info("No objects yet — add one above.")
    labels = {o["id"]: f'{o["title"]} ({o["year"] or "—"})' for o in objs}
    if objs:
        oid = st.selectbox("Open object", [o["id"] for o in objs], format_func=lambda i: labels[i])
        o = conn.execute("SELECT * FROM object WHERE id=? AND user_id=?", (oid, uid)).fetchone()

        tab_meta, tab_media, tab_prov, tab_contrib = st.tabs(
            ["Details", "Images", "Provenance", "Contribute"])

        with tab_meta:
            title = st.text_input("Title", value=o["title"])
            c1, c2 = st.columns(2)
            maker = c1.text_input("Maker", value=o["maker"])
            year = c2.text_input("Year", value=o["year"])
            techs = st.multiselect("Techniques", TECHNIQUES,
                                   default=[t for t in (o["techniques"] or "").split("|") if t])
            materials = st.text_input("Materials", value=o["materials"])
            dims = st.text_input("Dimensions", value=o["dimensions"])
            desc = st.text_area("Description", value=o["description"])
            st.markdown("**Value & insurance** 🔒 _private — never contributed unless you opt in_")
            c5, c6, c7 = st.columns(3)
            vamt = c5.text_input("Value", value=o["value_amount"])
            vcur = c6.text_input("Currency", value=o["value_currency"] or "USD")
            insured = c7.checkbox("Insured", value=bool(o["insured"]))
            if st.button("Save details", type="primary"):
                update_object(conn, oid, uid, title=title, maker=maker, year=year,
                              techniques="|".join(techs), materials=materials, dimensions=dims,
                              description=desc, value_amount=vamt, value_currency=vcur, insured=insured)
                st.success("Saved.")

        with tab_media:
            ups = st.file_uploader("Add images (originals kept local as the archival copy)",
                                   type=["jpg", "jpeg", "png"], accept_multiple_files=True)
            with st.expander("📷 Take a photo (mobile)"):
                shot = st.camera_input("Capture the piece")
            role = st.selectbox("Role", ["primary", "photo", "detail"])
            cap = st.text_input("Caption")
            if st.button("Save images", disabled=not (ups or shot)):
                d = aip_dir(uid, oid)
                saved = 0
                for u in (ups or []):
                    fp = d / u.name; fp.write_bytes(u.getvalue())
                    add_image(conn, oid, uid, role=role, aip_path=fp, caption=cap)
                    saved += 1
                if shot is not None:
                    fp = d / f"capture-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
                    fp.write_bytes(shot.getvalue())
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
                if st.form_submit_button("Add event", type="primary"):
                    add_event(conn, oid, uid, event_type=et, event_date=ed, actor=actor,
                              location=eloc, note=note, value_amount=eva, value_currency=evc)
                    st.success("Added."); st.rerun()
            evs = conn.execute("SELECT * FROM prov_event WHERE object_id=? AND user_id=? ORDER BY event_date, id", (oid, uid)).fetchall()
            for e in evs:
                st.markdown(f"**{e['event_type']}** — {e['event_date'] or '?'}  ·  {e['actor']}")
                if e["note"]: st.caption(e["note"])

        with tab_contrib:
            st.write("Publish a **condensed** version of this object and its provenance to the "
                     "public registry. Your originals and any values stay here."
                     + ("  Submissions are **reviewed by an admin** before they appear publicly."
                        if MODERATION else ""))
            imgs = conn.execute("SELECT * FROM object_image WHERE object_id=? AND user_id=?", (oid, uid)).fetchall()
            evs = conn.execute("SELECT * FROM prov_event WHERE object_id=? AND user_id=? ORDER BY event_date, id", (oid, uid)).fetchall()
            incl_val = st.checkbox("Include value/insurance in the public record", value=False)
            can_sign = c2pa_sign.available()
            sign = st.checkbox("Embed Content Credentials (C2PA) in each image", value=can_sign,
                               disabled=not can_sign,
                               help="Cryptographically signs a provenance manifest into every image "
                                    "so it travels with the file.")
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
                       f"{len(evs)} provenance event(s) included{vid_note}")

            minio_on = aip.minio_config() is not None
            with st.expander("Archive full-fidelity AIP (BagIt" + (" → MinIO" if minio_on else "") + ")"):
                archive_aip = st.checkbox("Bag the originals as a BagIt AIP (sha256 + sha512 fixity)",
                                          value=False)
                push_minio = st.checkbox("Also push the bag to MinIO",
                                         value=False, disabled=not minio_on,
                                         help=None if minio_on else "Set MINIO_* env vars to enable.")
                if not minio_on:
                    st.caption("MinIO not configured — bags are written locally under data/aip_bags/.")

            with st.expander("Also post to Bluesky (optional receipt)"):
                bsky_on = st.checkbox("Post this object to Bluesky when I publish")
                bsky_handle = st.text_input("Handle", placeholder="you.bsky.social")
                bsky_pw = st.text_input("App password", type="password",
                                        help="Create one in Bluesky → Settings → App Passwords. Not stored.")

            a = st.checkbox("I want this object contributed to the public, CC-licensed registry.")
            b = st.checkbox("The provenance I've recorded is accurate to the best of my knowledge.")
            btn_label = "Submit for review" if MODERATION else "Publish condensed version"
            if st.button(btn_label, type="primary", disabled=not (a and b and o["title"])):
                try:
                    manifest, primary_bytes = contribute_object(
                        uid, display, o, evs,
                        [{"aip_path": im["aip_path"], "role": im["role"], "caption": im["caption"]}
                         for im in imgs if Path(im["aip_path"]).exists()],
                        incl_val, sign=sign, object_id=oid,
                        archive_aip=archive_aip, push_minio=push_minio)
                    conn.execute("INSERT INTO contribution (user_id,object_id,content_hash,manifest_json) VALUES (?,?,?,?)",
                                 (uid, oid, manifest["content_hash"], json.dumps(manifest)))
                    conn.commit()
                    signed = manifest.get("_signed")
                    cred = " · Content Credentials embedded 🔐" if signed else ""
                    if manifest.get("_pending"):
                        st.success(f"Submitted for review (ref {manifest['content_hash']}){cred}. "
                                   "An admin will approve it before it appears publicly.")
                    else:
                        st.success(f"Published (ref {manifest['content_hash']}){cred}. "
                                   "Visible in Explore → Objects.")
                    if manifest.get("has_video"):
                        st.caption("Video transcoded to a condensed MP4 (+ poster) for the public rendition.")
                    st.download_button("⬇ Download provenance manifest",
                                       json.dumps({k: v for k, v in manifest.items() if not k.startswith("_")},
                                                  indent=2, ensure_ascii=False),
                                       file_name=f"{manifest['content_hash']}.manifest.json",
                                       mime="application/json")
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
                    if bsky_on and bsky_handle and bsky_pw:
                        from glowtbook import bluesky
                        try:
                            url = bluesky.publish_object(
                                bsky_handle.strip(), bsky_pw,
                                title=o["title"], maker=o["maker"] or display,
                                content_hash=manifest["content_hash"],
                                image_bytes=primary_bytes,          # the actual provenance image
                                link=f"{PUBLIC_BASE}/explore")
                            st.success(f"Posted to Bluesky (with image): {url}")
                            if signed:
                                st.caption("Note: Bluesky re-encodes images, which usually strips the "
                                           "C2PA credential from the copy it hosts. The verifiable "
                                           "original stays in the registry.")
                        except Exception as bex:  # noqa: BLE001
                            st.warning(f"Saved here, but Bluesky post failed: {bex}")
                except Exception as ex:  # noqa: BLE001
                    st.error(f"Couldn't submit: {ex}")

# ===========================================================================
else:  # My profile
    st.header("My registry profile")
    p = profile_row()
    name = st.text_input("Display name", value=p["display_name"])
    web = st.text_input("Website / social", value=p["website"])
    nat = st.text_area("Nationality / base", value=p["nationality_base"])
    techs = st.multiselect("Techniques", TECHNIQUES,
                           default=[t for t in (p["techniques"] or "").split("|") if t])
    contribs = st.multiselect("Kinds of work", CONTRIBUTIONS,
                              default=[t for t in (p["contributions"] or "").split("|") if t])
    hi = st.text_area("Career highlights", value=p["career_highlights"])
    if st.button("Save profile", type="primary"):
        conn.execute("""UPDATE profile SET display_name=?,website=?,nationality_base=?,
                        techniques=?,contributions=?,career_highlights=? WHERE user_id=?""",
                     (name, web, nat, "|".join(techs), "|".join(contribs), hi, uid))
        conn.commit(); st.success("Saved.")
