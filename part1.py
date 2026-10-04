import asyncio
import json
import math
import os
import re
import time
import threading
from collections import Counter, OrderedDict, deque
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

VERSION = "2.0.0"
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


# ---------- operations: metrics + response headers ----------
_m: Dict[str, Any] = {"started": time.time(), "routes": Counter(), "status": Counter(),
                      "lat": {}, "cache": Counter(), "partial": 0}
_KNOWN_ROUTES = {"/signal", "/signals", "/exit", "/stats", "/health", "/metrics", "/mcp", "/llms.txt", "/docs", "/openapi.json"}
_TIMED_ROUTES = {"/signal", "/signals", "/exit", "/mcp"}


def route_label(path: str) -> str:
    if path in ("", "/"):
        return "/"
    return path.rstrip("/") if path.rstrip("/") in _KNOWN_ROUTES else "other"


def pctl(vals, p: float) -> Optional[float]:
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(len(v) * p))], 1) if v else None


class MetricsMiddleware:
    """Pure ASGI (safe with the mounted MCP app): adds X-API-Version / X-Response-Time and counts requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0 = time.time()
        label = route_label(scope.get("path", ""))
        box = {"code": 500}

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                box["code"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-api-version", VERSION.encode()))
                headers.append((b"x-response-time", f"{(time.time() - t0) * 1000:.0f}ms".encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrap)
        finally:
            ms = (time.time() - t0) * 1000
            _m["routes"][label] += 1
            _m["status"][str(box["code"])] += 1
            if label in _TIMED_ROUTES:
                _m["lat"].setdefault(label, deque(maxlen=300)).append(ms)


def metrics_report() -> dict:
    c = _m["cache"]
    total = sum(c.values())
    pct = lambda x: round(x / total * 100, 1) if total else None  # noqa: E731
    return {
        "version": VERSION,
        "uptime_s": int(time.time() - _m["started"]),
        "note": "Counters live in memory and reset when the server restarts or sleeps.",
        "requests": sum(_m["routes"].values()),
        "by_route": dict(_m["routes"]),
        "by_status": dict(_m["status"]),
        "latency_ms": {r: {"n": len(d), "p50": pctl(d, 0.5), "p95": pctl(d, 0.95)} for r, d in _m["lat"].items() if d},
        "signals": {"total": total, "cache_hit_rate_pct": pct(c["HIT"]), "stale_rate_pct": pct(c["STALE"]),
                    "partial_rate_pct": pct(_m["partial"])},
        "sources": src_status(),
    }


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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Cache", "X-Response-Time", "X-API-Version", "Retry-After"],
)
app.add_middleware(MetricsMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    """Same {"detail": ...} shape as before. For 502/503 we add source health + a retry hint, so an agent
    can tell 'wait a few seconds' from 'give up' when one or more data sources are down."""
    body: Dict[str, Any] = {"detail": exc.detail}
    if exc.status_code in (502, 503):
        retry = int((exc.headers or {}).get("Retry-After", 10))
        body.update(retry_after_s=retry, sources=src_status())
    return JSONResponse(content=body, status_code=exc.status_code, headers=exc.headers)

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
