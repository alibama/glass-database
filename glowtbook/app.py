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

import base64
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os  # noqa: E402

from central.dbconn import connect as central_connect  # noqa: E402
from glowtbook import (
    aip,  # noqa: E402  (BagIt AIP + MinIO)
    c2pa_sign,  # noqa: E402  (Content Credentials signing)
    media,  # noqa: E402  (media.py sits beside this file)
    video,  # noqa: E402  (video transcode to condensed DIP)
)

DATA = Path(__file__).resolve().parent.parent / "data"
DEMO_DB = DATA / "glowtbook_demo.db"
MEDIA = DATA / "glowtbook_media"
DIP_MEDIA = DATA / "dip_media"        # transcoded DIP video, keyed by content hash

# When on (default), contributions land in a staging queue and only become
# public once an admin approves them. Set GLASSDB_MODERATION=0 to publish
# immediately (the earlier POC behaviour).
MODERATION = os.environ.get("GLASSDB_MODERATION", "1").lower() not in ("0", "false", "no")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://glassdatabase.org").rstrip("/")

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


def uid_slug(uid: str) -> str:
    return hashlib.sha1(uid.encode()).hexdigest()[:16]


def aip_dir(uid: str, object_id: int) -> Path:
    d = MEDIA / uid_slug(uid) / "aip" / str(object_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- central objects registry (the public DIP side) ------------------------
def ensure_central_objects(c):
    c.execute("""CREATE TABLE IF NOT EXISTS objects (
        _row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, _imported_at TEXT,
        title TEXT, maker TEXT, year TEXT, techniques TEXT, materials TEXT, dimensions TEXT,
        description TEXT, contributor TEXT, sourcing TEXT, value_display TEXT DEFAULT '',
        has_credentials INTEGER DEFAULT 0, manifest_json TEXT, content_hash TEXT, published_at TEXT)""")
    # tolerate an older objects table from a previous deploy
    if "has_credentials" not in {r[1] for r in c.execute("PRAGMA table_info(objects)")}:
        c.execute("ALTER TABLE objects ADD COLUMN has_credentials INTEGER DEFAULT 0")
    c.execute("""CREATE TABLE IF NOT EXISTS object_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, object_row_id TEXT, role TEXT, caption TEXT,
        image_b64 TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    now = datetime.now(timezone.utc).isoformat()
    n = c.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    c.execute("""INSERT OR REPLACE INTO _datasets
        (tbl,domain,source_file,source_sheet,visibility,row_count,description,updated_at)
        VALUES ('objects','objects','glowtbook','contributions','public',?,
                'Provenance-tracked glass objects contributed via Glowtbook (condensed DIP)',?)""",
        (n, now))
    for i, col in enumerate(["title", "maker", "year", "techniques", "materials",
                             "dimensions", "description", "contributor", "sourcing",
                             "value_display", "content_hash"]):
        c.execute("""INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public)
                     VALUES ('objects',?,?,?,1)""", (col, col.replace("_", " ").title(), i))
    c.commit()


def ensure_object_staging(c):
    """Moderation queue for object contributions. NOT registered in _datasets, so
    nothing here is ever served by the public API or explorer until approved."""
    c.execute("""CREATE TABLE IF NOT EXISTS object_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT UNIQUE,
        submitted_uid TEXT, submitted_by TEXT,
        title TEXT, maker TEXT, year TEXT, techniques TEXT, materials TEXT, dimensions TEXT,
        description TEXT, sourcing TEXT, value_display TEXT DEFAULT '',
        has_credentials INTEGER DEFAULT 0, manifest_json TEXT,
        status TEXT DEFAULT 'pending', review_note TEXT,
        received_at TEXT DEFAULT (datetime('now')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS object_submission_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER, role TEXT, caption TEXT,
        image_b64 TEXT)""")
    c.commit()


def contribute_object(uid, display, obj, events, images, include_value, sign=False,
                      object_id=None, archive_aip=False, push_minio=False):
    """Build the DIP (condense images + optional video transcode + manifest,
    optionally C2PA-signed) and publish/stage. Optionally archive a full-fidelity
    BagIt AIP (and push it to MinIO). Returns (manifest, primary_image_bytes)."""
    c = central_connect()
    ensure_central_objects(c)
    techniques = [t for t in (obj["techniques"] or "").split("|") if t]
    ingredients, conds = [], []
    video_mp4 = None                      # transcoded DIP rendition, if any
    originals = []                        # full-fidelity files for the AIP bag
    for im in images:
        p = Path(im["aip_path"])
        if p.exists():
            originals.append(p)
        if im["role"] == "video":
            if video.available():
                try:
                    mp4, poster, _ = video.transcode(p)
                    video_mp4 = mp4
                    pcond = media.condense_image(poster)   # poster is a DIP image
                    ingredients.append({"title": p.name + ".poster.jpg",
                                        "hash": media.sha256_hex(pcond), "role": "video-poster"})
                    conds.append(("video-poster", im.get("caption", "") or "video still", pcond))
                except Exception:
                    pass  # transcode failed -> this video stays AIP-only
            continue
        dip = media.condense_image(p.read_bytes())
        ingredients.append({"title": p.name, "hash": media.sha256_hex(dip), "role": im["role"]})
        conds.append((im["role"], im.get("caption", ""), dip))
    manifest = media.build_manifest(dict(obj), [dict(e) for e in events], ingredients,
                                    techniques, include_value, contributor=display)

    # Optionally embed Content Credentials (C2PA) in each condensed image
    do_sign = bool(sign) and c2pa_sign.available()
    prov = {"content_hash": manifest["content_hash"], "sourcing": manifest["sourcing"],
            "contributor": display,
            "events": next((a["data"] for a in manifest["assertions"]
                            if a["label"] == "glassdb.provenance.events"), [])}
    condensed = []          # (role, caption, b64)
    primary_bytes = None     # for optional Bluesky post / receipts
    for role, cap, dip in conds:
        out = dip
        if do_sign:
            try:
                out = c2pa_sign.sign_jpeg(dip, obj["title"], obj["maker"] or display, prov)
            except Exception:
                do_sign = False  # fall back to unsigned for the whole batch
                out = dip
        if primary_bytes is None or role in ("primary", "video-poster"):
            primary_bytes = out
        condensed.append((role, cap, base64.b64encode(out).decode()))
    manifest["signature"] = "c2pa:es256 (self-signed test cert)" if do_sign else None
    manifest["has_video"] = video_mp4 is not None

    # write the transcoded DIP video keyed by content hash (served after approval)
    if video_mp4:
        DIP_MEDIA.mkdir(parents=True, exist_ok=True)
        (DIP_MEDIA / f"{manifest['content_hash']}.mp4").write_bytes(video_mp4)

    now = datetime.now(timezone.utc).isoformat()
    val_disp = ""
    if include_value and obj["value_amount"]:
        val_disp = f'{obj["value_amount"]} {obj["value_currency"]}'

    if MODERATION:
        # stage for admin review; nothing here is public until approved
        ensure_object_staging(c)
        c.execute("DELETE FROM object_submissions WHERE content_hash=?", (manifest["content_hash"],))
        cur = c.execute("""INSERT INTO object_submissions
            (content_hash,submitted_uid,submitted_by,title,maker,year,techniques,materials,
             dimensions,description,sourcing,value_display,has_credentials,manifest_json,status,received_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
            (manifest["content_hash"], uid, display, obj["title"], obj["maker"], obj["year"],
             ", ".join(techniques), obj["materials"], obj["dimensions"], obj["description"],
             "self-reported", val_disp, int(do_sign),
             json.dumps(manifest, ensure_ascii=False), now))
        sub_id = cur.lastrowid
        for role, cap, b64 in condensed:
            c.execute("INSERT INTO object_submission_images (submission_id,role,caption,image_b64) VALUES (?,?,?,?)",
                      (sub_id, role, cap, b64))
        c.commit()
        manifest["_pending"] = True
    else:
        rid = hashlib.sha1(("obj|" + uid + "|" + manifest["content_hash"]).encode()).hexdigest()[:16]
        c.execute("""INSERT OR REPLACE INTO objects
            (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,techniques,materials,
             dimensions,description,contributor,sourcing,value_display,has_credentials,manifest_json,content_hash,published_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "glowtbook", "contributions", now, obj["title"], obj["maker"], obj["year"],
             ", ".join(techniques), obj["materials"], obj["dimensions"], obj["description"],
             display, "self-reported", val_disp, int(do_sign),
             json.dumps(manifest, ensure_ascii=False), manifest["content_hash"], now))
        c.execute("DELETE FROM object_images WHERE object_row_id=?", (rid,))
        for role, cap, b64 in condensed:
            c.execute("INSERT INTO object_images (object_row_id,role,caption,image_b64) VALUES (?,?,?,?)",
                      (rid, role, cap, b64))
        c.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM objects) WHERE tbl='objects'")
        c.commit()
        from central import approvals as _appr
        _appr.ensure_approvals(c)
        _appr.set_status(c, "objects", [rid], "approved")   # immediate publish path
        manifest["_pending"] = False

    # Optionally archive the full-fidelity AIP as a BagIt bag (+ MinIO)
    aip_receipt = None
    if archive_aip and originals:
        aip_meta = {
            "object_id": object_id, "content_hash": manifest["content_hash"],
            "title": obj["title"], "maker": obj["maker"], "year": obj["year"],
            "materials": obj["materials"], "dimensions": obj["dimensions"],
            "techniques": techniques, "description": obj["description"],
            "events": [dict(e) for e in events],
            "value": ({"amount": obj["value_amount"], "currency": obj["value_currency"],
                       "insurer": obj["insurer"], "policy_no": obj["policy_no"]}
                      if obj["value_amount"] else None),   # private — AIP only
            "manifest": {k: v for k, v in manifest.items() if not k.startswith("_")},
        }
        try:
            aip_receipt = aip.archive_object_aip(
                object_id or manifest["content_hash"], manifest["content_hash"],
                originals, aip_meta, DATA / "aip_bags",
                push=bool(push_minio) and aip.minio_config() is not None)
        except Exception as ex:  # noqa: BLE001
            aip_receipt = {"error": str(ex)}

    manifest["_signed"] = do_sign
    manifest["_aip"] = aip_receipt
    return manifest, primary_bytes


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
                conn.execute("""INSERT INTO object (user_id,title,maker,year,techniques,materials,dimensions,description)
                                VALUES (?,?,?,?,?,?,?,?)""",
                             (uid, title, maker, year, "|".join(techs), materials, dims, desc))
                conn.commit(); st.success("Created."); st.rerun()

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
                conn.execute("""UPDATE object SET title=?,maker=?,year=?,techniques=?,materials=?,
                                dimensions=?,description=?,value_amount=?,value_currency=?,insured=?,
                                updated_at=datetime('now') WHERE id=? AND user_id=?""",
                             (title, maker, year, "|".join(techs), materials, dims, desc,
                              vamt, vcur, int(insured), oid, uid))
                conn.commit(); st.success("Saved.")

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
                    conn.execute("INSERT INTO object_image (object_id,user_id,role,aip_path,caption) VALUES (?,?,?,?,?)",
                                 (oid, uid, role, str(fp), cap))
                    saved += 1
                if shot is not None:
                    fp = d / f"capture-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
                    fp.write_bytes(shot.getvalue())
                    conn.execute("INSERT INTO object_image (object_id,user_id,role,aip_path,caption) VALUES (?,?,?,?,?)",
                                 (oid, uid, role, str(fp), cap))
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
                    conn.execute("""INSERT INTO prov_event (object_id,user_id,event_type,event_date,actor,location,note,value_amount,value_currency)
                                    VALUES (?,?,?,?,?,?,?,?,?)""",
                                 (oid, uid, et, ed, actor, eloc, note, eva, evc))
                    conn.commit(); st.success("Added."); st.rerun()
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
