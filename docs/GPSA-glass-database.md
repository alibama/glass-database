# GPSA — Generator Product Security Architecture
## Glass Database (v1.0.0)

*Prepared for the C2PA Conformance Program. This document describes the product
and its Target of Evaluation **as built and in testing** — it contains no
forward-looking or planned language. Where a control is minimal, it is stated as
such.*

Conformance record ID: `01a0792c-d2d7-7608-b532-25e229e3cf03`

---

## 1. Product description

**Glass Database** is a provenance and cataloging service for glass objects. Its
Generator Product function embeds C2PA Content Credentials into still images that
a contributor publishes to the public registry. When a maker contributes a piece,
the service produces a condensed public rendition of each image and signs a C2PA
manifest into it recording authorship, edit actions, domain provenance, and (when
present) a physical re-identification fingerprint.

- **Generated media types:** `image/jpeg`, `image/png`.
- **Validated (ingested) media types:** `image/jpeg`, `image/png` — originals are
  ingested as `parentOf` ingredients when deriving the public rendition.
- **Claim generator:** `Glass Database / 1.0.0` (c2pa-rs reported in
  `claim_generator_info`).
- **Signature algorithm:** ECDSA P-256 / SHA-256 (**ES256**).
- **Asserted C2PA spec version:** 2.2. **Assurance level sought:** the minimum
  (software-based signing key; see §6).

## 2. Target of Evaluation (TOE)

