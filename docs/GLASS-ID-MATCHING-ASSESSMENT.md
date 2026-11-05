People see this as **Glass ID** (see docs/NAMING.md); the code and URLs keep the old words.

# Glass ID matching — an assessment

*Written 5 November 2026, for the question "is one ornament different from another, and
can the app tell?" Companion to [FINGERPRINT-PROTOCOL.md](FINGERPRINT-PROTOCOL.md) (what
is captured and how it is scored) and [FINGERPRINT-WORKFLOW.md](FINGERPRINT-WORKFLOW.md)
(the process). The grants plan assumes a Glass ID calibration on a museum shelf; this
page says what that calibration can stand on today, and what it still needs.*

## 1. The short answer

**The question is right; the evidence is thin.** The shape of the approach is sound and
worth keeping: many views of a piece, a descriptor per view, a confidence that is *quality
of the strongest matches × spread across distinct reference views*, explicit thresholds,
and a person who decides. What is not yet established is whether the descriptors the app
computes can separate pieces that look alike, and no threshold in the app has been set
from real captures. Specifically:

- **Colour** (the default descriptor) separates palettes, not pieces. It is view-stable and
  it works on colour-distinct ornaments, which is why it shipped. It cannot tell two pieces
  from one batch of rods apart, by construction, and it reads pale, clear and dark
  transparent pieces as their background (measured today, §3).
- **The shape hash (dHash)** is not an identity signal for a 3-D object. Two views of the
  same piece are as unrelated as two different pieces. It stays as a novelty gate for
  coverage, nothing more.
- **The AI texture vector (DINOv2, pooled, up to eight views)** is the only descriptor with
  a chance at siblings, and it has **zero** real measurements behind it: not one same-piece
  pair, not one different-piece pair, on real glass.
- **Nothing has been measured on siblings** (same maker, same rods, same day), which is the
  case the museum shelf and the collecting events actually pose.

So: precision on the easy wins is good where it has been looked at, recall is middling,
the hard cases are unmeasured, and the app's "Match likely" should be read today as *same
palette, views consistent*, not *same object*. The way out is data, and this round built
the pipe for it (§6).

## 2. What the app decides with today

The rules live in `deploy/fingerprint-web/verify.html` and are mirrored, number for number,
in `central/glassid.py` (the server) and `deploy/fingerprint-web/glassid-kit.js`
(`tests/test_glassid.py` fails if they drift).

| Setting | Value | What it does |
|---|---|---|
| Descriptor | colour; AI when the reference carries embeddings | per-shot similarity: Pearson correlation of 64-bin HSV histograms, or cosine of pooled DINOv2 vectors |
| `STRONG_T` | 0.50 | a shot/reference pair at or above this counts as *matched* |
| quality | mean of the 8 strongest matched pairs | the first factor of the confidence |
| `SPREAD_TARGET` | 6 distinct reference views | spread = views matched / 6, capped at 1; confidence = 100 × quality × (0.4 + 0.6 × spread) |
| `MATCH_READY` | 70, **and** ≥ 3 reference views matched | "Match likely" |
| `NO_MATCH` | 40, **and** < 2 views matched, **and** ≥ 8 sharp shots (`MIN_EFFORT`) | "No match here" |
| otherwise | | "Inconclusive" |
| `SIM_LO` / `SIM_HI` | 6 / 22 bits | the dHash ramp (shape mode only) |
| `EMB_CAP` | 8 | AI vectors sampled per registration, then it stops |
| Enrolment `READY` | 75/100 | when the capture may be saved without a warning; the rating measures coverage, detail and robustness, **not distinctiveness** |
| Style suggestions (`central/vision_index.py`) | k = 5, no threshold | nearest public pieces by centroid cosine; for traits, never for identity |

Added this round, server-side only and advisory: `SIZE_TOLERANCE` 0.25 (mat-measured
height or footprint more than 25 % apart holds a picture match at *inconclusive*) and
`AMBIGUOUS_MARGIN` 10 (two candidates closer than ten points are reported as not
separated). Neither moves a threshold; both add a reason the person can read.

