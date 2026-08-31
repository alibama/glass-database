# Provenance & archive architecture

Glassdatabase as a proof-of-concept across two axes: **craft + capital** (a
maker/object registry with value, insurance and history) and **library +
archive** (OAIS-shaped preservation with verifiable provenance). This is the map.

## The OAIS spine (what's built)

Every object contribution already follows the OAIS packaging model:

```
  SIP  (what the maker enters)             AIP  (kept local, full fidelity)
  ────────────────────────────             ─────────────────────────────────
  object metadata, images/video,           originals on the maker's machine:
  provenance events, private value    ──▶  data/glowtbook_media/<user>/aip/<obj>/
                                           + full record incl. value/insurance
                                                        │
                                                        │  condense on contribute
                                                        ▼
                                           DIP  (what the public gets)
                                           ─────────────────────────────────
                                           downscaled JPEGs (≤1200px, EXIF
                                           stripped) + a provenance MANIFEST,
                                           value only if the maker opts in
```

- **AIP** = the archival copy: full-resolution originals and private values never
  leave the maker's machine. (Production path: back these with your
  Kopia/BagIt/MinIO stack — see below.)
- **DIP** = the "condensed / compromised version" you asked for: what's published
  to the central registry and rendered in Explore → Objects.
- **Per-user isolation**: signed-in users key every record and every media
  directory to their identity (`st.user`), so "let users contribute" is safe.

## The provenance manifest is C2PA-shaped, and now C2PA-signed

`glowtbook/media.py` emits a manifest per object with C2PA's vocabulary:

```jsonc
{
  "assertions": [
    {"label": "glassdb.object.metadata",    "data": { … }},
    {"label": "glassdb.provenance.events",  "data": [ … ]},
    {"label": "glassdb.technique.ontology", "data": [{"label":"…","gbo":"…#CaneWork"}]}
  ],
  "ingredients": [{"title":"front.jpg","hash":"<sha256>","role":"primary"}],
  "signature": "c2pa:es256 (self-signed test cert)"   // ← now embedded
}
```

- **Built**: assertions, ingredients (images by SHA-256), ontology-linked
  techniques, a content hash — and real **C2PA Content Credentials**
  (`glowtbook/c2pa_sign.py`). On contribution each condensed DIP JPEG is signed
  (ES256) with a manifest carrying a `stds.schema-org.CreativeWork` author and
  our `glassdb.provenance` assertion, so the credential travels *with* the file.
  Explore reads it back and shows a verify panel.
- **Manifest shape (spec-correct)**: each signed image is built with the C2PA
  **edit intent** and records the original as a `parentOf` **ingredient**, so the
  first action is `c2pa.opened` (followed by `c2pa.resized` / `c2pa.converted`) —
  satisfying the spec rule that the first action be `created` or `opened`. A
  first-party capture with no prior version instead uses the **create intent**
  (`c2pa.created`, digital-capture source). Creator and descriptive metadata ride
  in a **CAWG metadata assertion** (`cawg.metadata`, JSON-LD with `dc:` fields),
  the successor to the deprecated `stds.schema-org.CreativeWork` assertion; our
  domain data stays in a custom `glassdb.provenance` assertion.
