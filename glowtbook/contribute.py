"""
glowtbook.contribute
====================
UI-agnostic contribution logic: the local object CRUD and the central publish
pipeline (AIP/DIP, C2PA signing, video transcode, BagIt AIP, moderation gate).

No Streamlit here — every function takes a database connection or plain values,
so the same logic backs the Streamlit app today and can back a Gradio surface,
a write API, or a native client tomorrow.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from central.dbconn import connect as central_connect
from glowtbook import aip, c2pa_sign, media, video

DATA = Path(__file__).resolve().parent.parent / "data"
MEDIA = DATA / "glowtbook_media"
DIP_MEDIA = DATA / "dip_media"        # transcoded DIP video, keyed by content hash

# When on (default), contributions land in a staging queue and only become
# public once an admin approves them. Set GLASSDB_MODERATION=0 to publish
# immediately (the earlier POC behaviour).
MODERATION = os.environ.get("GLASSDB_MODERATION", "1").lower() not in ("0", "false", "no")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://glassdatabase.org").rstrip("/")


# --- local object CRUD (per-user journal DB) -------------------------------
def create_object(conn, uid, *, title, maker="", year="", techniques="",
                  materials="", dimensions="", description=""):
    cur = conn.execute(
        """INSERT INTO object (user_id,title,maker,year,techniques,materials,dimensions,description)
           VALUES (?,?,?,?,?,?,?,?)""",
        (uid, title, maker, year, techniques, materials, dimensions, description))
    conn.commit()
    return cur.lastrowid


def update_object(conn, oid, uid, *, title, maker, year, techniques, materials,
                  dimensions, description, value_amount="", value_currency="USD", insured=0):
    conn.execute(
        """UPDATE object SET title=?,maker=?,year=?,techniques=?,materials=?,
           dimensions=?,description=?,value_amount=?,value_currency=?,insured=?,
           updated_at=datetime('now') WHERE id=? AND user_id=?""",
        (title, maker, year, techniques, materials, dimensions, description,
         value_amount, value_currency, int(insured), oid, uid))
    conn.commit()


def add_event(conn, oid, uid, *, event_type, event_date="", actor="", location="",
              note="", value_amount="", value_currency="USD"):
    conn.execute(
        """INSERT INTO prov_event (object_id,user_id,event_type,event_date,actor,location,note,value_amount,value_currency)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (oid, uid, event_type, event_date, actor, location, note, value_amount, value_currency))
    conn.commit()


def add_image(conn, oid, uid, *, role, aip_path, caption=""):
    conn.execute(
        "INSERT INTO object_image (object_id,user_id,role,aip_path,caption) VALUES (?,?,?,?,?)",
        (oid, uid, role, str(aip_path), caption))
    conn.commit()


def uid_slug(uid: str) -> str:
    return hashlib.sha1(uid.encode()).hexdigest()[:16]

