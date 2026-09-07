#!/usr/bin/env bash
# General hardening for the single Ubuntu host running Glass Database.
# Idempotent; run as root. Review each section — some choices (SSH port, IP allow)
# are yours to set. Nothing here deletes data.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

echo "==> Automatic security updates"
apt-get update -y
apt-get install -y unattended-upgrades fail2ban ufw
dpkg-reconfigure -f noninteractive unattended-upgrades || true
systemctl enable --now unattended-upgrades

echo "==> Firewall: allow SSH + HTTP/HTTPS only; app ports stay internal"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
# The Streamlit/API ports (8000/8501/8502/8503) and Vault (8200) are NOT opened —
# they must be reachable only via the Apache reverse proxy / localhost.
ufw --force enable

echo "==> SSH hardening (key-only, no root password login)"
SSHD=/etc/ssh/sshd_config.d/99-glassdb-hardening.conf
cat > "$SSHD" <<'SSH'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
SSH
echo "  (ensure you have a working SSH key before the next restart!)"
sshd -t && systemctl reload ssh || echo "  sshd config test failed — NOT reloaded"

echo "==> fail2ban for SSH"
cat > /etc/fail2ban/jail.d/glassdb.conf <<'F2B'
[sshd]
enabled = true
maxretry = 4
bantime = 1h
F2B
systemctl enable --now fail2ban
systemctl restart fail2ban || true

echo "==> Bind app services to localhost (only Apache should reach them)"
for u in api explore glowtbook admin; do
  f="/etc/systemd/system/glassdb-$u.service"
  [ -f "$f" ] || continue
  if grep -q -- "--server.address" "$f" 2>/dev/null || grep -q -- "--host" "$f" 2>/dev/null; then
    sed -i 's/--server.address=[0-9.]*/--server.address=127.0.0.1/; s/--host=[0-9.]*/--host=127.0.0.1/' "$f"
  fi
done
systemctl daemon-reload || true
echo "  (verify each unit binds 127.0.0.1; restart with: systemctl restart glassdb-*)"

echo "==> Lock down secrets & signing material permissions"
D=/opt/glassdatabase
if [ -d "$D" ]; then
  chown -R glassdb:glassdb "$D/data" 2>/dev/null || true
  chmod 700 "$D/data/c2pa" 2>/dev/null || true
  chmod 600 "$D/data/c2pa/"*.pem 2>/dev/null || true
  # .env and secrets.toml are read by the app (runs as glassdb) — 600 is right.
  chmod 600 "$D/.env" "$D/.streamlit/secrets.toml" 2>/dev/null || true
  # .htpasswd is read by APACHE (www-data), NOT the app — it must stay readable by
  # the web server or /admin returns 500 ("could not open password file").
  if [ -f "$D/.htpasswd" ]; then
    chown root:www-data "$D/.htpasswd" && chmod 640 "$D/.htpasswd"
  fi
fi

echo "==> Kernel/network sysctl hardening"
cat > /etc/sysctl.d/99-glassdb.conf <<'SYS'
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.all.accept_source_route=0
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.tcp_syncookies=1
net.ipv4.icmp_echo_ignore_broadcasts=1
kernel.randomize_va_space=2
fs.protected_hardlinks=1
fs.protected_symlinks=1
SYS
sysctl --system >/dev/null || true

echo "==> Done. Review deploy/HARDENING.md for the manual items (SSH key check,"
echo "    Apache security headers, backups, Vault) and confirm services still work."
