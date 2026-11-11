#!/usr/bin/env python3
"""CLI lookup for the pairing database."""

from __future__ import annotations

import argparse
import json
import sys

from db import connect, counts, new_today, pairs_for_mint, poll_cycle_report, search_tokens, today_start_utc
from enrich import enrich_mint


def _print_pairs(pairs: list[dict]) -> None:
    print(f"{'DEX':16} {'PAIR':22} {'LIQ_USD':>14} {'VOL24':>14}  POOL")
    for p in pairs:
        liq = p.get("liquidity_usd")
        vol = p.get("volume_h24")
        liq_s = f"{liq:,.0f}" if isinstance(liq, (int, float)) else "—"
        vol_s = f"{vol:,.0f}" if isinstance(vol, (int, float)) else "—"
        print(f"{(p.get('dex') or '?'):16} {(p.get('name') or ''):22.22} {liq_s:>14} {vol_s:>14}  {p.get('address')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Look up Solana token pairs in pairs.db")
    ap.add_argument("query", nargs="?", help="mint or symbol")
    ap.add_argument("--new-today", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    conn = connect()
    if args.stats:
        data = {"counts": counts(conn), "polls": poll_cycle_report(conn), "today_start_utc": today_start_utc()}
        print(json.dumps(data, indent=2, default=str))
        return 0
    if args.new_today:
        rows = new_today(conn)
        if args.as_json:
            print(json.dumps({"cutoff_utc": today_start_utc(), "count": len(rows), "pools": rows}, indent=2, default=str))
        else:
            print(f"New today since {today_start_utc()} UTC: {len(rows)}")
            _print_pairs(rows)
        return 0
    if not args.query:
        ap.print_help()
        return 2
    tokens = search_tokens(conn, args.query)
    conn.close()
    if not tokens and not args.no_enrich and len(args.query) >= 32:
        enrich_mint(args.query)
        conn = connect()
        tokens = search_tokens(conn, args.query)
        conn.close()
    if not tokens:
        print(f"No tokens matched {args.query}", file=sys.stderr)
        return 1
    if len(tokens) > 1 and not any(t["mint"] == args.query for t in tokens):
        print(f"{len(tokens)} tokens matched; specify a mint:")
        for t in tokens:
            print(f"  {t.get('symbol'):10} {t['mint']}  {t.get('name')}")
        return 0
    mint = args.query if any(t["mint"] == args.query for t in tokens) else tokens[0]["mint"]
    if not args.no_enrich:
        enrich_mint(mint)
    conn = connect()
    tokens = search_tokens(conn, mint)
    token = next((t for t in tokens if t["mint"] == mint), tokens[0])
    pairs = pairs_for_mint(conn, mint)
    conn.close()
    if args.as_json:
        print(json.dumps({"token": token, "count": len(pairs), "pairs": pairs}, indent=2, default=str))
        return 0
    print(f"{token.get('symbol')} ({token.get('name')})  {token['mint']}")
    print(f"{len(pairs)} pairs, sorted by liquidity")
    _print_pairs(pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
