# Wikibase — the public record on glass.wikibase.cloud

The Glass Database's public, durable facts live on **https://glass.wikibase.cloud** (MediaWiki
1.43 with Wikibase, EntitySchema, EDTF, CirrusSearch and OAuth, run by wikibase.cloud). The
site stays the place people type into; the wiki is the backstop of the record and the feeder
for Wikidata, which the Museum of Glass already edits. This document is the runbook; the
plan behind it is *Glass Database on Wikibase: the model, the migration, the rebuild*.

Everything here is runnable from **`/wikibase.html`** (the migration, viewable by anyone;
the buttons appear for administrators) and from the command line
(`python -m central.wikibase …`). The code is `central/wikibase/` and `api/wikibase.py`.

## What goes where

One test: public, durable and useful to someone who is not us → the wiki. Private, fast-changing
or heavy → the app.

| In the wiki | In the app |
|---|---|
| people who make glass; studios, schools and programs, museums, funders | emails, phones, demographic fields, home addresses |
| published pieces with everything their public page shows (maker, year, technique, form, colour, size, story events, the credential hash, the fingerprint hash, Glass ID strength, image and page URLs) | unpublished pieces and their images, fingerprint vectors and frames, embeddings, the archive bags |
| vocabularies: techniques, the trait thesaurus, colours, forms, occupations, story-event types, units, countries | prices paid, buyers, votes while a season runs, claims and their reports, event registrations |
| lineage (student of / student), shows and sales, collecting events with their results once closed, awards | sessions, analytics, Discord settings |
| links to Wikidata, Getty AAT and ULAN | a cache of the wiki (`wb_cache`) the rebuild reads |

**Collectors stay in the app.** A collector is captured when they check a piece and add it to
their collection; no collector item, no collection, no claim and no vote is ever staged, and a
piece on the wiki shows no owner. A collector who later wants a public profile can have a person
item of their own, with none of their collection on it.

**Licence.** Statements are published as CC0 so they can flow to Wikidata; photographs, field
notes and Glowtbook stories keep their own licences and stay on glassdatabase.org. Decide this
with the museum before the first real push.

## The model

`central/wikibase/model.py` is the schema: 55 properties (each with its Wikidata twin as
*equivalent property* where one exists; ids checked 2026-10-31), 29 class items, and the
vocabularies built from code (`central.techniques`, `central.glass_traits`, colours, forms,
occupations from the artist form's roles, story-event types, the millimetre, the *Glass
Database* source item, countries). What is deliberately out: dates of birth and death, place
of birth, gender, ethnicity, email, phone, addresses, prices paid, buyers, votes, collectors,
fingerprint vectors.

Every item carries a **Glass Database ID**: `gdb:<table>:<row id>` for an ingested row,
`gdb:object:<content hash>` for a piece, `gdb:technique:<slug>` for a vocabulary term,
`gdb:place:<slug>` for a place made from a city, region and country. Its formatter URL is
`/api/wikibase/go/$1`, which resolves to the page on the site. Our rows carry the wiki id in
`wb_items.qid`. That pair is the round trip, in either direction.

## The pipeline

Eight steps, in the order the page shows them. Each one is a background job with a log the
page follows; state is in the database (`wb_runs`, `wb_log`), so every API worker sees it and
one job runs at a time.

1. **Bot settings** (admins). On the wiki: *Special:BotPasswords* → a bot password with the
   grants *Edit existing pages*, *Create, edit, and move pages* and *High-volume editing*. Save
   the wiki URL, the bot user (`Name@botname`) and the password on the page. The password is
   stored encrypted with the site secret (`GLASSDB_SESSION_SECRET`, like connected social
   accounts) and never shown again. *Test sign-in* reads site info, signs in, and reports the
   account's rights; it writes nothing. Creating properties needs the `property-create` right,
   which an administrator account on wikibase.cloud has.
