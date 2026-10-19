# The fingerprint workflow — from "open the app" to "it's on the piece"

*Companion to [FINGERPRINT-PROTOCOL.md](FINGERPRINT-PROTOCOL.md) (which covers
descriptors, calibration and thresholds). This page is about the **process**:
who you are, which piece you're working on, where the capture goes, and what
happens next — so nothing gets lost between the camera and the registry.*

## The problem this solves

The capture app used to be an island: you opened it, shot sixty frames, exported
a `.zip`, then had to find the right object in Glowtbook, upload the file, and
remember to contribute the piece so the fingerprint ended up signed into its
credential. Three hand-offs, no state, nothing telling you what still needed
doing. Now the app knows **who** you are and **which piece** you're on, saves
straight to it, keeps an unsaved session on the device, and ends with a receipt
that says what to do next.

```
  Glowtbook (signed in)                 Capture app (phone or desktop)
  ────────────────────                  ──────────────────────────────
  Objects → Fingerprint                 1  Who's saving this   ← signed in / paired
     "Capture on this device"  ──────▶  2  Which piece         ← preselected
     "Pair your phone" (QR)             3  Capture             ← rating climbs
                                        ▶  Save to «piece»     ─┐
  Objects → Contribute  ◀───────────────   receipt: strength,   │ POST /api/me/objects/{id}/fingerprint
     fingerprint signed into C2PA          dims, next steps   ◀─┘
  Explore → "Verify this piece"  ──────▶  verify.html?object=…  → record / claim
```

## Identity: one sign-in for the whole site

Streamlit's `st.login()` only works *inside* a Streamlit app, so the plain pages
(capture, verify, Seasons, graph) talk to the API, which now has its own
session: a signed token in the `gdb_session` cookie, or an `Authorization:
Bearer` header. Two ways to get one — see [`deploy/AUTH.md`](../deploy/AUTH.md):

| | How | When to use |
|---|---|---|
| **Sign in with Google** | `/api/auth/login` → Google → `/api/auth/callback` sets the cookie | Someone arrives at the capture app or Seasons page directly |
| **Pair from Glowtbook** | Glowtbook mints a 2-hour link `…/enroll.html#token=…&object=12`; the page stores the token locally | You're already in Glowtbook on a laptop and want the **phone** to be the camera — scan the QR, no second login |

The pairing token travels in the URL *fragment*, so it never appears in server
logs or referrers, and the page removes it from the address bar on first load.
Tokens are HMAC-signed with `GLASSDB_SESSION_SECRET` (falls back to the
Streamlit `cookie_secret`, then the admin token). No passwords, no new accounts.

## The capture app, step by step

1. **Who's saving this.** The page checks `/api/me`. Signed in → your name and a
   "paired" badge if you arrived from Glowtbook. Not signed in → *Sign in*,
   or *Capture without saving* (export-only, the old behaviour). If the server
   has no Google client configured for the API, the button says so and points
   you at pairing instead.
2. **Which piece.** Your Glowtbook pieces, newest first, each with status chips:
   `FP 84` (fingerprint on file, rating), `published` / `in review`, or *no
   fingerprint*. Pick one, or add a new piece by title right there (it becomes
   a normal Glowtbook object you can flesh out later). The piece from the
   pairing link / `?object=` is preselected. Re-capturing a piece that already
   has a fingerprint says so — saving replaces it.
3. **Capture.** Unchanged protocol: fill the frame, circle slowly at a few
   heights, feed the detail score, until the gauge reads **Strong**. The rail
   shows the target ("Saving to *Amber goblet*") the whole time.
4. **Save to «piece».** Builds the same export as before (descriptors + small
   thumbnails) and `POST`s it to `/api/me/objects/{id}/fingerprint?set_dimensions=1`.
   The server runs the mat measurement (OpenCV), stores **descriptors only**,
   stamps `fingerprint_at`, and copies the measured W×D×H onto the piece if the
   mat was in frame. *Export .zip* is still there as a fallback (and the zip can
   be imported in Glowtbook as before).
5. **Receipt.** Strength, views, measured dimensions, publication state, and the
   next moves: *Verify it now* (verify.html against your own record), *Open in
   Glowtbook* (deep link to the piece), *Fingerprint another piece*. If a season
   is open, it says so and links to it.

