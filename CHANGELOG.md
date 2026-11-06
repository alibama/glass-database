# Changelog

## [1.0.0] — C2PA conformance readiness

All notable changes are documented here. This project distinguishes
proof-of-concept features from production-ready ones in its docs.

## [Unreleased]
### Fixed
- **Over https, `/record/<gid>` and every pretty URL were 404s on production.** certbot's
  :443 vhost was copied from the :80 one before the shared Apache snippet existed, so it
  carried its own stale rules and never saw a route added since; http redirected to https
  first, so nothing noticed. The installer now adds `IncludeOptional
  /etc/apache2/glassdatabase-proxy.conf` to every enabled vhost for the domain that lacks it
  (backup, `configtest`, revert on failure). The wiki's references and the Glass Database ID
  property's formatter already point at `/record/<gid>`; they resolve once this is deployed.
- **The Directory spoke to the reader.** Tables ingested from a form carried the form's
  questions as labels ("What's your Ethnicity?", "Birth Year (What year were you born?)")
  into the cards, the filters and the lock line. Labels are now nouns
  (`central.fieldnames.clean_label`: the words before the question, no parenthetical, and
  the thing a bare question asks about), ethnicity, gender, age and the like never appear
  per row or as a filter (`is_demographic`; the aggregate charts may still count them), the
  record pages apply the same rule to a row's fields, and "where were you born and where do
  you reside" reads as a place, not a date of birth. A form's grid question, which lands as
  one column per row ("Hot Glass & Flameworking Techniques [Offhand Blown Glass]" … sixteen
  of them, each holding "Primary Focus", "Secondary" or "Occasional Use"), is one
  **Techniques** filter whose choices are the rows, one composed field on the card and the
  record page ("Offhand Blown Glass (primary), Cane & Murrine (occasional)"), and searchable
  by the row's name; each such column is labelled by its row, and no table ever has two
  columns with one name again (the table view had crashed on sixteen "Techniques").
- **Links into the Pieces view found page one only.** `#obj-<id>` anchors became
  `?view=objects&piece=<id>` (`central/links.py`), which opens the view on that piece; a
  search by name from a wiki item, a record page or the graph opens the Directory on the
  table that has the row (`?q=` picks the table, `?table=` names it).
- **A changed formatter URL stood beside the old one.** Pushing the properties added a second
  *formatter URL* statement when the value changed; it now edits the existing statement in
  place (one twin, one formatter per property). P3 on the live wiki carries both; the older
  `/api/wikibase/go/$1` redirects to the same page.
- **"See the standings" on My account led nowhere.** The links went to `#e-<id>`; the
  Collecting Events page scrolls to `#entry-<id>`.
- **The second real push failed on every item with coordinates.** The pusher wrote a coordinate's
  value type as `globe-coordinate`, the property's datatype, where the wiki wants `globecoordinate`;
  219 studios came back `Data value corrupt`. Fixed, and two smaller refusals from the same run with
  it: a URL with square brackets or spaces (a Facebook event link) is percent-encoded at staging and
  at push time, and when the wiki's spam blacklist refuses a link (a link shortener), the item goes
  up without its outside links and its row says which were left off, instead of failing whole.
- **Wikibase runs could overlap.** A run's heartbeat only beat on log lines, and a push logs
  every 100 items (about three minutes), so the page declared a live push *lost* and let the
  next one start; three pushes then wrote the same items at once, which showed up as
  `failed-save` edit conflicts and duplicate items. Now every item and every request to the wiki
  beats, a run that finds itself marked lost stops itself, a run from before a service restart is
  recognised as dead, the start gate takes the write lock, and the Runs section has a *Stop*
  button (`POST /api/wikibase/runs/{id}/stop`). Edit conflicts are retried; the log names the
  real reason behind a generic save error; *Pull* reports Glass Database IDs that sit on more
  than one item, with the merge link for a person (the loader still never merges or deletes).

### Changed
- **Pieces are cards you can filter by maker.** A search box and a maker dropdown sit above
  the grid, twelve pieces show at a time with *Show more*, the maker's name on each card
  filters to everything by them, a *Record* button goes to the piece's permanent address,
  and the card data (images, credentials) is built for the page shown only.
