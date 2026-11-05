"""
central.captures
================
Capture-session manifests: what the Glass ID capture pages send when a person taps
*Send for analysis*. One session is the photos the page kept (the small frame
thumbnails and the full-size best shot), the descriptors it computed, the
candidates the server offered with their scores, the decision the person took
(new piece, match confirmed, match rejected, not sure), the mat detection, and the
device and app version. The point is to hand real data over for analysis while the
app is used, so thresholds can be set from captures instead of guesses
(docs/GLASS-ID-MATCHING-ASSESSMENT.md).

Storage
  * the files: ``GLASSDB_CAPTURES_DIR`` (default ``data/capture_manifests``), one folder per
    session named by its id, with ``manifest.json`` and ``images/…``;
  * the index: the ``capture_manifests`` table in the central database - one row per
    session with the summary columns an admin list filters on and the manifest itself.

Who sees what: the person who sent a session can read it back (their own frames
beside a later check); administrators see every session on ``/captures.html`` and pull
them down with ``python -m scripts.pull_capture``. Nothing here is public.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
TOOL = "glass-id-capture-manifest"
KINDS = ("enroll", "verify")
DECISIONS = ("new_piece", "re_registered", "match_confirmed", "match_rejected", "undecided")
DECISION_LABELS = {"new_piece": "New piece", "re_registered": "Registered again",
                   "match_confirmed": "Match confirmed", "match_rejected": "Match rejected",
                   "undecided": "Not decided"}
MAX_ZIP = 80 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
MAX_FILES = 400
MAX_MANIFEST = 12 * 1024 * 1024
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
MAT_FRAMES = 16          # how many images the server looks at for the mat
_NAME = re.compile(r"^(images/)?[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


# --- where things live ------------------------------------------------------------
def captures_dir() -> Path:
    return Path(os.environ.get("GLASSDB_CAPTURES_DIR") or (DATA / "capture_manifests")).expanduser()


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS capture_manifests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        manifest_id TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        user_uid TEXT DEFAULT '', user_email TEXT DEFAULT '', user_name TEXT DEFAULT '',
        kind TEXT DEFAULT '', app_version TEXT DEFAULT '', device TEXT DEFAULT '',
        object_id INTEGER, object_row_id TEXT, object_title TEXT DEFAULT '',
        decision TEXT DEFAULT 'undecided', decision_note TEXT DEFAULT '', decided_at TEXT,
        verdict TEXT DEFAULT '', confidence INTEGER, rating INTEGER, descriptor TEXT DEFAULT '',
        n_frames INTEGER DEFAULT 0, n_images INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0,
        has_embeddings INTEGER DEFAULT 0,
        mat_detected INTEGER DEFAULT 0, mat_json TEXT,
        top_candidate TEXT DEFAULT '', top_score INTEGER, n_candidates INTEGER DEFAULT 0,
        manifest_json TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_capman_user ON capture_manifests(user_uid, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_capman_obj ON capture_manifests(object_id, object_row_id)")
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"cap-{when:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def safe_name(name: str) -> str | None:
    """A file name inside a session folder: ``images/000.jpg``, ``hero.jpg`` - never a
    path that climbs out, never an odd extension. Returns the normalised name or None."""
    n = str(name or "").replace("\\", "/").strip()
    if n.startswith("images/images/"):
        n = n[len("images/"):]
    if not n or n.startswith((".", "/")) or ".." in n or not _NAME.match(n):
        return None
    low = n.lower()
    if not (low.endswith(IMAGE_EXT) or low == "manifest.json"):
        return None
    if low != "manifest.json" and not n.startswith("images/"):
        n = "images/" + n
    return n


# --- reading an upload -----------------------------------------------------------------
def read_zip(zip_bytes: bytes) -> tuple[dict, dict[str, bytes]]:
    """The manifest and the images out of an upload. Raises ValueError with a plain
    sentence for anything the pages would not have produced."""
    if not zip_bytes:
        raise ValueError("empty upload")
    if len(zip_bytes) > MAX_ZIP:
        raise ValueError(f"upload too large ({len(zip_bytes) // (1024 * 1024)} MB; the limit is {MAX_ZIP // (1024 * 1024)} MB)")
    if zip_bytes[:2] != b"PK":
        try:
            man = json.loads(zip_bytes.decode("utf-8"))
        except Exception:
            raise ValueError("not a zip and not JSON")
        if not isinstance(man, dict):
            raise ValueError("the manifest must be a JSON object")
        return man, {}
    files: dict[str, bytes] = {}
    man = None
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            names = z.namelist()
            if len(names) > MAX_FILES:
                raise ValueError(f"too many files in the upload ({len(names)}; the limit is {MAX_FILES})")
            for info in z.infolist():
                if info.is_dir():
                    continue
                base = info.filename.replace("\\", "/")
                if base.lower().endswith("manifest.json") and man is None:
                    if info.file_size > MAX_MANIFEST:
                        raise ValueError("manifest.json is too large")
                    man = json.loads(z.read(info).decode("utf-8"))
                    continue
                # the name as sent, else its bare file name (a path that climbs out keeps its
                # picture under images/ and loses the path); anything else is not a picture
                name = safe_name(base) or safe_name(base.split("/")[-1])
                if not name or name == "manifest.json":
                    continue                      # not a picture: left out, not an error
                if info.file_size > MAX_FILE:
                    raise ValueError(f"{base} is too large ({info.file_size // (1024 * 1024)} MB)")
                data = z.read(info)
                if not _looks_like_image(data):
                    continue
                files[name] = data
    except zipfile.BadZipFile:
        raise ValueError("the upload is not a readable zip")
    except json.JSONDecodeError as ex:
        raise ValueError(f"manifest.json is not valid JSON: {ex}")
    if man is None:
        raise ValueError("no manifest.json inside the upload")
    if not isinstance(man, dict):
        raise ValueError("the manifest must be a JSON object")
    return man, files


def _looks_like_image(data: bytes) -> bool:
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


# --- the summary columns ---------------------------------------------------------------
def _i(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _s(v, n=200) -> str:
    return ("" if v is None else str(v))[:n]


def device_line(dev: dict | None) -> str:
    """One short line about the phone: model words out of the user agent, the camera
    resolution, the compass."""
    if not isinstance(dev, dict):
        return ""
    ua = str(dev.get("ua") or "")
    model = ""
    m = re.search(r"\(([^)]*)\)", ua)
    if m:
        inner = m.group(1)
        if "Android" in inner:
            parts = [p.strip() for p in inner.split(";")]
            model = next((p for p in parts if p and p not in ("Linux", "U") and not p.startswith("Android")
                          and "wv" != p), "") or "Android"
            av = next((p for p in parts if p.startswith("Android")), "")
            model = (model + (" · " + av if av else "")).strip(" ·")
        elif "iPhone" in inner or "iPad" in inner:
            model = ("iPhone" if "iPhone" in inner else "iPad")
            m2 = re.search(r"OS (\d+[_\d]*)", inner)
            if m2:
                model += " · iOS " + m2.group(1).replace("_", ".")
        elif "Macintosh" in inner:
            model = "Mac"
        elif "Windows" in inner:
            model = "Windows"
        else:
            model = inner.split(";")[0][:40]
    browser = ""
    for key, word in (("CriOS", "Chrome"), ("FxiOS", "Firefox"), ("EdgA", "Edge"), ("Edg/", "Edge"),
                      ("SamsungBrowser", "Samsung"), ("Firefox", "Firefox"), ("Chrome", "Chrome"),
                      ("Safari", "Safari")):
        if key in ua:
            browser = word
            break
    res = dev.get("resolution") if isinstance(dev.get("resolution"), dict) else None
    cam = f"{res.get('w')}×{res.get('h')}" if res and res.get("w") else ""
    bits = [b for b in (model, browser, cam) if b]
    if dev.get("compass") is False:
        bits.append("no compass")
    return " · ".join(bits)[:200]


def summarise(man: dict) -> dict:
    """The index columns for one manifest."""
    kind = _s(man.get("kind"), 20).lower()
    if kind not in KINDS:
        kind = "verify" if man.get("reference") or man.get("match") else "enroll"
    fp = man.get("fingerprint") if isinstance(man.get("fingerprint"), dict) else {}
    frames = fp.get("frames") if isinstance(fp.get("frames"), list) else []
    match = man.get("match") if isinstance(man.get("match"), dict) else {}
    dec = man.get("decision") if isinstance(man.get("decision"), dict) else {}
    obj = (dec.get("object") if isinstance(dec.get("object"), dict) else None) or \
          (man.get("object") if isinstance(man.get("object"), dict) else None) or {}
    refo = man.get("reference") if isinstance(man.get("reference"), dict) else {}
    cands = man.get("candidates") if isinstance(man.get("candidates"), dict) else {}
    items = cands.get("items") if isinstance(cands.get("items"), list) else []
    top = items[0] if items and isinstance(items[0], dict) else {}
    app = man.get("app") if isinstance(man.get("app"), dict) else {}
    dev = man.get("device") if isinstance(man.get("device"), dict) else {}
    sess = man.get("session") if isinstance(man.get("session"), dict) else {}
    decision = _s(dec.get("decision"), 30) or "undecided"
    if decision not in DECISIONS:
        decision = "undecided"
    md = fp.get("metadata") if isinstance(fp.get("metadata"), dict) else {}
    has_emb = bool(md.get("hasEmbeddings")) or any(isinstance(f, dict) and f.get("emb") for f in frames) \
        or bool(match.get("descriptor") == "dino")
    n_frames = len(frames) or _i(match.get("shots")) or _i(sess.get("shots")) or 0
    return {
        "kind": kind, "app_version": _s(app.get("version") or man.get("version"), 60),
        "device": device_line(dev),
        "object_id": _i(obj.get("id")) if obj.get("id") is not None and str(obj.get("id")).isdigit() else
                     (_i(refo.get("id")) if refo.get("kind") == "mine" else None),
        "object_row_id": _s(obj.get("row_id") or (refo.get("id") if refo.get("kind") == "public" else ""), 80) or None,
        "object_title": _s(obj.get("title") or refo.get("title"), 200),
        "decision": decision, "decision_note": _s(dec.get("note"), 1000),
        "verdict": _s(match.get("verdict") or (top.get("label") if top else ""), 40),
        "confidence": _i(match.get("confidence")) if match else (_i(top.get("confidence")) if top else None),
        "rating": _i(fp.get("rating")) if fp else _i(refo.get("rating")),
        "descriptor": _s(match.get("descriptor") or cands.get("descriptor") or sess.get("descriptor"), 20),
        "n_frames": int(n_frames or 0), "has_embeddings": 1 if has_emb else 0,
        "top_candidate": _s(top.get("title") or top.get("ref"), 200) if top else "",
        "top_score": _i(top.get("confidence")) if top else None,
        "n_candidates": len(items),
    }


# --- saving ----------------------------------------------------------------------------
def _mat_report(files: dict[str, bytes]) -> dict:
    """What the server can see of the Photo Mat in the stored pictures: markers per
    image, scale, a size estimate. Never raises; says when OpenCV is missing."""
    out = {"checked": 0, "with_mat": 0, "images": [], "dimensions": None, "note": ""}
    try:
        from glowtbook import matdetect
    except Exception as ex:  # noqa: BLE001
        out["note"] = f"mat detection unavailable: {ex}"
        return out
    if not matdetect.available():
        out["note"] = "OpenCV with ArUco is not installed on the server"
        return out
    names = sorted(files, key=lambda n: (0 if "hero" in n else 1, n))[:MAT_FRAMES]
    measured = []
    for n in names:
        try:
            d = matdetect.mat_details(files[n])
        except Exception as ex:  # noqa: BLE001
            d = {"markers": 0, "error": str(ex)[:120]}
        d["file"] = n
        out["images"].append(d)
        out["checked"] += 1
        if d.get("markers"):
            out["with_mat"] += 1
            measured.append(files[n])
    if measured:
        try:
            m = matdetect.measure_frames(measured)
            out["dimensions"] = m if m.get("ok") else None
            if not m.get("ok"):
                out["note"] = m.get("reason", "")
        except Exception as ex:  # noqa: BLE001
            out["note"] = f"measurement failed: {ex}"
    elif out["checked"]:
        out["note"] = "no ArUco markers in the stored pictures (the piece filled the frame, or the mat was not used)"
    return out


def save(conn, manifest: dict, files: dict[str, bytes], user: dict | None, *, manifest_id: str | None = None,
         mat: bool = True) -> dict:
    """Store one session: the folder, the manifest (with a ``server`` block added) and
    the index row. Returns the index row as a dict."""
    ensure(conn)
    user = user or {}
    mid = manifest_id or new_id()
    root = captures_dir() / mid
    if root.exists():
        raise ValueError(f"a session called {mid} already exists")
    (root / "images").mkdir(parents=True, exist_ok=True)
    total = 0
    stored = []
    try:
        for name, data in files.items():
            sn = safe_name(name)
            if not sn or sn == "manifest.json":
                continue
            p = root / sn
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            total += len(data)
            stored.append({"file": sn, "bytes": len(data)})
        man = dict(manifest)
        man.setdefault("tool", TOOL)
        man.setdefault("version", 1)
        srv = {"manifest_id": mid, "received_at": now_iso(),
               "by": {"uid": _s(user.get("uid"), 120), "email": _s(user.get("email"), 200),
                      "name": _s(user.get("name"), 200), "kind": _s(user.get("kind"), 20)},
               "images_stored": stored, "bytes": total}
        if mat and files:
            srv["mat"] = _mat_report(files)
        man["server"] = srv
        text = json.dumps(man, ensure_ascii=False, indent=1)
        (root / "manifest.json").write_text(text, encoding="utf-8")
        total += len(text.encode("utf-8"))
        s = summarise(man)
        matr = srv.get("mat") or {}
        conn.execute("""INSERT INTO capture_manifests
            (manifest_id, created_at, user_uid, user_email, user_name, kind, app_version, device,
             object_id, object_row_id, object_title, decision, decision_note, decided_at, verdict, confidence,
             rating, descriptor, n_frames, n_images, bytes, has_embeddings, mat_detected, mat_json,
             top_candidate, top_score, n_candidates, manifest_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, srv["received_at"], srv["by"]["uid"], srv["by"]["email"], srv["by"]["name"], s["kind"],
             s["app_version"], s["device"], s["object_id"], s["object_row_id"], s["object_title"], s["decision"],
             s["decision_note"], (manifest.get("decision") or {}).get("at") if isinstance(manifest.get("decision"), dict) else None,
             s["verdict"], s["confidence"], s["rating"], s["descriptor"], s["n_frames"], len(stored), total,
             s["has_embeddings"], 1 if matr.get("with_mat") else 0, json.dumps(matr) if matr else None,
             s["top_candidate"], s["top_score"], s["n_candidates"], text))
        conn.commit()
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return get(conn, mid, with_manifest=False)


