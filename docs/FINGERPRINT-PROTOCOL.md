People see this as **Glass ID** (see docs/NAMING.md); the code and URLs keep the old words.

# Glass Object Fingerprinting Protocol

*A protocol for re-identifying a physical glass object from photographs, and
binding that identity to tamper-evident provenance. Written for developers
integrating with the registry and for archivists assessing what is captured,
stored, and claimed.*

Status: working protocol, v1. Nothing here is a forensic guarantee — it is
decision support with explicit, tunable thresholds (see §7–§9).

---

## 1. What it does

An **enrolment** turns many photos of one physical piece, taken from different
angles on a calibrated mat, into a compact **fingerprint** — a set of
view-stable descriptors plus real-world measurements. A later **verification**
re-photographs a physical piece and matches it against a registered fingerprint,
returning a confidence and a verdict. The fingerprint is signed into the object's
**C2PA Content Credentials**, so the claim "this fingerprint was enrolled by X on
date D" is tamper-evident.

Two design commitments:
- **Descriptors, not images, are the identity.** The registry stores the
  descriptors; the source photos live in the archival package (AIP), not the
  public record.
- **Matching runs in the browser.** Verification loads the reference and matches
  on-device; the verifier's capture never leaves their machine.

## 2. Descriptor tiers

Each captured frame yields up to three descriptors, used for different jobs:

| Descriptor | Dim | Role | Cost |
|---|---|---|---|
| **Colour histogram** (HSV, hue×sat) | 64 | Primary match signal — view-stable across angles | cheap, every frame |
| **dHash** (difference hash) | 64-bit | Novelty/coverage gate — decides a frame is a *new* view; weak match signal | cheap, every frame |
| **DINOv2 embedding** (mean-pooled) | 384 | Texture/identity — separates *similar-coloured* pieces (bubbles, pontil, tool marks) | expensive, **sampled** (§6) |

Rationale: colour re-identifies colour-distinct pieces robustly but **cannot
separate two similarly-coloured pieces** — that is DINOv2's job. dHash on its own
keys on layout/background and is unreliable for identity, so it is used only to
gate coverage, not to decide a match.

### 2.1 Colour is white-balanced first
Before the histogram, each analysis frame is **white-balanced against the mat**
(the brightest pixels — the mat's white margin — are taken as the illuminant and
neutralised). This makes the colour descriptor lighting- and device-invariant:
the same piece under warm vs. cool light converges (≈19°→≈2° hue in tests).

### 2.2 Descriptors are computed on a small analysis buffer
Colour/dHash are computed on a 160×120 buffer, centre-cropped to the object so
the mat/background does not dominate. The stored thumbnail (≤384 px) is for
display only, not for matching.

## 3. Capture protocol

1. Print the **capture mat** at 100% (US Letter). It carries four ArUco markers
   (`DICT_4X4_50`, ids 0–3, 30 mm), a neutral placement area, and a colour strip.
2. Fill the grey area with the piece; keep **all four markers in frame**.
3. Even light, dim surroundings. A **backlit pass** and a **clip-on polarizer**
   help transparent glass (see §9).
4. Orbit slowly at 2–3 heights until the strength reads **Strong**. Grab detail
   shots of hard-to-forge features (pontil, internal bubbles).

Coverage is driven by **visual novelty**: a frame is kept only when its dHash is
sufficiently different from what's already captured.

## 4. Data model (`fingerprint.json`)

```jsonc
{
  "tool": "…", "version": 1, "created": "ISO-8601",
  "rating": 0-100, "tier": "Weak|Fair|Good|Strong",
  "metadata": {
    "dominantColor": {"name": "amber", "hex": "#c60"},
    "hasEmbeddings": true,
    "dimensions_mm": {"width": 41, "depth": 26, "height": 92}   // if shot on the mat
  },
  "frames": [
    { "file": "frames/000.jpg", "sector": 0, "cell": "…",
      "dhash": [hi, lo], "chist": [64 floats],
      "emb": [384 floats]        // present on a CAPPED subset of frames only
    }
  ]
}
```

Only descriptors are stored in the registry (`file` is a pointer to the AIP
thumbnail). Contact/value data is never part of the fingerprint.

## 5. Calibration & measurement (the mat)

