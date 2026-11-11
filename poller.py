#!/usr/bin/env python3
"""Always-on poller: GeckoTerminal new_pools pages 1-10 every ~2 minutes,
plus optional Jupiter /tokens/v2/recent metadata."""

from __future__ import annotations

import json
import sys
import time
import traceback

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

GT_BASE = "https://api.geckoterminal.com/api/v2"
JUP_RECENT = "https://lite-api.jup.ag/tokens/v2/recent"
CYCLE_S = 120
PAGES = 10


def parse_gt_token(inc: dict | None) -> tuple[str | None, str | None, str | None, int | None]:
    if not inc:
        return None, None, None, None
    attrs = inc.get("attributes") or {}
    return attrs.get("address"), attrs.get("symbol"), attrs.get("name"), attrs.get("decimals")


def poll_gt_new_pools(s) -> dict:
    started = utc_now()
    included: dict[str, dict] = {}
    created_ats: list[str] = []
    dexes: dict[str, int] = {}
    rows_fetched = 0
    pools_new = 0
    tokens_new = 0
    pages_ok = 0
    error = None
    conn = connect()
    try:
        for page in range(1, PAGES + 1):
            url = f"{GT_BASE}/networks/solana/new_pools?page={page}&include=base_token,quote_token,dex"
            print(f"[{utc_now()}] gt new_pools page {page}", flush=True)
            try:
                payload = get_json(s, url, provider="geckoterminal")
            except RuntimeError as e:
                if "401" in str(e):
                    error = f"page {page} 401 (page cap)"
                    break
                raise
            for inc in payload.get("included") or []:
                typ, iid = inc.get("type"), inc.get("id")
                if typ and iid:
                    included[f"{typ}:{iid}"] = inc
            rows = payload.get("data") or []
            if not rows:
                break
            pages_ok += 1
            for row in rows:
                attrs = row.get("attributes") or {}
                rel = row.get("relationships") or {}
                address = attrs.get("address")
                name = attrs.get("name")
                created = attrs.get("pool_created_at")
                dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id")
                if created:
                    created_ats.append(created)
                if dex_id:
                    dexes[dex_id] = dexes.get(dex_id, 0) + 1
                if upsert_pool(conn, address, dex_id, name, created, "geckoterminal_new_pools"):
                    pools_new += 1
                for role in ("base_token", "quote_token"):
                    ref = (rel.get(role) or {}).get("data") or {}
                    inc = included.get(f"{ref.get('type')}:{ref.get('id')}")
                    mint, symbol, tname, decimals = parse_gt_token(inc)
                    if upsert_token(conn, mint or "", symbol, tname, decimals, "geckoterminal_new_pools"):
                        tokens_new += 1
                    add_pool_token(conn, address, mint or "", "base" if role == "base_token" else "quote")
                reserve = attrs.get("reserve_in_usd")
                vol = (attrs.get("volume_usd") or {}).get("h24")
                price = attrs.get("base_token_price_usd")
                tx = (attrs.get("transactions") or {}).get("h24") or {}
                txns = None
                if isinstance(tx, dict):
                    txns = (tx.get("buys") or 0) + (tx.get("sells") or 0)
                add_snapshot(
                    conn,
                    address,
                    "geckoterminal_new_pools",
                    to_float(reserve),
                    to_float(vol),
                    to_float(price),
                    txns,
                    {"name": name, "dex": dex_id, "created_at": created, "reserve_in_usd": reserve},
                )
                rows_fetched += 1
            conn.commit()
    except Exception:
        error = traceback.format_exc()
        raise
    finally:
        finished = utc_now()
        record_poll_cycle(
            conn,
            started_at=started,
            finished_at=finished,
            source="geckoterminal_new_pools",
            pages=pages_ok,
            rows_fetched=rows_fetched,
            pools_new=pools_new,
            tokens_new=tokens_new,
            min_created_at=min(created_ats) if created_ats else None,
            max_created_at=max(created_ats) if created_ats else None,
            error=error,
            notes=json.dumps({"dexes": dexes}),
        )
        conn.close()
    return {
        "rows_fetched": rows_fetched,
        "pools_new": pools_new,
        "tokens_new": tokens_new,
        "pages": pages_ok,
        "window": {
            "min": min(created_ats) if created_ats else None,
            "max": max(created_ats) if created_ats else None,
        },
        "dexes": dexes,
    }


def poll_jupiter_recent(s) -> dict:
    started = utc_now()
    tokens_new = 0
    n = 0
    error = None
    conn = connect()
    try:
        print(f"[{utc_now()}] jupiter recent", flush=True)
        payload = get_json(s, JUP_RECENT)
        if not isinstance(payload, list):
            raise RuntimeError(f"unexpected jupiter shape {type(payload)}")
        for tok in payload:
            mint = tok.get("id") or tok.get("address")
            first = tok.get("firstPool") or {}
            if upsert_token(
                conn,
                mint or "",
                tok.get("symbol"),
                tok.get("name"),
                tok.get("decimals"),
                "jupiter_recent",
            ):
                tokens_new += 1
            pool_id = first.get("id")
            created = first.get("createdAt") or tok.get("createdAt")
            if pool_id and pool_id != mint:
                upsert_pool(conn, pool_id, tok.get("launchpad") or "unknown", tok.get("symbol"), created, "jupiter_recent")
                add_pool_token(conn, pool_id, mint or "", "base")
            n += 1
        conn.commit()
    except Exception as e:
        error = f"{e}"
        print(f"[{utc_now()}] jupiter recent failed: {e}", file=sys.stderr, flush=True)
    finally:
        record_poll_cycle(
            conn,
            started_at=started,
            finished_at=utc_now(),
            source="jupiter_recent",
            pages=1,
            rows_fetched=n,
            pools_new=0,
            tokens_new=tokens_new,
            error=error,
        )
        conn.close()
    return {"rows": n, "tokens_new": tokens_new, "error": error}


def loop() -> None:
    s = make_session()
    print(f"[{utc_now()}] poller starting, cycle={CYCLE_S}s", flush=True)
    while True:
        t0 = time.time()
        try:
            gt = poll_gt_new_pools(s)
            print(
                f"[{utc_now()}] gt cycle ok fetched={gt['rows_fetched']} new_pools={gt['pools_new']} "
                f"new_tokens={gt['tokens_new']} window={gt['window']}",
                flush=True,
            )
        except Exception:
            print(f"[{utc_now()}] gt cycle failed:\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        try:
            ju = poll_jupiter_recent(s)
            print(f"[{utc_now()}] jupiter cycle ok rows={ju['rows']} new_tokens={ju['tokens_new']}", flush=True)
        except Exception:
            print(f"[{utc_now()}] jupiter cycle failed:\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        elapsed = time.time() - t0
        sleep_for = max(5.0, CYCLE_S - elapsed)
        print(f"[{utc_now()}] sleeping {sleep_for:.0f}s (cycle took {elapsed:.0f}s)", flush=True)
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        loop()
    except KeyboardInterrupt:
        print(f"[{utc_now()}] poller stopped", flush=True)
        raise SystemExit(0)
