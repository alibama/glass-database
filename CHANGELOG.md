# Changelog

## [1.0.0] — C2PA conformance readiness

All notable changes are documented here. This project distinguishes
proof-of-concept features from production-ready ones in its docs.

## [Unreleased]
### Added
- **Button-pill forms** — intake select/multiselect fields (and Glowtbook techniques/
  role) render as tappable st.pills chips instead of dropdowns, styled to Field Data.
- **Studios world map** — /studios.html: a dark Leaflet/CARTO map of studios with
  type-filter pills, search, and Field Data popups, fed by GET /api/studios.geojson.
  Linked from the homepage.
- **Confidential-but-assertable pricing (prototype)** — central/price_commit.py: hash
  commitments (selective disclosure) + Pedersen commitments (homomorphic — prove a
  portfolio total without revealing individual prices). docs/PRICING-COMMITMENTS.md.
- **Whole-site Field Data consistency** — the Streamlit apps (Explore, Glowtbook,
  Admin) now share the homepage identity: dark reheat-glow ground, Fraunces headings
  (explicit axis order, weight-300 fallback), Archivo body, IBM Plex Mono labels/pills,
  amber/teal/violet accents (brand theme + Streamlit dark base). The Explore objects
  grid and the relationship graph are recoloured to the Field Data palette.
- **Homepage remodeled to the Field Data house style** — dark editorial ground, Fraunces
  headlines with amber italic accents, Archivo body, IBM Plex Mono eyebrows/labels/CTAs,
  the teal→amber→molten palette, filament motif, index rows, glowing-node lists, and the
  capture→fingerprint→verify step chain. All links/sections/newsletter preserved.
  deploy/field-data.css served at /field-data.css.
- **Serve the real image format** — the object image endpoint now sends image/png vs
  image/jpeg (and a matching filename) based on the actual bytes, instead of always
  image/jpeg; the Explore download label shows PNG or JPEG correctly.
- **deploy/reset_objects.py** — wipe published objects + submissions (keep artists,
  studios, users, etc.). **deploy/crjson_harness.py** — §2.3 validation test harness
  (asset + trust lists + time -> crJSON).
- **Format preservation** — condense_image now keeps PNG as PNG (was JPEG-only);
  signing preserves the format end-to-end (PNG→PNG, JPEG→JPEG).
- **C2PA Conformance v0.2 manifest fields** — every manifest now sets
  claim_generator_info.specVersion (2.2, §2.1), c2pa.actions.v2 allActionsIncluded
  (§2.2), and digitalSourceType on c2pa.created but not on the excepted
  opened/resized.proportional/converted actions (§2.4/§2.5). GPSA rewritten to the
  Appendix C template; deploy/sca-scan.sh (pip-audit + CycloneDX SBOM).
- **Signing-key custody via HashiCorp Vault** — glowtbook/vault_signer.py signs C2PA
  claims through Vault's Transit engine (key non-exportable, never on the app host);
  enabled with C2PA_SIGNER=vault. Adds deploy/vault-setup.sh, deploy/vault_provision.py
  (BYOK import + cert), deploy/VAULT.md. Also wires an optional RFC 3161 time-stamp
  (C2PA_TSA_URL) — closing that readiness gap.
- **Host hardening** — deploy/harden.sh (ufw, SSH key-only, fail2ban, bind app ports to
  localhost, secret perms, sysctl) + deploy/HARDENING.md checklist.
- **Fingerprint ↔ C2PA verification** — the whole multi-view fingerprint is hashed and
  that hash is signed into the credential; new verify_binding() + GET
  /api/objects/<id>/fingerprint/verify recompute it and confirm the served fingerprint
  is the signed one (bound true/false + signer + validation state). Shown in verify.html.
- **1.0.0** — product name/version centralized (central/version.py); C2PA claim
  generator now signs as **Glass Database / 1.0.0** to match the Conformance record.
  GPSA security-architecture doc drafted (docs/GPSA-glass-database.md).
- **C2PA: PNG in scope** — signing generalized to sign_image() supporting image/jpeg
  *and* image/png (format-aware dc:format + container), matching the Conformance
  Program assertion; both validate as Valid. sign_jpeg() kept as a wrapper. Adds
  deploy/c2pa_evidence.py to build the X-sample/X-ingredient evidence package.