- **Size (`glowtbook/matdetect.py`, server-side on import, OpenCV).** Rewritten
  November 2026. The old version measured the box around anything that wasn't
  exactly paper-white (245) or grey (128) in each picture. Under ordinary room
  light a phone records paper at about 190–220, so it measured the mat itself:
  about 187 × 158 mm, and up to 250 mm for tilted or turned shots, whatever the
  piece was. The new pipeline works in mat coordinates:
  1. **Pose.** The marker corners (2 or more of the 4) give the homography from
     the mat plane to the picture. From the session's tilted views it gets the
     focal length, then each camera's foot point G and height Hc above the mat.
  2. **Flatten** each picture onto a top-down grid of the plane, 1 px/mm,
     reaching 250 mm past the page: the table is part of the same plane.
  3. **Segment.** On the page, against the printed mat (`mat_template.png`),
     after fitting the frame's exposure and colour cast as a per-channel gain
     with a gentle gradient. That gain is anchored on the paper next to the
     markers that were read, so a big piece can't throw the fit. Off the page,
     against the table itself: the median of each plane pixel across the
     session, with that pixel's own normal variation. For big pieces this is
     repeated, leaving out the frames where the piece stands in front of that
     patch. Soft shadows count as background.
  4. **Carve a visual hull.** A point at height z above plane point X lands on
     the plane at G + (X − G)·Hc/(Hc − z). It is kept only if every frame that
     sees where it lands, including at least one side view, shows piece there.
     Each column is one solid run, which allows for the bulge of an ornament.
     **W × D** = the minimum-area rectangle of the columns that hold piece;
     **H** = the top of what survives.

  On rendered test sessions (`tests/test_matdetect.py`: 384 px thumbnails, room
  light, a shadow, orbits at 80°/50°/30°), most cases come out within ~4 mm.
  Width runs a few mm over and height runs high on straight-sided pieces:

  | Rendered piece | Measured |
  |---|---|
  | 80 mm ball | 82 × 81 × 79 mm |
  | 40 × 90 mm cylinder | 44 × 43 × 92 mm |
  | 40 × 25 × 90 mm box | 47 × 29 × 91 mm |
  | 120 mm ball, shot close | 116 × 116 mm, height not measured (no side view saw past its top) |
  | 60 × 140 mm vase | 68 × 64 × 165 mm |

  The cause is in the geometry: a hull can only be bigger than the piece. A flat
  top seen only from steep angles leaves a cone-shaped cap, because low views
  see the top against the room, not the mat or table. Results therefore carry
  `height_range_mm` (how much lower a flat top could be). **Height is `None`**
  when every picture looked straight down, or when no side view could see past
  the top. For a good height, circle the piece once low (about 30°) with the
  whole mat and some table in frame.

  These numbers are from rendered pictures. They have **not been calibrated on
  real captures yet**: compare against a ruler on a few pieces before relying on
  them for the biggest-pumpkin ranking. Clear glass under-measures (§9).
- **Colour.** Client-side white balance (§2.1) anchors to the mat's neutral. The
  printed colour strip is the seam for a future full colour transform (true
  Lab/hex); not yet wired.

## 6. DINOv2 is sampled, not exhaustive

DINOv2 is expensive and its raw per-patch output is large. So:
- The embedder runs on a **capped number of representative views**
  (`EMB_CAP`, default 8), then stops — colour/dHash continue on every frame.
- Each embedding is **mean-pooled to 384 dims and quantised** (4 dp).

Effect: embeddings add ~20 KB to a fingerprint (vs. tens of MB unpooled), and
capture speed returns to normal after the cap. It runs **in-browser** via
transformers.js (`Xenova/dinov2-small`); the model downloads once and caches.

## 7. Verification

1. `verify.html?object=<id>` loads the reference from
   `GET /api/objects/<id>/fingerprint`.
2. The verifier captures live; descriptors are computed the same way (white
   balance, cap on DINOv2).
3. Matching runs client-side with the kit (`glassid-kit.js`), the same arithmetic the server
   runs for lookups (`central/glassid.py`). The descriptor is the first that both sides carry
   on at least three shots, in this order: AI texture of the piece (`emb_p`), AI texture of the
   frame (`emb`), colour of the piece (`chist_p`), colour of the frame (`chist`).
4. **Rules v2** (since 17 November 2026): each registered view pairs with at most one shot,
   strongest pairs first, at 0.50 or better; with the Photo Mat in view on both sides a pair
   must also be within 25 degrees of elevation and 45 of azimuth after the best of twelve
   30-degree turns of the piece. The **confidence** is the mean similarity of the shots' own
   views, a shot with none counting 0: how much of the capture the registration explains.
5. Output: a confidence (0-100) and a verdict - `match-likely` (70 or more, 3 views),
   `no-match` (under 40, fewer than 2 views, 8 sharp shots), `not enough to tell` (fewer than
   3 shots carry the descriptor, or colour reads on under half the shots), otherwise
   `inconclusive`.

What the numbers mean is measured, not assumed: docs/GLASS-ID-MATCHING-ASSESSMENT.md section 9
has the live registry under both rule sets. Rules v1 (the 8 best per-shot matches x spread
over distinct views) is kept as `rules="v1"` so earlier numbers can be reproduced.