- **The Directory shows cards, not a table.** `/explore/` now lists a table's rows as cards,
  three to a row and twelve at a time with a *Show more* button, each with the name, what it
  is and where, up to four public columns, and buttons to its record page, its maker card, the
  map or its website; a search box and up to three filters sit above them. The charts, the
  map and the table with its CSV download moved into expanders below. Names now appear on the
  cards (every row has a public record page under the same name); dates of birth stay out of
  every per-row view, and the table and the download keep names out as before.

### Added
- **Glass ID: look a piece up, compare it side by side, send the session for analysis.**
  Check a Piece opens on *Find the piece*: by Glass ID number, title or maker, from the
  recent chips, or *by photo* — the camera runs with no piece loaded and *Find it* asks the
  server which registered pieces look most like the shots (`POST /api/glassid/candidates`:
  `central/glassid.py` runs the page's own matching rules against every public piece and
  the caller's own, and returns each candidate's confidence, verdict, per-descriptor
  breakdown, the shots and reference views that carried the score, the mat size check and
  the reasons in words). The Pairs drawer opens on a compare panel: the registered photos
  beside the new shots, the score broken down by AI texture / colour / shape and views
  matched, *Same piece* / *Different piece*. Register Your Piece runs the same check before
  a piece gets a new Glass ID and asks before registering a look-alike. *Send for
  analysis* at the bottom of either drawer packs the session (photos, descriptors,
  candidates, decision with a note, mat, device and app version) and posts it to
  `POST /api/me/captures`; the server keeps it under `data/capture_manifests/`, runs the
  Photo Mat detection on the pictures, and lists it for administrators on `/captures.html`
  (open, annotate, download); `python -m scripts.pull_capture` pulls sessions down with the
  admin key from `.env`, and `python -m scripts.glassid_bench` scores every session against
  every other. `docs/GLASS-ID-MATCHING-ASSESSMENT.md` says what the matching can and cannot
  tell today — four clearly different cubes never reach *Match likely* on colour, but a
  white and an amber cube score 49–60 against each other because the colour histogram
  describes a pale piece by its background, and siblings and the AI descriptor have no real
  measurements yet — and what captures are needed. The Python descriptor port is checked
  bit for bit against the page's JavaScript in the tests.
