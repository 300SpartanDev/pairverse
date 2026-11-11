#!/usr/bin/env python3
"""Token logos: resolve logo URLs from free sources and serve small cached webp thumbnails.

Sources, in order (batched where the API allows it):
  1. Jupiter token API v2 (100 mints / request)
  2. DexScreener tokens endpoint (30 mints / request; pair "info.imageUrl")
  3. On-chain metadata via Solana RPC (Token-2022 metadata extension or the Metaplex
     metadata account) -> metadata JSON "image". Uses the Helius RPC when HELIUS_API_KEY
     is set, otherwise the public mainnet RPC (or SOLANA_RPC_URL).

Thumbnails are fetched through safe_get(): http(s) only, public IPs only (no SSRF),
manual redirects, timeouts and a byte cap. IPFS links are rewritten to gateways that
still serve content (ipfs.io / dweb.link now answer 429 or "service worker only").

CLI:
  images.py backfill        run the resumable, rate-limited background job (systemd)
  images.py stats           coverage numbers
  images.py resolve <mint>  resolve one mint and build its thumbnail
"""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

BASE = Path(__file__).resolve().parent
_CACHE = Path(os.environ.get("PAIRS_CACHE", BASE / "cache"))
IMG_DIR = Path(os.environ.get("PAIRS_IMG_DIR", _CACHE.parent / "img"))
IMG_DB = IMG_DIR / "images.db"
THUMBS = IMG_DIR / "t"
CAP_BYTES = int(os.environ.get("PAIRS_IMG_CAP_MB", "4096")) * 1024 * 1024
MAIN_DB = Path(os.environ.get("PAIRS_DB", BASE / "pairs.db"))
SIZES = (32, 64, 128)
MAX_BYTES = 6 * 1024 * 1024
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
UA = "Mozilla/5.0 (compatible; pairverse-logos/1.0; +https://pairverse.fun)"
RETRY_NONE_S = 12 * 3600      # look again for tokens that had no logo anywhere
RETRY_BAD_S = 6 * 3600        # look again for logos whose image could not be fetched

IPFS_GATEWAYS = [
    "https://ipfs.filebase.io/ipfs/",
    "https://4everland.io/ipfs/",
    "https://w3s.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
]
DEAD_HOSTS = {"cloudflare-ipfs.com", "cf-ipfs.com", "ipfs.io", "dweb.link", "gateway.ipfs.io"}
CID_RE = re.compile(r"(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{50,})")

_session = threading.local()


def http() -> requests.Session:
    s = getattr(_session, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "*/*"})
        _session.s = s
    return s


