# Login & authentication

## The short version

- **Mechanism:** Streamlit's built-in OIDC login (`st.login()` / `st.user`,
  Streamlit ≥ 1.42 + Authlib). It runs the whole OAuth/OIDC flow and session
  cookie for you. **Your app never stores a password or sends an email.**
- **It only speaks OpenID Connect**, not generic OAuth2. That decides what's
  realistic among the providers you named.
- **Start with Google** (clean OIDC, ~15 min). **Add a broker** when you want the
  rest without pain. **Drop Instagram as a login** (Meta deprecated it as an
  identity provider) — keep it as a profile link instead. **Bluesky** is a
  fast-follow (ATProto OAuth isn't OIDC, so it needs a broker or a small custom
  handler).
- **Moderation/admin** stays behind the HTTP basic auth it already has — that's
  your one local credential, no email involved.

Glowtbook is already wired: **with login configured it becomes a per-user private
journal; without it, it stays a shared demo.** So nothing breaks before you turn
auth on.

## Turn on Google login (Option A)

1. Google Cloud Console → APIs & Services → Credentials → **Create OAuth client
   ID** → *Web application*.
2. Add the **Authorized redirect URI** exactly:
   `https://glassdatabase.org/glowtbook/oauth2callback`
3. Copy the **Client ID** and **Client secret**.
4. On the server:
   ```bash
   cd /opt/glassdatabase/.streamlit
   sudo cp secrets.toml.example secrets.toml
   sudo nano secrets.toml         # uncomment the [auth] + [auth.google] blocks, paste creds
   # generate the cookie secret:
   openssl rand -base64 32
   sudo chown glassdb:glassdb secrets.toml && sudo chmod 600 secrets.toml
   sudo systemctl restart glassdb-glowtbook
   ```
5. Visit `/glowtbook/` — you'll get a "Continue with Google" button, and each
   Google account gets its own private journal + profile.

## Add the long tail of providers (Option B: a broker)

Put an OIDC **broker** in front. Streamlit still does one `st.login()`; the broker
federates Google / Facebook / Apple / Bluesky / GitHub / email-password and owns
all of it. You only change `secrets.toml` to point at the broker's
`server_metadata_url`.

- **Self-hosted** (matches owning your own box): Authentik, Keycloak, or Logto.
- **Managed** (zero ops, free tier): Auth0 or Logto Cloud.

Configure your social connections once in the broker's dashboard; register one
redirect URI (`https://glassdatabase.org/glowtbook/oauth2callback`); done.

## Sign-in for the static pages (capture app, verify, Seasons, graph)

`st.login()` only exists inside a Streamlit app. The plain pages talk to the API,
so the API carries its own session (`central/sessions.py`, `api/auth.py`):
a signed token in the `gdb_session` cookie or an `Authorization: Bearer` header.
It reuses the **same** `[auth.google]` client as Glowtbook — just register one
more redirect URI in Google Cloud Console:

```
https://glassdatabase.org/api/auth/callback
```

Then `/api/auth/login?next=/fingerprint/enroll.html` runs the authorization-code
+ PKCE flow and drops the cookie for the whole origin. `GET /api/auth/providers`
tells the pages whether this is configured. A broker (Option B) works the same
way — the API reads `server_metadata_url` from the first configured provider.

Two settings in `.env`:

- `GLASSDB_SESSION_SECRET` — the HMAC key for session and pairing tokens. If
  unset it falls back to `[auth].cookie_secret`, then `GLASSDB_ADMIN_TOKEN`. Set it
  explicitly in production (`openssl rand -hex 32`), and keep it stable or
  everyone is signed out.
- `GLASSDB_DEV_LOGIN=1` — **local development only**: enables
  `/api/auth/dev-login`, a fake sign-in so the flows can be clicked through
  without a Google client. Never set this on a public server.

**Pairing (no second login on the phone).** Glowtbook → Objects → Fingerprint
shows a QR / link of the form `…/fingerprint/enroll.html#token=…&object=12`: a
2-hour token minted for the signed-in user, carried in the URL fragment (never
sent to the server, never logged). The page stores it locally and sends it as a
Bearer header, so the phone is signed in as you with that piece chosen. This
works even when the API's Google sign-in isn't configured — the Glowtbook
session is the source of truth.

