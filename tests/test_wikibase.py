"""The migration to glass.wikibase.cloud: the model, staging, deduplication, the OpenRefine
round trip, the two-pass pusher against a fake wiki, the jobs, and the API + page."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central import sessions
from central.wikibase import dedupe, jobs, model, push, refine, stage, store, sync
from central.wikibase.client import Wikibase, WikibaseError

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
BOSS = "wiki-boss@example.com"


@pytest.fixture
def conn(demo_db, monkeypatch, tmp_path):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    c = store.open_db()
    for t in ("wb_items", "wb_props", "wb_runs", "wb_log", "wb_cache"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM _settings WHERE key LIKE 'wikibase_%'")
    c.commit()
    monkeypatch.setattr(push, "OUT_DIR", tmp_path / "wb")
    yield c
    for t in ("wb_items", "wb_props", "wb_runs", "wb_log", "wb_cache"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM _settings WHERE key LIKE 'wikibase_%'")
    c.commit()


# --------------------------------------------------------------------------- model
def test_model_is_consistent():
    labels = [p.label for p in model.PROPERTIES]
    assert len(labels) == len(set(labels))
    assert model.KEY_PROP in labels and model.WIKIDATA_PROP in labels and "equivalent property" in labels
    for p in model.PROPERTIES:
        assert p.datatype in push.DATATYPES
        if p.wikidata:
            assert re.fullmatch(r"P\d+", p.wikidata), p.label
    slugs = [c.slug for c in model.CLASSES]
    assert len(slugs) == len(set(slugs))
    for c in model.CLASSES:
        assert not c.parent or c.parent in model.CLASS_BY_SLUG
    fixed = model.fixed_items()
    keys = [it.key for it in fixed]
    assert len(keys) == len(set(keys)), "a vocabulary key is repeated"
    # every item kind that a term uses has a class item to be an instance of
    cls_keys = {it.key for it in fixed if it.kind == "class"}
    for it in fixed:
        if it.cls:
            assert it.cls in cls_keys, it.key
    # the techniques vocabulary carries its ontology IRIs and the known Wikidata twin
    blown = next(it for it in fixed if it.key == "gdb:technique:offhand-blown")
    assert "Q12185286" in blown.values(model.WIKIDATA_PROP)
    assert any(v.startswith("http://example.org/glassblowing#") for v in blown.values("exact match"))
    assert model.country_key("USA") == "gdb:country:united-states" == model.country_key("United States")
    assert model.time_value("2026")  .precision == 9 and model.time_value("2026-10-31").precision == 11
    assert model.time_value("sometime") is None


def test_item_round_trip_and_hash():
    it = model.Item("gdb:x:1", "person", "Rae Sutter", "glass artist", ["R. Sutter"], "gdb:class:person")
    it.add("official website", model.url("https://example.com/rae"))
    it.add("technique", model.item("gdb:technique:offhand-blown"), [("series ordinal", model.string("2"))])
    it.add("technique", model.item("gdb:technique:offhand-blown"), [("series ordinal", model.string("2"))])  # deduped
    back = model.Item.from_json(json.loads(json.dumps(it.to_json())))
    assert back.to_json() == it.to_json() and len(back.claims) == 2
    h = it.content_hash()
    back.description = "glass artist, Crozet"
    assert back.content_hash() != h


# --------------------------------------------------------------------------- stage + dedupe
def test_stage_builds_items_from_the_demo_registry(conn):
    lines = []
    s = stage.run(conn, lines.append, {"public_base": "https://glassdatabase.test"})
    assert s["items"] > 200 and s["kinds"]["person"] >= 5 and s["kinds"]["studio"] >= 5 and s["kinds"]["object"] >= 3
    rows = {r["key"]: r for r in store.items(conn)}
    rae = store.load_item(rows["gdb:artists:artists-0000"])
    assert rae.label == "Rae Sutter" and rae.cls == "gdb:class:person"
    assert "gdb:technique:offhand-blown" in rae.values("technique") and "gdb:technique:cane-murrine" in rae.values("technique")
    assert rae.values("work location") == ["gdb:place:crozet-virginia"]
    assert "phone" not in json.dumps(rae.to_json())                       # a private column never enters
    place = store.load_item(rows["gdb:place:crozet-virginia"])
    assert place.description == "Virginia, United States" and "gdb:country:united-states" in place.values("country")
    studio = store.load_item(rows["gdb:studios:studios-0000"])
    assert studio.cls == "gdb:class:hot-shop" and studio.values("coordinates") == ["38.06900,-78.70100"]
    assert studio.values("inception") == ["2009-00-00"] and "email" not in json.dumps(studio.to_json())
    obj = next(store.load_item(r) for r in rows.values() if r["source_row"] == "obj-demo-0001")
    assert obj.values("maker") == ["gdb:artists:artists-0000"]            # the maker resolved to the directory row
    assert obj.values("form") == ["gdb:form:pumpkin"] and obj.values("colour") == ["gdb:colour:orange"]
    assert obj.values("height") == ["134"] and obj.values("width") == ["118"]
    assert obj.values("participant in") == ["gdb:seasons:1"] and obj.values("edition size") == ["12"]
    assert obj.values("made at") == ["gdb:studios:studios-0000"]
    assert all(r["status"] == "new" for r in rows.values())
    assert "artists" in s["tables"] and "phone" not in s["tables"]["artists"]["used"]
    # a second run changes nothing
    s2 = stage.run(conn, lines.append)
    assert s2["items"] == s["items"] and s2["statuses"].get("new") == s["items"] and s2["dropped"] == 0


def test_dedupe_folds_places_and_merged_claims(conn):
    stage.run(conn, lambda *_: None)
    s = dedupe.run(conn, lambda *_: None)
    assert s["duplicates"] >= 1
    dup = store.one(conn.execute("SELECT * FROM wb_items WHERE key='gdb:place:venice-italy'"))
    assert dup["status"] == "duplicate" and dup["canonical"] == "gdb:place:venice-veneto"
    canon = store.load_item(store.one(conn.execute("SELECT * FROM wb_items WHERE key='gdb:place:venice-veneto'")))
    assert "gdb:place:venice-italy" in canon.values(model.KEY_PROP)       # the folded row's key rides along
    cl = dedupe.clusters(conn, "place")
    assert cl and any(len(c["members"]) == 2 for c in cl)
    # keep apart: both sides carry different from, the duplicate stands again
    r = dedupe.split(conn, "gdb:place:venice-italy")
    assert r["status"] == "split"
    me = store.load_item(r)
    assert me.values("different from") == ["gdb:place:venice-veneto"]
    canon2 = store.load_item(store.one(conn.execute("SELECT * FROM wb_items WHERE key='gdb:place:venice-veneto'")))
    assert "gdb:place:venice-italy" not in canon2.values(model.KEY_PROP)


def test_dedupe_clusters_people_by_name_and_website():
    rows = []
    for i, (label, web, table) in enumerate([("Rae Sutter", "https://raesutter.com", "artists"),
                                             ("Sutter, Rae", "", "artists_legacy"), ("Rae  Sutter", "", "museum_artists"),
                                             ("Rae Sutter", "", "artist_submissions"), ("Ray Sutter", "https://raesutter.com/about", "objects"),
                                             ("Theo Park", "", "artists")]):
        it = model.Item(f"gdb:{table}:{i}", "person", label, "glass artist")
        if web:
            it.add("official website", model.url(web))
        rows.append({"key": it.key, "kind": "person", "label": label, "status": "new", "cluster": "", "qid": "",
                     "source_table": table, "item_json": json.dumps(it.to_json())})
    groups = dedupe.cluster(rows)
    sizes = sorted(len(v) for v in groups.values())
    assert sizes == [1, 5]


# --------------------------------------------------------------------------- OpenRefine
def test_csv_round_trip_and_reconcile(conn):
    stage.run(conn, lambda *_: None)
    text = refine.export_csv(conn, "person")
    head = text.splitlines()[0].split(",")
    assert head[:7] == ["key", "label", "description", "aliases", "status", "wikidata_qid", "merge_into"]
    assert "official website" in head and "technique" in head
    import csv as _csv
    import io
    rows = list(_csv.DictReader(io.StringIO(text)))
    rae = next(r for r in rows if r["label"] == "Rae Sutter")
    rae["label"], rae["status"], rae["wikidata_qid"] = "Rae A. Sutter", "ready", "Q777"
    ivo = next(r for r in rows if r["label"] == "Ivo Marchetti")
    ivo["merge_into"] = "gdb:artists:artists-0000"
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=head, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    counts = refine.import_csv(conn, buf.getvalue(), lambda *_: None)
    assert counts["wikidata"] == 1 and counts["status"] == 1 and counts["merged"] == 1 and counts["edits"] == 1
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key='gdb:artists:artists-0000'"))
    assert r["status"] == "ready" and r["wikidata_qid"] == "Q777" and r["label"] == "Rae A. Sutter"
    eff = store.effective_item(r)
    assert eff.label == "Rae A. Sutter" and "Q777" in eff.values(model.WIKIDATA_PROP)
    # a re-stage keeps the reviewer's edits
    stage.run(conn, lambda *_: None)
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE key='gdb:artists:artists-0000'"))
    assert r["label"] == "Rae A. Sutter" and r["wikidata_qid"] == "Q777" and r["status"] == "ready"
    assert store.one(conn.execute("SELECT status, canonical FROM wb_items WHERE key='gdb:artists:artists-0001'"))["status"] == "duplicate"
    # reconciliation
    cands = refine.reconcile(conn, "rae sutter", "person")           # the label is now "Rae A. Sutter"
    assert cands and cands[0]["key"] == "gdb:artists:artists-0000" and cands[0]["score"] >= 90
    exact = refine.reconcile(conn, "Rae A. Sutter", "person")
    assert exact[0]["match"] is True and exact[0]["score"] == 100
    assert refine.reconcile(conn, "nobody at all", "person") == []
    assert refine.reconcile(conn, "Nadia Okonkwo")[0]["type"][0]["id"] == "person"
    with pytest.raises(LookupError):
        refine.openrefine_manifest(conn, "https://glassdatabase.test", "https://glass.wikibase.cloud")
    m = refine.service_manifest("https://glassdatabase.test", "https://glass.wikibase.cloud")
    assert m["identifierSpace"].startswith("https://glass.wikibase.cloud/entity/") and "{{id}}" in m["view"]["url"]


# --------------------------------------------------------------------------- a fake wiki
class FakeWiki(Wikibase):
    """An in-memory wikibase.cloud: creates items and properties, remembers them, answers
    the reads the pusher makes. No network."""

    def __init__(self):
        super().__init__("https://fake.wikibase.test", "Bot@x", "pw", dry_run=False, pause=0)
        self.entities: dict[str, dict] = {}
        self.n_q = self.n_p = 0
        self.edits: list[dict] = []
        self.csrf = "tok"

    def login(self):
        return "Bot"

    def properties(self):
        return {e["labels"]["en"]["value"]: {"pid": pid, "datatype": e.get("datatype", "")}
                for pid, e in self.entities.items() if pid.startswith("P")}

    def get_entities(self, ids, props="labels|descriptions|aliases|claims|datatype"):
        return {i: dict(self.entities[i]) if i in self.entities else {} for i in ids}

    def key_map(self, key_pid):
        out = {}
        for q, e in self.entities.items():
            for s in (e.get("claims") or {}).get(key_pid, []):
                out[s["mainsnak"]["datavalue"]["value"]] = q
        return out

    def edit(self, data, *, qid=None, new=None, clear=False, summary=""):
        self.edits.append({"qid": qid, "new": new, "clear": clear, "data": data, "summary": summary})
        if qid is None:
            label = data["labels"]["en"]["value"]
            desc = (data.get("descriptions") or {}).get("en", {}).get("value", "")
            for q, e in self.entities.items():
                if q[0] == ("P" if new == "property" else "Q") and e["labels"]["en"]["value"] == label and \
                        (e.get("descriptions") or {}).get("en", {}).get("value", "") == desc:
                    raise WikibaseError(f"wbeditentity: modification-failed: Item {q} already has label \"{label}\" "
                                        f"associated with language code en, using the same description text.")
            if new == "property":
                self.n_p += 1
                qid = f"P{self.n_p}"
            else:
                self.n_q += 1
                qid = f"Q{self.n_q}"
            self.entities[qid] = {"id": qid, "labels": {}, "descriptions": {}, "aliases": {}, "claims": {}}
            if new == "property":
                self.entities[qid]["datatype"] = data["datatype"]
        e = self.entities[qid]
        if clear:
            e["claims"] = {}
        for k in ("labels", "descriptions"):
            if data.get(k):
                e[k].update(data[k])
        for lang, als in (data.get("aliases") or {}).items():
            e["aliases"].setdefault(lang, [])
            e["aliases"][lang] += [{"language": lang, "value": a["value"]} for a in als]
        for st in data.get("claims") or []:
            e["claims"].setdefault(st["mainsnak"]["property"], []).append(st)
        self.writes += 1
        return {"entity": {"id": qid}}


def _push(conn, api, params):
    import central.wikibase.push as P
    orig = P.make_api
    P.make_api = lambda *a, **k: api
    try:
        return P.run(conn, lambda *_: None, params)
    finally:
        P.make_api = orig


def test_dry_run_builds_every_body_offline(conn):
    stage.run(conn, lambda *_: None)
    dedupe.run(conn, lambda *_: None)
    lines = []
    s = push.run(conn, lines.append, {"really": False, "live": False})
    assert s["dry_run"] and s["api_writes"] == 0
    assert s["creates"] >= 300 and s["updates"] >= s["creates"] - 60
    assert not s["failures_by_code"]
    plan = json.loads(Path(s["plan"]).read_text(encoding="utf-8"))
    import hashlib
    pumpkin = "gdb:object:demo-" + hashlib.sha1(b"obj-demo-0001").hexdigest()[:24]   # the measured demo piece
    obj = next(b for b in plan["bodies"] if b["pass"] == 2 and b["key"] == pumpkin)
    props = {s_["mainsnak"]["datavalue"]["type"] for s_ in obj["body"]["claims"]}
    assert {"string", "wikibase-entityid", "monolingualtext", "quantity", "time"} <= props
    assert all("references" in st for st in obj["body"]["claims"][2:]), "every statement carries a reference"
    assert store.counts(conn)["live"] == 0                                   # nothing recorded on a dry run


def test_bootstrap_and_two_pass_push_against_a_fake_wiki(conn):
    stage.run(conn, lambda *_: None)
    dedupe.run(conn, lambda *_: None)
    store.set_settings(conn, user="Bot@x", password="pw", pause=0)
    api = FakeWiki()
    s = _push(conn, api, {"really": True, "what": "properties"})
    assert not s["failures_by_code"]
    props = store.props(conn)
    assert props[model.KEY_PROP]["pid"] and props["instance of"]["pid"]
    key_pid = props[model.KEY_PROP]["pid"]
    eq = api.entities[props["coordinates"]["pid"]]["claims"]
    assert any(st["mainsnak"]["datavalue"]["value"].endswith("/P625") for sts in eq.values() for st in sts)
    # items: a first push creates everything, with references, and records the ids
    s = _push(conn, api, {"really": True, "what": "items", "limit": 120})
    assert not s["failures_by_code"] and s["creates"] == 120
    c = store.counts(conn)
    assert c["status"]["pushed"] == 120 and c["live"] == 120
    row = store.one(conn.execute("SELECT * FROM wb_items WHERE status='pushed' AND kind='technique' LIMIT 1"))
    assert row["qid"].startswith("Q") and row["pushed_hash"] == store.effective_item(row).content_hash()
    ent = api.entities[row["qid"]]
    assert ent["claims"][key_pid][0]["mainsnak"]["datavalue"]["value"] == row["key"]
    # pushing again is a no-op for what is current
    before = api.writes
    s = _push(conn, api, {"really": True, "what": "items", "keys": [row["key"]]})
    assert s["items"] == 0 and api.writes == before
    # a changed source → changed → one clear write
    r = store.one(conn.execute("SELECT * FROM wb_items WHERE status='pushed' AND kind='technique' LIMIT 1"))
    store.set_item(conn, r["key"], status="changed")
    s = _push(conn, api, {"really": True, "what": "items", "keys": [r["key"]]})
    assert s["updates"] == 1 and api.edits[-1]["clear"] is True and api.edits[-1]["qid"] == r["qid"]
    assert store.one(conn.execute("SELECT status FROM wb_items WHERE key=?", (r["key"],)))["status"] == "pushed"
    # an item that already exists on the wiki under our key is found, not created twice
    nxt = store.one(conn.execute("SELECT * FROM wb_items WHERE status='new' AND kind='colour' LIMIT 1"))
    it = store.effective_item(nxt)
    api.edit({"labels": {"en": {"language": "en", "value": it.label}}, "descriptions": {"en": {"language": "en", "value": it.description}},
              "claims": [{"mainsnak": {"snaktype": "value", "property": key_pid, "datavalue": {"type": "string", "value": it.key}},
                          "type": "statement", "rank": "normal"}]}, new="item")
    made = api.n_q
    s = _push(conn, api, {"really": True, "what": "items", "keys": [nxt["key"]]})
    assert api.n_q == made and store.one(conn.execute("SELECT qid FROM wb_items WHERE key=?", (nxt["key"],)))["qid"] == f"Q{made}"
    # pull reads it back into the cache
    import central.wikibase.sync as S
    orig = S.make_api
    S.make_api = lambda *a, **k: api
    try:
        p = sync.pull(conn, lambda *_: None, {})
    finally:
        S.make_api = orig
    assert p["fetched"] >= 120 and conn.execute("SELECT COUNT(*) FROM wb_cache").fetchone()[0] >= 120


def test_label_clash_is_suffixed_not_fatal(conn):
    a = model.Item("gdb:t:1", "person", "Sam Glass", "glass artist", cls="gdb:class:person")
    b = model.Item("gdb:t:2", "person", "Sam Glass", "glass artist", cls="gdb:class:person")
    for it in (model.Item("gdb:class:person", "class", "person", "a person"), a, b):
        store.upsert_item(conn, it, source_hash="x")
    api = FakeWiki()
    _push(conn, api, {"really": True, "what": "properties"})
    s = _push(conn, api, {"really": True, "what": "items"})
    assert not s["failures_by_code"]
    qa = store.one(conn.execute("SELECT qid FROM wb_items WHERE key='gdb:t:1'"))["qid"]
    qb = store.one(conn.execute("SELECT qid FROM wb_items WHERE key='gdb:t:2'"))["qid"]
    assert qa and qb and qa != qb
    assert api.entities[qb]["descriptions"]["en"]["value"].endswith("[2]")


# --------------------------------------------------------------------------- jobs
def test_jobs_run_inline_log_and_refuse_overlap(conn):
    rid = jobs.start(conn, "stage", stage.run, {}, by_email="t@example.com", thread=False)
    st = jobs.status(conn, rid)
    assert st["status"] == "done" and st["lines"] and st["summary"]["items"] > 0
    tail = jobs.status(conn, rid, after=st["lines"][-2]["id"])
    assert len(tail["lines"]) == 1
    conn.execute("UPDATE wb_runs SET status='running', heartbeat=? WHERE id=?", (store.now(), rid))
    conn.commit()
    with pytest.raises(jobs.Busy):
        jobs.start(conn, "dedupe", dedupe.run, {}, thread=False)
    conn.execute("UPDATE wb_runs SET heartbeat='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    conn.commit()
    assert jobs.running(conn) is None                                        # stale → lost, no longer blocks
    with pytest.raises(ValueError):
        jobs.start(conn, "nope", stage.run, {}, thread=False)


# --------------------------------------------------------------------------- API + page
@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("GLOWTBOOK_DB_PATH", str(tmp_path / "gb.db"))
    monkeypatch.setenv("GLASSDB_ADMIN_EMAILS", BOSS)
    monkeypatch.setenv("GLASSDB_ADMIN_TOKEN", "wiki-test-key")
    monkeypatch.delenv("GLASSDB_DEV_LOGIN", raising=False)
    from api.main import app
    return TestClient(app)


def _auth(email=BOSS):
    return {"Authorization": f"Bearer {sessions.issue('u-' + email, email, 'Someone')}"}


def test_api_public_views_and_admin_gates(client, conn):
    st = client.get("/wikibase/status").json()
    assert st["wiki"].startswith("https://") and st["configured"] is False and "collectors" in st
    assert client.get("/wikibase/settings").status_code == 401
    assert client.get("/wikibase/settings", headers=_auth("someone@example.com")).status_code == 403
    assert client.post("/wikibase/jobs", json={"kind": "stage"}).status_code == 401
    r = client.post("/wikibase/settings", json={"user": "Bot@x", "password": "secret-pw", "pause": 0.2}, headers=_auth())
    assert r.status_code == 200 and r.json()["password_set"] is True and "secret" not in r.text
    assert store.credentials(conn) == ("Bot@x", "secret-pw")
    assert client.get("/wikibase/settings", headers={"X-API-Key": "wiki-test-key"}).json()["user"] == "Bot@x"
    assert client.post("/wikibase/settings", json={"url": "ftp://nope"}, headers=_auth()).status_code == 400
    assert client.post("/wikibase/jobs", json={"kind": "push"}, headers=_auth()).status_code == 400   # confirm
    assert client.post("/wikibase/jobs", json={"kind": "bogus"}, headers=_auth()).status_code == 400


def test_api_stage_job_items_review_and_refine(client, conn):
    import time
    r = client.post("/wikibase/jobs", json={"kind": "stage"}, headers=_auth())
    assert r.status_code == 200
    rid = r.json()["run_id"]
    for _ in range(200):
        st = client.get(f"/wikibase/runs/{rid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.2)
    assert st["status"] == "done" and st["by_email"] == BOSS and st["lines"]
    j = client.get("/wikibase/items", params={"kind": "person", "q": "Sutter"}).json()
    assert j["total"] == 1 and j["items"][0]["label"] == "Rae Sutter"
    key = j["items"][0]["key"]
    one = client.get(f"/wikibase/items/{key}").json()
    assert one["item"]["cls"] == "gdb:class:person" and one["refs"]["gdb:class:person"]["label"] == "person"
    assert client.post(f"/wikibase/items/{key}", json={"status": "ready"}).status_code == 401
    r = client.post(f"/wikibase/items/{key}", json={"status": "ready", "wikidata_qid": "Q42", "label": "Rae Sutter (glass)"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "ready" and r.json()["label"] == "Rae Sutter (glass)"
    assert client.post(f"/wikibase/items/{key}", json={"wikidata_qid": "nope"}, headers=_auth()).status_code == 400
    csv = client.get("/wikibase/refine/person.csv", headers=_auth())
    assert csv.status_code == 200 and csv.text.startswith("key,label,description,aliases,status,wikidata_qid,merge_into")
    assert client.get("/wikibase/refine/person.csv").status_code == 401
    r = client.post("/wikibase/refine/import", content=csv.text.encode(), headers={**_auth(), "Content-Type": "text/csv"})
    assert r.status_code == 200 and r.json()["rows"] >= 5
    rec = client.get("/wikibase/reconcile", params={"queries": json.dumps({"q0": {"query": "Rae Sutter", "type": "person"}})}).json()
    assert rec["q0"]["result"][0]["key"] == key
    man = client.get("/wikibase/reconcile").json()
    assert "identifierSpace" in man and man["defaultTypes"]
    assert client.get("/wikibase/openrefine-manifest.json").status_code == 409
    go = client.get(f"/wikibase/go/{key}", follow_redirects=False)
    assert go.status_code == 302 and "explore" in go.headers["location"]
    assert client.get("/wikibase/go/Q5", follow_redirects=False).headers["location"].endswith("/wiki/Item:Q5")
    assert client.get(f"/wikibase/preview/{key}").status_code == 200
    assert client.get("/wikibase/clusters").status_code == 200
    assert client.get("/wikibase/plan/999").status_code == 404


def test_page_markup():
    page = (DEPLOY / "wikibase.html").read_text(encoding="utf-8")
    assert '<ul id="menu" data-nav data-active=""></ul>' in page
    for anchor in ("#pipeline", "#items", "#openrefine", "#runs", "#settings", "/review.html"):
        assert anchor in page
    for must in ("/wikibase/status", "/wikibase/jobs", "/wikibase/runs/", "/wikibase/settings", "/wikibase/refine/import",
                 "Collectors", "Special:BotPasswords", "openrefine-manifest.json", "confirm("):
        assert must in page, must
    assert 'type="password"' in page and "GDB.me()" in page
    # the proxy, the dev site and the installer know the page
    assert "wikibase" in (DEPLOY / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8")
    assert "wikibase.html" in (ROOT / "scripts" / "dev_site.py").read_text(encoding="utf-8")
    inst = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "wikibase.html" in inst and "--exclude 'data/wikibase'" in inst and "--exclude 'data/splat_media'" in inst
