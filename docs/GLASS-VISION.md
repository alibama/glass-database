# Seeing Glass: a local vision model for the registry

*Design note for the question "could we build a custom Ollama AI that dials in on
the nuance of glass production — sees murrine or cane, reads colour back to its
fabrication source, notices minute details? How does one see a Lino?" — and the
companion question of whether the fingerprint data can feed an AI and the graph.*

Status: Stage 0 and Stage 1 are implemented — the Ollama adapter
(`glowtbook/vision.py`), the k-NN index over the public registry's fingerprint
embeddings (`central/vision_index.py`, wired into Glowtbook's Style tab, no
model needed), its leave-one-out evaluation (`scripts/eval_vision_probe.py`)
and the corpus exporter (`scripts/export_vision_corpus.py`); the rest is a plan
with honest cost estimates. Related: [GLASS-TRAITS.md](GLASS-TRAITS.md) (the
vocabulary and the "features, not verdicts" rule), [FINGERPRINT-PROTOCOL.md](FINGERPRINT-PROTOCOL.md)
(what the capture already collects), [object-fingerprinting-best-practices.md](object-fingerprinting-best-practices.md)
(lighting and capture), `deploy/HARVEST.md` (consent).

---

## 1. What "seeing glass" means

"Seeing" is several different problems stacked up, and they differ wildly in how
learnable they are from photographs. Naming the levels keeps us honest about
which ones a model can target and with what evidence.

**Material and process.** Blown, hot-sculpted, furnace- or kiln-cast, fused and
slumped, flameworked (soft glass vs. borosilicate), cold-worked, stained and
leaded, dalle de verre, pâte de verre, neon. These leave gross, consistent
visual signatures — wall thickness and optical distortion for blown work, mould
texture and translucency for kiln casting, seams and lead came for stained glass,
the hard-edged clarity of boro. A general vision-language model already does
tolerably here, and the registry's technique list (`central/techniques.py`, 25
labels across all disciplines) is the right output vocabulary.

**Construction.** This is where glass people actually look: **cane** (pulled
rods, *a fili*, *a retortoli*/zanfirico twists, reticello nets with their trapped
bubble grid), **murrine** (cross-sections of built-up cane — the eye reads the
pattern repetition and the slight ovalisation from pick-up and blowing),
**incalmo** (two bubbles joined hot, a clean horizontal seam between colour
fields), optic ribbing, battuto and inciso cold surfaces, pulled feathers in soft
glass, dichroic or fumed surfaces in boro, lead lines and painted trace in stained
glass. These are *patterns*, often small, often only visible at an angle or in
transmitted light. A generic model is weak here; this is exactly what a trained
head learns well once it has a few hundred clean examples per pattern, because
the signal is repeated and geometric.

**Colour — and the fabrication-source question.** The owner's hunch is sound:
colour rods and frits are made by a handful of houses and each has a recognisable
vocabulary and behaviour. Reichenbach, Gaffer and Kugler (furnace colour),
Effetre/Moretti, Vetrofond and CiM (soft-glass rod), Northstar, Glass Alchemy,
Trautman and Momka (borosilicate) — the striking and reducing silver colours of
boro, Gaffer's dense opal series, Reichenbach's gold-ruby and the way Kugler
reds strike, Effetre's EDP and ivory reactions. Humans learn these by handling
them; a model can learn them from pixels only if the capture controls for
lighting and the labels exist. Two things in the system make this learnable
rather than wishful:

1. **Mat-calibrated, white-balanced capture.** The fingerprint protocol already
   white-balances every analysis frame against the mat's neutral margin
   (§2.1 of the protocol), and the printed colour strip is the seam for a full
   colour transform to Lab. That turns "colour" from a lighting accident into a
   measurement.
2. **Makers recording the rods they used.** Glowtbook's object record has a
   `colour_sources` field ("Reichenbach R-102, Kugler K-203 opal"). Every piece
   where a maker fills it in is a labelled example. The exporter carries that
   field; a colour-source classifier on mat-calibrated patches is a small,
   well-posed model once a few hundred such records exist.

