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
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# ==================== CACHE ====================
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

    if volume_spike is not None:
        if volume_spike >= 5.0:
            flags.append("extreme_volume_spike")
        elif volume_spike >= 3.0:
            flags.append("volume_spike")

    if buy_pressure is not None:
        if buy_pressure >= 0.75:
            flags.append("heavy_buying")
        elif buy_pressure <= 0.25:
            flags.append("heavy_selling")

    if liquidity_usd < 20_000:
        flags.append("very_low_liquidity")
    elif liquidity_usd < 50_000:
        flags.append("low_liquidity")

    if turnover is not None:
        if turnover >= 3.0:
            flags.append("extreme_turnover")
        elif turnover >= 1.5:
            flags.append("high_turnover")

    created = pair.get("pairCreatedAt")
    age_minutes = None
    if created:
        age_minutes = int((time.time() * 1000 - created) / 60_000)
        if age_minutes < 60:
            flags.append("brand_new_pair")
        elif age_minutes < 360:
            flags.append("very_new_pair")
        elif age_minutes < 1440:
            flags.append("new_pair")

    pc = price_change
    if (pc.get("m5") or 0) > 10 and (pc.get("h1") or 0) > 20:
        flags.append("strong_momentum")
    if (pc.get("m5") or 0) < -15 and (pc.get("h1") or 0) < -25:
        flags.append("dump_risk")

    if total_5m >= 300:
        flags.append("high_activity")

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
      --bg: #070b12;
      --card: rgba(18, 25, 35, 0.75);
      --line: rgba(34, 48, 66, 0.8);
      --text: #e8eef6;
      --muted: #8b9bb4;
      --a: #14f195;
      --b: #9945ff;
      --danger: #ff4d6d;
      --warn: #ffb020;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, sans-serif;
      line-height: 1.55;
      min-height: 100vh;
      background-image: 
        radial-gradient(ellipse 80% 50% at 20% -10%, rgba(153, 69, 255, 0.18), transparent),
        radial-gradient(ellipse 60% 40% at 90% 10%, rgba(20, 241, 149, 0.12), transparent);
    }
    .wrap { max-width: 720px; margin: 0 auto; padding: 36px 18px 80px; }
    .brand {
      font-size: 2.6rem; font-weight: 800; letter-spacing: -1px;
      background: linear-gradient(120deg, var(--b), var(--a));
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .tag { color: var(--muted); margin: 6px 0 28px; font-size: 1.05rem; }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 22px;
      backdrop-filter: blur(12px);
      margin-bottom: 20px;
    }
    h2 { font-size: 1.15rem; margin: