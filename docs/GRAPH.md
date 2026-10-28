People see this as **Who Worked With Whom** (see docs/NAMING.md); the code and URLs keep the old words.

# The relationship graph

*Who taught whom, who made what, which pieces look alike.* The graph is built on
request from the tables the registry already has — no graph database, no new
service — and is the Glass Database's **knowledge-graph seam**: the same
structure is drawn at `/graph.html`, served as JSON at `/api/graph.json`, and
exported as JSON-LD at `/api/graph.jsonld` for RAG pipelines and local LLMs.

Code: `central/graph.py` (build, path, neighbours, JSON-LD) ·
`api/graph_ext.py` (endpoints, 120 s in-process cache) · `deploy/graph.html`
(the page). Only **approved** rows take part — every source table goes through
`central/approvals.py`, the same gate as the API and Explore.

## Nodes

| type | id | from | `meta` |
|---|---|---|---|
| `artist` | `artist:<name, lower>` | approved `artist_submissions`, the `artists` directory, makers named on published objects, names in approved lineage links | `source`: `submission` · `directory` · `object` · `lineage` |
| `technique` | `tech:<label, lower>` | the controlled vocabulary (`central/techniques.py`) plus any label found on an artist or object | `vocab`: the `gbo:` ontology IRI when the label is in the vocabulary |
| `studio` | `studio:<name, lower>` | approved `studios` + `studio_submissions` | `city`, `country`, `kind` |
| `object` | `object:<_row_id>` | approved `objects` (the provenance registry) | `maker`, `year`, `techniques[]`, `image` (`/api/objects/<id>/image` — a URL, never bytes), `dominant_color`, `traits[]`, `fingerprinted` |
| `collector` | `collector:<uid>` | verified `season_claims` | `count` of pieces |

Every node has `id`, `label`, `type`, `href` (where to open it: Explore, the
studios map, the object card) and `meta`. Orphan techniques and studios are
dropped; artists, objects and collectors are always kept.

## Edges and how each one is derived

| type | direction | derived from | confidence (`meta.confidence`) |
|---|---|---|---|
| `uses` | artist → technique | the artist's `tech_primary / tech_secondary / tech_occasional` (submissions) or `primary_technique` (directory) | structured |
| `uses` | object → technique | `objects.techniques` split on `,` and `\|`, matched case-insensitively to technique labels (a new technique node is created if needed) | structured |
| `made_by` | object → artist | `objects.maker` matched case-insensitively to an artist label; otherwise an artist node is created with `source: object` | structured |
| `studied_under` | student → mentor | **first** approved `lineage_submissions` (carries `relation`, `meta.where`, `meta.years`); **then** the artist form's free-text *studied_under* field, where another artist's name (≥ 5 chars) appears as a substring | `structured` > `text-match` |
| `based_at` | artist → studio | an approved studio's name (≥ 5 chars) appears in the artist's `work_experience`, `training_education`, `nationality_base` or `based_in` text | `text-match` |
| `based_at` | object → studio | a studio's name appears in a provenance event's `location` in the object's manifest | `text-match` (`meta.via: provenance-event`) |
| `similar_to` | object ↔ object (undirected, de-duplicated) | cosine similarity of the two fingerprints' embedding centroids ≥ 0.85; `weight` = similarity (3 dp) | `fingerprint` |
| `collected_by` | object → collector | `season_claims` rows with status `verified` / `approved` (table owned by the Seasons module; read best-effort) | structured |

The rule from `docs/GLASS-TRAITS.md` §2 applies — **features, not verdicts**. A
text-match edge says "this name occurs in that text", nothing more; the page
draws it dashed and labels it, and the JSON-LD carries the confidence so a
downstream model can weight it. Structured links (a reviewed lineage
submission, a published piece, a fingerprint) are drawn solid.

### How the fingerprint feeds the graph

A published piece's manifest may carry its re-identification fingerprint
(`manifest_json.fingerprint`, see `docs/FINGERPRINT-PROTOCOL.md`): per-frame
descriptors including a 384-d **DINOv2 embedding** (`frames[*].emb`) and
`metadata.dominantColor`. The builder mean-pools the frame embeddings into one
unit-length **centroid per object** and compares centroids pairwise (pure-python
cosine; capped at the 600 most recent fingerprinted objects). Pairs at or above
`SIMILAR_THRESHOLD = 0.85` get a `similar_to` edge with the similarity as
`weight`. Objects without embeddings get no such edges. The dominant colour
name lands in `meta.dominant_color` (and `gdb:dominantColor` in JSON-LD) so a
consumer can say "amber pieces that look like this one". A `similar_to` edge is a
*visual resemblance*, never a claim of the same maker or the same object.

