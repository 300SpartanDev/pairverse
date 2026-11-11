#!/usr/bin/env bash
# Print the current public Cloudflare quick-tunnel URL (changes whenever the tunnel restarts) and whether it answers.
ROOT="$(cd "$(dirname "$0")" && pwd)"
u="$(cat "$ROOT/logs/tunnel-url.txt" 2>/dev/null)"
[[ -z "$u" ]] && { echo "tunnel not up (start with: PAIRS_TUNNEL=1 $ROOT/start.sh; log: $ROOT/logs/tunnel.log)"; exit 1; }
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$u/health")"
echo "$u  (health: HTTP $code$([[ $code == 530 ]] && echo ' = tunnel registered but not connected to the Cloudflare edge'))"
