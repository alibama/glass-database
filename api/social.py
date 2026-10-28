"""
api.social
==========
Promote work and collections.

Connected accounts (signed in):
  GET    /social/status                       what is connected (no secrets)
  POST   /social/bluesky/connect              {handle, app_password} → verified + stored encrypted
  DELETE /social/bluesky                      forget it
  POST   /social/bluesky/post                 {text, image?, alt?, links?} → {url}
                                              image = "entry:<id>" | "object:<row_id>" | "collection"
  GET    /social/shares                       what I shared lately

Public collections:
  POST   /social/collections/share {name?}    opt in → {share_id, url}   (signed in)
  DELETE /social/collections/share            withdraw                   (signed in)
  GET    /social/collections/{share_id}       the collection, public
  GET    /social/collections/{share_id}/card.png

Share landing pages (what you hand to Facebook, Bluesky link cards, iMessage…):
  GET    /social/share/{kind}/{ref}           HTML with Open Graph tags, then on to the real page
  GET    /social/objects/{row_id}/card.png    share card for a published piece

A share card is a 1080×1350 PNG from central.cards. Bluesky posts carry it as a
JPEG under 1 MB.
"""
from __future__ import annotations

import html
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from api.auth import current_user, require_user  # noqa: E402
from central import seasons, social  # noqa: E402
from central.dbconn import connect  # noqa: E402

router = APIRouter(prefix="/social", tags=["social"])
SITE = "Glass Database"


def _conn():
    c = connect()
    social.ensure(c)
    return c


def _base(request: Request) -> str:
    env = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


# --- connected accounts ---------------------------------------------------------------
@router.get("/status", summary="Connected accounts (no secrets)")
def status(u: dict = Depends(require_user)):
    return social.status(_conn(), u["uid"])


@router.post("/bluesky/connect", summary="Connect Bluesky with an app password (stored encrypted)")
def bluesky_connect(payload: dict, u: dict = Depends(require_user)):
    try:
        return social.connect_bluesky(_conn(), u["uid"], payload.get("handle", ""),
                                      payload.get("app_password", ""))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@router.delete("/bluesky", summary="Disconnect Bluesky")
def bluesky_disconnect(u: dict = Depends(require_user)):
    social.disconnect(_conn(), u["uid"], "bluesky")
    return {"ok": True}


def _image_for(conn, ref: str, uid: str, base: str) -> tuple[bytes | None, str]:
    """Resolve a share image reference to (png bytes, alt)."""
    from api.seasons import primary_image_bytes, render_card
    if not ref:
        return None, ""
    kind, _, ident = ref.partition(":")
    if kind == "entry":
        e = seasons.get_entry(conn, int(ident))
        s = seasons.get_season(conn, e["season_id"]) if e else None
        if not e or not s:
            raise HTTPException(404, "no such entry")
        return render_card(conn, e, s, f"{base}/games.html#entry-{e['id']}"), e.get("title") or ""
    if kind == "object":
        return object_card_png(conn, ident, base), ident
    if kind == "collection":
        sh = social.my_collection_share(conn, uid)
        if not sh:
            raise HTTPException(400, "share your collection first")
        return collection_card_png(conn, sh["share_id"], base), "My collection"
    if kind == "photo":
        return primary_image_bytes(conn, ident), ident
    raise HTTPException(400, "image must be entry:<id>, object:<row_id>, photo:<row_id> or collection")


