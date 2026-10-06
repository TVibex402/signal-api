# ---------- v2.5: operator diagnostics, remembered exit checks, hypothesis flags, pricing page ----------
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).
import platform

VERSION = "2.5.0"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version

# --- /health: tell the operator exactly what is switched on and what is missing (never any secret values) ---
_redis_state: Dict[str, Any] = {"ts": 0.0, "ok": None}


async def redis_status(client: Optional[httpx.AsyncClient]) -> dict:
    missing = [name for name, value in (("UPSTASH_REDIS_REST_URL", REDIS_URL), ("UPSTASH_REDIS_REST_TOKEN", REDIS_TOKEN)) if not value]
    if missing:
        return {"configured": False, "missing_env": missing}
    if client is not None and time.time() - _redis_state["ts"] > 300:
        res = await redis_pipe(client, [["PING"]])
        _redis_state.update(ts=time.time(), ok=bool(res) and res[0] == "PONG")
    return {"configured": True, "reachable": _redis_state["ok"]}


_drop_route("/health")


@app.get("/health", tags=["ops"], summary="Service status and what is switched on")
async def health():
    return {
        "status": "ok", "version": VERSION, "uptime_s": int(time.time() - _m["started"]), "python": platform.python_version(),
        "cache_size": len(_cache), "rpc": "custom" if os.getenv("SOLANA_RPC_URL") else "public", "mcp": mcp_server is not None,
        "mcp_error": mcp_error, "jupiter_key": bool(JUP_KEY), "track_record": PRED_ENABLED,
        "track_record_store": await redis_status(getattr(app.state, "client", None)), "auto_tune": AUTO_TUNE,
        "api_keys_configured": len(API_KEYS), "streams": {"watching": len(_watchers), "open": sum(_stream_open.values())},
        "sources": src_status(),
    }


# --- an exit check you made recently follows the token: no_sell_route / high_exit_cost become real flags ---
EXIT_MEMORY_S = 600
_exit_seen: Dict[str, tuple] = {}
REASONS["no_sell_route"] = "No sell route was found on Jupiter when it was checked a few minutes ago"
REASONS["high_exit_cost"] = "Selling would lose a large part of the value to fees and price impact (checked recently)"
for _flag, _weight in (("no_sell_route", 40), ("high_exit_cost", 20)):
    RISK_WEIGHTS.setdefault(_flag, _weight)
    ACTIVE_WEIGHTS.setdefault(_flag, _weight)
if "no_sell_route" not in AVOID_FLAGS:
    AVOID_FLAGS.append("no_sell_route")
if "high_exit_cost" not in CAUTION_FLAGS:
    CAUTION_FLAGS.append("high_exit_cost")
_exit_check_v24 = exit_check


async def exit_check(client: httpx.AsyncClient, mint: str, sizes: List[float], sig: dict) -> dict:
    result = await _exit_check_v24(client, mint, sizes, sig)
    if result.get("available"):
        _exit_seen[mint] = (time.time(), result.get("grade"))
        if len(_exit_seen) > 2000:
            for old in sorted(_exit_seen, key=lambda m: _exit_seen[m][0])[:1000]:
                _exit_seen.pop(old, None)
    return result


def _fresh_exit(mint: Optional[str]):
    seen = _exit_seen.get(mint) if mint else None
    return seen if seen and time.time() - seen[0] <= EXIT_MEMORY_S else None


# --- hypotheses: flags that are shown and MEASURED, but not scored (weight 0) until the track record supports them ---
HYPOTHESES = {
    "late_entry_risk": "Up 100%+ in the last hour on a pair younger than a day: late entry into a pump",
    "one_sided_flow": "Over 90% of the last 5 minutes of trades were on one side, with high activity",
    "no_recent_trades": "No trades in the last 5 minutes: price and exit quotes are less reliable",
}
for _flag in HYPOTHESES:
    RISK_WEIGHTS.setdefault(_flag, 0)
    ACTIVE_WEIGHTS.setdefault(_flag, 0)


def add_hypothesis_flags(r: dict):
    flags = r["flags"]
    age = r.get("pair_age_minutes")
    if (((r.get("price_change_pct") or {}).get("1h")) or 0) >= 100 and age is not None and age < 1440:
        flags.append("late_entry_risk")
    trades = (r.get("buys_5m") or 0) + (r.get("sells_5m") or 0)
    bp = r.get("buy_pressure_5m")
    if bp is not None and trades >= 50 and (bp >= 0.9 or bp <= 0.1):
        flags.append("one_sided_flow")
    if trades == 0:
        flags.append("no_recent_trades")


_finalize_risk_v24 = finalize_risk


def finalize_risk(result: dict, onchain: bool):
    seen = _fresh_exit(result.get("mint"))
    if seen and seen[1] == "no_route":
        result["flags"].append("no_sell_route")
    elif seen and seen[1] in ("poor", "very_poor"):
        result["flags"].append("high_exit_cost")
    add_hypothesis_flags(result)
    _finalize_risk_v24(result, onchain)


