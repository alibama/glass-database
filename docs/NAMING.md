# Naming — what we call things on the site

Name things after what people do, not after the technology. This is the one list; every
page, app, doc and commit uses these words. URLs, code identifiers, table names and API
paths do **not** change (links in the wild keep working); only what people read does.

## Brand

**Glass Database** stays the brand. Everywhere it appears as a header, the tagline sits
under it, small:

> A home for every piece of glass and the people who make it.

## The words

| People read | Was | Explained as | Lives at |
|---|---|---|---|
| **Find Artists & Studios** (short: **The Directory**) | Registry, Explore | Who makes glass, where, and how to reach them | `/explore/` |
| **Who Worked With Whom** (short: **Connections**) | Relationship graph, Graph | Who taught whom, who shares a studio, what touches what | `/graph.html` |
| **Studios on a map** | Map | Every studio we know of, by city and country | `/studios.html` |
| **Jobs, Calls & Classes** | Opportunities, Community boards, Exchange/Jobs/Resources | Open calls, residencies, teaching, gaffer and assistant openings, suppliers | `/explore/?view=opportunities`, `?view=community` |
| **For Researchers** | Datasets, API, CC BY 4.0, Open data | Tables, exports and the read API, all under one heading so beginners never see it | `/explore/` datasets view, `/api/docs` |
| **Glass ID** | Fingerprint, barcoding, digital barcoding, C2PA, Content Credentials | A one-of-a-kind ID for your piece, like a serial number for handmade glass | `/fingerprint/` |
| **Register Your Piece** | Enrol, Enroll, capture app, capture | Walk around your piece with your phone and it gets its Glass ID | `/fingerprint/enroll.html` |
| **Check a Piece** | Verify, verify app | Point your phone at it to see who made it | `/fingerprint/verify.html` |
| **Add to My Collection** | Claim, register a find, add a find | The piece joins your collection with its story | `/register.html`, claim buttons |
| **Photo Mat** | Capture mat, printed mat, coverage mat | The printed sheet you set the piece on; "Walk around your piece until the bar fills up." | `/fingerprint/capture-mat.pdf` |
| **Glowtbook — your studio notebook** | Glowtbook | Always with "your studio notebook" beside it the first time on a page | `/glowtbook/` |
| **Collecting Events** | Seasons, Games, the season | Time-boxed collecting games with awards and a leaderboard | `/games.html` |
| **Court of the Gourds** | Pumpkin Patch 2026 | This autumn's collecting event (rename the live season's title in Admin) | current season |
| **Shows & Sales** | Events, maker-hosted events, ornament days | Where makers sell in person; buyers add what they bought to their collection at the table | `/events.html` |
| **3D Photo** | Splat, Gaussian splat, 3D view | Turn a piece in your hands on screen; your phone makes it | `/splat.html` |
| **Studio Cost Calculator** | Shop cost calculator, shop costs | What it costs to run a studio, by the month and by the piece | `/shop-costs.html` |
| **Studio docs** | Shop docs, open shop documentation | Describe a studio by its parts: gas, air, power, heat, exhaust | `/shop.html` |
| **Field notes** | Blog | Studies and essays | `/blog/` |

Fine print keeps the technical words, once, where a curious person can find them: "Glass ID
is stored as a fingerprint of the glass itself and signed into the photo's Content
Credentials (C2PA)", "Data under CC BY 4.0", and the API docs. Never in a heading, a button
or a nav item.

## Buttons

Every button gets one plain sentence under or beside it saying what happens when you
press it. "Register Your Piece" → "Walk around it with your phone; it gets its Glass ID."
"Check a Piece" → "Point your phone at it to see who made it." "Add to My Collection" →
"It joins your collection with its story attached." "Enter a piece" → "Put one of your
registered pieces into this collecting event."

## Three doors

The homepage opens with three doors, each a short path:

- **I make glass** → Get your studio notebook (Glowtbook) · Register your pieces (Glass ID) ·
  Enter a collecting event, or host a show.
- **I collect glass** → Check a piece · Add to my collection · Vote in the collecting event.
- **I'm just curious** → Find artists & studios · Who worked with whom · Read the field notes.

## Navigation

Primary nav (every page, in this order): **Find Artists & Studios** · **Collecting Events** ·
**Glass ID** · **Glowtbook** · **Field notes** · **About**. The brand links home.

Sub-navs (the crumb is the primary item, then its pages):

- Find Artists & Studios › The Directory (`/explore/`) · Who Worked With Whom (`/graph.html`) ·
  Studios on a map (`/studios.html`) · Jobs, Calls & Classes (`/explore/?view=opportunities`) ·
  For Researchers (`/api/docs`)
- Collecting Events › Court of the Gourds (`/games.html`) · Shows & Sales (`/events.html`) ·
  Add to My Collection (`/register.html`) · Check a Piece (`/fingerprint/verify.html`)
- Glass ID › Register Your Piece (`/fingerprint/enroll.html`) · Check a Piece
  (`/fingerprint/verify.html`) · 3D Photo (`/splat.html`) · Photo Mat
  (`/fingerprint/capture-mat.pdf`) · Studio docs (`/shop.html`) · Studio Cost Calculator
  (`/shop-costs.html`)

Footer columns: **Find** (Directory, Connections, Map, Jobs, Calls & Classes, For
Researchers) · **Collect** (Collecting Events, Shows & Sales, Add to My Collection, Check a
Piece) · **Make** (Glowtbook, Register Your Piece, 3D Photo, Studio docs, Studio Cost
Calculator) · **Elsewhere** (Discord, Instagram, Field notes, GitHub, About).
