# Self-hosting Pairverse

This folder turns any small Linux VPS into a copy of pairverse.fun: the API, the Liquidity Universe front end, the 24/7 poller and Caddy with automatic HTTPS.

- **Recommended:** `install-host.sh` on a fresh Ubuntu 24.04 (or 22.04) server: systemd units + Caddy.
- **Alternative:** `install-docker.sh`: Docker Compose (app + Caddy).

The poller and the API must run on the **same machine** and share one SQLite file in WAL mode. Don't split them across hosts.

## What's in here

| Path | Role |
| --- | --- |
| `install-host.sh` | Ubuntu/Debian: packages, Python venv, systemd units, Caddy, ufw (22/80/443) |
| `install-docker.sh` | Ubuntu/Debian: Docker, `compose up`, ufw |
| `systemd/pairverse-api.service` | API + site on `127.0.0.1:8765` |
| `systemd/pairverse-poller.service` | 24/7 new-pool ingest |
| `Caddyfile.host` / `Caddyfile` | HTTPS reverse proxy (host / Docker). SSE-safe (`flush_interval -1`) |
| `Dockerfile`, `docker-compose.yml`, `entrypoint.sh` | Container build and production compose (publishes 80/443) |
| `docker-compose.local.yml` | Local smoke test on `127.0.0.1:8765`, no TLS |
| `snapshot-db.sh` | Consistent copy of a live database (`VACUUM INTO`) |
| `restore-db.sh` | Install a snapshot into `/var/lib/pairverse` |

On the server, code lives in `/opt/pairverse` and data (database, WAL, cache) in `/var/lib/pairverse`. The app reads `PAIRS_DB` and `PAIRS_CACHE` to find them.

## Requirements

- Ubuntu 24.04 / 22.04 (or Debian), public IPv4 (IPv6 optional), ports 22/80/443 open
- 1 vCPU / 2 GB RAM minimum (4 GB is comfortable), 20 GB+ disk. The database grows by roughly a few GB a month.
- A domain you control and an email address for Let's Encrypt
- Outbound HTTPS to GeckoTerminal, DexScreener, Raydium, Orca and Jupiter

## Install (systemd + Caddy)

```bash
# on your machine: copy the repo to the server
scp -r pairverse root@SERVER_IP:/tmp/pairverse

# on the server
export CADDY_EMAIL=you@example.com
export PAIRVERSE_DOMAIN=example.com
bash /tmp/pairverse/deploy/install-host.sh
```

The script starts the services with an empty database. Then either:

- **Restore a snapshot** you already have (keeps all history):

  ```bash
  # where the old database lives
  deploy/snapshot-db.sh                      # writes deploy/dist/pairs-<timestamp>.db
  scp deploy/dist/pairs-*.db root@SERVER_IP:/tmp/pairs-snapshot.db
  # on the server
  bash /opt/pairverse/deploy/restore-db.sh /tmp/pairs-snapshot.db
  chown -R pairverse:pairverse /var/lib/pairverse
  rm -f /var/lib/pairverse/cache/*           # drop the cache built from the empty DB
  systemctl start pairverse-api pairverse-poller
  ```

- **Or seed from scratch** with every current Raydium and Orca pool (takes a while), then let the poller pick up new pools from now on:

  ```bash
  sudo -u pairverse PAIRS_DB=/var/lib/pairverse/pairs.db \
    /opt/pairverse/.venv/bin/python /opt/pairverse/seed.py
  ```

## DNS and HTTPS

1. Check the app answers on the server: `curl -s http://127.0.0.1:8765/health`.
2. At your registrar, create `A` records for `@` and `www` pointing at the server (and `AAAA` if it has working IPv6).
3. Once DNS resolves, Caddy fetches the certificates by itself. Check `https://your-domain/health`.

Caddy can't get a certificate until DNS points at the server. Until then, test on the server itself.

## Docker alternative

```bash
CADDY_EMAIL=you@example.com PAIRVERSE_DOMAIN=example.com bash /tmp/pairverse/deploy/install-docker.sh
# restore a snapshot as above, then
docker compose -f /opt/pairverse/deploy/docker-compose.yml up -d
```

Local image smoke test (no public ports):

```bash
docker compose -f deploy/docker-compose.local.yml up --build    # http://127.0.0.1:8765
```

## Optional: Helius

To enrich tokens with Helius DAS metadata, add your key to the poller and API units:

```bash
systemctl edit pairverse-api      # and pairverse-poller
# [Service]
# Environment=HELIUS_API_KEY=your-key
systemctl restart pairverse-api pairverse-poller
```

Without a key, nothing changes.

## Operations

- Logs: `journalctl -u pairverse-api -u pairverse-poller -f`
- Restart: `systemctl restart pairverse-api` (stop timeout is 10 s so open SSE streams don't stall it)
- Backups: run `snapshot-db.sh` daily (set `PAIRS_DB=/var/lib/pairverse/pairs.db`) and copy the file off the server.
- Hardening: `unattended-upgrades` and `fail2ban` are worth installing; a 2 GB swap file helps on 4 GB machines.