- **Roles vocabulary** — a 'your role(s) in the glass field' multiselect on the artist
  form (30 categories + Other), editable in one place (central/techniques.ROLES).
- **Consent-gated content harvesting** — artists opt in on the form; central/harvest.py
  re-checks consent, signs C2PA provenance asserting the ARTIST as owner, and stores
  each item as pending. Nothing is published without per-item approval in Admin →
  Harvest; approved images served C2PA-intact at /api/harvest/<id>/image. Playwright/
  Instagram-API runner (deploy/harvest_runner.py) + deploy/HARVEST.md (uses the IG API,
  not scraping; respects robots.txt; consent revocable).
- **Relationship graph (light)** — a client-side network of artists · techniques ·
  studios · mentors built from existing data (central/graph.py → /api/graph.json),
  drawn with cytoscape.js at /graph.html (search, click-to-focus a neighbourhood).
  No graph database. Mentor edges come from matching studied_under text to directory
  artists.
- **Batch-tool examples** for new admins, and a GitHub link on the homepage + admin.
- **Homepage: 'digital barcoding' section** — brands the re-identification fingerprint as a
  barcode you read off the object, with real capture screenshots, the C2PA provenance tie-in,
  a clear *experimental* label, and Try-the-app / Print-the-mat CTAs.
