# A Trait Thesaurus for Venetian & façon-de-Venise Glass
### A proposal for standards-aligned, community-scale style description

*Prepared for discussion with institutional partners (e.g. The Corning Museum of
Glass). This describes a working system, not a finished authority — the point is
to align it with the field's standards and expertise.*

---

## 1. The problem

Existing directories capture *who* and *where* but almost nothing structured
about *style*. A façon-de-Venise goblet has a dozen diagnostic features — bowl
form, stem construction, knop sequence, foot and pontil treatment, filigrana
family, surface decoration, tooling evidence — that scholars read fluently but
that live only in free-text catalog prose, unqueryable and unlinkable.

We want to capture that vocabulary as **structured, linkable data**, entered by
the people who know it, at the moment a piece is documented — and to do it in a
way a museum can trust and align to.

## 2. What we built

A controlled vocabulary of **observable style traits** for Renaissance
Venetian-style drinking glasses, published as a **SKOS concept scheme**
(70 concepts across 10 facets): *vessel form, bowl, stem, knop, foot & pontil,
rim, metal/body, filigrana, other decoration,* and *making-evidence/condition*.

- Live, dereferenceable, CC-BY:
  **`/api/vocab/glass-traits.ttl`** (SKOS/Turtle) and `/api/vocab/glass-traits.json`.
- Every concept has a **stable URI**, `prefLabel`/`altLabel` (including Italian
  terms — *a retortoli, reticello, ghiaccio, calcedonio*), a working
  `definition`, and **authority-mapping seams**: `skos:exactMatch` to Getty AAT
  and Wikidata, and `rdfs:seeAlso` to the **Corning Glass Dictionary**.
- Traits are **tagged at capture time** in the object tool (a "Style" tab of
  faceted pick-lists) and travel with the piece into the registry and its C2PA
  credential — so the description is bound to the object, not floating in prose.

Design principles a curator will care about:

1. **Features, not verdicts.** Traits record what is *observable*. The system
   never asserts "Venice" vs. "façon de Venise vs. Altare/Hall/Netherlandish" —
   that attribution is genuinely contested in the literature, and we model the
   evidence, not the conclusion.
2. **Human-authored, provenance-tracked.** Every trait is entered or confirmed by
   a person and carried in a signed, reviewable record. If we later add
   model-suggested traits (§5), they arrive as *suggestions with confidence*,
   reviewed like any other submission — never silent machine labels.
3. **Standards-native.** SKOS for the vocabulary; the object model maps cleanly to
   **CIDOC-CRM** and **LIDO** for exchange. Nothing here is bespoke.

## 3. Why SKOS + Getty AAT + CIDOC-CRM

These are the interoperability standards museums already use, so alignment is a
*mapping* exercise, not a migration:

- **SKOS** is how controlled vocabularies are published (Getty's own vocabularies
  are SKOS). Our scheme is valid SKOS (503 triples, parses under `rdflib`).
- **Getty AAT** is the term authority. Each concept has an `exactMatch` slot ready
  for its AAT id.
- **CIDOC-CRM / LIDO** is how the *object* carrying these traits is described and
  exchanged — the registry record maps to `E22 Human-Made Object` with traits as
  typed attributes.

## 4. Where alignment with an institution comes in

The vocabulary is deliberately a *scaffold*. The high-value collaboration is
exactly the part we should not invent unilaterally:

- **Authority mapping.** Fill each concept's `exactMatch` to the correct Getty AAT
  id and Wikidata QID, and link the Corning Glass Dictionary entry. (We seeded a
  few; the rest are marked "mapping pending" on purpose.)
- **Definitions & hierarchy.** Tighten definitions and broader/narrower relations
  against the scholarship — including Corning's own *Techniques of Renaissance
  Venetian-Style Glassworking*.
- **Scope.** Decide what belongs (e.g. how finely to split stem types, whether to
  model knop *sequences* as ordered lists).

The output of that work is a citable, jointly-authored thesaurus that any
collection — not just ours — can adopt.

## 5. The roadmap the data enables (not built yet)

Because traits are captured as clean labels against a fixed vocabulary, the
directory is quietly assembling an **expert-labelled corpus**. That unlocks, in
order of cost:

1. **Now:** rich, queryable style metadata, human-entered — "show me every
   reticello goblet with a folded foot and a serpent stem."
2. **Next:** zero-shot *suggestions* (open-vocabulary vision models prompted with
   our labels) to pre-fill the capture form for an expert to correct — weak
   supervision that respects scholarly time.
3. **Later:** small trained probes on a **frozen** DINOv2 backbone for the traits
   that have enough clean examples and a clear visual signal (filigrana family is
   very learnable). Always reported with per-trait precision/recall against
   expert agreement, always surfaced as confidence, never as fact.

Note we keep this **separate from the re-identification fingerprint**: that
backbone stays general and frozen (see the [fingerprint
protocol](FINGERPRINT-PROTOCOL.md)); trait recognition is a separate head. And
training imagery stays clean — own captures and open-access museum collections,
not auction images — consistent with our C2PA/licensing posture.

## 6. Honest limitations

- **Single photos miss 3-D/tactile evidence** (weight, ring, pontil feel, tooling
  in raking light). Our multi-view + backlit + macro capture rig helps, but does
  not replace handling.
- **The vocabulary is v1** and Venice-centric; other traditions need their own
  branches.
- **Contested attributions stay contested.** This system makes the *evidence*
  legible and comparable; it does not settle authorship.

---

*Vocabulary source: [`central/glass_traits.py`](../central/glass_traits.py) →
[`ontology/glass-traits.ttl`](../ontology/glass-traits.ttl). Diagram:
[`glass-traits.svg`](glass-traits.svg). Terms and definitions draw on standard
references incl. the Corning Museum of Glass Glass Dictionary; authority mappings
are pending alignment.*