Caveat worth stating: many colours are near-metamers across manufacturers, glass
is layered (a cased ruby over clear over an opal), and striking depends on the
maker's heat history. Expect "family of sources with confidence", not a SKU.

**Surface and micro-detail.** Pontil treatment (rough, ground, fire-polished),
jack lines and tool marks in raking light, shear marks, seeds and cords, chill
marks, devitrification, cold-work facets and wheel paths, wear rings. The
fingerprint protocol's capture passes — **macro detail shots**, a **raking-light
pass**, a **backlit pass**, a polariser for stress — exist precisely to make
these visible; the trait thesaurus has an *evidence* facet to label them. This
level needs the right photographs more than it needs a clever model.

**Attribution: "how does one see a Lino?"** Honestly: connoisseurship is not one
feature, it is a **pattern** — a recurring set of technique choices (incalmo in
stacked bands, zanfirico cane, specific murrine), characteristic proportions and
form families, a colour vocabulary, a finishing standard (the quality of the lip,
the foot, the cold work), plus context the photo does not carry (provenance,
signatures, year, who the assistants were). An expert sees a Lino Tagliapietra
the way a musician recognises a composer: by style similarity over many cues,
with a known error rate. A model can learn two legitimate things here: **style
similarity** ("this is close, in embedding space, to pieces documented with these
techniques and traits") and **surface evidence** (the individual features above,
each with a confidence). What it must not do is emit a name. The registry's house
rule — *features, not verdicts* ([GLASS-TRAITS.md §2](GLASS-TRAITS.md)) — applies
with extra force to a model: it presents evidence and similarity with confidence;
the maker, an admin or an expert draws the conclusion and signs it into the
record. Attribution harm and forgery assistance (§5) are the reasons, not
modesty.

## 2. The staged plan — and what exists today

| Stage | What | Status |
|---|---|---|
| 0 | Zero-shot VLM through Ollama with the closed vocabulary | **built** — `glowtbook.vision.suggest()` |
| 1 | Probes on the frozen DINOv2 embeddings the fingerprint collects | **built** — `central/vision_index.py` (cached k-NN index over the approved registry), *Suggest from similar pieces* in the Style tab, `scripts/eval_vision_probe.py` (measured precision/recall); logistic heads next |
| 2 | LoRA fine-tune of a VLM on the exported corpus, served locally | exporter built (`scripts/export_vision_corpus.py`); training is a recipe below |
| 3 | Specialist heads (murrine/cane detector, colour-source classifier, defect/detail detector) | planned; needs Stage 1–2 data |

### Stage 0 — zero-shot through Ollama (what `vision.py` does)

`build_prompt()` turns the registry's vocabularies into the instruction: all
technique labels, the trait thesaurus grouped by facet with definitions and
alternate terms, the house rule, and a strict JSON shape. `suggest(image)`
downsizes the picture to 1024 px (`media.condense_image`), POSTs to Ollama's
`/api/chat` with `format: json` and temperature 0.1, and `parse_response()`
normalises the reply: fences stripped, labels validated case-insensitively
against the vocabularies (unknown terms land in `unmapped` instead of being
invented), confidences clamped to [0, 1], missing keys tolerated. It never
raises; without `OLLAMA_URL` it returns `ok: false` with the instruction to
enable it, and the UI shows `suggest_from_fingerprint()` instead — colour,
dimensions and view counts straight from the descriptor metadata.

Expected quality, from experience with 7B-class VLMs (Qwen2.5-VL 7B is the
default): **decent on process and colour** (blown vs. cast vs. flameworked,
transparent vs. opal, dominant hue), **useful on gross form** (goblet, bowl,
stem type when the whole piece is in frame), **weak on fine construction** — it
will say "cane" for any stripe and miss murrine unless they are large and
frontal, and it cannot reliably separate *a retortoli* from *a fili*. That is
fine for the job it has: pre-filling a form that a person corrects. Everything
it produces lands as a suggestion with confidence.

### Stage 1 — probes on the frozen DINOv2 backbone (data we already have)

