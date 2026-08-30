#!/usr/bin/env bash
#
# Glass Database — quick install on an existing Ubuntu server.
#
#   sudo bash deploy/install.sh [domain] [--webserver apache|nginx|none]
#
# Run from inside the project directory (the folder with central/, api/,
# admin/, deploy/). Default domain: glassdatabase.org.
#
# The APP TIER (service user, venv, DB, systemd services) is identical no
# matter what fronts it. Only the reverse-proxy layer differs:
#   * apache  — reuse the Apache already on the box (auto-detected). Does NOT
#               touch your other Apache sites.
#   * nginx   — install and configure Nginx.
#   * none    — set up the app + services only; wire your own proxy to
#               127.0.0.1:8000 (API, ROOT_PATH=/api) and :8501 (admin).
#
# Idempotent: safe to re-run. An existing data/glassdb.db is preserved.
#
set -euo pipefail

DOMAIN="glassdatabase.org"
WEBSERVER=""
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --webserver) WEBSERVER="${2:-}"; shift 2 ;;
        --webserver=*) WEBSERVER="${1#*=}"; shift ;;
        *) args+=("$1"); shift ;;
    esac
done
[ "${#args[@]}" -gt 0 ] && DOMAIN="${args[0]}"

APP_USER="glassdb"
APP_DIR="/opt/glassdatabase"
WEB_GROUP="www-data"     # web server runs as this on Debian/Ubuntu (apache + nginx)
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf "\n\033[1;32m==>\033[0m %s\n" "$*"; }
warn() { printf "\n\033[1;33m!!\033[0m %s\n" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo/root."; exit 1; }

# --- sanity: is the full project here, not just deploy/? --------------------
# (Run from the project root: `sudo bash deploy/install.sh <domain>`.)
missing=""
for need in requirements.txt central/ingest.py api/main.py admin/app.py; do
    [ -e "$SRC/$need" ] || missing="$missing $need"
done
if [ -n "$missing" ]; then
    echo "ERROR: '$SRC' doesn't look like the glass-central project."
    echo "       Missing:$missing"
    echo
    echo "You likely copied only the deploy/ folder. Put the WHOLE glass-central"
    echo "folder on the server, then run from its root, e.g.:"
    echo "    tar xzf glass-central.tar.gz && cd glass-central"
    echo "    sudo bash deploy/install.sh $DOMAIN"
    exit 1
fi

# --- auto-detect the web server if not specified ---------------------------
if [ -z "$WEBSERVER" ]; then
    if systemctl is-active --quiet apache2 || command -v apache2ctl >/dev/null 2>&1; then
        WEBSERVER="apache"
    elif command -v nginx >/dev/null 2>&1; then
        WEBSERVER="nginx"
    else
        WEBSERVER="nginx"   # nothing present -> install nginx
    fi
fi
say "Domain: $DOMAIN   |   Web server: $WEBSERVER"

# --- base packages (web-server-agnostic) -----------------------------------
say "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync apache2-utils openssl certbot ffmpeg

# --- service user + app dir ------------------------------------------------
say "Creating service user '$APP_USER' and $APP_DIR"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR/data" "$APP_DIR/public"
mkdir -p "$APP_DIR/data/dip_media" "$APP_DIR/data/aip_bags" "$APP_DIR/data/c2pa"

# --- copy app (preserve an existing DB + generated media/certs) -------------
say "Copying application to $APP_DIR (preserving any existing database)"
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'data/glassdb.db' --exclude 'data/glassdb.db-*' \
    --exclude 'data/glowtbook_demo.db' \
    --exclude 'data/glowtbook_media' --exclude 'data/dip_media' \
    --exclude 'data/aip_bags' --exclude 'data/c2pa' \
    --exclude '.env' --exclude '.htpasswd' \
    --exclude '.streamlit/secrets.toml' \
    --exclude 'public' \
    "$SRC/" "$APP_DIR/"
cp "$SRC/deploy/landing-index.html" "$APP_DIR/public/index.html"
if [ ! -f "$APP_DIR/data/glassdb.db" ] && [ -f "$SRC/data/glassdb.db" ]; then
    cp "$SRC/data/glassdb.db" "$APP_DIR/data/glassdb.db"
    say "Seeded initial database from the shipped glassdb.db"
elif [ -f "$APP_DIR/data/glassdb.db" ]; then
    say "Kept existing server database"
else
    warn "No data/glassdb.db yet. Build it after install with:"
    echo "   sudo -u $APP_USER $APP_DIR/.venv/bin/python -m central.ingest build --uploads <xlsx-folder>"
fi

# --- venv + deps -----------------------------------------------------------
say "Creating virtualenv and installing requirements"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- environment file ------------------------------------------------------
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
GLASSDB_PATH=$APP_DIR/data/glassdb.db
ROOT_PATH=/api
GLASSDB_ADMIN_TOKEN=$(openssl rand -hex 24)
# Object contributions: reviewed before going public (1) or published immediately (0)
GLASSDB_MODERATION=1
# Public base URL (used to build verifiable image/video links)
PUBLIC_BASE_URL=https://$DOMAIN
# --- AIP -> MinIO / S3 (optional; leave blank to keep bags local) ----------
# MINIO_ENDPOINT=minio.host:9000
# MINIO_ACCESS_KEY=
# MINIO_SECRET_KEY=
# MINIO_BUCKET=glassdb-aip
# MINIO_SECURE=1
# For Turso instead of the local file, set these and \`pip install libsql\`:
# TURSO_DATABASE_URL=
# TURSO_AUTH_TOKEN=
EOF
    say "Wrote $ENV_FILE with a fresh API admin token (X-API-Key)"
else
    say "Kept existing $ENV_FILE"
fi

# --- admin web login (basic auth) ------------------------------------------
HTPASSWD="$APP_DIR/.htpasswd"
ADMIN_PW=""
if [ ! -f "$HTPASSWD" ]; then
    ADMIN_PW="$(openssl rand -base64 12)"
    htpasswd -bc "$HTPASSWD" admin "$ADMIN_PW" >/dev/null 2>&1
fi

# --- ownership -------------------------------------------------------------
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"; chown "$APP_USER:$APP_USER" "$ENV_FILE"
# the web server (www-data) must be able to READ the htpasswd file:
chown "$APP_USER:$WEB_GROUP" "$HTPASSWD"; chmod 640 "$HTPASSWD"

# --- systemd services (identical for every web server) ---------------------
say "Installing systemd services"
for svc in api admin explore glowtbook; do
    cp "$APP_DIR/deploy/glassdb-$svc.service" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now glassdb-api.service glassdb-admin.service \
                       glassdb-explore.service glassdb-glowtbook.service

# --- reverse proxy ---------------------------------------------------------
CERTBOT_CMD=""

setup_apache() {
    say "Configuring Apache (existing sites left untouched)"
    command -v apache2ctl >/dev/null 2>&1 || apt-get install -y -qq apache2
    apt-get install -y -qq python3-certbot-apache >/dev/null 2>&1 || true
    a2enmod proxy proxy_http proxy_wstunnel headers ssl >/dev/null
    cp "$APP_DIR/deploy/apache-glassdatabase-proxy.conf" /etc/apache2/glassdatabase-proxy.conf
    local site="/etc/apache2/sites-available/glassdatabase.conf"
    sed "s/glassdatabase\.org/$DOMAIN/g" \
        "$APP_DIR/deploy/apache-glassdatabase.conf" > "$site"
    if apache2ctl -S 2>/dev/null | grep -qi "namevhost $DOMAIN "; then
        warn "An Apache vhost for $DOMAIN already exists. Review $site before enabling."
    fi
    a2ensite glassdatabase.conf >/dev/null
    apache2ctl configtest
    systemctl reload apache2
    CERTBOT_CMD="sudo certbot --apache -d $DOMAIN -d www.$DOMAIN"
}

setup_nginx() {
    say "Configuring Nginx"
    command -v nginx >/dev/null 2>&1 || apt-get install -y -qq nginx
    apt-get install -y -qq python3-certbot-nginx >/dev/null 2>&1 || true
    local site="/etc/nginx/sites-available/glassdatabase.conf"
    sed "s/glassdatabase\.org/$DOMAIN/g" \
        "$APP_DIR/deploy/nginx-glassdatabase.conf" > "$site"
    ln -sf "$site" /etc/nginx/sites-enabled/glassdatabase.conf
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl reload nginx
    CERTBOT_CMD="sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN"
}

case "$WEBSERVER" in
    apache) setup_apache ;;
    nginx)  setup_nginx ;;
    none)   say "Skipping proxy setup (--webserver none)" ;;
    *) echo "Unknown --webserver '$WEBSERVER' (use apache|nginx|none)"; exit 1 ;;
