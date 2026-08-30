"""Video transcode to a condensed DIP + poster."""
import tempfile
from pathlib import Path

import pytest

from glowtbook import video


def test_transcode_downscales(sample_video_path):
    if not video.available():
        pytest.skip("ffmpeg not installed")
    mp4, poster, meta = video.transcode(sample_video_path, max_h=480)
    assert mp4[:12] and len(poster) > 0
    out = Path(tempfile.mktemp(suffix=".mp4"))
    out.write_bytes(mp4)
    info = video.probe(out)
    assert info["codec"] == "h264" and info["height"] <= 480
