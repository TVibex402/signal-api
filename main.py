import re
import time
import threading
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

VERSION = "1.3.0"
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58

CACHE_TTL = 30
CACHE_MAX = 500
RATE_LIMIT = 30      # requests
RATE_WINDOW = 60     # seconds, per IP


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared client = connection reuse, much faster than a new client per request
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(6.0, connect=3.0),
        headers={"User-Agent": f"TVibex402/{VERSION}"},
    )
    yield
    await app.state.client.aclose()


app = FastAPI(
    title="TVibex402",
    version=VERSION,
    description="Free Solana token data API for AI agents & bots. Not financial advice.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# ---------- cache ----------
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_cache_lock = threading.Lock()


def cache_get(key: str) -> Optional[dict]:
    with _cache_lock:
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
    with _cache_lock:
        _cache[key] = (time.time(), data)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


# ---------- rate limit (per IP, in-memory sliding window) ----------
_hits: Dict[str, deque] = {}
_rl_lock = threading.Lock()


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(ip: str) -> int:
    """Return 0 if allowed, else seconds to wait."""
    now = time.time()
    with _rl_lock:
        dq = _hits.setdefault(ip, deque())
        while dq and now - dq[0] > RATE_WINDOW:
            dq.popleft()
        if len(dq) >= RATE_LIMIT:
            return int(RATE_WINDOW - (now - dq[0])) + 1
        dq.append(now)
        if len(_hits) > 5000:  # cleanup stale IPs
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW]:
                _hits.pop(k, None)
    return 0


# ---------- signal logic ----------
def pick_best_pair(pairs: list, mint: str) -> Optional[dict]:
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        return None
    # Prefer pairs where the requested mint is the BASE token (otherwise data is for the other token)
    base_match = [p for p in sol if (p.get("baseToken") or {}).get("address") == mint]
    pool = base_match or sol
    return max(pool, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


RISK_WEIGHTS = {
    "very_low_liquidity": 30,
    "low_liquidity": 15,
    "brand_new_pair": 20,
    "very_new_pair": 10,
    "new_pair": 5,
    "extreme_turnover": 15,
    "high_turnover": 5,
    "dump_risk": 25,
    "heavy_selling": 10,
    "extreme_volume_spike": 10,
}


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
    if liquidity_usd < 20000:
        flags.append("very_low_liquidity")
    elif liquidity_usd < 50000:
        flags.append("low_liquidity")
    if turnover is not None:
        if turnover >= 3.0:
            flags.append("extreme_turnover")
        elif turnover >= 1.5:
            flags.append("high_turnover")

    created = pair.get("pairCreatedAt")
    age_minutes = None
    if created:
        age_minutes = max(0, int((time.time() * 1000 - created) / 60000))
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

    # Heuristic market-based risk score (0-100). NOT a rug-check: it does not
    # look at mint authority, holders or LP lock.
    risk_score = min(100, sum(RISK_WEIGHTS.get(f, 0) for f in flags))
    risk_level = "low" if risk_score < 25 else "medium" if risk_score < 50 else "high"

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
        "risk_score": risk_score,
        "risk_level": risk_level,
        "timestamp": int(time.time()),
        "source": "dexscreener",
        "version": VERSION,
    }


