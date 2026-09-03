# Performance & scaling notes

The stack is three Streamlit apps + one FastAPI service + SQLite behind Apache, on
one box. Streamlit re-runs the whole script on every interaction, so most wins are
about (a) not redoing work, (b) DB concurrency, and (c) letting the browser and
Apache do their jobs.

## Already applied (in the repo)

- **SQLite pragmas** (`central/dbconn.py`): WAL + `synchronous=NORMAL`,
  `busy_timeout=5000` (no more "database is locked" under 4 processes),
  16 MB cache, 128 MB mmap.
- **Indexes** on the hot paths: the approval-gate join (`_approvals(tbl,row_id)`,
  `(tbl,status)`), `object_images(object_row_id)`, `_columns(tbl)`.
- **Streamlit prod config** (`.streamlit/config.toml`): `fileWatcherType="none"`
  (no inotify/polling CPU), no telemetry, `fastReruns`, minimal toolbar.
- **Apache**: gzip (`mod_deflate`) for HTML/JSON/Turtle/SVG, and `Cache-Control`
  for static assets — big win for the base64-heavy pages and API responses.
- **Caching** the expensive C2PA credential read in Explore, and dataset reads
  (`@st.cache_data`).

## If you're still tight — in order of impact

1. **Serve images by URL, not inline.** Object images are base64 in the DB and
   currently embedded as `data:` URIs. That pulls every image into the app
   process and re-sends it each render. Switching the object views to
   `<img src="/api/objects/<id>/image">` (already an endpoint) lets the browser
   cache them and drops app memory sharply. Highest-impact change if you have many
   objects with images.

2. **Cap what Explore loads.** Charts/filters load full datasets into pandas each
   rerun. For big tables, select only needed columns and add a row cap / paging;
   keep the `@st.cache_data(ttl=…)` in front. Consider precomputing the heavy
   aggregates once on import rather than per view.

3. **Give each service a memory ceiling + auto-restart.** In each systemd unit:
   `MemoryMax=512M`, `Restart=always`, `RestartSec=3`. A runaway Streamlit session
   then can't take the box down, and OpenCV (in Glowtbook) won't creep.

4. **uvicorn workers for the API** if it's CPU-bound (it's read-only, so usually
   isn't): `--workers 2`. Don't over-provision — each worker is a process.

5. **Move the DB to Turso** (already supported via `TURSO_DATABASE_URL`) if you
   outgrow a single box or want reads served near users. Same code path.

6. **`PRAGMA optimize` / `ANALYZE` on a schedule** (e.g. nightly cron) so the
   query planner has fresh stats as data grows.

## How to see where it hurts

- `systemctl status glassdb-*` and `systemd-cgtop` — which service eats CPU/RAM.
- `htop` during a slow page — is it one Streamlit pegging a core (usually an
  uncached rerun) or memory pressure/swap?
- Slow SQLite query: `EXPLAIN QUERY PLAN <sql>` — a `SCAN` on a hot table wants an
  index.
- Big responses: check `Content-Encoding: gzip` is present on the API/HTML
  responses (`curl -sI -H 'Accept-Encoding: gzip' https://…/api/datasets`).

Rule of thumb: on Streamlit, a slow page is almost always **work repeated on every
rerun that should be cached**, or a **full dataset loaded when a slice would do** —
look there before adding hardware.
