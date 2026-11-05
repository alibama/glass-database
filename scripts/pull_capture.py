#!/usr/bin/env python3
"""
Pull Glass ID capture sessions down for analysis.

The capture pages' *Send for analysis* stores each session on the server
(``central.captures``: one folder with ``manifest.json`` and ``images/``, indexed in
the central database). This pulls them to a laptop through the admin API, so a
later session - yours, or a Claude session - can read real captures instead of
guessing. The admin key is read from ``GLASSDB_ADMIN_TOKEN`` in the environment or
``.env`` and is never printed.

    python -m scripts.pull_capture --list [--kind enroll|verify] [--decision …] [--since YYYY-MM-DD]
    python -m scripts.pull_capture <manifest-id> [<manifest-id> …] [--out data/pulled_captures]
    python -m scripts.pull_capture --latest [--kind verify]
    python -m scripts.pull_capture --all [--since 2026-11-01]
    python -m scripts.pull_capture --local --list            # read the local store, no network, no key

``--base`` is the API root (default: ``GLASSDB_API_BASE``, else ``PUBLIC_BASE_URL`` +
``/api``, else https://glassdatabase.org/api). Each pulled session lands in
``<out>/<manifest-id>/`` exactly as stored; ``<out>/index.json`` lists what was pulled.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "data" / "pulled_captures"
DEFAULT_BASE = "https://glassdatabase.org/api"


def load_dotenv(path: Path | None = None) -> None:
    """KEY=VALUE lines from .env into the environment (never overriding what is set)."""
    p = path or (ROOT / ".env")
    if not p.is_file():
        return
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k = k.strip()
        v = v.split(" #")[0].strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def api_base(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    if os.environ.get("GLASSDB_API_BASE"):
        return os.environ["GLASSDB_API_BASE"].rstrip("/")
    pub = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return (pub + "/api") if pub else DEFAULT_BASE


def _client(base: str):
    import httpx
    key = os.environ.get("GLASSDB_ADMIN_TOKEN", "")
    if not key:
        raise SystemExit("GLASSDB_ADMIN_TOKEN is not set: put it in .env (never in a command line) or use --local.")
    return httpx.Client(base_url=base, headers={"X-API-Key": key}, timeout=120, follow_redirects=True)


# --- the two sources: the API, or the local store ----------------------------------------
class Remote:
    def __init__(self, base: str):
        self.c = _client(base)

    def list(self, **f) -> dict:
        q = {k: v for k, v in f.items() if v}
        r = self.c.get("/admin/captures", params=q)
        r.raise_for_status()
        return r.json()

    def one(self, mid: str) -> dict:
        r = self.c.get(f"/admin/captures/{mid}")
        r.raise_for_status()
        return r.json()

    def zip(self, mid: str) -> bytes:
        r = self.c.get(f"/admin/captures/{mid}/download")
        r.raise_for_status()
        return r.content


class Local:
    def __init__(self):
        from central import captures
        from central.dbconn import connect
        self.captures = captures
        self.conn = connect()
        captures.ensure(self.conn)

    def list(self, **f) -> dict:
        out = self.captures.list_manifests(self.conn, kind=f.get("kind"), decision=f.get("decision"),
                                           since=f.get("since"), q=f.get("q"), limit=int(f.get("limit") or 50),
                                           offset=int(f.get("offset") or 0))
        out["summary"] = self.captures.stats(self.conn)
        return out

    def one(self, mid: str) -> dict:
        d = self.captures.get(self.conn, mid)
        if not d:
            raise SystemExit(f"no such session: {mid}")
        return d

    def zip(self, mid: str) -> bytes:
        data = self.captures.zip_bytes(mid)
        if data is None:
            raise SystemExit(f"no files on disk for {mid}")
        return data


def unpack(zip_bytes: bytes, out: Path, mid: str) -> Path:
    dest = out / mid
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith(mid + "/"):
                name = name[len(mid) + 1:]
            if not name or ".." in name or name.startswith("/"):
                continue
            p = dest / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(z.read(info))
    return dest


def table(items: list[dict]) -> str:
    if not items:
        return "(no sessions)"
    cols = [("manifest_id", 29), ("created_at", 17), ("kind", 6), ("object_title", 24), ("decision", 16),
            ("confidence", 5), ("rating", 6), ("top_candidate", 20), ("top_score", 5), ("n_frames", 5),
            ("n_images", 6), ("device", 28)]
    head = " ".join(k[:w].ljust(w) for k, w in cols)
    lines = [head, "-" * len(head)]
    for it in items:
        row = []
        for k, w in cols:
            v = it.get(k)
            v = "" if v is None else str(v)
            if k == "created_at":
                v = v.replace("T", " ")[:16]
            row.append(v[:w].ljust(w))
        lines.append(" ".join(row))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="manifest ids to pull")
    ap.add_argument("--list", action="store_true", help="list sessions and stop")
    ap.add_argument("--latest", action="store_true", help="pull the newest session (with the filters)")
    ap.add_argument("--all", action="store_true", help="pull every session (with the filters)")
    ap.add_argument("--kind", choices=("enroll", "verify"), default=None)
    ap.add_argument("--decision", default=None)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD")
    ap.add_argument("--q", default=None, help="search words")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--base", default=None, help="API root, e.g. https://glassdatabase.org/api")
    ap.add_argument("--local", action="store_true", help="read the local store instead of the API")
    ap.add_argument("--env", default=None, help="a .env file to read the admin key from")
    a = ap.parse_args(argv)
    load_dotenv(Path(a.env) if a.env else None)
    src = Local() if a.local else Remote(api_base(a.base))
    out = Path(a.out)
    filters = {"kind": a.kind, "decision": a.decision, "since": a.since, "q": a.q, "limit": a.limit}

    if a.list or not (a.ids or a.latest or a.all):
        j = src.list(**filters)
        print(table(j.get("items") or []))
        s = j.get("summary")
        if s:
            print(f"\n{s.get('total', 0)} session(s) in all · by kind {s.get('by_kind')} · by decision {s.get('by_decision')}")
        if not (a.ids or a.latest or a.all):
            return 0

    ids = list(a.ids)
    if a.latest or a.all:
        j = src.list(**{**filters, "limit": 1 if a.latest else max(a.limit, 500)})
        ids += [it["manifest_id"] for it in (j.get("items") or [])]
    if not ids:
        print("nothing to pull")
        return 1
    index = []
    out.mkdir(parents=True, exist_ok=True)
    for mid in dict.fromkeys(ids):
        try:
            data = src.zip(mid)
        except SystemExit:
            raise
        except Exception as ex:  # noqa: BLE001
            print(f"  {mid}: could not pull ({ex})")
            continue
        dest = unpack(data, out, mid)
        try:
            man = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            man = {}
        n_img = len(list((dest / "images").glob("*"))) if (dest / "images").is_dir() else 0
        srv = man.get("server") or {}
        index.append({"manifest_id": mid, "dir": str(dest), "kind": man.get("kind"), "images": n_img,
                      "received_at": srv.get("received_at"), "decision": (man.get("decision") or {}).get("decision"),
                      "object": (man.get("decision") or {}).get("object") or man.get("object")})
        print(f"  {mid}: {n_img} picture(s) -> {dest}")
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"{len(index)} session(s) in {out} (index.json lists them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
