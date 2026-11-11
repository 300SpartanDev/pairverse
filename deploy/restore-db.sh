#!/usr/bin/env bash
# Install a snapshot onto a server data directory.
# Usage: restore-db.sh /path/to/pairs-YYYYMMDD.db  [/var/lib/pairverse]
set -euo pipefail
SRC="${1:?usage: restore-db.sh <snapshot.db> [data-dir]}"
DEST_DIR="${2:-/var/lib/pairverse}"
DEST="$DEST_DIR/pairs.db"
if [[ ! -f "$SRC" ]]; then
  echo "snapshot not found: $SRC" >&2
  exit 1
fi
mkdir -p "$DEST_DIR"
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop pairverse-poller.service pairverse-api.service 2>/dev/null || true
fi
if command -v docker >/dev/null 2>&1; then
  docker compose -f /opt/pairverse/deploy/docker-compose.yml stop app 2>/dev/null || true
fi
# Remove leftover WAL/SHM so we do not mix a new file with an old journal.
rm -f "$DEST" "$DEST-wal" "$DEST-shm"
cp -f "$SRC" "$DEST"
chmod 644 "$DEST"
sqlite3 "$DEST" "PRAGMA integrity_check;" | tail -1
echo "restored $DEST"
echo "Start the app after this (systemd start or docker compose up)."
