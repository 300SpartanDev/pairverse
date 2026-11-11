#!/usr/bin/env bash
# Start (or no-op if already running) the pairing-db poller and API supervisors.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"
chmod +x "$ROOT/supervise.sh" "$ROOT/stop.sh" 2>/dev/null || true

is_running() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

start_one() {
  local name="$1"
  local pidfile="$ROOT/logs/supervisor-${name}.pid"
  if is_running "$pidfile"; then
    echo "$name supervisor already running pid=$(cat "$pidfile")"
    return 0
  fi
  nohup "$ROOT/supervise.sh" "$name" >/dev/null 2>&1 &
  disown || true
  sleep 0.3
  echo "started $name supervisor pid=$(cat "$pidfile" 2>/dev/null || echo unknown)"
}

start_one poller
start_one api
echo "API + site: http://127.0.0.1:8765/   (classic UI: /classic)"
# Optional public Cloudflare quick tunnel (needs bin/cloudflared; some networks block the
# tunnel edge on port 7844). Enable with: PAIRS_TUNNEL=1 ./start.sh
if [[ "${PAIRS_TUNNEL:-0}" == 1 ]]; then
  start_one tunnel
  for _ in $(seq 1 20); do [[ -s "$ROOT/logs/tunnel-url.txt" ]] && break; sleep 1; done
  echo "public: $(cat "$ROOT/logs/tunnel-url.txt" 2>/dev/null || echo 'starting… see logs/tunnel-url.txt')  (check it answers: ./tunnel-url.sh)"
fi
echo "logs: $ROOT/logs/"
echo "stop: $ROOT/stop.sh"
echo "restart: $ROOT/stop.sh && $ROOT/start.sh"
