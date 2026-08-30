#!/usr/bin/env bash
#
# Redeploy after a code change. Run from the updated project directory:
#   sudo bash deploy/update.sh
#
# Preserves the live database and .env; updates code + deps; restarts services.
set -euo pipefail

APP_USER="glassdb"
APP_DIR="/opt/glassdatabase"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo/root."; exit 1; fi

echo "==> Syncing code (keeping data/ and .env)"
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'data/glassdb.db' --exclude 'data/glassdb.db-*' \
    --exclude 'data/glowtbook_demo.db' \
    --exclude '.env' --exclude '.htpasswd' \
    --exclude '.streamlit/secrets.toml' \
    "$SRC/" "$APP_DIR/"

echo "==> Updating dependencies"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Restarting services"
systemctl restart glassdb-api glassdb-admin glassdb-explore glassdb-glowtbook
systemctl reload nginx 2>/dev/null || systemctl reload apache2 2>/dev/null || true
echo "==> Done."