esac

# --- firewall (best effort) ------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    if [ "$WEBSERVER" = "apache" ]; then ufw allow 'Apache Full' >/dev/null 2>&1 || true
    elif [ "$WEBSERVER" = "nginx" ]; then ufw allow 'Nginx Full' >/dev/null 2>&1 || true
    fi
fi

# --- summary ---------------------------------------------------------------
say "Done. Services:"
systemctl --no-pager --lines=0 status glassdb-api glassdb-admin glassdb-explore glassdb-glowtbook 2>/dev/null | grep -E "glassdb|Active" || true

echo
echo "  Glass Database app tier is running (API :8000, admin :8501, explore :8502, glowtbook :8503)."
if [ "$WEBSERVER" = "none" ]; then
    echo "  Wire your own proxy to those localhost ports (see deploy/*.conf for the paths)."
else
    echo "  Live over HTTP via $WEBSERVER:"
    echo "    • Home      : http://$DOMAIN/"
    echo "    • API       : http://$DOMAIN/api/      (docs: /api/docs)"
    echo "    • Explore   : http://$DOMAIN/explore/"
    echo "    • Glowtbook : http://$DOMAIN/glowtbook/  (shared demo)"
    echo "    • Admin     : http://$DOMAIN/admin/     (login: admin${ADMIN_PW:+ / $ADMIN_PW})"
    echo
    echo "  Enable HTTPS once DNS for $DOMAIN points here:"
    echo "    $CERTBOT_CMD"
fi
echo
echo "  Manage:"
echo "    sudo systemctl restart glassdb-api glassdb-admin"
echo "    sudo journalctl -u glassdb-api -f"
echo "    sudo -u $APP_USER $APP_DIR/.venv/bin/python -m central.ingest list"
echo
echo "  API admin key (X-API-Key, unlocks restricted data) is in $ENV_FILE."
[ -n "$ADMIN_PW" ] && echo "  NOTE: save the admin web password above; change it with: sudo htpasswd $HTPASSWD admin"
