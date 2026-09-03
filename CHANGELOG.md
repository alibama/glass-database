# Changelog

All notable changes are documented here. This project distinguishes
proof-of-concept features from production-ready ones in its docs.

## [Unreleased]
### Added
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
