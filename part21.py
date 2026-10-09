# ---------- part21: v2.8.1 — Phase 2 step 2: seed traffic + accuracy growth ----------
# Loads before part7 (MCP). Slow, capped sampling so track-record fills without melting DexScreener.
VERSION = "2.8.1"
app.version = VERSION
app.openapi_schema = None

SEED_ENABLED = (os.getenv("SEED_TRAFFIC") or "true").lower() in ("1", "true", "yes")
SEED_INTERVAL_S = max(180, int(os.getenv("SEED_INTERVAL_S", "900")))  # default 15 min between seeds
SEED_MAX_PER_DAY = max(5, int(os.getenv("SEED_MAX_PER_DAY", "40")))   # hard cap
_SEED_BOOST = "https://api.dexscreener.com/token-boosts/top/v1"
_SEED_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
_seed_state = {"day": "", "count": 0, "last": 0.0, "last_mint": None, "last_ok": False, "errors": 0}


def _seed_day_ok() -> bool:
    today = time.strftime("%Y%m%d", time.gmtime())
    if _seed_state["day"] != today:
        _seed_state.update(day=today, count=0)
    return _seed_state["count"] < SEED_MAX_PER_DAY


async def _seed_candidate_mints(client: httpx.AsyncClient) -> list:
    """Solana mints from DexScreener boosts + latest profiles (best-effort, no throw)."""
    mints = []
    for url in (_SEED_BOOST, _SEED_PROFILES):
        try:
            resp = await client.get(url, timeout=6.0)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not isinstance(data, list):
                continue
            for row in data:
                if (row.get("chainId") or "").lower() != "solana":
                    continue
                addr = row.get("tokenAddress") or row.get("token_address")
                if addr and MINT_RE.match(addr) and addr not in MAJOR_ASSETS:
                    mints.append(addr)
        except Exception:
            continue
    # de-dupe, shuffle lightly by time bucket
    seen = set()
    out = []
    for m in mints:
        if m not in seen:
            seen.add(m)
            out.append(m)
    if out:
        # rotate starting point so we don't always hit the same top boosts
        off = int(time.time() // SEED_INTERVAL_S) % len(out)
        out = out[off:] + out[:off]
    return out


async def run_one_seed(client: httpx.AsyncClient) -> dict:
    """Fetch one diverse mint and run full signal path (records prediction automatically)."""
    if not SEED_ENABLED:
        return {"ok": False, "detail": "SEED_TRAFFIC disabled"}
    if not _seed_day_ok():
        return {"ok": False, "detail": f"daily seed cap {SEED_MAX_PER_DAY} reached"}
    now = time.time()
    if now - _seed_state["last"] < SEED_INTERVAL_S:
        wait = int(SEED_INTERVAL_S - (now - _seed_state["last"]))
        return {"ok": False, "detail": "too soon", "retry_after_s": wait}
    try:
        if float(_DEX_COOLDOWN.get("until") or 0) > now:
            return {"ok": False, "detail": "dex cooling, skip seed"}
    except Exception:
        pass

    mints = await _seed_candidate_mints(client)
    if not mints:
        _seed_state["errors"] += 1
        return {"ok": False, "detail": "no solana candidates from boosts/profiles"}

    # pick first not recently locked by prediction lock (best effort: try up to 5)
    last_err = None
    for mint in mints[:5]:
        try:
            result, status = await build_signal(client, mint, True)
            _seed_state.update(last=time.time(), last_mint=mint, last_ok=True, count=_seed_state["count"] + 1)
            return {
                "ok": True,
                "mint": mint,
                "token": result.get("token"),
                "verdict": result.get("verdict"),
                "risk_score": result.get("risk_score"),
                "cache": status,
                "seeded_today": _seed_state["count"],
            }
        except HTTPException as e:
            last_err = getattr(e, "detail", str(e))
            if getattr(e, "status_code", None) == 503:
                break  # don't hammer further
            continue
        except Exception as e:
            last_err = str(e)[:120]
            continue
    _seed_state["errors"] += 1
    _seed_state["last"] = time.time()
    return {"ok": False, "detail": last_err or "all candidates failed"}


@app.get("/seed", tags=["ops"], summary="Slow sample of live tokens to grow accuracy data",
         include_in_schema=False)
async def seed_route(request: Request):
    """Call from cron every 15–20 min. Capped, skips when Dex is cooling. No API key needed."""
    # cheap IP rate limit so random scanners cannot spray
    wait = rate_limited("seed:" + client_ip(request), limit=6)
    if wait:
        raise HTTPException(status_code=429, detail="seed rate limited", headers={"Retry-After": str(wait)})
    client = request.app.state.client
    report = await run_one_seed(client)
    code = 200 if report.get("ok") else 503 if "cooling" in str(report.get("detail")) else 200
    return JSONResponse(content=report, status_code=code,
                        headers={"Cache-Control": "no-store"})


# Auto-seed on /ping occasionally (every 2nd ping after interval), so one cron covers both
_drop_route("/ping")
_ping_prev = ping
_ping_count = {"n": 0}


@app.get("/ping", include_in_schema=False)
async def ping():
    body = await _ping_prev() if asyncio.iscoroutinefunction(_ping_prev) else _ping_prev()
    if not isinstance(body, dict):
        return body
    _ping_count["n"] += 1
    body["seed"] = {
        "enabled": SEED_ENABLED,
        "seeded_today": _seed_state["count"],
        "max_per_day": SEED_MAX_PER_DAY,
        "interval_s": SEED_INTERVAL_S,
        "last_mint": _seed_state.get("last_mint"),
    }
    # every other ping, try a seed if due (fire-and-forget)
    if SEED_ENABLED and _ping_count["n"] % 2 == 0 and _seed_day_ok():
        if time.time() - _seed_state["last"] >= SEED_INTERVAL_S:
            client = getattr(app.state, "client", None)
            if client is not None:
                async def _bg():
                    try:
                        await run_one_seed(client)
                    except Exception:
                        pass
                t = asyncio.create_task(_bg())
                _seed_bg = globals().setdefault("_seed_bg", set())
                _seed_bg.add(t)
                t.add_done_callback(_seed_bg.discard)
    return body


# health: expose seed counters
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
        "seed": {
            "enabled": SEED_ENABLED,
            "seeded_today": _seed_state["count"],
            "max_per_day": SEED_MAX_PER_DAY,
            "interval_s": SEED_INTERVAL_S,
            "last_mint": _seed_state.get("last_mint"),
            "errors": _seed_state["errors"],
            "hint": "GET /seed or rely on /ping auto-seed; aim for ok/avoid n>=20 on /stats",
        },
    }


try:
    if "GET /seed" not in LLMS_TXT:
        LLMS_TXT = LLMS_TXT + """
## Accuracy growth
GET /seed — slow sample of live Solana tokens (capped) so measured accuracy fills in.
Also runs occasionally when /ping is hit. Targets: enough judged samples for ok/caution/avoid rates.
"""
except NameError:
    pass

print(f"[part21] v{VERSION} seed traffic ready (interval={SEED_INTERVAL_S}s, max/day={SEED_MAX_PER_DAY})")
