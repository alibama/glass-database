"""
api.seasons — seasonal games over HTTP
======================================
The public surface for /games.html, the Glowtbook "Seasons" tab and the verify
app's "Claim" button. Reads are open; writes need a signed-in person
(``require_user`` — the Google sign-in cookie or a pairing Bearer token).

  GET  /seasons                               current season + every non-draft season
  GET  /seasons/{slug}                        season, approved entries, my votes, leaderboard, awards
  GET  /seasons/{slug}/leaderboard            people's choice · most collected · studios · points · collectors
  GET  /seasons/{slug}/awards                 award categories with standings (+ my picks)
  GET  /seasons/{slug}/entries/{entry_id}     one entry
  POST /seasons/{slug}/entries   (sign-in)    {object_row_id, studio, edition_size, price_note, title,
                                               solo, legendary, legend} → live at once (the piece was
                                               reviewed when published); GLASSDB_SEASON_ENTRY_REVIEW=1 queues it
  POST /seasons/{slug}/vote      (sign-in)    {entry_id}  → toggles; {"voted","votes","remaining"}
  POST /seasons/{slug}/awards/{category}/vote (sign-in) {entry_id} → one pick per award; swaps/toggles
  POST /claims                   (sign-in)    {object_row_id, confidence, report, location}
  GET  /me/collection            (sign-in)    my claimed pieces + badges
  GET  /me/claims                (sign-in)    claims on the pieces I published (the maker's inbox)
  GET  /seasons/entries/{entry_id}/card.png   1080×1350 share card (Instagram story / post)

Every domain error (closed season, duplicate, not your piece…) is a 400 with a
human sentence in ``detail``. Discord nudges for claims and season openings are
posted by ``central.seasons`` itself (best effort), not here.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import current_user, require_user  # noqa: E402
from central import seasons  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(tags=["seasons"])


# --- helpers -----------------------------------------------------------------
def _conn():
    conn = connect()
    seasons.ensure(conn)
    seasons.release_backlog(conn)
    return conn


def _season_or_404(conn, slug: str) -> dict:
    s = seasons.get_season(conn, slug)
    if not s or s["status"] == "draft":
        raise HTTPException(404, "No such season")
    return s


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host")
            or request.url.netloc)
    return f"{proto}://{host}"


def _pub_entry(e: dict, uid: str | None = None) -> dict:
    """An entry as the public sees it: no account ids, plus `mine` for the caller.
    Which piece is the legendary stays secret until a collector has claimed it
    (rarity == "legendary"); the maker sees their own flag."""
    e = dict(e)
    e["mine"] = bool(uid) and e.get("artist_uid") == uid
    e.pop("artist_uid", None)
    if not (e["mine"] or e.get("rarity") == "legendary"):
        e.pop("legendary", None); e.pop("legend", None)
    return e


def _pub_board(lb: dict, uid: str | None = None) -> dict:
    out = dict(lb)
    for key in ("peoples_choice", "most_collected"):
        out[key] = [_pub_entry(e, uid) for e in lb.get(key, [])]
    for key in ("collectors", "points"):
        out[key] = [{k: v for k, v in c.items() if k != "collector_uid"} for c in lb.get(key, [])]
    return out


def _my_picks(conn, s: dict, uid: str | None) -> dict:
    """{category_slug: entry_id} for the caller."""
    if not uid:
        return {}
    slugs = {c["id"]: c["slug"] for c in seasons.categories(conn, s["id"])}
    return {slugs[cid]: eid for cid, eid in seasons.my_award_votes(conn, s["id"], uid).items()
            if cid in slugs}


def _bad(ex: ValueError):
    return HTTPException(400, str(ex))


# --- share card (registered first so /seasons/entries/… never reads as a slug) --
def primary_image_bytes(conn, object_row_id: str) -> bytes | None:
    """The stored primary image of a public object, as bytes (None if none)."""
    row = conn.execute(
        "SELECT image_b64 FROM object_images WHERE object_row_id=? ORDER BY "
        "CASE role WHEN 'primary' THEN 0 ELSE 1 END, id LIMIT 1", (object_row_id,)).fetchone()
    if row and row["image_b64"]:
        try:
            return base64.b64decode(row["image_b64"])
        except Exception:  # noqa: BLE001
            return None
    return None


def render_card(conn, entry: dict, season: dict, link: str) -> bytes:
    """A 1080×1350 share card for an entry: the piece, title, maker · studio, the
    collecting event, and a QR back to the entry (central.cards, paper palette)."""
    from central import cards
    ed = int(entry.get("edition_size") or 1)
    meta = ("Edition of %d" % ed) if ed > 1 else "One of one"
    if entry.get("price_note"):
        meta += f"  ·  {entry['price_note']}"
    who = " · ".join(x for x in [entry.get("artist_name"), entry.get("studio")] if x)
    return cards.render_piece(primary_image_bytes(conn, entry["object_row_id"]),
                              eyebrow=f"Collecting event  ·  {season.get('title') or ''}",
                              title=entry.get("title") or "Untitled", who=who, meta=meta,
                              note=season.get("theme") or "", link=link,
                              footer_url="glassdatabase.org/games",
                              footer_note="Scan to see the piece · vote · add it to your collection")


@router.get("/seasons/entries/{entry_id}/card.png", summary="Share card for an entry (1080×1350 PNG)")
def entry_card(entry_id: int, request: Request):
    conn = _conn()
    e = seasons.get_entry(conn, entry_id)
    if not e or e["status"] != "approved":
        raise HTTPException(404, "No such entry")
    s = seasons.get_season(conn, int(e["season_id"])) or {}
    link = f"{_public_base(request)}/games.html?season={s.get('slug', '')}#entry-{e['id']}"
    try:
        png = render_card(conn, e, s, link)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(500, f"card render failed: {ex}")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600",
                             "Content-Disposition": f'inline; filename="season-entry-{e["id"]}.png"'})


# --- seasons -----------------------------------------------------------------------
@router.get("/seasons", summary="Current season + every published season")
def list_all(request: Request):
    conn = _conn()
    cur = seasons.current_season(conn)
    return {"current": cur, "seasons": seasons.list_seasons(conn),
            "badges": seasons.BADGES, "signed_in": current_user(request) is not None}


@router.get("/seasons/{slug}", summary="One season with its approved entries and boards")
def one_season(slug: str, request: Request):
    conn = _conn()
    s = _season_or_404(conn, slug)
    u = current_user(request)
    uid = u["uid"] if u else None
    mine = seasons.my_votes(conn, s["id"], uid) if uid else []
    lb = seasons.leaderboard(conn, s["id"])
    lb["collectors"] = seasons.collectors(conn)
    cap = int(s.get("votes_per_voter") or 3)
    return {"season": s,
            "entries": [_pub_entry(e, uid) for e in seasons.entries(conn, s["id"], "approved")],
            "my_votes": mine, "remaining_votes": max(0, cap - len(mine)),
            "can_vote": s["status"] in ("open", "voting"), "signed_in": uid is not None,
            "leaderboard": _pub_board(lb, uid),
            "awards": seasons.awards(conn, s["id"]), "my_picks": _my_picks(conn, s, uid),
            "legendaries_out": int(s.get("legendaries_out") or 0)}


@router.get("/seasons/{slug}/leaderboard", summary="People's choice · most collected · studios · collectors")
def season_leaderboard(slug: str, request: Request):
    conn = _conn()
    s = _season_or_404(conn, slug)
    u = current_user(request)
    lb = seasons.leaderboard(conn, s["id"])
    lb["collectors"] = seasons.collectors(conn)
    return {"season": {"id": s["id"], "slug": s["slug"], "title": s["title"], "status": s["status"]},
            **_pub_board(lb, u["uid"] if u else None)}


@router.get("/seasons/{slug}/awards", summary="Award categories with their standings")
def season_awards(slug: str, request: Request):
    conn = _conn()
    s = _season_or_404(conn, slug)
    u = current_user(request)
    uid = u["uid"] if u else None
    return {"season": {"id": s["id"], "slug": s["slug"], "title": s["title"], "status": s["status"]},
            "awards": seasons.awards(conn, s["id"]), "my_picks": _my_picks(conn, s, uid),
            "can_vote": s["status"] in ("open", "voting"), "signed_in": uid is not None,
            "legendaries_out": int(s.get("legendaries_out") or 0)}


@router.post("/seasons/{slug}/awards/{category_slug}/vote", summary="Pick one entry for an award")
def award_pick(slug: str, category_slug: str, payload: dict, u: dict = Depends(require_user)):
    conn = _conn()
    s = _season_or_404(conn, slug)
    cat = seasons.get_category(conn, s["id"], category_slug)
    if not cat:
        raise HTTPException(404, "No such award")
    try:
        eid = int(payload.get("entry_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "entry_id is required")
    try:
        return seasons.award_vote(conn, s["id"], cat["id"], eid, u["uid"])
    except ValueError as ex:
        raise _bad(ex)


@router.get("/seasons/{slug}/entries/{entry_id}", summary="One entry")
def one_entry(slug: str, entry_id: int, request: Request):
    conn = _conn()
    s = _season_or_404(conn, slug)
    e = seasons.get_entry(conn, entry_id)
    u = current_user(request)
    uid = u["uid"] if u else None
    if not e or e["season_id"] != s["id"] or (e["status"] != "approved" and e.get("artist_uid") != uid):
        raise HTTPException(404, "No such entry")
    out = _pub_entry(e, uid)
    out["voted"] = int(e["id"]) in (seasons.my_votes(conn, s["id"], uid) if uid else [])
    return out


@router.post("/seasons/{slug}/entries", summary="Enter one of your published pieces (pending review)")
def enter_piece(slug: str, payload: dict, request: Request, u: dict = Depends(require_user)):
    conn = _conn()
    s = _season_or_404(conn, slug)
    rid = (payload.get("object_row_id") or "").strip()
    if not rid:
        raise HTTPException(400, "object_row_id is required")
    try:
        ed = int(payload.get("edition_size") or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "edition_size must be a whole number")
    try:
        eid = seasons.enter(conn, s["id"], object_row_id=rid, artist_uid=u["uid"],
                            artist_name=u.get("name") or u.get("email") or "",
                            title=(payload.get("title") or "").strip(),
                            studio=(payload.get("studio") or "").strip(), edition_size=ed,
                            price_note=(payload.get("price_note") or "").strip(),
                            solo=bool(payload.get("solo")), legendary=bool(payload.get("legendary")),
                            legend=str(payload.get("legend") or ""))
    except ValueError as ex:
        raise _bad(ex)
    e = seasons.get_entry(conn, eid) or {"id": eid}
    live = e.get("status") == "approved"
    try:
        from central import notify
        fields = {"Maker": e.get("artist_name"), "Studio": e.get("studio"),
                  "Edition": e.get("edition_size"), "Price": e.get("price_note"), "Object": rid}
        if live:    # already public once: no approve button, just the news and where it is
            fields["Page"] = f"{_public_base(request)}/games.html?season={s['slug']}#entry-{eid}"
            fields["Reject if needed"] = "Admin → 🎃 Seasons"
            notify.notify_message(f"New entry, live: {e.get('title') or 'Untitled'} — {s['title']}", fields)
        else:
            fields["Review"] = "Admin → 🎃 Seasons"
            notify.notify_submission("season entry", f"{e.get('title') or 'Untitled'} — {s['title']}",
                                     fields, "season_entries", str(eid), _public_base(request))
    except Exception:  # noqa: BLE001
        pass
    try:
        from central import analytics
        analytics.log(conn, "seasons", s["slug"], "season:enter")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "pending": not live, "entry": _pub_entry(e, u["uid"]),
            "note": "It's on the season page now." if live
            else "Thanks — it'll show on the season page once an admin approves it."}


@router.post("/seasons/{slug}/vote", summary="Toggle your vote for an entry")
def cast_vote(slug: str, payload: dict, u: dict = Depends(require_user)):
    conn = _conn()
    s = _season_or_404(conn, slug)
    try:
        eid = int(payload.get("entry_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "entry_id is required")
    try:
        return seasons.vote(conn, s["id"], eid, u["uid"])
    except ValueError as ex:
        raise _bad(ex)


# --- claims + collection -------------------------------------------------------------
@router.post("/claims", summary="Claim a scanned piece for your collection")
def make_claim(payload: dict, request: Request, u: dict = Depends(require_user)):
    conn = _conn()
    rid = (payload.get("object_row_id") or "").strip()
    if not rid:
        raise HTTPException(400, "object_row_id is required")
    report = payload.get("report")
    if report is not None and not isinstance(report, dict):
        report = {"raw": report}
    try:
        out = seasons.claim(conn, object_row_id=rid, collector_uid=u["uid"],
                            collector_name=u.get("name") or u.get("email") or "",
                            confidence=payload.get("confidence"), report=report,
                            location=(payload.get("location") or "").strip())
    except ValueError as ex:
        raise _bad(ex)
    try:
        from central import analytics
        analytics.log(conn, "seasons", rid, f"season:claim:{out.get('status')}")
    except Exception:  # noqa: BLE001
        pass
    out.pop("collector_uid", None)
    if out.get("status") == "pending":
        out["note"] = ("Under 70 — the maker (or an admin) can confirm it; it joins your "
                       "collection once they do.")
    return out


@router.get("/me/collection", summary="My claimed pieces and badges")
def my_collection(u: dict = Depends(require_user)):
    conn = _conn()
    coll = [{k: v for k, v in c.items() if k != "collector_uid"}
            for c in seasons.collection(conn, u["uid"])]
    return {"collection": coll, "badges": seasons.badges_for(conn, u["uid"]),
            "all_badges": seasons.BADGES,
            "entries": [_pub_entry(e, u["uid"]) for e in seasons.my_entries(conn, u["uid"])]}


@router.get("/me/claims", summary="Claims on the pieces I published (the maker's inbox)")
def my_claims(u: dict = Depends(require_user)):
    """Every claim collectors have made on the caller's published pieces —
    pending first, then newest first — so the capture/verify pages can show a
    maker what is waiting. Confirming happens in Glowtbook → Objects → Seasons."""
    conn = _conn()
    claims = [{k: v for k, v in c.items() if k != "collector_uid"}
              for c in seasons.claims_on_my_pieces(conn, u["uid"])]
    return {"claims": claims, "pending": sum(1 for c in claims if c["status"] == "pending"),
            "confirm_in": "Glowtbook → Objects → Seasons"}