Every AI-on enrolment stores up to eight mean-pooled 384-d DINOv2 vectors. The
fingerprint stays general and frozen (it is the re-identification backbone; see
GLASS-TRAITS §5 on keeping the two separate), but it is also a free, decent
image representation. `centroid(fp)` averages and normalises a piece's vectors;
`knn_suggest(fp, corpus, k, key)` votes labels from the nearest records
(`embedding_centroid` + `techniques` or `traits`). This is the first probe, and
it works with zero training.

**What exists now** (`central/vision_index.py`, tests in `tests/test_vision_index.py`):

- `load_records(conn)` walks the public `objects` table — approved rows only,
  the central publication gate — and keeps every object whose
  `manifest_json.fingerprint` carries embeddings, with its canonical technique
  labels, SKOS traits grouped by facet, dominant colour and centroid. The record
  shape is one line of `corpus.jsonl`, so an exported corpus and the live
  registry are interchangeable (`records_from_corpus()` de-duplicates a corpus
  to one record per object).
- `VisionIndex` holds those records in memory, bucketed by vector dimension, and
  answers `neighbours(fp, k, exclude)` by cosine similarity (a numpy matrix
  product when numpy is importable, a pure-python loop otherwise — no new
  dependency). `get()` caches the built index for five minutes per database;
  `invalidate()` drops it after approvals change.
- `suggest_for_fingerprint(fp, k=5, exclude=...)` returns
  `{techniques: [{label, score, support}], traits: {facet: [...]},
  neighbours: [{object_row_id, title, score, image, href, ...}], evidence,
  caveats}`: *score* is the label's similarity-weighted share of the
  neighbourhood (`knn_suggest`), *support* how many of the k neighbours carry
  it. A fingerprint without embeddings, an empty registry or a broken database
  come back as `ok: false` with a sentence, never an exception.
- **Glowtbook → Objects → Style** shows *Suggest from similar pieces* for any
  piece whose fingerprint has embeddings, with or without Ollama: the nearest
  public pieces as thumbnails (`/api/objects/<id>/image`, linked to their
  Explore page) with their similarity, the voted techniques and traits with
  support ("4/5 · 100%"), and *Apply majority traits* — labels at least half the
  neighbourhood agrees on land in the pick-lists for the person to confirm and
  save, the same flow as the Ollama suggestions. The piece's own published twin
  is excluded from its neighbourhood.

**Measured, not promised.** `python -m scripts.eval_vision_probe` holds out
each object with embeddings in turn, lets its k nearest *other* objects vote,
counts a label as predicted when its weighted share reaches `--threshold`
(default 0.5), and reports per-label **precision / recall / F1 / support** for
techniques and for every trait facet, micro and macro averages per group
(`--min-support` keeps tiny labels out of the macro), each label's prevalence
(chance level), and how often the nearest neighbour shares a technique with the
held-out piece. It prints the table and writes the same numbers as JSON
(`--out`, default `data/vision_eval.json`); `--corpus corpus.jsonl` scores an
exported corpus instead of the live registry, `--include-pending` adds
not-yet-reviewed rows for a local experiment, `--k` sets the neighbourhood. On
the seeded demo database it reports that nothing carries embeddings yet — the
demo fingerprints are descriptor-only — which is the honest number until real
AI-on captures are approved. The registry's labels are contributor-entered and
admin-approved, so the report measures agreement with the registry, not truth;
report its numbers next to the suggestion type and retire what does not clear a
bar.

The next probe is a per-label **logistic regression / small MLP** on the same
vectors (scikit-learn, seconds to train, a few KB per head). Data need:
roughly **50–100 clean examples per class** for a usable head on visually
distinct labels (filigrana family, kiln-cast vs. blown, stained vs. everything
else); subtler traits need several hundred. Evaluation is **per-label
precision/recall against expert agreement** on a held-out set — the registry's
labels are maker-entered and admin-approved, which is weak supervision, so an
expert-checked evaluation set (a few hundred images) is the first thing to build.
Report the numbers next to every suggestion type; retire heads that do not clear
a bar.

