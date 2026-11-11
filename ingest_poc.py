#!/usr/bin/env python3
"""Proof-of-concept Solana pairing-database ingest.

Pulls:
  1) GeckoTerminal /networks/solana/new_pools pages 1-10 (free cap).
     This is the newest ~200 pools, NOT a full 24h window — the script
     records the actual created_at span so the gap is visible.
  2) DexScreener /token-pairs/v1/solana/{mint} for 3 sample tokens
     (BONK, WIF, plus the newest GeckoTerminal base token).
  3) GeckoTerminal /tokens/{mint}/pools page 1 for those same tokens
     (20 pools/page, free max page 10).

Stores everything in SQLite: tokens, pools, pool_tokens, snapshots.

Usage:
  python3 ingest_poc.py
  python3 ingest_poc.py --lookup DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "pairs.db"
UA = "pairing-db-poc/0.1 (research; local ingest)"
GT_BASE = "https://api.geckoterminal.com/api/v2"
DEX_PAIRS = "https://api.dexscreener.com/token-pairs/v1/solana/{mint}"

SAMPLE_TOKENS = [
    ("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "BONK"),
    ("EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "WIF"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    decimals INTEGER,
    first_seen_at TEXT,
    sources TEXT
);
CREATE TABLE IF NOT EXISTS pools (
    address TEXT PRIMARY KEY,
    dex TEXT,
    name TEXT,
    created_at TEXT,
    source TEXT,
    first_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS pool_tokens (
    pool_address TEXT NOT NULL,
    mint TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (pool_address, mint, role)
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_address TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL,
    liquidity_usd REAL,
    volume_h24 REAL,
    price_usd REAL,
    txns_h24 INTEGER,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pool_tokens_mint ON pool_tokens(mint);
CREATE INDEX IF NOT EXISTS idx_snapshots_pool ON snapshots(pool_address, fetched_at);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    notes TEXT
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


def get_json(s: requests.Session, url: str, *, retries: int = 3, sleep_s: float = 7.0) -> Any:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 401:
                raise RuntimeError(f"{r.status_code} {url} body={r.text[:300]}")
            if r.status_code == 429:
                wait = sleep_s * (i + 2)
                print(f"  rate-limited {r.status_code} on {url}, sleep {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if "401" in str(e):
                raise
            print(f"  retry {i+1} after error: {e}", file=sys.stderr)
            time.sleep(sleep_s)
    raise last  # type: ignore[misc]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_token(
    conn: sqlite3.Connection,
    mint: str,
    symbol: str | None,
    name: str | None,
    decimals: int | None,
    source: str,
) -> None:
    if not mint:
        return
    row = conn.execute("SELECT sources FROM tokens WHERE mint=?", (mint,)).fetchone()
    ts = utc_now()
    if row is None:
        conn.execute(
            "INSERT INTO tokens(mint, symbol, name, decimals, first_seen_at, sources) VALUES (?,?,?,?,?,?)",
            (mint, symbol, name, decimals, ts, source),
        )
        return
    sources = set(filter(None, (row["sources"] or "").split(",")))
    sources.add(source)
    conn.execute(
        "UPDATE tokens SET symbol=COALESCE(?, symbol), name=COALESCE(?, name), decimals=COALESCE(?, decimals), sources=? WHERE mint=?",
        (symbol, name, decimals, ",".join(sorted(sources)), mint),
    )


def upsert_pool(
    conn: sqlite3.Connection,
    address: str,
    dex: str | None,
    name: str | None,
    created_at: str | None,
    source: str,
) -> None:
    if not address:
        return
    row = conn.execute("SELECT address FROM pools WHERE address=?", (address,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO pools(address, dex, name, created_at, source, first_seen_at) VALUES (?,?,?,?,?,?)",
            (address, dex, name, created_at, source, utc_now()),
        )
        return
    conn.execute(
        "UPDATE pools SET dex=COALESCE(?, dex), name=COALESCE(?, name), created_at=COALESCE(?, created_at) WHERE address=?",
        (dex, name, created_at, address),
    )


def add_pool_token(conn: sqlite3.Connection, pool: str, mint: str, role: str) -> None:
    if not pool or not mint:
        return
    conn.execute(
        "INSERT OR IGNORE INTO pool_tokens(pool_address, mint, role) VALUES (?,?,?)",
        (pool, mint, role),
    )


def add_snapshot(
    conn: sqlite3.Connection,
    pool: str,
    source: str,
    liquidity_usd: float | None,
    volume_h24: float | None,
    price_usd: float | None,
    txns_h24: int | None,
    raw: dict,
) -> None:
    conn.execute(
        "INSERT INTO snapshots(pool_address, fetched_at, source, liquidity_usd, volume_h24, price_usd, txns_h24, raw_json) VALUES (?,?,?,?,?,?,?,?)",
        (pool, utc_now(), source, liquidity_usd, volume_h24, price_usd, txns_h24, json.dumps(raw, separators=(",", ":"))),
    )


def parse_gt_token(inc: dict | None) -> tuple[str | None, str | None, str | None, int | None]:
    if not inc:
        return None, None, None, None
    attrs = inc.get("attributes") or {}
    return attrs.get("address"), attrs.get("symbol"), attrs.get("name"), attrs.get("decimals")


def ingest_gt_new_pools(s: requests.Session, conn: sqlite3.Connection) -> dict:
    included_index: dict[str, dict] = {}
    created_ats: list[str] = []
    n_pools = 0
    dexes: dict[str, int] = {}
    notes: list[str] = []
    for page in range(1, 11):
        url = f"{GT_BASE}/networks/solana/new_pools?page={page}&include=base_token,quote_token,dex"
        print(f"[gt] new_pools page {page}")
        try:
            payload = get_json(s, url)
        except RuntimeError as e:
            notes.append(f"page {page} failed: {e}")
            break
        for inc in payload.get("included") or []:
            typ = inc.get("type")
            iid = inc.get("id")
            if typ and iid:
                included_index[f"{typ}:{iid}"] = inc
        rows = payload.get("data") or []
        if not rows:
            notes.append(f"page {page} empty")
            break
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
            upsert_pool(conn, address, dex_id, name, created, "geckoterminal_new_pools")
            for role in ("base_token", "quote_token"):
                ref = (rel.get(role) or {}).get("data") or {}
                inc = included_index.get(f"{ref.get('type')}:{ref.get('id')}")
                mint, symbol, tname, decimals = parse_gt_token(inc)
                upsert_token(conn, mint or "", symbol, tname, decimals, "geckoterminal_new_pools")
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
                {
                    "id": row.get("id"),
                    "name": name,
                    "dex": dex_id,
                    "created_at": created,
                    "reserve_in_usd": reserve,
                    "volume_usd": attrs.get("volume_usd"),
                },
            )
            n_pools += 1
        conn.commit()
        if page < 10:
            time.sleep(7)
    window = {
        "min_created_at": min(created_ats) if created_ats else None,
        "max_created_at": max(created_ats) if created_ats else None,
    }
    return {"pools": n_pools, "created_window": window, "dexes": dexes, "notes": notes}


def ingest_gt_token_pools(s: requests.Session, conn: sqlite3.Connection, mint: str, label: str) -> dict:
    url = f"{GT_BASE}/networks/solana/tokens/{mint}/pools?page=1"
    print(f"[gt] token pools page 1 for {label} {mint}")
    payload = get_json(s, url)
    n = 0
    for row in payload.get("data") or []:
        attrs = row.get("attributes") or {}
        rel = row.get("relationships") or {}
        address = attrs.get("address")
        name = attrs.get("name")
        created = attrs.get("pool_created_at")
        dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id")
        upsert_pool(conn, address, dex_id, name, created, "geckoterminal_token_pools")
        add_pool_token(conn, address, mint, "queried")
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
            {"id": row.get("id"), "name": name, "dex": dex_id, "created_at": created},
        )
        n += 1
    conn.commit()
    return {"pools": n}


def ingest_dexscreener_pairs(s: requests.Session, conn: sqlite3.Connection, mint: str, label: str) -> dict:
    url = DEX_PAIRS.format(mint=mint)
    print(f"[dex] token-pairs for {label} {mint}")
    payload = get_json(s, url, sleep_s=2)
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected dexscreener shape: {type(payload)}")
    for pair in payload:
        address = pair.get("pairAddress")
        dex = pair.get("dexId")
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        created_ms = pair.get("pairCreatedAt")
        created = None
        if created_ms:
            created = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
            {
                "dexId": dex,
                "labels": pair.get("labels"),
                "liquidity": pair.get("liquidity"),
                "volume": pair.get("volume"),
                "url": pair.get("url"),
            },
        )
    conn.commit()
    return {"pools": len(payload)}


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for table in ("tokens", "pools", "pool_tokens", "snapshots", "ingest_runs"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return out


def lookup(conn: sqlite3.Connection, mint: str) -> None:
    tok = conn.execute("SELECT * FROM tokens WHERE mint=?", (mint,)).fetchone()
    print(f"token: {dict(tok) if tok else 'not in tokens table (may still have pool_tokens)'}")
    rows = conn.execute(
        """
        SELECT p.address, p.dex, p.name, p.created_at, p.source,
               s.liquidity_usd, s.volume_h24, s.price_usd, s.fetched_at, s.source AS snap_source
        FROM pool_tokens pt
        JOIN pools p ON p.address = pt.pool_address
        LEFT JOIN snapshots s ON s.id = (
            SELECT id FROM snapshots WHERE pool_address = p.address ORDER BY fetched_at DESC, id DESC LIMIT 1
        )
        WHERE pt.mint = ?
        GROUP BY p.address
        ORDER BY COALESCE(s.liquidity_usd, 0) DESC
        """,
        (mint,),
    ).fetchall()
    print(f"pairs: {len(rows)}")
    for r in rows:
        print(
            f"  {r['dex'] or '?':16} {r['name'] or '':20} "
            f"liq=${(r['liquidity_usd'] or 0):,.0f} vol24=${(r['volume_h24'] or 0):,.0f} "
            f"{r['address']} src={r['source']}"
        )


def newest_base_mint(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT pt.mint
        FROM pools p
        JOIN pool_tokens pt ON pt.pool_address = p.address AND pt.role = 'base'
        WHERE p.source = 'geckoterminal_new_pools'
        ORDER BY p.created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return row["mint"] if row else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookup", help="print stored pairs for a mint and exit")
    args = ap.parse_args()
    conn = connect()
    if args.lookup:
        lookup(conn, args.lookup)
        return 0

    started = utc_now()
    s = make_session()
    run_notes: dict[str, Any] = {}

    gt = ingest_gt_new_pools(s, conn)
    run_notes["geckoterminal_new_pools"] = gt
    print(f"[gt] ingested {gt['pools']} new pools, created_at window {gt['created_window']}")

    third_mint = newest_base_mint(conn)
    samples = list(SAMPLE_TOKENS)
    if third_mint and third_mint not in {m for m, _ in samples}:
        samples.append((third_mint, "NEWEST_GT_BASE"))
    run_notes["sample_tokens"] = samples

    ds_results = {}
    for mint, label in samples:
        upsert_token(conn, mint, label if label != "NEWEST_GT_BASE" else None, None, None, "sample")
        ds_results[label] = ingest_dexscreener_pairs(s, conn, mint, label)
        time.sleep(0.3)
    run_notes["dexscreener_token_pairs"] = ds_results

    print("[gt] sleeping 60s before token-pool lookups to respect ~10 calls/min")
    time.sleep(60)
    gt_tok = {}
    for i, (mint, label) in enumerate(samples):
        gt_tok[label] = ingest_gt_token_pools(s, conn, mint, label)
        if i < len(samples) - 1:
            time.sleep(7)
    run_notes["geckoterminal_token_pools"] = gt_tok

    finished = utc_now()
    conn.execute(
        "INSERT INTO ingest_runs(started_at, finished_at, notes) VALUES (?,?,?)",
        (started, finished, json.dumps(run_notes, indent=2)),
    )
    conn.commit()
    c = counts(conn)
    print("\n=== row counts ===")
    for k, v in c.items():
        print(f"  {k:14} {v}")
    print("\n=== notes ===")
    print(json.dumps(run_notes, indent=2))
    print(f"\nDB: {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
