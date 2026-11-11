#!/usr/bin/env python3
"""Shared SQLite helpers for the pairing database."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PAIRS_DB", BASE / "pairs.db"))
MADRID = ZoneInfo("Europe/Madrid")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    decimals INTEGER,
    first_seen_at TEXT,
    sources TEXT,
    last_enriched_at TEXT
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
CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS poll_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL,
    pages INTEGER,
    rows_fetched INTEGER,
    pools_new INTEGER,
    tokens_new INTEGER,
    min_created_at TEXT,
    max_created_at TEXT,
    error TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    called_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pool_tokens_mint ON pool_tokens(mint);
CREATE INDEX IF NOT EXISTS idx_snapshots_pool ON snapshots(pool_address, fetched_at);
CREATE INDEX IF NOT EXISTS idx_tokens_symbol ON tokens(symbol);
CREATE INDEX IF NOT EXISTS idx_pools_created ON pools(created_at);
CREATE INDEX IF NOT EXISTS idx_pools_first_seen ON pools(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_api_calls_provider ON api_calls(provider, called_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_start_utc() -> str:
    now_local = datetime.now(MADRID)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tokens)")}
    if "last_enriched_at" not in cols:
        conn.execute("ALTER TABLE tokens ADD COLUMN last_enriched_at TEXT")
        conn.commit()


def to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def upsert_token(
    conn: sqlite3.Connection,
    mint: str,
    symbol: str | None = None,
    name: str | None = None,
    decimals: int | None = None,
    source: str | None = None,
) -> bool:
    """Insert or update a token. Returns True if newly inserted."""
    if not mint:
        return False
    row = conn.execute("SELECT sources FROM tokens WHERE mint=?", (mint,)).fetchone()
    ts = utc_now()
    if row is None:
        conn.execute(
            "INSERT INTO tokens(mint, symbol, name, decimals, first_seen_at, sources) VALUES (?,?,?,?,?,?)",
            (mint, symbol, name, decimals, ts, source or ""),
        )
        return True
    sources = set(filter(None, (row["sources"] or "").split(",")))
    if source:
        sources.add(source)
    conn.execute(
        """UPDATE tokens SET
             symbol=COALESCE(?, symbol),
             name=COALESCE(?, name),
             decimals=COALESCE(?, decimals),
             sources=?
           WHERE mint=?""",
        (symbol, name, decimals, ",".join(sorted(sources)), mint),
    )
    return False


def mark_enriched(conn: sqlite3.Connection, mint: str) -> None:
    conn.execute("UPDATE tokens SET last_enriched_at=? WHERE mint=?", (utc_now(), mint))


def upsert_pool(
    conn: sqlite3.Connection,
    address: str,
    dex: str | None = None,
    name: str | None = None,
    created_at: str | None = None,
    source: str | None = None,
) -> bool:
    if not address:
        return False
    row = conn.execute("SELECT address FROM pools WHERE address=?", (address,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO pools(address, dex, name, created_at, source, first_seen_at) VALUES (?,?,?,?,?,?)",
            (address, dex, name, created_at, source, utc_now()),
        )
        return True
    conn.execute(
        """UPDATE pools SET
             dex=COALESCE(?, dex),
             name=COALESCE(?, name),
             created_at=COALESCE(?, created_at)
           WHERE address=?""",
        (dex, name, created_at, address),
    )
    return False


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
    liquidity_usd: float | None = None,
    volume_h24: float | None = None,
    price_usd: float | None = None,
    txns_h24: int | None = None,
    raw: dict | None = None,
) -> None:
    payload = json.dumps(raw, separators=(",", ":")) if raw else None
    conn.execute(
        """INSERT INTO snapshots(pool_address, fetched_at, source, liquidity_usd, volume_h24, price_usd, txns_h24, raw_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (pool, utc_now(), source, liquidity_usd, volume_h24, price_usd, txns_h24, payload),
    )


