#!/usr/bin/env bash
#
# One-command production update: pull the latest code from git, then deploy it.
#
#   cd ~/glass-database        # your git checkout
#   bash deploy/update.sh      # do NOT use sudo — it asks for sudo itself
#
# Pulls from the git remote (fast-forward only), then runs the full installer,
# which preserves the live database, .env, .htpasswd, secrets, generated media,
# and the C2PA signing key, updates dependencies, and restarts the services.
set -euo pipefail

DOMAIN="${1:-glassdatabase.org}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SRC"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this WITHOUT sudo, as the user who owns the git checkout."
  echo "It will prompt for sudo only for the deploy step (so .git stays yours)."
  exit 1
fi

if [ -d .git ]; then
  echo "==> Pulling latest from git"
  BEFORE="$(git rev-parse --short HEAD)"
  git pull --ff-only
  AFTER="$(git rev-parse --short HEAD)"
  if [ "$BEFORE" = "$AFTER" ]; then
    echo "    already up to date ($AFTER)"
  else
    echo "    $BEFORE -> $AFTER:"
    git --no-pager log --oneline "$BEFORE..$AFTER" | sed 's/^/      /'
  fi
else
  echo "==> Not a git checkout — deploying the current files as-is."
fi

echo "==> Deploying to /opt/glassdatabase (data, .env, secrets, media, signing key preserved)"
sudo bash "$SRC/deploy/install.sh" "$DOMAIN"

echo "==> Health check"
ok=1
for s in api explore glowtbook admin; do
  if sudo systemctl is-active --quiet "glassdb-$s"; then
    echo "    glassdb-$s: active"
  else
    echo "    glassdb-$s: NOT ACTIVE  —  sudo journalctl -u glassdb-$s -n 50"
    ok=0
  fi
done
[ "$ok" = 1 ] && echo "==> Update complete." || { echo "==> Update finished with service errors (see above)."; exit 1; }
