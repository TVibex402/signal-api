# ---------- v2.3: Token-2022 details, smarter batch filters ----------
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).
VERSION = "2.3.0"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version

# --- Token-2022: read what each extension actually does instead of flagging every extension ---
T22_FEE_HIGH_BPS = 500       # 5% on every transfer
T22_FEE_EXTREME_BPS = 2500   # 25%: a sell tax
_ZERO_KEY = "11111111111111111111111111111111"
REASONS.update({
    "permanent_delegate_active": "A permanent delegate can move or burn tokens from any holder",
    "transfer_fee_extreme": "Transfer fee of 25% or more on every transfer (a sell tax)",
    "default_account_frozen": "New token accounts start frozen: holders cannot move tokens until the issuer thaws them",
    "transfer_fee_high": "Transfer fee of 5% or more on every transfer",
    "transfer_hook_active": "A transfer hook program runs on every transfer and can block it",
})
for _flag, _weight in (("permanent_delegate_active", 40), ("transfer_fee_extreme", 40), ("default_account_frozen", 40),
                       ("transfer_fee_high", 20), ("transfer_hook_active", 20), ("transfer_fee_set", 5)):
    RISK_WEIGHTS.setdefault(_flag, _weight)
    ACTIVE_WEIGHTS.setdefault(_flag, _weight)
for _flag in ("permanent_delegate_active", "transfer_fee_extreme", "default_account_frozen"):
    if _flag not in AVOID_FLAGS:
        AVOID_FLAGS.append(_flag)
for _flag in ("transfer_fee_high", "transfer_hook_active"):
    if _flag not in CAUTION_FLAGS:
        CAUTION_FLAGS.append(_flag)


def parse_t22(extensions) -> Optional[dict]:
    """What the Token-2022 extensions of a mint do. None if the data does not look like jsonParsed extensions."""
    if not isinstance(extensions, list):
        return None
    out: Dict[str, Any] = {"transfer_fee_bps": None, "max_transfer_fee": None, "permanent_delegate": None,
                           "default_account_frozen": False, "transfer_hook_program": None,
                           "non_transferable": False, "other": []}
    for e in extensions:
        if not isinstance(e, dict):
            return None
        name, st = e.get("extension"), e.get("state") or {}
        if name == "transferFeeConfig":
            bps = maximum = 0
            for k in ("newerTransferFee", "olderTransferFee"):  # the newer fee applies from its epoch: assume the worse
                fee = st.get(k) or {}
                bps = max(bps, int(fee.get("transferFeeBasisPoints") or 0))
                maximum = max(maximum, int(fee.get("maximumFee") or 0))
            out["transfer_fee_bps"], out["max_transfer_fee"] = bps, maximum
        elif name == "permanentDelegate":
            delegate = st.get("delegate")
            out["permanent_delegate"] = delegate if delegate and delegate != _ZERO_KEY else None
        elif name == "defaultAccountState":
            out["default_account_frozen"] = st.get("accountState") == "frozen"
        elif name == "transferHook":
            program = st.get("programId")
            out["transfer_hook_program"] = program if program and program != _ZERO_KEY else None
        elif name == "nonTransferable":
            out["non_transferable"] = True
        elif name:
            out["other"].append(name)
    return out


_fetch_security_v22 = fetch_security


