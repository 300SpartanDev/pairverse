#!/usr/bin/env python3
"""Optional Helius integration.

Set HELIUS_API_KEY (free tier or paid plan, https://www.helius.dev) to enable:
  * DAS getAsset token metadata (symbol, name, decimals, supply, image) during enrichment
  * a Helius RPC URL for anything that needs a Solana RPC (rpc_url())

With no key set every function is a silent no-op, so the free pipeline keeps working.
The key is only ever sent to Helius and is never logged.
"""

from __future__ import annotations

import os
from typing import Any

import requests

HELIUS_RPC = "https://mainnet.helius-rpc.com/"


def api_key() -> str | None:
    k = (os.environ.get("HELIUS_API_KEY") or "").strip()
    return k or None


def enabled() -> bool:
    return api_key() is not None


def rpc_url() -> str | None:
    """Helius mainnet RPC endpoint (includes the key), or None when not configured."""
    k = api_key()
    return f"{HELIUS_RPC}?api-key={k}" if k else None


def _rpc(method: str, params: Any, timeout: int = 15) -> Any:
    url = rpc_url()
    if not url:
        return None
    try:
        r = requests.post(url, json={"jsonrpc": "2.0", "id": "pairverse", "method": method, "params": params},
                          timeout=timeout, headers={"User-Agent": "pairverse/1.0"})
    except requests.RequestException as e:  # never echo the URL: it carries the key
        raise RuntimeError(f"helius {method}: {type(e).__name__}") from None
    if r.status_code != 200:
        raise RuntimeError(f"helius {method}: HTTP {r.status_code}")
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"helius {method}: {str(body['error'].get('message', 'error'))[:120]}")
    return body.get("result")


def get_asset(mint: str) -> dict | None:
    """DAS getAsset, flattened to the fields Pairverse stores. None when disabled or unknown."""
    if not enabled():
        return None
    res = _rpc("getAsset", {"id": mint, "displayOptions": {"showFungible": True}})
    if not res:
        return None
    meta = (res.get("content") or {}).get("metadata") or {}
    ti = res.get("token_info") or {}
    links = (res.get("content") or {}).get("links") or {}
    return {
        "mint": mint,
        "symbol": ti.get("symbol") or meta.get("symbol") or None,
        "name": meta.get("name") or None,
        "decimals": ti.get("decimals"),
        "supply": ti.get("supply"),
        "token_program": ti.get("token_program"),
        "image": links.get("image"),
        "interface": res.get("interface"),
    }


def enrich_helius(s, conn, mint: str) -> dict:
    """Enrichment step used by enrich.enrich_mint(); fills in token metadata from DAS."""
    from db import upsert_token

    if not enabled():
        return {"source": "helius_das", "skipped": "HELIUS_API_KEY not set"}
    a = get_asset(mint)
    if not a:
        return {"source": "helius_das", "found": False}
    dec = a.get("decimals")
    upsert_token(conn, mint, a.get("symbol"), a.get("name"), int(dec) if isinstance(dec, int) else None, "helius_das")
    return {"source": "helius_das", "found": True, "symbol": a.get("symbol")}


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: helius.py <mint>   (needs HELIUS_API_KEY)", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(get_asset(sys.argv[1]) if enabled() else {"enabled": False}, indent=2))
