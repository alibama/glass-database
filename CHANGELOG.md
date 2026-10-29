# Changelog

## [1.0.0] — C2PA conformance readiness

All notable changes are documented here. This project distinguishes
proof-of-concept features from production-ready ones in its docs.

## [Unreleased]
### Added
- **My account** (`/account.html`, in the primary menu with a red dot once signed in): everything
  a person added in one place — what is waiting on them as a numbered list of clicks, their pieces
  with a linked next step each, their entries and collection, connected accounts. The next step
  is computed once (`glowtbook.store.next_step`) and shared by the Glowtbook home table, which is
  now all links (title → the piece, next step → where it happens), and `GET /api/me/objects`
  (`next`, `url` per piece).
- **How it runs** (`/infrastructure.html` + `docs/INFRASTRUCTURE.md`): the topology (one public
  server today, a private network of lent machines being built), an honest what-runs-where table,
  why we build our own AI for members instead of renting one, and how to lend a machine. About
  gained the same note; Ways in gained *Lend a machine*.
### Fixed
- **Everything moves to https.** The :80 vhost now redirects to https (ACME path excepted),
  the proxy snippet sends HSTS, the API redirects any request that arrives with
  `X-Forwarded-Proto: http` when `PUBLIC_BASE_URL` is https, and the session cookie is always
  Secure on an https site. Plain-http pages used to look signed out (the Secure cookie was not
  sent), which broke sign-in. `a2enmod rewrite` is part of the installer.
- **The capture rating was a frame counter.** Without a compass (laptops, iOS without motion
  access, Android browsers that send no heading) every auto shot opened a new coverage cell, so
  the rating read 100% at about 71 shots whatever you pointed at. Now a new cell needs a view
  that differs clearly from every shot so far, coverage credit without a compass tops out at 75%
  (the rating at "Strong", never "Verified-ready"), and detail and robustness only count in
  proportion to coverage. The fingerprint document records `scoring: v2`.
### Changed
- **Register Your Piece, the first screen**: choosing a piece now shows its one next step right
  under the list — *Enable camera and register it* for a piece without a Glass ID, or the piece's
  real next step (publish, enter, confirm) when it already has one, with *Register it again* as the
  quieter option. Step 3 is retitled after the chosen piece and says when none is chosen.
- **Register Your Piece**: the Lens overlay is off by default (it dimmed the view and hid the
  progress) and lighter when on; the gauge reads as a percentage with a plain line under it
  (*62% captured · ready at 75%*); the three small bars are hidden on phones; the Review drawer
  puts the actions first and the shots behind *See the shots*; the receipt links to Publish and
  My account.