### 7a. The Photo Mat on the phone

`glassid-mat.js` reads the mat in the live picture (js-aruco2, ARUCO_4X4_1000 = OpenCV's
DICT_4X4_50 ids 0-3; geometry from `glowtbook/matdetect.py`): a least-squares homography of
the marker corners, the focal length from the session's tilted views (Zhang's constraints,
median), the camera's position, and from it the azimuth round the grey placement area and the
elevation above the mat. Gates: two markers, reprojection under 3.5 px, marker sides of 14 px,
elevation of 12 degrees or more; overhead views (80 degrees and up) are one cell. Each shot
records `mat {az, el, n, rms, h, d, cc}`; its view cell is `m<sector of 30 degrees>:<band>`.
The **crop** is the picture region above the grey area up to 150 mm (on Check a Piece, the
registered height x 1.2 + 10 mm). The **colour reference** is the strip's five greys: per
channel, a gain and offset in linear light fitted to their nominal values, clipped patches
left out; refused when the greys don't read in order or have too little range. A frame
without its own strip in view uses the session's last good one (`cc: "session"`). Printed
patches are not measured reflectances, so this corrects white balance and exposure, not
colour fidelity across printers.

## 8. Settings & where to test

| Setting | Where | What it does |
|---|---|---|
| **AI toggle** | enroll/verify app | turn DINOv2 on; samples `EMB_CAP` views then stops |
| `EMB_CAP` | enroll/verify app source | how many DINOv2 vectors to sample |
| **Match thresholds** (`SIM_LO/HI`, `MATCH_READY`) | verify app source | where confidence lands as match / inconclusive / no-match |
| **White balance** | enroll/verify app | on by default; anchors colour to the mat |
| **Marker size / mat geometry** | `glowtbook/matdetect.py` (`MARKER_MM`, `MARKER_CORNERS_MM`, `mat_template.png`) | real-world scale, page layout |
| **C2PA certificate** | `data/c2pa/{cert,key}.pem` | self-signed (untrusted) vs. Trust-List (trusted) |

**Calibrate before trusting a verdict.** Enrol a real piece, then verify (i) the
same piece and (ii) a deliberately-similar different piece; move the thresholds
until same-piece lands high with margin over different-piece. A labelled set of
same-piece pairs + hard negatives (same-mould, same-colour) lets you measure
false-accept / false-reject and set the threshold from data, not guesswork.

## 9. Best practices & limitations

- **Clear / colourless glass is the hard case.** Colour barely separates it and
  it under-measures on the mat. Use a **backlit pass** (internal bubbles/striae
  become the signal), a **polarizer** (stress birefringence), and rely on DINOv2
  + distinctive detail shots.
- **Fill the frame with the piece** so the mat isn't in the colour descriptor;
  keep the markers in frame for scale.
- **Re-enrol after a descriptor change.** Colour white-balance and the pooled
  embedding changed the descriptor definition; old fingerprints won't line up
  with new captures.
- **Not forensic.** Treat the verdict as decision support. The C2PA credential
  proves *integrity and origin of the record*, not that the physical match is
  certain.

## 10. Provenance binding (C2PA)
## 10a. Binding the multi-view fingerprint to C2PA

A fingerprint is computed from *many* frames. Rather than sign each frame, the whole fingerprint (every frame's descriptors, in one canonical JSON) is hashed with **SHA-256**, and that hash is what's signed into the object's Content Credentials as `fingerprint_sha256` inside the `glassdb.fingerprint` assertion. So the credential commits to the entire multi-image aggregate: change any one frame's contribution and the hash — and therefore the match — breaks.

**Verification** (`/api/objects/<id>/fingerprint/verify`, and shown in verify.html) recomputes the hash over the fingerprint the registry serves and compares it to the signed value, returning `bound: true/false` plus the signer and validation state. This proves the served fingerprint is the one that was signed — not that each source frame was independently signed (the frames live in the AIP, not the public record).


On contribution the fingerprint is written into the object's manifest and a
**compact attestation** is signed into the C2PA credential: `rating`, `tier`,
`dominant_color`, `views`, `algorithm`, and a **`fingerprint_sha256`** over the
canonical fingerprint. The credential stays lean (the bulky per-frame vectors are
served from the registry, not embedded), and the hash binds "this exact
fingerprint, enrolled by this signer, at this time." See
[`PROVENANCE-ARCHITECTURE.md`](PROVENANCE-ARCHITECTURE.md) for the C2PA/OAIS
framing. Self-signed certs read as *untrusted* until a C2PA Trust-List cert is
installed.

---

*Diagram: [`fingerprint-protocol.svg`](fingerprint-protocol.svg).*
