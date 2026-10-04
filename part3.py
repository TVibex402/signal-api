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
LP_FLAGS = {"lp_not_locked", "lp_partially_locked"}


def add_verdict(result: dict):
    """One-line decision for agents: ok | caution | avoid (heuristic, not advice)."""
    flags = set(result["flags"])
    # Established tokens (liquidity >= $1M + pair age > 30 days) không bị avoid vì authority hoặc LP unlock
    established = result["liquidity_usd"] >= 1_000_000 and (result.get("pair_age_minutes") or 0) >= 43200
    avoid = [f for f in AVOID_FLAGS if f in flags and not (established and (f in AUTHORITY_FLAGS or f in LP_FLAGS))]
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
        miss = [k for k, v in (result.get("sources") or {}).items() if v != "ok"]
        nxt.append("retry in a few seconds for full on-chain data" + (f" (missing: {', '.join(miss)})" if miss else ""))
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


async def _dex_or_prefetched(client: httpx.AsyncClient, mint: str, prefetched: Optional[dict]) -> dict:
    return prefetched if prefetched is not None else await fetch_dex(client, mint)


async def fetch_dex_many(client: httpx.AsyncClient, mints: List[str]) -> Optional[Dict[str, dict]]:
    """ONE DexScreener request for up to 30 mints. Returns {mint: {"pairs": [...]}} or None if the call failed."""
    out: Dict[str, dict] = {m: {"pairs": []} for m in mints}
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=",".join(mints)))
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        src_log("dexscreener", True, t0)
    except (httpx.HTTPError, ValueError):
        src_log("dexscreener", False, t0)
        return None
    for p in pairs:
        for m in {(p.get("baseToken") or {}).get("address"), (p.get("quoteToken") or {}).get("address")}:
            if m in out:
                out[m]["pairs"].append(p)
    return out


async def _build_signal_core(client: httpx.AsyncClient, mint: str, security: bool, prefetched: Optional[dict] = None):
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
    if prefetched is None and rate_limited("__global__", GLOBAL_LIMIT):  # batches pay once, in build_batch
        return serve_stale(HTTPException(status_code=503, detail="Service busy, retry shortly", headers={"Retry-After": "5"}))

    try:
        if security:
            data, raw_sec, lp_raw = await asyncio.gather(
                _dex_or_prefetched(client, mint, prefetched),
                soft(get_security(client, mint), SECONDARY_TIMEOUT),
                soft(get_lp_lock(client, mint), SECONDARY_TIMEOUT),
            )
        else:
            data, raw_sec, lp_raw = await _dex_or_prefetched(client, mint, prefetched), None, None
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


# ---------- data quality (what is missing, how fresh, how complete) ----------
def with_quality(result: dict, status: str, security: bool) -> dict:
    """Return a COPY with a data_quality block (the cached dict is never mutated).
    data_confidence (0-1) = how complete and fresh the data is. It is NOT the probability that the
    verdict is correct: that is what /stats measures."""
    now = time.time()
    age = max(0, int(now - (result.get("timestamp") or now)))
    freshness = "stale" if status == "STALE" else "fresh" if age <= 5 else "cached"
    missing = [k for k, v in (result.get("sources") or {}).items() if v != "ok"]
    completeness = "market_only" if not security else "partial" if missing else "full"
    conf = 1.0 - sum({"solana_rpc": 0.25, "rugcheck": 0.2}.get(m, 0.15) for m in missing)
    if not security:
        conf = min(conf, 0.6)
    if status == "STALE":
        conf -= 0.2 + min(0.3, age / 600 * 0.3)
    return dict(result, data_quality={"freshness": freshness, "age_s": age, "completeness": completeness,
                                      "missing": missing, "data_confidence": max(0.05, round(conf, 2))})


async def build_signal(client: httpx.AsyncClient, mint: str, security: bool, prefetched: Optional[dict] = None):
    result, status = await _build_signal_core(client, mint, security, prefetched)
    _m["cache"][status] += 1
    if result.get("partial"):
        _m["partial"] += 1
    return with_quality(result, status, security), status


# ---------- smart batch ----------
BATCH_DEADLINE_S = 9.0
_COMPACT_KEYS = ("token", "name", "mint", "price_usd", "liquidity_usd", "pair_age_minutes", "verdict",
                 "verdict_confidence", "verdict_reasons", "risk_score", "risk_level", "flags", "summary", "safety_rank",
                 "partial", "stale", "data_quality")


def compact_signal(r: dict) -> dict:
    out = {k: r.get(k) for k in _COMPACT_KEYS if k in r}
    sec = r.get("security") or {}
    for k in ("mint_authority_revoked", "freeze_authority_revoked", "top10_holders_pct", "lp_locked_pct"):
        if k in sec:
            out[k] = sec[k]
    return out


