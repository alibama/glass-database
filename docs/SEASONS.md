# Collecting Events — time-boxed collecting games for makers and collectors

On the site these are **Collecting Events** (the code and URLs still say seasons/games; see
`docs/NAMING.md`). A collecting event is a themed, time-boxed call for artists ("Court of
the Gourds: blown-glass pumpkins from every studio") that doubles as a collecting game.
Makers enter pieces they have already published through Glowtbook and registered for a
Glass ID; collectors buy them from the shops, **check** them with Check a Piece and **add
them to their collection**; signed-in visitors **vote**; it all rolls up into leaderboards
and badges. Think gotta-catch-'em-all, but every "catch" is a checked piece of glass in a
real collection and a sale for an independent maker. The rest of this document uses the
code's words (season, claim, verify, fingerprint) because it describes the code.

Public page: `/games.html` · admin: **🎃 Seasons** · code: `central/seasons.py`,
`api/seasons.py` · demo data: `scripts/seed_demo.py`.

## The loop

```
scan ─▶ authenticate ─▶ collect ─▶ vote ─▶ share ─▶ (someone new scans)
```

1. **Scan** — a collector opens `/fingerprint/verify.html?object=<row_id>` (linked from every
   entry card) and captures the piece in front of them. Each shot is matched against the
   maker's enrolled fingerprint; the confidence climbs as angles line up.
2. **Authenticate** — confidence **≥ 70** (with ≥ 3 views the app calls it "Match likely")
   verifies the claim on the spot. Lower scores wait as *pending*: the **maker** sees them
   in Glowtbook → Objects → *Seasons* (and a *Claims* column on the Home dashboard) and
   taps **Confirm** ("they bought it") or **Reject**; an admin can also decide from
   Admin → 🎃 Seasons. Either way the claim then counts on the boards and earns badges.
3. **Collect** — the claim joins the collector's **My collection** with maker, studio, season
   and scan score. One claim per piece per person; a better scan upgrades a pending one.
4. **Vote** — a handful of votes per season (default 3), toggleable until voting closes.
5. **Share** — every approved entry has a 1080×1350 **share card** (`card.png`) with the piece,
   the maker, the season and a QR back to the entry. Post it to an Instagram story; the QR
   brings the next visitor to the page, signed-out, with a Vote button in front of them.

### Why it serves the mission

* **An audience of collectors.** Seasons give people a reason to buy from a working shop
  *this month*, and a shelf (the collection) that makes buying the next one feel like
  progress. Collectors come back; that is what sustainable careers are made of.
* **Sales, not prizes.** Entries carry the studio and a price note ("$85 at the studio
  shop"). The game never touches money; it points at the maker's own till.
* **Authentic provenance.** A claim is a fingerprint match recorded against a public,
  credentialed object — proof of what you own without a certificate in a drawer.
* **A voluntary census.** Every season is a snapshot of who is making what, where: makers,
  studios, editions, prices, and where the pieces ended up. Open data under CC BY 4.0.

## Season calendar

| When | Season | Theme |
|---|---|---|
| October | **Court of the Gourds** | Blown-glass pumpkins from every studio |
| December | **Heirloom Ornaments** | The ones that go on the tree every year |
| Spring | **Paperweights** | Murrine, millefiori, the lampworked garden |
| Summer | **Drinkware** | Tumblers, stemware — the glass you actually use |
| Anytime | **Marbles** | Small, fast, dangerously collectable |

One live season at a time keeps attention on it; a draft for the next one can sit in the
admin meanwhile. Propose new themes on the Discord.

## Roles

| Role | What they do | Where |
|---|---|---|
| **Maker** | Fingerprints a piece in Glowtbook → publishes it → enters the season with studio, edition size, price note → confirms or rejects low-confidence claims on it | Glowtbook → Objects → *Seasons* tab (or `POST /api/seasons/{slug}/entries`; the inbox is `GET /api/me/claims`) |
| **Shop / studio** | The name on the entry; appears on the Studios board; can tell customers "scan it to claim it" | Entry's `studio` field |
| **Collector** | Buys → scans with Verify → claims → builds a collection and badges | `/fingerprint/verify.html` → *Claim* → `/games.html#mine` |
| **Voter** | Anyone signed in; N votes per season, swappable | `/games.html` Vote buttons |
| **Admin** | Creates/opens/closes seasons, can reject an entry (approves them only in review mode); backstop for low-confidence claims the maker hasn't got to | Admin → 🎃 Seasons |

## Rules & anti-abuse

* **Sign in to vote or claim** (Google via `/api/auth/login`, or a Glowtbook pairing token).
  Anonymous visitors see everything and can scan; the Vote button sends them to sign in.
* **N votes per voter per season** (`votes_per_voter`, default 3), on distinct entries; a vote
  is a toggle so people can change their mind. Votes only count while the season is
  `open` or `voting`.
* **One entry per piece per season**, and only the person who published the piece may
  enter it (resolved through `object_submissions.submitted_uid` and the Glowtbook
  `contribution` table, both via `content_hash`). An entry of a public piece is **live at once**:
  the piece was reviewed when it was published. `GLASSDB_SEASON_ENTRY_REVIEW=1` holds entries
  in a review queue instead (Admin → Seasons; the Discord link approves them). Entries left
  pending from review mode go live the next time the API touches seasons.
* **One claim per piece per person.** A claim needs a fingerprint confidence **≥ 70** to be
  `verified`; otherwise it is `pending` until the **maker who published the piece** confirms
  it in Glowtbook or an admin approves it. Only the publisher may confirm — the same
  ownership check as entering (`seasons.owns_object`, via `object_submissions.submitted_uid`
  or the Glowtbook `contribution` table) — and only while the claim is still pending.
  Only verified/approved claims count on the boards and towards badges.
* **Only public, approved objects** can be entered or claimed — the same publication gate
  as everything else (`central/approvals.py`).
* **No cash prizes needed.** Recognition: the Crowd Favourite badge, a homepage feature,
  an Instagram post from @glassdatabase. That keeps the stakes friendly and the incentive
  to game it low. If a supplier wants to put up a prize later, the boards are already there.
* Account ids never leave the API: entries expose `mine` (for the caller), never
  `artist_uid`; collector boards show names only.

## Awards

Every season has the implicit **People's choice** board (`season_votes`, N votes per voter)
plus a small set of **award categories** (`season_categories`), one row per award:

| Column | Meaning |
|---|---|
| `kind` | `vote` — one pick per voter per category; or `measured` — settled by the fingerprint mat, no votes |
| `measure` | `height` (mm) for measured kinds; the third number of the `W × D × H mm (W×D×H)` string the mat writes onto the object (`glowtbook.matdetect` → `objects.dimensions`). Unmeasured pieces simply aren't ranked |
| `solo_only` | only entries flagged *solo* (made without an assistant) are eligible |

Defaults when the admin doesn't list any (picked by the theme text):

| Theme contains | Categories |
|---|---|
| "pumpkin" | Best stem (vote) · Best colour (vote) · Biggest pumpkin (measured height) · Biggest pumpkin, solo (measured height, solo only) |
| "ornament" | Best hook (vote) · Best colour (vote) · Most likely to survive the cat (vote) |
| anything else | Best in show (vote) · Best colour (vote) |

The admin's create form has a textarea, one award per line — `title | vote` or
`title | measured | solo` (`seasons.parse_categories`). Picks live in `season_award_votes`
(UNIQUE season × category × voter): picking again **swaps**, picking the same entry again
**removes** the pick, and you can never pick your own piece (`seasons.award_vote`). Standings
(`seasons.awards`) list the top five by picks for vote kinds and every measured entry, tallest
first, for measured kinds. The page shows each award with its kind, the current leader and a
select-and-Pick control. Picks are open while the season is `open` or `voting`.

## Rarity

Every entry carries a `rarity` and the `points` it is worth to the collector who claims it:

| Rarity | Points | When |
|---|---|---|
| common | 1 | any approved entry |
| uncommon | 2 | the object has a fingerprint on file |
| rare | 5 | fingerprinted **and** `edition_size` ≤ 3 |
| legendary | 20 | the entry is the maker's **legendary** *and* a collector holds a verified/approved claim on it |

A **legendary** is one per maker per season: an edition of one, fingerprinted, with a
*legend* of at least 40 characters (`seasons.enter(..., legendary=True, legend=...)` enforces
all four). Until it is claimed the public API shows its base rarity and **does not expose**
`legendary` or `legend` at all — nobody is told which piece it is; the maker sees their own
flag in `my_entries` / `GET /api/me/collection`. The season carries `legendaries_out` (approved
legendaries without a claim — "still out there"), shown as a line in the page's hero. The
collector who claims one earns the `legend_hunter` badge, and the **Points** board
(`leaderboard()["points"]`: `{collector_name, points, pieces, legendaries}`) sums the points of
each collector's verified pieces in the season.

Computation: `seasons.rarity_of(entry, has_fingerprint, claimed)`, applied in `_entry_shape`
with fingerprint presence read from `objects.fingerprint_json` / the manifest and *claimed*
meaning `claim_count > 0`.

## Rules of play

The same text sits behind "Rules of play" on `/games.html`, in the site's words (collecting
event, Glass ID, add to your collection).

**Makers**

1. Enter your own work, made for this collecting event.
2. Up to five entries per collecting event.
3. Register a piece (give it its Glass ID) before you enter it: that is what makes it
   Uncommon or better and what the measured awards use.
4. One legendary per maker per collecting event: an edition of one, with a Glass ID and its
   legend written. Don't say which piece it is; the collector finds out when they add it to
   their collection.
5. You can't vote for your own pieces.
6. "Solo" means made without an assistant on the pipe, on your honour.

**Collectors**

1. One pick per award, plus your People's choice votes.
2. Adding a piece to your collection needs the piece in your hands (its Glass ID matches) or
   the maker's confirmation.
3. Each piece can be added to your collection once.
4. Points: common 1, uncommon 2, rare 5, legendary 20.
5. Complete a set (one piece from every studio in the collecting event) for the badge.

**Everyone:** the collecting event never handles money; buy from the maker. Admins can remove
entries or collected pieces that break the rules; disputes go to the Discord.

Enforced in code: entries per maker (`MAX_ENTRIES = 5`), the legendary conditions, no self
picks, one pick per award, one claim per piece per person. The rest is honour and admins.

## The collector card share loop

`GET /api/seasons/entries/{id}/card.png` renders a 4:5 card (Instagram story / post size)
on the Field Data ground: the piece in a rounded frame, title, maker · studio, edition and
price note, the season's theme in amber, `glassdatabase.org/games.html`, and a QR to
`{PUBLIC_BASE_URL}/games.html?season=<slug>#entry-<id>`. It is cached for an hour and only
exists for approved entries. Makers post it to announce their entry; collectors post it
when they claim. The deep link scrolls to and highlights the card, so a story viewer lands
on exactly that piece with a Vote button and a "Scan to claim" link.

## Badges

| Key | Label | Earned when |
|---|---|---|
| `first-scan` | First scan | First verified/approved claim |
| `maker-debut` | Maker debut | First approved entry |
| `five-shops` | Five shops | Verified pieces from 5 distinct studios |
| `three-seasons` | Three seasons | Verified pieces across 3 seasons |
| `set-complete` | Set complete | A piece from every studio with an approved entry in a season (≥ 2 studios); per season |
| `crowd-favourite` | Crowd favourite | Maker of the top-voted entry when the season closes |
| `legend_hunter` | Legend hunter | A verified/approved claim on a maker's legendary |

Badges are computed by `seasons.award_badges(conn, uid)` (called after claims, approvals and
close-out) and live in `season_badges` (`season_id = 0` for the global ones). The catalogue,
with its blurbs, is `seasons.BADGES` and is returned by `GET /api/me/collection` so the page
can show earned and unearned badges side by side.

## Admin runbook

1. **Create** — Admin → 🎃 Seasons → *Create a season*: title, one-line theme, dates
   (starts / entries close / voting closes / ends), rules, prize note, votes per voter.
   It starts as a `draft` (invisible on the public page) unless you tick *Open right away*.
2. **Open** — the season appears on `/games.html` as the current season; makers can enter.
   A Discord nudge posts itself when the webhook is set (theme, dates, page link); add the
   Instagram post by hand.
3. **Watch entries** — entries go live as they come in and each posts to Discord with a
   link to its card; the Seasons tab can still reject one. With
   `GLASSDB_SEASON_ENTRY_REVIEW=1` they queue here instead (image, title, maker, studio,
   edition; approve or reject) and the Discord post carries the approve link.
4. **Start voting** — entries close, votes continue (another Discord nudge). Dates on the
   page are informational; the status buttons are what gates the behaviour, so you decide
   when.
5. **Review claims** — scans under 70 wait in *Pending claims*. The maker usually gets there
   first from Glowtbook; approve the stragglers yourself if you can vouch for the piece.
6. **Close** — sets `closed`, awards **Crowd Favourite** to the top-voted entry's maker and
   **Set Complete** to collectors who covered every studio. The page shows final boards and
   the season moves to *Past & upcoming*. Feature the winner on the homepage / Instagram.

Status flow: `draft → open → voting → closed` (`set_status` allows any transition if you
need to reopen).

## The maker's inbox

A claim is only useful to a collector once the maker knows about it, so the loop closes on
the maker's side too:

* **Glowtbook → Objects → Seasons** lists every claim on the selected piece (collector,
  scan confidence, date, status), pending first. Pending ones carry **✅ Confirm** and
  **⛔ Reject**; the Home dashboard shows a *Claims* column ("2 ✓ · 1 waiting") and makes
  "confirm N claim(s)" the piece's next step. Confirming calls
  `seasons.confirm_claim(conn, claim_id, uid)`, which refuses anyone but the publisher and
  anything that is no longer pending, then runs the usual `set_claim_status` → badges.
* **`GET /api/me/claims`** returns the same list across all of the caller's published
  pieces (`{claims[], pending, confirm_in}`), so the capture/verify pages can show a maker
  what is waiting. Deciding stays in Glowtbook for now (see *What's next*).

### Discord nudges

With the webhook set (Admin → Settings, or `DISCORD_WEBHOOK_URL`), `central.seasons` posts
a plain `notify_message` — no moderation links — when:

| Event | Message |
|---|---|
| a claim lands (`seasons.claim`) | "🎃 *Collector* claimed “*Piece*” — verified by scan", or "🎃 Claim on “*Piece*” needs a look (low-confidence scan)" with who can confirm it |
| a season opens (`set_status` → `open`) | "🎃 Season open: *Title*" with theme, dates, prize note and the page link |
| voting starts (`set_status` → `voting`) | "🗳️ Voting is open: *Title*" with the closing date and votes per person |

All of it is best effort: a missing webhook or a network error never fails the claim or the
status change, and a status set to what it already is posts nothing.

## API summary

| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/seasons` | – | `{current, seasons[], badges, signed_in}` (non-draft) |
| GET | `/api/seasons/{slug}` | – | season (+ `legendaries_out`) + approved entries (`vote_count`, `claim_count`, `image_url`, `mine`, `rarity`, `points`, `solo`, `height_mm`) + `my_votes` + `remaining_votes` + `can_vote` + `leaderboard` + `awards` + `my_picks` |
| GET | `/api/seasons/{slug}/leaderboard` | – | `peoples_choice`, `most_collected`, `studios`, `points`, `collectors` |
| GET | `/api/seasons/{slug}/awards` | – | `{awards[], my_picks, can_vote, signed_in, legendaries_out}` — each award with `standings[]`, `leader`, `eligible[]` |
| GET | `/api/seasons/{slug}/entries/{id}` | – | one entry (+ `voted` for the caller) |
| POST | `/api/seasons/{slug}/entries` | sign-in | `{object_row_id, studio, edition_size, price_note, title, solo, legendary, legend}` → pending entry |
| POST | `/api/seasons/{slug}/vote` | sign-in | `{entry_id}` → `{voted, votes, remaining}`; 400 when capped/closed |
| POST | `/api/seasons/{slug}/awards/{category_slug}/vote` | sign-in | `{entry_id}` → `{picked, entry_id, votes}`; swaps / toggles; 400 for your own piece |
| POST | `/api/claims` | sign-in | `{object_row_id, confidence, report, location}` → claim row + `badges` (+ `note` when pending) |
| GET | `/api/me/collection` | sign-in | `{collection[], badges[], all_badges, entries[]}` |
| GET | `/api/me/claims` | sign-in | `{claims[], pending, confirm_in}` — claims on the pieces I published, pending first |
| GET | `/api/seasons/entries/{id}/card.png` | – | 1080×1350 PNG share card (404 unless approved) |

Domain errors are `400` with a plain sentence in `detail`; anonymous writes are `401`.
Python callers use the same functions: `central.seasons.{ensure, list_seasons, get_season,
current_season, create_season, set_status, close_season, season_object_rows, owns_object,
enter, my_entries, entries, approve_entry, vote, my_votes, claim, set_claim_status,
claims_on, claims_on_my_pieces, claim_counts, confirm_claim, collection, leaderboard,
collectors, badges_for, award_badges, summary, default_categories, parse_categories,
add_categories, categories, get_category, award_vote, my_award_votes, awards, rarity_of,
has_fingerprint, parse_height_mm, legendaries_out}`.

### Tables

`seasons`, `season_entries` (UNIQUE season × object; `solo`, `legendary`, `legend` flags),
`season_votes` (UNIQUE season × entry × voter), `season_claims` (UNIQUE object × collector),
`season_badges` (UNIQUE uid × badge × season), `season_categories` (UNIQUE season × slug),
`season_award_votes` (UNIQUE season × category × voter). Created lazily by
`seasons.ensure(conn)`, which also adds the entry flag columns to an older table; not
registered in `_datasets`, so the generic read API never serves them — only the routes above do.

## What's next

* **Confirm from the phone** — a `POST /api/me/claims/{id}` so the verify/capture pages can
  act on the maker's inbox, not just show it (today they can read `GET /api/me/claims`;
  deciding is Glowtbook-only).
* **Geographic sets** — "every shop in Virginia", "the Seattle route" — using the studios
  map so a season can be a road trip.
* **Trading via the Exchange board** — a collector with two Blue Ridge pumpkins and no
  Tidewater ornament lists a swap; the claim moves with the piece (a `transferred` event).
* **Prizes from suppliers** — once the boards have an audience, a colour-rod or tool
  supplier can sponsor a season without changing a line of the rules.
* **More season-scoped notifications** — openings, voting and claims post today; next, a
  Discord post when an entry is approved and when the boards change hands in the final
  24 hours.