### In scope
| Component | Description |
|---|---|
| Signing module | `glowtbook/c2pa_sign.py` — builds the manifest, invokes c2pa-rs, signs |
| C2PA engine | `c2pa-python` (bindings to `c2pa-rs`), pinned in `requirements.txt` |
| Manifest construction | assertions, actions, ingredient handling (§5) |
| Signing key + certificate | ES256 key + X.509 cert on the host filesystem (§6) |
| Execution environment | the `glassdb-glowtbook` systemd service (Python/Streamlit) on a single Linux host |
| Host & OS | Ubuntu 22.04 VM (single cloud instance), reached only via the reverse proxy and SSH |
| Transport security | Apache 2.4 reverse proxy terminating TLS (Let's Encrypt) |

### Out of scope
- End-user devices/browsers; the contributor's original camera/source.
- The public read paths (API, Explore) — they serve already-signed bytes and do
  not hold signing material.
- Video/document/text/ML-model signing — not implemented; not asserted.
- The physical-object fingerprint *matching* (a separate, unsigned client feature);
  only the fingerprint *summary assertion* is in scope as manifest content.

A TOE component diagram is in Appendix A of this document.

## 3. Architecture and data flow

1. An authenticated contributor (Google OIDC) uploads one or more **original**
   images through the Glowtbook service. Originals are retained by the contributor;
   the service holds only what is needed to build the public rendition.
2. The service produces a **condensed rendition** (downscale/transcode with Pillow)
   in `image/jpeg` or `image/png`.
3. `c2pa_sign.sign_image()` constructs a manifest (§5), adds the **original as a
   `parentOf` ingredient** (this is the "validate/ingest" step for the ingredient's
   media type), sets the edit intent so the first action is `c2pa.opened`, and
   signs via a callback signer bound to the ES256 key (§6).
4. The signed bytes are written to the registry store and served read-only over
   HTTPS. The signing key never leaves the host and is never exposed by any
   network endpoint.

All signing happens **server-side, in-process**, on the single host. No signing
material is transmitted to clients.

## 4. Cryptographic operations

- **Algorithm:** ES256 (ECDSA, curve P-256, SHA-256).
- **Signature encoding:** raw IEEE P1363 (r‖s), supplied by the callback signer to
  c2pa-rs.
- **Hashing:** content hashing/manifest hashing performed by c2pa-rs per the C2PA
  spec (BMFF/JUMBF hard-binding for the supported image formats).
- **Certificate:** X.509 with a SubjectKeyIdentifier, EKU/KU appropriate to C2PA
  claim signing; SHA-256 signature.

## 5. Manifest construction

Each manifest contains:
- `claim_generator_info`: `{ name: "Glass Database", version: "1.0.0" }` plus the
  c2pa-rs version string.
- **`c2pa.actions`**: when derived from an ingested original, the first action is
  **`c2pa.opened`** (edit intent), followed by `c2pa.resized` and `c2pa.converted`;
  a first-party capture with no parent uses **`c2pa.created`** (create intent,
  `digitalSourceType = digitalCapture`).
- **`cawg.metadata`**: `dc:creator`, `dc:title`, `dc:format`, and `xmp:CreateDate`
  (the successor to the deprecated `stds.schema-org.CreativeWork`).
- **`glassdb.provenance`**: a custom assertion with the content hash, sourcing,
  contributor, and rights statement.
- **`glassdb.fingerprint`** *(optional)*: a compact re-identification attestation
  (rating, tier, dominant colour, and a SHA-256 of the object fingerprint).
- **Ingredients:** the original is added as a `parentOf` ingredient with its own
  media type, which is how the product validates each asserted "validate" format.

## 6. Key and certificate management *(the assurance-limiting control)*

**Stated plainly:** the current implementation uses a **software** ES256 signing
key held on the host filesystem. This is the reason the product seeks the
**minimum** assurance level and asserts **no** hardware/integrity attestation
method.

- **Generation:** the key is an ECDSA P-256 private key generated with the
  `cryptography` library and stored PKCS#8-encoded.
- **Storage & protection:** the private key and certificate live at
  `data/c2pa/{key,cert}.pem`, owned by the dedicated service user (`glassdb`),
  with the key file mode restricted to `600` (owner read/write only). It is not in
  a hardware security module or KMS.
- **Access:** only the `glassdb` service process reads the key at signing time.
  The host is administratively accessed over SSH; there is no application endpoint
  that returns or uses the raw key.
- **Certificate:** for pre-conformance testing this is a **self-signed** ES256
  certificate, which validates as *untrusted* — expected until a certificate is
  issued from a Trust-List CA following a Notice of Conformance. The certificate is
  drop-in replaceable at the same path without code changes.
- **Rotation/revocation:** replacing the key/cert files and restarting the service
  rotates the signing material.

## 7. Execution environment and access control

- Signing runs inside the `glassdb-glowtbook` systemd unit as the unprivileged
  `glassdb` user. The four services (api, explore, glowtbook, admin) run as the
  same service user on one host.
- The public reaches only the Apache reverse proxy (ports 80/443). Application
  ports (8000/8501/8502/8503) are not directly exposed.
- Administrative functions are gated (HTTP basic auth on the admin console;
  Google OIDC for contributor identity).
- Host administration is via SSH key authentication.

## 8. Supporting infrastructure

- **Host:** a single Ubuntu 22.04 cloud VM.
- **Reverse proxy / TLS:** Apache 2.4 with a Let's Encrypt certificate; HTTP is
  redirected to HTTPS.
- **Persistence:** a local SQLite database (WAL) on the same host; signed image
  bytes stored on the host filesystem / database.
- **Time:** system clock synchronized via the OS NTP client (relevant to signing
  timestamps).

## 9. Third-party components and software supply chain

- **Signing:** `c2pa-python` (bindings to `c2pa-rs`) — the C2PA implementation.
- **Crypto:** `cryptography` (key generation and the raw ES256 signing primitive).
- **Imaging:** `Pillow` (rendition production).
- **Fingerprint assertion data:** `object-fingerprint`.
- **Identity:** Google OIDC (contributor sign-in; not part of the signing trust
  path).
- Dependencies are pinned in `requirements.txt`; deployment is git-based via an
  idempotent installer (`deploy/install.sh`) that preserves signing material and
  secrets across updates. Source is public at
  `github.com/alibama/glass-database`.

## 10. Secrets management

Signing key/cert (§6), the OIDC client secret (`.streamlit/secrets.toml`), the
admin token (`.env`), and the admin basic-auth file (`.htpasswd`) are all on the
host filesystem, owned by the service user, and excluded from version control and
from the deploy sync. None are transmitted to clients.

## 11. Assumptions and limitations

- Trust in a produced credential is bounded by §6: until a Trust-List certificate
  is installed, credentials validate as *untrusted* by design.
- The TOE is a single host; there is no multi-region or HSM-backed key custody in
  the current design.
- Only the two asserted still-image formats are generated/validated.

## Appendix A — TOE component diagram

```
Contributor (browser, Google OIDC) ──HTTPS──▶ Apache 2.4 (TLS term)
                                                   │
                                                   ▼
                                   glassdb-glowtbook service  ── reads ──▶ data/c2pa/key.pem (mode 600, glassdb)
                                     │  Pillow rendition                    data/c2pa/cert.pem
                                     │  c2pa_sign.sign_image()
                                     │  c2pa-python → c2pa-rs (ES256 callback)
                                     ▼
                                   signed image bytes ──▶ registry store ──HTTPS(read-only)──▶ public
```
*Everything inside the service boundary runs on one Ubuntu host as the `glassdb`
user. The signing key is filesystem-resident and never leaves the host.*
