#!/usr/bin/env python3
"""One-shot seed: Orca whirlpool list + paginated Raydium pool list."""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone

from db import (
    add_pool_token,
    add_snapshot,
    connect,
    record_poll_cycle,
    to_float,
    upsert_pool,
    upsert_token,
    utc_now,
)
from http_util import get_json, make_session

ORCA_URL = "https://api.mainnet.orca.so/v1/whirlpool/list"
RAY_LIST = (
    "https://api-v3.raydium.io/pools/info/list"
    "?poolType=all&poolSortField=liquidity&sortType=desc&pageSize=1000&page={page}"
)


def seed_orca(s, max_seconds: float | None = None) -> dict:
    started = utc_now()
    t0 = time.time()
    conn = connect()
    pools_new = tokens_new = n = 0
    error = None
    has_more = None
    try:
        print(f"[{utc_now()}] fetching Orca whirlpool list (large)", flush=True)
        payload = get_json(s, ORCA_URL, timeout=120)
        whirlpools = payload.get("whirlpools") or []
        has_more = payload.get("hasMore")
        print(f"[{utc_now()}] orca returned {len(whirlpools)} whirlpools hasMore={has_more}", flush=True)
        for i, w in enumerate(whirlpools, 1):
            address = w.get("address")
            a = w.get("tokenA") or {}
            b = w.get("tokenB") or {}
            name = f"{a.get('symbol')}/{b.get('symbol')}"
            created = None
            ms = w.get("modifiedTimeMs")  # last update, not create; skip as created_at
            upsert_pool(conn, address, "orca", name, created, "orca_whirlpool_list")
            # upsert_pool returns new only; count below with a flag
            # recount via trying insert already handled
            for role, tok in (("base", a), ("quote", b)):
                is_new = upsert_token(
                    conn,
                    tok.get("mint") or "",
                    tok.get("symbol"),
                    tok.get("name"),
                    tok.get("decimals"),
                    "orca_whirlpool_list",
                )
                if is_new:
                    tokens_new += 1
                add_pool_token(conn, address, tok.get("mint") or "", role)
            # track new pools: check first_seen close to now is messy; use upsert return
            # Re-call would not be new. Patch: capture return.
            n += 1
            add_snapshot(
                conn,
                address,
                "orca_whirlpool_list",
                to_float(w.get("tvl")),
                to_float((w.get("volume") or {}).get("day")),
                to_float(w.get("price")),
                None,
                None,  # keep seed snapshots compact
            )
            if i % 1000 == 0:
                conn.commit()
                print(f"[{utc_now()}] orca ingested {i}/{len(whirlpools)}", flush=True)
            if max_seconds and time.time() - t0 > max_seconds:
                print(f"[{utc_now()}] orca stopped at time cap after {i}", flush=True)
                break
        conn.commit()
        # pools_new: count rows whose first_seen_at is this run
        pools_new = conn.execute(
            "SELECT COUNT(*) FROM pools WHERE source='orca_whirlpool_list' AND first_seen_at >= ?",
            (started,),
        ).fetchone()[0]
    except Exception:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
    finally:
        record_poll_cycle(
            conn,
            started_at=started,
            finished_at=utc_now(),
            source="orca_whirlpool_list",
            pages=1,
            rows_fetched=n,
            pools_new=pools_new,
            tokens_new=tokens_new,
            error=error,
            notes=f"hasMore={has_more}",
        )
        conn.close()
    return {"pools": n, "pools_new": pools_new, "tokens_new": tokens_new, "hasMore": has_more, "error": error}


def seed_raydium(s, max_pages: int = 500, max_seconds: float | None = None) -> dict:
    started = utc_now()
    t0 = time.time()
    conn = connect()
    pools_new = tokens_new = n = 0
    page = 1
    error = None
    try:
        while page <= max_pages:
            if max_seconds and time.time() - t0 > max_seconds:
                print(f"[{utc_now()}] raydium stopped at time cap after page {page}", flush=True)
                break
            url = RAY_LIST.format(page=page)
            print(f"[{utc_now()}] raydium list page {page}", flush=True)
            payload = get_json(s, url, timeout=90)
            data = (payload or {}).get("data") or {}
            rows = data.get("data") or []
            if not rows:
                break
            for row in rows:
                address = row.get("id")
                typ = row.get("type")
                dex = f"raydium-{(typ or 'amm').lower()}"
                mint_a = row.get("mintA") or {}
                mint_b = row.get("mintB") or {}
                name = f"{mint_a.get('symbol')}/{mint_b.get('symbol')}"
                created = None
                open_time = row.get("openTime")
                if open_time and str(open_time) not in ("0", "None"):
                    try:
                        ts = int(open_time)
                        if ts > 10_000_000_000:
                            ts //= 1000
                        if ts > 1_000_000_000:
                            created = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (TypeError, ValueError, OSError):
                        created = None
                if upsert_pool(conn, address, dex, name, created, "raydium_pool_list"):
                    pools_new += 1
                for role, tok in (("base", mint_a), ("quote", mint_b)):
                    if upsert_token(
                        conn,
                        tok.get("address") or "",
                        tok.get("symbol"),
                        tok.get("name"),
                        tok.get("decimals"),
                        "raydium_pool_list",
                    ):
                        tokens_new += 1
                    add_pool_token(conn, address, tok.get("address") or "", role)
                day = row.get("day") or {}
                add_snapshot(
                    conn,
                    address,
                    "raydium_pool_list",
                    to_float(row.get("tvl")),
                    to_float(day.get("volume")),
                    to_float(row.get("price")),
                    None,
                    None,
                )
                n += 1
            conn.commit()
            if not data.get("hasNextPage"):
                break
            page += 1
            time.sleep(0.15)
    except Exception:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
    finally:
        record_poll_cycle(
            conn,
            started_at=started,
            finished_at=utc_now(),
            source="raydium_pool_list",
            pages=page,
            rows_fetched=n,
            pools_new=pools_new,
            tokens_new=tokens_new,
            error=error,
        )
        conn.close()
    return {"pools": n, "pools_new": pools_new, "tokens_new": tokens_new, "pages": page, "error": error}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-orca", action="store_true")
    ap.add_argument("--skip-raydium", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args()
    s = make_session()
    if not args.skip_orca:
        r = seed_orca(s, max_seconds=args.max_seconds)
        print(f"[{utc_now()}] orca done {r}", flush=True)
    if not args.skip_raydium:
        r = seed_raydium(s, max_seconds=args.max_seconds)
        print(f"[{utc_now()}] raydium done {r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