Limit: the pooled vector is global. It separates pieces and gross style but
throws away the local pattern information murrine and cane live in. For those
we need patch-level features or a fine-tuned model (Stage 2–3).

### Stage 2 — LoRA fine-tune of a VLM, served locally

`scripts/export_vision_corpus.py --format both` writes `images/`, `corpus.jsonl`
and a ShareGPT-style `train.jsonl` where the user turn is `<image>` + the
closed-vocabulary prompt and the assistant turn is the target JSON — the same
shape `suggest()` returns, filled from the registry's labels. That is the input
format Unsloth and LLaMA-Factory expect for vision models.

Recipe (one GPU with 24 GB, or a rented A10/L4 for an hour or two):

1. **Base model:** Qwen2.5-VL 7B Instruct (best JSON discipline and fine-grained
   reading among open models of its size); alternatives are Gemma 3 12B or a
   LLaVA-family model if licensing or tooling prefers them.
2. **Train** with Unsloth (`FastVisionModel`, LoRA rank 16 on the language layers,
   vision tower frozen at first; unfreeze it only once there are thousands of
   images): 2–3 epochs over `train.jsonl`, batch 2–4 with gradient accumulation,
   lr 2e-4. Hold out 10 % by object, never by image.
3. **Evaluate** by parsing the model's JSON with `vision.parse_response()` and
   scoring per-label precision/recall against the held-out targets *and* the
   expert set from Stage 1. Compare with the zero-shot base model — a fine-tune
   that does not beat zero-shot on the expert set is not worth deploying.
4. **Merge** the LoRA into the base weights and **convert to GGUF**
   (`llama.cpp/convert_hf_to_gguf.py`, then quantise to Q4_K_M).
5. **Serve**: Ollama *serves* models, it does not train them. Import with a
   Modelfile:

   ```
   FROM ./glass-eye-q4_k_m.gguf
   # vision models also need their projector file beside the weights:
   # FROM ./mmproj-glass-eye-f16.gguf   (see caveat below)
   PARAMETER temperature 0.1
   PARAMETER num_ctx 8192      # the closed-vocabulary prompt is ~2.3k tokens + image tokens
   SYSTEM """You describe glass objects for an open registry. Describe observable
   features only; never attribute a maker, origin, period or value. Answer in JSON."""
   ```
   `ollama create glass-eye -f Modelfile`, then `GLASS_VISION_MODEL=glass-eye`.

   **Caveat, stated plainly:** GGUF import of *vision* models is the rough edge.
   The vision projector (`mmproj`) must be converted and paired with the language
   weights, and Ollama's support for importing custom multimodal GGUFs has lagged
   behind its support for the models it ships. If `ollama create` refuses the
   model or the projector, the practical path is to serve the merged HF model with
   **vLLM** (OpenAI-compatible `/v1/chat/completions`) or **llama.cpp's server**
   (`llama-server --mmproj ...`), and point a thin shim at it — `vision.py`'s
   `_post` is the only function that would change. Do not let the import step
   block the training step.

### Stage 3 — specialist heads

Small, single-purpose models that answer one question well, trained on the
corpus and surfaced as extra fields in the same suggestion JSON:

- **Murrine / cane pattern detector** — a patch-level detector (YOLO-style or a
  DINOv2 patch-feature probe) that finds and classifies cane and murrine regions
  (twist, net, cross-section) and reports count and coverage. This is the
  "sees murrine or cane" the owner asked for, done properly.
- **Colour-source classifier** — a classifier on mat-calibrated colour patches
  (white-balanced, ideally Lab-transformed via the colour strip) trained from
  `colour_sources` records; output is a ranked list of manufacturer/colour
  families with confidence. Needs the maker-recorded labels to accumulate.
- **Detail / defect detector** — pontil type, tool marks, seeds and cords, chill
  marks, cracks and repairs, from macro, raking and backlit frames. Useful for
  condition reports and for the *evidence* facet of the thesaurus.

## 3. Data

**Sources that respect the project's licence posture.**

