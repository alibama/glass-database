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

## Notes

- `secrets.toml` lives at `/opt/glassdatabase/.streamlit/secrets.toml` and is read
  by all the Streamlit apps, but only **Glowtbook** calls `st.login()`, so the
  public explorer stays open and admin stays on basic auth.
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
