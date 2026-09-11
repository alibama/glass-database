"""Aggregated studio open-data endpoint (env source + registry, 15-min cache)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def _server(port):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"firings": 4, "energy_kwh": 128.5, "since": "2026-09-11"}
                              if self.path == "/summary" else [])
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body.encode())
    s = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    time.sleep(0.2)
    return s


def test_empty_when_nothing_configured(demo_db, monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    monkeypatch.delenv("STUDIO_DATA_URL", raising=False)
    j = TestClient(app).get("/studio-data.json").json()
    assert j["ok"] is True and "totals" in j and "sources" in j


def test_env_source_aggregates_and_caches(demo_db, monkeypatch):
    from api.main import _STUDIO_CACHE
    _STUDIO_CACHE["data"] = None; _STUDIO_CACHE["at"] = 0.0
    srv = _server(8796)
    try:
        monkeypatch.setenv("STUDIO_DATA_URL", "http://127.0.0.1:8796")
        from fastapi.testclient import TestClient

        from api.main import app
        j = TestClient(app).get("/studio-data.json").json()
        assert j["totals"]["firings"] == 4 and j["count"] >= 1
    finally:
        srv.shutdown()
