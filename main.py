min_length=1, max_length=64),
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
