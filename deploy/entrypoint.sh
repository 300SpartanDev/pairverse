#!/bin/bash
# Start the 24/7 poller and the public API in one container, sharing /data.
set -euo pipefail
cd /app
export PYTHONUNBUFFERED=1
export PYTHONPATH=/app
export PAIRS_DB="${PAIRS_DB:-/data/pairs.db}"
export PAIRS_CACHE="${PAIRS_CACHE:-/data/cache}"
mkdir -p "$(dirname "$PAIRS_DB")" "$PAIRS_CACHE" /app/logs

echo "$(date -Is) starting poller (db=$PAIRS_DB cache=$PAIRS_CACHE)"
python /app/poller.py >> /app/logs/poller.log 2>&1 &
POLL_PID=$!

shutdown() {
  echo "$(date -Is) shutting down (poller=$POLL_PID)"
  kill -TERM "$POLL_PID" 2>/dev/null || true
  wait "$POLL_PID" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

# Restart the poller if it dies, without taking down the API.
(
  while true; do
    wait "$POLL_PID" || true
    echo "$(date -Is) poller exited; restarting in 5s" >> /app/logs/poller.log
    sleep 5
    python /app/poller.py >> /app/logs/poller.log 2>&1 &
    POLL_PID=$!
  done
) &

PORT="${PORT:-8765}"
echo "$(date -Is) starting api on :$PORT"
exec python -m uvicorn api:app --host 0.0.0.0 --port "$PORT" --app-dir /app --proxy-headers --forwarded-allow-ips='*'
