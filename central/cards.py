"""
central.cards
=============
Share cards: 1080×1350 (4:5) PNGs on the paper ground, the size Instagram and
Bluesky show well. One renderer for a single piece (an entry, a published piece)
and one for a grid (a collection). Pillow only; a QR (segno) when available.
"""
from __future__ import annotations

import io

W, H = 1080, 1350
PAPER, PAPER2, PAPER3 = (244, 238, 228), (237, 228, 214), (227, 215, 196)
INK, INK2, MUTED, RULE, RED = (31, 26, 22), (74, 66, 58), (107, 97, 87), (207, 194, 174), (179, 38, 30)


def font(size: int, serif: bool = True):
    from PIL import ImageFont
    names = (["DejaVuSerif.ttf", "georgia.ttf", "Georgia.ttf", "times.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"] if serif else
             ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def wrap(draw, text: str, f, width: int, max_lines: int = 2) -> list[str]:
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;:") + "…"
    return lines


def _open(image_bytes: bytes | None):
    if not image_bytes:
        return None
    try:
        from PIL import Image
        return Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _qr(card, link: str, x: int, y: int, size: int = 200) -> None:
    try:
        import segno
        from PIL import Image
        qr = segno.make(link, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=6, border=1, dark="#1F1A16", light="#F4EEE4")
        buf.seek(0)
        card.paste(Image.open(buf).convert("RGB").resize((size, size), Image.NEAREST), (x, y))
    except Exception:  # noqa: BLE001
        pass


def _footer(draw, card, link: str, footer_url: str, footer_note: str) -> None:
    draw.line((72, H - 118, W - 72, H - 118), fill=INK, width=1)
    draw.text((72, H - 92), footer_url, fill=RED, font=font(26, serif=False))
    draw.text((72, H - 54), footer_note, fill=MUTED, font=font(20, serif=False))
    if link:
        _qr(card, link, W - 72 - 200, H - 118 - 200 - 18)


def _frame(draw, box, fill=PAPER2):
    draw.rectangle(box, outline=INK, width=2, fill=fill)


def render_piece(image_bytes: bytes | None, *, eyebrow: str, title: str, who: str = "",
                 meta: str = "", note: str = "", link: str = "",
                 footer_url: str = "glassdatabase.org",
                 footer_note: str = "Scan to see the piece") -> bytes:
    """One piece: the picture in a frame, title, maker · studio, a meta line, a note."""
    from PIL import Image, ImageDraw, ImageOps

    card = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(card)
    fx0, fy0, fx1, fy1 = 72, 128, W - 72, 896
    _frame(draw, (fx0, fy0, fx1, fy1))
    img = _open(image_bytes)
    if img is not None:
        pad = 18
        box = (fx1 - fx0 - 2 * pad, fy1 - fy0 - 2 * pad)
        img = ImageOps.contain(img, box)
        card.paste(img, (fx0 + pad + (box[0] - img.width) // 2, fy0 + pad + (box[1] - img.height) // 2))
    else:
        f = font(30, serif=False)
        msg = "No photo yet"
        draw.text(((W - draw.textlength(msg, font=f)) / 2, (fy0 + fy1) / 2 - 15), msg, fill=MUTED, font=f)
    draw.text((72, 64), (eyebrow or "").upper(), fill=RED, font=font(24, serif=False))
    y = 940
    tf = font(60)
    for line in wrap(draw, title or "Untitled", tf, W - 144, 2):
        draw.text((72, y), line, fill=INK, font=tf); y += 70
    y += 6
    if who:
        draw.text((72, y), who, fill=INK2, font=font(32, serif=False)); y += 46
    if meta:
        draw.text((72, y), meta, fill=MUTED, font=font(24, serif=False)); y += 40
    if note:
        for line in wrap(draw, note, font(28), W - 144 - 230, 2):
            draw.text((72, y), line, fill=RED, font=font(28)); y += 36
    _footer(draw, card, link, footer_url, footer_note)
    out = io.BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()


def render_grid(images: list[bytes | None], *, eyebrow: str, title: str, who: str = "",
                meta: str = "", link: str = "", footer_url: str = "glassdatabase.org",
                footer_note: str = "Scan to see the collection") -> bytes:
    """Up to four pictures in a 2×2 grid: a collection, a studio's season, a set."""
    from PIL import Image, ImageDraw, ImageOps

    card = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(card)
    gx0, gy0, gx1, gy1 = 72, 128, W - 72, 896
    gap = 16
    cw, ch = (gx1 - gx0 - gap) // 2, (gy1 - gy0 - gap) // 2
    cells = [(gx0, gy0), (gx0 + cw + gap, gy0), (gx0, gy0 + ch + gap), (gx0 + cw + gap, gy0 + ch + gap)]
    imgs = (list(images or []) + [None] * 4)[:4]
    for (cx, cy), b in zip(cells, imgs):
        _frame(draw, (cx, cy, cx + cw, cy + ch), fill=PAPER2 if b else PAPER3)
        img = _open(b)
        if img is not None:
            img = ImageOps.fit(img, (cw - 12, ch - 12))
            card.paste(img, (cx + 6, cy + 6))
    draw.text((72, 64), (eyebrow or "").upper(), fill=RED, font=font(24, serif=False))
    y = 940
    tf = font(60)
    for line in wrap(draw, title or "A collection", tf, W - 144, 2):
        draw.text((72, y), line, fill=INK, font=tf); y += 70
    y += 6
    if who:
        draw.text((72, y), who, fill=INK2, font=font(32, serif=False)); y += 46
    if meta:
        draw.text((72, y), meta, fill=MUTED, font=font(24, serif=False)); y += 40
    _footer(draw, card, link, footer_url, footer_note)
    out = io.BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()


def to_jpeg(png_bytes: bytes, max_bytes: int = 950_000) -> bytes:
    """Bluesky caps image blobs near 1 MB: re-encode a card as JPEG under the cap."""
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    for q in (88, 80, 72, 64, 55):
        out = io.BytesIO()
        im.save(out, "JPEG", quality=q, optimize=True)
        if out.tell() <= max_bytes:
            return out.getvalue()
    im = im.resize((im.width * 3 // 4, im.height * 3 // 4))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=60, optimize=True)
    return out.getvalue()
