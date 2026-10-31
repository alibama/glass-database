"""
api.wikibase — the migration to glass.wikibase.cloud, viewable by anyone, run by admins
=====================================================================================
What ``/wikibase.html`` reads and presses. The pipeline itself is ``central.wikibase``
(stage → dedupe → OpenRefine → bootstrap → dry run → push → pull); jobs run in the
background with a log the page polls, and their state is in the database so every API
worker sees it.

Public (the migration is part of the record):
  GET  /wikibase/status                 counts, steps, settings without secrets, the last runs
  GET  /wikibase/items?kind&status&q    staged items, paged           GET /wikibase/items/{key}
  GET  /wikibase/clusters?kind          duplicate clusters for review
  GET  /wikibase/runs · /runs/{id}?after=N   a run and its log lines after line N
  GET  /wikibase/plan/{id}              the dry run's plan (every request body)
  GET  /wikibase/go/{id}                resolver: a key or a Q id → the page on the site / the wiki
  GET|POST /wikibase/reconcile          a W3C reconciliation service over the staged items
  GET  /wikibase/preview/{id}           the little preview OpenRefine shows for a candidate
  GET  /wikibase/openrefine-manifest.json   the manifest OpenRefine's Wikibase extension loads

Admin (``require_admin``: an admin email, or X-API-Key):
  GET|POST /wikibase/settings           wiki URL, bot user, bot password (write-only), bot flag, pause
  POST /wikibase/settings/test          sign in and report rights (nothing written)
  POST /wikibase/jobs                   {kind: stage|dedupe|bootstrap|dry-run|push|pull|check, params}
  POST /wikibase/items/{key}            {status?, wikidata_qid?, label?, description?, aliases?, split?}
  GET  /wikibase/refine/{kind}.csv      CSV out to OpenRefine
  POST /wikibase/refine/import          the edited CSV back (multipart "file" or text/csv body)
"""
from __future__ import annotations

import json
import os
import re
import sys
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.auth import require_admin  # noqa: E402
from central.wikibase import dedupe, jobs, push, refine, stage, store, sync  # noqa: E402
from central.wikibase.model import CLASSES, KIND_ORDER, PROPERTIES  # noqa: E402

router = APIRouter(prefix="/wikibase", tags=["wikibase"])
STEPS = ("settings", "stage", "dedupe", "refine", "bootstrap", "dry-run", "push", "pull")


def _conn():
    return store.open_db()


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _slim(r: dict) -> dict:
    keep = ("key", "kind", "label", "description", "status", "cluster", "canonical", "qid", "wikidata_qid",
            "source_table", "source_row", "error", "notes", "pushed_at", "staged_at", "updated_at")
    d = {k: r.get(k) for k in keep}
    try:
        it = json.loads(r.get("item_json") or "{}")
        d["url"] = it.get("url", "")
        d["claims"] = len(it.get("claims") or [])
        d["aliases"] = it.get("aliases") or []
    except ValueError:
        d["url"], d["claims"], d["aliases"] = "", 0, []
    return d


def _last(runs: list[dict], kind: str) -> dict | None:
    for r in runs:
        if r["kind"] == kind:
            return {k: r.get(k) for k in ("id", "kind", "status", "started_at", "finished_at", "by_email")} | \
                   {"summary": _small_summary(r.get("summary") or {})}
    return None


def _small_summary(s: dict) -> dict:
    out = {}
    for k in ("items", "kinds", "statuses", "orphaned", "dropped", "places", "clusters", "duplicates", "by_kind",
              "creates", "updates", "api_writes", "failures_by_code", "dropped_snaks", "seconds", "dry_run",
              "fetched", "drift", "ok", "properties", "keys_on_wiki", "sparql", "login", "error", "plan"):
        if k in s:
            out[k] = s[k]
    if "login" in out and isinstance(out["login"], dict):
        out["login"] = {k: v for k, v in out["login"].items() if k != "password"}
    return out