2. **Stage** (`stage.py`). The tables → one staged item per thing (`wb_items`). Only approved
   rows (`_approvals`), only public columns (`_columns.is_public` and the manifest's private
   patterns), restricted datasets never. Free text is split into items: *based in* becomes a
   place item with a country; a studio's type becomes its class (hot shop, flameworking
   studio, kiln studio, cold shop, teaching studio); technique words become technique items
   (unknown words are listed in the log, not invented). Sources: artists, artists_legacy,
   profiles, artist_submissions, museum_artists (+ *collection*), rakow_commissions (+ *award
   received*), the makers named on pieces, studios, studio_submissions, studios_directory_legacy,
   university_programs, programs, funding, maker_events (listed), events, open_calls, trade_*,
   art_fairs, seasons (results once closed), objects, lineage_submissions. The log says, per
   table, which columns were used and which were left out. Re-running is safe: new approvals
   become `new` items, edited rows `changed`, a pushed item whose row vanished `orphan` (never
   deleted on the wiki). Stage runs **dedupe** at the end.
3. **Duplicates** (`dedupe.py`). Clusters per kind: the same normalised name (and city, for
   studios), the same website host or Instagram handle, near names. One canonical per cluster
   (the most trusted source, then the most statements; an item already on the wiki always
   wins); the others become `duplicate`, their statements and labels fold into the canonical,
   and their Glass Database IDs ride along as extra identifiers, so every source row still finds
   the item. *Keep apart* on the page (or `merge_into` in a CSV) overrides it.
4. **OpenRefine** (`refine.py`). Export a kind as CSV (`/api/wikibase/refine/person.csv` …);
   reconcile `label` against Wikidata in OpenRefine (`Q5` for people, the suggested type
   otherwise) and copy the match into `wikidata_qid` (`cell.recon.match.id`); fix labels,
   descriptions, aliases; set `status` to `ready` or `skipped`; write a key into `merge_into`
   when two rows are the same thing; export CSV and upload it back. A reviewer's edits are kept
   in `review_json` and survive a re-stage. The site is itself a reconciliation service
   (W3C 0.2) at `/api/wikibase/reconcile`, so OpenRefine can also match a sheet against what is
   staged or already on the wiki; `/api/wikibase/openrefine-manifest.json` is the manifest
   OpenRefine's Wikibase extension loads (available once the properties exist). Nothing is
   deleted on the wiki by an import. This step is optional before the first load and
   completed after it: the wiki holds a person without a Wikidata id, and the id arrives as a
   statement when found.
5. **Bootstrap the properties** (`push.py`, `what=properties`). Creates every property that
   has no property of that label on the wiki, then adds *equivalent property* (the Wikidata
   URL) and *formatter URL* statements where missing. *Rehearse* lists them; *Create* writes.
6. **Dry run**. Builds every request the push would send, against the live wiki's ids (the
   property labels, and the SPARQL map of every Glass Database ID already there), and writes
   nothing. The plan file (`data/wikibase/plan-<run>.json`, also at `/api/wikibase/plan/<run>`)
   holds every body; the log holds the counts and the first examples. A *limit* takes only the
   first N items in write order (classes and vocabularies first).
7. **Push**. Two passes. Pass 1 creates every item that has no id yet, bare: labels,
   description, aliases, its Glass Database ID(s) and *instance of*. Find-before-create: the id
   in `wb_items`, then the wiki's key map, then the API's "already has label" answer, whose
   item is reused only when it carries our key; otherwise the key's tail is appended to the
   description and the create retried once. Pass 2 writes every statement, each with a
   reference (*stated in* Glass Database, *reference URL* the page on the site, *retrieved*
   today). An item we created is written with `clear` so a re-run replaces rather than appends;
   an item that existed before us is read first and only what it lacks is appended. One write a
   second (`wikibase_pause`), `maxlag=5`, back-off on `ratelimited`, a fresh sign-in when the
   session lapses. A failed item is marked `failed` with the error and retried next time; the
   run goes on. **Nothing is ever deleted or merged on the wiki by the loader.**
