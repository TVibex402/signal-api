import asyncio
import json
import math
import os
import re
import time
import threading
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

VERSION = "1.10.0"
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58

CACHE_TTL = 30
CACHE_MAX = 500
SEC_CACHE_TTL = 120  # authorities/holders change slowly

# Solana RPC. The public endpoint is heavily rate-limited: set SOLANA_RPC_URL
# (e.g. a free Helius URL) in Render > Environment for reliable results.
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"  # public, no key
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
RISKY_EXTENSIONS = {"permanentDelegate", "transferHook", "transferFeeConfig", "defaultAccountState"}
# Holders that are not "real" holders: burn address + common AMM pool authorities (best effort)
KNOWN_NON_HOLDERS = {
    "1nc1nerator11111111111111111111111111111111",  # burn
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM v4 authority
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",  # Raydium CPMM authority
}
RATE_LIMIT = 30      # requests
RATE_WINDOW = 60     # seconds, per IP
GLOBAL_LIMIT = 250   # upstream fetches/minute across ALL clients (protects DexScreener quota)
MAX_BATCH = 10
SECONDARY_TIMEOUT = 2.5  # max wait for RPC / RugCheck before answering with partial data
PARTIAL_TTL = 8          # partial results are cached only briefly
STALE_MAX = 600          # serve cached data up to 10 min old if upstream is down
# How many trusted proxies sit in front of the app (Render = 1). The client IP is read
# from the RIGHT side of X-Forwarded-For, so a client cannot spoof it by sending its own header.
TRUSTED_HOPS = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))


mcp_server = None  # set at the bottom of this file when the `mcp` package is installed


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared client = connection reuse, much faster than a new client per request
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(6.0, connect=3.0),
        headers={"User-Agent": f"TVibex402/{VERSION}"},
    )
    resolver = asyncio.create_task(resolver_loop(app)) if PRED_ENABLED else None
    try:
        if mcp_server is not None:
            # the MCP Streamable HTTP session manager must run for the app's whole lifetime
            async with mcp_server.session_manager.run():
                yield
        else:
            yield
    finally:
        if resolver:
            resolver.cancel()
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


def cache_peek(key: str):
    """Return (age_seconds, data) or None. Entries are NOT dropped when expired,
    so they can still be served as stale if upstream fails (LRU keeps the size bounded)."""
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, data = item
        _cache.move_to_end(key)
        return time.time() - ts, data


def cache_get(key: str, ttl: int = CACHE_TTL) -> Optional[dict]:
    hit = cache_peek(key)
    return hit[1] if hit and hit[0] <= ttl else None


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
        parts = [x.strip() for x in xff.split(",") if x.strip()]
        if len(parts) >= TRUSTED_HOPS:
            return parts[-TRUSTED_HOPS]
        if parts:
            return parts[0]
    return request.client.host if request.client else "unknown"


def rate_limited(key: str, limit: int = RATE_LIMIT, cost: int = 1) -> int:
    """Return 0 if allowed (and record `cost` hits), else seconds to wait."""
    now = time.time()
    with _rl_lock:
        dq = _hits.setdefault(key, deque())
        while dq and now - dq[0] > RATE_WINDOW:
            dq.popleft()
        if len(dq) + cost > limit:
            return int(RATE_WINDOW - (now - dq[0])) + 1 if dq else RATE_WINDOW
        dq.extend([now] * cost)
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
    "mint_authority_active": 25,
    "freeze_authority_active": 20,
    "risky_token_extension": 20,
    "extreme_holder_concentration": 30,
    "high_holder_concentration": 15,
    "dominant_holder": 10,
    "lp_not_locked": 25,
    "lp_partially_locked": 10,
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
        "timestamp": int(time.time()),
        "source": "dexscreener",
        "version": VERSION,
    }