@router.get("/status", summary="Where the migration stands")
def status(request: Request):
    conn = _conn()
    s = store.get_settings(conn)
    counts = store.counts(conn)
    runs = store.runs(conn, 40)
    busy = jobs.running(conn)
    cache_n = conn.execute("SELECT COUNT(*) FROM wb_cache").fetchone()[0]
    wd = conn.execute("SELECT COUNT(*) FROM wb_items WHERE wikidata_qid<>''").fetchone()[0]
    props_on_wiki = counts["properties"]
    steps = {
        "settings": {"done": bool(s["user"] and s["password_set"]), "url": s["url"], "user": s["user"]},
        "stage": {"done": counts["total"] > 0, "last": _last(runs, "stage"), "items": counts["total"]},
        "dedupe": {"done": bool(_last(runs, "dedupe")), "last": _last(runs, "dedupe"), **dedupe.summary(conn)},
        "refine": {"done": wd > 0, "with_wikidata": wd, "kinds": [c for c in KIND_ORDER if c in counts["kinds"]]},
        "bootstrap": {"done": props_on_wiki >= len(PROPERTIES), "properties_on_wiki": props_on_wiki,
                      "properties_total": len(PROPERTIES), "last": _last(runs, "bootstrap")},
        "dry-run": {"done": bool(_last(runs, "dry-run")), "last": _last(runs, "dry-run")},
        "push": {"done": counts["live"] > 0, "live": counts["live"], "pending": counts["pending"],
                 "failed": counts["status"].get("failed", 0), "last": _last(runs, "push")},
        "pull": {"done": cache_n > 0, "cached": cache_n, "last": _last(runs, "pull")},
    }
    return {"wiki": s["url"], "public_base": s["public_base"], "configured": steps["settings"]["done"],
            "counts": counts, "changes": sync.changes(conn), "steps": steps,
            "running": ({k: busy.get(k) for k in ("id", "kind", "started_at", "by_email")} if busy else None),
            "runs": [{k: r.get(k) for k in ("id", "kind", "status", "started_at", "finished_at", "by_email")}
                     | {"summary": _small_summary(r.get("summary") or {})} for r in runs[:12]],
            "classes": [{"slug": c.slug, "label": c.label, "wikidata": c.wikidata} for c in CLASSES],
            "property_count": len(PROPERTIES),
            "collectors": "Collectors, their collections, claims and votes stay in the app; they are never staged.",
            "licence": "Statements are published as CC0 so they can flow to Wikidata; photographs and stories "
                       "keep their own licences on glassdatabase.org."}


