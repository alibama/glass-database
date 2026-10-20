"""
central.events
==============
Maker-hosted events with buyer registration — the provenance moment at the
point of sale.

A maker (the **host**) registers an event: a sale, a demo, an ornament day at a
gallery. People who buy a piece there **register the purchase** online: sign in,
pick the event, describe the piece (photo optional, piece number optional). The
registration also becomes a piece in the buyer's own Glowtbook, so it can be
fingerprinted later if there wasn't time at the table.

Two-stage review, so the host curates and the site still gates:

    buyer registers  ──▶  pending_host  ──host approves──▶  host_approved
                                        └─host rejects───▶  rejected_host
    host_approved    ──admin approves──▶  approved  (published to `objects` with provenance:
                                                     created at the event by the maker,
                                                     acquired by the buyer)
                     └─admin rejects────▶  rejected

Events themselves work the moment the host creates them (link + QR for the
table); appearing in the public events directory needs admin approval of the
**listing**, like every other public row.

Tables (created lazily by ``ensure``):
  maker_events(id, slug UNIQUE, host_uid, host_name, host_email, studio, title, venue, city,
               region, country, starts, ends, description, piece_type, price_note,
               fingerprinting none|optional|all, registration_opens, registration_closes,
               status draft|open|closed, listing pending|approved|rejected,
               show_registrations, created_at, updated_at)
  event_registrations(id, event_id, buyer_uid, buyer_name, buyer_email, show_name, title,
               description, piece_ref, price_paid, image_b64, object_id, object_row_id,
               status, host_note, admin_note, created_at, host_reviewed_at, admin_reviewed_at)
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone

STATUSES = ("pending_host", "host_approved", "rejected_host", "approved", "rejected")
FINGERPRINTING = ("none", "optional", "all")
PUBLIC_EVENT_FIELDS = ("id", "slug", "host_name", "studio", "title", "venue", "city", "region", "country",
                       "starts", "ends", "description", "piece_type", "price_note", "fingerprinting",
                       "registration_opens", "registration_closes", "status", "listing",
                       "show_registrations", "created_at")
EDITABLE = ("studio", "title", "venue", "city", "region", "country", "starts", "ends", "description",
            "piece_type", "price_note", "fingerprinting", "registration_opens", "registration_closes",
            "status", "show_registrations")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "event"


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS maker_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, host_uid TEXT, host_name TEXT,
        host_email TEXT, studio TEXT DEFAULT '', title TEXT, venue TEXT DEFAULT '', city TEXT DEFAULT '',
        region TEXT DEFAULT '', country TEXT DEFAULT '', starts TEXT DEFAULT '', ends TEXT DEFAULT '',
        description TEXT DEFAULT '', piece_type TEXT DEFAULT '', price_note TEXT DEFAULT '',
        fingerprinting TEXT DEFAULT 'optional', registration_opens TEXT DEFAULT '',
        registration_closes TEXT DEFAULT '', status TEXT DEFAULT 'open', listing TEXT DEFAULT 'pending',
        show_registrations INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS event_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, buyer_uid TEXT, buyer_name TEXT,
        buyer_email TEXT DEFAULT '', show_name INTEGER DEFAULT 0, title TEXT, description TEXT DEFAULT '',
        piece_ref TEXT DEFAULT '', price_paid TEXT DEFAULT '', image_b64 TEXT, object_id INTEGER,
        object_row_id TEXT, status TEXT DEFAULT 'pending_host', host_note TEXT DEFAULT '',
        admin_note TEXT DEFAULT '', created_at TEXT, host_reviewed_at TEXT, admin_reviewed_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_evreg_event ON event_registrations(event_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_evreg_buyer ON event_registrations(buyer_uid)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ev_host ON maker_events(host_uid)")
    conn.commit()


def _nudge(title: str, fields: dict) -> None:
    try:
        from central import notify
        notify.notify_message(title, {k: str(v)[:200] for k, v in fields.items() if v})
    except Exception:  # noqa: BLE001
        pass


# --- events (host side) --------------------------------------------------------
def _unique_slug(conn, base: str) -> str:
    slug, i = base, 2
    while conn.execute("SELECT 1 FROM maker_events WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{i}"; i += 1
    return slug


def _clean(fields: dict) -> dict:
    out = {}
    for k in EDITABLE:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "show_registrations":
            out[k] = 1 if v in (1, True, "1", "true", "yes", "on") else 0
        elif k == "fingerprinting":
            out[k] = v if v in FINGERPRINTING else "optional"
        elif k == "status":
            out[k] = v if v in ("draft", "open", "closed") else "open"
        else:
            out[k] = str(v).strip()[:2000 if k == "description" else 200]
    return out


def create_event(conn, *, host_uid: str, host_name: str, host_email: str = "", title: str,
                 **fields) -> dict:
    ensure(conn)
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    f = _clean(fields)
    base = slugify(" ".join(x for x in [f.get("studio") or host_name, title, (f.get("starts") or "")[:4]] if x))
    slug = _unique_slug(conn, base)
    now = _now()
    cols = {"slug": slug, "host_uid": host_uid, "host_name": host_name, "host_email": host_email,
            "title": title[:200], "created_at": now, "updated_at": now, **f}
    conn.execute(f'INSERT INTO maker_events ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})',
                 list(cols.values()))
    conn.commit()
    ev = get_event(conn, slug)
    _nudge("🎟 New maker event (listing pending review)",
           {"Event": ev["title"], "Host": host_name, "Studio": ev["studio"], "Venue": ev["venue"],
            "When": ev["starts"], "Slug": slug})
    return ev


def update_event(conn, slug: str, host_uid: str | None, **fields) -> dict:
    """Host edits (host_uid must match) or admin edits (host_uid=None)."""
    ev = get_event(conn, slug)
    if not ev:
        raise ValueError("no such event")
    if host_uid is not None and ev["host_uid"] != host_uid:
        raise ValueError("not your event")
    f = _clean(fields)
    if not f:
        return ev
    f["updated_at"] = _now()
    conn.execute(f'UPDATE maker_events SET {", ".join(f"{k}=?" for k in f)} WHERE id=?',
                 [*f.values(), ev["id"]])
    conn.commit()
    return get_event(conn, slug)


def set_listing(conn, event_id: int, listing: str) -> None:
    if listing not in ("pending", "approved", "rejected"):
        raise ValueError("bad listing status")
    conn.execute("UPDATE maker_events SET listing=?, updated_at=? WHERE id=?", (listing, _now(), int(event_id)))
    conn.commit()


def get_event(conn, slug_or_id) -> dict | None:
    ensure(conn)
    if isinstance(slug_or_id, int) or str(slug_or_id).isdigit():
        return _one(conn.execute("SELECT * FROM maker_events WHERE id=?", (int(slug_or_id),)))
    return _one(conn.execute("SELECT * FROM maker_events WHERE slug=?", (str(slug_or_id),)))


def can_register(ev: dict, today: str | None = None) -> bool:
    if not ev or ev.get("status") != "open":
        return False
    t = today or date.today().isoformat()
    if ev.get("registration_opens") and t < ev["registration_opens"]:
        return False
    if ev.get("registration_closes") and t > ev["registration_closes"]:
        return False
    return True


def _counts(conn, event_id: int) -> dict:
    out = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) n FROM event_registrations WHERE event_id=? GROUP BY status",
                          (event_id,)).fetchall():
        out[r["status"]] = r["n"]
    out["total"] = sum(out[s] for s in STATUSES)
    return out


def public_event(conn, slug: str) -> dict | None:
    """What a buyer or visitor may see: no host email, plus registration state and
    (if the host allows) the approved pieces. Works for unlisted events too, since
    the link/QR at the table must work before the listing is reviewed."""
    ev = get_event(conn, slug)
    if not ev or ev["status"] == "draft":
        return None
    out = {k: ev.get(k) for k in PUBLIC_EVENT_FIELDS}
    out["listed"] = ev["listing"] == "approved"
    out["can_register"] = can_register(ev)
    c = _counts(conn, ev["id"])
    out["registered"] = c["approved"] + c["host_approved"] + c["pending_host"]
    out["published"] = c["approved"]
    out["pieces"] = public_registrations(conn, ev["id"]) if ev["show_registrations"] else []
    return out


def list_events(conn, *, include_unlisted: bool = False, include_closed: bool = True) -> list[dict]:
    ensure(conn)
    where = ["status != 'draft'"]
    if not include_unlisted:
        where.append("listing='approved'")
    if not include_closed:
        where.append("status='open'")
    rows = _rows(conn.execute(f"SELECT * FROM maker_events WHERE {' AND '.join(where)} "
                              "ORDER BY COALESCE(NULLIF(starts,''), created_at) DESC"))
    out = []
    for ev in rows:
        d = {k: ev.get(k) for k in PUBLIC_EVENT_FIELDS}
        d["listed"] = ev["listing"] == "approved"
        d["can_register"] = can_register(ev)
        c = _counts(conn, ev["id"])
        d["registered"] = c["approved"] + c["host_approved"] + c["pending_host"]
        d["published"] = c["approved"]
        out.append(d)
    return out


def host_events(conn, host_uid: str) -> list[dict]:
    ensure(conn)
    rows = _rows(conn.execute("SELECT * FROM maker_events WHERE host_uid=? ORDER BY created_at DESC", (host_uid,)))
    for ev in rows:
        ev["counts"] = _counts(conn, ev["id"])
        ev["can_register"] = can_register(ev)
    return rows


def pending_listings(conn) -> list[dict]:
    ensure(conn)
    return _rows(conn.execute("SELECT * FROM maker_events WHERE listing='pending' AND status!='draft' "
                              "ORDER BY created_at DESC"))


# --- registrations (buyer side) ------------------------------------------------
def register(conn, event_slug_or_id, *, buyer_uid: str, buyer_name: str, buyer_email: str = "",
             title: str, description: str = "", piece_ref: str = "", price_paid: str = "",
             image_b64: str | None = None, show_name: bool = False, object_id: int | None = None) -> dict:
    ev = get_event(conn, event_slug_or_id)
    if not ev:
        raise ValueError("no such event")
    if not can_register(ev):
        raise ValueError("registration for this event is closed")
    title = (title or "").strip()
    if not title:
        raise ValueError("tell us what the piece is (a title)")
    now = _now()
    cur = conn.execute(
        """INSERT INTO event_registrations (event_id,buyer_uid,buyer_name,buyer_email,show_name,title,
           description,piece_ref,price_paid,image_b64,object_id,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending_host',?)""",
        (ev["id"], buyer_uid, (buyer_name or "")[:200], (buyer_email or "")[:200], 1 if show_name else 0,
         title[:200], (description or "")[:2000], (piece_ref or "")[:80], (price_paid or "")[:80],
         image_b64, object_id, now))
    conn.commit()
    reg = get_registration(conn, cur.lastrowid)
    _nudge("🧾 Purchase registered (waiting for the host)",
           {"Event": ev["title"], "Host": ev["host_name"], "Piece": title, "Ref": piece_ref,
            "Buyer": buyer_name if show_name else "(name private)"})
    return reg


