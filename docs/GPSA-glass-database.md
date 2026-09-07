# GPSA — Generator Product Security Architecture
## Glass Database — C2PA Conformance Program

*Prepared to the Appendix C template of the C2PA Generator Product Security
Requirements. Describes the product **as built and in pre-production testing** —
no forward-looking language. Items you must supply before submitting are marked
**[PROVIDE]**; controls you must switch on to make a claim truthful are marked
**[CONFIRM]**.*

Record ID: `01a0792c-d2d7-7608-b532-25e229e3cf03`

---

## 1. Generator Product Information

### 1.1 Applicant organization details
- **Legal name:** **[PROVIDE — the legal entity operating glassdatabase.org]**
- **Address:** **[PROVIDE]**
- **Contact:** Anson Parker — **[PROVIDE email/phone]**

### 1.2 C2PA Conformance Program Version
**0.2**

### 1.3 C2PA Content Credentials Specification Version
**2.2**

### 1.4 Distinguished Name
- **CN (Common Name):** Glass Database
- **O (Organization):** **[PROVIDE — legal name, same as 1.1; minted into issued certs]**
- **OU (Organizational Unit):** *(optional)* Digital Provenance
- **C (Country):** US

### 1.5 Generator Product Description
Glass Database is a provenance and cataloging service for glass objects. Its
Generator Product function embeds C2PA Content Credentials into still images that a
contributor publishes to the public registry. When a maker contributes a piece, the
service produces a condensed, format-preserving public rendition of each image and
signs a C2PA manifest into it recording authorship (CAWG metadata), edit actions,
domain provenance, and (when present) a physical re-identification fingerprint.
Intended users are individual glass artists, studios, and collections; key features
are contribution with Content Credentials, a public registry/API, and a browser
verification flow.

### 1.6 GP TOE Description
The Target of Evaluation is the server-side signing pipeline and its host:

| In scope | Description |
|---|---|
| Signing module | `glowtbook/c2pa_sign.py` — builds the manifest, invokes c2pa-rs, signs (ES256) |
| C2PA engine | `c2pa-python` (bindings to `c2pa-rs`), pinned in `requirements.txt` |
| Content-processing | `Pillow` (format-preserving condense/rendition), `object-fingerprint` |
| Signing key + cert | ES256 key + X.509 cert on the host filesystem (§2.2) |
| Execution env | the `glassdb-glowtbook` systemd service (Python/Streamlit) as user `glassdb` |
| Host / OS | a single Ubuntu 22.04 cloud VM |
| Transport / edge | Apache 2.4 reverse proxy terminating TLS; app ports bound to localhost |

**Out of scope:** the contributor's browser and original camera; the public read
paths (API/Explore) which serve already-signed bytes and hold no signing material;
video/document/text/ML signing (not implemented, not asserted). *An architecture
diagram is at Appendix A.*

### 1.7 Implementation Class
**Backend.** The Claim Generator runs server-side on the host; the browser is a
plain client, not a GP Edge subsystem, so there is no Edge↔Backend split.

### 1.8 Target Max Assurance Level
**Level 1.** The claim-signing key is a software key protected by OS access
controls (see §2.2). Level 2 (hardware-root-of-trust key custody + attestation) is
not claimed.

### 1.9 Target Generator Product capabilities
- **Claim generation:** `image/jpeg`, `image/png`
- **Claim validation:** `image/jpeg`, `image/png` (originals ingested as `parentOf`
  ingredients when deriving the public rendition)

Manifests declare `specVersion: 2.2` and set `allActionsIncluded`; the
`c2pa.created` action carries a `digitalSourceType`; `c2pa.opened` and the excepted
resize/convert actions do not (per Conformance v0.2 §2.1/§2.2/§2.4/§2.5).

---

## 2. Security Architecture Details by Objective

### 2.1 [O.1] Automated Certificate Enrollment Proof of Eligibility

**2.1.1 Level 1 & 2 Base Evidence**
1. **Enrollment process.** Enrollment is **manual, not automated**. A single
   claim-signing certificate is provisioned per deployment: for pre-conformance
   testing a self-signed ES256 certificate is generated on the host; upon a Notice
   of Conformance, a certificate will be obtained from a C2PA Trust-List CA and
   installed at the same path. There is no per-instance automated enrollment API —
   the product is a single Backend instance with one signing identity.
2. **Authentication method / API.** Not applicable — no automated enrollment API.
   CA issuance (post-conformance) will follow the CA's manual/verified process.
3. **Management of authentication secrets.** No enrollment API secrets exist. The
   signing key itself is managed per §2.2.

**2.1.2 Level 2 Additional (Hardware RoT binary identity):** Not claimed (Level 1).

### 2.2 [O.2] Confidentiality of the Claim Signing Key

**2.2.1 Level 1 & 2 Base Evidence**
1. **Key generation & storage.** An ECDSA P-256 private key (**ES256**,
   NIST FIPS 186-4 / SEC P-256) is generated with the `cryptography` library and
   stored PKCS#8-encoded at `data/c2pa/key.pem`; the certificate at
   `data/c2pa/cert.pem`.
2. **Access controls & encryption.** The key file is mode `600`, owned by the
   dedicated unprivileged service user `glassdb`; only the signing service reads it,
   at signing time. `data/c2pa/` is mode `700`. No application endpoint returns or
   transmits the key. Host administration is via SSH key authentication; the box is
   hardened (firewall limits ingress to 80/443/22; app/service ports bound to
   `127.0.0.1`; secrets `600`; sysctl hardening — see `deploy/harden.sh`).
