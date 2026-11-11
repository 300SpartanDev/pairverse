#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
stop_tree() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      # kill supervisor and its children
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
      sleep 0.2
      kill -9 "$pid" 2>/dev/null || true
      echo "stopped $pidfile ($pid)"
    fi
    rm -f "$pidfile"
  fi
}
stop_tree "$ROOT/logs/supervisor-poller.pid"
stop_tree "$ROOT/logs/supervisor-api.pid"
stop_tree "$ROOT/logs/supervisor-tunnel.pid"
# leftover workers
pkill -f "$ROOT/poller.py" 2>/dev/null || true
pkill -f "uvicorn api:app --host 0.0.0.0 --port 8765" 2>/dev/null || true
pkill -f "$ROOT/bin/cloudflared tunnel" 2>/dev/null || true
# uvicorn can hang on open SSE streams during graceful shutdown
sleep 2
pkill -9 -f "$ROOT/poller.py" 2>/dev/null || true
pkill -9 -f "uvicorn api:app --host 0.0.0.0 --port 8765" 2>/dev/null || true
echo "stopped pairing-db services"