# ---------- pages ----------
HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVibex402 - Solana token data API for AI agents</title>
<meta name="description" content="Free Solana token data API for AI agents and bots. No API key, instant JSON.">
<style>
:root{--bg:#070b12;--card:rgba(18,25,35,.8);--line:rgba(34,48,66,.8);--text:#e8eef6;--muted:#8b9bb4;--a:#14f195;--b:#9945ff;--danger:#ff4d6d;--warn:#ffb020}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;line-height:1.55;min-height:100vh;background-image:radial-gradient(ellipse 80% 50% at 20% -10%,rgba(153,69,255,.18),transparent),radial-gradient(ellipse 60% 40% at 90% 10%,rgba(20,241,149,.12),transparent)}
.wrap{max-width:720px;margin:0 auto;padding:36px 18px 80px}
.brand{font-size:2.5rem;font-weight:800;background:linear-gradient(120deg,var(--b),var(--a));-webkit-background-clip:text;background-clip:text;color:transparent}
.tag{color:var(--muted);margin:6px 0 28px;font-size:1.05rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;margin-bottom:20px}
h2{font-size:1.15rem;margin:28px 0 12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid .item{background:rgba(13,17,23,.6);border:1px solid var(--line);border-radius:12px;padding:14px 12px;font-size:.9rem;color:var(--muted)}
input[type=text]{width:100%;padding:14px 16px;border-radius:12px;border:1px solid var(--line);background:rgba(13,17,23,.8);color:var(--text);font-size:.95rem;outline:none}
input:focus{border-color:var(--b)}
button{background:linear-gradient(90deg,var(--b),var(--a));color:#06110b;border:0;padding:13px 22px;border-radius:12px;font-weight:700;cursor:pointer;font-size:.95rem}
button:disabled{opacity:.6}
.muted{color:var(--muted);font-size:.88rem}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.75rem;font-weight:600;margin:3px 4px 3px 0}
.badge.green{background:rgba(20,241,149,.15);color:var(--a)}
.badge.purple{background:rgba(153,69,255,.2);color:#c084fc}
.badge.red{background:rgba(255,77,109,.15);color:var(--danger)}
.badge.yellow{background:rgba(255,176,32,.15);color:var(--warn)}
.result-box{display:none;margin-top:18px}
.result-box.show{display:block}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0}
.stat{background:rgba(13,17,23,.7);border-radius:12px;padding:12px;border:1px solid var(--line)}
.stat .label{font-size:.75rem;color:var(--muted)}
.stat .value{font-size:1.1rem;font-weight:700;margin-top:2px}
.loading{display:none;text-align:center;padding:20px;color:var(--muted)}
.loading.show{display:block}
.spinner{width:28px;height:28px;border:3px solid var(--line);border-top-color:var(--a);border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 10px}
@keyframes spin{to{transform:rotate(360deg)}}
a{color:var(--a);text-decoration:none}
pre{margin:0;background:#0d1117;padding:14px;border-radius:12px;overflow:auto;font-size:.82rem}
footer{margin-top:40px;text-align:center;color:var(--muted);font-size:.85rem}
</style>
</head>
<body>
<div class="wrap">
<h1 class="brand">TVibex402</h1>
<p class="tag">Free Solana token data API for AI agents &amp; bots</p>
<div class="card">
<p style="font-weight:600;margin-bottom:12px">Try it live</p>
<input type="text" id="mint" placeholder="Paste Solana token mint address" autocomplete="off" spellcheck="false">
<div style="margin-top:12px"><button id="btn" onclick="getSignal()">Get Signal</button></div>
<p class="muted" style="margin-top:12px">No API key &bull; No signup &bull; Instant JSON</p>
<div class="loading" id="loading"><div class="spinner"></div>Fetching live data...</div>
<div class="result-box" id="result"></div>
</div>
<h2>What you get</h2>
<div class="grid">
<div class="item">Price, FDV, Market Cap</div>
<div class="item">Price change 5m / 1h / 6h / 24h</div>
<div class="item">Liquidity + Pair age</div>
<div class="item">Volume 5m / 1h / 6h</div>
<div class="item">Buy / Sell + Buy pressure</div>
<div class="item">Volume spike + Flags + Risk score</div>
</div>
<h2>Endpoint for Agents</h2>
<div class="card" style="padding:16px">
<pre>GET /signal?mint=TOKEN_MINT_ADDRESS</pre>
<p class="muted" style="margin-top:10px">Example: <a href="/signal?mint=So11111111111111111111111111111111111111112">/signal?mint=So111...112</a></p>
<p class="muted" style="margin-top:6px">Docs: <a href="/docs">/docs</a> &bull; <a href="/llms.txt">/llms.txt</a> &bull; Limit: 30 req/min per IP</p>
</div>
<footer>TVibex402 &bull; Built for AI Agents<br>Data from DexScreener. Not financial advice. Risk score is a market heuristic, not a security audit.</footer>
</div>
<script>
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function getSignal(){
const mint=document.getElementById('mint').value.trim();
if(!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)){alert('Please paste a valid Solana mint address');return}
const btn=document.getElementById('btn'),loading=document.getElementById('loading'),result=document.getElementById('result');
btn.disabled=true;loading.classList.add('show');result.classList.remove('show');result.innerHTML='';
try{
const res=await fetch('/signal?mint='+encodeURIComponent(mint));
const data=await res.json();
if(!res.ok){result.innerHTML='<div style="color:#ff4d6d">Error: '+esc(data.detail||'Failed')+'</div>'}
else{
const flags=(data.flags||[]).map(f=>{
let cls='purple';
if(f.includes('buying')||f.includes('momentum'))cls='green';
if(f.includes('selling')||f.includes('dump')||f.includes('low'))cls='red';
if(f.includes('new')||f.includes('spike'))cls='yellow';
return '<span class="badge '+cls+'">'+esc(f)+'</span>';
}).join('');
const riskColor=data.risk_level==='high'?'var(--danger)':data.risk_level==='medium'?'var(--warn)':'var(--a)';
result.innerHTML=`<div style="margin-top:8px">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:1.4rem;font-weight:700">${esc(data.token)}</div>
<div class="muted" style="font-size:.85rem">${esc(data.name)}</div></div>
<div style="text-align:right"><div style="font-size:1.3rem;font-weight:700">$${esc(data.price_usd||'-')}</div>
<div class="muted" style="font-size:.8rem">${esc(data.dex)}</div></div></div>
<div class="stat-grid">
<div class="stat"><div class="label">Liquidity</div><div class="value">$${esc((data.liquidity_usd||0).toLocaleString())}</div></div>
<div class="stat"><div class="label">Buy Pressure 5m</div><div class="value">${data.buy_pressure_5m!=null?(data.buy_pressure_5m*100).toFixed(1)+'%':'-'}</div></div>
<div class="stat"><div class="label">Volume 5m</div><div class="value">$${esc((data.volume_5m||0).toLocaleString())}</div></div>
<div class="stat"><div class="label">Volume Spike</div><div class="value">${data.volume_spike_ratio_5m!=null?esc(data.volume_spike_ratio_5m)+'x':'-'}</div></div>
<div class="stat"><div class="label">Risk Score</div><div class="value" style="color:${riskColor}">${esc(data.risk_score)} (${esc(data.risk_level)})</div></div>
</div>
<div style="margin:12px 0 6px;font-size:.85rem;color:var(--muted)">Flags</div>
<div>${flags||'<span class="muted">None</span>'}</div></div>`;
}
result.classList.add('show');
}catch(e){result.innerHTML='<p style="color:#ff4d6d">Network error</p>';result.classList.add('show')}
loading.classList.remove('show');btn.disabled=false;
}
document.getElementById('mint').addEventListener('keypress',e=>{if(e.key==='Enter')getSignal()});
</script>
</body>
</html>"""

LLMS_TXT = """# TVibex402
> Free Solana token data API for AI agents and bots. No API key. Data from DexScreener. Not financial advice.

## Endpoint
GET /signal?mint=<SOLANA_TOKEN_MINT>
Returns JSON: price, FDV, market cap, price change (5m/1h/6h/24h), liquidity, volume, buy/sell counts,
buy pressure, volume spike ratio, pair age, flags, risk_score (0-100 heuristic), risk_level.

## Other
GET /health  - service status
GET /docs    - OpenAPI docs
Rate limit: 30 requests/minute per IP (HTTP 429 with Retry-After).
Cache: results cached up to 30 seconds (X-Cache: HIT/MISS).
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    return HOME_HTML


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
async def llms():
    return LLMS_TXT


@app.get("/demo")
async def demo():
    return {"token": "SOL", "price_usd": "119.54", "sample": True, "note": "Static sample"}


@app.get("/signal")
async def signal(request: Request, mint: str = Query(..., min_length=1, max_length=64)):
    mint = mint.strip()
    if not MINT_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid Solana token address")

    wait = rate_limited(client_ip(request))
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry in {wait}s",
            headers={"Retry-After": str(wait)},
        )

    cached = cache_get(mint)
    if cached:
        return JSONResponse(content=cached, headers={"X-Cache": "HIT", "Cache-Control": "public, max-age=10"})

    try:
        resp = await request.app.state.client.get(DEX_URL.format(mint=mint))
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="Upstream data source unavailable, try again shortly")

    pair = pick_best_pair(data.get("pairs") or [], mint)
    if not pair:
        raise HTTPException(status_code=404, detail="No Solana pair found for this token")

    result = compute_signals(pair)
    cache_set(mint, result)
    return JSONResponse(content=result, headers={"X-Cache": "MISS", "Cache-Control": "public, max-age=10"})


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION, "cache_size": len(_cache)}
