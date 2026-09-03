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

- **Scale.** Each detected 30 mm marker's edge gives a local mm-per-pixel scale —
  no inter-marker geometry needed. The object is segmented against the mat's two
  known surfaces (white margin + mid-grey area) and measured; orbit frames give
  **W × D × H** in millimetres (±~1–2% in tests). Runs server-side on import
  (OpenCV). Clear glass under-measures — see §9.
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
3. Matching runs client-side. It **prefers DINOv2** when the reference carries
   embeddings (texture separates similar pieces); otherwise colour.
4. Output: a **confidence** (0–100) and a **verdict** —
   `match-likely` / `inconclusive` / `no-match`.

Expected behaviour, calibrated: same piece → high (≈90–100); a *different*
piece on the same mat → a non-zero floor (≈40–50, "inconclusive") because they
share the mat and, for colour, the palette. That floor is by design; DINOv2 is
what pushes a similar-but-different piece down.

## 8. Settings & where to test

| Setting | Where | What it does |
|---|---|---|
| **AI toggle** | enroll/verify app | turn DINOv2 on; samples `EMB_CAP` views then stops |
| `EMB_CAP` | enroll/verify app source | how many DINOv2 vectors to sample |
| **Match thresholds** (`SIM_LO/HI`, `MATCH_READY`) | verify app source | where confidence lands as match / inconclusive / no-match |
| **White balance** | enroll/verify app | on by default; anchors colour to the mat |
| **Marker size / mat geometry** | `central/matdetect.py` (`MARKER_MM`) | real-world scale |
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