- **A page for every Glass Database ID, and the wiki now cites it.** `/record/<gid>`
  (`deploy/record.html` over `GET /api/records/{gid}`, `central/records.py`) shows a key's
  source row, the staged statements with their item values linked, the wiki and Wikidata
  links, and what else is tied to it (a maker's pieces, entries and lineage, a studio's makers,
  a piece's maker). The staging step sets every item's page to its record page, so the
  reference on every statement resolves instead of landing on a Directory search, the Glass
  Database ID property's formatter URL is `/record/$1`, and `/api/wikibase/go/{key}`
  redirects there. `GET /api/records?q=` finds a record by name.
- **Maker cards.** `/maker.html?m=<gid>` and the second section of *My account* show a
  maker's trading card (`GDB.makerCard`, `central/makers.py`): studio and techniques, pieces in
  the record and how many carry a Glass ID, collecting events, votes and collections, honours,
  lineage, a **bio completeness** bar ("Bio 54% complete") with the empty fields and where to
  fill each one, and a **knowledge score** out of 100 (forty for the bio, twenty for pieces,
  ten for the Glass ID share, ten for events, ten for recognition, ten for links out). The
  **career record** follows a maker across every collecting event: entries, votes, best rank,
  awards led at the close, prizes and badges, season by season; prize winners are now stored
  as data (`season_prizes.winner_json`, `seasons.prizes_won`). `GET /api/makers/{gid}`,
  `/api/makers/name:<name>`, `/api/makers?q=`, and `GET /api/me/card` for the signed-in maker.
- **The numbers.** `/stats.html` (`GET /api/stats`, `central/stats.py`) counts what the record
  holds: pieces, Glass IDs and Content Credentials, makers and studios, accounts, lineage;
  the collecting events; what the wiki migration has staged and pushed by kind, how many items
  carry a Wikidata id, what the wiki reports about itself, and the Wikidata lookup's statuses;
  and the makers the record knows best (`GET /api/stats/makers`: knowledge, most complete
  bios, most pieces, most events), every named maker scored the same way.
- **Prizes on a collecting event, and the boards to back them.** Every event now carries a
  list of prizes an administrator edits any time from `/events-admin.html` (a textarea on the
  create form, then a block per event: title, what it is awarded for, what the prize is,
  sponsor and link, and the winner; `POST/DELETE /api/admin/events/{id}/prizes[/{pid}]`).
  What a prize is awarded for is one of fourteen kinds or one of the event's awards: the shop
  with the most different makers entering, the maker with the most pieces entered with a
  Glass ID, the most-voted piece, the collector with the most points, the best first-time
  maker, the first piece in, a judges' pick named by hand… The standings come straight off
  the boards (`GET /api/seasons/{slug}/prizes`, and on the public page under *What's on the
  table*), and closing the event writes each leader in as the winner. The defaults for a
  theme are recognition only: *Most makers from one shop*, *Most registered pumpkins*, *Crowd
  favourite*, *Collector of the event*. New **Makers** board (registered pieces first); the
  **Studios** board groups a shop's spellings together, counts its makers and lists
  *Independent* last; the Points board counts studios held.
- **The shop a piece is entered from is asked for, offered and shown.** The one-click entry on
  *My account* now has a studio field (prefilled with the maker's last, with the Directory's
  studios and the event's names to pick from, `GET /api/seasons/studios`), the Glowtbook form
  says why it matters, every entry card shows its studio, and an administrator can set an
  entry's studio right from the entries table (`POST /api/admin/events/entries/{id}`), so one
  shop is one name on the board.
- **The Lima study opens on Lima.** *Blowing Glass in Lima* now carries a photograph of the
  Costa Verde from Barranco, looking north to Miraflores (David Almeida, CC BY-SA 2.0, via
  Wikimedia Commons), as its picture on the blog index and the homepage and as a captioned
  figure at the top of the post; the blog's pages now carry Open Graph tags (the post's
  picture is the link preview) and the feed's links and pictures are absolute.
- **The migration asks Wikidata what it already knows.** A new step between Duplicates and
  OpenRefine (`central/wikibase/wikidata.py`, the page's *Look up on Wikidata* button, `python
  -m central.wikibase wikidata`) finds the Wikidata item for staged people, places, countries,
  museums, schools, studios, funders, awards, shows and the colour, occupation, technique and
  trait vocabularies, and writes it into the same `wikidata_qid` the OpenRefine import fills,
  so the next push carries it to the wiki. People are matched against every person Wikidata
  lists in the glass occupations (one bulk read, about 2,100 of them); everything else goes
  through Wikidata's own reconciliation service with the class and country the kind calls
  for. The rules are narrow: exact name, the region in the description for a town, glass in
  the description for a studio, a technique or a person found only through the service. What
  passes once is `matched`; namesakes are `unsure` and wait in the page's new Wikidata section,
  where an administrator picks one, types a Q id or says none (`POST
  /api/wikibase/wikidata/{key}`); ids that were already there are `known` and never asked
  about; a person's decisions survive a re-run. The run reports the museums Wikidata says hold
  our people's work, the websites and Instagram accounts it has that we lack, and the glass
  artists it ties to our own institutions and places that have no row here
  (`/api/wikibase/wikidata/suggested`), each with its link. Nothing is written to Wikidata.
  An outside panel of local models (lexflow's `run resolve`) can leave a suggestion on an unsure
  row (`{"suggest": {...}}` on the same admin route); the page shows the panel's answer, its
  reasons and each model's vote beside the candidates, and a person still decides. The same
  tool's web search can leave a proposal on a record with nothing on Wikidata (`{"enrich": …}`
  on `POST /api/wikibase/items/{key}`: a website, an Instagram account, a city, each with the
  pages it was read from), listed under the Wikidata section; *Add the website* and *Add the
  account* put the statement on the item as a reviewer's edit (`add` on the same route), which a
  re-stage keeps.
  Also fixed on the way: the hand list of known cities was keyed with commas that the
  normaliser strips, so no city ever matched it; Tacoma, Seattle, Venice and the rest now get
  their ids at staging time.
- **The console's last sections are pages; the Streamlit admin can be switched off.**
  `/events-admin.html` creates and runs collecting events (title, theme, dates, rules, prize
  note, votes per voter, the award categories as `title | vote|measured | solo` lines with the
  theme presets; open, start voting, close with the end-of-event badges, edit the words and
  dates, delete a draft, pull an entry). `/datasets.html` is every table in the registry: pick
  one, page through its rows, search, edit a cell, add a row, import a CSV that updates by
  `_row_id` and adds the rest, export the whole table. `/analytics.html` is usage: 7, 30 or 90
  days, views and visitors by day as a plain inline SVG, by surface, view, event and country,
  and the newsletter CSV. The routes are `api/admin_data.py`, every one behind `require_admin`;
  every edit, added row and imported row is approved through `central.approvals` with the
  administrator as the reviewer, exactly as the console's grid did, and nothing is deleted
  (a row that should not be public is rejected). `/subscribers.csv` now also takes a signed-in
  administrator. The five admin pages share one sub-nav (Review queue · Collecting Events ·
  Tables · Usage · Wikibase); the Streamlit console's sidebar points at them and every section
  says where it went; the Duplicates tool is superseded by the Wikibase dedupe step; the
  Discord moderation page links to the Review queue. `tests/test_admin_pages.py` covers the
  gates, the event lifecycle, the cell edit flipping `_approvals`, the CSV upsert and the
  page markup.
- **The public record moves to glass.wikibase.cloud, and the migration is a page.**
  `central/wikibase/` is the pipeline: the model (55 properties with their Wikidata twins, 29
  classes, the vocabularies from code), **stage** (the site's tables → one staged item per
  person, studio, school, museum, funder, piece, show, collecting event, place and term; only
  approved rows and public columns; emails, prices, buyers and collectors never enter),
  **dedupe** (clusters by name, city, website and Instagram; one canonical per cluster, the
  rest fold into it and keep their Glass Database IDs), the **OpenRefine** round trip (CSV out,
  reconcile against Wikidata and against the site's own W3C reconciliation service at
  `/api/wikibase/reconcile`, CSV back; a manifest for OpenRefine's Wikibase extension),
  **bootstrap** of the properties, a **dry run** that writes every request body to a plan,
  the two-pass **push** (bare items, then every statement with a reference; find-before-create;
  `clear` only on items we made; never deletes or merges), and **pull** (the wiki's copy into
  `wb_cache`, with drift reported). Jobs run in the background with a log in the database that
  every API worker can read. **`/wikibase.html`** shows all of it to anyone (steps, items by
  kind, clusters, runs with their logs) and gives administrators the buttons, including the bot
  settings: the bot password is saved encrypted with the site secret and never shown again.
  Collectors stay in the app by design. `docs/WIKIBASE.md` is the runbook; `python -m
  central.wikibase` the command line.
- **The Review queue is a page.** `/review.html` and `api/admin.py` do what the Streamlit
  console's moderation sections did: pieces, artist profiles, every dataset's pending rows
  (with *Approve all*), show listings and registrations, collecting-event entries and
  collection requests, harvested content, studio data sources, feedback, users and the Discord
  settings, all behind `require_admin` (an admin email or the API key). The first slice of
  retiring Streamlit; the console stays for datasets, analytics and creating collecting events.
- **Install keeps what the server made.** The deploy's rsync now excludes `data/splat_media`,
  `data/wikibase` and the Glowtbook store's `-wal`/`-shm` files, so a deploy no longer deletes
  them.
- **Pieces as trading cards, and a Go-live panel.** One card renderer (`GDB.pieceCard` in
  site.js, styled in paper.css) draws a piece the way a graded card reads: the Glass ID strength
  as the big number up top, the top image in the art window, a type line, stat rows (Glass ID,
  story, registry, event), a serial and a rarity pill. My account shows your pieces and your
  collection as cards; a shared collection page and the Collecting Events page (its entries,
  votes as the big number, and your collection) do too. **Go live** on My account
  (`/account.html?piece=N#golive`) is the one place a piece goes from registered to published to
  entered: four steps, each with its button and status on the page. Publish is now one call
  (`POST /api/me/objects/{id}/publish`), entering the open season is one click, and a photo can be
  added from the phone (`POST /api/me/objects/{id}/images`, served back at `/image`). The only
  thing that leaves the page is the one Discord notification the reviewers already got; the
  Publish line turns to *live* on approval. Every *next step* link after Register now points at
  the panel instead of a Glowtbook tab.
- **Card-sized images.** `/api/objects/{id}/image?w=640` (and the private
  `/api/me/objects/{id}/image?w=`) serve a small progressive JPEG for cards and lists, made with
  Pillow and cached on disk (`data/dip_media/thumbs`, `data/glowtbook_media/thumbs`); widths snap to
  160/320/640/960 and never upscale. The bare URL is unchanged: the full rendition with its Content
  Credential intact. Every piece card, the standings list and the Go-live thumbnail ask for the
  small one; the Collecting Events grid went from 443 KB per picture to a few tens of KB.
- **Entering a published piece in a collecting event needs no second approval.** The piece
  was reviewed when it was published, so its entry is live on the season page the moment it is
  entered (claims re-attached, badges awarded); the Discord post is now news with a link, not an
  approve button. `GLASSDB_SEASON_ENTRY_REVIEW=1` restores the review queue. Entries left
  pending from before go live on the next API call that touches seasons.
- **The capture app keeps a top image.** While you walk around a piece it keeps a full-resolution
  still of the sharpest, most detailed view, and saves it onto the piece with the Glass ID (as the
  primary image when the piece has none). The receipt shows it and continues to the Go-live panel.
- **The front page is the mission.** The homepage now carries the mission statement and the
  seven ways we pursue it, each one linked into the part of the site where it happens (studio
  docs and the cost calculator, the opportunities calendar and shows, field notes, collecting
  events with the live season line, Glass ID and Glowtbook, the connections graph, the tables and
  API). A short About keeps the `/#about` anchor, the menu and the *our own AI* note. The previous
  long homepage lives on as **The full tour** (`/tour.html`, linked from the footer). The front page's
  menu is spartan: *Find Artists & Studios* and the sign-in; *Glowtbook* and *My account* appear
  once signed in (`data-nav-show` / `data-nav-signed-in` on the menu placeholder, honoured by
  site.js; every other page keeps the full menu).
- **The walkthrough short.** The YouTube short of registering a piece replaces the "video coming
  soon" placeholder on the full tour (privacy-enhanced embed) and is linked from the front page's
  Glowtbook-authentication entry, the capture app's intro and docs/FINGERPRINT-WORKFLOW.md.
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
- **Build the equipment: a parts list for the furnace, the annealer and the glory hole.**
  `/shop-build.html` and `deploy/SHOP-BUILD.md` are generated from one JSON
  (`deploy/shop-build/bom.json`) by `scripts/build_shop_build.py`: every part with a quantity,
  a specification and a price range, for single-phase 240 V or three-phase 208 or 240 V
  service, natural gas or propane, and the furnace electric or gas-fired. The page recomputes
  the branch circuits (amps, breaker, copper), the SiC element strings new and aged, the
  annealer coils (gauge, length, surface load), the burner orifices (number drill) and the gas
  pipe size as the reader toggles the options, and offers the chosen list as a CSV. Three
  section drawings (`deploy/shop-diagrams/*-section.svg`) come from the same script;
  `tests/test_shop_build.py` checks the arithmetic against hand-worked cases and fails on a
  stale generated file.
### Fixed
- **Discord "Approve & publish" on a season entry did nothing visible.** The one-click link wrote
  to the generic approvals table while the season page reads the entry's own status column; it
  now calls the same function as Admin → Seasons (approve or reject, claims re-attached, badges
  awarded). Links already posted keep working: the signature does not expire, so re-clicking an
  old one after this deploy approves the entry.
- **"Sign-in state didn't match" on phones.** The callback no longer depends on the state
  cookie alone: the same signed state is kept server-side (`oauth_pending`, single use, twenty
  minutes), so a browser that drops the cookie between leaving for Google and coming back still
  finishes. Static pages get the session token in the URL fragment (`#session=`), which site.js
  stores and sends as a Bearer header, so the capture app stays signed in even when a browser
  drops cookies. A replay of the callback while already signed in goes to My account. Failures
  are a readable page with *Try again* (ASCII only; the raw JSON showed mojibake on iOS). The
  API sends `www.` to the one public host and the Apache snippet does the same; the installer
  adds `PUBLIC_BASE_URL` and a `GLASSDB_SESSION_SECRET` to an older `.env`;
  `/api/auth/providers` reports `secret_source` (`ephemeral` means sign-ins fail at random
  behind two workers).
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