Two consequences of the rules themselves, before any data:

1. **"No match" cannot fire for a same-palette piece.** It needs fewer than two matched
   views; two pieces of one colour will always share a few views at 0.5 correlation. So
   a different piece of the same colour lands in *Inconclusive* for ever, and the person
   reads "maybe". The protocol admits this as the "non-zero floor (≈ 40–50)"; it is a
   floor on the *wrong* side of the decision for the sibling case.
2. **The best-of-N maximum is optimistic.** Every shot takes its best match over *all*
   reference views (sixty in a strong registration), and the eight best of those maxima
   make the quality term. The maximum of sixty noisy correlations is biased upward for any
   piece of a similar palette, and the bias grows with the size of the reference. A
   stronger registration therefore makes false accepts *more* likely under colour, not
   less.

## 3. What the evidence is, and is not

**Two real ornaments, October (best-practices Appendix B).** Red/white versus blue, on
the printed mat, colour descriptor: within one piece the best-view correlation was
0.96–0.99 and the mean 0.69–0.76; between the two pieces 0.02. Through the verify
aggregate: same piece **99, Match likely**; different pieces **46, Inconclusive**. That is
the data the shipped thresholds rest on: one positive pair, one negative pair, both
colour-distinct.

**Four clearly different cubes, today, staged.** The photo of the four cubes (blue-green
with a green stem, amber, orange with black swirls, white with black spots) was cropped
into ten framed views per cube, each cube's views split into two sessions, and every
session scored against every other with the page's own rules
(`python -m scripts.glassid_bench --images`; the crops are staged from one photograph,
so same-piece pairs share light, lens and moment and are optimistic). Colour descriptor,
confidence out of 100, `*` marks the same piece:

```
query \ reference   amber#1  amber#2  bluegreen#1  bluegreen#2  orange#1  orange#2  white#1  white#2
amber#1                 ·      60*         0            0           0         0        49       60
amber#2               60*       ·          0            0           0         0        50       50
bluegreen#1            0        0          ·           79*          0         0         0        0
bluegreen#2            0        0         79*           ·           0         0         0        0
orange#1               0        0          0            0           ·        80*        0        0
orange#2               0        0          0            0          60*        ·         0        0
white#1               49       49          0            0           0         0         ·       70*
white#2               59       59          0            0           0         0        60*       ·
```

- At the shipped thresholds: **no different pair reached "Match likely"** (false-accept
  rate 0 of 48), but **no different pair reached "No match here" either** (48 of 48
  *Inconclusive*), and **only 4 of 8 same-piece pairs reached "Match likely"** (recall
  0.5; the other four sat at 60). The lowest same-piece score (60) equals the highest
  different-piece score (60, amber against white): **separation zero**. The best single
  cut on these pairs is 60, which still admits one different pair in fifty.
- **Why amber and white confuse each other.** The histogram keeps only saturated, mid-lit
  pixels (s > 0.24, 0.16 < v < 0.96). In the white cube's framed views that keeps **6 %**
  of the central pixels, and what it keeps is the reddish-brown table around the cube;
  the amber cube keeps 75 % but reads "red" too, for the same reason. The white cube's
  dominant colour comes out as *red*. A pale, clear or dark transparent piece is described
  by its surroundings, not by itself; the mat-anchored white balance cannot help, because
  with the piece filling the frame the mat is not in it.
- **dHash**: zero on every pair but one (orange, 44). It is not an identity descriptor.

**The AI descriptor**: not measured. The live registry holds three demo pieces with one
synthetic frame each and no embeddings; the kNN index was checked on synthetic clusters
only (the earlier session's note: "4 reticello goblets at 0.99, one window panel at 0.00"
on made-up vectors). Nothing can be concluded from it until real pieces are registered
with AI on.

**Cross-session, cross-device same-piece captures**: none. Every positive pair so far was
one session scored against itself or against crops of one photograph. The variation that
actually causes false rejects (another day, another phone, another light) has not been
seen.

**Siblings**: none.

