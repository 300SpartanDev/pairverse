#!/usr/bin/env bash
# Idempotent Ubuntu/Debian install: Caddy + systemd units (no Docker).
# Run as root on a fresh VPS AFTER the DB snapshot is on the machine
# or accept an empty DB and re-seed later.
#
#   sudo PAIRVERSE_DOMAIN=pairverse.fun CADDY_EMAIL=you@example.com \
#        ./deploy/install-host.sh
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

DOMAIN="${PAIRVERSE_DOMAIN:-pairverse.fun}"
EMAIL="${CADDY_EMAIL:?set CADDY_EMAIL to a real address for Lets Encrypt}"
APP_DIR=/opt/pairverse
DATA_DIR=/var/lib/pairverse
SRC="$(cd "$(dirname "$0")/.." && pwd)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl sqlite3 python3 python3-venv python3-pip python3-dev \
  build-essential debian-keyring debian-archive-keyring apt-transport-https gnupg ufw rsync

# Official Caddy repo (automatic HTTPS)
if [[ ! -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]]; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
fi
apt-get install -y caddy

id -u pairverse >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin pairverse
mkdir -p "$APP_DIR" "$DATA_DIR/cache" /var/log/pairverse

# Sync application code (never overwrite a live DB in $DATA_DIR)
rsync -a --delete \
  --exclude '.venv' --exclude 'logs' --exclude 'pairs.db' --exclude 'pairs.db-*' \
  --exclude 'cache' --exclude 'mockups' --exclude 'site-captures' --exclude 'bin' \
  --exclude '__pycache__' --exclude '.git' --exclude 'deploy/dist' \
  "$SRC/" "$APP_DIR/"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

chown -R pairverse:pairverse "$APP_DIR" "$DATA_DIR"

install -m 644 "$APP_DIR/deploy/systemd/pairverse-api.service" /etc/systemd/system/pairverse-api.service
install -m 644 "$APP_DIR/deploy/systemd/pairverse-poller.service" /etc/systemd/system/pairverse-poller.service

install -m 644 "$APP_DIR/deploy/Caddyfile.host" /etc/caddy/Caddyfile
mkdir -p /etc/default
cat > /etc/default/caddy <<ENV
CADDY_EMAIL=$EMAIL
PAIRVERSE_DOMAIN=$DOMAIN
ENV
mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/override.conf <<UNIT
[Service]
EnvironmentFile=-/etc/default/caddy
UNIT

# Firewall: SSH + HTTP/HTTPS only. 8765 stays on localhost.
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH || ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
  ufw --force enable || true
fi

systemctl daemon-reload
systemctl enable --now pairverse-api.service pairverse-poller.service
systemctl enable --now caddy

echo
echo "Installed."
echo "  API unit:    systemctl status pairverse-api"
echo "  Poller unit: systemctl status pairverse-poller"
echo "  Caddy:       systemctl status caddy"
echo "  Data:        $DATA_DIR"
echo
echo "DNS is NOT changed by this script. After the server has a public IPv4,"
echo "point $DOMAIN and www.$DOMAIN A (and AAAA if you have IPv6) at it, then"
echo "Caddy will fetch certificates automatically."
echo "If the database is empty, restore a snapshot with deploy/restore-db.sh"
echo "or re-seed: sudo -u pairverse PAIRS_DB=$DATA_DIR/pairs.db $APP_DIR/.venv/bin/python $APP_DIR/seed.py"
