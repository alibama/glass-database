"""Venetian trait thesaurus: SKOS validity, resolution, and manifest tagging."""
from central import glass_traits as gt


def test_vocab_shape():
    assert len(gt.FACETS) == 10 and len(gt.CONCEPTS) >= 60
    assert "Vetro a reticello" in gt.labels_by_facet("filigrana")
    r = gt.resolve("Serpent stem")
    assert r["facet"] == "stem" and r["id"] == "stem-serpent"


def test_resolve_many_gives_uris():
    out = gt.resolve_many(["Cristallo", "Vetro a reticello", "not-a-trait"])
    assert len(out) == 2 and all(o["uri"].startswith(gt.VOCAB) for o in out)
    assert {o["facet"] for o in out} == {"metal", "filigrana"}


def test_skos_is_valid_turtle():
    ttl = gt.to_skos()
    assert "skos:ConceptScheme" in ttl and "rdfs:seeAlso" in ttl  # cmog links present
    import pytest
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph(); g.parse(data=ttl, format="turtle")
    from rdflib.namespace import SKOS
    assert len(set(g.subjects(rdflib.RDF.type, SKOS.Concept))) == len(gt.CONCEPTS)
    assert len(set(g.subjects(rdflib.RDF.type, SKOS.Collection))) == len(gt.FACETS)


def test_cmog_seealso_present():
    # a few concepts link to the Corning Glass Dictionary
    assert any(c["cmog"] for c in gt.CONCEPTS)
    assert gt.resolve("Cristallo")["cmog"].startswith("https://www.cmog.org/")