## 4. Precision and recall, as far as they can be stated

| Case | Descriptor | Precision of "Match likely" | Recall | Basis |
|---|---|---|---|---|
| Four clearly different pieces | colour | 1.0 (0 false accepts in 48) | 0.5 (4 of 8) | staged views from one photo, today |
| Two colour-distinct ornaments | colour | 1.0 (1 pair) | 1.0 (1 pair) | October, real captures |
| Same-palette different pieces | colour | **expected to fail**: held at *Inconclusive*, never *No match*; a "Match likely" is possible when the palette and framing agree | — | the rules in §2; no data |
| Siblings from one batch | AI (pooled DINOv2) | **unknown** | **unknown** | no data |
| Clear or pale pieces | any | **expected poor**: 6 % of pixels kept; described by the background | — | today's white cube |

The easy wins are safe from a false *Match likely* on colour, with one caveat: they are
not safe from a false *Inconclusive*, and the app offers "Inconclusive" as an invitation to
keep going rather than as a negative. On a phone that reads as "it might be". The hard
cases have no numbers because there are no captures.

### Frit ornaments: very similar, and each one unique

A run of ornaments rolled in the same frit is the sharpest form of the sibling question:
same colours, same form, same size, same maker, and yet every piece carries a random
arrangement of grains that no other piece has. How likely is the app to tell them apart?

- **With colour: no.** The histogram is the same for every piece in the run, by
  construction. Every sibling pair lands in *Inconclusive*, and a pair framed alike can
  reach *Match likely*.
- **With the pooled AI vector: unlikely, and unmeasured.** The vector is the *mean* of
  DINOv2's patch features over a 384 px thumbnail of the whole frame. Averaging keeps
  what the pieces share (the frit colours, their density, the form) and discards *where*
  the grains are, which is the only thing that differs. Expect sibling pairs and
  same-piece pairs to overlap in cosine similarity; some separation when the frit
  proportions differ visibly, none when they do not.
- **With the right descriptor: likely, and frit is the favourable case.** A random,
  dense, high-contrast pattern is what local-feature matching is best at: detect
  keypoints on the grains in a close shot, match them to a registration shot from a
  similar angle, and require the matches to agree geometrically (a RANSAC-verified
  homography or fundamental matrix). The October measurement already showed the ceiling
  — ORB found 47–648 geometrically consistent matches between overlapping views of one
  ornament and 0 across a half-turn — and the microstructure re-identification literature
  the best-practices page cites is this method. Two siblings share no grain constellation,
  so verified matches drop to the noise floor; the same piece re-photographed keeps them.

