#!/usr/bin/env python3
"""Public, read-only API for the Liquidity Universe site.

- /api/v1/galaxy.bin   float32 buffer of real pools (stride 6), regenerated from the DB every 10 min
- /api/v1/search?q=    autocomplete (symbol / name prefix, or mint), ranked by liquidity
- /api/v1/token/{mint} token + all its pairs (sortable), live price via DexScreener; schedules
                       background enrichment (rate-limited) when the token is stale
- /api/v1/new          latest new pools  (?after=<cursor>)
- /api/v1/live         Server-Sent Events: new pools as the poller ingests them
- /api/v1/stats        headline counts
Nothing here writes to the database except the background enrichment worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import struct
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PAIRS_DB", BASE / "pairs.db"))
CACHE_DIR = Path(os.environ.get("PAIRS_CACHE", BASE / "cache"))
CACHE_DB = CACHE_DIR / "public.db"
GALAXY_BIN = CACHE_DIR / "galaxy.bin"
REBUILD_S = 600
ENRICH_MAX_AGE_S = 6 * 3600
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
QUOTES = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "USD1",
}
STABLE = {"USDC", "USDT", "USD1"}
DEX_IDX = {
    "raydium-standard": 0, "raydium": 0, "raydium-concentrated": 1, "raydium-clmm": 1, "orca": 2,
    "meteora": 3, "meteora-damm-v2": 4, "meteora-dbc": 5, "pump-fun": 6, "pumpswap": 7,
    "raydium-launchlab": 8, "letsbonk-fun": 9,
}

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------- helpers
def ro() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


def cache_ro() -> sqlite3.Connection | None:
    if not CACHE_DB.exists():
        return None
    c = sqlite3.connect(f"file:{CACHE_DB}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def h01(s: str, salt: str = "") -> float:
    d = hashlib.blake2b((salt + s).encode(), digest_size=4).digest()
    return struct.unpack(">I", d)[0] / 4294967296.0


def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def client_ip(req: Request) -> str:
    return req.headers.get("cf-connecting-ip") or (req.client.host if req.client else "?")


class Bucket:
    """Per-key sliding-window limiter."""

    def __init__(self, n: int, window: float):
        self.n, self.w = n, window
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        t = time.time()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and q[0] < t - self.w:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(t)
            if len(self.hits) > 5000:
                for k in list(self.hits)[:1000]:
                    self.hits.pop(k, None)
            return True


class TTL:
    def __init__(self, ttl: float, size: int = 512):
        self.ttl, self.size = ttl, size
        self.d: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, k):
        with self.lock:
            v = self.d.get(k)
            if v and v[0] > time.time():
                return v[1]
            return None

    def put(self, k, v, ttl: float | None = None):
        with self.lock:
            self.d[k] = (time.time() + (ttl or self.ttl), v)
            self.d.move_to_end(k)
            while len(self.d) > self.size:
                self.d.popitem(last=False)


REQ_LIMIT = Bucket(240, 60)        # general per-IP
ENRICH_LIMIT = Bucket(6, 600)      # enrichment triggers per IP per 10 min
DS_GLOBAL = Bucket(50, 60)         # dexscreener live price calls (global)
token_cache = TTL(30)
price_cache = TTL(60, 2048)
search_cache = TTL(30, 2048)
count_cache = TTL(30, 4096)


def pairs_total(conn, mint: str) -> int:
    """Single source of truth for "how many pools does this token have" (search dropdown,
    token header, drawer): distinct pools in pool_tokens listing the mint in any role."""
    hit = count_cache.get(mint)
    if hit is not None:
        return hit
    n = conn.execute("SELECT COUNT(DISTINCT pool_address) FROM pool_tokens WHERE mint=?", (mint,)).fetchone()[0]
    count_cache.put(mint, n, 30 if n <= 6000 else 300)
    return n


def guard(req: Request) -> None:
    if not REQ_LIMIT.allow(client_ip(req)):
        raise HTTPException(429, "rate limited, slow down")


# ---------------------------------------------------------------- cache builder
_state: dict[str, Any] = {"built_at": None, "galaxy": b"", "galaxy_n": 0, "building": False, "pool_total": 0}
_build_lock = threading.Lock()


def build_cache() -> None:
    if not _build_lock.acquire(blocking=False):
        return
    _state["building"] = True
    t0 = time.time()
    tmp = CACHE_DIR / f"public.{os.getpid()}.tmp"
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        if tmp.exists():
            tmp.unlink()
        src = ro()
        out = sqlite3.connect(tmp)
        out.executescript(
            """
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE pool(address TEXT PRIMARY KEY, dex TEXT, created_at TEXT, liq REAL, vol REAL, price REAL,
                              txns INTEGER, base TEXT, quote TEXT);
            CREATE TABLE tok(mint TEXT PRIMARY KEY, symbol TEXT, sym_l TEXT, name TEXT, name_l TEXT,
                             pairs INTEGER, liq REAL, first_pool TEXT);
            """
        )
        latest = {}
        for r in src.execute(
            """SELECT s.pool_address, s.liquidity_usd, s.volume_h24, s.price_usd, s.txns_h24
               FROM snapshots s JOIN (SELECT MAX(id) id FROM snapshots GROUP BY pool_address) m ON m.id=s.id"""
        ):
            latest[r[0]] = (r[1], r[2], r[3], r[4])
        roles: dict[str, list] = {}
        for pool, mint, role in src.execute("SELECT pool_address, mint, role FROM pool_tokens WHERE role IN ('base','quote')"):
            e = roles.setdefault(pool, [None, None])
            i = 0 if role == "base" else 1
            if e[i] is None or mint < e[i]:
                e[i] = mint
        rows = []
        tokagg: dict[str, list] = {}
        for addr, dex, created, first_seen in src.execute("SELECT address, dex, created_at, first_seen_at FROM pools"):
            liq, vol, price, tx = latest.get(addr, (None, None, None, None))
            b, q = roles.get(addr, [None, None])
            rows.append((addr, dex, created or first_seen, liq, vol, price, tx, b, q))
            for m in {b, q}:
                if not m:
                    continue
                a = tokagg.setdefault(m, [0, 0.0, None])
                a[0] += 1
                if liq and 0 < liq < 5e9:
                    a[1] += liq
                c = created or first_seen
                if c and (a[2] is None or c < a[2]):
                    a[2] = c
        out.executemany("INSERT OR IGNORE INTO pool VALUES (?,?,?,?,?,?,?,?,?)", rows)
        for mint, n in src.execute("SELECT mint, COUNT(DISTINCT pool_address) FROM pool_tokens GROUP BY mint"):
            a = tokagg.get(mint)
            if a:
                a[0] = n
        trows = []
        for mint, sym, name in src.execute("SELECT mint, symbol, name FROM tokens"):
            a = tokagg.get(mint)
            if not a:
                continue
            trows.append((mint, sym, (sym or "").lower().lstrip("$").strip(), name, (name or "").lower(), a[0], a[1], a[2]))
        out.executemany("INSERT OR IGNORE INTO tok VALUES (?,?,?,?,?,?,?,?)", trows)
        out.executescript(
            """
            CREATE INDEX i_pool_base ON pool(base, liq DESC);
            CREATE INDEX i_pool_quote ON pool(quote, liq DESC);
            CREATE INDEX i_tok_sym ON tok(sym_l);
            CREATE INDEX i_tok_name ON tok(name_l);
            """
        )
        out.commit()
        out.close()
        src.close()
        os.replace(tmp, CACHE_DB)

        # galaxy buffer: every pool >= $20k first (always drawn), then a shuffled sample of
        # smaller pools so any prefix (LOD) is still a fair picture of the whole graph.
        big, small = [], []
        for addr, dex, created, liq, vol, price, tx, b, q in rows:
            if not liq or liq < 150 or liq > 5e8:
                continue
            qs = QUOTES.get(q or "") or QUOTES.get(b or "")
            tokm = b if (q in QUOTES or not b) else (q if b in QUOTES else b)
            if b in QUOTES and q and q not in QUOTES:
                tokm = q
            qt = 0 if qs == "SOL" else 1 if qs in STABLE else 2
            rec = (math.log10(liq), math.log10((vol or 0) + 1), DEX_IDX.get(dex or "", 10), qt,
                   h01(tokm or addr), h01(addr, "p"))
            (big if liq >= 20000 else small).append(rec)
        big.sort(key=lambda r: -r[0])
        rnd = random.Random(7)
        rnd.shuffle(small)
        allr = big + small[: max(0, 200000 - len(big))]
        buf = struct.pack(f"<{len(allr) * 6}f", *[v for r in allr for v in r])
        tmpb = CACHE_DIR / "galaxy.tmp"
        tmpb.write_bytes(buf)
        os.replace(tmpb, GALAXY_BIN)
        _state.update(galaxy=buf, galaxy_n=len(allr), galaxy_big=len(big), built_at=iso(now_utc()),
                      pool_total=len(rows), build_s=round(time.time() - t0, 1))
        search_cache.d.clear()
        print(f"[public] cache built: {len(rows)} pools, {len(trows)} tokens, galaxy {len(allr)} pts in {time.time()-t0:.1f}s", flush=True)
    except Exception as e:  # keep serving the previous cache
        print(f"[public] cache build failed: {e!r}", flush=True)
        try:
            tmp.unlink()
        except OSError:
            pass
    finally:
        _state["building"] = False
        _build_lock.release()


def _builder_loop() -> None:
    if GALAXY_BIN.exists():
        _state["galaxy"] = GALAXY_BIN.read_bytes()
        _state["galaxy_n"] = len(_state["galaxy"]) // 24
        _state["built_at"] = iso(datetime.fromtimestamp(GALAXY_BIN.stat().st_mtime, timezone.utc))
        age = time.time() - GALAXY_BIN.stat().st_mtime
        if age < REBUILD_S:
            time.sleep(REBUILD_S - age)
    while True:
        build_cache()
        time.sleep(REBUILD_S)


# ---------------------------------------------------------------- enrichment worker
_enrich_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enrich")
_enrich_state: dict[str, dict] = {}
_enrich_lock = threading.Lock()
MAX_QUEUE = 12


def _run_enrich(mint: str) -> None:
    with _enrich_lock:
        _enrich_state[mint]["state"] = "running"
        _enrich_state[mint]["started"] = time.time()
    try:
        from enrich import enrich_mint
        res = enrich_mint(mint)
        ok = sum(1 for r in res.get("results", []) if "error" not in r)
        st = "done" if ok else "failed"
    except Exception as e:
        print(f"[public] enrich {mint} failed: {e!r}", flush=True)
        st = "failed"
    with _enrich_lock:
        _enrich_state[mint].update(state=st, finished=time.time())
    with token_cache.lock:
        for k in [k for k in token_cache.d if k.startswith(mint + "|")]:
            token_cache.d.pop(k, None)
    count_cache.d.pop(mint, None)
    search_cache.d.clear()


def enrich_status(mint: str) -> dict | None:
    with _enrich_lock:
        s = _enrich_state.get(mint)
        if not s:
            return None
        if s["state"] in ("done", "failed") and time.time() - s.get("finished", 0) > 600:
            _enrich_state.pop(mint, None)
            return None
        return {"state": s["state"]}


def maybe_schedule_enrich(mint: str, last_enriched: str | None, ip: str) -> dict:
    if mint in QUOTES:  # SOL/USDC-scale: their pairs come from every other token's ingest
        return {"state": "fresh", "last": last_enriched}
    cur = enrich_status(mint)
    if cur and cur["state"] in ("queued", "running"):
        return cur
    if cur and cur["state"] in ("done", "failed"):
        return cur
    if last_enriched:
        try:
            t = datetime.fromisoformat(last_enriched.replace("Z", "+00:00"))
            if (now_utc() - t).total_seconds() < ENRICH_MAX_AGE_S:
                return {"state": "fresh", "last": last_enriched}
        except ValueError:
            pass
    with _enrich_lock:
        busy = sum(1 for s in _enrich_state.values() if s["state"] in ("queued", "running"))
    if busy >= MAX_QUEUE:
        return {"state": "busy", "last": last_enriched}
    if not ENRICH_LIMIT.allow(ip):
        return {"state": "limited", "last": last_enriched}
    with _enrich_lock:
        _enrich_state[mint] = {"state": "queued", "queued": time.time()}
    _enrich_pool.submit(_run_enrich, mint)
    return {"state": "queued", "last": last_enriched}


# ---------------------------------------------------------------- live price
_ds = requests.Session()
_ds.headers.update({"User-Agent": "pairing-db/1.0", "Accept": "application/json"})


def live_price(mint: str) -> dict | None:
    hit = price_cache.get(mint)
    if hit is not None:
        return hit or None
    if not DS_GLOBAL.allow("all"):
        return None
    failed = False
    try:
        r = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{mint}", timeout=6,
                         headers={"User-Agent": "pairing-db/1.0", "Accept": "application/json"})
        data = r.json() if r.ok else []
        failed = not r.ok
    except Exception:
        data, failed = [], True
    best = None
    for p in data if isinstance(data, list) else []:
        if (p.get("baseToken") or {}).get("address") != mint or not p.get("priceUsd"):
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if best is None or liq > best[0]:
            best = (liq, p)
    out = None
    if best:
        p = best[1]
        ch = (p.get("priceChange") or {}).get("h24")
        out = {"usd": float(p["priceUsd"]), "change_h24": float(ch) if ch is not None else None,
               "pair": p.get("pairAddress"), "dex": p.get("dexId"), "source": "dexscreener", "at": iso(now_utc())}
    price_cache.put(mint, out or {}, 60 if out else (15 if failed else 120))
    return out


# ---------------------------------------------------------------- token query
SORTS = {"liq": "liq DESC", "vol": "vol DESC", "age": "created_at DESC", "oldest": "created_at ASC"}


def _pairs_live(conn, mint: str, sort: str, limit: int):
    order = {"liq": "COALESCE(s.liquidity_usd,-1) DESC", "vol": "COALESCE(s.volume_h24,-1) DESC",
             "age": "COALESCE(p.created_at,p.first_seen_at) DESC", "oldest": "COALESCE(p.created_at,p.first_seen_at) ASC"}[sort]
    rows = conn.execute(
        f"""
        SELECT p.address, p.dex, p.name, COALESCE(p.created_at,p.first_seen_at) created_at,
               s.liquidity_usd liq, s.volume_h24 vol, s.price_usd price, s.txns_h24 txns, s.fetched_at snap_at, s.source snap_src,
               (SELECT MIN(mint) FROM pool_tokens WHERE pool_address=p.address AND role='base') base,
               (SELECT MIN(mint) FROM pool_tokens WHERE pool_address=p.address AND role='quote') quote,
               (SELECT MIN(mint) FROM pool_tokens WHERE pool_address=p.address AND role IN ('base','quote') AND mint<>?) oth
        FROM (SELECT DISTINCT pool_address FROM pool_tokens WHERE mint=?) x
        JOIN pools p ON p.address=x.pool_address
        LEFT JOIN snapshots s ON s.id=(SELECT id FROM snapshots WHERE pool_address=p.address ORDER BY fetched_at DESC, id DESC LIMIT 1)
        ORDER BY {order} LIMIT ?""",
        (mint, mint, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _pairs_cached(mint: str, sort: str, limit: int):
    c = cache_ro()
    if c is None:
        return [], 0
    try:
        order = SORTS[sort]
        rows = c.execute(
            f"""SELECT * FROM (SELECT address, dex, created_at, liq, vol, price, txns, base, quote FROM pool WHERE base=?
                UNION SELECT address, dex, created_at, liq, vol, price, txns, base, quote FROM pool WHERE quote=?)
                ORDER BY {order.replace('liq DESC','COALESCE(liq,-1) DESC').replace('vol DESC','COALESCE(vol,-1) DESC')} LIMIT ?""",
            (mint, mint, limit),
        ).fetchall()
        return [dict(r) for r in rows], 0
    finally:
        c.close()


def _symbols(conn, mints: set[str]) -> dict[str, tuple]:
    out = {}
    ms = [m for m in mints if m]
    for i in range(0, len(ms), 500):
        chunk = ms[i : i + 500]
        for r in conn.execute(f"SELECT mint, symbol, name FROM tokens WHERE mint IN ({','.join('?'*len(chunk))})", chunk):
            out[r[0]] = (r[1], r[2])
    return out


def token_payload(mint: str, sort: str, limit: int) -> dict:
    key = f"{mint}|{sort}|{limit}"
    hit = token_cache.get(key)
    if hit is not None:
        return hit
    conn = ro()
    try:
        t = conn.execute("SELECT mint, symbol, name, last_enriched_at FROM tokens WHERE mint=?", (mint,)).fetchone()
        total = pairs_total(conn, mint)
        if total <= 6000:
            pairs = _pairs_live(conn, mint, sort, limit)
            agg = conn.execute(
                """SELECT SUM(CASE WHEN s.liquidity_usd<5e9 THEN s.liquidity_usd END), SUM(s.volume_h24),
                          MIN(COALESCE(p.created_at,p.first_seen_at))
                   FROM (SELECT DISTINCT pool_address FROM pool_tokens WHERE mint=?) x JOIN pools p ON p.address=x.pool_address
                   LEFT JOIN snapshots s ON s.id=(SELECT id FROM snapshots WHERE pool_address=p.address ORDER BY fetched_at DESC, id DESC LIMIT 1)""",
                (mint,),
            ).fetchone()
            liq_total, vol_total, first_pool = agg[0], agg[1], agg[2]
        else:  # SOL / USDC scale: use the precomputed cache (<=10 min old)
            pairs, _ = _pairs_cached(mint, sort, limit)
            c = cache_ro()
            tr = c.execute("SELECT liq, first_pool FROM tok WHERE mint=?", (mint,)).fetchone() if c else None
            if c:
                c.close()
            liq_total, vol_total, first_pool = (tr[0] if tr else None), None, (tr[1] if tr else None)
        syms = _symbols(conn, {p.get("base") for p in pairs} | {p.get("quote") for p in pairs})
    finally:
        conn.close()
    stored_price = None
    for p in pairs:
        if p.get("base") == mint and (p.get("price") or 0) > 0 and (p.get("snap_src") or "").startswith(("dexscreener", "geckoterminal")):
            if stored_price is None or (p.get("liq") or 0) > stored_price[0]:
                stored_price = (p.get("liq") or 0, p)
    out_pairs = []
    for p in pairs:
        b, q = p.get("base"), p.get("quote")
        other = p.get("oth") or (q if b == mint else b)
        if other == mint:
            other = None
        # normalise: the looked-up token on one side, its counterpart on the other
        if b != mint and q != mint:
            pass
        elif b == q or (b == mint and q != other) or (q == mint and b != other):
            if b == mint or b == q:
                b, q = mint, other
            else:
                b, q = other, mint
        def tk(m):
            s = syms.get(m or "", (None, None))
            return {"mint": m, "sym": ("SOL" if m == "So11111111111111111111111111111111111111112" else s[0])}
        liq = p.get("liq")
        if liq is not None and liq > 5e9:
            liq = None  # obviously broken upstream value
        out_pairs.append({
            "pool": p["address"], "dex": p.get("dex"), "base": tk(b), "quote": tk(q), "other": tk(other),
            "liq": liq, "vol": p.get("vol"), "txns": p.get("txns"), "created_at": p.get("created_at"),
        })
    sym = t["symbol"] if t else None
    if mint == "So11111111111111111111111111111111111111112":
        sym = "SOL"
    tokm = mint
    payload = {
        "token": {"mint": mint, "symbol": sym, "name": t["name"] if t else None, "known": bool(t),
                  "pairs_total": total, "liq_total": liq_total, "vol_total": vol_total, "first_pool": first_pool,
                  "ht": f32(h01(tokm)), "last_enriched": t["last_enriched_at"] if t else None},
        "pairs": out_pairs, "sort": sort, "truncated": total > len(out_pairs), "time": iso(now_utc()),
        "stored_price": ({"usd": stored_price[1]["price"], "change_h24": None, "source": "snapshot",
                          "at": stored_price[1].get("snap_at")} if stored_price else None),
    }
    token_cache.put(key, payload, 300 if total > 6000 else None)
    return payload


# ---------------------------------------------------------------- routes
@router.get("/galaxy.bin")
def galaxy(request: Request, n: int = Query(200000, ge=1000, le=200000)):
    guard(request)
    buf = _state.get("galaxy") or b""
    if not buf:
        raise HTTPException(503, "galaxy is being built, retry shortly")
    n = min(n, len(buf) // 24)
    return Response(buf[: n * 24], media_type="application/octet-stream",
                    headers={"Cache-Control": "public, max-age=300", "X-Built-At": _state.get("built_at") or "",
                             "X-Points": str(n)})


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=1, max_length=64)):
    guard(request)
    q = q.strip()
    key = q.lower()
    hit = search_cache.get(key)
    if hit is not None:
        return hit
    res: list[dict] = []
    c = cache_ro()
    try:
        if MINT_RE.match(q):
            if c:
                res = [dict(r) for r in c.execute("SELECT mint, symbol, name, pairs, liq FROM tok WHERE mint=?", (q,))]
            if not res:
                conn = ro()
                r = conn.execute("SELECT mint, symbol, name FROM tokens WHERE mint=?", (q,)).fetchone()
                conn.close()
                res = [{"mint": q, "symbol": r["symbol"] if r else None, "name": r["name"] if r else None,
                        "pairs": None, "liq": None, "unmapped": True}]
        elif c:
            ql = key.lstrip("$").strip() or key
            hi = ql + "\uffff"
            seen = set()
            for sql, args in (
                ("SELECT mint, symbol, name, pairs, liq FROM tok WHERE sym_l=? ORDER BY liq DESC LIMIT 8", (ql,)),
                ("SELECT mint, symbol, name, pairs, liq FROM tok WHERE sym_l>? AND sym_l<? ORDER BY liq DESC LIMIT 8", (ql, hi)),
                ("SELECT mint, symbol, name, pairs, liq FROM tok WHERE name_l>=? AND name_l<? ORDER BY liq DESC LIMIT 6", (ql, hi)),
            ):
                for r in c.execute(sql, args):
                    if r["mint"] in seen:
                        continue
                    seen.add(r["mint"])
                    res.append(dict(r))
                if len(res) >= 8 and sql.startswith("SELECT mint, symbol, name, pairs, liq FROM tok WHERE sym_l>"):
                    break
            exact = [r for r in res if (r["symbol"] or "").lower().lstrip("$") == ql]
            rest = sorted([r for r in res if r not in exact], key=lambda r: -(r["liq"] or 0))
            res = sorted(exact, key=lambda r: -(r["liq"] or 0)) + rest
            res = res[:8]
            if len(q) >= 3 and len(q) < 32 and re.match(r"^[1-9A-HJ-NP-Za-km-z]+$", q) and not res:
                res = [dict(r) for r in c.execute("SELECT mint, symbol, name, pairs, liq FROM tok WHERE mint>=? AND mint<? ORDER BY liq DESC LIMIT 5", (q, q + "z"))]
    finally:
        if c:
            c.close()
    if res:
        conn = ro()
        try:
            for r in res:
                if not r.get("unmapped"):
                    r["pairs"] = pairs_total(conn, r["mint"])
        finally:
            conn.close()
    for r in res:
        if r["mint"] == "So11111111111111111111111111111111111111112":
            r["symbol"] = "SOL"
        if r.get("liq") is not None:
            r["liq"] = round(r["liq"], 2)
    syms = [(r["symbol"] or "").lower().lstrip("$") for r in res]
    for r in res:
        r["collision"] = syms.count((r["symbol"] or "").lower().lstrip("$")) > 1
    out = {"q": q, "results": res}
    search_cache.put(key, out)
    return out


@router.get("/token/{mint}")
def token(request: Request, mint: str, sort: str = "liq", limit: int = Query(500, ge=1, le=1000)):
    guard(request)
    if not MINT_RE.match(mint):
        raise HTTPException(400, "not a Solana mint address")
    if sort not in SORTS:
        sort = "liq"
    payload = dict(token_payload(mint, sort, limit))
    payload["enrich"] = maybe_schedule_enrich(mint, payload["token"].get("last_enriched"), client_ip(request))
    if payload["enrich"]["state"] in ("queued", "running"):
        token_cache.d.pop(f"{mint}|{sort}|{limit}", None)
    # live only: stored per-pool prices are not reliably the looked-up token's USD price, so no fallback
    payload["price"] = live_price(mint)
    payload.pop("stored_price", None)
    return payload


def _new_rows(conn, after: int, limit: int) -> list[dict]:
    cutoff = iso(now_utc() - timedelta(hours=3))
    rows = conn.execute(
        """SELECT p.rowid rid, p.address, p.dex, p.name, COALESCE(p.created_at,p.first_seen_at) created_at, p.first_seen_at,
                  s.liquidity_usd liq, s.volume_h24 vol,
                  (SELECT MIN(mint) FROM pool_tokens WHERE pool_address=p.address AND role='base') base,
                  (SELECT MIN(mint) FROM pool_tokens WHERE pool_address=p.address AND role='quote') quote
           FROM pools p
           LEFT JOIN snapshots s ON s.id=(SELECT id FROM snapshots WHERE pool_address=p.address ORDER BY id DESC LIMIT 1)
           WHERE p.rowid>? AND p.source IN ('geckoterminal_new_pools','jupiter_recent') AND COALESCE(p.dex,'unknown')<>'unknown'
             AND COALESCE(p.created_at,p.first_seen_at) >= ?
           ORDER BY p.rowid DESC LIMIT ?""",
        (after, cutoff, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        b, q = d["base"], d["quote"]
        tokm = q if (b in QUOTES and q and q not in QUOTES) else b
        qs = QUOTES.get(q or "") or QUOTES.get(b or "")
        d["qt"] = 0 if qs == "SOL" else 1 if qs in STABLE else 2
        d["ht"] = f32(h01(tokm or d["address"]))
        d["hp"] = f32(h01(d["address"], "p"))
        d["dexi"] = DEX_IDX.get(d["dex"] or "", 10)
        if d["liq"] is not None and d["liq"] > 5e9:
            d["liq"] = None
        out.append(d)
    return out


@router.get("/new")
def new(request: Request, after: int = 0, limit: int = Query(30, ge=1, le=200)):
    guard(request)
    conn = ro()
    try:
        rows = _new_rows(conn, after, limit)
    finally:
        conn.close()
    return {"pools": rows, "cursor": max([r["rid"] for r in rows], default=after), "time": iso(now_utc())}


_stats_cache = TTL(20)


@router.get("/stats")
def stats(request: Request):
    guard(request)
    hit = _stats_cache.get("s")
    if hit:
        return hit
    conn = ro()
    try:
        pools = conn.execute("SELECT MAX(rowid) FROM pools").fetchone()[0] or 0
        tokens = conn.execute("SELECT MAX(rowid) FROM tokens").fetchone()[0] or 0
        since = iso(now_utc() - timedelta(minutes=60))
        last_hour = conn.execute(
            "SELECT COUNT(*) FROM pools WHERE first_seen_at>=? AND source IN ('geckoterminal_new_pools','jupiter_recent')", (since,)
        ).fetchone()[0]
        last_poll = conn.execute("SELECT finished_at FROM poll_cycles WHERE source='geckoterminal_new_pools' AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    out = {"pools": pools, "tokens": tokens, "new_last_hour": last_hour, "new_per_min": round(last_hour / 60, 1),
           "galaxy_points": _state.get("galaxy_n"), "galaxy_built_at": _state.get("built_at"),
           "last_poll": last_poll[0] if last_poll else None, "time": iso(now_utc())}
    _stats_cache.put("s", out)
    return out


# ---------------------------------------------------------------- SSE live feed
class Hub:
    def __init__(self):
        self.subs: set[asyncio.Queue] = set()
        self.cursor = 0
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, rows):
        if not self.loop:
            return
        for q in list(self.subs):
            self.loop.call_soon_threadsafe(_put_nowait, q, rows)


def _put_nowait(q: asyncio.Queue, rows):
    try:
        q.put_nowait(rows)
    except asyncio.QueueFull:
        pass


hub = Hub()


def _live_loop():
    conn = None
    while True:
        try:
            if conn is None:
                conn = ro()
                hub.cursor = conn.execute("SELECT MAX(rowid) FROM pools").fetchone()[0] or 0
            mx = conn.execute("SELECT MAX(rowid) FROM pools").fetchone()[0] or 0
            if mx > hub.cursor:
                rows = _new_rows(conn, hub.cursor, 200)
                hub.cursor = mx
                if rows:
                    hub.publish(list(reversed(rows)))
        except Exception as e:
            print(f"[public] live loop: {e!r}", flush=True)
            try:
                conn and conn.close()
            except Exception:
                pass
            conn = None
        time.sleep(3)


MAX_SUBS = 300


@router.get("/live")
async def live(request: Request):
    guard(request)
    if len(hub.subs) >= MAX_SUBS:
        raise HTTPException(503, "too many live listeners")
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    hub.subs.add(q)

    async def gen():
        try:
            yield "retry: 5000\n\n"
            yield f"event: hello\ndata: {json.dumps({'cursor': hub.cursor})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    rows = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: pools\ndata: {json.dumps(rows, separators=(',', ':'))}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            hub.subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


def start_background() -> None:
    hub.loop = asyncio.get_event_loop()
    threading.Thread(target=_builder_loop, daemon=True, name="cache-builder").start()
    threading.Thread(target=_live_loop, daemon=True, name="live").start()