@router.post("/bluesky/post", summary="Post to Bluesky with the connected account")
def bluesky_post(payload: dict, request: Request, u: dict = Depends(require_user)):
    conn = _conn()
    creds = social.credentials(conn, u["uid"], "bluesky")
    if not creds:
        raise HTTPException(409, "Connect Bluesky first (Glowtbook → My profile)")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "nothing to post")
    links = [x for x in (payload.get("links") or []) if isinstance(x, str) and x.startswith("http")]
    img, alt = _image_for(conn, payload.get("image") or "", u["uid"], _base(request))
    jpeg = None
    if img:
        from central import cards
        jpeg = cards.to_jpeg(img)
    from glowtbook import bluesky
    try:
        url = bluesky.post(creds[0], creds[1], text, jpeg, "image/jpeg",
                           alt=payload.get("alt") or alt, links=links)
    except Exception as ex:  # noqa: BLE001
        social.mark_used(conn, u["uid"], "bluesky", f"{ex}")
        raise HTTPException(502, f"Bluesky didn't take the post: {ex}")
    social.mark_used(conn, u["uid"], "bluesky")
    kind, _, ident = (payload.get("image") or "text:").partition(":")
    social.log_share(conn, u["uid"], "bluesky", kind or "text", ident or "", url)
    return {"ok": True, "url": url}


@router.get("/shares", summary="What I shared lately")
def my_shares(u: dict = Depends(require_user)):
    return {"shares": social.shares_for(_conn(), u["uid"])}


# --- public collections -----------------------------------------------------------------
@router.post("/collections/share", summary="Make my collection public at an unguessable link")
def share_collection(payload: dict, request: Request, u: dict = Depends(require_user)):
    sh = social.share_collection(_conn(), u["uid"], (payload or {}).get("name") or u.get("name") or "")
    return {"share_id": sh["share_id"], "name": sh["name"],
            "url": f"/collection.html?c={sh['share_id']}",
            "share": f"{_base(request)}/api/social/share/collection/{sh['share_id']}"}


@router.delete("/collections/share", summary="Withdraw my collection's public link")
def unshare_collection(u: dict = Depends(require_user)):
    social.unshare_collection(_conn(), u["uid"])
    return {"ok": True}


@router.get("/collections/mine", summary="My collection's share state")
def my_collection_share(u: dict = Depends(require_user)):
    sh = social.my_collection_share(_conn(), u["uid"])
    return {"shared": bool(sh), **({"share_id": sh["share_id"], "name": sh["name"],
                                    "url": f"/collection.html?c={sh['share_id']}"} if sh else {})}


@router.get("/collections/{share_id}", summary="A shared collection (public)")
def collection(share_id: str):
    c = social.public_collection(_conn(), share_id)
    if not c:
        raise HTTPException(404, "no such collection")
    return c


def collection_card_png(conn, share_id: str, base: str) -> bytes:
    from api.seasons import primary_image_bytes
    from central import cards
    c = social.public_collection(conn, share_id)
    if not c:
        raise HTTPException(404, "no such collection")
    imgs = [primary_image_bytes(conn, p["object_row_id"]) for p in c["pieces"][:4]]
    n = c["count"]
    return cards.render_grid(imgs, eyebrow="A collection on the Glass Database",
                             title=f"{c['name']}'s collection",
                             who=f"{n} piece{'s' if n != 1 else ''} · {c['points']} points",
                             meta="Every piece checked against its Glass ID",
                             link=f"{base}/collection.html?c={share_id}",
                             footer_url="glassdatabase.org", footer_note="Scan to see the collection")


@router.get("/collections/{share_id}/card.png", summary="Share card for a collection (1080×1350 PNG)")
def collection_card(share_id: str, request: Request):
    png = collection_card_png(_conn(), share_id, _base(request))
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=600"})


# --- share cards for published pieces -------------------------------------------------
def object_card_png(conn, row_id: str, base: str) -> bytes:
    from api.seasons import primary_image_bytes
    from central import cards
    o = seasons._public_object(conn, row_id)
    if not o:
        raise HTTPException(404, "no such public piece")
    who = " · ".join(x for x in [o.get("maker")] if x)
    meta = " · ".join(x for x in [str(o.get("year") or ""), o.get("techniques") or ""] if x)
    return cards.render_piece(primary_image_bytes(conn, row_id), eyebrow="On the Glass Database",
                              title=o.get("title") or "Untitled", who=who, meta=meta,
                              link=f"{base}/api/social/share/object/{row_id}",
                              footer_url="glassdatabase.org", footer_note="Scan to see the piece and who made it")