Three things have to be true for that to work, and the capture protocol can provide all
of them: **overlapping views** (dense angular coverage, which the rating already rewards;
the mat's ArUco pose could align views properly instead of by compass), **resolution**
(the grains must be resolved: the 384 px thumbnails stored today are too small, the
1600 px best shot is borderline; the matcher wants full-resolution detail shots, or
features extracted on the phone at full resolution), and **frit at or near the surface**
(grains deep inside clear casing shift with the viewing angle through refraction;
surface frit, and frit under a thin skin, are stable). Moving highlights are handled by
the geometric check, not by the descriptor.

So the honest likelihood today is low, for a reason that is fixable: not the physics,
the descriptor. The first frit sessions sent for analysis are the data to prove it — the
bench can score them with a local-feature matcher on the stored pictures before anything
changes in the app — and if the result is what the October numbers suggest, frit
ornaments become one of the *easier* cases rather than the hardest.

## 5. What the Photo Mat contributes

| Signal | Today | Could |
|---|---|---|
| **Scale** | measured server-side on registration from the ArUco markers (±1–2 % on synthetic frames; clear glass under-measures); shown on the receipt; **not used in matching** until this round's 25 % size gate | a hard gate at the real accuracy once measured on real pieces; W/D are ambiguous from an orbit, height is the reliable axis |
| **White balance** | by proxy: the brightest 15 % of pixels of each analysis frame, which is the piece itself when the frame is filled | one wide *mat shot* per session, white-balanced from the margin and, later, colour-corrected from the printed swatch strip (the strip is still unused), applied to every close shot |
| **Segmentation** | the server knows the mat's two surfaces and finds the piece for measuring; the descriptors ignore it | compute colour and texture on the piece's pixels only, which removes the background problem of §3 outright for pieces photographed on the mat |
| **Pose** | the four markers give a camera pose per frame; unused — "views" are compass sectors, or novelty pseudo-cells without a compass | true view cells, so *spread* means angular coverage and two registrations of one piece align view by view |

The mat is the instrument that makes the hard cases measurable; today it only reports a
size.

## 6. What this round added, and what it did not change

- **Lookup and compare in both capture pages**: find a registered piece by Glass ID, title
  or maker, by recent sessions, or by photo; the server scores a capture against every
  registered piece at once with the page's own rules (`POST /api/glassid/candidates`) and
  returns each candidate's confidence, verdict, per-descriptor breakdown, the shots and
  reference views that carried the score, the size check and the reasons in words. The
  compare panel puts the registered photos beside the new shots; *Same piece* and
  *Different piece* record the person's call. Register Your Piece runs the check before a
  piece gets a new Glass ID and asks before registering a look-alike.
- **Send for analysis**: a session manifest (photos, descriptors, candidates, decision,
  mat detection, device and app version) goes to the server; administrators see them on
  `/captures.html`; `python -m scripts.pull_capture` pulls them down;
  `python -m scripts.glassid_bench --manifests` scores every session against every other
  and reports the numbers of §3 for real captures.
- **Not changed**: the thresholds. There is not enough data to move them, and moving them
  on staged crops would be the guesswork the protocol warns against.

## 7. The data still needed, concretely

The bench needs same-piece pairs that differ in session, and different-piece pairs that
share a palette. In order of value:

1. **The four cubes, properly.** For each cube: register it on the mat with **AI on**
   (phone A); then *Check a Piece* against each of the four (four sessions per cube,
   sixteen in all), sending every session. Then repeat the registration another day,
   with the piece off the mat, and if possible with a second phone. That yields the first
   real same-piece pairs under colour **and** AI, across sessions, and the first
   different-piece pairs with AI on. The white cube is the pale-piece test; the amber cube
   is the dark-transparent test.
2. **Siblings.** Three or more pieces from one batch (same rods, same day, same form), two
   sessions each. This is the museum-shelf question and the only data that can answer it.
   A **frit run** (§4) is the best sibling set of all: send close, sharp detail shots of
   the grain pattern with each session, because those are what a local-feature matcher
   would be scored on.
3. **Clear glass.** Two clear pieces, backlit pass included.
4. **Volume.** Thirty same-piece pairs and two hundred different-piece pairs before any
   threshold is trusted; the bench prints the separation and the best cut as they come in.

The decision rule to evaluate on that data, in this order: top candidate above the cut
**and** a margin of at least ten over the second **and** a mat size within tolerance;
colour as a pre-filter, AI texture (cropped to the piece) as the identity signal.

## 8. Recommendation

Keep the skeleton. Treat colour as a filter, not a verdict. Make the AI descriptor the
identity signal and compute it on the piece, not the frame. Add the margin and size rules
to the page once the bench shows what they buy. Until the sessions of §7 exist, present
"Match likely" as *strong evidence of the same palette and consistent views*, keep
"Inconclusive" honest in the app's words, and do not promise the museum calibration a
sibling test the data cannot yet support. The pipe for that data is live; the four cubes
are the first thing to send through it.

---

*Code: [`central/glassid.py`](../central/glassid.py), [`central/captures.py`](../central/captures.py),
[`api/captures.py`](../api/captures.py), [`deploy/fingerprint-web/glassid-kit.js`](../deploy/fingerprint-web/glassid-kit.js),
[`scripts/glassid_bench.py`](../scripts/glassid_bench.py), [`scripts/pull_capture.py`](../scripts/pull_capture.py);
tests in `tests/test_glassid.py` and `tests/test_captures.py`.*
