#!/usr/bin/env python3
"""
Print the back of the Photo Mat: a QR code to Check a Piece, and what the mat is for.

    python -m scripts.make_mat_back                       # writes the three files below
    python -m scripts.make_mat_back --base https://glassdatabase.org --code mat

Writes, into deploy/fingerprint-web/:

* ``capture-mat-back.png`` / ``capture-mat-back.pdf``: the back alone (US Letter, 300 dpi)
* ``capture-mat-2sided.pdf``: page 1 the mat (capture-mat.pdf's image), page 2 this back,
  for printing double-sided ("flip on long edge")

The QR goes to ``<base>/api/go/<code>``, which counts the scan (central.analytics.QR_CODES,
shown on /analytics.html) and forwards to the page. Fonts: Georgia/Arial/Courier New when
the system has them, else Pillow's built-in font.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "deploy" / "fingerprint-web"

DPI = 300
W, H = int(8.5 * DPI), int(11 * DPI)
MM = DPI / 25.4
INK, DIM, RED, RULE = (31, 26, 22), (74, 66, 58), (179, 38, 30), (207, 194, 174)

_FONT_DIRS = ["/System/Library/Fonts/Supplemental", "/Library/Fonts", "/usr/share/fonts/truetype/msttcorefonts",
              "/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts"]
_FONTS = {"serif": ["Georgia.ttf", "georgia.ttf", "DejaVuSerif.ttf"],
          "serif-i": ["Georgia Italic.ttf", "georgiai.ttf", "DejaVuSerif-Italic.ttf"],
          "sans": ["Arial.ttf", "arial.ttf", "DejaVuSans.ttf"],
          "sans-b": ["Arial Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
          "mono": ["Courier New.ttf", "cour.ttf", "DejaVuSansMono.ttf"]}


def font(kind: str, px: int):
    from PIL import ImageFont
    for d in _FONT_DIRS:
        for name in _FONTS[kind]:
            p = Path(d) / name
            if p.is_file():
                return ImageFont.truetype(str(p), px)
    return ImageFont.load_default(size=px)


def wrap(draw, text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= width or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def para(draw, xy, text, f, width, fill=DIM, gap=1.38):
    x, y = xy
    for line in wrap(draw, text, f, width):
        draw.text((x, y), line, font=f, fill=fill)
        y += int(f.size * gap)
    return y


def qr_image(url: str, px: int):
    import segno
    from PIL import Image
    q = segno.make(url, error="q")
    buf = io.BytesIO()
    q.save(buf, kind="png", scale=1, border=0, dark="#1F1A16", light="#FFFFFF")
    img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    n = img.size[0]
    return img.resize((px - px % n, px - px % n), Image.NEAREST)


def back(url: str, shown: str):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    L, R = int(0.85 * DPI), W - int(0.85 * DPI)
    y = int(0.8 * DPI)
    d.text((L, y), "GLASS ID  ·  PHOTO MAT  ·  ALPHA", font=font("mono", 40), fill=RED)
    y += 90
    d.text((L, y), "Check a piece", font=font("serif", 150), fill=INK)
    y += 175
    d.text((L, y), "of glass with your phone", font=font("serif-i", 96), fill=RED)
    y += 170

    # QR on the right, the "what this is" on the left
    qpx = int(2.9 * DPI)
    qr = qr_image(url, qpx)
    qx, qy = R - qr.size[0], y
    pad = 36
    d.rectangle([qx - pad, qy - pad, qx + qr.size[0] + pad, qy + qr.size[1] + pad], outline=INK, width=6)
    im.paste(qr, (qx, qy))
    cap = font("mono", 34)
    cw = d.textlength(shown, font=cap)
    d.text((qx + qr.size[0] / 2 - cw / 2, qy + qr.size[1] + pad + 24), shown, font=cap, fill=INK)
    scan = font("sans-b", 44)
    sw = d.textlength("Scan to check a piece", font=scan)
    d.text((qx + qr.size[0] / 2 - sw / 2, qy - pad - 76), "Scan to check a piece", font=scan, fill=INK)

    colw = qx - pad - L - 90
    body = font("sans", 44)
    head = font("sans-b", 46)
    d.text((L, y), "What this sheet is", font=head, fill=INK)
    y = para(d, (L, y + 70), "The other side is the Photo Mat. Its four black corner squares let the camera "
             "measure the piece and correct its colour, so a Glass ID taken here can be matched later "
             "on any phone.", body, colw)
    y += 40
    d.text((L, y), "A Glass ID", font=head, fill=INK)
    y = para(d, (L, y + 70), "is a fingerprint of the glass itself, read from photos and stored as "
             "numbers. It shows who made a piece, and lets a collector add it to their collection "
             "with its story.", body, colw)

    y = max(y, qy + qr.size[1] + pad + 110) + 70
    d.line([L, y, R, y], fill=RULE, width=4)
    y += 60

    # two columns of steps
    gap = 120
    cw2 = (R - L - gap) // 2
    step = font("sans", 42)
    num = font("serif", 60)

    def steps(x, y0, title, items):
        d.text((x, y0), title, font=head, fill=INK)
        yy = y0 + 84
        for i, t in enumerate(items, 1):
            d.text((x, yy - 10), str(i), font=num, fill=RED)
            yy = para(d, (x + 70, yy), t, step, cw2 - 70, fill=DIM) + 26
        return yy

    y1 = steps(L, y, "Picked up a piece?", [
        "Scan the code, or open glassdatabase.org and choose Check a Piece.",
        "Stand the piece on this mat (grey side up) or anywhere plain and bright.",
        "Tap Open the camera and find my piece. Take 3 to 8 shots as you walk round it.",
        "Pick the piece that comes up, keep circling until it says Match likely.",
        "Add it to your collection: say how you got it, when and where.",
    ])
    y2 = steps(L + cw2 + gap, y, "Made the piece?", [
        "Open glassdatabase.org/fingerprint/enroll.html and sign in.",
        "Stand the piece in the grey area with all four squares in view.",
        "Circle it slowly at two or three heights until the gauge reads Strong.",
        "Tap Give it its Glass ID. The size is measured from the mat.",
    ])
    y = max(y1, y2) + 40
    d.line([L, y, R, y], fill=RULE, width=4)
    y += 60

    # print-scale check
    d.text((L, y), "Printed at the right size?", font=head, fill=INK)
    y = para(d, (L, y + 70), "Print the mat at 100% (Actual size), never Fit to page. This bar and each black "
             "square on the front should both measure exactly as marked; if not, measurements will be off.",
             body, R - L)
    y += 30
    bar = int(50 * MM)
    d.rectangle([L, y, L + bar, y + 34], fill=INK)
    for k in range(0, 51, 10):
        x = L + int(k * MM)
        d.line([x, y + 34, x, y + 64], fill=INK, width=4)
    d.text((L + bar + 40, y - 6), "50 mm", font=font("mono", 44), fill=INK)
    d.text((L + bar + 260, y - 6), "Each black square on the front: 30 mm", font=font("mono", 36), fill=DIM)

    foot = font("mono", 32)
    d.text((L, H - int(0.7 * DPI)), "glassdatabase.org  ·  Glass ID is in alpha: thank you for testing.",
           font=foot, fill=DIM)
    d.text((L, H - int(0.7 * DPI) + 48), "Scans of this code are counted without storing who you are.",
           font=foot, fill=DIM)
    return im


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="https://glassdatabase.org")
    ap.add_argument("--code", default="mat")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    from PIL import Image
    from central.analytics import QR_CODES
    if a.code not in QR_CODES:
        print(f"unknown code {a.code!r}; add it to central.analytics.QR_CODES first", file=sys.stderr)
        return 2
    url = f"{a.base.rstrip('/')}/api/go/{a.code}"
    shown = url.split("://", 1)[-1]
    im = back(url, shown)
    a.out.mkdir(parents=True, exist_ok=True)
    im.save(a.out / "capture-mat-back.png", dpi=(DPI, DPI), optimize=True)
    im.save(a.out / "capture-mat-back.pdf", resolution=DPI)
    front_pdf = a.out / "capture-mat.pdf"
    if front_pdf.is_file():
        front = _pdf_image(front_pdf)
        if front is not None:
            front.save(a.out / "capture-mat-2sided.pdf", resolution=DPI, save_all=True, append_images=[im])
    print(f"QR -> {url}\nwrote {a.out / 'capture-mat-back.png'}, capture-mat-back.pdf"
          + (", capture-mat-2sided.pdf" if (a.out / "capture-mat-2sided.pdf").is_file() else ""))
    return 0


def _pdf_image(path: Path):
    """The page image inside capture-mat.pdf (a one-image PDF Pillow wrote)."""
    from PIL import Image
    data = path.read_bytes()
    s = data.find(b"\xff\xd8")
    e = data.rfind(b"\xff\xd9")
    if s < 0 or e < 0:
        return None
    img = Image.open(io.BytesIO(data[s:e + 2])).convert("RGB")
    return img if img.size == (W, H) else img.resize((W, H))


if __name__ == "__main__":
    raise SystemExit(main())
