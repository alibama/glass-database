# Administrators & login tracking

## Login ledger
Every real Google sign-in is recorded (Admin → 👥 Users) with **just name and
email**, first/last seen, and a login count. No passwords, no other profile data.

## Who's an administrator
Two ways, checked in this order:
1. **Bootstrap** — emails in `GLASSDB_ADMIN_EMAILS` (comma-separated) are always
   admins. Keep at least one here so you can never lock yourself out.
2. **Promoted** — any existing admin can promote a user in **Admin → 👥 Users**
   ("Make admin"), or grant admin by email before that person's first login.

```
# /opt/glassdatabase/.env
GLASSDB_ADMIN_EMAILS=you@gmail.com,cofounder@gmail.com
```

## Gating the admin console by Google role (optional)
By default the console is protected by the proxy's HTTP basic auth and the Users
page just manages roles. To make **Google admin role** the gate instead:

1. Add the admin URL (e.g. `https://glassdatabase.org/admin/oauth2callback`) to the
   authorized redirect URIs in your Google OAuth client.
2. Set `GLASSDB_ADMIN_OIDC=1` in `.env` and restart.
3. Now the console requires a Google login *and* admin role; signed-in
   non-admins get a "ask an admin to promote you" screen.
4. Once confirmed working, you may remove the basic-auth requirement on `/admin`
   in the Apache config if you no longer want two prompts.

**Don't set `GLASSDB_ADMIN_OIDC=1` without a bootstrap email** — you'd have no way
in. If that happens, unset it (or add your email to `GLASSDB_ADMIN_EMAILS`) and
restart.