def save_zip(conn, zip_bytes: bytes, user: dict | None, **kw) -> dict:
    man, files = read_zip(zip_bytes)
    return save(conn, man, files, user, **kw)


# --- reading back ----------------------------------------------------------------------
def _row(r) -> dict:
    d = dict(r)
    d.pop("manifest_json", None)
    d["decision_label"] = DECISION_LABELS.get(d.get("decision") or "undecided", d.get("decision"))
    try:
        d["mat"] = json.loads(d.pop("mat_json") or "null")
    except Exception:  # noqa: BLE001
        d["mat"] = None
    d["url"] = f"/captures.html?m={d['manifest_id']}"
    return d


def list_manifests(conn, *, kind: str | None = None, decision: str | None = None, uid: str | None = None,
                   object_id: int | None = None, object_row_id: str | None = None, q: str | None = None,
                   since: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    ensure(conn)
    where, args = [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if decision:
        where.append("decision=?"); args.append(decision)
    if uid:
        where.append("user_uid=?"); args.append(uid)
    if object_id is not None:
        where.append("object_id=?"); args.append(int(object_id))
    if object_row_id:
        where.append("object_row_id=?"); args.append(object_row_id)
    if since:
        where.append("created_at>=?"); args.append(since)
    if q:
        like = f"%{q}%"
        where.append("(manifest_id LIKE ? OR object_title LIKE ? OR user_email LIKE ? OR user_name LIKE ? "
                     "OR top_candidate LIKE ? OR device LIKE ? OR decision_note LIKE ?)")
        args += [like] * 7
    sql = " WHERE " + " AND ".join(where) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM capture_manifests{sql}", args).fetchone()[0]
    cols = ("id, manifest_id, created_at, user_uid, user_email, user_name, kind, app_version, device, object_id, "
            "object_row_id, object_title, decision, decision_note, decided_at, verdict, confidence, rating, "
            "descriptor, n_frames, n_images, bytes, has_embeddings, mat_detected, mat_json, top_candidate, "
            "top_score, n_candidates")
    rows = conn.execute(f"SELECT {cols} FROM capture_manifests{sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                        [*args, int(limit), int(offset)]).fetchall()
    return {"total": total, "limit": int(limit), "offset": int(offset), "items": [_row(r) for r in rows]}


def get(conn, manifest_id: str, *, with_manifest: bool = True) -> dict | None:
    ensure(conn)
    r = conn.execute("SELECT * FROM capture_manifests WHERE manifest_id=?", (manifest_id,)).fetchone()
    if not r:
        return None
    d = _row(r)
    if with_manifest:
        try:
            d["manifest"] = json.loads(r["manifest_json"] or "{}")
        except Exception:  # noqa: BLE001
            d["manifest"] = {}
        d["files"] = files_of(manifest_id)
    return d


def files_of(manifest_id: str) -> list[dict]:
    root = captures_dir() / manifest_id
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out.append({"file": rel, "bytes": p.stat().st_size,
                        "url": f"/api/admin/captures/{manifest_id}/files/{rel}"})
    return out


def file_path(manifest_id: str, name: str) -> Path | None:
    """The file on disk for ``name`` inside the session, or None when the name is not
    one we would have written (the only files are ``manifest.json`` and ``images/…``)."""
    if not re.match(r"^cap-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$", manifest_id or ""):
        return None
    sn = safe_name(name)
    if not sn:
        return None
    p = (captures_dir() / manifest_id / sn)
    try:
        p.resolve().relative_to(captures_dir().resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def zip_bytes(manifest_id: str) -> bytes | None:
    root = captures_dir() / manifest_id
    if not root.is_dir():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                z.write(p, f"{manifest_id}/{p.relative_to(root).as_posix()}")
    return buf.getvalue()


def set_decision(conn, manifest_id: str, decision: str, *, note: str = "", by: str = "",
                 matched: dict | None = None) -> dict | None:
    """Record (or change) the decision on a session, in the row and in the manifest file,
    keeping the earlier decisions as history."""
    ensure(conn)
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    r = conn.execute("SELECT manifest_json FROM capture_manifests WHERE manifest_id=?", (manifest_id,)).fetchone()
    if not r:
        return None
    try:
        man = json.loads(r["manifest_json"] or "{}")
    except Exception:  # noqa: BLE001
        man = {}
    prev = man.get("decision")
    if isinstance(prev, dict) and prev.get("decision"):
        man.setdefault("decisions", []).append(prev)
    entry = {"decision": decision, "note": _s(note, 1000), "at": now_iso(), "by": _s(by, 200)}
    if isinstance(matched, dict):
        entry["object"] = {k: matched.get(k) for k in ("ref", "id", "row_id", "title") if matched.get(k) is not None}
    man["decision"] = entry
    text = json.dumps(man, ensure_ascii=False, indent=1)
    s = summarise(man)
    conn.execute("UPDATE capture_manifests SET decision=?, decision_note=?, decided_at=?, manifest_json=?, "
                 "object_id=COALESCE(?, object_id), object_row_id=COALESCE(?, object_row_id), "
                 "object_title=CASE WHEN ?='' THEN object_title ELSE ? END WHERE manifest_id=?",
                 (decision, entry["note"], entry["at"], text, s["object_id"], s["object_row_id"],
                  s["object_title"], s["object_title"], manifest_id))
    conn.commit()
    p = captures_dir() / manifest_id / "manifest.json"
    if p.parent.is_dir():
        p.write_text(text, encoding="utf-8")
    return get(conn, manifest_id, with_manifest=False)


def stats(conn) -> dict:
    ensure(conn)
    out = {"total": conn.execute("SELECT COUNT(*) FROM capture_manifests").fetchone()[0],
           "by_kind": {}, "by_decision": {}, "people": 0, "last": None, "bytes": 0, "with_mat": 0}
    for k, n in conn.execute("SELECT kind, COUNT(*) FROM capture_manifests GROUP BY kind"):
        out["by_kind"][k or ""] = n
    for k, n in conn.execute("SELECT decision, COUNT(*) FROM capture_manifests GROUP BY decision"):
        out["by_decision"][k or "undecided"] = n
    out["people"] = conn.execute("SELECT COUNT(DISTINCT user_uid) FROM capture_manifests").fetchone()[0]
    out["bytes"] = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM capture_manifests").fetchone()[0]
    out["with_mat"] = conn.execute("SELECT COUNT(*) FROM capture_manifests WHERE mat_detected=1").fetchone()[0]
    r = conn.execute("SELECT created_at FROM capture_manifests ORDER BY created_at DESC LIMIT 1").fetchone()
    out["last"] = r[0] if r else None
    return out
