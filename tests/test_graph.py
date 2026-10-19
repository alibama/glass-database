"""Relationship graph builder + the knowledge-graph endpoints."""
import json

from central import approvals, graph, intake
from central.dbconn import connect


def _artist(c, name, techs=None, mentor_text=""):
    return intake.submit(c, "artist", {
        "artist_name": name, "nationality_base": "US", "status": "Living / Active",
        "primary_focus": "Hot Glass / Furnace Work", "tech_primary": techs or [],
        "training_education": "x", "studied_under": mentor_text}, base_url="")


def _object(c, rid, title, maker, techniques="", emb=None, year="2024"):
    """Insert an approved object straight into the public registry."""
    from glowtbook.contribute import ensure_central_objects
    ensure_central_objects(c)
    man = {"title": title, "maker": maker}
    if emb is not None:
        man["fingerprint"] = {"frames": [{"emb": emb}, {"emb": emb}],
                              "metadata": {"dominantColor": {"name": "amber"}}}
    c.execute("INSERT OR REPLACE INTO objects (_row_id,title,maker,year,techniques,manifest_json,"
              "content_hash,published_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
              (rid, title, maker, year, techniques, json.dumps(man), "hash-" + rid))
    c.commit()
    approvals.set_status(c, "objects", [rid], "approved")
    return rid


def _client():
    from fastapi.testclient import TestClient

    from api import graph_ext
    from api.main import app
    graph_ext.invalidate()
    return TestClient(app)


def test_graph_edges(demo_db):
    c = connect()
    a = _artist(c, "Lino Maestro", ["Offhand Blown Glass", "Cane & Murrine"])
    b = _artist(c, "Rae Student", ["Offhand Blown Glass"], "studied under Lino Maestro")
    approvals.set_status(c, "artist_submissions", [a, b], "approved")
    g = graph.build(c)
    assert g["counts"]["artists"] >= 2 and g["counts"]["techniques"] >= 2
    assert any(e["type"] == "studied_under" for e in g["edges"])
    # both artists connect to the shared technique (demo pieces may link to it too)
    shared = [e for e in g["edges"] if e["type"] == "uses" and e["target"] == "tech:offhand blown glass"
              and e["source"].startswith("artist:")]
    assert len(shared) == 2
    # the text-matched mentor link says so
    su = next(e for e in g["edges"] if e["type"] == "studied_under"
              and e["source"] == "artist:rae student")
    assert su["target"] == "artist:lino maestro" and su["meta"]["confidence"] == "text-match"
    # every node carries href + meta; artists say where they came from
    n = next(n for n in g["nodes"] if n["id"] == "artist:lino maestro")
    assert n["href"].startswith("/explore/?view=datasets&q=Lino") and n["meta"]["source"] == "submission"
    assert {"objects", "lineage", "similar", "collectors", "studios"} <= set(g["counts"])


def test_graph_endpoint(demo_db):
    c = connect()
    approvals.set_status(c, "artist_submissions", [_artist(c, "Solo Artist", ["Fusing & Slumping"])], "approved")
    from fastapi.testclient import TestClient

    from api.main import app
    j = TestClient(app).get("/graph.json").json()
    assert "nodes" in j and "edges" in j and j["counts"]["artists"] >= 1


def test_objects_join_the_graph(demo_db):
    c = connect()
    approvals.set_status(c, "artist_submissions",
                         [_artist(c, "Mara Vessel", ["Offhand Blown Glass"])], "approved")
    _object(c, "objA1", "Amber Goblet", "mara vessel", "Offhand Blown Glass, Cane & Murrine")
    _object(c, "objB2", "Lone Bowl", "Unknown Newcomer", "Fusing & Slumping | Cold Working")
    g = graph.build(c)
    ids = {n["id"]: n for n in g["nodes"]}
    ob = ids["object:objA1"]
    assert ob["type"] == "object" and ob["href"] == "/explore/?view=objects#obj-objA1"
    assert ob["meta"]["image"] == "/api/objects/objA1/image" and ob["meta"]["maker"] == "mara vessel"
    assert ob["meta"]["techniques"] == ["Offhand Blown Glass", "Cane & Murrine"]
    # made_by matches the existing artist case-insensitively (no duplicate artist node)
    mb = [e for e in g["edges"] if e["type"] == "made_by" and e["source"] == "object:objA1"]
    assert mb and mb[0]["target"] == "artist:mara vessel"
    assert ids["artist:mara vessel"]["meta"]["source"] == "submission"
    # uses edges split on "," and "|" and land on the canonical technique nodes
    uses = {e["target"] for e in g["edges"] if e["type"] == "uses" and e["source"] == "object:objA1"}
    assert uses == {"tech:offhand blown glass", "tech:cane & murrine"}
    uses2 = {e["target"] for e in g["edges"] if e["type"] == "uses" and e["source"] == "object:objB2"}
    assert uses2 == {"tech:fusing & slumping", "tech:cold working"}
    # a maker nobody has heard of becomes an artist node sourced from the object
    assert ids["artist:unknown newcomer"]["meta"]["source"] == "object"
    assert any(e["type"] == "made_by" and e["target"] == "artist:unknown newcomer" for e in g["edges"])
    assert g["counts"]["objects"] >= 2


