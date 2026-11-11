#!/usr/bin/env python3
"""On-demand enrichment: DexScreener token-pairs, GeckoTerminal token-pools
pages 1-10, paginated Raydium /pools/info/mint, and (optional, HELIUS_API_KEY)
Helius DAS getAsset metadata."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any

from db import (
    add_pool_token,
    add_snapshot,
    connect,
    mark_enriched,
    to_float,
    upsert_pool,
    upsert_token,
    utc_now,
)
from http_util import get_json, make_session
import helius

GT_BASE = "https://api.geckoterminal.com/api/v2"
DEX_PAIRS = "https://api.dexscreener.com/token-pairs/v1/solana/{mint}"
RAY_MINT = (
    "https://api-v3.raydium.io/pools/info/mint"
    "?mint1={mint}&poolType=all&poolSortField=default&sortType=desc&pageSize=100&page={page}"
)
GT_POOL_PAGES = 10


def _created_from_ms(ms: Any) -> str | None:
    try:
        if ms is None or ms == "" or int(ms) <= 0:
            return None
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def enrich_dexscreener(s, conn, mint: str) -> dict:
    url = DEX_PAIRS.format(mint=mint)
    payload = get_json(s, url)
    if not isinstance(payload, list):
        return {"source": "dexscreener", "pools": 0, "error": f"bad shape {type(payload)}"}
    n = 0
    for pair in payload:
        address = pair.get("pairAddress")
        dex = pair.get("dexId")
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        created = _created_from_ms(pair.get("pairCreatedAt"))
        name = f"{base.get('symbol')}/{quote.get('symbol')}"
        upsert_pool(conn, address, dex, name, created, "dexscreener_token_pairs")
        for role, tok in (("base", base), ("quote", quote)):
            upsert_token(conn, tok.get("address") or "", tok.get("symbol"), tok.get("name"), None, "dexscreener_token_pairs")
            add_pool_token(conn, address, tok.get("address") or "", role)
        liq = (pair.get("liquidity") or {}).get("usd")
        vol = (pair.get("volume") or {}).get("h24")
        tx = (pair.get("txns") or {}).get("h24") or {}
        txns = None
        if isinstance(tx, dict):
            txns = (tx.get("buys") or 0) + (tx.get("sells") or 0)
        add_snapshot(
            conn,
            address,
            "dexscreener_token_pairs",
            to_float(liq),
            to_float(vol),
            to_float(pair.get("priceUsd")),
            txns,
            {"dexId": dex, "labels": pair.get("labels"), "url": pair.get("url")},
        )
        n += 1
    conn.commit()
    return {"source": "dexscreener", "pools": n}


def enrich_gt_token_pools(s, conn, mint: str) -> dict:
    total = 0
    pages = 0
    for page in range(1, GT_POOL_PAGES + 1):
        url = f"{GT_BASE}/networks/solana/tokens/{mint}/pools?page={page}&include=base_token,quote_token,dex"
        try:
            payload = get_json(s, url, provider="geckoterminal")
        except RuntimeError as e:
            if "401" in str(e):
                break
            raise
        included: dict[str, dict] = {}
        for inc in payload.get("included") or []:
            typ, iid = inc.get("type"), inc.get("id")
            if typ and iid:
                included[f"{typ}:{iid}"] = inc
        rows = payload.get("data") or []
        if not rows:
            break
        pages += 1
        for row in rows:
            attrs = row.get("attributes") or {}
            rel = row.get("relationships") or {}
            address = attrs.get("address")
            name = attrs.get("name")
            created = attrs.get("pool_created_at")
            dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id")
            upsert_pool(conn, address, dex_id, name, created, "geckoterminal_token_pools")
            add_pool_token(conn, address, mint, "queried")
            for role in ("base_token", "quote_token"):
                ref = (rel.get(role) or {}).get("data") or {}
                inc = included.get(f"{ref.get('type')}:{ref.get('id')}")
                if not inc:
                    continue
                a = inc.get("attributes") or {}
                upsert_token(conn, a.get("address") or "", a.get("symbol"), a.get("name"), a.get("decimals"), "geckoterminal_token_pools")
                add_pool_token(conn, address, a.get("address") or "", "base" if role == "base_token" else "quote")
            reserve = attrs.get("reserve_in_usd")
            vol = (attrs.get("volume_usd") or {}).get("h24")
            price = attrs.get("base_token_price_usd") or attrs.get("token_price_usd")
            tx = (attrs.get("transactions") or {}).get("h24") or {}
            txns = None
            if isinstance(tx, dict):
                txns = (tx.get("buys") or 0) + (tx.get("sells") or 0)
            add_snapshot(
                conn,
                address,
                "geckoterminal_token_pools",
                to_float(reserve),
                to_float(vol),
                to_float(price),
                txns,
                {"name": name, "dex": dex_id, "created_at": created},
            )
            total += 1
        conn.commit()
    return {"source": "geckoterminal_token_pools", "pools": total, "pages": pages}


def enrich_raydium(s, conn, mint: str) -> dict:
    total = 0
    page = 1
    while True:
        url = RAY_MINT.format(mint=mint, page=page)
        payload = get_json(s, url, timeout=60)
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
            upsert_pool(conn, address, dex, name, created, "raydium_mint_pools")
            for role, tok in (("base", mint_a), ("quote", mint_b)):
                upsert_token(conn, tok.get("address") or "", tok.get("symbol"), tok.get("name"), tok.get("decimals"), "raydium_mint_pools")
                add_pool_token(conn, address, tok.get("address") or "", role)
            day = row.get("day") or {}
            add_snapshot(
                conn,
                address,
                "raydium_mint_pools",
                to_float(row.get("tvl")),
                to_float(day.get("volume")),
                to_float(row.get("price")),
                None,
                {"type": typ, "programId": row.get("programId")},
            )
            total += 1
        conn.commit()
        if not data.get("hasNextPage"):
            break
        page += 1
        if page > 50:
            break
        time.sleep(0.15)
    return {"source": "raydium_mint_pools", "pools": total, "pages": page}


def enrich_mint(mint: str, session=None) -> dict:
    s = session or make_session()
    conn = connect()
    upsert_token(conn, mint, source="enrich")
    results = []
    errors = []
    for fn, label in (
        (enrich_dexscreener, "dexscreener"),
        (enrich_gt_token_pools, "geckoterminal"),
        (enrich_raydium, "raydium"),
        *(((helius.enrich_helius, "helius"),) if helius.enabled() else ()),
    ):
        try:
            print(f"[{utc_now()}] enrich {label} {mint}", flush=True)
            results.append(fn(s, conn, mint))
        except Exception as e:
            err = {"source": label, "error": str(e)}
            errors.append(err)
            results.append(err)
            print(f"[{utc_now()}] enrich {label} failed: {e}", file=sys.stderr, flush=True)
    mark_enriched(conn, mint)
    conn.commit()
    conn.close()
    return {"mint": mint, "results": results, "errors": errors}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: enrich.py <mint>", file=sys.stderr)
        raise SystemExit(2)
    print(json_dumps := __import__("json").dumps(enrich_mint(sys.argv[1]), indent=2))
