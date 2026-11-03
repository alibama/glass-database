# Maker cards, the career record, record pages and the numbers

Three things the site shows about a maker that it did not before, and the plumbing under them.

## A page for every Glass Database ID — `/record/<gid>`

Every item the Wikibase migration stages carries a key, `gdb:<kind>:<id>` (`gdb:artists:12` for
a Directory row, `gdb:object:<content hash>` for a piece, `gdb:technique:murrine` for a term).
`deploy/record.html`, over `GET /api/records/{gid}` (`central/records.py`), is that key's page:

* the public columns of the source row, through the publication gate (restricted tables and
  unapproved rows never resolve);
* what the wiki item says: label, description, aliases and every statement with item values
  shown by label and linked to their own record pages;
* where it is: the wiki item (Q id), the Wikidata item, the website and Instagram, the
  Directory search, the map, the verify app for a piece, the maker's card for a person;
* what else is tied to it: a maker's published pieces and collecting-event entries and
  lineage, a studio's makers, a piece's maker and how many collections hold it;
* a citation line and schema.org JSON-LD with `sameAs` to Wikidata and the wiki.

The proxy redirects `/record/<gid>` to `record.html?gid=` (`deploy/apache-glassdatabase-proxy.conf`);
the dev site does the same. Over https that rule only applies when the :443 vhost reads the
snippet: a certbot vhost copied before the snippet existed carries its own stale rules, and
`/record/<gid>` is a 404 there while `/api/records/<gid>` answers; `deploy/install.sh` adds the
`IncludeOptional` line to such a vhost. The staging step sets every item's `url` to its record
page (`Stager.record_url`), so the wiki's statements cite a page that resolves, and the Glass
Database ID property's formatter URL is `/record/$1`. `GET /api/records?q=` finds a record by
name; `GET /api/wikibase/go/{key}` still works and redirects to the record page.

A record's other links are deep links the explorer reads (`central/links.py`):
`/explore/?view=datasets&q=<name>&table=<table>` opens the Directory on that row's card (with
no `table`, on the first table that has a row by that name, which is where the wiki's older
item pages land), `/explore/?view=objects&piece=<row id>` opens the Pieces view on one piece,
and `?view=objects&maker=<name>` on everything by one maker.

## The maker's card — `/maker.html?m=<gid>` and *My account*

`central/makers.py` joins the three places a maker appears (a Directory row, an account, a wiki
item) by key, account and name, and `card()` builds the card `GDB.makerCard` renders (the same
trading-card frame as a piece):

| On the card | Where it comes from |
|---|---|
| Studio, where based, since, techniques | the Directory row, the Glowtbook profile, the latest entry's studio, the pieces' techniques |
| **Pieces** in the record, with a Glass ID, with Content Credentials | `objects`, by the account's published hashes and by the names the maker goes by |
| **Events**: collecting events, entries, votes, times collected | `season_entries`, `season_votes`, `season_claims` by `artist_uid`, else by name |
| **Honours**: awards led at the close, prizes, badges, best People's choice rank, crowd favourites | `seasons.awards`, `season_prizes.winner_json` (`seasons.prizes_won`), `season_badges` |
| **Lineage**: teachers and students | approved `lineage_submissions` |
| **Bio completeness** | the nine profile fields below, weighted; the private card lists the empty ones and where each is filled in |
| **Knowledge score** | the formula below |

Profile fields (`makers.PROFILE_FIELDS`): name · studio or where based · techniques · a few lines
about you (weight 2) · website · Instagram · born or active since · who taught you · a Wikidata
item. A field counts as filled when the Directory row, the Glowtbook profile, the entries or
the wiki item have it. The *where to fill* link points at Glowtbook, the Directory form, the
lineage form or the Wikidata lookup.

**Knowledge, out of 100** (`makers.score_parts`, the one formula for the card and the boards):

| Part | Points | How |
|---|---|---|
| The bio | 40 | 0.4 × completeness % |
| Pieces in the record | 20 | 20 × log(1 + n) / log(31): 1 piece 4, 5 pieces 10, 30 pieces 20 |
| Share with a Glass ID | 10 | registered / pieces |
| Collecting events | 10 | 4 per event, three events for the lot |
| Votes, collections, honours | 10 | votes / 5 + 2 per collection + 3 per award + 4 per prize + 1 per badge, capped |
| Links out | 10 | Wikidata 4, the wiki 2, website 2, Instagram 2 |

Levels: 80 and up *on the record*, 60 *well known*, 40 *known*, 20 *a sketch*, else *barely known*.

The maker's own card is `GET /api/me/card` (signed in): the same card with `next_steps`, the
three or four things that would raise the score most, each with a link. *My account* shows it
as its second section. The public card, `GET /api/makers/{gid}` or `/makers/name:<name>`,
carries no account id or email.

## The career record

A maker enters year after year, so the card keeps the whole run: `makers.career` lists every
collecting event entered, newest first, with entries, votes, collections, the best People's
choice rank, the awards led when the event closed, the prizes settled to them and the badges.
Prizes are attributed as data: at close-out `seasons.settle_prizes` writes `winner_json`
beside `winner_text` (who the leader was: the piece's entry and its maker's account, the
maker, the studio, the collector), and `seasons.prizes_won(uid=…, name=…, studio=…)` reads it
back for a maker or a shop.

## The numbers — `/stats.html`

`central/stats.py` counts the record (`GET /api/stats`, cached ten minutes): the Directory's
tables, pieces and how many carry a Glass ID and Content Credentials, makers and studios,
accounts, lineage links; the collecting events (entries, makers and studios entering, votes,
collections, prizes on the table and settled, badges); the wiki (items staged and pushed, by
kind, with a Wikidata id, properties, the last push, and what the wiki reports about itself
through its API, cached an hour); the Wikidata lookup (known, matched, unsure, none, confirmed,
rejected, the Resolve tool's suggestions, enrichment proposals). `GET /api/stats/makers`
(`makers.leaderboards`, cached fifteen minutes) scores every named maker at once from a few
grouped queries and gives the boards: knowledge, most complete bios, most pieces, most events.

## Files

`central/records.py`, `central/makers.py`, `central/stats.py`, `api/records.py`, `api/makers.py`,
`api/me.py` (`/me/card`), `deploy/record.html`, `deploy/maker.html`, `deploy/stats.html`,
`deploy/account.html` (the card section), `deploy/site.js` (`GDB.makerCard`), `deploy/paper.css`
(`.pc-bar`, `.pc-mono`); tests in `tests/test_records.py` and `tests/test_makers.py`.