# ---------- on-chain security (Solana RPC) ----------
async def rpc(client: httpx.AsyncClient, method: str, params: list):
    resp = await client.post(
        RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"])[:200])
    return body.get("result")


async def fetch_security(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    """Raw on-chain data. Never raises; returns None if nothing could be read."""
    t_rpc = time.time()
    info_r, big_r = await asyncio.gather(
        rpc(client, "getAccountInfo", [mint, {"encoding": "jsonParsed"}]),
        rpc(client, "getTokenLargestAccounts", [mint, {"commitment": "confirmed"}]),
        return_exceptions=True,
    )
    src_log("solana_rpc", not isinstance(info_r, Exception) and not isinstance(big_r, Exception), t_rpc)
    raw: Dict[str, Any] = {"info_ok": False, "holders": None, "supply": None}

    try:
        val = (info_r or {}).get("value") if not isinstance(info_r, Exception) else None
        data = (val or {}).get("data")
        parsed = data.get("parsed") if isinstance(data, dict) else None
        if parsed and parsed.get("type") == "mint":
            info = parsed.get("info") or {}
            raw.update(
                info_ok=True,
                mint_authority=info.get("mintAuthority"),
                freeze_authority=info.get("freezeAuthority"),
                decimals=info.get("decimals"),
                supply=int(info.get("supply") or 0) or None,
                program="token-2022" if val.get("owner") == TOKEN_2022 else "spl-token",
                extensions=[e.get("extension") for e in (info.get("extensions") or []) if isinstance(e, dict)],
            )
    except Exception:
        pass

    try:
        if not isinstance(big_r, Exception) and big_r:
            accts = big_r.get("value") or []
            addrs = [a["address"] for a in accts]
            owners: Dict[str, str] = {}
            if addrs:
                try:
                    multi = await rpc(client, "getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
                    for addr, acc in zip(addrs, (multi or {}).get("value") or []):
                        try:
                            owners[addr] = acc["data"]["parsed"]["info"]["owner"]
                        except (TypeError, KeyError):
                            pass
                except Exception:
                    pass
            raw["holders"] = [
                {"account": a["address"], "owner": owners.get(a["address"]), "amount": int(a.get("amount") or 0)}
                for a in accts
            ]
    except Exception:
        pass

    if not raw["info_ok"] and raw["holders"] is None:
        return None
    return raw


async def get_security(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    key = "sec:" + mint
    cached = cache_get(key, SEC_CACHE_TTL)
    if cached:
        return cached
    try:
        raw = await fetch_security(client, mint)
    except Exception:
        return None
    if raw:
        cache_set(key, raw)
    return raw


def build_security(raw: Optional[dict], pool_ids: set):
    """Turn raw on-chain data into a public `security` block + extra flags."""
    if not raw:
        return {"available": False}, []
    flags = []
    out: Dict[str, Any] = {"available": True, "partial": False}

    if raw.get("info_ok"):
        ma, fa = raw.get("mint_authority"), raw.get("freeze_authority")
        out.update(
            mint_authority_revoked=ma is None,
            freeze_authority_revoked=fa is None,
            token_program=raw.get("program"),
            decimals=raw.get("decimals"),
        )
        risky = sorted(set(raw.get("extensions") or []) & RISKY_EXTENSIONS)
        out["risky_extensions"] = risky
        if ma is not None:
            flags.append("mint_authority_active")
        if fa is not None:
            flags.append("freeze_authority_active")
        if risky:
            flags.append("risky_token_extension")
    else:
        out["partial"] = True

    holders, supply = raw.get("holders"), raw.get("supply")
    if holders and supply:
        excluded_ids = pool_ids | KNOWN_NON_HOLDERS
        real, pool_pct = [], 0.0
        for h in holders:
            pct = h["amount"] / supply * 100
            if h["owner"] in excluded_ids or h["account"] in excluded_ids:
                pool_pct += pct
            else:
                real.append({"owner": h["owner"] or h["account"], "pct": round(pct, 2)})
        top1 = real[0]["pct"] if real else 0.0
        top10 = round(sum(h["pct"] for h in real[:10]), 2)
        out.update(
            top1_holder_pct=top1,
            top10_holders_pct=top10,
            liquidity_pool_pct=round(pool_pct, 2),
            top_holders=real[:5],
        )
        if top10 >= 80:
            flags.append("extreme_holder_concentration")
        elif top10 >= 50:
            flags.append("high_holder_concentration")
        if top1 >= 20:
            flags.append("dominant_holder")
    else:
        out["partial"] = True
    return out, flags


def finalize_risk(result: dict, onchain: bool):
    """Heuristic 0-100 score from market flags (+ on-chain flags when available).
    It is a quick filter, NOT a guarantee."""
    score = min(100, sum(ACTIVE_WEIGHTS.get(f, 0) for f in result["flags"]))
    result["scoring"] = "tuned" if _tune["on"] else "static"
    result["risk_score"] = score
    result["risk_level"] = "low" if score < 25 else "medium" if score < 50 else "high"
    result["risk_basis"] = "market+onchain" if onchain else "market"


async def get_lp_lock(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    """LP lock/burn % + RugCheck score from RugCheck's public summary. Never raises."""
    key = "lp:" + mint
    cached = cache_get(key, SEC_CACHE_TTL)
    if cached:
        return cached
    t0 = time.time()
    try:
        resp = await client.get(RUGCHECK_URL.format(mint=mint))
        resp.raise_for_status()
        j = resp.json()
        pct = j.get("lpLockedPct")
        out = {
            "lp_locked_pct": round(float(pct), 2) if pct is not None else None,
            "rugcheck_score": j.get("score_normalised"),  # 0-100, higher = riskier
            "rugcheck_risks": [
                {"name": r.get("name"), "level": r.get("level")}
                for r in (j.get("risks") or [])[:6]
                if isinstance(r, dict)
            ],
        }
        src_log("rugcheck", True, t0)
    except Exception:
        src_log("rugcheck", False, t0)
        return None
    cache_set(key, out)
    return out


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=mint))
        resp.raise_for_status()
        data = resp.json()
        src_log("dexscreener", True, t0)
        return data
    except (httpx.HTTPError, ValueError):
        src_log("dexscreener", False, t0)
        raise HTTPException(status_code=502, detail="Upstream data source unavailable, retry in a few seconds")


# ---------- verdict ----------
REASONS = {
    "mint_authority_active": "Mint authority still active: supply can be inflated",
    "freeze_authority_active": "Freeze authority active: wallets can be frozen",
    "risky_token_extension": "Token-2022 extension that can restrict or tax transfers",
    "lp_not_locked": "Liquidity is not locked or burned",
    "dump_risk": "Price is dumping fast",
    "extreme_holder_concentration": "Top 10 holders own 80%+ of supply",
    "very_low_liquidity": "Liquidity under $20k",
    "low_liquidity": "Liquidity under $50k",
    "high_holder_concentration": "Top 10 holders own 50%+ of supply",
    "dominant_holder": "One wallet holds 20%+ of supply",
    "lp_partially_locked": "Liquidity only partly locked or burned",
    "brand_new_pair": "Pair is under 1 hour old",
    "extreme_turnover": "Volume is extreme vs liquidity (wash-trading risk)",
}
AVOID_FLAGS = ["mint_authority_active", "freeze_authority_active", "risky_token_extension",
               "lp_not_locked", "dump_risk", "extreme_holder_concentration"]
CAUTION_FLAGS = ["very_low_liquidity", "low_liquidity", "high_holder_concentration", "dominant_holder",
                 "lp_partially_locked", "brand_new_pair", "extreme_turnover"]
AUTHORITY_FLAGS = {"mint_authority_active", "freeze_authority_active"}


def add_verdict(result: dict):
    """One-line decision for agents: ok | caution | avoid (heuristic, not advice)."""
    flags = set(result["flags"])
    # Established tokens (e.g. regulated stablecoins) legitimately keep authorities
    established = result["liquidity_usd"] >= 1_000_000 and (result.get("pair_age_minutes") or 0) >= 43200
    avoid = [f for f in AVOID_FLAGS if f in flags and not (established and f in AUTHORITY_FLAGS)]
    caution = [f for f in CAUTION_FLAGS if f in flags]
    if established:
        caution = [f for f in AUTHORITY_FLAGS if f in flags] + caution
    score = result["risk_score"]
    result["verdict"] = "avoid" if avoid or score >= 50 else "caution" if caution or score >= 25 else "ok"
    result["verdict_reasons"] = [REASONS[f] for f in avoid + caution]
    # "full" only when BOTH on-chain sources answered in time
    result["verdict_confidence"] = "full" if result.get("security") and not result.get("partial") else "market_only"
    top = "; ".join(result["verdict_reasons"][:3]) or "no major red flags found"
    result["summary"] = (
        f"{result.get('token') or '?'}: {result['verdict'].upper()} ({result['verdict_confidence']} data). "
        f"{top}. Liquidity ${result['liquidity_usd']:,.0f}, risk {result['risk_score']}/100."
    )
    nxt = []
    if result.get("partial"):
        nxt.append("retry in a few seconds for full on-chain data")
    if result["verdict"] != "avoid":
        nxt.append("run the exit check (GET /exit or MCP check_exit) before sizing a trade")
    result["suggested_next"] = nxt


# ---------- pipeline ----------
_bg: set = set()


async def soft(coro, timeout: float):
    """Wait up to `timeout`, then give up for THIS request but let the work finish
    in the background so it lands in the cache for the next request."""
    task = asyncio.ensure_future(coro)
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    try:
return await asyncio.wait_for(asyncio.shield(task), timeout)
    except Exception:
        return None


async def build_signal(client: httpx.AsyncClient, mint: str, security: bool):
    """Return (result, cache_status). Raises HTTPException on failure."""
    cache_key = mint if security else mint + ":nosec"
    hit = cache_peek(cache_key)
    stale = None
    if hit:
        age, cached = hit
        if age <= (PARTIAL_TTL if cached.get("partial") else CACHE_TTL):
            return cached, "HIT"
        if age <= STALE_MAX:
            stale = (age, cached)

    def serve_stale(err: HTTPException):
        if stale:
            return dict(stale[1], stale=True, stale_age_s=int(stale[0])), "STALE"
        raise err

    # global budget for upstream calls, independent of client IP
    if rate_limited("__global__", GLOBAL_LIMIT):
        return serve_stale(HTTPException(status_code=503, detail="Service busy, retry shortly"))

    try:
        if security:
            data, raw_sec, lp_raw = await asyncio.gather(
                fetch_dex(client, mint),
                soft(get_security(client, mint), SECONDARY_TIMEOUT),
                soft(get_lp_lock(client, mint), SECONDARY_TIMEOUT),
            )
        else:
            data, raw_sec, lp_raw = await fetch_dex(client, mint), None, None
    except HTTPException as e:
        return serve_stale(e)

    pair = pick_best_pair(data.get("pairs") or [], mint)
    if not pair:
        raise HTTPException(status_code=404, detail="No Solana pair found for this token")

    result = compute_signals(pair)
    result["partial"] = bool(security and (raw_sec is None or lp_raw is None))
    if security:
        result["sources"] = {"dexscreener": "ok", "solana_rpc": "ok" if raw_sec else "missing",
                             "rugcheck": "ok" if lp_raw else "missing"}
    onchain = False
    if security:
        pool_ids = {x for x in (pair.get("pairAddress"),) if x}
        sec, sec_flags = build_security(raw_sec, pool_ids)
        result["flags"].extend(sec_flags)
        if lp_raw:
            pct = lp_raw.get("lp_locked_pct")
            sec.update(lp_locked_pct=pct, rugcheck_score=lp_raw.get("rugcheck_score"),
                       rugcheck_risks=lp_raw.get("rugcheck_risks"), lp_source="rugcheck")
            # bonding-curve tokens have no LP yet, so skip the LP flags there
            if pct is not None and pair.get("dexId") != "pumpfun":
                if pct < 10:
                    result["flags"].append("lp_not_locked")
                elif pct < 80:
                    result["flags"].append("lp_partially_locked")
        result["security"] = sec
        onchain = bool(sec.get("available")) or bool(lp_raw)
    finalize_risk(result, onchain)
    add_verdict(result)
    cache_set(cache_key, result)
    if security and PRED_ENABLED:
        schedule(record_prediction(client, result))  # fire and forget: adds no latency
    return result, "MISS"


# ---------- self-calibration: track record, prediction error, source health ----------
# Every verdict is a prediction. About 24h later we measure what happened; the gap between
# prediction and outcome ("prediction error") feeds public stats and, optionally, the risk weights.
REDIS_URL = (os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
PRED_ENABLED = bool(REDIS_URL and REDIS_TOKEN)
PRED_HORIZON_S = int(float(os.getenv("PRED_HORIZON_HOURS", "24")) * 3600)
MAX_PRED_PER_DAY = int(os.getenv("MAX_PRED_PER_DAY", "300"))  # keeps Upstash usage well inside the free tier
AUTO_TUNE = os.getenv("AUTO_TUNE", "").lower() in ("1", "true", "yes")  # OFF by default
BAD_RETURN_PCT = -50.0   # "bad outcome": price down 50%+ ...
BAD_LIQ_RATIO = 0.3      # ... or liquidity down 70%+ (or the pair vanished)
MIN_N_SHOW = 20          # never show rates from fewer than 20 samples
STATS_TTL = 600
SOL_MINT = "So11111111111111111111111111111111111111112"
RESOLVE_BATCH = 25       # predictions judged per cycle (+1 slot for the SOL baseline price)
MIN_DAY_N = 5            # a day needs this many resolved predictions to count as a market-background cohort

ACTIVE_WEIGHTS: Dict[str, int] = dict(RISK_WEIGHTS)
_tune: Dict[str, Any] = {"on": False, "n": 0}
_pred_day: Dict[str, Any] = {"day": "", "count": 0}
_stats_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

# ----- source health (in memory) -----
_src: Dict[str, deque] = {}
SRC_WINDOW_S = 600


def src_log(name: str, ok: bool, t0: float):
    _src.setdefault(name, deque(maxlen=40)).append((time.time(), bool(ok), (time.time() - t0) * 1000))


def src_status() -> dict:
    now, out = time.time(), {}
    for name in ("dexscreener", "solana_rpc", "rugcheck", "jupiter"):
        recent = [x for x in _src.get(name, ()) if now - x[0] <= SRC_WINDOW_S]
        if not recent:
            out[name] = {"status": "unknown"}
            continue
        okr = sum(1 for x in recent if x[1]) / len(recent)
        out[name] = {"status": "ok" if okr >= 0.8 else "degraded" if okr >= 0.4 else "down",
                     "success_rate_pct": round(okr * 100), "avg_ms": round(sum(x[2] for x in recent) / len(recent)),
                     "samples": len(recent)}
    return out


# ----- Upstash Redis over REST (no extra dependency) -----
async def redis_pipe(client: httpx.AsyncClient, cmds: list) -> Optional[list]:
    if not PRED_ENABLED:
        return None
    try:
        resp = await client.post(
            REDIS_URL + "/pipeline",
            json=[[str(x) for x in c] for c in cmds],
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            timeout=5.0,
        )
        resp.raise_for_status()
        return [(x.get("result") if isinstance(x, dict) else None) for x in resp.json()]
    except Exception:
        return None


def to_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return dict(zip(v[0::2], v[1::2]))
    return {}


def schedule(coro):
    t = asyncio.ensure_future(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


_sol: Dict[str, Any] = {"ts": 0.0, "price": None}


async def sol_price(client: httpx.AsyncClient) -> Optional[float]:
    """SOL/USD, cached 60s. This is the 'background expansion' every token rides on."""
    if _sol["price"] and time.time() - _sol["ts"] < 60:
        return _sol["price"]
    price = None
    try:
        resp = await client.get(DEX_URL.format(mint=SOL_MINT))
        resp.raise_for_status()
        pair = pick_best_pair([p for p in (resp.json().get("pairs") or [])
                               if (p.get("baseToken") or {}).get("address") == SOL_MINT], SOL_MINT)
        price = float(pair.get("priceUsd")) if pair else None
    except (httpx.HTTPError, ValueError, TypeError):
        price = None
    if price and price > 0:
        _sol.update(ts=time.time(), price=price)
        return price
    return None


async def record_prediction(client: httpx.AsyncClient, r: dict):
    """Store one verdict to be judged later. At most one per mint per 6h and MAX_PRED_PER_DAY per day."""
    try:
        price = float(r.get("price_usd") or 0)
        mint = r.get("mint")
        if price <= 0 or not mint:
            return
        today = time.strftime("%Y%m%d", time.gmtime())
        if _pred_day["day"] != today:
            _pred_day.update(day=today, count=0)
        if _pred_day["count"] >= MAX_PRED_PER_DAY:
            return
        lock = await redis_pipe(client, [["SET", f"plock:{mint}", "1", "EX", 21600, "NX"]])
        if not lock or lock[0] != "OK":
            return
        _pred_day["count"] += 1
        now = int(time.time())
        pid = f"{mint}:{now}"
        sol = await sol_price(client)
        await redis_pipe(client, [
            ["HSET", f"pred:{pid}", "mint", mint, "token", r.get("token") or "", "ts", now, "price", price,
             "liq", r.get("liquidity_usd") or 0, "verdict", r["verdict"], "conf", r["verdict_confidence"],
             "score", r["risk_score"], "flags", ",".join(r.get("flags") or []), "sol", sol or ""],
            ["EXPIRE", f"pred:{pid}", PRED_HORIZON_S * 4],
            ["ZADD", "due", now + PRED_HORIZON_S, pid],
        ])
    except Exception:
        pass


async def resolve_due(client: httpx.AsyncClient) -> int:
    """Judge predictions whose time has come. Returns how many were processed."""
    now = int(time.time())
    got = await redis_pipe(client, [["ZRANGEBYSCORE", "due", "-inf", now, "LIMIT", 0, RESOLVE_BATCH]])
    ids = (got or [[]])[0] or []
    if not ids:
        return 0
    hashes = await redis_pipe(client, [["HGETALL", f"pred:{i}"] for i in ids])
    if hashes is None:
        return 0
    cmds: list = []
    preds: Dict[str, dict] = {}
    for pid, h in zip(ids, hashes):
        d = to_dict(h)
        if d.get("mint"):
            preds[pid] = d
        else:
            cmds.append(["ZREM", "due", pid])  # hash expired: drop the orphan
    best: Dict[str, Optional[dict]] = {}
    sol1: Optional[float] = None
    if preds:
        mints = sorted({d["mint"] for d in preds.values()})
        ask = mints if SOL_MINT in mints else mints + [SOL_MINT]
        t0 = time.time()
        try:
            resp = await client.get(DEX_URL.format(mint=",".join(ask)))
            resp.raise_for_status()
            pairs = resp.json().get("pairs") or []
            src_log("dexscreener", True, t0)
        except (httpx.HTTPError, ValueError):
            src_log("dexscreener", False, t0)
            if cmds:
                await redis_pipe(client, cmds)
            return 0  # try again next cycle, nothing is lost
        for m in mints:
            best[m] = pick_best_pair([p for p in pairs if (p.get("baseToken") or {}).get("address") == m], m)
        sp = pick_best_pair([p for p in pairs if (p.get("baseToken") or {}).get("address") == SOL_MINT], SOL_MINT)
        try:
            sol1 = float(sp.get("priceUsd")) if sp else None
        except (TypeError, ValueError):
            sol1 = None

    recent = []
    for pid, d in preds.items():
        cmds += [["ZREM", "due", pid], ["DEL", f"pred:{pid}"]]
        age = now - int(d.get("ts") or 0)
        if age > PRED_HORIZON_S * 3:
            continue  # judged far too late (server was asleep): would distort the stats
        p0, l0 = float(d.get("price") or 0), float(d.get("liq") or 0)
        pair = best.get(d["mint"])
        if pair is None:
            ret, liq_ratio = -100.0, 0.0
        else:
            p1 = float(pair.get("priceUsd") or 0)
            l1 = float((pair.get("liquidity") or {}).get("usd") or 0)
            ret = (p1 / p0 - 1) * 100 if p0 > 0 else 0.0
            liq_ratio = l1 / l0 if l0 > 0 else 1.0
        bad = ret <= BAD_RETURN_PCT or liq_ratio <= BAD_LIQ_RATIO
        # market background: how much did SOL itself move over the same window?
        sol0 = float(d.get("sol") or 0)
        sol_ret = (sol1 / sol0 - 1) * 100 if (sol1 and sol0 > 0) else None
        xret = ret - sol_ret if sol_ret is not None else None
        v, c = d.get("verdict", "?"), d.get("conf", "?")
        bucket = min(9, int(float(d.get("score") or 0)) // 10)
        day = time.strftime("%Y%m%d", time.gmtime(int(d.get("ts") or 0)))
        flags_t = [f for f in (d.get("flags") or "").split(",") if f in RISK_WEIGHTS]
        groups = ["all", f"v:{v}:{c}", f"rb:{bucket}", f"d:{day}"]
        groups += [f"f:{f}" for f in flags_t] + [f"fd:{f}:{day}" for f in flags_t]
        for g in groups:
            cmds.append(["HINCRBY", "st", f"{g}:n", 1])
            if bad:
                cmds.append(["HINCRBY", "st", f"{g}:bad", 1])
        cmds.append(["HINCRBYFLOAT", "st", f"v:{v}:{c}:ret", round(ret, 2)])
        if xret is not None:
            cmds += [["HINCRBYFLOAT", "st", f"v:{v}:{c}:xret", round(xret, 2)], ["HINCRBY", "st", f"v:{v}:{c}:xn", 1]]
        recent.append(json.dumps({"token": d.get("token"), "mint": d["mint"], "verdict": v, "risk_score": float(d.get("score") or 0),
                                  "return_pct": round(ret, 1), "sol_return_pct": round(sol_ret, 1) if sol_ret is not None else None,
                                  "excess_return_pct": round(xret, 1) if xret is not None else None,
                                  "bad_outcome": bad, "age_h": round(age / 3600, 1)}))
    for item in recent:
        cmds.append(["LPUSH", "recent", item])
    if recent:
        cmds.append(["LTRIM", "recent", 0, 49])
    if cmds:
        await redis_pipe(client, cmds)
    return len(ids)


def rate(n: int, bad: int) -> Optional[float]:
    return round(bad / n * 100, 1) if n >= MIN_N_SHOW else None


def flag_stats(st: dict):
    """Per-flag outcome stats. The lift is ADJUSTED for the market background: each flagged token is compared
    with the bad-outcome rate of its own day (observed / expected), so a flag that merely shows up on crash
    days is not blamed for the crash. Falls back to the raw lift until 2+ days of cohort data exist."""
    n_all, bad_all = int(st.get("all:n", 0)), int(st.get("all:bad", 0))
    day_rate: Dict[str, float] = {}
    for k, v in st.items():
        if k.startswith("d:") and k.endswith(":n") and int(v) >= MIN_DAY_N:
            day = k[2:-2]
            day_rate[day] = int(st.get(f"d:{day}:bad", 0)) / int(v)
    fd: Dict[str, Dict[str, List[int]]] = {}  # flag -> day -> [n, bad]
    for k, v in st.items():
        if k.startswith("fd:") and (k.endswith(":n") or k.endswith(":bad")):
            parts = k.split(":")
            if len(parts) == 4:
                e = fd.setdefault(parts[1], {}).setdefault(parts[2], [0, 0])
                e[0 if parts[3] == "n" else 1] = int(v)
    base = bad_all / n_all if n_all else 0.0
    adjusted_ok = len(day_rate) >= 2
    rows = []
    for f, prior in RISK_WEIGHTS.items():
        n, bad = int(st.get(f"f:{f}:n", 0)), int(st.get(f"f:{f}:bad", 0))
        row: Dict[str, Any] = {"flag": f, "n": n, "bad_rate_pct": rate(n, bad), "lift_raw": None, "lift": None,
                               "market_adjusted": False, "prior_weight": prior, "suggested_weight": None, "blended_weight": prior}
        if n_all >= 100 and bad_all > 0 and n >= 30:
            raw = ((bad + base * 10) / (n + 10)) / base  # smoothed toward the base rate
            lift = raw
            if adjusted_ok:
                obs = exp = 0.0
                for day, (nf, bf) in fd.get(f, {}).items():
                    obs += bf
                    exp += nf * day_rate.get(day, base)
                lift = (obs + base * 10) / (exp + base * 10)
                row["market_adjusted"] = True
            sugg = max(0, min(40, round(25 * math.log(lift)))) if lift > 1 else 0
            alpha = min(0.5, n / 400)  # trust data more as n grows, never more than 50%
            row.update(lift_raw=round(raw, 2), lift=round(lift, 2), suggested_weight=sugg,
                       blended_weight=round((1 - alpha) * prior + alpha * sugg))
        rows.append(row)
    return rows, n_all


async def refresh_weights(client: httpx.AsyncClient):
    res = await redis_pipe(client, [["HGETALL", "st"]])
    if not res:
        return
    rows, n_all = flag_stats(to_dict(res[0]))
    new = dict(RISK_WEIGHTS)
    changed = False
    for r in rows:
        if r["suggested_weight"] is not None:
            new[r["flag"]] = r["blended_weight"]
            changed = True
    ACTIVE_WEIGHTS.clear()
    ACTIVE_WEIGHTS.update(new)
    _tune.update(on=changed, n=n_all)


async def stats_report(client: httpx.AsyncClient) -> dict:
    if not PRED_ENABLED:
        return {"enabled": False, "detail": "Track record store is not configured on this server."}
    if _stats_cache["data"] and time.time() - _stats_cache["ts"] < STATS_TTL:
        return _stats_cache["data"]
    res = await redis_pipe(client, [["HGETALL", "st"], ["ZCARD", "due"], ["LRANGE", "recent", 0, 19]])
    if res is None:
        return {"enabled": True, "available": False, "detail": "Stats store unavailable, retry shortly"}
    st = to_dict(res[0])

    def grp(prefix: str) -> dict:
        n, bad = int(st.get(f"{prefix}:n", 0)), int(st.get(f"{prefix}:bad", 0))
        ret = float(st.get(f"{prefix}:ret", 0))
        xn, xr = int(st.get(f"{prefix}:xn", 0)), float(st.get(f"{prefix}:xret", 0))
        return {"n": n, "bad_outcome_rate_pct": rate(n, bad),
                "avg_return_pct": round(ret / n, 1) if n >= MIN_N_SHOW else None,
                "avg_excess_return_pct": round(xr / xn, 1) if xn >= MIN_N_SHOW else None}

    rows, n_all = flag_stats(st)
    by_verdict = {v: {c: grp(f"v:{v}:{c}") for c in ("full", "market_only")} for v in ("ok", "caution", "avoid")}
    ok_r, av_r = by_verdict["ok"]["full"]["bad_outcome_rate_pct"], by_verdict["avoid"]["full"]["bad_outcome_rate_pct"]
    recent = []
    for x in res[2] or []:
        try:
            recent.append(json.loads(x))
        except Exception:
            pass
    hours = round(PRED_HORIZON_S / 3600, 1)
    cohorts = []
    for k in sorted((k for k in st if k.startswith("d:") and k.endswith(":n")), reverse=True)[:14]:
        day, n = k[2:-2], int(st[k])
        cohorts.append({"day": day, "n": n,
                        "bad_outcome_rate_pct": round(int(st.get(f"d:{day}:bad", 0)) / n * 100, 1) if n >= MIN_DAY_N else None})
    data = {
        "enabled": True, "available": True, "horizon_hours": hours, "resolved": n_all, "pending": int(res[1] or 0),
        "bad_outcome_definition": f"price down {abs(BAD_RETURN_PCT):.0f}%+ or liquidity down {round((1 - BAD_LIQ_RATIO) * 100)}%+ "
                                  f"or pair gone, measured about {hours}h after the verdict",
        "by_verdict": by_verdict,
        "avoid_vs_ok_gap_pts": round(av_r - ok_r, 1) if ok_r is not None and av_r is not None else None,
        "by_risk_bucket": [{"risk_score_range": f"{b * 10}-{b * 10 + 9}", **grp(f"rb:{b}")} for b in range(10)],
        "flags": rows,
        "market_adjustment": {
            "method": "flag lift = observed bad outcomes / bad outcomes expected from each day's overall rate; "
                      "avg_excess_return_pct = token return minus SOL return over the same window",
            "active": any(r["market_adjusted"] for r in rows),
            "days_with_cohort_data": len([c for c in cohorts if c["bad_outcome_rate_pct"] is not None]),
        },
        "daily_background": cohorts,
        "scoring": "tuned" if _tune["on"] else "static",
        "recent_outcomes": recent,
        "caveats": [
            "Only tokens that people query are measured: this is not a random sample of all tokens.",
            "Bad outcomes can come from ordinary market moves, not only rugs. Flags overlap, so per-flag numbers are not causal.",
            f"Rates are hidden until there are at least {MIN_N_SHOW} samples.",
            "Flag lifts are corrected for each day's overall bad-outcome rate once 2+ days of data exist; before that they are raw.",
            "Heuristic risk scoring, not financial advice.",
        ],
    }
    _stats_cache.update(ts=time.time(), data=data)
    return data


async def resolver_loop(app: FastAPI):
    last_tune = 0.0
    await asyncio.sleep(20)
    while True:
        busy = False
        try:
            busy = (await resolve_due(app.state.client)) >= RESOLVE_BATCH
            if AUTO_TUNE and time.time() - last_tune > 1800:
                await refresh_weights(app.state.client)
                last_tune = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(5 if busy else 300)


# ---------- exit check (can you sell, and at what cost?) ----------
JUP_URL = "https://api.jup.ag/swap/v1/quote"
JUP_KEY = os.getenv("JUPITER_API_KEY")  # optional free key from portal.jup.ag (higher rate limit)
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUP_LIMIT = 50 if JUP_KEY else 25  # quotes per minute, shared by everyone (keyless tier is ~0.5 req/s)
EXIT_CACHE_TTL = 60
MAX_EXIT_USD = 50_000
DEFAULT_EXIT_SIZES = [100.0, 1000.0]
VERDICT_RANK = {"ok": 0, "caution": 1, "avoid": 2}
EXIT_NOTE = (
    "Based on a Jupiter quote vs the DexScreener market price, so the loss includes fees, price impact and "
    "price differences. A quote does NOT prove a real sell will succeed: it cannot detect every sell-blocking "
    "mechanism. Treat it as one signal, not a guarantee."
)


def clean_sizes(vals) -> List[float]:
    if not vals:
        return list(DEFAULT_EXIT_SIZES)
    out: List[float] = []
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="sizes must be numbers in USD, e.g. 100,1000")
        if not (1 <= f <= MAX_EXIT_USD):
            raise HTTPException(status_code=400, detail=f"each size must be between 1 and {MAX_EXIT_USD} USD")
        out.append(f)
    if len(out) > 3:
        raise HTTPException(status_code=400, detail="Use at most 3 sizes")
    return out


def parse_sizes(text: Optional[str]) -> List[float]:
    return clean_sizes([x for x in (text or "").split(",") if x.strip()])


async def _jup_quote_raw(client: httpx.AsyncClient, mint: str, raw_amount: int) -> dict:
    """status: ok | no_route | unavailable. Never raises."""
    headers = {"x-api-key": JUP_KEY} if JUP_KEY else {}
    params = {"inputMint": mint, "outputMint": USDC_MINT, "amount": str(raw_amount),
              "slippageBps": "100", "restrictIntermediateTokens": "true"}
    try:
        resp = await client.get(JUP_URL, params=params, headers=headers)
    except httpx.HTTPError:
        return {"status": "unavailable"}
    if resp.status_code == 200:
        try:
            j = resp.json()
            out = int(j["outAmount"])
        except (ValueError, KeyError, TypeError):
            return {"status": "unavailable"}
        if out <= 0:
            return {"status": "no_route"}
        labels = []
        for step in j.get("routePlan") or []:
            lab = (step.get("swapInfo") or {}).get("label")
            if lab and lab not in labels:
                labels.append(lab)
        return {"status": "ok", "out_usd": out / 1e6, "impact_raw": j.get("priceImpactPct"), "routes": labels[:3]}
    if resp.status_code in (400, 404):
        txt = resp.text.lower()
        if "route" in txt or "tradable" in txt or "tradeable" in txt:
            return {"status": "no_route"}
    return {"status": "unavailable", "http_status": resp.status_code}


async def jup_quote(client: httpx.AsyncClient, mint: str, raw_amount: int) -> dict:
    t0 = time.time()
    q = await _jup_quote_raw(client, mint, raw_amount)
    src_log("jupiter", q["status"] != "unavailable", t0)
    return q


async def exit_check(client: httpx.AsyncClient, mint: str, sizes: List[float], sig: dict) -> dict:
    key = f"exit:{mint}:{','.join(str(x) for x in sizes)}"
    cached = cache_get(key, EXIT_CACHE_TTL)
    if cached:
        return cached
    price = float(sig.get("price_usd") or 0)
    dec = (sig.get("security") or {}).get("decimals")
    if dec is None:
        raw = await soft(get_security(client, mint), SECONDARY_TIMEOUT)
        dec = (raw or {}).get("decimals")
    if price <= 0 or dec is None:
        return {"available": False, "reason": "price or token decimals unavailable, retry shortly", "note": EXIT_NOTE}

    levels = []
    for usd in sizes:
        if rate_limited("__jup__", JUP_LIMIT):
            levels.append({"size_usd": usd, "status": "rate_limited"})
            continue
        q = await jup_quote(client, mint, int(usd / price * 10 ** int(dec)))
        lvl: Dict[str, Any] = {"size_usd": usd, "status": q["status"]}
        if q["status"] == "ok":
            loss = max(0.0, (1 - q["out_usd"] / usd) * 100)
            lvl.update(sell_receives_usd=round(q["out_usd"], 2), loss_vs_market_pct=round(loss, 2),
                       jupiter_price_impact_raw=q.get("impact_raw"), routes=q.get("routes"))
        levels.append(lvl)

    ok = [l for l in levels if l["status"] == "ok"]
    no_route = any(l["status"] == "no_route" for l in levels)
    if not ok and not no_route:
        return {"available": False, "levels": levels, "note": EXIT_NOTE,
                "reason": "Jupiter quote unavailable or rate limited, retry in a minute"}

    reasons: List[str] = []
    if no_route:
        grade, verdict = "no_route", "avoid"
        reasons.append("No sell route found on Jupiter")
    else:
        worst = max(l["loss_vs_market_pct"] for l in ok)
        grade = "good" if worst < 3 else "fair" if worst < 10 else "poor" if worst < 25 else "very_poor"
        verdict = {"good": "ok", "fair": "ok", "poor": "caution", "very_poor": "avoid"}[grade]
        if grade in ("poor", "very_poor"):
            reasons.append(f"Selling would lose about {worst:.0f}% vs market price (fees + price impact)")
    result = {"available": True, "exit_route_found": not no_route, "grade": grade, "exit_verdict": verdict,
              "levels": levels, "reasons": reasons, "note": EXIT_NOTE}
    cache_set(key, result)
    return result


async def build_exit_report(client: httpx.AsyncClient, mint: str, sizes: List[float]) -> dict:
    sig, _ = await build_signal(client, mint, True)
    ex = await exit_check(client, mint, sizes, sig)
    base = sig["verdict"]
    combined = base
    if ex.get("available") and VERDICT_RANK[ex["exit_verdict"]] > VERDICT_RANK[base]:
        combined = ex["exit_verdict"]
    reasons = list(sig.get("verdict_reasons") or []) + list(ex.get("reasons") or [])
    if ex.get("available"):
        ok = [l for l in ex["levels"] if l["status"] == "ok"]
        big = max(ok, key=lambda l: l["size_usd"]) if ok else None
        sell = (f"Selling ${big['size_usd']:,.0f} returns about ${big['sell_receives_usd']:,.2f} "
                f"({big['loss_vs_market_pct']}% below market)." if big else "No sell route found.")
    else:
        sell = "Exit check unavailable right now."
    mkt_reasons = "; ".join((sig.get("verdict_reasons") or [])[:2])
    mkt = f" ({mkt_reasons})" if mkt_reasons else ""
    return {
        "mint": mint, "token": sig.get("token"), "combined_verdict": combined, "market_verdict": base,
        "verdict_confidence": sig.get("verdict_confidence"), "reasons": reasons, "exit": ex,
        "summary": f"{sig.get('token') or '?'}: {combined.upper()}. {sell} Market verdict: {base}{mkt}.",
        "timestamp": int(time.time()),
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
<div class="item">Mint/Freeze authority + Holder concentration</div>
<div class="item">LP locked/burned % (via RugCheck)</div>
<div class="item">Exit check: sell cost per size (via Jupiter)</div>
<div class="item">Verdict + plain-English summary</div>
</div>
<h2>Endpoint for Agents</h2>
<div class="card" style="padding:16px">
<pre>GET /signal?mint=TOKEN_MINT_ADDRESS
GET /exit?mint=TOKEN_MINT_ADDRESS&amp;sizes=100,1000</pre>
<p class="muted" style="margin-top:10px">Example: <a href="/signal?mint=So11111111111111111111111111111111111111112">/signal?mint=So111...112</a></p>
<p class="muted" style="margin-top:6px">Docs: <a href="/docs">/docs</a> &bull; <a href="/llms.txt">/llms.txt</a> &bull; <a href="/stats">/stats</a> &bull; Limit: 30 req/min per IP</p>
<p class="muted" style="margin-top:6px">MCP for Claude &amp; agents: add this site's URL + <b>/mcp</b> as a remote MCP server</p>
</div>
<footer>TVibex402 &bull; Built for AI Agents<br>Data from DexScreener. Not financial advice. Risk score is a heuristic (market + on-chain), not a security audit. LP lock % is sourced from RugCheck.</footer>
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
if(f.includes('selling')||f.includes('dump')||f.includes('low')||f.includes('active')||f.includes('not_locked')||f.includes('concentration')||f.includes('dominant')||f.includes('risky'))cls='red';
if(f.includes('new')||f.includes('spike')||f.includes('partially'))cls='yellow';
return '<span class="badge '+cls+'">'+esc(f)+'</span>';
}).join('');
const s=data.security||{};
const yn=v=>v===true?'Revoked':v===false?'ACTIVE':'?';
const secTxt=esc(yn(s.mint_authority_revoked))+' / '+esc(yn(s.freeze_authority_revoked));
const top10=s.top10_holders_pct!=null?esc(s.top10_holders_pct)+'%':'-';
const vcol=data.verdict==='avoid'?'var(--danger)':data.verdict==='caution'?'var(--warn)':'var(--a)';
const lp=s.lp_locked_pct!=null?esc(s.lp_locked_pct)+'%':'-';
const top1=s.top1_holder_pct!=null?esc(s.top1_holder_pct)+'%':'-';
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
<div class="stat"><div class="label">Verdict</div><div class="value" style="color:${vcol}">${esc((data.verdict||'-').toUpperCase())}</div></div>
<div class="stat"><div class="label">Risk Score</div><div class="value" style="color:${riskColor}">${esc(data.risk_score)} (${esc(data.risk_level)})</div></div>
<div class="stat"><div class="label">Mint / Freeze authority</div><div class="value" style="font-size:.95rem">${secTxt}</div></div>
<div class="stat"><div class="label">Top 10 Holders</div><div class="value">${top10}</div></div>
<div class="stat"><div class="label">Top Holder</div><div class="value">${top1}</div></div>
<div class="stat"><div class="label">LP Locked/Burned</div><div class="value">${lp}</div></div>
</div>
<div style="margin:12px 0 6px;font-size:.85rem;color:var(--muted)">Flags</div>
<div>${flags||'<span class="muted">None</span>'}</div>
<div class="muted" style="margin-top:8px">${esc((data.verdict_reasons||[]).join(' • '))}</div>
${data.partial?'<p class="muted" style="margin-top:8px">Some on-chain data was slow, so it is missing here.</p>':''}${data.stale?'<p class="muted" style="margin-top:8px">Showing cached data (source temporarily down).</p>':''}</div>`;
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
buy pressure, volume spike ratio, pair age, flags, risk_score (0-100 heuristic), risk_level, and a `security` object:
mint_authority_revoked, freeze_authority_revoked, token_program, risky_extensions, top1_holder_pct,
top10_holders_pct (excluding liquidity pools/burn), liquidity_pool_pct, top_holders.
Add &security=false to skip on-chain checks (faster).
verdict: "ok" | "caution" | "avoid" plus verdict_reasons (heuristic, not advice). verdict_confidence is "full" or "market_only".
Established tokens (liquidity >= $1M, pair > 30 days) are not marked "avoid" only for active authorities (e.g. stablecoins).
partial=true: a slow on-chain source timed out, market data is still valid. stale=true: upstream was down, a cached copy (stale_age_s old) is returned.
lp_locked_pct, rugcheck_score and rugcheck_risks come from RugCheck's public API (LP locked or burned %).
risk_score is a heuristic, not a guarantee.

## Batch
GET /signals?mints=A,B,C  (max 10) -> {count, results:[...], errors:[...]}. Each mint counts toward the rate limit.

## Track record
GET /stats -> how often verdicts were right: every verdict is judged ~24h later (bad outcome = price -50% or liquidity -70%
or pair gone). Shows rates per verdict, per risk bucket and per flag, recent outcomes and caveats. Rates are null until n >= 20. Flag lifts are adjusted for each day's market background (daily_background),
and avg_excess_return_pct is relative to SOL over the same window.

## Exit check
GET /exit?mint=<MINT>&sizes=100,1000  (1-3 sizes in USD, default 100,1000)
Returns combined_verdict (worst of market verdict and exit verdict), exit.levels[] with loss_vs_market_pct,
exit.grade (good|fair|poor|very_poor|no_route) and a summary. A quote is not proof a sell will succeed.

## Reference
verdict: ok | caution | avoid. Every signal also has `summary` (one sentence) and `suggested_next`.
Risk flags: mint_authority_active, freeze_authority_active, risky_token_extension, lp_not_locked, lp_partially_locked,
dump_risk, extreme_holder_concentration, high_holder_concentration, dominant_holder, very_low_liquidity, low_liquidity,
brand_new_pair, very_new_pair, new_pair, extreme_turnover, high_turnover, heavy_selling, extreme_volume_spike.
Info flags: volume_spike, heavy_buying, strong_momentum, high_activity.
`sources` shows which data sources answered (ok|missing). `scoring` is static, or tuned if the server learned weights from outcomes.

## MCP
Remote MCP server (Streamable HTTP, stateless, no auth): POST /mcp
Tools: get_token_signal (full data), check_token_risk (compact verdict), check_tokens_batch (max 10, compact),
check_exit (can you sell? price impact per size via Jupiter),
get_track_record (measured accuracy of past verdicts).

## Other
GET /health  - service status
GET /docs    - OpenAPI docs
Rate limit: 30 requests/minute per IP (HTTP 429 with Retry-After).
Cache: results cached up to 30 seconds (X-Cache: HIT/MISS/STALE).
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


def too_many(wait: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=f"Rate limit exceeded. Retry in {wait}s",
        headers={"Retry-After": str(wait)},
    )


@app.get("/signal")
async def signal(
    request: Request,
    mint: str = Query(..., min_length=1, max_length=64),
    security: bool = Query(True, description="Include on-chain checks: authorities, holders, LP lock"),
):
    mint = mint.strip()
    if not MINT_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid Solana token address")
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    result, status = await build_signal(request.app.state.client, mint, security)
    return JSONResponse(content=result, headers={"X-Cache": status, "Cache-Control": "public, max-age=10"})


@app.get("/signals")
async def signals(
    request: Request,
    mints: str = Query(..., max_length=600, description=f"Comma-separated mints, max {MAX_BATCH}"),
    security: bool = Query(True),
):
    ids: list = []
    for m in mints.split(","):
        m = m.strip()
        if m and m not in ids:
            ids.append(m)
    if not ids:
        raise HTTPException(status_code=400, detail="Provide at least one mint")
    if len(ids) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"Max {MAX_BATCH} mints per request")
    wait = rate_limited(client_ip(request), cost=len(ids))  # each mint counts toward the limit
    if wait:
        raise too_many(wait)

    client = request.app.state.client
    sem = asyncio.Semaphore(3)

    async def one(m: str):
        if not MINT_RE.match(m):
            return {"mint": m, "status": 400, "detail": "Invalid Solana token address"}, None
        async with sem:
            try:
                res, _ = await build_signal(client, m, security)
                return None, res
            except HTTPException as e:
                return {"mint": m, "status": e.status_code, "detail": e.detail}, None

    out = await asyncio.gather(*(one(m) for m in ids))
    results = [r for _, r in out if r]
    errors = [e for e, _ in out if e]
    return JSONResponse(
        content={"count": len(results), "results": results, "errors": errors},
        headers={"Cache-Control": "public, max-age=10"},
    )


@app.get("/exit")
async def exit_route(
    request: Request,
    mint: str = Query(..., min_length=1, max_length=64),
    sizes: Optional[str] = Query(None, description="Sell sizes in USD, comma separated, max 3. Default 100,1000"),
):
    mint = mint.strip()
    if not MINT_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid Solana token address")
    size_list = parse_sizes(sizes)
    wait = rate_limited(client_ip(request), cost=2)
    if wait:
        raise too_many(wait)
    report = await build_exit_report(request.app.state.client, mint, size_list)
    return JSONResponse(content=report, headers={"Cache-Control": "public, max-age=10"})


@app.get("/stats")
async def stats_route(request: Request):
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    report = await stats_report(request.app.state.client)
    return JSONResponse(content=report, headers={"Cache-Control": "public, max-age=60"})


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION, "cache_size": len(_cache), "rpc": "custom" if os.getenv("SOLANA_RPC_URL") else "public", "mcp": mcp_server is not None, "jupiter_key": bool(JUP_KEY),
            "track_record": PRED_ENABLED, "auto_tune": AUTO_TUNE, "sources": src_status()}


# ---------- MCP server (remote, Streamable HTTP at /mcp) ----------
# Lets Claude and other MCP-capable agents call the API as tools.
# IMPORTANT: this block must stay LAST, because it mounts at "/" and would
# otherwise swallow routes defined after it.
try:
    from mcp.server.fastmcp import Context, FastMCP
    _HAS_MCP = True
except ImportError:  # `mcp` not installed: the REST API still works
    _HAS_MCP = False

if _HAS_MCP:
    _mcp_kwargs: Dict[str, Any] = dict(
        stateless_http=True,   # no sessions: works with restarts / free-tier sleep
        json_response=True,
        instructions=(
            "Solana token data and risk checks. Use check_token_risk for a quick verdict, "
            "get_token_signal for full data. Heuristics only, not financial advice."
        ),
    )
    try:
        # FastMCP enables localhost-only Host checks by default, which would reject
        # requests to *.onrender.com. This server is public and has no cookies/auth.
        from mcp.server.transport_security import TransportSecuritySettings
        _mcp_kwargs["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    except ImportError:
        pass
    mcp_server = FastMCP("TVibex402", **_mcp_kwargs)

    def _compact(r: dict) -> dict:
        keys = ("token", "name", "mint", "price_usd", "liquidity_usd", "pair_age_minutes", "verdict",
                "verdict_confidence", "verdict_reasons", "risk_score", "risk_level", "flags", "partial", "stale")
        out = {k: r.get(k) for k in keys if k in r}
        sec = r.get("security") or {}
        for k in ("mint_authority_revoked", "freeze_authority_revoked", "top10_holders_pct", "lp_locked_pct"):
            if k in sec:
                out[k] = sec[k]
        return out

    def _mcp_key(ctx) -> str:
        try:
            return client_ip(ctx.request_context.request)
        except Exception:
            return "mcp-unknown"

    async def _mcp_signal(mint: str, security: bool) -> dict:
        mint = (mint or "").strip()
        if not MINT_RE.match(mint):
            raise ValueError("Invalid Solana token mint address")
        try:
            res, _ = await build_signal(app.state.client, mint, security)
        except HTTPException as e:
            raise ValueError(f"{e.detail} (HTTP {e.status_code})")
        return res

    @mcp_server.tool()
    async def get_token_signal(mint: str, ctx: Context, include_security: bool = True) -> dict:
        """Full market + on-chain data for ONE Solana token: price, liquidity, volume, buy pressure,
        flags, risk score, verdict (ok/caution/avoid), mint/freeze authority, holder concentration, LP lock.
        mint: the token's Solana mint address (base58). Heuristic data, not financial advice."""
        wait = rate_limited(_mcp_key(ctx))
        if wait:
            raise ValueError(f"Rate limit exceeded. Retry in {wait}s")
        return await _mcp_signal(mint, include_security)

    @mcp_server.tool()
    async def check_token_risk(mint: str, ctx: Context, include_security: bool = True) -> dict:
        """Quick risk check for ONE Solana token. Returns a compact summary: verdict (ok/caution/avoid)
        with reasons, risk score, price, liquidity, authorities, top-10 holder % and LP lock %.
        Prefer this over get_token_signal when you only need a decision."""
        wait = rate_limited(_mcp_key(ctx))
        if wait:
            raise ValueError(f"Rate limit exceeded. Retry in {wait}s")
        return _compact(await _mcp_signal(mint, include_security))

    @mcp_server.tool()
    async def check_tokens_batch(mints: list[str], ctx: Context, include_security: bool = True) -> dict:
        """Quick risk check for up to 10 Solana tokens at once (compact summaries, same fields as
        check_token_risk). Tokens that fail return an `error` entry instead of failing the whole call."""
        ids = list(dict.fromkeys(m.strip() for m in mints if m and m.strip()))
        if not ids:
            raise ValueError("Provide at least one mint")
        if len(ids) > MAX_BATCH:
            raise ValueError(f"Max {MAX_BATCH} mints per call")
        wait = rate_limited(_mcp_key(ctx), cost=len(ids))
        if wait:
            raise ValueError(f"Rate limit exceeded. Retry in {wait}s")
        sem = asyncio.Semaphore(3)

        async def one(m: str) -> dict:
            async with sem:
                try:
                    return _compact(await _mcp_signal(m, include_security))
                except ValueError as e:
                    return {"mint": m, "error": str(e)}

        results = await asyncio.gather(*(one(m) for m in ids))
        return {"count": len(results), "results": results}

    @mcp_server.tool()
    async def check_exit(mint: str, ctx: Context, sizes_usd: Optional[List[float]] = None) -> dict:
        """Can you SELL this Solana token, and at what cost? Quotes selling each size (default $100 and $1000)
        through Jupiter and compares with the market price. Returns combined_verdict (market + exit),
        loss_vs_market_pct per size and a plain-English summary. Use after check_token_risk, before sizing a trade.
        A quote is not proof a real sell will succeed."""
        wait = rate_limited(_mcp_key(ctx), cost=2)
        if wait:
            raise ValueError(f"Rate limit exceeded. Retry in {wait}s")
        mint = (mint or "").strip()
        if not MINT_RE.match(mint):
            raise ValueError("Invalid Solana token mint address")
        try:
            return await build_exit_report(app.state.client, mint, clean_sizes(sizes_usd))
        except HTTPException as e:
            raise ValueError(f"{e.detail} (HTTP {e.status_code})")

    @mcp_server.tool()
    async def get_track_record() -> dict:
        """How often were this service's verdicts right? Returns measured outcomes (about 24h after each verdict)
        per verdict (ok/caution/avoid) and per risk flag, plus recent examples and caveats. Use it to decide how
        much to trust the verdicts. Rates stay null until enough samples exist."""
        return await stats_report(app.state.client)

    app.mount("/", mcp_server.streamable_http_app())