- **Own captures with consent** — Glowtbook contributions and fingerprint
  enrolments: the cleanest data, already labelled, already under CC-BY with the
  contributor's consent, with the mat as calibration.
- **Consented harvest** — `central/harvest.py` only records items from artists who
  ticked harvest consent on an approved directory entry, signs the artist as owner,
  and keeps everything pending until approved. Those images may be used the same
  way the artist allowed them to be shown.
- **Open-access museum collections** — The Met Open Access (CC0), Smithsonian Open
  Access (CC0), Cleveland Museum of Art Open Access (CC0), Art Institute of Chicago
  (CC0 for public-domain works, with an API). These carry curator-written medium
  and technique fields that map onto our vocabulary. **Check The Corning Museum of
  Glass's terms** before use — its collection images are the most relevant and are
  not blanket open-licensed; a partnership conversation (GLASS-TRAITS §4) is the
  right route.
- **Not auction sites.** Attribution-rich but rights-encumbered, and scraping them
  would import exactly the "verdict" bias we are avoiding.

**Labels we already have.** Techniques (closed list), SKOS traits by facet,
materials, dimensions (text and mat-measured mm), the fingerprint's dominant
colour and DINOv2 vectors, image roles (primary / detail / backlit), captions,
and `colour_sources` where recorded. The exporter writes all of it per image.

**The mat as calibration instrument.** Four ArUco markers for scale, a white
margin for white balance, a colour strip for a future Lab transform. Every photo
taken on it is a measurement, not just a picture — which is what makes colour
and size learnable across phones and lighting.

**Seasonal games multiply labelled captures.** When people scan collectible
pieces (seasonal ornaments, paperweights, marbles) during a Seasons game, every
scan is a mat-calibrated multi-view capture of a *known* object class. One
season with a few hundred participants can produce more consistently-lit,
labelled captures than a year of ordinary contributions. Design the game rounds
around classes we want data for (a murrine round, a cane round, a colour round).

**Target dataset sizes.**

| Stage | Need | Rough target |
|---|---|---|
| 0 zero-shot | none to run; ~200 expert-checked images to *measure* it | 200 images |
| 1 probes | 50–100 per class for distinct classes, 300+ for subtle traits | 2–5 k images with embeddings |
| 2 LoRA VLM | thousands of image/target pairs, held out by object | 5–20 k images |
| 3 murrine/cane detector | region labels (boxes/masks) | 1–3 k annotated regions |
| 3 colour-source classifier | mat-calibrated patches with maker-recorded sources | 100+ per manufacturer family |
| 3 detail/defect detector | macro/raking/backlit frames with evidence labels | 2–5 k frames |

## 4. How the fingerprint data feeds the graph and the AI

The fingerprint was built for re-identification, but its descriptors are
general enough to enrich the relationship graph (`central/graph.py` today has
artists, techniques and studios with *uses* / *studied_under* / *based at* edges):

- **Embedding centroids → `similar_to` edges.** Nearest neighbours by cosine over
  `embedding_centroid` (thresholded, top-k) give object↔object edges with a
  weight; clusters of similar objects surface style families, and an object's
  neighbours' techniques become suggestions (`knn_suggest`).
- **Colour metadata → palette clusters.** Dominant colour (hex, white-balanced)
  clusters into palette nodes; later, colour-source predictions link objects to
  manufacturer colour families, and makers to the palettes they favour.
- **Dimensions → form classes.** Mat-measured W×D×H bins into form/size classes
  (miniature, tabletop, vessel proportions) that combine with the *form* facet.

**Privacy note.** The public record carries descriptors only — vectors,
histograms, hashes, a dominant colour, millimetres — never the capture frames,
which stay in the contributor's archival package (protocol §1). Graph edges are
derived from descriptors, so nothing new about a person leaks; object nodes link
to the already-public object page.

## 5. Risks and ethics

- **Attribution harm.** A wrong "this looks like X" attached to a real artist's
  record can cost them a sale or a reputation, and an unearned attribution can
  inflate a fake. The model therefore has **no attribution output at all**: no
  maker, origin, period or value fields, the prompt forbids names, and the parser
  only admits vocabulary labels. Similarity is reported between *objects*, with a
  score, not as a name.