_with_quality_v24 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v24(result, status, security)
    seen, d = _fresh_exit(out.get("mint")), out.get("decision")
    if d and seen:
        unknowns = [u for u in d.get("unknowns", []) if not u.startswith("Exit cost not checked")]
        out["decision"] = dict(d, unknowns=unknowns, exit_grade=seen[1])
    return out


# --- pricing and limits: always generated from the live settings, so the page cannot drift from the code ---
CONTACT_URL = os.getenv("CONTACT_URL", "")


def render_pricing() -> str:
    link = (f'<a href="{_h(CONTACT_URL)}">{_h(CONTACT_URL)}</a>' if CONTACT_URL.startswith("https://")
            else "the contact link of this project (see the GitHub page)")
    rows = (
        f"<tr><td><b>Free</b><br><span class='muted'>no key</span></td><td>{RATE_LIMIT} requests/min per IP</td><td>up to {MAX_BATCH} tokens</td>"
        f"<td>{STREAM_MAX_PER_IP} open streams</td><td>everything below, including MCP</td><td>$0</td></tr>"
        f"<tr><td><b>API key</b><br><span class='muted'>issued by hand</span></td><td>{KEY_RATE_LIMIT} requests/min</td><td>up to {MAX_BATCH} tokens</td>"
        f"<td>{STREAM_MAX_PER_KEY} open streams</td><td>its own reserved lane of {GLOBAL_KEY_RESERVE} upstream requests/min, "
        "so heavy anonymous traffic cannot lock it out</td><td>free during the beta</td></tr>"
        "<tr><td><b>Pay per call</b></td><td colspan='4'>Under evaluation (the x402 standard). Not available yet.</td><td>-</td></tr>")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVibex402 pricing and limits</title><style>
:root{{--bg:#070b12;--card:rgba(18,25,35,.8);--line:rgba(34,48,66,.8);--text:#e8eef6;--muted:#8b9bb4;--a:#14f195;--b:#9945ff}}
*{{box-sizing:border-box;margin:0;padding:0}}body{{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;line-height:1.55}}
.wrap{{max-width:820px;margin:0 auto;padding:32px 16px 70px}}h1{{font-size:2rem;background:linear-gradient(120deg,var(--b),var(--a));-webkit-background-clip:text;background-clip:text;color:transparent}}
h2{{font-size:1.1rem;margin:26px 0 10px}}.muted{{color:var(--muted);font-size:.88rem}}table{{width:100%;border-collapse:collapse;font-size:.9rem;display:block;overflow-x:auto}}
th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top}}li{{margin:6px 0 6px 18px}}a{{color:var(--a)}}
</style></head><body><div class="wrap"><h1>Pricing and limits</h1>
<p class="muted">Free first. These numbers are read from the running server, so they are always the real ones.</p>
<table><tr><th>Plan</th><th>Rate</th><th>Batch</th><th>Live streams</th><th>Also</th><th>Price</th></tr>{rows}</table>
<h2>What always stays free</h2><ul>
<li><a href="/accuracy">/accuracy</a>, <a href="/methodology">/methodology</a> and <a href="/ledger">/ledger</a>: measured results and the exact rules, in the open.</li>
<li>The MCP server at <code>/mcp</code> and the docs at <a href="/docs">/docs</a> and <a href="/llms.txt">/llms.txt</a>.</li></ul>
<h2>Getting a key</h2><p class="muted">Keys are issued by hand during the beta. Ask at {link} and say what you are building and how many requests you expect.</p>
<h2>Honest limits</h2><ul class="muted"><li>No uptime guarantee. The free host can sleep; clients should retry after a 503.</li>
<li>Verdicts are heuristics, not advice and not a security audit. A sell quote is not proof a sell will succeed.</li>
<li>Data comes from DexScreener, Solana RPC, RugCheck and Jupiter and can be late or wrong.</li></ul>
<p class="muted" style="margin-top:20px"><a href="/">Home</a></p></div></body></html>"""


@app.get("/pricing", response_class=HTMLResponse, tags=["ops"], summary="Plans, limits and how to get an API key")
async def pricing_page():
    return render_pricing()


if '<a href="/accuracy">/accuracy</a>' in HOME_HTML and "/pricing" not in HOME_HTML:
    HOME_HTML = HOME_HTML.replace('<a href="/accuracy">/accuracy</a>', '<a href="/accuracy">/accuracy</a> &bull; <a href="/pricing">/pricing</a>', 1)

LLMS_TXT = LLMS_TXT + """
## Limits, keys and pricing
GET /pricing lists the plans and the live limits. Free: no key. An X-API-Key header (issued by hand during the beta) gives a higher
rate limit and its own reserved lane in the shared upstream budget. Pay-per-call is not available yet.

## Hypothesis flags (shown, measured, not scored)
late_entry_risk, one_sided_flow, no_recent_trades. They carry weight 0 until /accuracy shows that they predict bad outcomes.
After an exit check, no_sell_route (avoid) and high_exit_cost (caution) also attach to later signals for that token for ten minutes.
"""
