#!/usr/bin/env bash
#
# One-command production update. Run it from your SOURCE tree (a git checkout you
# pull, or an untarred release) — NOT from /opt/glassdatabase (the runtime).
# Works whether you run it as root or as a normal user with sudo.
#
#   cd /root/glass-database     # your source checkout / untarred release
#   bash deploy/update.sh
#
# If the source is a git checkout it fast-forward-pulls first; then it runs the
# installer, which preserves the live database, .env, secrets, media, and the
# C2PA signing key, updates dependencies, and restarts services.
set -euo pipefail

DOMAIN="${1:-glassdatabase.org}"
APP_DIR="/opt/glassdatabase"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$SRC" = "$APP_DIR" ]; then
  echo "Don't run this from the runtime ($APP_DIR)."
  echo "Run it from your source checkout, e.g.  cd /root/glass-database && bash deploy/update.sh"
  exit 1
fi
cd "$SRC"

if [ -d .git ]; then
  echo "==> Pulling latest from git"
  BEFORE="$(git rev-parse --short HEAD 2>/dev/null || echo none)"
  git pull --ff-only || echo "    (pull skipped or failed — deploying the current files)"
  AFTER="$(git rev-parse --short HEAD 2>/dev/null || echo none)"
  if [ "$BEFORE" != "$AFTER" ] && [ "$BEFORE" != none ]; then
    git --no-pager log --oneline "$BEFORE..$AFTER" 2>/dev/null | sed 's/^/      /' || true
  fi
else
  echo "==> No git checkout here — deploying the current files."
fi

# The deploy needs root; run it directly if we already are, else via sudo.
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
echo "==> Deploying to $APP_DIR (data, .env, secrets, media, signing key preserved)"
$SUDO bash "$SRC/deploy/install.sh" "$DOMAIN"

echo "==> Health check"
ok=1
for s in api explore glowtbook admin; do
  if $SUDO systemctl is-active --quiet "glassdb-$s"; then
    echo "    glassdb-$s: active"
  else
    echo "    glassdb-$s: NOT ACTIVE  —  $SUDO journalctl -u glassdb-$s -n 50"; ok=0
  fi
done
[ "$ok" = 1 ] && echo "==> Update complete." || { echo "==> Finished with service errors."; exit 1; }
