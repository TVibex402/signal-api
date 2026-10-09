# ---------- part20: v2.8.0 — Phase 2 step 1: anti-429 + anti-sleep ----------
# Loads before part7 (MCP). Same shared namespace as all other parts.
VERSION = "2.8.0"
app.version = VERSION
app.openapi_schema = None

# Longer tolerance when upstream is angry
STALE_MAX = max(STALE_MAX, 1800)        # 30 minutes
PARTIAL_TTL = max(PARTIAL_TTL, 20)
BASE_CACHE_TTL = max(BASE_CACHE_TTL, 30)

_homeostat_prev = homeostat


def homeostat() -> int:
    """Stretch cache harder while DexScreener is in 429 cooldown."""
    global CACHE_TTL, STALE_MAX
    ttl = _homeostat_prev()
    cooling = False
    try:
        cooling = float(_DEX_COOLDOWN.get("until") or 0) > time.time()
    except Exception:
        pass
    if cooling:
        CACHE_TTL = max(CACHE_TTL, BASE_CACHE_TTL * 8)
        STALE_MAX = max(STALE_MAX, 1800)
    return CACHE_TTL


# Last-resort: if build would 503 and we still have any cache entry, serve it
_build_core_prev = _build_signal_core


async def _build_signal_core(client, mint: str, security: bool, prefetched=None):
    homeostat()
    cache_key = mint if security else mint + ":nosec"
    try:
        return await _build_core_prev(client, mint, security, prefetched)
    except HTTPException as exc:
        if getattr(exc, "status_code", None) != 503:
            raise
        hit = cache_peek(cache_key)
        if not hit:
            raise
        age, cached = hit
        out = dict(cached)
        out["stale"] = True
        out["stale_age_s"] = int(age)
        out["source_note"] = "served beyond normal stale window because market source is rate-limited"
        return out, "STALE"


@app.get("/ping", include_in_schema=False)
async def ping():
    """No upstream calls. Point a free cron (cron-job.org / UptimeRobot) here every 10–14 min."""
    cooling = False
    try:
        cooling = float(_DEX_COOLDOWN.get("until") or 0) > time.time()
    except Exception:
        pass
    return {
        "ok": True,
        "version": VERSION,
        "ts": int(time.time()),
        "dex_cooling": cooling,
        "cache_size": len(_cache),
    }


# Replace /health so operators see stability fields
_drop_route("/health")


@app.get("/health", tags=["ops"], summary="Service status and what is switched on")
async def health():
    cooling = False
    try:
        cooling = float(_DEX_COOLDOWN.get("until") or 0) > time.time()
    except Exception:
        pass
    return {
        "status": "ok",
        "version": VERSION,
        "uptime_s": int(time.time() - _m["started"]),
        "python": platform.python_version(),
        "cache_size": len(_cache),
        "rpc": "custom" if os.getenv("SOLANA_RPC_URL") else "public",
        "mcp": mcp_server is not None,
        "mcp_error": mcp_error,
        "jupiter_key": bool(JUP_KEY),
        "track_record": PRED_ENABLED,
        "track_record_store": await redis_status(getattr(app.state, "client", None)),
        "auto_tune": AUTO_TUNE,
        "api_keys_configured": len(API_KEYS),
        "streams": {"watching": len(_watchers), "open": sum(_stream_open.values())},
        "sources": src_status(),
        "stability": {
            "stale_max_s": STALE_MAX,
            "cache_ttl_s": CACHE_TTL,
            "dex_cooling": cooling,
            "keep_alive": "GET /ping every 10-14 min (cron-job.org or UptimeRobot)",
        },
    }


# Agent docs
try:
    if "GET /ping" not in LLMS_TXT:
        LLMS_TXT = LLMS_TXT + """
## Keep-alive
GET /ping — no upstream calls. Hit every 10–14 min from a free cron to reduce free-tier host sleep.
"""
except NameError:
    pass

print(f"[part20] v{VERSION} anti-429/anti-sleep ready (STALE_MAX={STALE_MAX}s)")
