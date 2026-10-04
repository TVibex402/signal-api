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
<p class="muted" style="margin-top:6px">Docs: <a href="/docs">/docs</a> &bull; <a href="/llms.txt">/llms.txt</a> &bull; <a href="/stats">/stats</a> &bull; <a href="/metrics">/metrics</a> &bull; Limit: 30 req/min per IP</p>
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
GET /signals?mints=A,B,C  (max 10) -> {count, summary, results:[...], errors:[...], filtered_out}
Smart batch: one shared market-data request, ranked (safety_rank 1 = lowest risk), summary.headline is one sentence.
Options: sort=input|safest|riskiest, only=ok,caution, max_risk=40, top=3, view=full|compact, security=false.
Rate-limit cost = tokens that needed fetching (cached ones are free). Slow tokens appear in errors with status 504.

## Data quality
Every signal has data_quality: freshness (fresh|cached|stale), age_s, completeness (full|partial|market_only),
missing (e.g. ["solana_rpc"]) and data_confidence 0-1. data_confidence says how complete and fresh the data is.
It is NOT the probability that the verdict is right (see /stats for measured accuracy).
When data sources are down you get HTTP 503 with Retry-After, retry_after_s and per-source status.
Every response carries X-API-Version and X-Response-Time. GET /metrics shows request counts, cache hit rate,
partial rate and latency (p50/p95).

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
Tools: get_token_signal (full data), check_token_risk (compact verdict), check_tokens_batch (max 10, ranked safest first, filters sort/only/max_risk/top),
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


@app.get("/signal", tags=["signals"], summary="Market + on-chain data and a verdict for one token")
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


@app.get("/signals", tags=["signals"], summary="Smart batch: up to 10 tokens, ranked and filtered",
         description="One shared market-data request for everything not cached; the rate-limit cost is the number of tokens "
                     "that needed fetching. Slow tokens are listed in `errors` (status 504) instead of delaying the batch. "
                     "Each result has `safety_rank` (1 = lowest risk).")
async def signals(
    request: Request,
    mints: str = Query(..., max_length=600, description=f"Comma-separated mints, max {MAX_BATCH}"),
    security: bool = Query(True, description="Include on-chain checks"),
    sort: str = Query("input", description="input | safest | riskiest"),
    only: Optional[str] = Query(None, description="Keep only these verdicts, e.g. ok,caution"),
    max_risk: Optional[int] = Query(None, ge=0, le=100, description="Keep only risk_score <= this"),
    top: Optional[int] = Query(None, ge=1, le=MAX_BATCH, description="Keep only the first N after sorting"),
    view: str = Query("full", description="full | compact (decision fields only)"),
):
    data = await build_batch(request.app.state.client, mints.split(","), security, sort=sort, only=only,
                             max_risk=max_risk, top=top, view=view,
                             charge=lambda cost: rate_limited(client_ip(request), cost=cost))
    return JSONResponse(content=data, headers={"Cache-Control": "public, max-age=10"})


@app.get("/exit", tags=["decision"], summary="Can you sell, and at what cost? (Jupiter quote, not proof)")
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


@app.get("/stats", tags=["trust"], summary="Measured accuracy of past verdicts")
async def stats_route(request: Request):
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    report = await stats_report(request.app.state.client)
    return JSONResponse(content=report, headers={"Cache-Control": "public, max-age=60"})


@app.get("/metrics", tags=["trust"], summary="Request counts, cache hit rate, latency, source health")
async def metrics_route():
    return JSONResponse(content=metrics_report(), headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION, "cache_size": len(_cache), "rpc": "custom" if os.getenv("SOLANA_RPC_URL") else "public", "mcp": mcp_server is not None, "mcp_error": mcp_error, "jupiter_key": bool(JUP_KEY),
            "uptime_s": int(time.time() - _m["started"]), "track_record": PRED_ENABLED, "auto_tune": AUTO_TUNE, "sources": src_status()}
