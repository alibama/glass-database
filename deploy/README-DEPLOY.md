# Deploying Glass Database on Ubuntu

A one-command install that puts the API and admin behind Nginx on your server.
Tested against the layout in this repo; targets Ubuntu 22.04 / 24.04.

## What gets installed

```
                         nginx (:80 / :443)
                        /                    \
        /api/  ──▶ uvicorn 127.0.0.1:8000     /admin/ ──▶ streamlit 127.0.0.1:8501
        (public, read-only)                   (basic-auth protected, can write)
                        \                    /
                         data/glassdb.db  (SQLite/libSQL, WAL)
```

- `glassdb-api` systemd service — the read-only FastAPI, bound to localhost.
- `glassdb-admin` systemd service — the Streamlit add/edit UI, bound to localhost.
- `glassdb-explore` systemd service — the public interactive data explorer.
- `glassdb-glowtbook` systemd service — the Glowtbook demo (journal + contribute).
- A reverse proxy — `/` landing, `/api/` + `/explore/` + `/glowtbook/` public, `/admin/` behind HTTP basic auth.
- A dedicated `glassdb` system user; app in `/opt/glassdatabase`.

**Adding these to a box that already has the API+admin running:** upload the new
project, then just re-run the installer — it's idempotent and now preserves your
database, `.env`, and admin password across runs:

```bash
cd glass-central && sudo bash deploy/install.sh glassdatabase.org
```

**Already running Apache (or Nginx)?** You don't need to change it. The two app
services are just localhost processes; only the proxy layer cares which web
server you use. The installer **auto-detects** an existing Apache and reuses it
**without touching your other sites** — it only adds a `glassdatabase.conf`
vhost. Force a choice with `--webserver apache|nginx|none`. Both the Apache and
Nginx vhosts were config-tested end-to-end (landing, `/api/` + docs, and
`/admin/` with basic auth all verified through the proxy).

## Install

Get the project onto the server (either works):

```bash
# from your machine
rsync -av ./glass-central/ user@glassdatabase.org:~/glass-central/
# or: git clone <your repo> on the server
```

Then on the server:

```bash
cd ~/glass-central
sudo bash deploy/install.sh glassdatabase.org
```

That's it. The script installs packages, creates the venv, seeds the database
from the shipped `data/glassdb.db` (or keeps an existing one), generates the
admin credentials, installs and starts both services, and configures Nginx. It
prints the admin web-login password and where the API admin key lives — save
them.

The site is now live over **HTTP**:
- API: `http://glassdatabase.org/api/` (docs at `/api/docs`)
- Admin: `http://glassdatabase.org/admin/`

## Turn on HTTPS

Once DNS for the domain points at the server (the installer prints the exact
command for your web server):

```bash
sudo certbot --apache -d glassdatabase.org -d www.glassdatabase.org   # Apache
sudo certbot --nginx  -d glassdatabase.org -d www.glassdatabase.org   # Nginx
```

Certbot edits the vhost to add the TLS block and sets up auto-renewal. The
websocket and proxy directives carry over into the HTTPS vhost.

## Adding content on the server

Three ways, same as locally — all write to `/opt/glassdatabase/data/glassdb.db`:

```bash
# point-and-click: the admin UI
#   https://glassdatabase.org/admin/   (login: admin / <password from install>)

# CLI, as the service user:
sudo -u glassdb /opt/glassdatabase/.venv/bin/python -m central.ingest add \
    --table studios name="New Studio" city="Crozet" country="USA"

# bulk: drop updated .xlsx on the server and re-import (idempotent)
sudo -u glassdb /opt/glassdatabase/.venv/bin/python -m central.ingest build --uploads ~/xlsx
```

After a bulk rebuild, no restart is needed — the API reads the file live.

## Managing the services

```bash
sudo systemctl status  glassdb-api glassdb-admin
sudo systemctl restart glassdb-api glassdb-admin
sudo journalctl -u glassdb-api -f          # live logs
sudo journalctl -u glassdb-admin -f
```

## Updating the code later

```bash
cd ~/glass-central          # with your new code
sudo bash deploy/update.sh  # syncs code, keeps DB + .env, restarts
```

## Switching to Turso later (optional)

Nothing in the deploy assumes SQLite. To move the managed DB to Turso, add
`TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` to `/opt/glassdatabase/.env`,
`sudo -u glassdb /opt/glassdatabase/.venv/bin/pip install libsql`, then
`sudo systemctl restart glassdb-api glassdb-admin`. Same code, same endpoints.

## Notes & troubleshooting

- **Admin security**: `/admin/` is behind HTTP basic auth *and* the app has no
  public write path of its own. Change the web password with
  `sudo htpasswd /opt/glassdatabase/.htpasswd admin`. For extra safety you can
  instead bind it to localhost only and reach it via an SSH tunnel
  (`ssh -L 8501:127.0.0.1:8501 user@host`) — then remove the `/admin/` block
  from the Nginx config.
- **API admin key**: the `X-API-Key` that unlocks restricted datasets/columns is
  the `GLASSDB_ADMIN_TOKEN` in `.env`; never hand it out for public use.
- **DB file permissions**: the database and its `-wal`/`-shm` siblings must be
  writable by `glassdb`. The installer sets this; if you copy a DB in by hand,
  `sudo chown glassdb:glassdb /opt/glassdatabase/data/glassdb.db*`.
- **Streamlit behind the proxy**: if the admin page hangs on "Please wait…",
  it's almost always the websocket — confirm the `/admin/` block keeps the
  `Upgrade`/`Connection` headers, which it does by default here.
- **502 on /api/**: check `sudo systemctl status glassdb-api` and the journal;
  usually a missing dependency or a bad `.env`.
```
