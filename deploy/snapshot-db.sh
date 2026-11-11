#!/usr/bin/env bash
# Consistent snapshot of the SQLite DB (VACUUM INTO a new file).
# Safe to run on a live machine while the poller is up.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${PAIRS_DB:-$ROOT/pairs.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-$ROOT/deploy/dist/pairs-$STAMP.db}"
mkdir -p "$(dirname "$OUT")"
if [[ ! -f "$SRC" ]]; then
  echo "database not found: $SRC" >&2
  exit 1
fi
# Best-effort checkpoint so the main file is close to complete.
sqlite3 "$SRC" "PRAGMA busy_timeout=60000; PRAGMA wal_checkpoint(PASSIVE);" >/dev/null || true
rm -f "$OUT"
sqlite3 "$SRC" "PRAGMA busy_timeout=60000; VACUUM INTO '$OUT';"
python3 -c "
import os, sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(p)
tokens, pools = c.execute('select (select count(*) from tokens), (select count(*) from pools)').fetchone()
print('snapshot', p)
print('  size  ', f'{os.path.getsize(p):,} bytes')
print('  tokens', f'{tokens:,}')
print('  pools ', f'{pools:,}')
" "$OUT"
echo "Copy this file to the VPS as /var/lib/pairverse/pairs.db (see deploy/README.md)."