def link_buyer_object(conn, reg_id: int, object_id: int) -> None:
    conn.execute("UPDATE event_registrations SET object_id=? WHERE id=?", (int(object_id), int(reg_id)))
    conn.commit()


def get_registration(conn, reg_id: int) -> dict | None:
    return _one(conn.execute(
        "SELECT r.*, e.slug AS event_slug, e.title AS event_title, e.host_uid, e.host_name, e.studio, "
        "e.venue, e.starts, e.piece_type FROM event_registrations r JOIN maker_events e ON e.id=r.event_id "
        "WHERE r.id=?", (int(reg_id),)))


def _shape(r: dict, *, private: bool) -> dict:
    """Trim a registration row for an API response. `private` = the buyer, the host
    or an admin is asking (email stays hidden unless host/admin asks via `with_email`)."""
    out = {k: r.get(k) for k in ("id", "event_id", "event_slug", "event_title", "host_name", "studio",
                                 "venue", "starts", "piece_type", "title", "description", "piece_ref",
                                 "status", "created_at", "host_reviewed_at", "admin_reviewed_at",
                                 "object_id", "object_row_id", "show_name")}
    out["has_image"] = bool(r.get("image_b64"))
    out["buyer"] = r.get("buyer_name") if (private or r.get("show_name")) else "a collector"
    if private:
        out["price_paid"] = r.get("price_paid")
        out["host_note"] = r.get("host_note")
        out["admin_note"] = r.get("admin_note")
    if r.get("object_row_id"):
        out["object_url"] = f"/explore/?view=objects#obj-{r['object_row_id']}"
    return out