### Nothing gets lost

Every capture is stashed in the browser's IndexedDB (debounced, descriptors +
thumbnails, keyed to the piece). Close the tab, take a call, let the phone die —
when you come back, the gate shows **Unsaved capture: 41 frames for «Amber
goblet», 14 min ago → Resume / Discard**. Resume restores the frames and opens
the review drawer so you can save. The draft is cleared on save, on *Clear all*,
or on *Discard*. The Review button turns amber while there's unsaved work.

## Verify: the other half

`verify.html` has the same account chip and three ways to load a reference:

- **From the registry** — `?object=<row_id>` (the *Verify this piece* button in
  Explore). It also shows whether the served fingerprint is **bound** to the
  signed credential (`/fingerprint/verify`).
- **One of my pieces** — signed in, pick any fingerprinted piece from your
  Glowtbook; loads `/api/me/objects/{id}/fingerprint` (works before publishing).
- **From a file** — the `.zip` / `fingerprint.json`, as before.

After the verdict, the Pairs drawer offers what makes sense:

| Reference | Verdict | Action |
|---|---|---|
| my piece | Match likely | **Record verification** → a `verified` event (date, confidence, views matched) in the piece's provenance |
| public piece, signed in | Match likely | **Claim for my collection** → `POST /api/claims` (verified at ≥ 70, else pending) — the Seasons collection |
| public piece, anonymous | Match likely | *Sign in to claim* |
| any | Inconclusive / No match | guidance to line up with more reference angles |

## Glowtbook's side

- **Home** shows *Your pieces at a glance*: fingerprinted / published / in review
  counts and a table with the **next step** per piece (capture → contribute →
  wait for review → enter the season → all set).
- **Objects → Fingerprint**: status line ("On file: 84/100 Strong · 31 views ·
  DINOv2 · saved 2026-10-18"), *Capture on this device*, *Verify it*, the
  **Pair your phone** QR, the mat note, and the zip import tucked in an expander.
- **Objects → Seasons**: enter a published piece in the open season.
- **Details** gains *Colour rods / sources used* — the seed for learning colour
  by fabrication source (see [GLASS-VISION.md](GLASS-VISION.md)).
- Deep links: `/glowtbook/?page=objects&object=12` opens that piece.

## API surface the pages use

```
GET  /api/me                              who am I (200 even when anonymous)
GET  /api/me/objects                      my pieces + fingerprint + publication state
POST /api/me/objects                      {title, maker?, year?, techniques?}
GET  /api/me/objects/{id}                 one piece (+ events)
GET  /api/me/objects/{id}/fingerprint     raw fingerprint (owner only)
POST /api/me/objects/{id}/fingerprint     body = enroll .zip or fingerprint.json; ?set_dimensions=1
POST /api/me/objects/{id}/events          {event_type: verified|acquired|…, note…}
GET  /api/auth/providers · /auth/login · /auth/callback · /auth/logout
```

The public read endpoints (`/api/objects/{row_id}/fingerprint`, `…/fingerprint/verify`,
`…/image`) are unchanged.

## Local development

```bash
make seed
GLASSDB_DEV_LOGIN=1 PUBLIC_BASE_URL=http://127.0.0.1:8080 python -m scripts.dev_site   # :8080 — pages + /api
PUBLIC_BASE_URL=http://127.0.0.1:8080 make run-glowtbook                                  # :8503
```

`scripts/dev_site.py` serves the static pages and mounts the API under `/api/`
exactly like the production proxy, so the capture app, verify, Seasons and the
graph work without Apache. `GLASSDB_DEV_LOGIN=1` adds `/api/auth/dev-login`, a
fake sign-in for clicking through the flow without a Google client. Note the
camera needs `https://` or `localhost`/`127.0.0.1`.

## What the data feeds

Each saved fingerprint carries a white-balanced colour signature, up to eight
DINOv2 vectors, mat-measured dimensions and a dominant colour. Beyond
re-identification these are features: the relationship graph draws
`similar_to` edges between objects from the embedding centroids, and the
[vision plan](GLASS-VISION.md) uses the same corpus to train probes for
construction (cane / murrine), colour family and colour source. Descriptors
only ever reach the registry — the frames stay with you.
