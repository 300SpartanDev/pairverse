#!/usr/bin/env python3
"""HTTP helpers and a process-safe GeckoTerminal rate limiter (~10 req/min)."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from db import connect, utc_now

UA = "pairing-db/1.0 (solana desk; local pipeline)"
GT_MAX_PER_MIN = 8  # stay under the documented ~10/min
GT_WINDOW_S = 60.0


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


def wait_gt_slot(timeout_s: float = 180.0) -> None:
    """Block until a GeckoTerminal request slot is available."""
    deadline = time.time() + timeout_s
    while True:
        conn = connect()
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(seconds=GT_WINDOW_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "DELETE FROM api_calls WHERE provider='geckoterminal' AND called_at < ?",
                (cutoff,),
            )
            rows = list(conn.execute(
                "SELECT called_at FROM api_calls WHERE provider='geckoterminal' AND called_at >= ? ORDER BY called_at",
                (cutoff,),
            ))
            n = len(rows)
            last = rows[-1][0] if rows else None
            wait_needed = 0.0
            if last:
                try:
                    tlast = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    gap = (now - tlast).total_seconds()
                    if gap < 7.0:
                        wait_needed = max(wait_needed, 7.0 - gap)
                except ValueError:
                    pass
            if n >= GT_MAX_PER_MIN and rows:
                try:
                    t0 = datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))
                    wait_needed = max(
                        wait_needed,
                        (t0 + timedelta(seconds=GT_WINDOW_S) - now).total_seconds() + 0.4,
                    )
                except ValueError:
                    wait_needed = max(wait_needed, 5.0)
            if wait_needed <= 0:
                conn.execute(
                    "INSERT INTO api_calls(provider, called_at) VALUES ('geckoterminal', ?)",
                    (utc_now(),),
                )
                conn.commit()
                return
            conn.commit()
        finally:
            conn.close()
        if time.time() >= deadline:
            raise TimeoutError("timed out waiting for GeckoTerminal rate-limit slot")
        time.sleep(min(max(wait_needed, 0.4), 15.0))


def get_json(
    s: requests.Session,
    url: str,
    *,
    provider: str | None = None,
    retries: int = 5,
    timeout: int = 45,
) -> Any:
    last: Exception | None = None
    for i in range(retries):
        try:
            if provider == "geckoterminal":
                wait_gt_slot()
            r = s.get(url, timeout=timeout)
            if r.status_code == 401:
                raise RuntimeError(f"401 {url} body={r.text[:300]}")
            if r.status_code == 429:
                wait = 10 * (i + 1)
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                print(f"  429 on {url} — backoff {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                last = RuntimeError(f"429 {url}")
                continue
            if r.status_code >= 500:
                wait = 5 * (i + 1)
                print(f"  {r.status_code} on {url} — retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                last = RuntimeError(f"{r.status_code} {url}")
                continue
            r.raise_for_status()
            return r.json()
        except RuntimeError:
            raise
        except Exception as e:
            last = e
            print(f"  request error {e} — retry {i+1}", file=sys.stderr)
            time.sleep(4 * (i + 1))
    raise last  # type: ignore[misc]