@router.get("/items", summary="Staged items")
def items(kind: str = "", status: str = "", q: str = "", page: int = Query(1, ge=1), per: int = Query(50, ge=1, le=500)):
    conn = _conn()
    sts = [x for x in status.split(",") if x] or None
    rows = store.items(conn, kind=kind or None, status=sts, q=q, limit=per, offset=(page - 1) * per)
    where, args = [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if sts:
        where.append(f"status IN ({','.join('?' * len(sts))})"); args += sts
    if q:
        where.append("(label LIKE ? OR key LIKE ? OR description LIKE ?)"); args += [f"%{q}%"] * 3
    total = conn.execute("SELECT COUNT(*) FROM wb_items" + (" WHERE " + " AND ".join(where) if where else ""),
                         args).fetchone()[0]
    return {"items": [_slim(r) for r in rows], "total": total, "page": page, "per": per}


@router.get("/items/{key:path}", summary="One staged item, as it would be pushed")
def item_one(key: str):
    conn = _conn()
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    if not r:
        raise HTTPException(404, "no such staged item")
    it = store.effective_item(r)
    labels = {}
    refs = {c.value.value for c in it.claims if c.value.kind == "item"} | ({it.cls} if it.cls else set())
    if refs:
        ks = list(refs)
        for x in store.rows(conn.execute(f"SELECT key, label, qid FROM wb_items WHERE key IN ({','.join('?' * len(ks))})", ks)):
            labels[x["key"]] = {"label": x["label"], "qid": x["qid"]}
    try:
        review = json.loads(r.get("review_json") or "{}")
    except ValueError:
        review = {}
    return {**_slim(r), "item": it.to_json(), "review": review, "refs": labels}


@router.get("/clusters", summary="Duplicate clusters")
def clusters(kind: str = ""):
    return {"clusters": dedupe.clusters(_conn(), kind or None)}


@router.get("/runs", summary="Recent runs")
def runs():
    rs = store.runs(_conn(), 30)
    return {"runs": [{k: r.get(k) for k in ("id", "kind", "status", "started_at", "finished_at", "by_email")}
                     | {"summary": _small_summary(r.get("summary") or {})} for r in rs]}


@router.get("/runs/{run_id}", summary="A run and its log")
def run_status(run_id: int, after: int = Query(0, ge=0)):
    st = jobs.status(_conn(), run_id, after)
    if not st:
        raise HTTPException(404, "no such run")
    st["summary"] = _small_summary(st.get("summary") or {}) | ({"plan": st["summary"].get("plan")} if st.get("summary") else {})
    return st


@router.get("/plan/{run_id}", summary="The plan a dry run or push wrote")
def plan(run_id: int):
    r = store.run(_conn(), run_id)
    path = (r or {}).get("summary", {}).get("plan")
    if not path or not Path(path).exists():
        raise HTTPException(404, "that run wrote no plan file")
    return Response(Path(path).read_bytes(), media_type="application/json",
                    headers={"Content-Disposition": f'inline; filename="wikibase-plan-{run_id}.json"'})


@router.get("/go/{ident:path}", summary="Resolver: a Glass Database ID or a Q id → where it lives")
def go(ident: str, request: Request):
    conn = _conn()
    s = store.get_settings(conn)
    if re.fullmatch(r"[QP]\d+", ident):
        return RedirectResponse(f"{s['url']}/wiki/{'Item' if ident[0] == 'Q' else 'Property'}:{ident}", status_code=302)
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (ident,)))
    if not r:
        raise HTTPException(404, "no such Glass Database ID")
    it = store.load_item(r)
    return RedirectResponse(it.url or f"/wikibase.html?key={ident}", status_code=302)


# --------------------------------------------------------------------------- reconciliation
def _jsonp(data, callback: str | None):
    if callback and re.fullmatch(r"[A-Za-z_$][\w$.]*", callback):
        return Response(f"{callback}({json.dumps(data, ensure_ascii=False)});", media_type="application/javascript")
    return JSONResponse(data)


def _reconcile(conn, queries: dict, limit_default: int = 5) -> dict:
    out = {}
    for qid, q in (queries or {}).items():
        if not isinstance(q, dict):
            continue
        text = str(q.get("query") or "")
        kind = q.get("type")
        if isinstance(kind, list):
            kind = kind[0] if kind else None
        if isinstance(kind, dict):
            kind = kind.get("id")
        limit = int(q.get("limit") or limit_default)
        out[qid] = {"result": refine.reconcile(conn, text, kind or None, limit)}
    return out


@router.get("/reconcile", summary="Reconciliation service (W3C 0.2): manifest, or ?queries=")
def reconcile_get(request: Request, queries: str | None = None, query: str | None = None, callback: str | None = None):
    conn = _conn()
    s = store.get_settings(conn)
    if queries:
        try:
            qs = json.loads(queries)
        except ValueError:
            raise HTTPException(400, "queries must be JSON")
        return _jsonp(_reconcile(conn, qs), callback)
    if query:
        return _jsonp({"result": refine.reconcile(conn, query, request.query_params.get("type"), 5)}, callback)
    return _jsonp(refine.service_manifest(_public_base(request), s["url"]), callback)


@router.post("/reconcile", summary="Reconciliation service: form-encoded queries")
async def reconcile_post(request: Request):
    conn = _conn()
    ctype = (request.headers.get("content-type") or "").lower()
    if "json" in ctype:
        body = await request.json()
        qs = body.get("queries") if isinstance(body, dict) else None
    else:
        form = await request.form()
        raw = form.get("queries")
        if not raw:
            s = store.get_settings(conn)
            return JSONResponse(refine.service_manifest(_public_base(request), s["url"]))
        try:
            qs = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "queries must be JSON")
    return JSONResponse(_reconcile(conn, qs or {}))