async def fetch_security(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    raw = await _fetch_security_v22(client, mint)
    try:
        if raw and raw.get("program") == "token-2022" and raw.get("extensions"):  # one extra call, Token-2022 only
            res = await rpc(client, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
            data = ((res or {}).get("value") or {}).get("data")
            parsed = data.get("parsed") if isinstance(data, dict) else None
            details = parse_t22(((parsed or {}).get("info") or {}).get("extensions"))
            if details is not None:
                raw["t22"] = details
    except Exception:  # noqa: BLE001  best effort: without it the older, coarser flag stays
        pass
    return raw


_build_security_v22 = build_security


def build_security(raw: Optional[dict], pool_ids: set):
    out, flags = _build_security_v22(raw, pool_ids)
    t = (raw or {}).get("t22")
    if t is not None and "unparseableExtension" not in t["other"]:
        flags = [f for f in flags if f != "risky_token_extension"]  # replaced by the specific flags below
        bps = t["transfer_fee_bps"] or 0
        if t["permanent_delegate"]:
            flags.append("permanent_delegate_active")
        if bps >= T22_FEE_EXTREME_BPS:
            flags.append("transfer_fee_extreme")
        elif bps >= T22_FEE_HIGH_BPS:
            flags.append("transfer_fee_high")
        elif bps > 0:
            flags.append("transfer_fee_set")
        if t["transfer_hook_program"]:
            flags.append("transfer_hook_active")
        if t["default_account_frozen"]:
            flags.append("default_account_frozen")
        out["token2022"] = {
            "transfer_fee_pct": round(bps / 100, 2), "max_transfer_fee_raw": t["max_transfer_fee"],
            "permanent_delegate": t["permanent_delegate"], "transfer_hook_program": t["transfer_hook_program"],
            "default_account_frozen": t["default_account_frozen"], "non_transferable": t["non_transferable"],
            "other_extensions": t["other"],
        }
    return out, flags


# --- batch: cheap filters run BEFORE the expensive per-token work ---
BATCH_SORTS_V23 = ("input", "safest", "riskiest", "liquidity", "volume_1h")


def market_facts(mint: str, security: bool, pre: Optional[dict]):
    """(liquidity_usd, pair_age_minutes, volume_1h) from the shared DexScreener data or a fresh cache entry, else None."""
    if pre is not None:
        pair = pick_best_pair(pre.get("pairs") or [], mint)
        if pair is None:
            return None
        created = pair.get("pairCreatedAt")
        age = max(0, int((time.time() * 1000 - created) / 60000)) if created else None
        return (float((pair.get("liquidity") or {}).get("usd") or 0), age, float((pair.get("volume") or {}).get("h1") or 0))
    hit = cache_peek(mint if security else mint + ":nosec")
    if hit:
        d = hit[1]
        return float(d.get("liquidity_usd") or 0), d.get("pair_age_minutes"), float(d.get("volume_1h") or 0)
    return None


async def build_batch(client: httpx.AsyncClient, raw_mints: List[str], security: bool, *, sort: str = "input",
                      only: Optional[str] = None, max_risk: Optional[int] = None, top: Optional[int] = None,
                      view: str = "full", charge=None, min_liq: Optional[float] = None,
                      max_age_minutes: Optional[int] = None, min_volume_1h: Optional[float] = None) -> dict:
    """Smart batch: ONE shared DexScreener request for everything not cached, cost = only what needs fetching,
    an overall deadline (slow tokens are reported, not waited for), ranking, filters and a one-line summary."""
    ids = list(dict.fromkeys(m.strip() for m in raw_mints if m and m.strip()))
    if not ids:
        raise HTTPException(status_code=400, detail="Provide at least one mint")
    if len(ids) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"Max {MAX_BATCH} mints per request")
    if sort not in BATCH_SORTS_V23:
        raise HTTPException(status_code=400, detail="sort must be one of: " + ", ".join(BATCH_SORTS_V23))
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

    # cheap filters first: drop tokens that fail them BEFORE the expensive RPC / RugCheck work
    skipped: List[dict] = []
    if min_liq is not None or max_age_minutes is not None or min_volume_1h is not None:
        keep = []
        for m in valid:
            facts = market_facts(m, security, prefetched.get(m))
            why = None
            if facts is not None:  # unknown facts: let the full pipeline decide (it reports the real error)
                liq, age, vol = facts
                if min_liq is not None and liq < min_liq:
                    why = f"liquidity ${liq:,.0f} is below min_liq ${min_liq:,.0f}"
                elif max_age_minutes is not None and age is not None and age > max_age_minutes:
                    why = f"pair is {age} minutes old, above max_age_minutes {max_age_minutes}"
                elif min_volume_1h is not None and vol < min_volume_1h:
                    why = f"1h volume ${vol:,.0f} is below min_volume_1h ${min_volume_1h:,.0f}"
            if why:
                skipped.append({"mint": m, "reason": why})
            else:
                keep.append(m)
        valid = keep

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

    order = sorted(range(len(results)), key=lambda i: rank_key(results[i]))
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
    elif sort == "liquidity":
        results.sort(key=lambda r: -(r.get("liquidity_usd") or 0))
    elif sort == "volume_1h":
        results.sort(key=lambda r: -(r.get("volume_1h") or 0))
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
                + (f", {len(skipped)} skipped by filters" if skipped else "")
                + (f". Lowest risk: {best.get('token')} ({best['risk_score']}/100)" if best else "") + ".")
    return {
        "count": len(results),
        "summary": {"checked": len(ids), "ok": cnt["ok"], "caution": cnt["caution"], "avoid": cnt["avoid"],
                    "failed": len(errors), "headline": headline,
                    "safest": brief(best) if best else None, "riskiest": brief(worst) if worst else None,
                    "min_data_confidence": min((r["data_quality"]["data_confidence"] for r in everything), default=None)},
        "results": results, "errors": errors, "filtered_out": len(everything) - len(results),
        "sort": sort, "view": view, "skipped": skipped,
        "filters": {k: v for k, v in (("min_liq", min_liq), ("max_age_minutes", max_age_minutes),
                                      ("min_volume_1h", min_volume_1h), ("only", only), ("max_risk", max_risk),
                                      ("top", top)) if v is not None},
        "rank_basis": "risk_score + data-quality penalty (up to 20 points), ties broken by liquidity",
    }