- **Promote work and collections** — a Share kit on every entry, published piece, shared
  collection and 3D Photo: Copy link, Save image (a 1080×1350 share card on the paper ground),
  Share… (the phone's share sheet, where Instagram lives), Facebook, Bluesky and an Instagram
  tip. Links hand out an Open Graph landing page (`/api/social/share/<kind>/<id>`) so previews
  show the card. **Bluesky is connected once**, in Glowtbook → My profile, with an app password
  stored encrypted (Fernet, key from `GLASSDB_SOCIAL_KEY` or the session secret); the Share
  button and the publish step then post for you, card attached, links clickable. **Public
  collections**: a collector opts in to an unguessable link (`/collection.html?c=…`) with its
  own card, and can withdraw it. `central/social.py`, `central/cards.py`, `api/social.py`,
  `docs/SHARING.md`. Entry share cards moved to the paper palette.
- **Seasons, first live game feedback** — a ranked **standings** list with thumbnails at the top
  of the entries (visible from the first entry), the homepage shows the season's pieces as
  pictures ranked by votes with the counts and closing date, the games page says how to get in
  (a Google sign-in, nothing to install; a third column for shops and hosts) and always states
  how many votes everyone gets and when voting closes. Seasons created before awards existed
  get their theme's default categories on first read, so the categories section is never empty
  for an open season. `/games`, `/events`, `/register`, `/graph`, `/studios`, `/shop`,
  `/shop-costs` and `/splat` work without `.html` (Apache redirect + dev server route).
- **Seasons: awards, rarity and rules (first draft)** — each season has award categories, voted
  (Best stem, Best hook, Best colour…) or measured from the capture mat (Biggest pumpkin, and a
  solo-made variant); one pick per award per person, no voting for your own piece. Every entry
  has a rarity by rule: common, uncommon (fingerprinted), rare (fingerprinted edition of 3 or
  fewer), legendary (the maker's one-of-one per season, with its legend written, revealed only
  when a collector claims it; "N legendaries still out there" in the hero). Collector points
  1 / 2 / 5 / 20 with a Points board and a Legend hunter badge; five entries per maker per
  season. Rules of play on the page and in `docs/SEASONS.md`; admin presets per theme;
  solo / legendary flags on the Glowtbook entry form; `GET /seasons/{slug}/awards`,
  `POST /seasons/{slug}/awards/{category}/vote`.
- **3D views (prototype)** — `/splat.html` shows a Gaussian splat of a piece, rotatable, with
  a procedural demo pumpkin so testers see it work before they own a scan. Makers train the
  splat on their own phone (Scaniverse, Polycam) or laptop (Brush, OpenSplat), crop it in
  SuperSplat, and upload it for one of their Glowtbook pieces (`POST /api/splats?object=`,
  .splat/.ply/.ksplat, 150 MB cap, one per piece); the share link `/splat.html?s=<id>` opens
  it for anyone. Splats are pictures: never signed into credentials, never fingerprints.
  `docs/SPLATS.md`.
### Changed
- **One menu, everywhere** — the primary menu is defined once in `central/nav.py`; static pages
  carry an empty `<ul id="menu" data-nav>` that `site.js` fills, the blog generator and the
  Streamlit brand render the same list, and `tests/test_nav.py` fails if any copy drifts. The
  Streamlit apps now show the same six items as the rest of the site (Admin appears only
  inside the admin console). The tagline is gone from the header so the menu flows on one line;
  it stays in the homepage hero.
- **Named after what people do** — the site's words follow `docs/NAMING.md`: Find Artists &
  Studios (was Registry/Explore), Who Worked With Whom (graph), Jobs, Calls & Classes
  (opportunities and boards), For Researchers (datasets, API, CC BY, under one heading), Glass ID
  (fingerprint/C2PA, now fine print), Register Your Piece (enrol) and Check a Piece (verify),
  Add to My Collection (claim), Photo Mat (capture mat) with "walk around your piece until the
  bar fills up", Collecting Events (seasons; this one is Court of the Gourds), Shows & Sales
  (maker-hosted events), 3D Photo (splat), Studio Cost Calculator and Studio docs. The tagline
  "A home for every piece of glass and the people who make it." sits under the brand on every
  page. URLs, code and API paths are unchanged. The homepage opens with three doors (I make
  glass / I collect glass / I'm just curious), every button has one sentence under it, and a
  two-minute How-it-works video has its placeholder.
- **New theme: paper** — the site moves from the dark amber "Field Data" look to a light,
  editorial one: paper ground (#F4EEE4), ink text, one red accent (#B3261E), Fraunces headings,
  Inter body, IBM Plex Mono labels with "№" section numbering, square ink-bordered buttons and
  hairline rules. `deploy/paper.css` is the shared stylesheet (served at /paper.css); the
  homepage is rebuilt on it as a long-scroll page (hero + claim strip, registry index strip,
  Events & collecting, Open data, Field notes, Glowtbook, Barcoding (alpha) with tabs and the
  tester signup, Seasons calendar, Visualizations & shops, About, Ways in, dark footer) with a
  sticky header, a season notice bar fed by /api/seasons, live event counts and the sign-in
  chip. The blog generator, Seasons/Events/Register pages, graph, map, shop pages, the capture
  apps' gate screens and the Streamlit brand theme follow the same tokens; the camera HUD stays
  dark by design. The sign-in chip that `site.js` injects uses the paper tokens too. The old
  `field-data.css` is removed: no page references it any more.
- **Barcoding is labelled alpha, for testers** — on the homepage (with a tester signup that
  lands in the newsletter list under source `alpha-testers`), in the capture and verify apps and
  in Glowtbook's Fingerprint tab, so the state of things is clear: enrol and verify work,
  thresholds are still being calibrated, a verdict is evidence not proof.
- **"Ways in" on the homepage** — nine doors into the project (add or update a listing, host an
  event, alpha-test the barcode, enter a season, map a lineage, document a shop, write a study,
  review the vocabulary, contribute code), linked from the quick links as *Get involved*.
  `docs/ENGAGEMENT.md` designs the profile-update push (claim a listing by email match, edit in
  place through the gate, outbound mail) and answers the CMS question: keep the API-first core,
  add Markdown pages, no Drupal.
- **Explore keeps people's names and birth dates out of the per-row view** — in the Datasets
  view, name columns (artist_name, artist, led_by, *_name for people) and date-of-birth columns
  (birth_year, dob, death_year…) are withheld from the table, the filters and the CSV download;
  birth years may still feed the aggregate charts (decade histogram), names never do. Website
  links stay so readers can reach a maker directly. A 🔒 line says what's withheld.
  (`explore/dataclean.py: person_identifying`.) The public API is unchanged.
- **About section on the homepage** — the project's origin (started by Jason Bauer to look at
  glass from the craft and the capital side; now open source), short bios for the founder and
  the co-founder/maintainer, and an open invitation to the field. Linked from the quick links
  and the footer.
- **Homepage: events up front, nav with room to breathe, CSS that updates** — an *Events &
  collecting* section directly under the hero (three tappable steps, a live count line, Add-a-find /
  See-the-events / Host-an-event buttons), an "Add a find to my collection" hero button, and a
  quick-links pill row under the main nav. Apache now caches CSS/JS for 5 minutes with
  must-revalidate instead of a day — a day-long cache left people on an unstyled nav after a release.
- **Five sections instead of eleven links** — the site nav is now *Explore · Blog · Glowtbook ·
  Visualizations · Shops* on every page (home, blog, Seasons, graph, map, shop pages). Barcoding and
  Seasons live under Glowtbook, the graph under Explore, the map and the shop-cost calculator under
  Visualizations; pages with children show them in a sub-nav row. The API link and Discord moved to
  the footer of every page (Discord stays in the home hero and the "Come say hi" section). The home
  page folds the Opportunities and Community sections into Explore cards and gains a Visualizations
  section; nav + footer styles are one shared "Site chrome" block in `deploy/field-data.css`
  (`scripts/build_blog.py` carries the same five links). Explore's sidebar links to the graph.
- **Readable Streamlit widgets** — `.streamlit/config.toml` is now a dark base theme in the brand
  palette, so dropdown menus, date pickers, tooltips and dataframes (rendered outside the CSS's
  reach) no longer fall back to light-on-light; the "faint" text tone was lifted to clear 4.5:1
  on the dark ground across the site and the apps.
- **Maker events** — `api/events.py` + `central/events.py` (two-stage approval: host, then admin)
  are in with their tests: published registrations list newest first deterministically, event
  creation no longer passes `title` twice, and the tests mint session tokens after the fixture
  sets the secret. The public `/register.html` page is still to come.
### Fixed
- **Signed images were unreadable with current c2pa-python** — `claim_generator_info`
  carried an extra `specVersion` key; c2pa-rs 0.91+ (c2pa-python 0.37.12 / 0.38) decodes
  that structure strictly and then failed to read the claim back ("claim could not be
  converted from CBOR: unexpected trailing data"), which broke every C2PA test in CI and
  meant freshly contributed images showed no credential in Explore. The spec version is
  now asserted as `c2pa_spec_version` in the `org.glassdatabase.provenance` assertion and
  `claim_generator_info` holds only name/version. Objects contributed since the regression
  should be re-contributed to get a readable credential.
- **"Sign-in isn't configured" on hosts running Python 3.10** — the API read
  `.streamlit/secrets.toml` only with the stdlib `tomllib` (3.11+), so on an Ubuntu 22.04
  server it silently saw no `[auth.google]` and the capture app's Sign in returned 503. The
  reader now falls back to `tomli` / `toml` (the latter ships with Streamlit), honours
  `GLASSDB_SECRETS_PATH`, and `GET /api/auth/providers` plus `deploy/doctor.sh` report *why*
  sign-in is off (file, parser, `[auth]` tables — no values). The 503 message says the same.

### Added
- **Maker events with purchase registration** — a maker hosts an event in Glowtbook → Events
  (a sale, a demo, an ornament day at a gallery; venue, dates, piece type, fingerprinting
  none/optional/all) and gets a link + QR for the table. Buyers sign in at `/register.html`,
  pick the event and register the piece (photo optional, piece number optional, names private
  by default); the piece also lands in their Glowtbook ready to fingerprint. Two-stage review:
  the host approves (bulk) in Glowtbook, then Admin → Review queue → **Event registrations**
  publishes to the registry with provenance (created at the event by the maker, acquired by
  the buyer). Events work by link immediately; the public directory at `/events.html` lists
  them once an admin approves the listing. `central/events.py`, `api/events.py`,
  `docs/EVENTS.md`.
- **A blog, featured on the homepage** — Markdown posts in `blog/posts/` rendered by a
  dependency-free generator (`scripts/build_blog.py`, `make build-blog`) into `deploy/blog/`
  (index, one page per post, RSS `feed.xml`, `posts.json`) in the Field Data style, with
  readable pipe tables, older/newer navigation, a subscribe form and Article JSON-LD. The
  generator also rewrites the "From the blog" block that now sits directly under the
  homepage hero, so the latest post is always the featured card; "Blog" is second in every
  page's nav and `deploy/install.sh` builds it on deploy. First post: *Blowing Glass in
  Lima*, a theoretical study of a hot shop's economics. How to add a post: `blog/README.md`.
- **The capture app is now part of the site** — it knows who you are and which piece
  you're on. Sign in with Google (`/api/auth/login`, same OAuth client as Glowtbook, one extra
  redirect URI) or **pair your phone** from Glowtbook → Objects → Fingerprint with a QR code
  (a 2-hour token in the URL fragment; no second login). A 1-2-3 gate (who / which piece /
  capture), a *Save to «piece»* button that writes the fingerprint straight onto the object
  (`POST /api/me/objects/{id}/fingerprint`, mat dimensions copied across), an unsaved-draft
  safety net in IndexedDB with Resume/Discard, and a receipt with next steps (verify, open
  in Glowtbook, enter the season). Verify gets the same sign-in, a *my pieces* picker, and
  post-verdict actions: *record verification* on your piece or *claim* a public piece for
  your collection. Glowtbook adds a pieces-at-a-glance dashboard, deep links
  (`?page=objects&object=12`), a *Colour rods / sources* field, a Seasons tab and an
  AI-suggest-traits hook. Under the hood: `central/sessions.py` (signed tokens),
  `api/auth.py` + `api/me.py`, `glowtbook/store.py` (the per-user store, shared with the
  API), `deploy/site.js` (client helper), `scripts/dev_site.py` (`make run-site`: pages +
  API together, as the proxy serves them). Docs: `docs/FINGERPRINT-WORKFLOW.md`,
  `deploy/AUTH.md`.
- **The relationship graph does something now** — `central/graph.py` links the whole
  registry: artists, techniques, studios, **published pieces** and **collectors**, with
  `made_by`, `uses`, `based_at`, structured `studied_under` (a new **Lineage** intake form,
  Explore → Submit → Lineage, reviewed like everything else; free-text mentions stay as a
  `text-match` fallback), `similar_to` (DINOv2 fingerprint centroids, cosine ≥ 0.85) and
  `collected_by` (verified season claims). Only approved rows take part. New endpoints:
  `GET /api/graph.jsonld` (JSON-LD knowledge-graph export), `/api/graph/neighbors`,
  `/api/graph/path` (120 s cache). `/graph.html` is now a Field Data page: legend, type/edge
  toggles, Lineage mode, "How are they connected?" path finder, a node panel with image
  thumbnails and grouped neighbours, `?focus=`. docs/GRAPH.md.
- **Local vision model ("seeing glass")** — `glowtbook/vision.py` asks a local Ollama
  vision-language model (default `qwen2.5vl:7b`, set OLLAMA_URL / GLASS_VISION_MODEL) to
  suggest techniques, SKOS style traits, colour and construction (cane / murrine / incalmo)
  for an object photo, strictly from the registry's closed vocabularies — *features, not
  verdicts*: no maker, origin or value, unknown labels go to `unmapped`, confidences clamped,
  never raises; falls back to the fingerprint's own colour/size metadata when no model is
  configured, and `knn_suggest` votes labels from nearest DINOv2 fingerprint centroids (the
  first "Stage 1 probe"). `python -m scripts.export_vision_corpus` writes approved objects
  + images as a CC-BY corpus (`corpus.jsonl`, optional ShareGPT `train.jsonl` for an
  Unsloth / LLaMA-Factory LoRA). Design, staged plan, data/ethics and hardware notes in
  docs/GLASS-VISION.md.
- **Stage 1 of "seeing glass" is real: suggestions from look-alike pieces, no model needed** —
  `central/vision_index.py` builds an in-memory k-NN index over the approved registry's DINOv2
  fingerprint centroids (cached five minutes, numpy when present, pure python otherwise) and
  `suggest_for_fingerprint()` votes techniques and SKOS traits by facet from the nearest public
  pieces, with support counts and the neighbours themselves. Glowtbook → Objects → Style gets
  *Suggest from similar pieces* for any fingerprint with embeddings — thumbnails of the
  neighbours, the voted labels, and *Apply majority traits* into the pick-lists (the Apply flow
  now stages labels for the rerun, which also fixes the Ollama path's apply). The promised
  measurement exists: `python -m scripts.eval_vision_probe` runs leave-one-out over the registry
  (or a `corpus.jsonl`) and prints + writes per-label precision/recall/F1/support for techniques
  and every trait facet. Tests: `tests/test_vision_index.py`.
- **Seasons — seasonal games for makers and collectors** — themed calls for artists
  ("Pumpkin Patch 2026") where makers enter pieces they've published through Glowtbook,
  collectors scan them with Verify and **claim** them (fingerprint match ≥70 verifies on the
  spot), signed-in visitors cast a handful of votes, and it all rolls up into leaderboards
  (people's choice, most collected, studios, collectors) and badges. New `central/seasons.py`
  (tables seasons / season_entries / season_votes / season_claims / season_badges), API
  routes under /api/seasons + POST /api/claims + GET /api/me/collection + a 1080×1350
  share-card PNG with QR per entry, the public /games.html page, a '🎃 Seasons' admin
  section, demo objects + season in the seed, and docs/SEASONS.md (game design + runbook).
- **The claim loop closes on the maker's side** — Glowtbook → Objects → Seasons lists the
  claims on a piece (collector, scan confidence, date, status) with **Confirm / Reject** for
  the pending ones, owner-only (`seasons.confirm_claim`, same publisher check as entering);
  the Home dashboard gets a *Claims* column and a "confirm N claim(s)" next step. New
  `GET /api/me/claims` (the maker's inbox, for the capture/verify pages). Best-effort
  Discord nudges from `central.seasons` when a claim lands and when a season opens or
  starts voting (never fatal).
- **Studio open-data registry (multi-studio)** — a front-end 'Publish your studio's
  data' form on the studios map submits a studio's open-API URL (POST /api/studio-
  sources); it lands **pending** and is only polled after admin approval (SSRF-safe:
  https-only + reject private/loopback hosts). GET /api/studio-data.json aggregates
  every approved source (15-min cache each) + the env source, with totals; the map
  shows live-data studios with a glowing marker + firings/energy popup. Admin gets a
  '🛰 Studio data' approve/reject section. docs/STUDIO-DATA-CONTRACT.md defines the
  API a studio app must expose.
- **Live open data from a partner studio** — GET /api/studio-data.json proxies a
  studio's open kiln/furnace API (STUDIO_DATA_URL in .env) with a **15-minute cache**
  (polls the studio at most once per window; serves last-good on failure). Homepage
  gets a 'Live from a working studio' section showing firings / energy / cost with
  CC-BY attribution + source link, feeding the cost estimator.
- **Operating-cost estimator** — /shop-costs.html: a client-side calculator that
  estimates monthly running cost from each device's power, hours, operating temp, and
  duty cycle, against editable utility rates; device presets (furnace/glory hole/
  annealer/…), per-utility breakdown, all in-browser. Linked from the shop hub + nav.
- **Open shop documentation** — a /shop.html hub with the worked-example diagrams
  (electrical one-line + P&ID) shown as blueprints-on-paper, copy-paste AI prompts
  (scene, one-line, P&ID, BOM), and a parts reference. A **Shop** submission type
  (Explore → Add → Shop) lets people publish their shop (description, diagram SVGs,
  BOM, safety notes). Homepage gets a shop section highlighting the one-line SVG.
  deploy/shop-prompts.md + deploy/SHOP-PARTS.md + the diagram generator vendored.
- **Readability fixes** — dark, high-contrast inputs, selectboxes, dropdown popovers,
  multiselect tags, file-uploader, and pills (selected = dark text on amber) so nothing
  renders light-on-white; robust to the theme base. Explore's 'What are you submitting?'
  dropdown is now button pills.
- **Site nav on the map & graph** — /studios.html gets a top nav bar and /graph.html
  gets site links, so those full-screen pages link back to Explore/Glowtbook/API/etc.
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