def aip_dir(uid: str, object_id: int) -> Path:
    d = MEDIA / uid_slug(uid) / "aip" / str(object_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

def ensure_central_objects(c):
    c.execute("""CREATE TABLE IF NOT EXISTS objects (
        _row_id TEXT PRIMARY KEY, _source_file TEXT, _source_sheet TEXT, _imported_at TEXT,
        title TEXT, maker TEXT, year TEXT, techniques TEXT, materials TEXT, dimensions TEXT,
        description TEXT, contributor TEXT, sourcing TEXT, value_display TEXT DEFAULT '',
        has_credentials INTEGER DEFAULT 0, manifest_json TEXT, content_hash TEXT, published_at TEXT,
        fingerprint_json TEXT, fingerprint_rating INTEGER, fingerprint_tier TEXT)""")
    # tolerate an older objects table from a previous deploy
    have = {r[1] for r in c.execute("PRAGMA table_info(objects)")}
    for col, decl in [("has_credentials", "INTEGER DEFAULT 0"), ("fingerprint_json", "TEXT"),
                      ("fingerprint_rating", "INTEGER"), ("fingerprint_tier", "TEXT")]:
        if col not in have:
            c.execute(f"ALTER TABLE objects ADD COLUMN {col} {decl}")
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


def promote_object_submission(conn, submission_id) -> str | None:
    """Promote a pending object submission into the public objects + object_images
    tables and approve it through the gate. Shared by the admin console and the
    one-click Discord approve link. Returns the new object row id, or None."""
    from central import approvals
    sub = conn.execute("SELECT * FROM object_submissions WHERE id=?", (submission_id,)).fetchone()
    if not sub:
        return None
    sub = dict(sub)
    ensure_central_objects(conn)
    now = datetime.now(timezone.utc).isoformat()
    rid = hashlib.sha1(("obj|" + (sub.get("content_hash") or str(submission_id))).encode()).hexdigest()[:16]
    conn.execute("""INSERT OR REPLACE INTO objects
        (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,techniques,materials,
         dimensions,description,contributor,sourcing,value_display,has_credentials,manifest_json,content_hash,published_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, "glowtbook", "contributions", now, sub.get("title"), sub.get("maker"), sub.get("year"),
         sub.get("techniques"), sub.get("materials"), sub.get("dimensions"), sub.get("description"),
         sub.get("submitted_by"), sub.get("sourcing"), sub.get("value_display"),
         sub.get("has_credentials"), sub.get("manifest_json"), sub.get("content_hash"), now))
    conn.execute("DELETE FROM object_images WHERE object_row_id=?", (rid,))
    for im in conn.execute("SELECT role,caption,image_b64 FROM object_submission_images WHERE submission_id=?",
                           (submission_id,)).fetchall():
        conn.execute("INSERT INTO object_images (object_row_id,role,caption,image_b64) VALUES (?,?,?,?)",
                     (rid, im["role"], im["caption"], im["image_b64"]))
    conn.execute("UPDATE object_submissions SET status='approved' WHERE id=?", (submission_id,))
    try:
        conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM objects) WHERE tbl='objects'")
    except Exception:
        pass
    conn.commit()
    approvals.set_status(conn, "objects", [rid], "approved")
    return rid

def contribute_object(uid, display, obj, events, images, include_value, sign=False,
                      object_id=None, archive_aip=False, push_minio=False, base_url=""):
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
                    # a poster is a freshly extracted frame -> no parent (created)
                    conds.append(("video-poster", im.get("caption", "") or "video still",
                                  pcond, None, None))
                except Exception:
                    pass  # transcode failed -> this video stays AIP-only
            continue
        raw = p.read_bytes()
        dip = media.condense_image(raw)
        fmt = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        ingredients.append({"title": p.name, "hash": media.sha256_hex(dip), "role": im["role"]})
        conds.append((im["role"], im.get("caption", ""), dip, raw, fmt))   # raw = parent original
    manifest = media.build_manifest(dict(obj), [dict(e) for e in events], ingredients,
                                    techniques, include_value, contributor=display)

    # physical re-identification fingerprint (optional; travels in the manifest)
    fp_json = None
    try:
        fp_json = obj["fingerprint_json"]
    except Exception:
        fp_json = None
    fp_assertion = None
    if fp_json:
        try:
            from glowtbook import fingerprint as _fp
            manifest["fingerprint"] = _fp._as_obj(fp_json)   # raw fingerprint (verify.html loads this)
            fp_assertion = _fp.assertion(fp_json)             # compact, signed attestation
        except Exception:
            fp_json = None

    # style traits (linked to the SKOS thesaurus) travel with the piece
    try:
        tj = obj["traits_json"]
    except Exception:
        tj = None
    if tj:
        try:
            import json as _json

            from central import glass_traits as _gt
            labels = [x for v in _json.loads(tj).values() for x in v]
            resolved = _gt.resolve_many(labels)
            if resolved:
                manifest["traits"] = resolved
        except Exception:
            pass

    # Optionally embed Content Credentials (C2PA) in each condensed image
    do_sign = bool(sign) and c2pa_sign.available()
    prov = {"content_hash": manifest["content_hash"], "sourcing": manifest["sourcing"],
            "contributor": display,
            "events": next((a["data"] for a in manifest["assertions"]
                            if a["label"] == "glassdb.provenance.events"), [])}
    condensed = []          # (role, caption, b64)
    primary_bytes = None     # for optional Bluesky post / receipts
    for role, cap, dip, parent_bytes, parent_fmt in conds:
        out = dip
        if do_sign:
            try:
                out = c2pa_sign.sign_jpeg(dip, obj["title"], obj["maker"] or display, prov,
                                          parent_bytes=parent_bytes,
                                          parent_format=parent_fmt or "image/jpeg",
                                          year=obj["year"],
                                          extra_assertions=[fp_assertion] if fp_assertion else None)
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
        try:
            from central import notify
            notify.notify_submission(
                "object", obj["title"] or "Object",
                {"Maker": obj["maker"] or display, "Year": obj["year"],
                 "Techniques": ", ".join(techniques), "Signed": "yes" if do_sign else "no"},
                "object_submissions", str(sub_id), base_url)
        except Exception:
            pass
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
        try:
            from central import notify
            notify.notify_message(f"🍷 New object published: {obj['title'] or 'Object'}",
                                  {"Maker": obj["maker"] or display, "Year": obj["year"],
                                   "View": f"{base_url.rstrip('/')}/explore/" if base_url else ""})
        except Exception:
            pass

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