def _drop_route(path: str) -> int:
    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if routes is None:
        return 0
    keep = [r for r in routes if getattr(r, "path", None) != path]
    removed = len(routes) - len(keep)
    routes[:] = keep
    return removed


_drop_route("/signals")  # replaced below by a version with the new parameters


@app.get("/signals", tags=["signals"], summary="Smart batch: up to 10 tokens, filtered cheaply, ranked",
         description="One shared market-data request for everything not cached. min_liq, max_age_minutes and min_volume_1h are "
                     "applied right after that request, so tokens that fail them cost no RPC or RugCheck calls and are listed "
                     "in `skipped`. The rate-limit cost is the number of tokens that needed fetching. Slow tokens appear in "
                     "`errors` (status 504). Each result has `safety_rank` (1 = lowest risk).")
async def signals(
    request: Request,
    mints: str = Query(..., max_length=600, description=f"Comma-separated mints, max {MAX_BATCH}"),
    security: bool = Query(True, description="Include on-chain checks"),
    sort: str = Query("input", description="input | safest | riskiest | liquidity | volume_1h"),
    only: Optional[str] = Query(None, description="Keep only these verdicts, e.g. ok,caution"),
    max_risk: Optional[int] = Query(None, ge=0, le=100, description="Keep only risk_score <= this"),
    top: Optional[int] = Query(None, ge=1, le=MAX_BATCH, description="Keep only the first N after sorting"),
    view: str = Query("full", description="full | compact (decision fields only)"),
    min_liq: Optional[float] = Query(None, ge=0, description="Skip tokens with liquidity below this (USD)"),
    max_age_minutes: Optional[int] = Query(None, ge=1, le=525600, description="Skip pairs older than this"),
    min_volume_1h: Optional[float] = Query(None, ge=0, description="Skip tokens with 1h volume below this (USD)"),
):
    data = await build_batch(request.app.state.client, mints.split(","), security, sort=sort, only=only,
                             max_risk=max_risk, top=top, view=view, min_liq=min_liq, max_age_minutes=max_age_minutes,
                             min_volume_1h=min_volume_1h, charge=lambda cost: rate_limited(client_ip(request), cost=cost))
    return JSONResponse(content=data, headers={"Cache-Control": "public, max-age=10"})


LLMS_TXT = LLMS_TXT + """
## Batch filters
GET /signals also takes min_liq (USD), max_age_minutes, min_volume_1h and sort=liquidity|volume_1h. These filters run right after
the shared market-data request, so tokens that fail them cost no RPC or RugCheck calls; they are listed in `skipped`.

## Token-2022 flags
permanent_delegate_active, transfer_fee_extreme (>=25%), transfer_fee_high (>=5%), transfer_fee_set (<5%), transfer_hook_active,
default_account_frozen. security.token2022 shows the details. A fee of 0% is no longer treated as risky.
"""
