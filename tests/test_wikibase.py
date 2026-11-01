"""The migration to glass.wikibase.cloud: the model, staging, deduplication, the OpenRefine
round trip, the two-pass pusher against a fake wiki, the jobs, and the API + page."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central import sessions
from central.wikibase import dedupe, jobs, model, push, refine, stage, store, sync, wikidata
from central.wikibase.client import Wikibase, WikibaseError

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
BOSS = "wiki-boss@example.com"


@pytest.fixture
def conn(demo_db, monkeypatch, tmp_path):
    monkeypatch.setenv("GLASSDB_SESSION_SECRET", "test-secret")
    c = store.open_db()
    for t in ("wb_items", "wb_props", "wb_runs", "wb_log", "wb_cache", "wb_wikidata"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM _settings WHERE key LIKE 'wikibase_%'")
    c.commit()
    monkeypatch.setattr(push, "OUT_DIR", tmp_path / "wb")
    yield c
    for t in ("wb_items", "wb_props", "wb_runs", "wb_log", "wb_cache", "wb_wikidata"):
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
    # the known-city list is keyed the way stage looks it up (norm drops the comma)
    assert all(k == model.norm(k) for k in model.KNOWN_PLACES)
    assert model.KNOWN_PLACES[model.norm("Tacoma, Washington")] == "Q199797"


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

    def duplicate_keys(self, key_pid, limit=2000):
        seen = {}
        for q, e in self.entities.items():
            for s in (e.get("claims") or {}).get(key_pid, []):
                seen.setdefault(s["mainsnak"]["datavalue"]["value"], []).append(q)
        return {k: v for k, v in seen.items() if len(v) > 1}

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


def test_jobs_stop_lost_and_boot(conn, monkeypatch):
    # a job asked to stop ends at its next beat, as "stopped", and says so in its log
    def slow(c, log, params):
        log("working")
        jobs.stop(c, log.run_id)                   # the Stop button, from another request
        log.beat(force=True)                       # the pusher's per-item beat
        raise AssertionError("not reached")
    rid = jobs.start(conn, "pull", slow, {}, thread=False)
    st = jobs.status(conn, rid)
    assert st["status"] == "stopped" and any("stopped" in ln["line"] for ln in st["lines"])
    assert st["summary"]["stopped"].startswith("stopped from the page")
    assert jobs.stop(conn, rid)["status"] == "stopped" and jobs.stop(conn, 99999) is None
    # a fresh running row blocks the next start; asking it to stop marks it stopping (still blocks)
    conn.execute("UPDATE wb_runs SET status='running', heartbeat=? WHERE id=?", (store.now(), rid))
    conn.commit()
    with pytest.raises(jobs.Busy):
        jobs.start(conn, "dedupe", dedupe.run, {}, thread=False)
    assert jobs.stop(conn, rid)["status"] == "stopping"
    with pytest.raises(jobs.Busy):
        jobs.start(conn, "dedupe", dedupe.run, {}, thread=False)
    # a run from before this process booted that has not beaten since is dead, not a blocker
    monkeypatch.setattr(jobs, "BOOT", jobs._now())
    old = (datetime.now(timezone.utc) - timedelta(seconds=jobs.GRACE + 5)).isoformat(timespec="seconds")
    conn.execute("UPDATE wb_runs SET status='running', heartbeat=? WHERE id=?", (old, rid))
    conn.commit()
    assert jobs.running(conn) is None and store.run(conn, rid)["status"] == "lost"
    # …but one that beat a moment ago is alive even if it started before the boot
    conn.execute("UPDATE wb_runs SET status='running', heartbeat=? WHERE id=?", (store.now(), rid))
    conn.commit()
    assert jobs.running(conn)["id"] == rid
    # a live run that finds itself marked lost stops itself at the next beat
    conn.execute("UPDATE wb_runs SET status='lost' WHERE id=?", (rid,))
    conn.commit()
    with pytest.raises(jobs.Cancelled):
        jobs.Logger(conn, rid).beat(force=True)
    conn.execute("UPDATE wb_runs SET status='done' WHERE id=?", (rid,))
    conn.commit()


def test_client_names_the_real_error_and_retries_conflicts(monkeypatch):
    from central.wikibase import client as cl
    err = {"code": "failed-save", "info": "The save has failed.",
           "messages": [{"name": "wikibase-api-failed-save"}, {"name": "edit-conflict"}]}
    assert cl.error_text("wbeditentity", err) == "wbeditentity: failed-save: The save has failed. [edit-conflict]"
    assert cl.error_text("wbeditentity", {"code": "badtoken", "info": "Invalid CSRF token."}) == "wbeditentity: badtoken: Invalid CSRF token."
    assert cl.is_conflict(WikibaseError("wbeditentity: editconflict: Edit conflict."))
    assert not cl.is_conflict(WikibaseError("wbeditentity: failed-save: The save has failed. [actionthrottledtext]"))

    class Log:
        def __init__(self):
            self.lines, self.beats = [], 0

        def __call__(self, line):
            self.lines.append(line)

        def beat(self):
            self.beats += 1

    api = Wikibase("https://fake.wikibase.test", pause=0, log=Log())
    calls = {"n": 0}

    def once(params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise WikibaseError("wbeditentity: failed-save: The save has failed. [edit-conflict]")
        return {"success": 1}
    monkeypatch.setattr(api, "_post_once", once)
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    assert api.post(action="wbeditentity")["success"] == 1 and calls["n"] == 2
    assert api.log.beats == 2 and any("edit conflict" in ln for ln in api.log.lines)
    # a stop request raised from the beat ends the call before anything is sent
    api.log.beat = lambda: (_ for _ in ()).throw(jobs.Cancelled("stopped from the page"))
    with pytest.raises(jobs.Cancelled):
        api.post(action="wbeditentity")
    assert calls["n"] == 2


def test_api_stop_run(client, conn):
    assert client.post("/wikibase/runs/1/stop").status_code == 401
    assert client.post("/wikibase/runs/999999/stop", headers=_auth()).status_code == 404
    rid = jobs.start(conn, "dedupe", dedupe.run, {}, thread=False)
    assert client.post(f"/wikibase/runs/{rid}/stop", headers=_auth()).json()["status"] == "done"
    conn.execute("UPDATE wb_runs SET status='running', heartbeat=? WHERE id=?", (store.now(), rid))
    conn.commit()
    assert client.get("/wikibase/status").json()["running"]["status"] == "running"
    assert client.post(f"/wikibase/runs/{rid}/stop", headers=_auth()).json()["status"] == "stopping"
    assert client.get(f"/wikibase/runs/{rid}").json()["status"] == "stopping"
    assert client.get("/wikibase/status").json()["running"]["status"] == "stopping"
    conn.execute("UPDATE wb_runs SET status='done' WHERE id=?", (rid,))
    conn.commit()
    html = (DEPLOY / "wikibase.html").read_text(encoding="utf-8")
    assert '"stop-run"' in html and "/stop" in html and "stopping" in html


def test_page_markup():
    page = (DEPLOY / "wikibase.html").read_text(encoding="utf-8")
    assert '<ul id="menu" data-nav data-active=""></ul>' in page
    for anchor in ("#pipeline", "#items", "#wikidata", "#openrefine", "#runs", "#settings", "/review.html"):
        assert anchor in page
    for must in ("/wikibase/status", "/wikibase/jobs", "/wikibase/runs/", "/wikibase/settings", "/wikibase/refine/import",
                 "Collectors", "Special:BotPasswords", "openrefine-manifest.json", "confirm(", "Nine steps",
                 "/wikibase/wikidata/suggested", "/wikibase/wikidata?status=unsure", '"wd-use"', '"wd-none"',
                 '"wikidata-recheck"', "www.wikidata.org/wiki/", "as suggested", "local model panel"):
        assert must in page, must
    assert 'type="password"' in page and "GDB.me()" in page
    # the proxy, the dev site and the installer know the page
    assert "wikibase" in (DEPLOY / "apache-glassdatabase-proxy.conf").read_text(encoding="utf-8")
    assert "wikibase.html" in (ROOT / "scripts" / "dev_site.py").read_text(encoding="utf-8")
    inst = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "wikibase.html" in inst and "--exclude 'data/wikibase'" in inst and "--exclude 'data/splat_media'" in inst


# --------------------------------------------------------------------------- the Wikidata lookup
def _people():
    P = wikidata.Person
    return [
        P("Q900001", "Nadia Okonkwo", "Nigerian glass artist", [],
          [("has works in the collection", "Q2894440", "Museum of Glass"), ("educated at", "Q16898382", "Pilchuck Glass School")],
          "https://nadia.example", "nadia.glass"),
        P("Q900002", "Ivo Marchetti", "Italian glass artist (born 1950)", [], [("work location", "Q641", "Venice")]),
        P("Q900003", "Ivo Marchetti", "Italian glassblower (born 1971)", ["Ivo F. Marchetti"], []),
        P("Q900010", "Pike Example", "American glass artist", [],
          [("educated at", "Q16898382", "Pilchuck Glass School"), ("work location", "Q1141856", "Crozet")]),
        P("Q900011", "Far Away", "Swedish glass artist", [], [("work location", "Q1754", "Stockholm")]),
        P("Q900012", "State Wide", "American glass artist", [], [("work location", "Q1370", "Virginia")]),
    ]


def _svc(name, qid, desc, *, match=False, types=("human",), alias_only=False):
    return {"id": qid, "name": name, "score": 100.0, "match": match, "description": desc,
            "type": [{"id": "Qx", "name": t} for t in types], "features": [{"id": "all_labels", "value": 100}]}


SERVICE = {
    "crozet": [_svc("Crozet", "Q1141856", "census designated place in Albemarle County, Virginia, United States", match=True,
                    types=("census-designated place in the United States",))],
    "venice": [_svc("Venice", "Q641", "comune and city in Veneto, Italy", types=("city",)),
               _svc("Venice", "Q845634", "city in Sarasota County, Florida, United States", types=("city",))],
    "virginia": [_svc("Virginia", "Q1370", "state of the United States of America", match=True, types=("U.S. state",))],
    "rae sutter": [_svc("Rae Sutter", "Q900100", "American politician")],
    "theo park": [_svc("Theo Park", "Q900101", "British sculptor"), _svc("Theo Park", "Q900102", "Canadian ice hockey player")],
    "bea lindqvist": [_svc("Bea Lindqvist", "Q900103", "Swedish glass sculptor")],
    "teal": [_svc("teal", "Q1078214", "low-saturated color", types=("color",)), _svc("teal", "Q87067389", "eye color", types=("eye color",))],
    "stained glass": [_svc("Stained Glass", "Q483121", "South Korea television drama series", types=("television series",)),
                      _svc("stained glass", "Q1473346", "colored glass used as an art material", types=("type of work of art",))],
    "cane": [_svc("cane", "Q1", "walking stick", types=("walking stick",))],
    "fusing": [_svc("glass fusing", "Q2527106", "process of joining glass by heating it", types=("technique",))],
}


@pytest.fixture
def fake_wikidata(monkeypatch):
    asked: list[dict] = []

    def reconcile(client, queries):
        asked.extend(queries.values())
        return {k: {"result": SERVICE.get(model.norm(q["query"]), [])} for k, q in queries.items()}

    monkeypatch.setattr(wikidata, "fetch_index", lambda client, log=print: _people())
    monkeypatch.setattr(wikidata, "reconcile_batch", reconcile)
    monkeypatch.setattr(wikidata, "PAUSE", 0)
    return asked


def test_wikidata_decisions_and_queries():
    c = lambda qid, name, desc, by=True, match=False, types=("human",): {  # noqa: E731
        "qid": qid, "label": name, "description": desc, "score": 100.0, "match": match, "types": list(types), "exact": True, "by_name": by}
    # a town: the region in the description settles two namesakes
    st, q, _ = wikidata.decide([c("Q1", "Tacoma", "county seat of Pierce County, Washington, United States"),
                                c("Q2", "Tacoma", "unincorporated community in Wise County, Virginia")], context=[["Washington"], ["United States"]])
    assert (st, q) == ("matched", "Q1")
    # two colours of the same name, both named exactly: a person picks
    assert wikidata.decide([c("Q1", "teal", "a colour", types=("color",)), c("Q2", "teal", "eye color", types=("eye color",))])[0] == "unsure"
    # an alias match loses to the item whose own name is ours
    assert wikidata.decide([c("Q1", "curator", "specialist"), c("Q2", "exhibition curator", "organiser", by=False)])[1] == "Q1"
    # glass required: the TV series and the band fall away
    assert wikidata.decide([c("Q1", "Stained Glass", "television drama series"), c("Q2", "stained glass", "colored glass used as an art material")], glass=True)[1] == "Q2"
    # a person found only through the service: another kind of artist waits, a politician is dropped
    assert wikidata.decide([c("Q1", "David Smith", "American visual artist")], glass=True, art_is_unsure=True)[0] == "unsure"
    assert wikidata.decide([c("Q1", "Rae Sutter", "American politician")], glass=True, art_is_unsure=True)[0] == "none"
    assert wikidata.decide([], context=[["x"]])[0] == "none"
    # the service's own unique match passes when no description names our place; otherwise it waits
    assert wikidata.decide([c("Q1", "Venini", "Italian glassmaking company", match=True)], context=[["Venice"], ["Italy"]], glass=True)[0] == "matched"
    assert wikidata.decide([c("Q1", "Venini", "Italian glassmaking company")], context=[["Venice"], ["Italy"]], glass=True)[0] == "unsure"
    # the service's result → our candidate: the name it returns can be in any language
    cand = wikidata.candidate({"id": "Q613380", "name": "دال كيهولى", "score": 100, "match": True, "description": "American glass sculptor",
                               "type": [{"id": "Q5", "name": "human"}], "features": [{"id": "all_labels", "value": 100}]}, {"dale chihuly"})
    assert cand["exact"] is True and cand["by_name"] is False and cand["types"] == ["human"]
    # what is asked, per kind
    items = {}
    person = model.Item("gdb:artists:1", "person", "Rae Sutter", "glass artist, Crozet, Virginia", [], "gdb:class:person")
    q = wikidata.query_for(person, items)
    assert q["type"] == "Q5" and q["glass"] and q["art_is_unsure"] and q["properties"] == [{"pid": "P106", "v": {"id": "Q2865798"}}]
    tech = model.Item("gdb:technique:hot-sculpted", "technique", "Hot-Sculpted Solid Glass (Massiccio)", "x", [], "gdb:class:technique")
    q = wikidata.query_for(tech, items)
    assert q["query"] == "Hot-Sculpted Solid Glass" and "type" not in q and q["shortened"] is True
    assert wikidata.query_for(model.Item("gdb:trait:merese", "trait", "Merese", "x", [], "gdb:class:trait"), items)["shortened"] is False
    fair = model.Item("gdb:art_fairs:1", "show", "Glass Fair (2026)", "art fair in Tacoma", [], "gdb:class:art-fair")
    q = wikidata.query_for(fair, items)
    assert q["query"] == "Glass Fair" and q["type"] == "Q1792535" and q["context"] == [["Tacoma"], []]
    studio = model.Item("gdb:studios:1", "studio", "Blue Ridge Hot Glass", "hot shop in Crozet, Virginia", [], "gdb:class:hot-shop")
    studio.add("country", model.item("gdb:country:united-states"))
    q = wikidata.query_for(studio, items)
    assert q["type"] == "Q43229" and q["glass"] and q["context"] == [["Crozet"], ["Virginia"]] and {"pid": "P17", "v": {"id": "Q30"}} in q["properties"]
    town = model.Item("gdb:place:crozet-virginia", "place", "Crozet", "Virginia, United States", [], "gdb:class:place")
    town.add("located in", model.item("gdb:place:virginia-united-states"))
    region = model.Item("gdb:place:virginia-united-states", "place", "Virginia", "United States", [], "gdb:class:place")
    items = {town.key: town, region.key: region}
    parents = wikidata.parents_of(items)
    assert wikidata.query_for(town, items, parents)["type"] == "Q486972" and wikidata.query_for(town, items, parents)["context"] == [["Virginia"], ["United States"]]
    assert wikidata.query_for(region, items, parents)["type"] == "Q35657"
    veneto = model.Item("gdb:place:veneto-italy", "place", "Veneto", "Italy", [], "gdb:class:place")
    assert wikidata.query_for(veneto, items, {veneto.key})["type"] == "Q10864048"
    assert wikidata.query_for(veneto, items, set())["type"] == "Q486972"       # nothing is located in it: a town


def test_wikidata_lookup_matches_waits_and_reports(conn, fake_wikidata, monkeypatch):
    stage.run(conn, lambda *_: None)
    dedupe.run(conn, lambda *_: None)
    nadia = store.one(conn.execute("SELECT * FROM wb_items WHERE label='Nadia Okonkwo'"))
    store.set_item(conn, nadia["key"], status="pushed", qid="Q77", pushed_hash=nadia["content_hash"])
    lines = []
    s = wikidata.run(conn, lines.append, {})
    rows = {r["key"]: r for r in store.wikidata_rows(conn, limit=5000)[0]}
    by_label = {r["label"]: r for r in rows.values()}
    # people: the index first (one namesake → matched, two → unsure), then the service
    assert by_label["Nadia Okonkwo"]["status"] == "matched" and by_label["Nadia Okonkwo"]["qid"] == "Q900001"
    assert by_label["Nadia Okonkwo"]["facts"]["links"]["has works in the collection"] == ["Museum of Glass"]
    assert by_label["Ivo Marchetti"]["status"] == "unsure" and len(by_label["Ivo Marchetti"]["candidates"]) == 2
    assert by_label["Rae Sutter"]["status"] == "none"                    # a politician is not a glass artist
    assert by_label["Theo Park"]["status"] == "unsure"                   # a sculptor namesake waits for a person
    assert by_label["Bea Lindqvist"]["status"] == "matched" and by_label["Bea Lindqvist"]["qid"] == "Q900103"
    # places: the region in the description decides; a U.S. state is asked for as one
    assert by_label["Crozet"]["qid"] == "Q1141856" and by_label["Virginia"]["qid"] == "Q1370"
    venice = rows["gdb:place:venice-veneto"]
    assert venice["status"] == "matched" and venice["qid"] == "Q641"
    assert "gdb:place:venice-italy" not in rows                          # a duplicate is never looked up
    assert rows["gdb:studios:studios-0000"]["status"] == "none"
    # vocabularies: hand-listed ids are known and never asked about; glass settles a technique
    assert rows["gdb:colour:red"]["status"] == "known" and rows["gdb:colour:red"]["qid"] == "Q3142"
    assert rows["gdb:colour:teal"]["status"] == "unsure"
    assert rows["gdb:technique:stained-glass"]["qid"] == "Q1473346"
    assert rows["gdb:technique:cane-murrine"]["status"] == "none"
    fusing = rows["gdb:technique:fusing-slumping"]                      # asked as "Fusing": right, but a person confirms
    assert fusing["status"] == "unsure" and fusing["candidates"][0]["qid"] == "Q2527106" and "shortened" in fusing["why"]
    assert rows["gdb:country:united-states"]["status"] == "known"
    assert not any(model.norm(q["query"]) in ("red", "united states", "offhand blown glass") for q in fake_wikidata)
    # the id went onto the staged item, and the pushed item now reads changed
    nadia = store.one(conn.execute("SELECT * FROM wb_items WHERE key=?", (nadia["key"],)))
    assert nadia["wikidata_qid"] == "Q900001" and nadia["status"] == "changed"
    assert "Q900001" in store.effective_item(nadia).values(model.WIKIDATA_PROP)
    bea = store.one(conn.execute("SELECT * FROM wb_items WHERE label='Bea Lindqvist'"))
    assert bea["wikidata_qid"] == "Q900103" and bea["status"] == "new"
    # the report: museums holding our people's work, what we lack, and whom Wikidata ties to our places
    assert s["in_collections"] == {"Museum of Glass": ["Nadia Okonkwo"]}
    assert s["adds"]["website"] == (0 if store.effective_item(nadia).has("official website") else 1)
    names = [p["label"] for p in s["suggested"]]
    assert "Pike Example" in names and "Far Away" not in names and "Nadia Okonkwo" not in names and "Ivo Marchetti" not in names
    assert "State Wide" not in names                                     # a state is not an anchor, a town is
    pike = next(p for p in s["suggested"] if p["label"] == "Pike Example")
    assert pike["via"] == ["Crozet (work location)"] and s["suggested_n"] == 1
    assert s["by_status"]["matched"] >= 4 and s["totals"]["linked"] >= 60 and any("Museum of Glass" in ln for ln in lines)
    # a second run asks about nothing; a recheck redoes the automatic decisions, never a person's
    n_asked = len(fake_wikidata)
    assert wikidata.run(conn, lambda *_: None, {})["looked_up"] == 0 and len(fake_wikidata) == n_asked
    ivo = store.one(conn.execute("SELECT * FROM wb_items WHERE label='Ivo Marchetti'"))
    r = wikidata.confirm(conn, ivo["key"], "Q900003", "boss@example.com")
    assert r["status"] == "confirmed" and r["decided_by"] == "boss@example.com"
    assert store.one(conn.execute("SELECT wikidata_qid FROM wb_items WHERE key=?", (ivo["key"],)))["wikidata_qid"] == "Q900003"
    r = wikidata.confirm(conn, bea["key"], "")
    assert r["status"] == "rejected"
    assert store.one(conn.execute("SELECT wikidata_qid FROM wb_items WHERE key=?", (bea["key"],)))["wikidata_qid"] == ""
    # Wikidata changed its mind about Crozet: a recheck revisits the lookup's own matches and
    # takes back an id it set, while a person's decisions and the hand lists stand
    monkeypatch.setitem(SERVICE, "crozet", [])
    s2 = wikidata.run(conn, lambda *_: None, {"recheck": True})
    assert s2["looked_up"] > 0 and s2["by_status"]["matched"] >= 4
    rows = {r["key"]: r for r in store.wikidata_rows(conn, limit=5000)[0]}
    assert rows[ivo["key"]]["status"] == "confirmed" and rows[bea["key"]]["status"] == "rejected"
    assert rows[nadia["key"]]["status"] == "matched" and rows["gdb:colour:red"]["status"] == "known"
    assert rows["gdb:place:crozet-virginia"]["status"] == "none"
    assert store.one(conn.execute("SELECT wikidata_qid FROM wb_items WHERE key='gdb:place:crozet-virginia'"))["wikidata_qid"] == ""
    assert store.one(conn.execute("SELECT wikidata_qid FROM wb_items WHERE key=?", (nadia["key"],)))["wikidata_qid"] == "Q900001"
    assert store.one(conn.execute("SELECT wikidata_qid FROM wb_items WHERE key=?", (bea["key"],)))["wikidata_qid"] == ""
    counts = store.wikidata_counts(conn)
    assert counts["confirmed"] == 1 and counts["rejected"] == 1 and counts["unsure"] >= 2
    assert wikidata.confirm(conn, "gdb:nobody:1", "Q1") is None


def test_api_wikidata_rows_decisions_and_job(client, conn, fake_wikidata):
    import time
    stage.run(conn, lambda *_: None)
    wikidata.run(conn, lambda *_: None, {})
    j = client.get("/wikibase/wikidata", params={"status": "unsure"}).json()
    assert j["total"] >= 2 and all(r["status"] == "unsure" and r["candidates"] for r in j["items"])
    theo = next(r for r in j["items"] if r["label"] == "Theo Park")
    assert theo["item_description"].startswith("glass artist") and theo["candidates"][0]["qid"] == "Q900101"
    assert client.post(f"/wikibase/wikidata/{theo['key']}", json={"qid": "Q900101"}).status_code == 401
    assert client.post(f"/wikibase/wikidata/{theo['key']}", json={"qid": "nope"}, headers=_auth()).status_code == 400
    assert client.post("/wikibase/wikidata/gdb:nobody:1", json={"qid": "Q1"}, headers=_auth()).status_code == 404
    r = client.post(f"/wikibase/wikidata/{theo['key']}", json={"qid": "Q900101"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["decided_by"] == BOSS
    assert client.get(f"/wikibase/items/{theo['key']}").json()["wikidata_qid"] == "Q900101"
    # an outside panel (lexflow run resolve) leaves a suggestion on an unsure row; the row stays unsure, the page shows it
    ivo = next(r for r in j["items"] if r["label"] == "Ivo Marchetti")
    r = client.post(f"/wikibase/wikidata/{ivo['key']}", json={"suggest": {"qid": "q900003", "confidence": 0.91, "settled": True,
                    "because": "the glassblower born 1971 works in Venice", "models": {"qwen3:8b": {"verdict": "Q900003", "confidence": 0.91}},
                    "version": "1-abc"}}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "unsure" and r.json()["suggestion"]["qid"] == "Q900003"
    assert r.json()["suggestion"]["by"] == BOSS and r.json()["suggestion"]["models"]["qwen3:8b"]["verdict"] == "Q900003"
    row = next(x for x in client.get("/wikibase/wikidata", params={"status": "unsure"}).json()["items"] if x["key"] == ivo["key"])
    assert row["suggestion"]["confidence"] == 0.91 and row["suggestion"]["settled"] is True
    assert client.post("/wikibase/wikidata/gdb:nobody:1", json={"suggest": {"qid": "Q1"}}, headers=_auth()).status_code == 404
    assert client.post(f"/wikibase/wikidata/{ivo['key']}", json={"suggest": {"qid": "Q1"}}).status_code == 401
    st = client.get("/wikibase/status").json()
    assert st["steps"]["wikidata"]["matched"] >= 3 and st["steps"]["wikidata"]["confirmed"] == 1 and "wikidata" in st["steps"]
    r = client.post("/wikibase/jobs", json={"kind": "wikidata", "params": {"recheck": True}}, headers=_auth())
    assert r.status_code == 200 and r.json()["params"]["recheck"] is True
    rid = r.json()["run_id"]
    for _ in range(200):
        run = client.get(f"/wikibase/runs/{rid}").json()
        if run["status"] not in ("running", "stopping"):
            break
        time.sleep(0.2)
    assert run["status"] == "done" and run["summary"]["suggested_n"] == 1 and "suggested" not in run["summary"]
    sg = client.get("/wikibase/wikidata/suggested").json()
    assert sg["run"] == rid and sg["people"][0]["label"] == "Pike Example" and sg["in_collections"] == {"Museum of Glass": ["Nadia Okonkwo"]}
    assert client.get("/wikibase/wikidata", params={"status": "confirmed"}).json()["total"] == 1
