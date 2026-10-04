# ---------- MCP server (remote, Streamable HTTP at /mcp) ----------
# Lets Claude and other MCP agents call the API as tools.
# IMPORTANT: this block must stay LAST (it mounts at "/"). A problem in here disables MCP only:
# the REST API keeps running and /health shows mcp_error.
mcp_error: Optional[str] = None
try:
    from mcp.server.fastmcp import Context, FastMCP
    _HAS_MCP = True
except ImportError:  # `mcp` not installed: the REST API still works
    _HAS_MCP = False
    mcp_error = "mcp package not installed"
try:
    from mcp.server.fastmcp.exceptions import ToolError  # message is shown to the model
except Exception:  # noqa: BLE001
    ToolError = ValueError  # type: ignore[misc,assignment]
try:
    from mcp.types import ToolAnnotations
except Exception:  # noqa: BLE001
    ToolAnnotations = None  # type: ignore[assignment,misc]

MCP_INSTRUCTIONS = (
    "Solana token data and risk checks (heuristics, not financial advice). Recommended workflow: "
    "1) check_tokens_batch for a watchlist (ranked, safest first) or check_token_risk for one token; "
    "2) if the verdict is not 'avoid', call check_exit to see what selling would cost; "
    "3) call get_track_record once to learn how reliable past verdicts were. "
    "Always read data_quality: if completeness is 'partial' or freshness is 'stale', say so and lower your confidence. "
    "Never present a verdict as a guarantee."
)


def _mcp_key(ctx) -> str:
    try:
        return client_ip(ctx.request_context.request)
    except Exception:  # noqa: BLE001
        return "mcp-unknown"


def _rl(key: str, cost: int = 1):
    wait = rate_limited(key, cost=cost)
    if wait:
        raise ToolError(f"Rate limit exceeded. Retry in {wait}s")


async def _mcp_signal(mint: str, security: bool) -> dict:
    mint = (mint or "").strip()
    if not MINT_RE.match(mint):
        raise ToolError("Invalid Solana token mint address (expected base58, 32-44 characters)")
    try:
        res, _ = await build_signal(app.state.client, mint, security)
    except HTTPException as e:
        raise ToolError(f"{e.detail} (HTTP {e.status_code})")
    return res


def _setup_mcp():
    import inspect

    kwargs: Dict[str, Any] = dict(stateless_http=True, json_response=True, instructions=MCP_INSTRUCTIONS)
    try:
        # FastMCP enables localhost-only Host checks by default, which would reject *.onrender.com.
        # This server is public with no cookies or auth.
        from mcp.server.transport_security import TransportSecuritySettings
        kwargs["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    except ImportError:
        pass
    srv = FastMCP("TVibex402", **kwargs)

    def supports(fn, name: str) -> bool:
        try:
            return name in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False

    def tool(title: str):
        kw: Dict[str, Any] = {}
        if supports(srv.tool, "title"):
            kw["title"] = title
        if ToolAnnotations is not None and supports(srv.tool, "annotations"):
            kw["annotations"] = ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False,
                                                idempotentHint=True, openWorldHint=True)
        return srv.tool(**kw)

    @tool("Token risk check")
    async def check_token_risk(mint: str, ctx: Context, include_security: bool = True) -> dict:
        """Quick decision for ONE Solana token: verdict (ok/caution/avoid) with reasons, risk score, price, liquidity,
        authorities, top-10 holder % and LP lock %. Start here. Read data_quality before trusting it.
        mint: the token's Solana mint address (base58)."""
        _rl(_mcp_key(ctx))
        return compact_signal(await _mcp_signal(mint, include_security))

    @tool("Token signal (full data)")
    async def get_token_signal(mint: str, ctx: Context, include_security: bool = True) -> dict:
        """Full market and on-chain data for ONE token: price changes, volume, buy pressure, flags, security block,
        summary and suggested next steps. Use when check_token_risk is not enough."""
        _rl(_mcp_key(ctx))
        return await _mcp_signal(mint, include_security)

    @tool("Smart batch check")
    async def check_tokens_batch(mints: List[str], ctx: Context, include_security: bool = True, sort: str = "safest",
                                 only: Optional[str] = None, max_risk: Optional[int] = None,
                                 top: Optional[int] = None) -> dict:
        """Check up to 10 tokens at once with ONE shared data request. Returns summary.headline (one sentence), results
        ranked by safety (safety_rank 1 = lowest risk) and errors for tokens that failed or are still computing.
        sort: safest (default) | riskiest | input. only: keep these verdicts, e.g. 'ok,caution'.
        max_risk: keep risk_score <= this. top: keep the first N after sorting."""
        key = _mcp_key(ctx)
        try:
            return await build_batch(app.state.client, mints, include_security, sort=sort, only=only, max_risk=max_risk,
                                     top=top, view="compact", charge=lambda cost: rate_limited(key, cost=cost))
        except HTTPException as e:
            raise ToolError(f"{e.detail} (HTTP {e.status_code})")

    @tool("Exit check (can you sell?)")
    async def check_exit(mint: str, ctx: Context, sizes_usd: Optional[List[float]] = None) -> dict:
        """Can you SELL this token, and at what cost? Quotes selling each size (default $100 and $1000) through Jupiter
        and compares with the market price. Returns combined_verdict (market + exit), loss_vs_market_pct per size and a
        summary. Use after check_token_risk, before sizing a trade. A quote is not proof a real sell will succeed."""
        _rl(_mcp_key(ctx), cost=2)
        mint = (mint or "").strip()
        if not MINT_RE.match(mint):
            raise ToolError("Invalid Solana token mint address (expected base58, 32-44 characters)")
        try:
            return await build_exit_report(app.state.client, mint, clean_sizes(sizes_usd))
        except HTTPException as e:
            raise ToolError(f"{e.detail} (HTTP {e.status_code})")

    @tool("Track record")
    async def get_track_record() -> dict:
        """How often were this service's verdicts right? Measured outcomes about 24h after each verdict, per verdict
        and per flag, with recent examples and caveats. Rates stay null until enough samples exist."""
        return await stats_report(app.state.client)

    return srv


if _HAS_MCP:
    try:
        _srv = _setup_mcp()
        app.mount("/", _srv.streamable_http_app())
        mcp_server = _srv
    except Exception as _e:  # noqa: BLE001  never let MCP problems take the REST API down
        mcp_server = None
        mcp_error = f"{type(_e).__name__}: {str(_e)[:200]}"
