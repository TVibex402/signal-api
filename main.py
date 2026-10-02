from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
from typing import Optional, Dict, Any
import time
from collections import OrderedDict
import threading

app = FastAPI(
    title="TVibex402",
    description="Free Solana token data API for AI agents & bots",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# ==================== CACHE (TTL 30 giây) ====================
CACHE_TTL = 30
CACHE_MAX = 200
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_lock = threading.Lock()


def cache_get(key: str) -> Optional[dict]:
    with _lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, data = item
        if time.time() - ts > CACHE_TTL:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return data


def cache_set(key: str, data: dict):
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
        _cache[key] = (time.time(), data)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


# ==================== DEXSCREENER ====================
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"


def pick_best_pair(pairs: list) -> Optional[dict]:
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        return None
    return max(sol, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


def compute_signals(pair: dict) -> Dict[str, Any]:
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}
    liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or 0)

    m5 = txns.get("m5") or {}
    buys_5m = int(m5.get("buys") or 0)
    sells_5m = int(m5.get("sells") or 0)
    total_5m = buys_5m + sells_5m

    buy_pressure = round(buys_5m / total_5m, 3) if total_5m > 0 else None

    vol_5m = float(volume.get("m5") or 0)
    vol_1h = float(volume.get("h1") or 0)
    vol_6h = float(volume.get("h6") or 0)

    avg_5m = vol_1h / 12 if vol_1h > 0 else 0
    volume_spike = round(vol_5m / avg_5m, 2) if avg_5m > 0 else None

    turnover = round(vol_1h / liquidity_usd, 3) if liquidity_usd > 0 else None

    flags = []
    if volume_spike is not None and volume_spike >= 3.0:
        flags.append("volume_spike")
    if buy_pressure is not None and buy_pressure >= 0.70:
        flags.append("heavy_buying")
    if buy_pressure is not None and buy_pressure <= 0.30:
        flags.append("heavy_selling")
    if liquidity_usd < 50_000:
        flags.append("low_liquidity")
    if turnover is not None and turnover >= 1.5:
        flags.append("high_turnover")

    created = pair.get("pairCreatedAt")
    age_minutes = None
    if created:
        age_minutes = int((time.time() * 1000 - created) / 60_000)
        if age_minutes < 1440:
            flags.append("new_pair")

    return {
        "token": base.get("symbol") or "UNKNOWN",
        "name": base.get("name"),
        "mint": base.get("address"),
        "price_usd": pair.get("priceUsd"),
        "price_change_pct": {
            "5m": price_change.get("m5"),
            "1h": price_change.get("h1"),
            "6h": price_change.get("h6"),
            "24h": price_change.get("h24"),
        },
        "liquidity_usd": round(liquidity_usd, 2),
        "fdv_usd": pair.get("fdv"),
        "market_cap_usd": pair.get("marketCap"),
        "volume_5m": round(vol_5m, 2),
        "volume_1h": round(vol_1h, 2),
        "volume_6h": round(vol_6h, 2) if vol_6h else None,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "buy_pressure_5m": buy_pressure,
        "volume_spike_ratio_5m": volume_spike,
        "turnover_1h": turnover,
        "pair_age_minutes": age_minutes,
        "dex": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "flags": flags,
    }