- **Cert model & trust**: a self-signed ES256 cert is generated under
  `data/c2pa/` on first use. Verifiers report `signingCredential.untrusted`
  because the cert isn't on the C2PA Trust List (and isn't chained to a private
  trust anchor) — honest for a POC. Two ways to become trusted: configure a
  private trust anchor for internal verification, or, once the app is complete,
  apply to the [C2PA Conformance Program](https://c2pa.org/conformance/) and use a
  trusted cert. Either way it's a drop-in at `data/c2pa/cert.pem` + `key.pem`; no
  code change.
- **Next on the identity side**: add a **CAWG identity assertion**
  (`cawg.identity`) so a contributor can cryptographically prove control of a
  named identity. Unlike the metadata assertion, it needs an identity signature
  (its own signer), so it's a follow-up rather than a drop-in. See
  [cawg.io](https://cawg.io/).

## Where each of your threads plugs in

| Goal | Status | Where it goes |
|---|---|---|
| Object tracking, history, value, insurance | **built** | Glowtbook → Objects (metadata + images + provenance timeline; value/insurance private, opt-in to publish) |
| Render objects publicly | **built** | Explore → Objects (image gallery + provenance + manifest download) |
| Condensed contribution ("compromised" copy) | **built** | AIP→DIP in `media.py`; images downscaled, manifest generated |
| Ontology alignment | **built** | technique assertions carry `gbo:` IRIs; events map cleanly to CIDOC-CRM |
| C2PA content credentials | **built (self-signed)** | `c2pa_sign.py` signs each DIP image; Explore verifies. Production = swap in a Trust List cert |
| ATProto / Bluesky "receipt" | **built (needs live test)** | `bluesky.py` posts the object **with its provenance image** as an app.bsky.feed.post; optional per-object, app-password entered at publish time, never stored |
| Moderation before public | **built** | contributions land in a staging queue (`object_submissions`); admin **Review queue → Objects** approves/rejects; only approved rows reach the public `objects` table. Toggle with `GLASSDB_MODERATION` (default on) |
| Verify on public tools | **built** | the API serves each approved object's image at `/api/objects/{id}/image` with the credential **byte-intact** (no re-encoding), so anyone can verify it at contentcredentials.org/verify or c2paviewer.com by pasting the URL |
| Video condensing | **built** | `video.py` transcodes video to a condensed H.264 MP4 (≤720p, +faststart) + a poster frame for the DIP; served at `/api/objects/{id}/video` and played in Explore. Falls back to AIP-only if ffmpeg is absent |
| BagIt export / MinIO backing | **built** | `aip.py` wraps an object's originals as a **BagIt** bag (sha256 + sha512 fixity, `metadata.json` payload), serializes it to a tar, and optionally pushes to **MinIO**/S3 — the Kopia/BagIt/MinIO pattern. Local-only if MinIO isn't configured |

## Verifying Content Credentials on public tools

The signed image carries a real C2PA manifest, so it verifies on the standard
tools — with the honest caveat that a self-signed cert reads as *untrusted*:

1. **Get an un-re-encoded copy.** Re-encoding strips C2PA the same way it strips
   EXIF, so a screenshot or a social-media copy won't carry the credential. Use
   the registry's own bytes: the **⬇ Signed image** button in Explore, or the
   URL `https://glassdatabase.org/api/objects/{id}/image`.
2. **Drop it into a verifier** — contentcredentials.org/verify (the CAI
   reference tool) or c2paviewer.com. Both run in-browser; you can drag the file
   in or paste the image URL. Explore also deep-links straight to the verifier.
3. **Read the result.** You'll see the creator, the assertions
   (`stds.schema-org.CreativeWork`, `glassdb.provenance`), and a validation
   state. With the **self-signed test cert** that state is "invalid/untrusted" —
   the manifest is intact and readable, it just doesn't chain to a trusted CA.
4. **To make it trusted**, drop an end-entity cert from a C2PA **Trust List** CA
   (e.g. SSL.com's 2026 free tier) in as `data/c2pa/cert.pem` + `key.pem`. Same
   pipeline, and the validation state flips to trusted.

Note the Bluesky post attaches the provenance image, but Bluesky re-encodes on
upload, so the credential is usually stripped from the copy it hosts — the
verifiable original stays in the registry at the URL above.

## The AIP is now a real preservation package

The AIP side (`aip.py`) wraps an object's **originals** — full-fidelity, plus a
`metadata.json` that may include the private value/insurance record — as a
**BagIt** bag:

```
<object>-<hash8>/
  bagit.txt                     # BagIt 1.0, UTF-8
  bag-info.txt                  # Source-Organization, External-Identifier=<content_hash>, …
  manifest-sha256.txt           # payload fixity
  manifest-sha512.txt
  tagmanifest-sha256.txt        # tag-file fixity
  tagmanifest-sha512.txt
  data/
    metadata.json               # full object record + provenance (private incl.)
    originals/…                  # the untouched masters
```

The bag is serialized to a single `.tar` and, when MinIO is configured, pushed
to object storage under `aip/<object_id>/<bag_id>.tar` (bucket auto-created).
The bag validates with any BagIt tool, so it drops straight into the
Kopia/BagIt/MinIO preservation flow. Configure with `MINIO_ENDPOINT`,
`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE`; leave
them unset to keep bags local under `data/aip_bags/`.

OAIS mapping, end to end: **SIP** = what the contributor uploads in Glowtbook;
**AIP** = the BagIt bag of originals (+ private metadata), local or in MinIO;
**DIP** = the condensed, public rendition — downscaled images, transcoded video
+ poster, and the C2PA-signed manifest — served through the API and Explorer.

## The publication gate — nothing public until approved

Every dataset row — whether it arrived via `central/ingest.py`, was edited in the
admin, or was contributed through Glowtbook — is gated by a central `_approvals`
table:

```
_approvals(tbl, row_id, status, reviewed_at, reviewer)   -- status: approved | rejected | (absent = pending)
```

The default is **deny**: a row with no entry is not served. The API and explorer
add one filter — `_row_id IN (SELECT row_id FROM _approvals WHERE tbl=? AND
status='approved')` — so public surfaces only ever show approved rows. An admin
key on the API bypasses the gate for review.

Why a side table instead of a column on every dataset: it makes the default
deny for free, needs **no schema change** to enable on a live database, and lets
a whole dataset be approved in a single statement.

**Admin → ✅ Approvals** shows approved/pending counts per dataset with:
- "Approve ALL pending content" (first-time setup, republishes the existing DB),
- per-dataset "Approve all pending" / "Reject all pending", and
- row-level select-and-approve for finer control.

Object and profile promotions write an approved entry automatically, so the
existing moderation queues feed the same gate. Enable on an existing deployment
with `python -m scripts.migrate_approval_gate` (creates the empty table; existing
rows become pending until you approve them).

## Suggested build order (remaining)

1. **Trusted C2PA cert** — replace the self-signed test cert with a Trust-List
   end-entity cert (e.g. SSL.com's 2026 free tier) so Content Credentials
   validate as trusted. Drop-in at `data/c2pa/{cert,key}.pem`; no code change.
2. **AIP fixity audits** — periodic `bagit` validation + a re-verification report,
   the natural next step once bags are landing in MinIO.
3. **Range requests** on the video endpoint for scrubbing long clips (the DIP
   renditions are small, so full-body serving is fine for now).

Each is an additive module against the seams that already exist — nothing here
requires reworking what's built.
