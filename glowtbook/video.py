"""
glowtbook.video
===============
Condensed video renditions for the DIP. The AIP keeps the original untouched;
this produces a web-friendly derivative (downscaled H.264 MP4, +faststart) plus
a poster frame, mirroring the image AIP→DIP condensing in media.py.

Shells out to ffmpeg/ffprobe. If they aren't installed the caller falls back to
keeping video AIP-only (its previous behaviour), so this is purely additive.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_HEIGHT = 720       # cap the condensed rendition
CRF = 28               # H.264 quality/size trade-off (higher = smaller)
POSTER_AT = 1.0        # seconds into the clip to grab the poster


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path: str | Path) -> dict:
    """Return {duration, width, height, codec} via ffprobe, best-effort."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60).stdout
        data = json.loads(out or "{}")
    except Exception:
        return {}
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = data.get("format", {}).get("duration")
    return {"duration": float(dur) if dur else None,
            "width": v.get("width"), "height": v.get("height"),
            "codec": v.get("codec_name")}


def transcode(src_path: str | Path, max_h: int = MAX_HEIGHT, crf: int = CRF):
    """Return (mp4_bytes, poster_jpeg_bytes, meta). Raises if ffmpeg fails."""
    src_path = str(src_path)
    src_meta = probe(src_path)
    with tempfile.TemporaryDirectory() as d:
        mp4 = os.path.join(d, "dip.mp4")
        poster = os.path.join(d, "poster.jpg")
        # even dimensions required by H.264; scale down only if taller than max_h
        vf = f"scale=-2:'min({max_h},ih)':flags=lanczos"
        subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-c:a", "aac", "-b:a", "96k", "-ac", "2", mp4],
            capture_output=True, check=True, timeout=1800)
        # poster: a frame near the start, downscaled
        ss = min(POSTER_AT, (src_meta.get("duration") or POSTER_AT) / 2)
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(ss), "-i", src_path, "-frames:v", "1",
             "-vf", f"scale=-2:'min({max_h},ih)'", "-q:v", "3", poster],
            capture_output=True, check=True, timeout=120)
        mp4_bytes = Path(mp4).read_bytes()
        poster_bytes = Path(poster).read_bytes()
    out_meta = probe_bytes_hint(src_meta, len(mp4_bytes))
    return mp4_bytes, poster_bytes, out_meta


def probe_bytes_hint(src_meta: dict, mp4_len: int) -> dict:
    return {"source": src_meta, "dip_bytes": mp4_len,
            "dip_max_height": MAX_HEIGHT, "dip_codec": "h264/aac"}
