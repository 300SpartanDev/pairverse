#!/usr/bin/env bash
# Restart a named service (poller|api) if it exits.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
NAME="${1:?usage: supervise.sh poller|api|tunnel}"
mkdir -p "$ROOT/logs"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT"

case "$NAME" in
  poller)
    CMD=("$PY" "$ROOT/poller.py")
    LOG="$ROOT/logs/poller.log"
    ;;
  api)
    CMD=("$PY" -m uvicorn api:app --host 0.0.0.0 --port 8765 --app-dir "$ROOT")
    LOG="$ROOT/logs/api.log"
    ;;
  tunnel)
    # Cloudflare quick tunnel (no account). The public URL changes whenever this restarts;
    # current URL: logs/tunnel-url.txt  (or ./tunnel-url.sh)
    CMD=("$ROOT/bin/cloudflared" tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8765)
    LOG="$ROOT/logs/tunnel.log"
    ;;
  *)
    echo "unknown service: $NAME" >&2
    exit 2
    ;;
esac

echo "$$" > "$ROOT/logs/supervisor-${NAME}.pid"
while true; do
  echo "$(date -Is) starting $NAME" >> "$ROOT/logs/supervisor-${NAME}.log"
  if [[ "$NAME" == tunnel ]]; then
    rm -f "$ROOT/logs/tunnel-url.txt"
    ( for _ in $(seq 1 60); do
        u="$(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1)"
        if [[ -n "$u" && "$u" != "$(cat "$ROOT/logs/tunnel-url.last" 2>/dev/null)" ]]; then echo "$u" > "$ROOT/logs/tunnel-url.txt"; echo "$u" > "$ROOT/logs/tunnel-url.last"; echo "$(date -Is) tunnel url $u" >> "$ROOT/logs/supervisor-tunnel.log"; break; fi
        sleep 2
      done ) &
  fi
  "${CMD[@]}" >> "$LOG" 2>&1
  code=$?
  echo "$(date -Is) $NAME exited code=$code; restarting in 5s" >> "$ROOT/logs/supervisor-${NAME}.log"
  sleep 5
done
