"""Shared fixtures. A temp demo DB is seeded once per session; media samples
are generated on demand so tests don't depend on any real data."""
import io
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory):
    """Seed a throwaway demo database and point the whole stack at it."""
    db = tmp_path_factory.mktemp("db") / "glassdb.db"
    import os
    os.environ["GLASSDB_PATH"] = str(db)
    os.environ["ROOT_PATH"] = ""
    os.environ.setdefault("GLASSDB_MODERATION", "1")
    from scripts.seed_demo import seed
    seed()
    return db


@pytest.fixture
def sample_image_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1200), (150, 60, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def sample_video_path(tmp_path):
    if not (subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0):
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=2",
         "-c:v", "libx264", "-t", "2", "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, check=True)
    return out
