# Glass Database — central store, ingest, and API

One managed database for **all** the Glass Database content, a re-runnable
importer, a simple add-content admin, and a read-only API other developers can
build UIs on. Built on SQLite/libSQL so it drops straight into **Turso**.

```
  spreadsheets ──▶ central/ingest.py ──▶ glassdb.db (SQLite/libSQL, WAL)
                                              │
                        ┌─────────────────────┼─────────────────────┐
                   turso db create      api/main.py            admin/app.py
                   --from-file          (read-only, /docs)     (add & edit)
                        │                     │                     │
                   Turso primary  ◀───────────┴─────────── writes ──┘
                   (managed central DB)   devs api-ize off /datasets
```

## What's in the box (from your sheets)

`python -m central.ingest build` consolidated **23 datasets / ~2,300 rows** out
of 7 workbooks, dropping the spreadsheet machinery (Summary/Mapping/Rules/
Dashboard/Lookup/Read-Me/dupes — all listed under "skipped" so the choice is
transparent). Highlights: `artists` (545), `studios` (221), `museum_artists`
(151), `university_programs` (85), `resources_legacy` (122), plus trade shows,
funding, events, open calls, and more.

Four datasets are **restricted** (managed but never served publicly):
`studios_internal`, `programs_internal`, `studio_intake`, `comments` — because
they carry claim tokens, internal notes, and raw contact info. And inside public
tables, columns like `email_address`, `phone`, `contact_email`, `claim_token`,
`internal_notes` are flagged private and withheld from the API. This is the data
side of your Removal & Correction Policy.

## 1 · Build / rebuild the central DB

```bash
pip install -r requirements.txt

# Consolidate every content sheet into data/glassdb.db (idempotent — safe to re-run)
python -m central.ingest build --uploads /path/to/folder-of-xlsx

python -m central.ingest list           # see datasets + row counts
```

Re-running is safe: rows have a **stable id** from each dataset's natural key, so
editing a sheet and rebuilding **updates** rows instead of duplicating them.

## 2 · Put it on Turso (the managed central DB)

The file is already WAL-mode and checkpointed, so it's import-ready.

```bash
curl -sSfL https://get.tur.so/install.sh | bash
turso auth login

# create the cloud DB straight from the file
turso db create glassdatabase --from-file data/glassdb.db

turso db show   glassdatabase --url      # -> TURSO_DATABASE_URL
turso db tokens create glassdatabase     # -> TURSO_AUTH_TOKEN
```

Then set `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` (and `pip install libsql`) and
**every** tool here — ingest, API, admin — targets the cloud DB with no code
change. Free tier covers this easily (500 DBs, 9 GB, 1B row reads). An SQL dump
is also in `data/glassdb.sql` if you'd rather `turso db shell glassdatabase < data/glassdb.sql`.

## 3 · Add content (three ways, all easy)

```bash
# a) re-import an edited sheet (bulk) — just rebuild
python -m central.ingest build --uploads /path/to/xlsx

# b) one row from the command line
python -m central.ingest template --table studios          # shows the fields
python -m central.ingest add --table studios name="New Studio" city="Crozet" country="USA"
```

```bash
# c) point-and-click
streamlit run admin/app.py        # pick a dataset -> "Add a row"
```

All three write to whatever `dbconn` points at — local file or Turso.

## 4 · The developer API

```bash
uvicorn api.main:app --reload      # http://127.0.0.1:8000/docs
```

Read-only, self-describing, CORS-enabled, with interactive Swagger docs — the
"convenient exploration" surface for other devs.

| Endpoint | What |
|---|---|
| `GET /datasets` | list datasets (public only, unless admin key) |
| `GET /schema/{table}` | columns + labels + visibility |
| `GET /datasets/{table}` | rows — `?limit&offset`, any column as `?col=value`, `?q=` search, `?order_by=&desc=`, `?format=csv` |
| `GET /datasets/{table}/{row_id}` | one row |

```bash
curl 'http://127.0.0.1:8000/datasets/studios?country=United%20States&limit=5'
curl 'http://127.0.0.1:8000/datasets/museum_artists?q=chihuly'
curl 'http://127.0.0.1:8000/datasets/university_programs?format=csv' -o programs.csv
```

**Public by default.** Restricted datasets and private columns only appear when a
caller sends `X-API-Key: <GLASSDB_ADMIN_TOKEN>`. Table/column names are validated
against the registry, so identifiers can't be injected. There are **no write
endpoints** — content is added via the admin/CLI, so the public API can't be used
to alter the data.

## Notes / next steps

- The importer keeps `_source_file`, `_source_sheet`, `_imported_at`, and each
  dataset's `verification` / `last_verified` / `basis` columns — the provenance
  your policy promises ("how each entry was sourced and when it was last checked").
- Legacy vs current sheets are kept as separate tables (`artists` /
  `artists_legacy`, etc.) rather than silently merged — deduping them across
  schemas is a data-cleaning pass worth doing deliberately, not at import time.
- Natural next steps: point the public **explorer** and **Glowtbook**
  contributions at this same DB; add a moderation view to the admin for the
  restricted `studio_intake` / submissions queue; and turn on a Turso embedded
  replica for the API's reads.
```
