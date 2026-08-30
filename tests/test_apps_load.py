"""Smoke test: every Streamlit surface imports and renders without error."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APPS = ["explore/app.py", "glowtbook/app.py", "admin/app.py"]


@pytest.mark.parametrize("path", APPS)
def test_app_loads(demo_db, path):
    at = AppTest.from_file(str(ROOT / path), default_timeout=120).run()
    assert not at.exception, at.exception
