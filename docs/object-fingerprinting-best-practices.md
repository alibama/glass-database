# Non-contact object fingerprinting: methods, best practices, and storage

A working note for the glass-database / Glowtbook provenance stack. The goal: give a
physical object a **re-identification fingerprint** from a phone, robust enough to say
"this is the same piece" later, ideally without touching it. This document is
deliberately hard-nosed about what works, what doesn't, and why — and it lays out a
storage architecture that fixes the "127 MB for 71 photos" problem.

---

## 1. Frame the problem correctly

Two things are constantly conflated and must be kept apart:

- **Class recognition** ("this is a vase") — solved, easy, useless for provenance.
- **Instance re-identification** ("this is *that specific* vase, out of a million similar
  ones") — this is the whole game, and it's a different, harder problem.

And a second split:

- **Reconstruction** (build a 3D model / Gaussian splat) — what people reach for first.
- **Re-identification** (store a compact signature, compare later) — what provenance
  actually needs.

For transparent, refractive blown glass these distinctions matter enormously, because
**reconstruction is the wrong tool.** Photogrammetry and Gaussian splatting both assume
opaque, Lambertian-ish surfaces with stable, view-consistent features. Glass violates
every assumption: the "surface" you see through it shifts as the camera moves
(refraction), highlights slide across it (specularity), and there's little stable texture
to anchor. The transparency-depth ambiguity means the optimizer can fit appearance while
recovering wrong geometry. Re-identification sidesteps all of this: you never need correct
3D — you need a stable, discriminative descriptor.

**Design consequence #1:** don't store or depend on 3D. Store descriptors.

---

## 2. The science of image-based re-identification

### 2.1 Descriptor families (worst → best for this use case)

**(a) Perceptual hashes (dHash) — what the current tool uses.**
A dHash downsamples to ~9×8 luma and records the sign of horizontal gradients as 64 bits;
match by Hamming distance. Virtues: tiny (8 bytes), fast, no model, offline. Limits, and
they're serious: it encodes *coarse layout and tone*, not micro-texture. It is sensitive
to viewpoint and framing, and — critically for provenance — it does **not** separate the
object from its background or reliably distinguish two objects of similar silhouette. It
is a fine bootstrap and a good "novelty/near-duplicate" gate during capture. It is not a
forensic identity.

**(b) Hand-crafted local features (SIFT / ORB / AKAZE).**
Detect keypoints, describe local patches with (scale-, rotation-, partially
illumination-) invariant descriptors, match by nearest-neighbor + geometric verification
(RANSAC homography). This is the classical instance-recognition workhorse and is far more
discriminative than a hash. It degrades on low-texture and transparent surfaces (few
stable keypoints on clear glass) but shines on engraved marks, cold-working, labels, and
patterned or colored glass. Descriptor sets are larger (hundreds of keypoints × 32–128
bytes) but still small versus images.

**(c) Learned global embeddings (self-supervised ViT, i.e. DINOv2) — the modern answer.**
A self-supervised vision transformer maps an image to a fixed vector (e.g. 384-D) whose
cosine distance encodes semantic *and* fine textural similarity, learned with no labels.
This is what the current art-authentication startups actually use: MIRAS.ART generates a
384-dimensional visual fingerprint from two photos with a DINOv2 backbone, capturing
brushstroke texture, canvas weave, and craquelure, and reports that even a master copy
produces a completely different vector. Embeddings are robust to viewpoint, lighting, and
background in ways a hash never will be, and they attack the same-mould problem directly
because they key on micro-texture rather than outline. **This is the single highest-value
upgrade to the tool** (see §6).

**(d) Surface microstructure / optical PUF — the industrial gold standard.**
The most robust physical fingerprints exploit sub-visible surface randomness — the
微scopic texture, grain, and irregularities created incidentally during manufacture, which
are effectively unclonable. This is the basis of "physical unclonable functions": optical
PUFs were first demonstrated by imaging a plastic object's 3-D microstructure via laser
speckle, and paper can be fingerprinted from its fiber structure with nothing but a
commodity scanner. Commercially, Alitheon's FeaturePrint reads an object's inherent
micro-surface features from a single ordinary photo — no tags, no modification — and
reports >99.9% accuracy distinguishing even items manufactured to be identical; AlpVision's
Fingerprint does the same for molded, stamped, and rolled parts by comparing surface
structure to a stored reference. The catch: sub-visible features demand controlled capture
(consistent distance, focus, lighting) and often macro/close range. This is the ceiling
you're aiming at; embeddings on well-lit macro detail get you a usable fraction of it with
a phone.

### 2.2 The matching decision (and how to not fool yourself)

Matching is a detection problem with two error rates you trade against each other:

- **False accept (FAR / Pfa):** a different object is called the same. In provenance this
  is the expensive error — a forged or swapped piece passes.
- **False reject (FRR / Pm):** the genuine object fails to match (bad angle, lighting,
  wear).

Every threshold choice slides along an ROC curve between these. The microstructure
literature (the FAMOS industrial dataset) reports these as Pfa/Pm and shows the obvious but
important result: **you get the best separation when enrollment and verification use the
same camera and conditions.** That single fact should shape the whole capture protocol
(§3): the more you standardize capture, the cleaner the score gap, the more confidently you
can set a low-FAR threshold.

The current tool already does the right structural thing: it doesn't trust one frame. Its
confidence is *quality of the strongest matches × spread across distinct reference views*,
so a single lucky frame can't carry a verdict. Keep that. When you move to embeddings, the
aggregation stays identical — only the per-frame distance changes from Hamming to cosine.

**Calibration is not optional.** Thresholds (`SIM_LO/HI`, `STRONG_T`, `MATCH_READY`) must
be set from data, not vibes: enroll a real piece, then verify (i) the same piece and (ii) a
*deliberately similar* different piece, and move the thresholds until the same-piece score
lands high and the different-piece score stays low with margin. Do this per capture rig,
because the score distributions move with camera and lighting.

### 2.3 The genuinely hard cases (name them, plan for them)

- **Near-identical / same-mould pieces.** Silhouette and coarse layout are shared;
  identity lives in micro-texture (bubbles, seeds, pontil, cold-working, tool marks). A
  hash will confuse them; an embedding on *macro detail shots* is your defense. Always
  capture distinctive detail deliberately.
- **Transparency & specularity (glass).** Moving highlights get baked into any
  appearance-based descriptor. Mitigations are optical, not algorithmic: diffuse
  lighting, a dark surround to cut reflections, cross-polarization (§3.2), and a backlit
  pass so internal bubbles/inclusions become the signal.
- **Wear, aging, restoration.** Fingerprints drift. Good systems tolerate partial change
  (Alitheon markets robustness to damage and partial views); embeddings degrade
  gracefully, hashes do not. Store multiple views so partial change never zeroes you out.
- **Viewpoint & illumination.** The reason to prefer embeddings and/or local features over
  a hash. Also the reason to standardize capture geometry.

---

## 3. Capture best practices (actionable)

### 3.1 Turntable vs. moving the camera

Photogrammetry's core rule — the scene must be static; nothing should change in relative
position or lighting between frames — is why a **naive turntable is risky**: the object
rotates while the background stays fixed, and the solver can't tell which is "the world."
It only works if the object and turntable are evenly lit against a **featureless
background**, so the background simply drops out of feature matching.

For *fingerprinting* (no 3D solve) this matters less for correctness but a lot for
**repeatability**, which is what actually drives match quality (§2.2). Recommendation:

- **Turntable + blank seamless backdrop + fixed lighting + fixed camera.** This gives you
  identical distance, exposure, and framing on every capture — the single biggest lever on
  clean enroll↔verify separation. Rotate in even increments (e.g. 15°), at 2–3 heights.
- Moving the camera around a static piece is more forgiving of a busy background (the
  background *helps* a 3D solve) but is far less repeatable — better for one-off
  documentation than for a registry you'll re-match against.

For the "walk through a museum" case you can't bring a turntable, so lean on a **reference
target** (§3.3) and an **embedding** (viewpoint-robust) instead of geometric repeatability.

### 3.2 Lighting (the part everyone underestimates)

- **Diffuse, even light** for overall shape/color and stable descriptors.
- **Raking (low-angle) light** to reveal surface relief and tool marks — this is exactly
  the principle behind Reflectance Transformation Imaging (RTI), the museum-standard
  non-contact technique that has been used since the 1930s in the form of raking-light
  photography. A cheap RTI-style pass (fixed camera, a phone flashlight moved around the
  piece) records surface micro-relief that a flat photo misses.
- **Backlight** for glass: internal bubbles, seeds, and inclusions become the
  hardest-to-forge signal.
- **Cross-polarization** to kill specular glare: a linear polarizer over the light and a
  second over the lens, crossed ~90°, removes surface reflections and reveals true color
  and sub-surface detail. This is standard practice in art/coin documentation and is the
  most effective single trick for shiny/transparent objects. A phone clip-on polarizer is
  a few dollars.

### 3.3 The reference target — build the printed mat

Your instinct here is exactly right and it's established practice. A single printed sheet
the object sits on should carry three things:

1. **Fiducial markers (ArUco / CharUco board).** These give camera pose *and* metric scale,
   and remain usable even when the whole target isn't in frame — a decisive advantage over
   a plain checkerboard, which needs every corner visible. From known marker spacing you
   recover **real dimensions** of the piece.
2. **A color reference (ColorChecker / gray patch).** Enables white-balance and true,
   repeatable color across devices and lighting — turning "looks amber-ish" into a
   measured value in a defined color space. RTI/photogrammetry museum protocols already
   mandate a color card in the frame; this is the same discipline.
3. **A neutral matte field.** Because the mat's appearance is known, everything that *isn't*
   the mat is the object — giving you a cheap, model-free **background segmentation** and
   auto-crop.

One sheet therefore solves scale, color, background suppression, and repeatable geometry at
once. It's the highest-leverage physical artifact in the whole system, and it's a PDF.

### 3.4 Coverage

Sample the viewing hemisphere: azimuth around the piece at a few elevations, plus dedicated
**macro detail** shots of pontil, signature, cold-working, and a **backlit** pass. The
tool's live coverage ring already nudges azimuth spread; add explicit "detail" and
"backlight" passes to guarantee the identity-bearing shots exist.

---

## 4. Metadata you can extract (and what you can't)

With a calibrated capture (reference mat + backlit pass), from images alone:

- **Dimensions** — from the fiducial scale (height, width, rim diameter).
- **Color** — measured in a known space via the color chart; for glass, a rough
  **transmission/opacity** read by comparing a backlit frame against the chart.
- **Marks** — OCR/where-is of any signature, engraving, or label; store the crop.
- **Form descriptors** — silhouette/aspect ratios, once segmented against the known mat.

What a camera cannot give you: **weight, density, refractive index, exact composition** —
those need instruments (a scale, a refractometer, XRF). Record them separately when
available; don't pretend to infer them.

All of this belongs in the fingerprint record alongside the visual descriptors, and it
composes cleanly with the existing `glassdb.fingerprint` C2PA assertion.

---

## 5. Storage architecture — fixing "127 MB for 71 photos"

The 127 MB is almost entirely full-resolution JPEGs. That is the wrong thing to store,
because **the image is not the fingerprint — the descriptors are.** The fix is a tiered
model.

### 5.1 What actually needs to persist

| Tier | Content | Size (71 views) | Kept where |
|---|---|---|---|
| **T1 — Fingerprint** | per-view descriptors + aggregate + metadata | **~3 KB** (dHash) → ~30–100 KB (embeddings) | the registry; this *is* the identity |
| **T2 — Evidence** | small thumbnails / one reference-sheet montage | **~1.9 MB** (384 px, q60) | with the record, for human review + C2PA image |
| **T3 — Raw** | full-res frames, RTI stacks | 100 MB+ | **cold archive only**, if provenance demands originals (BagIt/MinIO — your existing AIP path) |

Measured on synthetic but representative data: the old path was ~127 MB (≈1.8 MB/frame,
full-res). A 384 px, quality-60 thumbnail is ~27 KB, so **71 thumbnails ≈ 1.87 MB** — a
~65× reduction — and the **descriptor-only fingerprint is ~2.8 KB**. The capture apps have
been changed to store the thumbnail, not the full-res photo; because descriptors are
computed from the 160×120 analysis buffer, matching is completely unaffected.

### 5.2 Descriptor sizes, and how small this can get

- **dHash:** 8 bytes/view → ~0.6 KB for 71 views.
- **DINOv2 float32 (384-D):** ~1.5 KB/view. Store one aggregate embedding + a handful of
  per-cell embeddings, not all 71: ~10–15 KB/object.
- **Quantized int8:** 384 bytes/view with negligible accuracy loss.
- **Product quantization (PQ):** ~32–64 bytes/view — museum-scale registries store
  millions of objects this way.

So a whole-object fingerprint is **a few KB regardless of how many frames you shot.** View
count should be driven by robustness, not storage.

### 5.3 Matching at scale

Cosine similarity over embeddings with an approximate-nearest-neighbor index (FAISS/HNSW)
searches millions of fingerprints in milliseconds. For a "1:N identify this piece" museum
registry that's the right substrate; for "1:1 is this the enrolled piece" it's overkill and
a linear scan is fine.

### 5.4 Per-object record (recommended)

```
object_id, created, capture_rig_id
descriptors: { algorithm, aggregate_embedding, per_cell[], dhash[] }
metadata:    { dimensions_mm, color_lab, marks[], opacity }
evidence:    reference_sheet.jpg (T2), thumbnails/ (T2)
provenance:  glassdb.fingerprint C2PA assertion (signed)
archive_ref: BagIt bag id (T3, optional)
```

---

## 6. The learned-embedding upgrade (do this next)

Concretely: add a **DINOv2 (or comparable self-supervised ViT) embedding per frame** and
make it the primary descriptor; keep dHash as the fast novelty gate during capture and as a
fallback.

- **Match** by cosine similarity; the existing quality×spread aggregation and verdict logic
  are unchanged.
- **Where it runs:** in-browser via `transformers.js` / ONNX Runtime Web (a small distilled
  ViT is a few hundred ms/frame on a modern phone), or server-side in the Gradio/registry
  path for heavier models. Either way the exported fingerprint carries the vector, so
  enrollment and verification interoperate.
- **What it fixes:** background sensitivity, viewpoint/illumination fragility, and the
  same-mould weak spot — the three things a hash can't solve. It's precisely the method the
  art-authentication field converged on.
- **Cost:** a model download (tens of MB, cached) and compute; storage stays tiny with
  quantization (§5.2).

Pair the embedding with the reference mat (§3.3) and cross-polarized/backlit capture (§3.2)
and you have, honestly, a credible non-contact instance-ID kit — phone plus a printed sheet
plus a clip-on polarizer.

---

## 7. The museum vision — feasible, with caveats

"Walk in and fingerprint many objects without touching them" is not speculative; it's the
intersection of two mature things: cultural-heritage imaging already does non-contact
documentation at scale (RTI needs only a camera, tripod, and a movable light, with no limit
on subject size or material), and the anti-counterfeit/art-auth industry already does
phone-photo instance identity. The honest gaps between the current tool and that vision are
engineering, not research:

1. **Descriptor** — dHash → embedding (§6). *Required.*
2. **Lighting control** — glass especially: polarizer + a backlit or raking pass; a small
   fold-flat copy stand or monopod for repeatable distance.
3. **Reference target** — the printed mat for scale/color/segmentation (§3.3).
4. **Logistics** — permission, and not moving objects; work with the piece in situ using a
   handheld rig and an embedding that tolerates viewpoint.

None of these are blockers. They're a bill of materials.

---

## 8. Where this lives in industry (so you know it's real)

You hadn't seen it because it's spread across three fields under three vocabularies:

- **Anti-counterfeiting / supply chain (surface fingerprinting, optical PUF).** Alitheon
  FeaturePrint and AlpVision Fingerprint (phone photo → item-level identity, no tags);
  the FAMOS microstructure dataset; laser-speckle optical PUFs; paper-fiber fingerprinting.
  Search terms: *optical PUF, surface fingerprinting, physical unclonable function,
  FAMOS.*
- **Art authentication / provenance.** MIRAS.ART (DINOv2 384-D fingerprint from front/back
  photos, blockchain-anchored, museum-standard export); craquelure-network fingerprinting
  with CNN embeddings; ArtDiscovery "Pictology." Search terms: *artwork fingerprint,
  craquelure authentication, brushstroke fingerprint.*
- **Cultural heritage imaging (non-contact documentation).** Cultural Heritage Imaging
  (CHI) and its RTI + photogrammetry standards, incl. the color-card + reflectance-sphere
  capture discipline and the handbook *Principles and Practices of Robust,
  Photography-based Digital Imaging Techniques for Museums.* Search terms: *RTI,
  reflectance transformation imaging, CHI photogrammetry.*

Your project sits precisely at the seam of these three and is unusually well-placed:
you already have the provenance/C2PA layer that the anti-counterfeit tools bolt on
afterward.

---

## 9. Recommended roadmap

1. **Done:** thumbnail storage (127 MB → ~2 MB), manual+auto capture, working
   enroll/verify, live matching.
2. **Next (highest leverage):** DINOv2 embedding as primary descriptor, cosine matching,
   quantized storage. Ship it in the capture apps and the registry path.
3. **Capture kit:** generate the ArUco + ColorChecker reference mat (PDF) and auto-detect
   it for scale/color/auto-crop; document the polarizer + backlight protocol.
4. **Metadata:** parse dimensions + calibrated color + mark crops from calibrated captures
   into the fingerprint record and the C2PA assertion.
5. **Scale:** HNSW/FAISS index for 1:N identification once the registry grows.
6. **Rigor:** a small labeled test set (same-piece pairs + hard negatives) to measure
   FAR/FRR and set thresholds honestly, per rig.

---

## References

- Alitheon FeaturePrint — https://alitheon.com/about-featureprint/
- AlpVision Fingerprint — https://alpvision.com/physical-product-protection-fingerprint/
- Physical object identification (FAMOS microstructure) — https://spie.org/news/5524-physical-object-identification-using-micro-structure-images
- Digital fingerprinting / anti-counterfeiting patent (feature extraction) — https://patents.google.com/patent/US20150117701
- Optical PUF / chip-surface authentication (survey) — https://arxiv.org/html/2412.15186v1
- Paper anti-counterfeiting via microstructure — https://www.researchgate.net/publication/352171679
- MIRAS.ART (DINOv2 artwork fingerprint) — https://miras.art/
- Craquelure-network fingerprinting (autoencoder/VGG19) — https://doi.org/10.3390/app15169014
- ArtDiscovery "Pictology" — https://artdiscovery.com/art-authentication
- Cultural Heritage Imaging — RTI — https://culturalheritageimaging.org/Technologies/RTI/
- RTI for cultural heritage (overview) — https://dh2016.adho.org/abstracts/113
- Photogrammetry turntable vs. static-scene discussion — https://forums.autodesk.com/t5/remake-forum/turn-table-and-fixed-camera/td-p/5822584
- ArUco/CharUco partial-frame calibration target (patent) — https://patents.google.com/patent/US10547833

---

## Appendix A — measured on real capture data (glass ornament, 68-frame enroll)

A field session (ornament enrolled on wood, verified on a couch) matched at 0%. Analyzing
the actual frames pinned the cause and the fix, quantitatively:

**Background is the entire failure.** Taking one real frame, keeping the object, and
swapping only the background (simulating wood → couch):

| descriptor region | dHash bits flipped (same object, bg swapped) |
|---|---|
| full frame (old behavior) | **21 / 64** — exceeds the no-match threshold by itself |
| central 75% crop | 17 / 64 |
| central 62% crop | **6 / 64** — a strong match |
| central 50% crop | 0 / 64 — background-invariant |

So the fingerprint was mostly encoding the surface the piece sat on. **Fix shipped:** the
dHash is now computed on the central 60% of the frame (`CROP=0.6`), and the apps draw a
dashed framing box — keep the piece inside it. This requires **re-enrolling** (the
descriptor definition changed), which is the right moment since standardized capture starts
now. The printed mat is the robust version of the same idea: everything that isn't the
known mat is the object, giving true segmentation rather than a fixed box.

**The object is genuinely matchable — dHash is just too weak.** ORB local features between
overlapping views of the same ornament: 47–648 geometrically consistent matches. Across a
half-turn (opposite faces): 0. Two lessons: (1) a real descriptor easily re-identifies the
piece, so the ceiling is high; (2) local features and hashes only match *overlapping*
views, which is why dense angular coverage matters and why a viewpoint-robust **embedding
(DINOv2)** is the next upgrade — it tolerates the wide-baseline gaps ORB and dHash cannot.

**Net:** center-crop + the mat should turn the couch-vs-wood 0% into real matches; the
embedding is what will make it robust across large viewpoint changes and near-identical
pieces.

---

## Appendix B — the dHash ceiling, and the color-signature fix (measured on two real ornaments)

A second session (on the printed mat) still matched at 0%. Analyzing both sessions
settled the question: **dHash is the wrong descriptor, full stop — not a tuning problem.**

- Within a single object's own enrollment, the nearest-neighbor dHash distance is median
  **~20/64** in *both* sessions (object at 40% and at 15% of frame). Adjacent frames run
  ~28 — essentially random (random ≈ 32). A hash captures 2-D layout, which changes
  completely as you orbit a 3-D object, so two views of the same piece are unrelated to it.
  No crop, mat, or threshold rescues this.
- Auto-cropping to "detail" made it worse, because the highest-contrast things in frame are
  the **ArUco markers** — the detail box locks onto them. ORB matching was likewise
  dominated by the constant markers (762 "matches" on one pair, 4 on the next). Lesson:
  for a small object, the markers must be out of the fingerprint frame; use a separate wide
  shot for scale/color.

**The fix: a color signature.** An HSV hue-saturation histogram of the object region is
*view-stable* — a piece keeps its palette from every angle — which is exactly the property
dHash lacks. Measured on the two real ornaments (red/white vs blue):

| | correlation |
|---|---|
| within same piece (best view) | **0.96–0.99** |
| within same piece (mean) | 0.69–0.76 |
| between the two different pieces | **0.02** |

Run through the full verify aggregate, the **shipped** JS gives: same piece → **99,
"Match likely"**; different pieces → 46, "Inconclusive" — where dHash gave 0 for the same
piece. **Shipped:** enroll now stores a 64-bin color histogram per frame; verify matches on
color correlation (dHash stays only as a novelty/coverage gate). Re-enroll to pick up the
new descriptor.

**Honest limits of color, and the next step.** Color re-identifies *color-distinct* pieces
well and needs no model, so it unblocks real testing now. It will **not** separate two
similarly-colored pieces (two blue ornaments) — for that you need fine texture, i.e. the
**DINOv2 embedding**, which subsumes both color and micro-texture. Color is the right
interim; the embedding is the destination. Capture guidance that still matters regardless:
**fill the frame with the object** (a separate wide shot carries the mat for scale/color).