Record of who signed in is the same `users` ledger either way (`Admin → 👥 Users`).

**If the capture app says "Sign-in isn't configured on this server"** (a 503 from
`/api/auth/login`), the API could not find a complete provider in `secrets.toml`.
Open `https://<domain>/api/auth/providers` (or run `sudo bash deploy/doctor.sh`):
its `note` says why and `diagnostics` shows the file it looked at, whether it
parsed and with which TOML parser, and which `[auth.*]` tables are complete —
never the values. The usual causes, in order:

1. The file is missing on the API's side (`/opt/glassdatabase/.streamlit/secrets.toml`),
   or lives elsewhere — point `GLASSDB_SECRETS_PATH` at it in `.env`.
2. It is not readable by the `glassdb` service user (see the gotcha below).
3. `[auth.google]` lacks one of `client_id`, `client_secret`, `server_metadata_url`.
4. Older builds only parsed TOML with Python 3.11's `tomllib`; on a Python 3.10
   host (Ubuntu 22.04) that silently read nothing. The reader now falls back to
   the `toml` package Streamlit already installs, so redeploying fixes it.

Restart the API after changing the file: `sudo systemctl restart glassdb-api`.
Pairing from Glowtbook keeps working throughout, since it needs no OAuth on the API.

## Notes

- `secrets.toml` lives at `/opt/glassdatabase/.streamlit/secrets.toml` and is read
  by all the Streamlit apps, but only **Glowtbook** calls `st.login()`, so the
  public explorer stays open and admin stays on basic auth. The API reads the
  same file (read-only) for the static-page sign-in above.
- Streamlit's `[auth]` allows a single `redirect_uri`. Since only Glowtbook uses
  OIDC today, that's fine. If you later OIDC-gate a second app, give it its own
  working directory / secrets, or move auth to the proxy (mod_auth_openidc /
  oauth2-proxy) so one config covers every surface.
- If you'd rather keep auth out of app code entirely, the proxy-level route
  (Apache `mod_auth_openidc`, or `oauth2-proxy`) protects every surface uniformly
  and passes the signed-in user in a header. That pairs especially well with a
  broker. Happy to wire that instead.

## Gotcha: secrets.toml must be readable by the service user

Streamlit reads `.streamlit/secrets.toml` on every page load, so if that file
exists it MUST be readable by the `glassdb` service user — otherwise **every**
Streamlit app (explore and glowtbook, even though explore doesn't use auth) will
crash-loop with `PermissionError`. If you create it with `sudo`, fix ownership:

    sudo chown glassdb:glassdb /opt/glassdatabase/.streamlit/secrets.toml
    sudo chmod 600 /opt/glassdatabase/.streamlit/secrets.toml
    sudo systemctl restart glassdb-explore glassdb-glowtbook

(The installer's final `chown -R glassdb` also fixes this, and now never deletes
your secrets.toml on redeploy.)

## https, always

Sign-in only works over https: the session cookie is `Secure`, so a page served over plain
http looks signed out, and Google only accepts an https redirect URI. Three layers keep every
request on https:

1. `deploy/apache-glassdatabase.conf` redirects the :80 vhost to https (the ACME challenge path
   excepted); `a2enmod rewrite` is needed and `install.sh` enables it.
2. `deploy/apache-glassdatabase-proxy.conf` sends `Strict-Transport-Security` on https responses.
3. The API itself (`api/main.py`) answers any request that arrives with `X-Forwarded-Proto: http`
   with a 301 to the https URL when `PUBLIC_BASE_URL` starts with `https://`, and
   `set_session_cookie` marks the cookie Secure whenever `PUBLIC_BASE_URL` is https.

Check it: `curl -sI http://glassdatabase.org/games` should return `301` with a `Location:
https://…` header, and `curl -sI https://glassdatabase.org/` should carry `Strict-Transport-Security`.