def my_registrations(conn, buyer_uid: str) -> list[dict]:
    ensure(conn)
    rows = _rows(conn.execute(
        "SELECT r.*, e.slug AS event_slug, e.title AS event_title, e.host_uid, e.host_name, e.studio, e.venue, "
        "e.starts, e.piece_type FROM event_registrations r JOIN maker_events e ON e.id=r.event_id "
        "WHERE r.buyer_uid=? ORDER BY r.created_at DESC", (buyer_uid,)))
    return [_shape(r, private=True) for r in rows]


def registrations(conn, event_id: int, status: str | None = None, *, with_email: bool = False) -> list[dict]:
    """For the host (and admin): every registration of an event, optionally by status."""
    q = ("SELECT r.*, e.slug AS event_slug, e.title AS event_title, e.host_uid, e.host_name, e.studio, "
         "e.venue, e.starts, e.piece_type FROM event_registrations r JOIN maker_events e ON e.id=r.event_id "
         "WHERE r.event_id=?")
    args: list = [int(event_id)]
    if status:
        q += " AND r.status=?"; args.append(status)
    rows = _rows(conn.execute(q + " ORDER BY r.created_at", args))
    out = []
    for r in rows:
        d = _shape(r, private=True)
        if with_email:
            d["buyer_email"] = r.get("buyer_email")
        d["image_b64"] = r.get("image_b64")
        out.append(d)
    return out


