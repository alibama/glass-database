# Changelog

All notable changes are documented here. This project distinguishes
proof-of-concept features from production-ready ones in its docs.

## [Unreleased]
### Added
- **Accessibility (mDLAUG)** — the Explore → Objects view is rendered as
  purpose-built accessible HTML aligned to the Mobile Digital Library
  Accessibility & Usability Guidelines: named list of items, real image alt
  text, live result count, per-item position, provenance as a data table, and
  file links with format/size. Regression tests + docs/ACCESSIBILITY.md.
- **Unified look + cross-app nav** — a shared `brand` package gives Explore,
  Glowtbook, and Admin the homepage identity (furnace/molten palette, Fraunces
  headings, the glass mark) and a top nav bar to jump between Home, Explore,
  Glowtbook, and Admin. Streamlit theme set in .streamlit/config.toml.
- **C2PA fixes (spec-correctness)** — signed images now use the edit intent with
  the original as a `parentOf` ingredient, so the first action is `c2pa.opened`
  (fixes `assertion.action.malformed`); creator/metadata moved from the
  deprecated schema.org CreativeWork assertion to a **CAWG metadata** assertion.
  Validation now reads Valid apart from the expected untrusted-cert note.
- **Homepage** — a complete landing page (furnace/molten identity, Fraunces
  display) linking Explore, Glowtbook, the API, and contribution, with a
  swappable logo at public/logo.svg.
- **Contribution module** — object CRUD + the central publish pipeline extracted
  to glowtbook/contribute.py (UI-agnostic), so a Gradio surface, write API, or
  native client can reuse it. Streamlit stays the UI.
- **Mobile** — Glowtbook installs as a PWA (manifest + icons + service worker,
  injected at the Apache proxy so OAuth still works), with camera capture and a
  mobile-tuned layout. App-store path documented via Capacitor (deploy/MOBILE.md).
- **Publication gate** — a central `_approvals` table gates every dataset row;
  nothing (imported, edited, or contributed) is served publicly until approved.
  Admin → ✅ Approvals gives per-dataset counts, one-click "approve all pending",
  reject, and row-level select-and-approve. Default-deny, no destructive schema
  change (safe on a live DB); migrate an existing DB with
  `python -m scripts.migrate_approval_gate`.
- Central self-describing SQLite store + re-runnable spreadsheet importer.
- Read-only, self-documenting FastAPI service with private-column withholding
  and restricted-dataset protection.
- Public data explorer (break-downs, charts, studio map, downloads) with
  coordinate/identifier exclusion and outlier-trimmed histograms.
- Admin console: content editing, a moderation review queue, de-duplication.
- Glowtbook object registry with an OAIS AIP/DIP split.
- C2PA Content Credentials signing + verification (self-signed test cert; drop
  in a Trust-List cert for trusted validation).
- Moderation gate for object contributions (staging → approve/reject).
- Video transcoding to a condensed H.264 DIP rendition + poster (ffmpeg).
- BagIt AIP packaging (sha256 + sha512) with optional MinIO/S3 push.
- Optional Bluesky/ATProto publishing with the provenance image attached.
- Synthetic demo seed, pytest suite, and CI.