8. **Pull** (`sync.py`). Fetches what we pushed into `wb_cache` (the wiki's JSON per item) and
   reports drift: a label changed by hand, statements added on the wiki, an item gone, a lost
   Glass Database ID. *Check the wiki* is the read-only connectivity test.

### Statuses

`new` staged, never pushed · `ready` a reviewer said go · `held` something is wrong · `skipped`
left out on purpose · `duplicate` folded into its canonical · `pushed` on the wiki and current ·
`changed` on the wiki, the row moved since · `failed` the last push failed on it · `orphan` its
row is gone, the wiki item stays · `split` a reviewer kept it apart from its cluster.

## Incoming data: how new submissions reach the wiki

Nothing changes for the forms, Glowtbook publishing or event registrations: they write to the
site, approval happens in the review queue (`/review.html`) or from the Discord link, and the
publication gate decides what is public. Staging reads that gate, so:

* a row approved after the last stage becomes a `new` item on the next **Stage**;
* a row edited since its push becomes `changed`;
* **Push** sends what is new or changed, and nothing else.

The schedule: run *Stage* and *Push what is new or changed* after a round of approvals, or
nightly from cron (the bot credentials saved on the page are used; the operator's own `.env`
can also carry `WIKIBASE_BOT_USER` / `WIKIBASE_BOT_PASSWORD`):

```
# /etc/cron.d/glassdb-wikibase (as the glassdb user, from /opt/glassdatabase)
15 3 * * *  glassdb  cd /opt/glassdatabase && .venv/bin/python -m central.wikibase stage  >/dev/null 2>&1
30 3 * * *  glassdb  cd /opt/glassdatabase && .venv/bin/python -m central.wikibase push --really >/dev/null 2>&1
45 3 * * *  glassdb  cd /opt/glassdatabase && .venv/bin/python -m central.wikibase pull   >/dev/null 2>&1
```

Edit summaries name the kind, the key and the source table, so the wiki's history says where
each item came from. Hand edits on the wiki are never overwritten blindly: an item we did not
create is appended to, and *Pull* reports where ours drifted so a person can decide.

## Rebuilding the app on top of the wiki

The pages keep reading the API and the API keeps serving tables shaped like today's; what
moves is where those tables come from. `wb_cache` is the first seam: after every push and every
nightly pull it holds the wiki's copy of every item we published. The order of the rebuild, as
in the plan: the cache-first switch (pages read cache tables filled from today's SQLite), then
class by class the cache's source becomes the wiki (vocabularies, people and studios, pieces,
events). The Directory and the Pieces pages reading the cache is what retires Explore; the
review queue has already replaced the Admin console's approvals.

## Command line

```
python -m central.wikibase status
python -m central.wikibase stage            # then dedupe runs by itself from the page; here: `dedupe`
python -m central.wikibase check
python -m central.wikibase bootstrap        # dry; --really creates the properties
python -m central.wikibase dry-run [--kinds person,studio] [--limit 50] [--no-live]
python -m central.wikibase push --really [--kinds …] [--limit N] [--keys k1,k2]
python -m central.wikibase pull
python -m central.wikibase export person people.csv  ·  import people.csv
WIKIBASE_BOT_PASSWORD=… python -m central.wikibase set-bot "Name@botname"
```

## Checks before leaning on the wiki

* The bot password's grants, and `property-create` for the account that bootstraps.
* The first real push with `--limit 60` (classes and vocabularies), then a look at the wiki.
* wikibase.cloud's rate limit: one write a second has been fine elsewhere; `maxlag` and back-off
  are on.
* SPARQL lag: the key map is read from the query service, which can trail the wiki by minutes
  after a big push; `wb_items.qid` is the source of truth for what we created.
* Backups: a nightly entity dump of the wiki (every item as JSON, via the API) next to the
  SQLite backup; `wb_cache` is not a backup.