@router.get("/objects/{row_id}/card.png", summary="Share card for a published piece (1080×1350 PNG)")
def object_card(row_id: str, request: Request):
    png = object_card_png(_conn(), row_id, _base(request))
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


# --- share landing pages (Open Graph) --------------------------------------------------
def share_target(conn, kind: str, ref: str, base: str) -> dict | None:
    """Title, description, image and destination for a shareable thing."""
    if kind == "entry":
        try:
            e = seasons.get_entry(conn, int(ref))
        except (TypeError, ValueError):
            e = None
        if not e or e.get("status") != "approved":
            return None
        s = seasons.get_season(conn, e["season_id"]) or {}
        who = " · ".join(x for x in [e.get("artist_name"), e.get("studio")] if x)
        return {"title": e.get("title") or "A piece", "description": f"{who} · {s.get('title') or 'Collecting event'} on the Glass Database",
                "image": f"{base}/api/seasons/entries/{e['id']}/card.png",
                "url": f"{base}/games.html#entry-{e['id']}"}
    if kind == "object":
        o = seasons._public_object(conn, ref)
        if not o:
            return None
        return {"title": o.get("title") or "A piece", "description": f"Made by {o.get('maker') or 'a maker'} · on the Glass Database",
                "image": f"{base}/api/social/objects/{ref}/card.png",
                "url": f"{base}/explore/?view=objects#obj-{ref}"}
    if kind == "collection":
        c = social.public_collection(conn, ref)
        if not c:
            return None
        return {"title": f"{c['name']}'s collection", "description": f"{c['count']} pieces, each checked against its Glass ID",
                "image": f"{base}/api/social/collections/{ref}/card.png",
                "url": f"{base}/collection.html?c={ref}"}
    if kind == "splat":
        from api import splats as _splats
        c = connect()
        _splats.ensure(c)
        r = c.execute("SELECT * FROM splats WHERE id=?", (ref,)).fetchone()
        if not r:
            return None
        return {"title": f"{r['title'] or 'A piece'} · 3D Photo", "description": "Turn it in your hands, on the Glass Database",
                "image": f"{base}/splat/demo-pumpkin.jpg", "url": f"{base}/splat.html?s={ref}"}
    return None


@router.get("/share/{kind}/{ref}", summary="Share landing page with Open Graph tags", response_class=HTMLResponse)
def share_page(kind: str, ref: str, request: Request):
    t = share_target(_conn(), kind, ref, _base(request))
    if not t:
        raise HTTPException(404, "nothing to share here")
    e = {k: html.escape(str(v), quote=True) for k, v in t.items()}
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{e['title']} — {SITE}</title>
<meta name="description" content="{e['description']}">
<meta property="og:type" content="website"><meta property="og:site_name" content="{SITE}">
<meta property="og:title" content="{e['title']}"><meta property="og:description" content="{e['description']}">
<meta property="og:image" content="{e['image']}"><meta property="og:image:width" content="1080"><meta property="og:image:height" content="1350">
<meta property="og:url" content="{e['url']}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e['title']}">
<meta name="twitter:description" content="{e['description']}"><meta name="twitter:image" content="{e['image']}">
<link rel="canonical" href="{e['url']}">
<meta http-equiv="refresh" content="0; url={e['url']}">
<style>body{{font-family:Georgia,serif;background:#F4EEE4;color:#1F1A16;padding:3rem;max-width:40rem;margin:auto}}a{{color:#B3261E}}</style>
</head><body><p>{e['title']}</p><p>{e['description']}</p><p><a href="{e['url']}">Open it on the Glass Database →</a></p></body></html>""")


@router.get("/share-target/{kind}/{ref}", summary="What a share link resolves to (JSON)")
def share_target_json(kind: str, ref: str, request: Request):
    t = share_target(_conn(), kind, ref, _base(request))
    if not t:
        raise HTTPException(404, "nothing to share here")
    t["signed_in"] = current_user(request) is not None
    return t