@router.get("/preview/{ident:path}", response_class=HTMLResponse, summary="Candidate preview for OpenRefine")
def preview(ident: str):
    conn = _conn()
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=? OR qid=?", (ident, ident)))
    if not r:
        raise HTTPException(404, "no such item")
    it = store.effective_item(r)
    facts = "".join(f"<li><b>{escape(c.prop)}</b>: {escape(str(c.value.value))}</li>" for c in it.claims[:8])
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>body{{font:14px/1.4 system-ui,sans-serif;margin:10px;"
            f"color:#1F1A16;background:#F4EEE4}}h3{{margin:0 0 4px;font-size:16px}}p{{margin:0 0 6px;color:#4A423A}}"
            f"ul{{margin:0;padding-left:18px}}small{{color:#6B6157}}</style></head><body><h3>{escape(it.label)}</h3>"
            f"<p>{escape(it.description)}</p><ul>{facts}</ul><small>{escape(r['key'])} · {escape(r['status'])}"
            f"{' · ' + escape(r['qid']) if r['qid'] else ''}</small></body></html>")


@router.get("/openrefine-manifest.json", summary="Manifest for OpenRefine's Wikibase extension")
def openrefine_manifest(request: Request):
    conn = _conn()
    s = store.get_settings(conn)
    try:
        return refine.openrefine_manifest(conn, _public_base(request), s["url"])
    except LookupError as e:
        raise HTTPException(409, str(e))


# --------------------------------------------------------------------------- admin
@router.get("/settings", summary="Wiki settings (no secrets)")
def get_settings(u: dict = Depends(require_admin)):
    return store.get_settings(_conn())


@router.post("/settings", summary="Save the wiki URL, bot user and password, bot flag, pause")
def post_settings(payload: dict, u: dict = Depends(require_admin)):
    payload = payload or {}
    url = payload.get("url")
    if url is not None and not re.fullmatch(r"https://[\w.-]+(:\d+)?(/[\w./-]*)?", url.strip()):
        raise HTTPException(400, "the wiki URL must be https://host[/path]")
    pw = payload.get("password")
    if pw is not None and not isinstance(pw, str):
        raise HTTPException(400, "password must be text")
    out = store.set_settings(_conn(), url=url, user=payload.get("user"), password=pw,
                             bot=payload.get("bot") if isinstance(payload.get("bot"), bool) else None,
                             pause=payload.get("pause") if payload.get("pause") is not None else None,
                             public_base=payload.get("public_base"))
    return out


@router.post("/settings/test", summary="Reach the wiki and sign in as the bot (nothing written)")
def test_settings(u: dict = Depends(require_admin)):
    lines: list[str] = []
    out = sync.check(_conn(), lines.append)
    out["lines"] = lines
    return out


def _stage_job(conn, log, params):
    s1 = stage.run(conn, log, params)
    s2 = dedupe.run(conn, log, params) if params.get("dedupe", True) else {}
    return {**s1, "clusters": s2.get("clusters"), "duplicates": s2.get("duplicates"), "by_kind": s2.get("by_kind")}


JOB_FNS = {"stage": _stage_job, "dedupe": dedupe.run, "bootstrap": push.run, "dry-run": push.run, "push": push.run,
           "pull": sync.pull, "check": sync.check}


@router.post("/jobs", summary="Start a job: stage, dedupe, bootstrap, dry-run, push, pull, check")
def start_job(payload: dict, u: dict = Depends(require_admin)):
    payload = payload or {}
    kind = payload.get("kind")
    if kind not in JOB_FNS:
        raise HTTPException(400, f"kind must be one of {', '.join(JOB_FNS)}")
    params = dict(payload.get("params") or {})
    for k in ("kinds", "keys"):
        if isinstance(params.get(k), str):
            params[k] = [x.strip() for x in params[k].split(",") if x.strip()]
    if params.get("limit") is not None:
        try:
            params["limit"] = int(params["limit"]) or None
        except (TypeError, ValueError):
            raise HTTPException(400, "limit must be a number")
    if kind == "bootstrap":
        params.update(what="properties", really=bool(payload.get("really")))
    elif kind == "dry-run":
        params.update(really=False)
    elif kind == "push":
        if not payload.get("confirm"):
            raise HTTPException(400, "a real push needs confirm: true")
        params.update(really=True, what=params.get("what") or "all")
    conn = _conn()
    if kind in ("push",) or (kind == "bootstrap" and params.get("really")):
        s = store.get_settings(conn)
        if not (s["user"] and s["password_set"]):
            raise HTTPException(409, "save the bot user and password first")
    try:
        run_id = jobs.start(conn, kind, JOB_FNS[kind], params, by_email=u.get("email") or u.get("name") or "")
    except jobs.Busy as e:
        raise HTTPException(409, str(e))
    return {"run_id": run_id, "kind": kind, "params": params}


@router.post("/items/{key:path}", summary="Review one staged item")
def post_item(key: str, payload: dict, u: dict = Depends(require_admin)):
    conn = _conn()
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (key,)))
    if not r:
        raise HTTPException(404, "no such staged item")
    payload = payload or {}
    if payload.get("split"):
        return _slim(dedupe.split(conn, key) or r)
    fields: dict = {}
    st = payload.get("status")
    if st:
        if st not in ("ready", "skipped", "new", "held"):
            raise HTTPException(400, "status must be ready, skipped, new or held")
        if r["status"] in ("pushed", "changed") and st != "skipped":
            raise HTTPException(409, "that item is on the wiki already; it follows its source row now")
        fields["status"] = st
    q = payload.get("wikidata_qid")
    if q is not None:
        q = str(q).strip()
        if q and not re.fullmatch(r"Q\d+", q):
            raise HTTPException(400, "a Wikidata id looks like Q123")
        fields["wikidata_qid"] = q
    try:
        review = json.loads(r.get("review_json") or "{}")
    except ValueError:
        review = {}
    for k in ("label", "description"):
        if payload.get(k) is not None:
            review[k] = str(payload[k]).strip()[:250]
    if payload.get("aliases") is not None:
        al = payload["aliases"]
        if isinstance(al, str):
            al = [a.strip() for a in al.split("|")]
        review["aliases"] = [str(a).strip()[:250] for a in al if str(a).strip()]
    if any(k in payload for k in ("label", "description", "aliases")):
        fields["review_json"] = json.dumps(review, ensure_ascii=False)
    if payload.get("notes") is not None:
        fields["notes"] = str(payload["notes"])[:1000]
    if fields:
        eff = store.effective_item(dict(r, **{k: v for k, v in fields.items() if k in ("review_json", "wikidata_qid")}))
        fields["content_hash"] = eff.content_hash()
        fields["label"], fields["description"] = eff.label, eff.description
        if r["status"] in ("pushed", "changed") and "status" not in fields:
            fields["status"] = "pushed" if eff.content_hash() == r["pushed_hash"] else "changed"
        r = store.set_item(conn, key, **fields)
    return _slim(r)


@router.get("/refine/{kind}.csv", summary="CSV for OpenRefine")
def refine_export(kind: str, status: str = "", u: dict = Depends(require_admin)):
    sts = [x for x in status.split(",") if x] or None
    text = refine.export_csv(_conn(), kind, sts)
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="glass-{kind}.csv"'})


@router.post("/refine/import", summary="An edited CSV back in")
async def refine_import(request: Request, u: dict = Depends(require_admin)):
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/"):
        form = await request.form()
        f = form.get("file") or next((v for v in form.values() if hasattr(v, "read")), None)
        data = await f.read() if f is not None else b""
    else:
        data = await request.body()
    if not data:
        raise HTTPException(400, "send the CSV as multipart 'file' or as the body")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    lines: list[str] = []
    try:
        counts = refine.import_csv(_conn(), text, lines.append)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **counts, "lines": lines}
