#!/usr/bin/env python3
"""FastAPI lookup service + simple web UI."""

from __future__ import annotations

import threading
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import public

from db import (
    connect,
    counts,
    new_today,
    new_today_count,
    pairs_for_mint,
    poll_cycle_report,
    recent_polls_summary,
    search_tokens,
    today_start_utc,
    utc_now,
)
from enrich import enrich_mint

app = FastAPI(title="Solana pairing database", version="2.0", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(public.router)
SITE = Path(__file__).resolve().parent / "site"


@app.on_event("startup")
async def _startup() -> None:
    public.start_background()


@app.middleware("http")
async def _harden(request: Request, call_next):
    # read-only service: only GET/HEAD, no write verbs anywhere
    if request.method not in ("GET", "HEAD"):
        return PlainTextResponse("read-only", status_code=405)
    try:
        resp = await call_next(request)
    except Exception:
        return PlainTextResponse("internal error", status_code=500)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return resp


def _enrich_allowed(request: Request | None) -> bool:
    if request is None:
        return True
    return public.ENRICH_LIMIT.allow(public.client_ip(request))
_enrich_locks: dict[str, threading.Lock] = {}
_enrich_guard = threading.Lock()


def _lock_for(mint: str) -> threading.Lock:
    with _enrich_guard:
        lock = _enrich_locks.get(mint)
        if lock is None:
            lock = threading.Lock()
            _enrich_locks[mint] = lock
        return lock


def maybe_enrich(mint: str, force: bool = False, max_age_s: int = 900) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT last_enriched_at FROM tokens WHERE mint=?", (mint,)).fetchone()
        last = row["last_enriched_at"] if row else None
        if last and not force:
            # skip if enriched recently
            try:
                from datetime import datetime, timezone

                t = datetime.fromisoformat(last.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - t).total_seconds()
                if age < max_age_s:
                    return None
            except ValueError:
                pass
    finally:
        conn.close()
    lock = _lock_for(mint)
    if not lock.acquire(blocking=False):
        return {"status": "enrichment already running"}
    try:
        return enrich_mint(mint)
    finally:
        lock.release()


@app.get("/health")
def health() -> dict:
    conn = connect()
    try:
        last = recent_polls_summary(conn).get("last") or {}
        return {"ok": True, "time": utc_now(), "last_poll": last.get("finished_at")}
    finally:
        conn.close()


@app.get("/api/stats")
def api_stats() -> dict:
    conn = connect()
    try:
        return {
            "time": utc_now(),
            "counts": counts(conn),
            "today_start_utc": today_start_utc(),
        }
    finally:
        conn.close()


@app.get("/api/search")
def api_search(request: Request, q: str = Query(..., min_length=1, max_length=64), enrich: bool = False) -> dict:
    public.guard(request)
    conn = connect()
    try:
        tokens = search_tokens(conn, q)
    finally:
        conn.close()
    enrichment = None
    if enrich and len(tokens) == 1 and _enrich_allowed(request):
        enrichment = maybe_enrich(tokens[0]["mint"])
        conn = connect()
        try:
            tokens = search_tokens(conn, q)
        finally:
            conn.close()
    return {"q": q, "tokens": tokens, "enrichment": enrichment}


@app.get("/api/token/{mint}")
def api_token(request: Request, mint: str, enrich: bool = True) -> dict:
    public.guard(request)
    if not public.MINT_RE.match(mint):
        raise HTTPException(400, "not a Solana mint address")
    enrichment = None
    if enrich and _enrich_allowed(request):
        try:
            enrichment = maybe_enrich(mint)
        except Exception:
            enrichment = {"error": "enrichment failed"}
    conn = connect()
    try:
        tokens = search_tokens(conn, mint, limit=5)
        token = next((t for t in tokens if t["mint"] == mint), tokens[0] if tokens else None)
        pairs = pairs_for_mint(conn, mint)
    finally:
        conn.close()
    return {"token": token, "pairs": pairs, "count": len(pairs), "enrichment": enrichment, "time": utc_now()}


@app.get("/api/new")
def api_new(request: Request, limit: int = Query(500, ge=1, le=1000)) -> dict:
    public.guard(request)
    conn = connect()
    try:
        rows = new_today(conn, limit=limit)
        return {"cutoff_utc": today_start_utc(), "count": new_today_count(conn), "returned": len(rows), "pools": rows, "time": utc_now()}
    finally:
        conn.close()


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Solana pairing database</title>
<style>
  :root { --bg:#0e1116; --card:#171b22; --fg:#e8edf4; --muted:#8b98a8; --acc:#6ee7b7; --line:#2a3140; --warn:#fbbf24; }
  * { box-sizing: border-box; }
  body { margin:0; font: 14px/1.45 ui-sans-serif, system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:20px 24px 12px; border-bottom:1px solid var(--line); display:flex; gap:16px; align-items:flex-end; flex-wrap:wrap; }
  h1 { font-size:18px; margin:0 0 4px; font-weight:600; }
  .sub { color:var(--muted); font-size:12px; }
  nav { margin-left:auto; display:flex; gap:8px; }
  nav a, button, .btn { color:var(--bg); background:var(--acc); border:0; border-radius:6px; padding:8px 12px; text-decoration:none; font-weight:600; cursor:pointer; font-size:13px; }
  nav a.secondary, button.secondary { background:var(--card); color:var(--fg); border:1px solid var(--line); }
  main { padding:20px 24px 48px; max-width:1280px; }
  form { display:flex; gap:8px; margin-bottom:16px; }
  input[type=text] { flex:1; background:var(--card); border:1px solid var(--line); color:var(--fg); border-radius:6px; padding:10px 12px; font-size:14px; }
  table { width:100%; border-collapse:collapse; background:var(--card); border-radius:8px; overflow:hidden; }
  th, td { padding:8px 10px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; font-variant-numeric: tabular-nums; }
  th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; font-weight:600; }
  tr:hover td { background:#1d232c; }
  .mint, .addr { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; word-break:break-all; }
  .muted { color:var(--muted); }
  .stats { display:flex; gap:16px; flex-wrap:wrap; margin:0 0 16px; color:var(--muted); font-size:12px; }
  .stats b { color:var(--fg); }
  .empty { padding:24px; color:var(--muted); }
  .token-list a { color:var(--acc); text-decoration:none; }
  .warn { color:var(--warn); }
</style>
</head>
<body>
<header>
  <div>
    <h1>Solana pairing database</h1>
    <div class="sub">Free pipeline on this box — GeckoTerminal + DexScreener + Raydium + Orca seed</div>
  </div>
  <nav>
    <a href="/" class="secondary">Universe</a>
    <a href="/classic" class="secondary">Search</a>
    <a href="/new" class="secondary">New today</a>
    <a href="/api/stats" class="secondary">Stats JSON</a>
  </nav>
</header>
<main>
  <div class="stats" id="stats"></div>
  %CONTENT%
</main>
<script>
async function loadStats(){
  try {
    const s = await fetch('/api/stats').then(r=>r.json());
    const c = s.counts || {};
    document.getElementById('stats').innerHTML =
      `<span>Tokens <b>${c.tokens??'?'}</b></span>
       <span>Pools <b>${c.pools??'?'}</b></span>
       <span>Snapshots <b>${c.snapshots??'?'}</b></span>
       <span>Poll cycles <b>${c.poll_cycles??'?'}</b></span>
       <span class="muted">Today starts ${s.today_start_utc} UTC (Europe/Madrid midnight)</span>`;
  } catch(e) {}
}
loadStats();
</script>
</body>
</html>
"""


def page(content: str) -> HTMLResponse:
    return HTMLResponse(HTML.replace("%CONTENT%", content))


def _esc(v: Any) -> str:
    return (
        str(v if v is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_usd(v: Any) -> str:
    try:
        if v is None or v == "":
            return "—"
        n = float(v)
        if n >= 1_000_000:
            return f"${n/1_000_000:,.2f}M"
        if n >= 1_000:
            return f"${n:,.0f}"
        if n >= 1:
            return f"${n:,.2f}"
        return f"${n:,.6f}"
    except (TypeError, ValueError):
        return "—"


def _pairs_table(pairs: list[dict]) -> str:
    if not pairs:
        return '<div class="empty">No pairs stored for this token yet.</div>'
    rows = []
    for p in pairs:
        rows.append(
            "<tr>"
            f"<td>{_esc(p.get('dex'))}</td>"
            f"<td>{_esc(p.get('name'))}</td>"
            f"<td>{_fmt_usd(p.get('liquidity_usd'))}</td>"
            f"<td>{_fmt_usd(p.get('volume_h24'))}</td>"
            f"<td class='muted'>{_esc(p.get('created_at') or p.get('first_seen_at'))}</td>"
            f"<td class='addr'>{_esc(p.get('address'))}</td>"
            f"<td class='muted'>{_esc(p.get('source'))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>DEX</th><th>Pair</th><th>Liquidity</th><th>Vol 24h</th><th>Created / seen</th><th>Pool</th><th>Source</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


@app.get("/classic", response_class=HTMLResponse)
def ui_home() -> HTMLResponse:
    body = """
    <form action="/search" method="get">
      <input type="text" name="q" placeholder="Mint address or symbol (e.g. BONK)" autofocus required>
      <button type="submit">Search</button>
    </form>
    <p class="muted">Looks up every stored pool for a token, then refreshes from DexScreener, GeckoTerminal, and Raydium.</p>
    """
    return page(body)


@app.get("/search", response_class=HTMLResponse)
def ui_search(request: Request, q: str = "", enrich: bool = True) -> HTMLResponse:
    public.guard(request)
    q = (q or "").strip()[:64]
    if enrich and not _enrich_allowed(request):
        enrich = False
    form = f"""
    <form action="/search" method="get">
      <input type="text" name="q" value="{_esc(q)}" placeholder="Mint or symbol" required>
      <button type="submit">Search</button>
    </form>
    """
    if not q:
        return page(form)
    conn = connect()
    try:
        tokens = search_tokens(conn, q)
    finally:
        conn.close()
    if not tokens:
        # still try enrich if it looks like a mint
        if enrich and public.MINT_RE.match(q):
            try:
                maybe_enrich(q, force=True)
            except Exception:
                pass
            conn = connect()
            try:
                tokens = search_tokens(conn, q)
            finally:
                conn.close()
        if not tokens:
            return page(form + f'<div class="empty">No tokens matched <b>{_esc(q)}</b>.</div>')
    exact = [t for t in tokens if (t.get("symbol") or "").casefold() == q.casefold()]
    dominant = None
    if exact:
        dominant = max(exact, key=lambda t: int(t.get("pair_count") or 0))
        if int(dominant.get("pair_count") or 0) == 0:
            dominant = exact[0]
    if len(tokens) == 1 or (len(q) >= 32 and any(t["mint"] == q for t in tokens)) or dominant:
        mint = q if any(t["mint"] == q for t in tokens) else (dominant["mint"] if dominant else tokens[0]["mint"])
        enrichment = None
        err = ""
        if enrich:
            try:
                enrichment = maybe_enrich(mint)
            except Exception:
                err = '<p class="warn">Enrichment failed; showing stored pairs.</p>'
        conn = connect()
        try:
            listed = search_tokens(conn, q)
            token = next((t for t in listed if t["mint"] == mint), None)
            if token is None:
                found = search_tokens(conn, mint)
                token = found[0] if found else {}
            pairs = pairs_for_mint(conn, mint)
        finally:
            conn.close()
        others = [t for t in listed if t["mint"] != mint]
        other_html = ""
        if others:
            links = ", ".join(
                f"<a href='/search?q={_esc(t['mint'])}'>{_esc(t.get('symbol'))} {_esc((t.get('name') or '')[:40])}</a>"
                for t in others[:12]
            )
            other_html = f"<p class='muted'>Other tokens also named {_esc(q)}: {links}</p>"
        head = (
            f"<p><b>{_esc(token.get('symbol'))}</b> · {_esc(token.get('name'))}<br>"
            f"<span class='mint'>{_esc(token.get('mint'))}</span></p>"
            f"<p class='muted'>{len(pairs)} pairs, sorted by last known liquidity."
            f" Enriched: {_esc(token.get('last_enriched_at') or 'just now')}.</p>"
            + other_html
        )
        return page(form + err + head + _pairs_table(pairs))
    items = []
    for t in tokens:
        items.append(
            f"<li><a href='/search?q={_esc(t['mint'])}'><b>{_esc(t.get('symbol'))}</b></a> "
            f"{_esc(t.get('name'))} <span class='mint'>{_esc(t['mint'])}</span></li>"
        )
    return page(form + f"<p>{len(tokens)} tokens matched. Pick one:</p><ul class='token-list'>" + "".join(items) + "</ul>")


@app.get("/new", response_class=HTMLResponse)
def ui_new() -> HTMLResponse:
    conn = connect()
    try:
        rows = new_today(conn, limit=500)
        total = new_today_count(conn)
        cutoff = today_start_utc()
    finally:
        conn.close()
    extra = (
        f"<h2 style='font-size:16px;margin:0 0 8px'>New today</h2>"
        f"<p class='muted'>Pools created or first seen since { _esc(cutoff) } UTC "
        f"(midnight Europe/Madrid). Showing {len(rows)} of {total}.</p>"
    )
    return page(extra + _pairs_table(rows))


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(SITE / "favicon.svg", media_type="image/svg+xml")


@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots() -> PlainTextResponse:
    p = SITE / "robots.txt"
    body = p.read_text() if p.exists() else "User-agent: *\nAllow: /\n"
    return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=3600"})


@app.api_route("/og-image.png", methods=["GET", "HEAD"], include_in_schema=False)
def og_image_png() -> FileResponse:
    return FileResponse(SITE / "og-image.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.api_route("/og-image.jpg", methods=["GET", "HEAD"], include_in_schema=False)
def og_image_jpg() -> FileResponse:
    return FileResponse(SITE / "og-image.jpg", media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def site_index() -> FileResponse:
    return FileResponse(SITE / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/", StaticFiles(directory=SITE), name="site")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8765, reload=False)