## Adding lineage

1. **Explore → Submit → Lineage (who taught whom)** — student, mentor, the
   relationship (*Studied under · Apprenticed to · Assisted · Took a class /
   workshop from · Taught by (university / school) · Collaborated with*), where,
   years, a note, and a private email so a reviewer can check.
2. It lands **pending** in `lineage_submissions` and pings Discord like every
   other intake form.
3. An admin approves it (admin console or the one-click link). The next graph
   build draws the edge, creating artist nodes for either name if they are not
   in the directory yet.

Mentions in an artist's own *"Who did you study under?"* text keep working as
the fallback, marked `text-match`.

## The path finder

`/graph.html` — *How are they connected?* Two inputs (a shared datalist of every
label), **Find path**. The page asks `GET /api/graph/path?from=&to=` (BFS over
the undirected graph; ids or labels, case-insensitive; `404 {"detail":"no path"}`
when there is none) and falls back to cytoscape's client-side `aStar` if the API
is unreachable. The path is highlighted and read out as `N steps: A → B → C`
with the edge kinds underneath. The side panel's **Find connections from here**
prefills the first box. Deep links: `?focus=<id or label>` opens the panel on
load, `?mode=lineage`, `?from=&to=` runs a path.

Other modes on the page: type and edge-kind toggles, **Lineage** mode (artists
and `studied_under` only, top-down tree with mentors as roots, optional pieces),
search, and a right-side panel (bottom sheet on phones) with the node's meta,
image thumbnail, neighbours grouped by edge type, and an *Open* link.

## Endpoints

| route | returns |
|---|---|
| `GET /api/graph.json` | `{nodes, edges, counts}` — counts: artists, techniques, studios, objects, collectors, lineage, similar, edges |
| `GET /api/graph.jsonld` | the graph as JSON-LD (`application/ld+json`, 10-min cache header) |
| `GET /api/graph/neighbors?id=&depth=1` | `{node, depth, nodes, edges, groups}` — `groups` is neighbours keyed by edge type with `direction` (`out`/`in`/`both`), `relation`, `weight`, `confidence` |
| `GET /api/graph/path?from=&to=` | `{nodes, edges, steps}` or 404 |

The three `graph_ext` routes share one in-process cache (120 s), so the page's
load + path + neighbours do not rebuild the graph three times.

## JSON-LD for LLMs and RAG

```bash
curl -s https://glassdatabase.org/api/graph.jsonld | python -m json.tool | head -40
```

`@context` maps `schema:` (Person, CreativeWork, Organization, DefinedTerm),
`gbo:` (the glassblowing ontology, `http://example.org/glassblowing#`) and
`gdb:` (`https://glassdatabase.org/vocab/graph#`) with the properties
`studiedUnder`, `similarTo`, `basedAt`, `collectedBy`, `usesTechnique`
(plus `schema:creator` for *made by*). `@graph` holds one object per node; its
edges are properties whose values are the neighbours' IRIs. `lineage` entries
keep the relation, where/years and confidence; `similarity` entries keep the
weight.

Turn it into triples / text chunks for an embedding index or an Ollama prompt:

```python
import json, urllib.request
g = json.load(urllib.request.urlopen("https://glassdatabase.org/api/graph.jsonld"))
name = {n["@id"]: n["name"] for n in g["@graph"]}
for n in g["@graph"]:
    for p in ("studiedUnder", "schema:creator", "usesTechnique", "basedAt", "similarTo", "collectedBy"):
        for o in n.get(p, []):
            print(f'{n["name"]} --{p.split(":")[-1]}--> {name.get(o, o)}')
```

Each printed line is a ready-made chunk ("Rae Student --studiedUnder--> Lino
Maestro"); the `url` of the subject node is the citation to attach. For a graph
query instead of a dump, `/api/graph/neighbors?id=<label>&depth=2` is the
"tell me about X" call and `/api/graph/path` the "how is X related to Y" call —
both answer in the same node/edge shape, so one tool definition covers both.

## What's next

- **Wikidata QIDs** — `central/techniques.py` already gives each technique a
  stable slug and a `gbo:` class; the next column is a QID, which becomes
  `schema:sameAs` on technique nodes (and later on artists and studios), so the
  export links out to Wikidata and the Lexipedia Wikibase.
- **CIDOC-CRM export** — objects as `E22 Human-Made Object`, production events
  (`E12`) carrying maker and technique (`P32 used general technique`), lineage
  as `E7 Activity` with `P14 carried out by`, keeping the same node IRIs.
- **Reviewed suggestions** — fingerprint and text matches could propose lineage
  or attribution rows into the intake queue; they would still arrive as
  suggestions with a confidence, approved by a person, never as silent labels.
