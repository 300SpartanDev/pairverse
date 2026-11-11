#!/usr/bin/env bash
# Idempotent Ubuntu/Debian install via Docker Compose + Caddy.
# Run as root on the VPS.
#
#   sudo CADDY_EMAIL=you@example.com ./deploy/install-docker.sh
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

EMAIL="${CADDY_EMAIL:?set CADDY_EMAIL to a real address for Lets Encrypt}"
DOMAIN="${PAIRVERSE_DOMAIN:-pairverse.fun}"
APP_DIR=/opt/pairverse
DATA_DIR=/var/lib/pairverse
SRC="$(cd "$(dirname "$0")/.." && pwd)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl sqlite3 gnupg ufw rsync

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

mkdir -p "$APP_DIR" "$DATA_DIR/cache"
rsync -a --delete \
  --exclude '.venv' --exclude 'logs' --exclude 'pairs.db' --exclude 'pairs.db-*' \
  --exclude 'cache' --exclude 'mockups' --exclude 'site-captures' --exclude 'bin' \
  --exclude '__pycache__' --exclude '.git' --exclude 'deploy/dist' \
  "$SRC/" "$APP_DIR/"

cat > "$APP_DIR/deploy/.env" <<ENV
CADDY_EMAIL=$EMAIL
PAIRVERSE_DOMAIN=$DOMAIN
ENV
chmod 600 "$APP_DIR/deploy/.env"

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH || ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
  ufw --force enable || true
fi

cd "$APP_DIR"
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build

echo
echo "Installed via Docker Compose."
echo "  Data: $DATA_DIR"
echo "  Logs: docker compose -f $APP_DIR/deploy/docker-compose.yml logs -f"
echo "DNS is NOT changed by this script. Point $DOMAIN at this server IPv4 when ready."