# ------------------------------------------------------------------ storage
def db() -> sqlite3.Connection:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(IMG_DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("""CREATE TABLE IF NOT EXISTS img(
        mint TEXT PRIMARY KEY, url TEXT, src TEXT, state TEXT, tries INTEGER DEFAULT 0,
        checked_at INTEGER, thumb_at INTEGER, fail_at INTEGER)""")
    c.execute("CREATE INDEX IF NOT EXISTS i_img_state ON img(state, checked_at)")
    return c


def thumb_path(mint: str, size: int) -> Path:
    return THUMBS / mint[:2] / f"{mint}_{size}.webp"


def save_result(c, mint: str, url: str | None, src: str | None) -> None:
    now = int(time.time())
    if url:
        c.execute("""INSERT INTO img(mint,url,src,state,tries,checked_at) VALUES(?,?,?,'ok',0,?)
                     ON CONFLICT(mint) DO UPDATE SET url=excluded.url, src=excluded.src,
                       state=CASE WHEN img.url=excluded.url AND img.state='bad' THEN 'bad' ELSE 'ok' END,
                       checked_at=excluded.checked_at""", (mint, url, src, now))
    else:
        c.execute("""INSERT INTO img(mint,state,tries,checked_at) VALUES(?,'none',1,?)
                     ON CONFLICT(mint) DO UPDATE SET state=CASE WHEN img.url IS NULL THEN 'none' ELSE img.state END,
                       tries=img.tries+1, checked_at=excluded.checked_at""", (mint, now))


# ------------------------------------------------------------------ SSRF-safe fetch
def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def safe_get(url: str, max_bytes: int = MAX_BYTES, timeout: float = 8.0, deadline: float = 14.0) -> tuple[bytes, str] | None:
    """GET a public http(s) URL. Returns (body, content_type) or None."""
    t_end = time.time() + deadline
    for _ in range(4):  # original + up to 3 redirects, each re-validated
        try:
            p = urlsplit(url)
        except ValueError:
            return None
        if p.scheme not in ("http", "https") or not p.hostname or (p.port and p.port not in (80, 443, 8080, 8443)):
            return None
        if not _public_host(p.hostname):
            return None
        try:
            r = http().get(url, timeout=(4, timeout), stream=True, allow_redirects=False)
        except requests.RequestException:
            return None
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            r.close()
            continue
        if r.status_code != 200:
            r.close()
            return None
        if int(r.headers.get("content-length") or 0) > max_bytes:
            r.close()
            return None
        buf = bytearray()
        try:
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) > max_bytes or time.time() > t_end:
                    return None
        except requests.RequestException:
            return None
        finally:
            r.close()
        return bytes(buf), (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    return None


def candidates(url: str) -> list[str]:
    """Rewrite ipfs:// / dead IPFS gateways / ar:// to fetchable URLs, best first."""
    url = (url or "").strip()
    if not url:
        return []
    # unwrap "uploadbyurl?url=https://..." style wrappers
    m = re.search(r"[?&](?:url|u|src|image)=(https?%3A%2F%2F[^&]+|https?://[^&]+)", url)
    if m and ("lens.google" in url or "uploadbyurl" in url or "proxy" in url):
        from urllib.parse import unquote
        url = unquote(m.group(1))
    if url.startswith("ipfs://"):
        rest = url[7:].removeprefix("ipfs/")
        return [g + rest for g in IPFS_GATEWAYS]
    if url.startswith("ar://"):
        return ["https://arweave.net/" + url[5:]]
    if url.startswith("http://") or url.startswith("https://"):
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        m = CID_RE.search(host) or (CID_RE.search(p.path) if "/ipfs/" in p.path else None)
        if m:
            cid = m.group(1)
            path = p.path.split(cid, 1)[1] if cid in p.path else p.path
            rewritten = [g + cid + path for g in IPFS_GATEWAYS]
            out = rewritten if host in DEAD_HOSTS else [url] + rewritten
            if url.startswith("http://"):
                out = [u for u in out if u.startswith("https://")] or out
            return out[:5]
        return [url]
    if url.startswith("data:image/"):
        return [url]
    return []


def fetch_any(url: str, max_bytes: int = MAX_BYTES) -> tuple[bytes, str] | None:
    for u in candidates(url):
        if u.startswith("data:image/"):
            try:
                head, b64 = u.split(",", 1)
                return base64.b64decode(b64)[:max_bytes], head[5:].split(";")[0]
            except Exception:
                return None
        got = safe_get(u, max_bytes=max_bytes)
        if got:
            return got
    return None


# ------------------------------------------------------------------ thumbnails
def make_thumbs(mint: str, raw: bytes, ctype: str) -> bool:
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 40_000_000
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
            return False
        im = Image.open(io.BytesIO(raw))
        im.seek(0)
        im = im.convert("RGBA")
        im = ImageOps.fit(im, (128, 128), Image.LANCZOS)
    except Exception:
        return False
    d = THUMBS / mint[:2]
    d.mkdir(parents=True, exist_ok=True)
    for s in SIZES:
        out = im if s == 128 else im.resize((s, s), Image.LANCZOS)
        tmp = d / f".{mint}_{s}.{os.getpid()}.{threading.get_ident()}.tmp"
        out.save(tmp, "WEBP", quality=82, method=4)
        os.replace(tmp, thumb_path(mint, s))
    return True


def build_thumb(c, mint: str, url: str, alternates: bool = True) -> bool:
    got = fetch_any(url)
    ok = bool(got) and make_thumbs(mint, got[0], got[1])
    now = int(time.time())
    if not ok and alternates:
        # the listed logo host refuses us (e.g. bot-protected CDNs): try the other sources' URLs
        for fn, label in ((from_dexscreener, "dexscreener"), (from_chain, "metadata"), (from_jupiter, "jupiter")):
            alt = fn([mint]).get(mint)
            if alt and alt != url:
                got = fetch_any(alt)
                if got and make_thumbs(mint, got[0], got[1]):
                    c.execute("UPDATE img SET url=?, src=? WHERE mint=?", (alt, label, mint))
                    ok = True
                    break
    if ok:
        c.execute("UPDATE img SET thumb_at=?, state='ok' WHERE mint=?", (now, mint))
    else:
        c.execute("UPDATE img SET fail_at=?, state='bad', tries=tries+1 WHERE mint=?", (now, mint))
    c.commit()
    return ok


# ------------------------------------------------------------------ resolvers
class Pace:
    def __init__(self, gap: float):
        self.gap, self.t, self.lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            d = self.t + self.gap - time.time()
            if d > 0:
                time.sleep(d)
            self.t = time.time()

    def backoff(self, s: float):
        with self.lock:
            self.t = time.time() + s


# Jupiter lite allows ~5 requests / 10 s per IP: the backfill takes ~3 of them, on-demand lookups the rest
JUP_PACE = Pace(float(os.environ.get("PAIRS_JUP_GAP", "2.2")))
DS_PACE, RPC_PACE = Pace(1.0), Pace(0.6)


def _get_json(url: str, pace: Pace, timeout: float = 15):
    for i in range(3):
        pace.wait()
        try:
            r = http().get(url, timeout=timeout, headers={"Accept": "application/json"})
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429:
            pace.backoff(float(r.headers.get("retry-after") or 10))
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def from_jupiter(mints: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(mints), 100):
        d = _get_json("https://lite-api.jup.ag/tokens/v2/search?query=" + ",".join(mints[i:i + 100]), JUP_PACE)
        for t in d if isinstance(d, list) else []:
            if t.get("id") and t.get("icon"):
                out[t["id"]] = t["icon"]
    return out


def from_dexscreener(mints: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(mints), 30):
        d = _get_json("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints[i:i + 30]), DS_PACE)
        for p in d if isinstance(d, list) else []:
            m = (p.get("baseToken") or {}).get("address")
            im = (p.get("info") or {}).get("imageUrl")
            if m and im and m not in out:
                out[m] = im
    return out


# --- on-chain metadata (Token-2022 extension, Metaplex account)
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def _on_curve(b: bytes) -> bool:
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    y2 = y * y % _P
    x2 = (y2 - 1) * pow((_D * y2 + 1) % _P, _P - 2, _P) % _P
    if x2 == 0:
        return True
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return (x * x - x2) % _P == 0


METAPLEX = b58decode("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def metaplex_pda(mint: str) -> str:
    seeds = b"metadata" + METAPLEX + b58decode(mint)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(seeds + bytes([bump]) + METAPLEX + b"ProgramDerivedAddress").digest()
        if not _on_curve(h):
            return b58encode(h)
    raise ValueError("no PDA")


def _borsh_str(b: bytes, i: int) -> tuple[str, int]:
    n = struct.unpack_from("<I", b, i)[0]
    i += 4
    return b[i:i + n].decode("utf-8", "ignore").rstrip("\0").strip(), i + n


def _uri_metaplex(raw: bytes) -> str | None:
    try:
        i = 65
        _, i = _borsh_str(raw, i)
        _, i = _borsh_str(raw, i)
        uri, _ = _borsh_str(raw, i)
        return uri or None
    except Exception:
        return None


def _uri_token2022(raw: bytes) -> str | None:
    if len(raw) <= 166 or raw[165] != 1:
        return None
    i = 166
    while i + 4 <= len(raw):
        t, ln = struct.unpack_from("<HH", raw, i)
        i += 4
        if t == 19:
            try:
                j = i + 64
                _, j = _borsh_str(raw, j)
                _, j = _borsh_str(raw, j)
                uri, _ = _borsh_str(raw, j)
                return uri or None
            except Exception:
                return None
        if t == 0 and ln == 0:
            break
        i += ln
    return None


def rpc_url() -> str:
    try:
        import helius
        u = helius.rpc_url()
        if u:
            return u
    except Exception:
        pass
    return os.environ.get("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com"


def _accounts(keys: list[str]) -> list:
    RPC_PACE.wait()
    try:
        r = http().post(rpc_url(), json={"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                                         "params": [keys, {"encoding": "base64"}]}, timeout=20)
        if r.status_code == 429:
            RPC_PACE.backoff(10)
            return [None] * len(keys)
        return (r.json().get("result") or {}).get("value") or [None] * len(keys)
    except Exception:  # never log the RPC URL (may carry a key)
        return [None] * len(keys)


def _image_from_uri(uri: str) -> str | None:
    got = fetch_any(uri, max_bytes=1024 * 1024)
    if not got:
        return None
    body, ctype = got
    if ctype.startswith("image/"):
        return uri
    try:
        j = json.loads(body.decode("utf-8", "ignore"))
    except ValueError:
        return None
    if not isinstance(j, dict):
        return None
    im = j.get("image") or j.get("image_uri") or j.get("logo") or j.get("icon")
    if isinstance(im, str) and im.strip():
        return im.strip()
    files = (j.get("properties") or {}).get("files") if isinstance(j.get("properties"), dict) else None
    for f in files if isinstance(files, list) else []:
        if isinstance(f, dict) and str(f.get("type", "")).startswith("image") and f.get("uri"):
            return f["uri"]
    return None


def from_chain(mints: list[str], pool: ThreadPoolExecutor | None = None) -> dict[str, str]:
    uris: dict[str, str] = {}
    for i in range(0, len(mints), 100):
        chunk = mints[i:i + 100]
        need_mp = []
        for m, a in zip(chunk, _accounts(chunk)):
            if not a:
                continue
            if a.get("owner") == TOKEN_2022:
                u = _uri_token2022(base64.b64decode(a["data"][0]))
                if u:
                    uris[m] = u
                    continue
            need_mp.append(m)
        if need_mp:
            pdas = {}
            for m in need_mp:
                try:
                    pdas[metaplex_pda(m)] = m
                except ValueError:
                    pass
            keys = list(pdas)
            for k, a in zip(keys, _accounts(keys)):
                if a:
                    u = _uri_metaplex(base64.b64decode(a["data"][0]))
                    if u:
                        uris[pdas[k]] = u
    out: dict[str, str] = {}
    items = list(uris.items())
    if pool and len(items) > 1:
        for (m, _), im in zip(items, pool.map(lambda kv: _image_from_uri(kv[1]), items)):
            if im:
                out[m] = im
    else:
        for m, u in items:
            im = _image_from_uri(u)
            if im:
                out[m] = im
    return out


def resolve_batch(mints: list[str], pool: ThreadPoolExecutor | None = None, chain: bool = True,
                  ds_first: bool = False) -> dict[str, tuple[str, str]]:
    order = [(from_dexscreener, "dexscreener"), (from_jupiter, "jupiter")]
    if not ds_first:
        order.reverse()
    found: dict[str, tuple[str, str]] = {}
    rest = list(mints)
    for fn, label in order:
        if not rest:
            break
        found.update({m: (u, label) for m, u in fn(rest).items()})
        rest = [m for m in rest if m not in found]
    if rest and chain:
        found.update({m: (u, "metadata") for m, u in from_chain(rest, pool).items()})
    return found


# ------------------------------------------------------------------ on-demand (API)
class _Batcher:
    """Collects mints requested by concurrent API calls and resolves them together
    (one DexScreener / Jupiter request for up to 30 / 100 mints) instead of one call each."""

    def __init__(self):
        self.cv = threading.Condition()
        self.pending: dict[str, threading.Event] = {}
        self.results: dict[str, tuple[str | None, str | None]] = {}
        self.pool = ThreadPoolExecutor(4)
        threading.Thread(target=self._run, daemon=True, name="logo-batcher").start()

    def resolve(self, mint: str, timeout: float = 20) -> tuple[str | None, str | None] | None:
        with self.cv:
            ev = self.pending.get(mint)
            if ev is None:
                ev = self.pending[mint] = threading.Event()
                self.cv.notify()
        if not ev.wait(timeout):
            return None
        return self.results.get(mint)

    def _run(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
            time.sleep(0.25)  # let a burst (a search dropdown, a feed page) accumulate
            with self.cv:
                batch = dict(list(self.pending.items())[:100])
            mints = list(batch)
            try:
                got = resolve_batch(mints, self.pool, ds_first=True)
            except Exception:
                got = {}
            c = db()
            try:
                for m in mints:
                    u, src = got.get(m, (None, None))
                    save_result(c, m, u, src)
                    self.results[m] = (u, src)
                c.commit()
            finally:
                c.close()
            with self.cv:
                for m, ev in batch.items():
                    self.pending.pop(m, None)
                    ev.set()
                if len(self.results) > 20000:
                    self.results.clear()


_batcher: _Batcher | None = None
_batcher_lock = threading.Lock()
_fetch_slots = threading.BoundedSemaphore(12)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(mint: str) -> threading.Lock:
    with _locks_guard:
        if len(_locks) > 5000:
            _locks.clear()
        return _locks.setdefault(mint, threading.Lock())


class Busy(Exception):
    """Transient: try again later (do not cache a negative answer)."""


def thumb_for(mint: str, size: int) -> Path | None:
    """Thumbnail path for mint, resolving and fetching on first request.
    None = no logo exists (cacheable). Raises Busy when the answer is not known yet."""
    global _batcher
    p = thumb_path(mint, size)
    if p.exists():
        return p
    c = db()
    try:
        row = c.execute("SELECT url, state, checked_at, fail_at FROM img WHERE mint=?", (mint,)).fetchone()
    finally:
        c.close()
    now = time.time()
    if row and row["state"] == "none" and now - (row["checked_at"] or 0) < RETRY_NONE_S:
        return None
    if row and row["state"] == "bad" and now - (row["fail_at"] or 0) < RETRY_BAD_S:
        return None
    url = row["url"] if row and row["url"] else None
    if not url:
        with _batcher_lock:
            if _batcher is None:
                _batcher = _Batcher()
        got = _batcher.resolve(mint)
        if got is None:
            raise Busy()
        url = got[0]
        if not url:
            return None
    if not _fetch_slots.acquire(timeout=10):
        raise Busy()
    try:
        with _lock(mint):
            if p.exists():
                return p
            c = db()
            try:
                if build_thumb(c, mint, url):
                    return p
            finally:
                c.close()
    finally:
        _fetch_slots.release()
    return None


# ------------------------------------------------------------------ backfill job
def _main_ro():
    c = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _missing(c_img, mints: list[str]) -> list[str]:
    if not mints:
        return []
    have = set()
    now = int(time.time())
    for i in range(0, len(mints), 500):
        ch = mints[i:i + 500]
        for r in c_img.execute(f"SELECT mint, state, checked_at FROM img WHERE mint IN ({','.join('?' * len(ch))})", ch):
            if r["state"] != "none" or now - (r["checked_at"] or 0) < RETRY_NONE_S:
                have.add(r["mint"])
    return [m for m in mints if m not in have and MINT_RE.match(m)]


def _by_liquidity(offset: int, n: int) -> list[str]:
    p = _CACHE / "public.db"
    if not p.exists():
        return []
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    try:
        return [r[0] for r in c.execute("SELECT mint FROM tok ORDER BY liq DESC LIMIT ? OFFSET ?", (n, offset))]
    finally:
        c.close()


def _newest(n: int) -> list[str]:
    c = _main_ro()
    try:
        return [r[0] for r in c.execute("SELECT mint FROM tokens ORDER BY rowid DESC LIMIT ?", (n,))]
    finally:
        c.close()


def _trim_cache() -> None:
    files = []
    total = 0
    for p in THUMBS.rglob("*.webp"):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
        total += st.st_size
    if total <= CAP_BYTES:
        return
    files.sort()
    for _, sz, p in files:
        if total <= CAP_BYTES * 0.9:
            break
        try:
            p.unlink()
            total -= sz
        except OSError:
            pass


def backfill(prewarm: int = 20000) -> None:
    pool = ThreadPoolExecutor(6)
    c = db()
    offset_file = IMG_DIR / "backfill.offset"
    offset = int(offset_file.read_text()) if offset_file.exists() else 0
    last_trim = 0.0
    print(f"[logos] backfill start offset={offset}", flush=True)
    while True:
        t0 = time.time()
        # 1. freshest tokens first, so the live feed has logos
        fresh = _missing(c, _newest(300))
        for i in range(0, len(fresh), 100):
            got = resolve_batch(fresh[i:i + 100], pool)
            for m in fresh[i:i + 100]:
                u, s = got.get(m, (None, None))
                save_result(c, m, u, s)
            c.commit()
        # 2. backlog, most liquid first (resumable offset)
        chunk = _by_liquidity(offset, 400)
        if not chunk:
            offset = 0
        else:
            miss = _missing(c, chunk)
            for i in range(0, len(miss), 100):
                got = resolve_batch(miss[i:i + 100], pool, chain=True)
                for m in miss[i:i + 100]:
                    u, s = got.get(m, (None, None))
                    save_result(c, m, u, s)
                c.commit()
            offset += len(chunk)
            offset_file.write_text(str(offset))
        # 3. pre-build thumbnails for the most important tokens and the newest ones
        top = _by_liquidity(0, prewarm) if prewarm else []
        want = fresh + [m for m in _newest(300) if m not in fresh] + top
        todo = []
        for i in range(0, len(want), 500):
            ch = want[i:i + 500]
            for r in c.execute(f"SELECT mint, url FROM img WHERE state='ok' AND thumb_at IS NULL AND mint IN ({','.join('?' * len(ch))})", ch):
                todo.append((r["mint"], r["url"]))
            if len(todo) >= 120:
                break
        if todo:
            def work(mu):
                cc = db()
                try:
                    return build_thumb(cc, mu[0], mu[1])
                finally:
                    cc.close()
            ok = sum(1 for x in pool.map(work, todo[:120]) if x)
        else:
            ok = 0
        if time.time() - last_trim > 900:
            _trim_cache()
            last_trim = time.time()
        print(f"[logos] cycle fresh={len(fresh)} offset={offset} thumbs+={ok} in {time.time() - t0:.0f}s", flush=True)
        time.sleep(5 if chunk else 60)


def stats() -> dict:
    c = db()
    m = _main_ro()
    tokens = m.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    s = {r[0]: r[1] for r in c.execute("SELECT state, COUNT(*) FROM img GROUP BY state")}
    thumbs = c.execute("SELECT COUNT(*) FROM img WHERE thumb_at IS NOT NULL").fetchone()[0]
    src = {r[0]: r[1] for r in c.execute("SELECT src, COUNT(*) FROM img WHERE url IS NOT NULL GROUP BY src")}
    return {"tokens": tokens, "checked": sum(s.values()), "states": s, "with_url": sum(src.values()),
            "with_url_pct_of_tokens": round(100 * sum(src.values()) / max(1, tokens), 1),
            "with_url_pct_of_checked": round(100 * sum(src.values()) / max(1, sum(s.values())), 1),
            "thumbs": thumbs, "by_source": src}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "backfill":
        backfill()
    elif cmd == "resolve" and len(sys.argv) > 2:
        p = thumb_for(sys.argv[2], 64)
        print(p or "no logo")
    else:
        print(json.dumps(stats(), indent=1))
