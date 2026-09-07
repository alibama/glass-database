# C2PA Conformance Readiness — gap analysis

*What the Glass Database generator already satisfies in the C2PA Content
Credentials Specification, and what to address before submission. Section
numbers reference the technical spec.*

## Already satisfied (the important core)

- **Everything is hashed and signed — natively.** Each assertion is referenced
  from the claim by a **hashed_uri** (§6.3.1): the claim holds a SHA-256 over each
  assertion's JUMBF box, and the **claim itself is COSE-signed (ES256)** (§9.3.2.4,
  §12.2). So our `cawg.metadata`, `org.glassdatabase.provenance`, and fingerprint
  assertions are each hashed, and the signature covers those hashes. We do **not**
  need to sign the JSON separately — that's inherent to C2PA.
- **Hard binding on the asset.** c2pa-rs emits a `c2pa.hash.data` assertion that
  hashes the image bytes (§8.2, §17.5), which a standard manifest **requires**
  (§14.6.1). PNG chunk exclusions (§17.5.3) are handled by the library. So the
  image content is bound, not just the metadata.
- **Signature & algorithm.** ES256 (P-256/SHA-256) is on the allowed list (§12.2).
- **Actions.** First action is `c2pa.opened` (edit intent) or `c2pa.created`
  (capture intent) (§17.10) — correct per the earlier reviewer feedback.
- **Assertions are statements, not verified facts** (§13.6) — our design matches
  this: we (the signer) vouch for the provenance/fingerprint; C2PA guarantees they
  weren't tampered with, not that they're "true."

## Fixed in this pass

- **Assertion label namespacing (§5.2).** Entity labels *shall* begin with the
  entity's Internet domain (like `com.litware`). Renamed `glassdb.provenance` →
  **`org.glassdatabase.provenance`** (and the app-manifest labels similarly). The
  fingerprint label `io.github.object_fingerprint.fingerprint` is package-provided
  and already reverse-domain-shaped.

## Gaps to address BEFORE submission

1. **Signing-key custody (highest priority).** The private key is currently a
   software key on the host filesystem — the assurance-limiting control. Move it
   to a KMS/HSM where it is **non-exportable** (options below). This is the §6 item
   in the GPSA and almost certainly what the Generator Product Security
   Requirements gate on.
2. **RFC 3161 time-stamp (strongly recommended, §9.3.2.5 / §14.4).** We emit **no**
   `sigTst` countersignature today. Without it, a manifest **ceases to be valid
   once the signing certificate expires or is revoked** (§14.4). Add a trusted TSA
   at signing time. c2pa-python supports configuring a TSA URL. Free/public TSAs
   exist (e.g. DigiCert, Sectigo). Do this before the cert has a real expiry that
   matters.
3. **OCSP stapling / revocation (§9.3.2.6, §13.4.1.2).** Only relevant once we have
   a real Trust-List CA certificate (self-signed has no AIA/OCSP). When the
   conformant cert is issued, staple an OCSP response (`rVals`) at signing and pair
   it with a time-stamp. Plan for it; not actionable while self-signed.
4. **Certificate profile (§13.4.1.1).** The Trust-List CA issues a conformant cert
   (EKU, KeyUsage=digitalSignature, SubjectKeyIdentifier, v3). Until then, ensure
   the self-signed test cert clears the Conformulator's cert checks *except* the
   expected `signingCredential.untrusted`.
5. **v0.2 Additional Conformance Requirements.** The program's v0.2 PDF adds
   mandatory items beyond the spec; the Conformulator flags them. Run every sample
   through it and clear all but the untrusted-cert flag.

## Optional, more spec-native improvements

- **Bind the full fingerprint as external data (§17.8 Cloud Data / hashed_ext_uri).**
  Today we embed a compact assertion carrying a SHA-256 of the fingerprint that the
  registry serves. The spec-native way to reference external data with an integrity
  hash is a **`c2pa.cloud-data`** assertion (`hashed_ext_uri`) pointing at
  `/api/objects/<id>/fingerprint`. Same guarantee, standardized construct.
- **Per-frame binding.** If an assessor wants each capture frame bound, add a
  hash-list of frames (still not embedding the bytes). Not required — the
  fingerprint hash already covers the aggregate.

## Identity (see the response) — summary

- The **C2PA trust identity is our X.509 signing certificate** (the org — "Glass
  Database"), per §1.4/§13.2. That is the trust root, and what the conformance cert
  issues. Contributors are named in `dc:creator` / CAWG metadata as **signer
  statements**, not independently verified.
- **Google OIDC does not provide a DID.** It gives an OIDC `sub` + email, not a
  W3C DID. Don't treat it as one.
- W3C Verifiable Credentials / DIDs (§7) are **optional trust signals** and have
  **no bearing on the trust model** (§7.1). If we later want verified per-creator
  identity, the path is a **CAWG identity assertion** (which can use `did:web` or
  OAuth verification) — a deliberate build, not automatic.