def test_structured_lineage(demo_db):
    c = connect()
    rid = intake.submit(c, "lineage", {
        "student_name": "Zed Pupil", "mentor_name": "Yara Teacher",
        "relation": "Apprenticed to", "where": "Pilchuck", "years": "2014–2016",
        "email": "z@example.org"}, base_url="")
    assert not any(e["source"] == "artist:zed pupil" for e in graph.build(c)["edges"])  # pending
    approvals.set_status(c, "lineage_submissions", [rid], "approved")
    g = graph.build(c)
    ids = {n["id"] for n in g["nodes"]}
    assert {"artist:zed pupil", "artist:yara teacher"} <= ids   # neither was in the directory
    e = next(e for e in g["edges"] if e["type"] == "studied_under"
             and e["source"] == "artist:zed pupil")
    assert e["target"] == "artist:yara teacher" and e["relation"] == "Apprenticed to"
    assert e["meta"]["confidence"] == "structured" and e["meta"]["where"] == "Pilchuck"
    assert g["counts"]["lineage"] >= 1
    # the private fields never reach the public column list
    priv = {r[0] for r in c.execute(
        "SELECT column FROM _columns WHERE tbl='lineage_submissions' AND is_public=0")}
    assert {"email", "submitted_by"} <= priv


def test_similar_objects_by_fingerprint(demo_db):
    c = connect()
    base = [0.9, 0.1, 0.05, 0.0, 0.2, 0.0, 0.1, 0.0]
    near = [0.88, 0.12, 0.06, 0.01, 0.19, 0.0, 0.11, 0.02]
    ortho = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    _object(c, "simA", "Twin One", "Pair Maker", emb=base)
    _object(c, "simB", "Twin Two", "Pair Maker", emb=near)
    _object(c, "simC", "Odd One", "Pair Maker", emb=ortho)
    g = graph.build(c)
    sim = [e for e in g["edges"] if e["type"] == "similar_to"]
    pairs = {tuple(sorted((e["source"], e["target"]))) for e in sim}
    assert ("object:simA", "object:simB") in pairs
    assert not any("object:simC" in p for p in pairs)
    ab = next(e for e in sim if {e["source"], e["target"]} == {"object:simA", "object:simB"})
    assert ab["weight"] >= 0.85 and ab["meta"]["confidence"] == "fingerprint"
    assert len([e for e in sim if {e["source"], e["target"]} == {"object:simA", "object:simB"}]) == 1
    node = next(n for n in g["nodes"] if n["id"] == "object:simA")
    assert node["meta"]["dominant_color"] == "amber" and node["meta"]["fingerprinted"] is True
    assert graph.cosine(base, near) >= 0.85 and graph.cosine(base, ortho) < 0.1


def test_jsonld_export(demo_db):
    cl = _client()
    r = cl.get("/graph.jsonld")
    assert r.status_code == 200 and "application/ld+json" in r.headers["content-type"]
    assert "max-age=600" in r.headers["cache-control"]
    j = r.json()
    assert "@context" in j and "@graph" in j
    assert j["@context"]["gdb"] == "https://glassdatabase.org/vocab/graph#"
    assert j["@context"]["studiedUnder"]["@id"] == "gdb:studiedUnder"
    people = [n for n in j["@graph"] if n["@type"] == "schema:Person"]
    assert people and all(n["@id"].startswith("https://glassdatabase.org/graph#") for n in people)
    pupil = next(n for n in j["@graph"] if n["name"] == "Zed Pupil")
    assert pupil["studiedUnder"] and pupil["lineage"][0]["schema:roleName"] == "Apprenticed to"
    # pure-python conversion of the export to triples is a one-liner away
    g = graph.build(connect())
    assert graph.to_jsonld(g)["gdb:counts"] == g["counts"]


def test_path_via_shared_technique(demo_db):
    c = connect()
    a = _artist(c, "Path Alpha", ["Pâte de Verre"])
    b = _artist(c, "Path Beta", ["Pâte de Verre"])
    approvals.set_status(c, "artist_submissions", [a, b], "approved")
    cl = _client()
    j = cl.get("/graph/path", params={"from": "Path Alpha", "to": "path beta"}).json()
    assert j["steps"] == 2 and [n["label"] for n in j["nodes"]] == ["Path Alpha", "Pâte de Verre", "Path Beta"]
    assert [e["type"] for e in j["edges"]] == ["uses", "uses"]
    # ids work too, and the same node is a zero-step path
    assert cl.get("/graph/path", params={"from": "artist:path alpha", "to": "artist:path alpha"}).json()["steps"] == 0
    # no path → 404 with the agreed body
    lone = _artist(c, "Island Nobody", [])
    approvals.set_status(c, "artist_submissions", [lone], "approved")
    from api import graph_ext
    graph_ext.invalidate()
    r = cl.get("/graph/path", params={"from": "Island Nobody", "to": "Path Alpha"})
    assert r.status_code == 404 and r.json() == {"detail": "no path"}
    assert cl.get("/graph/path", params={"from": "Nobody At All", "to": "Path Alpha"}).status_code == 404


def test_neighbors_endpoint(demo_db):
    cl = _client()
    j = cl.get("/graph/neighbors", params={"id": "Zed Pupil"}).json()
    assert j["node"]["id"] == "artist:zed pupil" and j["depth"] == 1
    assert {n["id"] for n in j["nodes"]} == {"artist:zed pupil", "artist:yara teacher"}
    su = j["groups"]["studied_under"][0]
    assert su["id"] == "artist:yara teacher" and su["direction"] == "out"
    assert su["relation"] == "Apprenticed to" and su["confidence"] == "structured"
    # depth 2 from an object reaches the technique's other users
    j2 = cl.get("/graph/neighbors", params={"id": "object:objA1", "depth": 2}).json()
    assert "artist:mara vessel" in {n["id"] for n in j2["nodes"]}
    assert {"made_by", "uses"} <= set(j2["groups"])
    assert cl.get("/graph/neighbors", params={"id": "no such node anywhere"}).status_code == 404