- **Forgery assistance.** A model that says "to pass as X, add these features"
  is a forger's checklist. Evidence-not-verdict limits that: the outputs describe
  what *is* in a photo, not what is *missing* relative to a maker. We do not
  publish maker-conditioned classifiers or style-transfer tooling.
- **Consent and licence.** Training only on own captures with consent, consented
  harvest, and open-access collections; the exporter honours the approvals gate
  and stamps every record CC-BY-4.0 with an object URL for attribution. Contributor
  identities are not in the corpus; maker and year are present as public registry
  fields but excluded from captions and training targets.
- **Human in the loop.** Every suggestion lands as **pending** and is confirmed by
  the maker or an admin before it touches the record — the same review path as any
  submission (GLASS-TRAITS §2, principle 2). Confidence is shown, model name and
  version are recorded alongside the suggestion, and corrections become training
  data.
- **Bias in the data.** The thesaurus is Venice-centric (v1) and early
  contributions skew to furnace work. Report per-discipline coverage with each
  model release so flameworkers and stained-glass makers can see when the model
  does not yet know their work.

## 6. Running it

**Hardware.** A 7B VLM at Q4 needs **≈ 6–8 GB of VRAM** (Qwen2.5-VL 7B Q4_K_M
plus its projector fits a 12 GB card comfortably). **CPU-only works** but expect
30–120 s per image on a laptop — fine for one-off suggestions, not for batch.
A used **RTX 3060 12 GB** (or any 12 GB+ card) is the studio-budget option;
a 24 GB card (3090/4090) also handles the Stage 2 LoRA training. Apple Silicon
with 16 GB+ unified memory runs the 7B comfortably through Ollama.

**Environment variables** (`.env`):

```
OLLAMA_URL=http://127.0.0.1:11434     # blank = suggestions off, fingerprint fallback only
GLASS_VISION_MODEL=qwen2.5vl:7b       # any Ollama vision model tag; later: glass-eye
```

**Commands:**

```bash
# install Ollama (ollama.com/download), then:
ollama pull qwen2.5vl:7b              # ~6 GB; qwen2.5vl:3b for small machines
ollama serve                          # usually already running as a service
curl http://127.0.0.1:11434/api/tags  # sanity check — vision.reachable() does the same

# in this repo
echo OLLAMA_URL=http://127.0.0.1:11434 >> .env
.venv/Scripts/python.exe -c "from glowtbook import vision; print(vision.models())"

# export the training corpus (approved objects only)
python -m scripts.export_vision_corpus --out data/vision_corpus --format both

# Stage 1: measure the k-NN probe (no Ollama needed) - table on stdout, JSON in data/vision_eval.json
python -m scripts.eval_vision_probe
python -m scripts.eval_vision_probe --corpus data/vision_corpus/corpus.jsonl --k 7 --threshold 0.4
```

**No model at all?** The Style tab's *Suggest from similar pieces* needs only
the registry: it works the moment some approved pieces carry AI-on fingerprints,
and it stays available next to the Ollama suggestions when both are configured.

**Hosted alternative.** The same adapter works against any OpenAI-compatible or
Ollama-compatible endpoint: a rented GPU box running Ollama behind a reverse
proxy (set `OLLAMA_URL=https://vision.yourhost`), or a managed inference
provider. Keep the image downscale (1024 px) and remember that sending images off
the machine is a privacy step the contributor should see — the local default
exists so a studio can run suggestions without any image leaving the room.

---

*Code: [`glowtbook/vision.py`](../glowtbook/vision.py),
[`central/vision_index.py`](../central/vision_index.py),
[`scripts/eval_vision_probe.py`](../scripts/eval_vision_probe.py),
[`scripts/export_vision_corpus.py`](../scripts/export_vision_corpus.py),
tests in `tests/test_vision.py` and `tests/test_vision_index.py`. Vocabularies:
[`central/techniques.py`](../central/techniques.py),
[`central/glass_traits.py`](../central/glass_traits.py).*
