"""Relationship graph builder."""
from central import approvals, graph, intake
from central.dbconn import connect


def _artist(c, name, techs=None, mentor_text=""):
    return intake.submit(c, "artist", {
        "artist_name": name, "nationality_base": "US", "status": "Living / Active",
        "primary_focus": "Hot Glass / Furnace Work", "tech_primary": techs or [],
        "training_education": "x", "studied_under": mentor_text}, base_url="")


def test_graph_edges(demo_db):
    c = connect()
    a = _artist(c, "Lino Maestro", ["Offhand Blown Glass", "Cane & Murrine"])
    b = _artist(c, "Rae Student", ["Offhand Blown Glass"], "studied under Lino Maestro")
    approvals.set_status(c, "artist_submissions", [a, b], "approved")
    g = graph.build(c)
    assert g["counts"]["artists"] >= 2 and g["counts"]["techniques"] >= 2
    assert any(e["type"] == "studied_under" for e in g["edges"])
    # both artists connect to the shared technique
    shared = [e for e in g["edges"] if e["type"] == "uses" and e["target"] == "tech:offhand blown glass"]
    assert len(shared) == 2


def test_graph_endpoint(demo_db):
    c = connect()
    approvals.set_status(c, "artist_submissions", [_artist(c, "Solo Artist", ["Fusing & Slumping"])], "approved")
    from fastapi.testclient import TestClient

    from api.main import app
    j = TestClient(app).get("/graph.json").json()
    assert "nodes" in j and "edges" in j and j["counts"]["artists"] >= 1