def record_poll_cycle(conn: sqlite3.Connection, **kwargs: Any) -> int:
    cols = [
        "started_at",
        "finished_at",
        "source",
        "pages",
        "rows_fetched",
        "pools_new",
        "tokens_new",
        "min_created_at",
        "max_created_at",
        "error",
        "notes",
    ]
    values = [kwargs.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO poll_cycles({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        values,
    )
    conn.commit()
    return int(cur.lastrowid)


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("tokens", "pools", "pool_tokens", "snapshots", "poll_cycles", "ingest_runs"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return out


def search_tokens(conn: sqlite3.Connection, q: str, limit: int = 25) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    if len(q) >= 32:
        rows = conn.execute(
            "SELECT * FROM tokens WHERE mint = ? COLLATE NOCASE LIMIT ?",
            (q, limit),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
    rows = conn.execute(
        """SELECT t.*,
                  (SELECT COUNT(DISTINCT pt.pool_address) FROM pool_tokens pt WHERE pt.mint = t.mint) AS pair_count
           FROM tokens t
           WHERE t.symbol = ? COLLATE NOCASE OR t.mint = ? COLLATE NOCASE
           ORDER BY pair_count DESC, t.last_enriched_at IS NULL, t.symbol
           LIMIT ?""",
        (q, q, limit),
    ).fetchall()
    if rows:
        return [dict(r) for r in rows]
    like = f"%{q}%"
    rows = conn.execute(
        """SELECT * FROM tokens
           WHERE symbol LIKE ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE OR mint LIKE ?
           ORDER BY symbol
           LIMIT ?""",
        (like, like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def pairs_for_mint(conn: sqlite3.Connection, mint: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.address, p.dex, p.name, p.created_at, p.source, p.first_seen_at,
               s.liquidity_usd, s.volume_h24, s.price_usd, s.fetched_at AS snap_at, s.source AS snap_source,
               GROUP_CONCAT(DISTINCT pt.role) AS roles
        FROM pool_tokens pt
        JOIN pools p ON p.address = pt.pool_address
        LEFT JOIN snapshots s ON s.id = (
            SELECT id FROM snapshots WHERE pool_address = p.address
            ORDER BY fetched_at DESC, id DESC LIMIT 1
        )
        WHERE pt.mint = ?
        GROUP BY p.address
        ORDER BY COALESCE(s.liquidity_usd, -1) DESC, p.address
        """,
        (mint,),
    ).fetchall()
    return [dict(r) for r in rows]


def new_today(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    cutoff = today_start_utc()
    rows = conn.execute(
        """
        SELECT p.address, p.dex, p.name, p.created_at, p.source, p.first_seen_at,
               s.liquidity_usd, s.volume_h24, s.price_usd, s.fetched_at AS snap_at
        FROM pools p
        LEFT JOIN snapshots s ON s.id = (
            SELECT id FROM snapshots WHERE pool_address = p.address
            ORDER BY fetched_at DESC, id DESC LIMIT 1
        )
        WHERE (
                p.created_at IS NOT NULL
                AND p.created_at >= ?
                AND p.created_at <= ?
              )
           OR (
                p.first_seen_at IS NOT NULL AND p.first_seen_at >= ?
                AND (p.created_at IS NULL OR p.created_at < ? OR p.created_at > ?)
                AND p.source IN ('geckoterminal_new_pools','jupiter_recent')
              )
        ORDER BY COALESCE(
            CASE WHEN p.created_at IS NOT NULL AND p.created_at >= ? AND p.created_at <= ? THEN p.created_at END,
            p.first_seen_at
        ) DESC
        LIMIT ?
        """,
        (cutoff, utc_now(), cutoff, cutoff, utc_now(), cutoff, utc_now(), limit),
    ).fetchall()
    return [dict(r) for r in rows]



def new_today_count(conn: sqlite3.Connection) -> int:
    cutoff = today_start_utc()
    now = utc_now()
    return conn.execute(
        """
        SELECT COUNT(*) FROM pools p
        WHERE (
                p.created_at IS NOT NULL
                AND p.created_at >= ?
                AND p.created_at <= ?
              )
           OR (
                p.first_seen_at IS NOT NULL AND p.first_seen_at >= ?
                AND (p.created_at IS NULL OR p.created_at < ? OR p.created_at > ?)
                AND p.source IN ('geckoterminal_new_pools','jupiter_recent')
              )
        """,
        (cutoff, now, cutoff, cutoff, now),
    ).fetchone()[0]


def poll_cycle_report(conn: sqlite3.Connection, limit: int = 50) -> dict[str, Any]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM poll_cycles WHERE source='geckoterminal_new_pools' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()]
    rows = list(reversed(rows))
    gaps = []
    for prev, cur in zip(rows, rows[1:]):
        gap: dict[str, Any] = {"from_id": prev["id"], "to_id": cur["id"]}
        if prev["finished_at"] and cur["started_at"]:
            try:
                t0 = datetime.fromisoformat(prev["finished_at"].replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(cur["started_at"].replace("Z", "+00:00"))
                gap["idle_seconds"] = round((t1 - t0).total_seconds(), 1)
            except ValueError:
                gap["idle_seconds"] = None
        prev_max = prev.get("max_created_at")
        cur_min = cur.get("min_created_at")
        if prev_max and cur_min:
            gap["created_overlap"] = cur_min <= prev_max
            if cur_min > prev_max:
                gap["created_gap"] = {"after": prev_max, "before": cur_min}
        gaps.append(gap)
    return {"cycles": list(reversed(rows)), "gaps": list(reversed(gaps)), "cutoff_today_utc": today_start_utc()}


def recent_polls_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    n = conn.execute("SELECT COUNT(*) FROM poll_cycles").fetchone()[0]
    last = conn.execute("SELECT * FROM poll_cycles ORDER BY id DESC LIMIT 1").fetchone()
    return {"poll_cycles": n, "last": dict(last) if last else None}