def public_registrations(conn, event_id: int) -> list[dict]:
    rows = _rows(conn.execute(
        "SELECT r.*, e.slug AS event_slug, e.title AS event_title, e.host_uid, e.host_name, e.studio, e.venue, "
        "e.starts, e.piece_type FROM event_registrations r JOIN maker_events e ON e.id=r.event_id "
        "WHERE r.event_id=? AND r.status='approved' ORDER BY r.admin_reviewed_at DESC, r.id DESC",
        (int(event_id),)))
    return [_shape(r, private=False) for r in rows]


def image_bytes(conn, reg_id: int) -> bytes | None:
    import base64
    r = _one(conn.execute("SELECT image_b64 FROM event_registrations WHERE id=?", (int(reg_id),)))
    if not r or not r["image_b64"]:
        return None
    try:
        return base64.b64decode(r["image_b64"])
    except Exception:  # noqa: BLE001
        return None


# --- review: host first, then the site -------------------------------------------
def host_review(conn, event_id: int, host_uid: str | None, ids, approve: bool, note: str = "") -> int:
    """Stage one. Only pending_host rows move; others are left alone. host_uid None = admin
    acting for the host. Returns how many changed."""
    ev = get_event(conn, int(event_id))
    if not ev:
        raise ValueError("no such event")
    if host_uid is not None and ev["host_uid"] != host_uid:
        raise ValueError("not your event")
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    new = "host_approved" if approve else "rejected_host"
    q = (f"UPDATE event_registrations SET status=?, host_note=?, host_reviewed_at=? "
         f"WHERE event_id=? AND status='pending_host' AND id IN ({','.join('?' for _ in ids)})")
    cur = conn.execute(q, [new, (note or "")[:500], _now(), ev["id"], *ids])
    conn.commit()
    n = cur.rowcount
    if n and approve:
        _nudge("✅ Host approved registrations — waiting for site review",
               {"Event": ev["title"], "Host": ev["host_name"], "Count": n,
                "Review": "Admin → Review queue → Event registrations"})
    return n


def host_approve_all(conn, event_id: int, host_uid: str | None) -> int:
    ids = [r["id"] for r in registrations(conn, event_id, "pending_host")]
    return host_review(conn, event_id, host_uid, ids, True)


def pending_admin(conn) -> list[dict]:
    """Stage two queue: host-approved registrations across all events."""
    ensure(conn)
    rows = _rows(conn.execute(
        "SELECT r.*, e.slug AS event_slug, e.title AS event_title, e.host_uid, e.host_name, e.studio, e.venue, "
        "e.starts, e.piece_type FROM event_registrations r JOIN maker_events e ON e.id=r.event_id "
        "WHERE r.status='host_approved' ORDER BY e.id, r.created_at"))
    out = []
    for r in rows:
        d = _shape(r, private=True)
        d["image_b64"] = r.get("image_b64")
        out.append(d)
    return out


def admin_review(conn, ids, approve: bool, reviewer: str = "admin", note: str = "") -> list[dict]:
    """Stage two. Approving PUBLISHES: the registration becomes a public object with
    provenance (created at the event by the maker, acquired by the buyer). Returns
    the rows touched, each with object_row_id when published."""
    ids = [int(i) for i in (ids or [])]
    done = []
    for rid in ids:
        r = get_registration(conn, rid)
        if not r or r["status"] != "host_approved":
            continue
        if approve:
            row_id = publish_registration(conn, r, reviewer=reviewer)
            conn.execute("UPDATE event_registrations SET status='approved', object_row_id=?, admin_note=?, "
                         "admin_reviewed_at=? WHERE id=?", (row_id, (note or "")[:500], _now(), rid))
        else:
            conn.execute("UPDATE event_registrations SET status='rejected', admin_note=?, admin_reviewed_at=? "
                         "WHERE id=?", ((note or "")[:500], _now(), rid))
        conn.commit()
        done.append(get_registration(conn, rid))
    return done