- **Admin bulk-set + undo** — Batch tools gains a column bulk-set ('set region=Virginia
  where city=Crozet', with a live match count) and an automatic snapshot before every
  batch op (CSV import, find/replace, bulk-set) with one-click Restore
  (central/snapshots.py, last 8 kept per table).
- **Newsletter signup** — prominent homepage form → POST /api/subscribe (stored, deduped,
  Discord-pinged); admin sees subscriber count + CSV export. Homepage refreshed with a
  Join/subscribe section and framing (from the hot shop to the archive; residencies;
  museum-grade cataloging aligned to Getty AAT/Wikidata; accessibility).
- **Editable tables + batch tools for admins** — Admin → Datasets gains an ✏️ Edit
  grid (edit cells, add rows, tick-to-delete; admin edits publish on save) and a ⚙️
  Batch tools tab: CSV export/import round-trip (upsert by _row_id) and column
  find & replace (whole-cell or substring).
- **Admin roles by Google login + login ledger** — every Google sign-in is
  recorded (name + email, Admin → 👥 Users); admins are set via GLASSDB_ADMIN_EMAILS
  (bootstrap) or promoted in the UI. Opt-in GLASSDB_ADMIN_OIDC=1 gates the console on
  Google admin role. See deploy/ADMIN-ROLES.md.
- **Analytics (privacy-first, self-hosted)** — Admin → 📊 Analytics shows page
  views, approximate visitors, which surfaces/views are used, submissions, and
  (optional) country. No cookies, no third parties, no raw IPs stored (visitors are
  a per-day rotating hash), Do-Not-Track honoured. Optional GeoLite2 country lookup;
  deploy/ANALYTICS.md covers that plus GoAccess (log-based geo) and Umami/Plausible.
- **Every content addition now pings Discord** — object contributions were the
  gap: a staged object submission now notifies with a **one-click Approve** that
  actually *promotes* it into the public registry (shared promotion logic used by
  both the admin console and /api/moderate), and immediate publishes post an FYI.
  Intake (artist/studio/event/resource/exchange/job), opportunities, and feedback
  already notified; objects close the loop.
- **Performance** — SQLite tuned (WAL + synchronous=NORMAL, busy_timeout, cache/
  mmap) and indexed on the approval-gate + image paths; Streamlit prod config
  (file watcher off, no telemetry, fastReruns); Apache gzip + static caching;
  cached the per-object C2PA read. See deploy/PERFORMANCE.md.
- **Venetian trait thesaurus (SKOS) + capture-time tagging** — a controlled
  vocabulary of observable style traits for façon-de-Venise glass (70 concepts / 10
  facets), served as SKOS/Turtle + JSON at /api/vocab/glass-traits.{ttl,json} with
  authority-mapping seams to Getty AAT / Wikidata / the Corning Glass Dictionary. A
  Style tab tags traits on an object; they travel into the manifest linked to the
  vocabulary. Prospectus (docs/GLASS-TRAITS.md) + facet diagram for institutional
  alignment; traits describe features, never attributions.
- **Homepage refresh** — sections for the directory + intake, opportunities
  calendar, community exchange/jobs/resources, Glowtbook provenance + physical
  fingerprint, and a Join section; Discord (discord.gg/9ek2UxvPT) and Instagram
  (@glassdatabase) linked in the hero, a Join section, and the footer.
- **DINOv2 sampled, not exhaustive** — embeddings are mean-pooled to 384 dims,
  quantised, and computed on a capped handful of views (EMB_CAP≈8) then stopped,
  so AI-enabled fingerprints drop from tens of MB to ~20 KB and capture stays fast.
- **Fingerprinting protocol published** — docs/FINGERPRINT-PROTOCOL.md (technical
  spec: descriptor tiers, calibration, thresholds, settings/testing, best
  practices) + a flowchart (docs/fingerprint-protocol.svg).
- **Feedback, community exchange, job board + admin Discord controls** — a
  sidebar feedback form (private, pings Discord, resolved in Admin → Feedback); a
  Community view with Exchange (WTS/WTB/WTT, incl. a togglable “open to trade”
  flag), Jobs, and Resources boards, all submittable via Submit and gated; and an
  Admin → Discord panel to set the webhook, toggle notifications, and send a test —
  webhook now lives in a DB setting (env fallback), so no .env edit needed.
- **Full artist intake + Wikibase-ready techniques + mentorship** — the artist
  submission form now matches the directory questionnaire (identity, optional
  demographics, primary discipline, a **mentorship** section, training,
  recognition), with the technique matrix as three multiselects drawn from a
  controlled vocabulary (central/techniques.py) whose entries carry a stable id +
  gbo class — the seam for the upcoming Wikibase section. Intake framework gained
  section headers, select, and multiselect field types.
- **Intake sheets + Discord approvals** — public forms (Explore → Submit) for
  artists, studios, and events that write pending rows through the approval gate
  (extensible: add a FORMS entry for any type). Each submission posts to a Discord
  channel (DISCORD_WEBHOOK_URL) with **one-click Approve/Reject links** — signed
  (HMAC over table+row) and handled by /api/moderate, so an admin approves straight
  from Discord. Opportunities notify too; contact details stay private.
- **Opportunities calendar view** — a month-grid calendar (accessible HTML, brand
  styled) with month navigation and a Calendar/List toggle; each opportunity sits
  on its deadline day (amber) or violet for residencies/grants, opening on the
  nearest upcoming month.
- **Opportunities calendar + intake** — a public intake form (Explore →
  Opportunities) for open calls / residencies / grants / shows that writes a
  *pending* row through the existing approval gate; once approved they appear on a
  display page with per-item **Add to Google Calendar** links and a subscribable /
  downloadable **.ics** feed (`/api/opportunities.ics`). Admin approves them in
  ✅ Approvals like any dataset; contact details stay private.
- **Colour balancing (mat-anchored white balance)** — enroll and verify now
  white-balance each analysis frame against the mat's white before computing the
  colour histogram, so the descriptor is lighting/device invariant. Same piece
  under a warm vs cool cast converges (Δ19°→Δ2° hue in tests), improving matching;
  the recorded dominant colour is corrected too. Requires re-enrolling.
- **Capture mat → real dimensions** — enroll on the ArUco reference mat and the
  import measures the piece server-side (OpenCV): W×D×H in mm from the 30 mm
  markers, written into the fingerprint metadata and offered to the object's
  dimensions field. Printable mat served at /fingerprint/capture-mat.pdf.
- **Fingerprint apps refreshed** — vendored the latest enroll/verify capture apps
  (colour-histogram descriptor + center-crop + thumbnail storage + optional in-browser
  DINOv2). verify.html now loads the reference straight from the registry
  (verify.html?object=<id> → /api/objects/<id>/fingerprint) so anyone can verify
  anytime; matching runs in the browser. Enroll requires sign-in; the fingerprint is
  stored raw and a compact hash-bound attestation is signed into the C2PA credential.
- **Physical re-identification (object-fingerprint)** — enroll a piece's fingerprint
  in Glowtbook (Fingerprint tab → capture app → import), which rides in the manifest
  and is embedded as a C2PA assertion; verify a physical piece in Explore by matching
  a fresh capture. Capture apps served at /fingerprint/{enroll,verify}.html.
- **update.sh** — one-command git-based production update (pull → install →
  health check). Fixed mobile: the shared header is no longer hidden (it holds
  the sidebar toggle), and Explore/Admin open with the sidebar expanded.
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