# ==================== ROUTES ====================

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TVibex402 | Solana Token Signal API</title>
  <style>
    :root {
      --bg: #0b0f14; --card: #121923; --line: #223042;
      --text: #e6edf3; --muted: #8b9bb0; --a: #14f195; --b: #9945ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--text);
      font-family: system-ui, -apple-system, sans-serif; line-height: 1.55;
    }
    .wrap { max-width: 720px; margin: 0 auto; padding: 32px 18px 70px; }
    .brand {
      font-size: 2.4rem; font-weight: 800; margin: 0;
      background: linear-gradient(90deg, var(--b), var(--a));
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .tag { color: var(--muted); margin: .4rem 0 1.4rem; }
    .card {
      background: var(--card); border: 1px solid var(--line);
      border-radius: 14px; padding: 18px; margin: 1.2rem 0;
    }
    h2 { font-size: 1.15rem; margin: 2rem 0 .7rem; }
    pre {
      background: #0d1117; padding: 14px; border-radius: 10px;
      overflow-x: auto; font-size: .88rem; line-height: 1.45;
    }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    a { color: var(--a); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .muted { color: var(--muted); font-size: .92rem; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .grid .card { padding: 12px; font-size: .9rem; margin: 0; }
    input[type=text] {
      width: 100%; padding: 12px; border-radius: 10px;
      border: 1px solid var(--line); background: #0d1117; color: var(--text);
      font-size: .95rem; margin-bottom: 10px;
    }
    button {
      background: linear-gradient(90deg, var(--b), var(--a));
      color: #06110b; border: 0; padding: 11px 18px; border-radius: 10px;
      font-weight: 600; cursor: pointer; font-size: .95rem;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1 class="brand">TVibex402</h1>
    <p class="tag">Free Solana token data API for AI agents & bots</p>

    <div class="card">
      <p style="margin-top:0"><b>Try it live</b></p>
      <form action="/signal" method="get">
        <input type="text" name="mint" placeholder="Paste Solana token mint address"
               required minlength="32" maxlength="44" autocomplete="off">
        <button type="submit">Get Signal</button>
      </form>
      <p class="muted" style="margin-bottom:0">No API key • No signup • JSON response</p>
    </div>

    <h2>What you get</h2>
    <div class="grid">
      <div class="card">Price, FDV, Market Cap</div>
      <div class="card">Price change 5m / 1h / 6h / 24h</div>
      <div class="card">Liquidity + Pair age</div>
      <div class="card">Volume 5m / 1h / 6h</div>
      <div class="card">Buy / Sell count + Buy pressure</div>
      <div class="card">Volume spike + Turnover + Flags</div>
    </div>

    <h2>Endpoint</h2>
    <pre><code>GET /signal?mint=TOKEN_MINT_ADDRESS</code></pre>
    <p class="muted">Example:
      <a href="/signal?mint=So11111111111111111111111111111111111111112">
        /signal?mint=So11111111111111111111111111111111111111112
      </a>
    </p>

    <h2>Sample response</h2>
    <pre><code>{
  "token": "SOL",
  "price_usd": "119.54",
  "price_change_pct": {"5m": 0.1, "1h": -0.4, "6h": 1.2, "24h": 2.8},
  "liquidity_usd": 37607261.15,
  "volume_5m": 31690.17,
  "volume_1h": 458039.23,
  "buys_5m": 468,
  "sells_5m": 365,
  "buy_pressure_5m": 0.562,
  "volume_spike_ratio_5m": 0.83,
  "turnover_1h": 0.01,
  "flags": []
}</code></pre>

    <h2>Notes</h2>
    <ul class="muted">
      <li>Data from DexScreener (may be slightly delayed)</li>
      <li>Flags are simple heuristics — not financial advice</li>
      <li>Cache 30 seconds to reduce load</li>
      <li>Free during beta</li>
    </ul>
  </div>
</body>
</html>
"""


@app.get("/demo")
async def demo():
    return {
        "token": "SOL",
        "name": "Wrapped SOL",
        "mint": "So11111111111111111111111111111111111111112",
        "price_usd": "119.54",
        "price_change_pct": {"5m": 0.1, "1h": -0.4, "6h": 1.2, "24h": 2.8},
        "liquidity_usd": 37607261.15,
        "fdv_usd": None,
        "market_cap_usd": None,
        "volume_5m": 31690.17,
        "volume_1h": 458039.23,
        "volume_6h": None,
        "buys_5m": 468,
        "sells_5m": 365,
        "buy_pressure_5m": 0.562,
        "volume_spike_ratio_5m": 0.83,
        "turnover_1h": 0.01,
        "pair_age_minutes": None,
        "dex": "orca",
        "pair_address": None,
        "flags": [],
        "sample": True,
        "note": "Static sample. /signal returns live data."
    }


@app.get("/signal")
async def signal(
    mint: str = Query(..., min_length=32, max_length=48, description="Solana token mint address")
):
    mint = mint.strip()

    if not (32 <= len(mint) <= 44) or not all(c.isalnum() for c in mint):
        raise HTTPException(status_code=400, detail="Invalid token address")

    cached = cache_get(mint)
    if cached:
        return JSONResponse(content=cached, headers={"X-Cache": "HIT"})

    url = DEX_URL.format(mint=mint)
    try:
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Could not fetch data from DexScreener")
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout while fetching data")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch data: {type(e).__name__}")

    pairs = data.get("pairs") or []
    pair = pick_best_pair(pairs)

    if not pair:
        raise HTTPException(status_code=404, detail="No Solana trading pair found for this mint")

    result = compute_signals(pair)
    cache_set(mint, result)

    return JSONResponse(content=result, headers={"X-Cache": "MISS"})


@app.get("/health")
async def health():
    return {"status": "ok", "cache_size": len(_cache)}