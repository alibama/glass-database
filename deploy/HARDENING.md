# Host hardening checklist

Run `sudo bash deploy/harden.sh` for the automated parts, then work the manual
items below. Everything is scoped to the single Ubuntu host.

## Automated by harden.sh
- **Unattended security updates** (`unattended-upgrades`).
- **Firewall** (`ufw`): only 22/80/443 open. App ports (8000/8501/8502/8503) and
  Vault (8200) stay internal — reachable only via Apache/localhost.
- **SSH**: key-only, no password auth, no root password login, MaxAuthTries 3.
- **fail2ban** on SSH.
- **Bind app services to 127.0.0.1** (so only the Apache proxy reaches them).
- **Secrets/signing-material permissions** — `data/c2pa` 700, `*.pem`/.env/
  secrets.toml 600 (read by the app as `glassdb`). **`.htpasswd` is 640, group
  `www-data`** — it's read by Apache, not the app; 600 would break /admin with a 500.
- **sysctl** network/kernel hardening.

## Do these by hand
1. **Confirm SSH key access BEFORE the reload** — harden.sh disables password
   login. Have a working key in `~/.ssh/authorized_keys` first, and keep a session
   open while you test a new one.
2. **Verify app ports aren't public:** `ss -ltnp` — the 850x/8000/8200 ports should
   show `127.0.0.1`, not `0.0.0.0`. If a Streamlit unit still binds all interfaces,
   add `--server.address=127.0.0.1` to its `ExecStart` and restart.
3. **Apache security headers** — add to the vhost:
   `Header always set X-Content-Type-Options nosniff`,
   `Header always set X-Frame-Options SAMEORIGIN`,
   `Header always set Referrer-Policy strict-origin-when-cross-origin`,
   and a **Strict-Transport-Security** header (once you're sure HTTPS is solid).
   Do NOT add a restrictive Content-Security-Policy that blocks the DINOv2 CDN
   (jsdelivr / huggingface) the capture apps use.
4. **Backups** — the SQLite DB (`data/glassdb.db`), signing cert, AIP bags, and
   media. A nightly `sqlite3 .backup` + off-box copy. Test a restore.
5. **Least-privilege service user** — services run as `glassdb`; confirm it isn't a
   sudoer and owns only what it needs.
6. **2FA on the cloud/DNS/registrar accounts** and on your GitHub — the supply
   chain matters as much as the box.
7. **Vault** (see VAULT.md) — moves the signing key off disk; store unseal keys
   off-box.
8. **Log review / minimal footprint** — remove unused packages
   (`apt autoremove`), disable services you don't run.

## Verify after
```bash
ufw status verbose
ss -ltnp                     # app ports on 127.0.0.1 only
systemctl status glassdb-*   # all four still active
sshd -t                      # config valid
```