3. **Ephemeral plaintext key handling.** During signing, the private key is loaded
   into the `glassdb` process memory to compute one ES256 signature via the
   `cryptography` primitive (a maintained third-party library, monitored for
   vulnerabilities per §2.3). The key is not written elsewhere and is not exposed
   outside the process.
4. **Key rotation.** Rotation replaces the key/cert files and restarts the service;
   triggered on suspected compromise or CA cert renewal.
5. **Subsystem mutual authentication:** N/A — single Backend, no Edge subsystem.

**2.2.2 Level 2 Additional (HSM / hardware attestation):** Not claimed. *(A
HashiCorp Vault Transit path exists in the codebase — `glowtbook/vault_signer.py`,
`deploy/VAULT.md` — that keeps the key non-exportable; if pursued for Level 2 it
would be documented here as-built. It is not enabled in the evaluated
configuration, so no Level 2 claim is made.)*

**2.2.3 Level 2 Distributed/Backend client attestation:** N/A (Level 1).

### 2.3 [O.3] Protection of the Claim Generator

**2.3.1 Level 1 & 2 Base Evidence**
1. **SCA / SBOM scanning.** Dependencies are pinned in `requirements.txt`.
   `deploy/sca-scan.sh` runs **pip-audit** (OSV / NIST NVD) and emits a CycloneDX
   SBOM (`GPSA-sbom.cyclonedx.json`). **[CONFIRM — run it in the build/deploy
   pipeline and attach the SBOM + a clean/triaged report.]**
2. **90-day remediation.** Deployment is git-based via `deploy/update.sh`. Policy:
   no build ships with a `CRITICAL`/`HIGH` (CVSS v3+) dependency vulnerability
   unremediated more than 90 days after detection; `sca-scan.sh` gates this.
   **[CONFIRM — enforce as a pipeline gate.]**

**2.3.2 Level 2 Additional:** Not claimed (Level 1).

### 2.4 [O.4] Protection of Assets & Assertions at Generation

**2.4.1 Level 1 & 2 Base Evidence**
1. **SCA / SBOM scanning.** The content-processing/assertion software in the TOE is
   `Pillow`, `c2pa-python`/`c2pa-rs`, and `object-fingerprint`, all pinned and
   covered by the same `deploy/sca-scan.sh` scan and SBOM as §2.3. **[CONFIRM.]**
2. **90-day remediation.** Same pipeline policy as §2.3.2 applies to
   content-processing software. **[CONFIRM.]**

**2.4.2 Level 2 Additional:** Not claimed (Level 1).

### 2.5 [O.5] Protection of Traffic Between Subsystems
**Not applicable to this Backend.** There is no Edge↔Backend GP split. Client↔server
traffic is protected by Apache TLS (**[CONFIRM TLS 1.3]**, Let's Encrypt cert, HTTP
redirected to HTTPS); the internal app ports are bound to `127.0.0.1` and not
network-exposed.

### 2.6 [O.6] Protection of the Hosting Environment *(Backend class)*

**2.6.1 Level 1 & 2 Base Evidence**
1. **IAM & RBAC.** OS-level: a single unprivileged service user (`glassdb`) runs the
   services; administrative host access is SSH-key-only (no passwords, no root
   password login). Application admin is gated by HTTP basic auth; contributor
   identity by Google OIDC.
2. **Principal access policies.** Non-human principal = the `glassdb` service user
   (least privilege, owns only its data/secrets, not a sudoer). Human principals =
   named SSH keys for administrators.
3. **Cloud resource IAM.** Single VM; **[PROVIDE — cloud provider IAM/roles on the
   VM, and 2FA on the cloud/DNS/registrar accounts]**.
4. **Vulnerability scanning & OWASP Top 10.** Dependency/API scanning per §2.3.
   **[CONFIRM — an OWASP Top 10 review/scan of the public API + admin surfaces,
   e.g., with OWASP ZAP; attach results.]**
5. **Remediation timelines.** High ≤30 days, Moderate ≤90, Low ≤180. **[CONFIRM.]**

**2.6.2 Level 2 Additional (audit logging, HIDS, segmentation, Attachments A–C):**
Not claimed (Level 1). *(Baselines that exist: systemd/journald logs, fail2ban on
SSH, ufw firewall. Full audit-logging/HIDS/segmentation reports are Level 2.)*

---

## Appendix A — TOE component diagram

```
Contributor (browser, Google OIDC) ──HTTPS──▶ Apache 2.4 (TLS termination, :443)
                                                    │  (app ports bound to 127.0.0.1)
                                                    ▼
                                 glassdb-glowtbook service (user: glassdb)
                                   │  Pillow format-preserving rendition (PNG→PNG, else JPEG)
                                   │  c2pa_sign.sign_image()  →  c2pa-python → c2pa-rs
                                   │      • claim_generator_info.specVersion = 2.2
                                   │      • c2pa.actions.v2  (allActionsIncluded=true;
                                   │        created→digitalSourceType; opened→none)
                                   │      • ES256 signature (callback + cryptography)
                                   ▼                         reads ──▶ data/c2pa/key.pem (600, glassdb)
                                 signed image ──▶ registry ──HTTPS (read-only)──▶ public / API
```
*Everything inside the service boundary runs on one Ubuntu host as `glassdb`. The
signing key is filesystem-resident (mode 600) and never leaves the host.*

## Appendix B — Evidence checklist to attach
- `a-sample.jpeg` / `a-ingredient1.jpeg`, `b-sample.png` / `b-ingredient1.png`
  (from `deploy/c2pa_evidence.py`, using the program's ingredient library), each
  cleared in the Conformulator except the expected untrusted-cert flag.
- `GPSA-sbom.cyclonedx.json` and the pip-audit report (§2.3/§2.4).
- **[Level-2 only, not required here]** countermeasure/HIDS/segmentation reports.