def publish_registration(conn, r: dict, reviewer: str = "admin") -> str:
    """Insert the registration into the public registry: an `objects` row (+ image)
    with a provenance manifest, approved through the gate. Idempotent per registration."""
    from central import approvals
    from glowtbook import media
    from glowtbook.contribute import ensure_central_objects
    ensure_central_objects(conn)
    ev_date = (r.get("starts") or r.get("created_at") or "")[:10]
    buyer = r.get("buyer_name") if r.get("show_name") else "a collector"
    maker = r.get("host_name") or r.get("studio") or ""
    if r.get("studio") and r.get("studio") != maker:
        maker = f"{maker} ({r['studio']})"
    obj = {"title": r["title"], "maker": maker, "year": ev_date[:4], "materials": "glass",
           "dimensions": "", "description": r.get("description") or "",
           "status": "in collection"}
    events = [
        {"event_type": "created", "event_date": ev_date, "actor": maker,
         "location": " · ".join(x for x in [r.get("venue"), r.get("event_title")] if x),
         "note": f"Made for {r.get('event_title')}" + (f" (piece {r['piece_ref']})" if r.get("piece_ref") else "")},
        {"event_type": "acquired", "event_date": ev_date, "actor": buyer,
         "location": r.get("venue") or "", "note": "Purchase registered online at the event"},
    ]
    ingredients, cond = [], None
    raw = image_bytes(conn, r["id"])
    if raw:
        try:
            cond, _mime = media.condense_image(raw)
            ingredients.append({"title": f"registration-{r['id']}.jpg", "hash": media.sha256_hex(cond),
                                "role": "primary"})
        except Exception:  # noqa: BLE001
            cond = None
    techniques = [r["piece_type"]] if r.get("piece_type") else []
    manifest = media.build_manifest(obj, events, ingredients, techniques, False,
                                    contributor=buyer, sourcing="event registration")
    manifest["event"] = {"slug": r.get("event_slug"), "title": r.get("event_title"),
                         "host": r.get("host_name"), "venue": r.get("venue"), "date": ev_date}
    # a fingerprint captured on the buyer's Glowtbook piece travels with the record
    try:
        if r.get("object_id") and r.get("buyer_uid"):
            from glowtbook import store as gstore
            s = gstore.open_store()
            o = gstore.get_object(s, r["buyer_uid"], int(r["object_id"]))
            if o and o["fingerprint_json"]:
                manifest["fingerprint"] = json.loads(o["fingerprint_json"])
    except Exception:  # noqa: BLE001
        pass
    row_id = hashlib.sha1(f"evreg|{r['id']}".encode()).hexdigest()[:16]
    now = _now()
    conn.execute("""INSERT OR REPLACE INTO objects
        (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,techniques,materials,dimensions,
         description,contributor,sourcing,value_display,has_credentials,manifest_json,content_hash,published_at,
         fingerprint_json,fingerprint_rating,fingerprint_tier)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row_id, "events", "registrations", now, obj["title"], obj["maker"], obj["year"],
         ", ".join(techniques), obj["materials"], obj["dimensions"], obj["description"], buyer,
         "event registration", "", 0, json.dumps(manifest, ensure_ascii=False), manifest["content_hash"], now,
         json.dumps(manifest["fingerprint"]) if manifest.get("fingerprint") else None,
         (manifest.get("fingerprint") or {}).get("rating"), (manifest.get("fingerprint") or {}).get("tier")))
    conn.execute("DELETE FROM object_images WHERE object_row_id=?", (row_id,))
    if cond:
        import base64
        conn.execute("INSERT INTO object_images (object_row_id,role,caption,image_b64) VALUES (?,?,?,?)",
                     (row_id, "primary", r.get("piece_ref") or "", base64.b64encode(cond).decode()))
    try:
        conn.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM objects) WHERE tbl='objects'")
    except Exception:  # noqa: BLE001
        pass
    conn.commit()
    approvals.set_status(conn, "objects", [row_id], "approved", reviewer=reviewer)
    return row_id


def summary(conn) -> dict:
    ensure(conn)
    s = {"events": conn.execute("SELECT COUNT(*) FROM maker_events").fetchone()[0],
         "listings_pending": conn.execute("SELECT COUNT(*) FROM maker_events WHERE listing='pending' "
                                          "AND status!='draft'").fetchone()[0]}
    for st in STATUSES:
        s[st] = conn.execute("SELECT COUNT(*) FROM event_registrations WHERE status=?", (st,)).fetchone()[0]
    return s