async def build_batch(client: httpx.AsyncClient, raw_mints: List[str], security: bool, *, sort: str = "input",
                      only: Optional[str] = None, max_risk: Optional[int] = None, top: Optional[int] = None,
                      view: str = "full", charge=None) -> dict:
    """Smart batch: ONE shared DexScreener request for everything not cached, cost = only what needs fetching,
    an overall deadline (slow tokens are reported, not waited for), ranking, filters and a one-line summary."""
    ids = list(dict.fromkeys(m.strip() for m in raw_mints if m and m.strip()))
    if not ids:
        raise HTTPException(status_code=400, detail="Provide at least one mint")
    if len(ids) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"Max {MAX_BATCH} mints per request")
    if sort not in ("input", "safest", "riskiest"):
        raise HTTPException(status_code=400, detail="sort must be input, safest or riskiest")
    if view not in ("full", "compact"):
        raise HTTPException(status_code=400, detail="view must be full or compact")
    only_set = None
    if only:
        only_set = {x.strip().lower() for x in only.split(",") if x.strip()}
        if not only_set or not only_set <= {"ok", "caution", "avoid"}:
            raise HTTPException(status_code=400, detail="only must be a comma list of: ok, caution, avoid")

    errors: List[dict] = []
    valid: List[str] = []
    for m in ids:
        if MINT_RE.match(m):
            valid.append(m)
        else:
            errors.append({"mint": m, "status": 400, "detail": "Invalid Solana token address"})

    # what actually needs upstream work? (fresh cache hits are free)
    todo = []
    for m in valid:
        hit = cache_peek(m if security else m + ":nosec")
        if not (hit and hit[0] <= (PARTIAL_TTL if hit[1].get("partial") else CACHE_TTL)):
            todo.append(m)
    wait = charge(max(1, len(todo))) if charge else 0
    if wait:
        raise too_many(wait)

    prefetched: Dict[str, dict] = {}
    for i in range(0, len(todo), 30):
        if rate_limited("__global__", GLOBAL_LIMIT):
            break  # over budget: each token falls back to stale cache or a clear 503
        got = await fetch_dex_many(client, todo[i:i + 30])
        if got:
            prefetched.update(got)

    sem = asyncio.Semaphore(4)

    async def one(m: str) -> dict:
        async with sem:
            res, _ = await build_signal(client, m, security, prefetched.get(m))
            return res

    tasks: Dict[str, Any] = {}
    for m in valid:
        t = asyncio.ensure_future(one(m))
        t.add_done_callback(lambda f: f.cancelled() or f.exception())  # never log 'exception never retrieved'
        _bg.add(t)
        t.add_done_callback(_bg.discard)
        tasks[m] = t
    if tasks:
        await asyncio.wait(list(tasks.values()), timeout=BATCH_DEADLINE_S)

    results: List[dict] = []
    for m in valid:
        t = tasks[m]
        if not t.done():  # keeps running in the background and lands in the cache
            errors.append({"mint": m, "status": 504, "detail": "Still computing, retry in a few seconds", "retry_after_s": 5})
            continue
        exc = t.exception()
        if exc is None:
            results.append(t.result())
        elif isinstance(exc, HTTPException):
            e = {"mint": m, "status": exc.status_code, "detail": exc.detail}
            if exc.headers and "Retry-After" in exc.headers:
                e["retry_after_s"] = int(exc.headers["Retry-After"])
            errors.append(e)
        else:
            errors.append({"mint": m, "status": 500, "detail": "Internal error"})

    order = sorted(range(len(results)), key=lambda i: (results[i]["risk_score"], -(results[i].get("liquidity_usd") or 0)))
    for rank, i in enumerate(order, 1):
        results[i] = dict(results[i], safety_rank=rank)
    everything = list(results)

    if only_set:
        results = [r for r in results if r["verdict"] in only_set]
    if max_risk is not None:
        results = [r for r in results if r["risk_score"] <= max_risk]
    if sort == "safest":
        results.sort(key=lambda r: r["safety_rank"])
    elif sort == "riskiest":
        results.sort(key=lambda r: -r["safety_rank"])
    if top:
        results = results[:top]
    if view == "compact":
        results = [compact_signal(r) for r in results]

    cnt = Counter(r["verdict"] for r in everything)
    best = min(everything, key=lambda r: r["safety_rank"]) if everything else None
    worst = max(everything, key=lambda r: r["safety_rank"]) if everything else None
    brief = lambda r: {"mint": r["mint"], "token": r.get("token"), "risk_score": r["risk_score"], "verdict": r["verdict"]}  # noqa: E731
    headline = (f"{len(ids)} requested: {cnt['ok']} ok, {cnt['caution']} caution, {cnt['avoid']} avoid"
                + (f", {len(errors)} failed" if errors else "")
                + (f". Lowest risk: {best.get('token')} ({best['risk_score']}/100)" if best else "") + ".")
    return {
        "count": len(results),
        "summary": {"checked": len(ids), "ok": cnt["ok"], "caution": cnt["caution"], "avoid": cnt["avoid"],
                    "failed": len(errors), "headline": headline,
                    "safest": brief(best) if best else None, "riskiest": brief(worst) if worst else None,
                    "min_data_confidence": min((r["data_quality"]["data_confidence"] for r in everything), default=None)},
        "results": results, "errors": errors, "filtered_out": len(everything) - len(results),
        "sort": sort, "view": view,
    }