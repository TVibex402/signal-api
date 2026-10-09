# ---------- part19: v2.7.2 (consolidated: v2.6.0 .. v2.7.1 merged, duplicates removed) ----------
# Soli Deo Gloria - to the glory of God alone.
# Work wholeheartedly, as working for the Lord (Colossians 3:23).
# May this product be honest, useful to the people who rely on it, and never promise what it cannot keep.
#
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).
VERSION = "2.7.2"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version

# --- cybernetics (negative feedback): when the main market source struggles, answers live longer so we lean on it less ---
BASE_CACHE_TTL = 30
STALE_MAX = max(STALE_MAX, 900)      # stale answers may be served for up to 15 minutes
PARTIAL_TTL = max(PARTIAL_TTL, 15)   # partial answers live a little longer


def homeostat() -> int:
    """When DexScreener is degraded or down, keep answers in cache longer."""
    global CACHE_TTL
    status = (src_status().get("dexscreener") or {}).get("status")
    CACHE_TTL = BASE_CACHE_TTL * {"degraded": 3, "down": 6}.get(status, 1)
    return CACHE_TTL


# --- pre-mortem (mental models): for every reason against a token, what would have to change for it to go away? ---
CHANGE_IF = {
    "mint_authority_active": "the mint authority is revoked",
    "freeze_authority_active": "the freeze authority is revoked",
    "lp_not_locked": "most of the liquidity is locked or burned",
    "lp_partially_locked": "at least 80% of the liquidity is locked or burned",
    "lp_unverified": "the pool's LP tokens are checked on a block explorer and shown to be burned or locked",
    "very_low_liquidity": "liquidity rises above $20k (above $50k to clear the next level)",
    "low_liquidity": "liquidity rises above $50k",
    "extreme_holder_concentration": "the top 10 real holders fall below 80% of supply (below 50% to clear the next level)",
    "high_holder_concentration": "the top 10 real holders fall below 50% of supply",
    "dominant_holder": "no single real holder owns 20% or more",
    "brand_new_pair": "the pair is older than 1 hour",
    "very_new_pair": "the pair is older than 6 hours",
    "dump_risk": "the price stops falling on both the 5 minute and 1 hour view",
    "extreme_turnover": "1h volume falls well below the liquidity",
    "no_sell_route": "a sell route appears on Jupiter",
    "high_exit_cost": "a fresh exit check shows a much smaller loss",
    "transfer_fee_extreme": "the issuer lowers the transfer fee below 25%",
    "transfer_fee_high": "the issuer lowers the transfer fee below 5%",
    "permanent_delegate_active": "the issuer removes the permanent delegate (rarely possible)",
    "default_account_frozen": "the issuer changes the default account state",
    "transfer_hook_active": "the issuer removes the transfer hook",
}


def would_change_if(out: dict, decision: dict) -> list:
    """Conditions, not predictions. Only for reasons that are actually part of this decision."""
    shown = set(decision.get("blockers") or []) | set(decision.get("cautions") or [])
    items = []
    for flag in out.get("flags") or []:
        if flag in CHANGE_IF and REASONS.get(flag) in shown:
            items.append({"flag": flag, "if": CHANGE_IF[flag]})
    return items[:6]


# --- volatility sets how long a reading stays useful (decision under pressure) ---
FAST_FLAGS = {"strong_momentum", "volume_spike", "extreme_volume_spike", "late_entry_risk", "dump_risk", "one_sided_flow"}


def valid_for(out: dict) -> int:
    flags = set(out.get("flags") or [])
    fast = bool(FAST_FLAGS & flags) or (out.get("pair_age_minutes") is not None and out["pair_age_minutes"] < 60)
    if fast:
        return 10
    return 120 if out.get("asset_class") in ("major", "established") else 30


# --- shadow flags: shown and measured, NOT scored (part8 scores with ACTIVE_WEIGHTS.get(flag, 0)) ---
DEAD_POOL_LIQ_USD = 5_000      # below this, with no recent trades, a sell is unlikely to go through
COLLAPSE_24H_PCT = -50.0       # price already lost half of its value in 24h
HYPOTHESES["dead_pool"] = "a pool under $5k liquidity with no recent trades is effectively untradeable (should be avoid)"
HYPOTHESES["price_collapse_24h"] = "a token that already lost 50%+ in 24h keeps losing (momentum of the dump)"

# LP flags are noise for majors like SOL
MAJOR_NOISE_FLAGS = {"lp_not_locked", "lp_partially_locked", "lp_unverified"}


def _tidy_major_flags(out: dict) -> None:
    if out.get("asset_class") != "major":
        return
    if out.get("flags"):
        out["flags"] = [f for f in out["flags"] if f not in MAJOR_NOISE_FLAGS]
    if out.get("flags_not_scored"):
        left = [f for f in out["flags_not_scored"] if f not in MAJOR_NOISE_FLAGS]
        if left:
            out["flags_not_scored"] = left
        else:
            out.pop("flags_not_scored", None)


def _add_shadow_flags(out: dict) -> None:
    flags = out.get("flags")
    if not isinstance(flags, list):
        return
    quiet = "no_recent_trades" in flags or (out.get("volume_1h") or 0) == 0
    if (out.get("liquidity_usd") or 0) < DEAD_POOL_LIQ_USD and quiet and "dead_pool" not in flags:
        flags.append("dead_pool")
    change = (out.get("price_change_pct") or {}).get("24h")
    if change is not None and change <= COLLAPSE_24H_PCT and "price_collapse_24h" not in flags:
        flags.append("price_collapse_24h")


# --- fallback market data: GeckoTerminal, used only when DexScreener refuses ---
_GT_URL = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?include=base_token,quote_token&page=1"
_GT_HEADERS = {"Accept": "application/json;version=20230302"}
_GT_COOLDOWN = {"until": 0.0}
_GT_SEM = asyncio.Semaphore(2)
_FALLBACK_MINTS: Dict[str, float] = {}   # mint -> when its data last came from the fallback
_GT_CONFIDENCE_CAP = 0.7


def _gt_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _gt_ms(iso):
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


def gt_to_dex_pairs(payload: dict, mint: str) -> list:
    """GeckoTerminal pools -> DexScreener-style pairs where `mint` is always the base token."""
    toks = {}
    for inc in payload.get("included") or []:
        a = inc.get("attributes") or {}
        if a.get("address"):
            toks[a["address"]] = {"address": a["address"], "name": a.get("name"), "symbol": a.get("symbol")}
    pairs = []
    for pool in payload.get("data") or []:
        a, rel = pool.get("attributes") or {}, pool.get("relationships") or {}

        def addr(key):
            tid = (((rel.get(key) or {}).get("data")) or {}).get("id") or ""
            return tid.split("_", 1)[-1] or None

        base, quote = addr("base_token"), addr("quote_token")
        if mint == base:
            ours, other, price, idx = base, quote, a.get("base_token_price_usd"), 0
        elif mint == quote:
            ours, other, price, idx = quote, base, a.get("quote_token_price_usd"), 1
        else:
            continue
        names = str(a.get("name") or "").split(" / ")
        base_tok = toks.get(ours) or {"address": ours, "symbol": names[idx] if len(names) == 2 else None}
        tx, vol, chg = a.get("transactions") or {}, a.get("volume_usd") or {}, a.get("price_change_percentage") or {}
        pairs.append({
            "chainId": "solana",
            "dexId": (((rel.get("dex") or {}).get("data")) or {}).get("id"),
            "pairAddress": a.get("address"),
            "baseToken": base_tok,
            "quoteToken": toks.get(other) or {"address": other},
            "priceUsd": price,
            "txns": {k: {"buys": (tx.get(k) or {}).get("buys"), "sells": (tx.get(k) or {}).get("sells")}
                     for k in ("m5", "h1", "h24") if k in tx},
            "volume": {k: _gt_float(vol.get(k)) for k in ("m5", "h1", "h6", "h24") if k in vol},
            "priceChange": {k: _gt_float(chg.get(k)) for k in ("m5", "h1", "h6", "h24") if k in chg},
            "liquidity": {"usd": _gt_float(a.get("reserve_in_usd"))},
            "fdv": _gt_float(a.get("fdv_usd")),
            "marketCap": _gt_float(a.get("market_cap_usd")),
            "pairCreatedAt": _gt_ms(a.get("pool_created_at")),
        })
    pairs.sort(key=lambda p: (p["liquidity"]["usd"] or 0), reverse=True)
    return pairs


def _mark_fallback(out: dict) -> None:
    if out.get("mint") not in _FALLBACK_MINTS:
        return
    out["source"] = "geckoterminal"
    out["source_note"] = ("DexScreener was rate-limiting, so market data comes from GeckoTerminal "
                          "(fewer fields, lower confidence)")
    dq = out.get("data_quality")
    if isinstance(dq, dict):
        dq = dict(dq, fallback_source="geckoterminal")
        dq["data_confidence"] = min(dq.get("data_confidence", 1.0), _GT_CONFIDENCE_CAP)
        out["data_quality"] = dq
    d = out.get("decision")
    if isinstance(d, dict):
        d = dict(d)
        d["confidence"] = min(d.get("confidence", 1.0), _GT_CONFIDENCE_CAP)
        d["unknowns"] = list(d.get("unknowns") or []) + ["Market data came from GeckoTerminal (DexScreener was rate-limiting)"]
        out["decision"] = d


_with_quality_v25 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    homeostat()
    out = _with_quality_v25(result, status, security)
    d = out.get("decision")
    if d:
        d = dict(d)
        d["valid_for_s"] = 0 if (out.get("data_quality") or {}).get("freshness") == "stale" else valid_for(out)
        if d.get("verdict") != "ok":
            change = would_change_if(out, d)
            if change:
                d["would_change_if"] = change
        out["decision"] = d
    _tidy_major_flags(out)
    _add_shadow_flags(out)
    _mark_fallback(out)
    return out


# --- science vs pseudoscience: the rules for promoting or dropping a hypothesis are fixed BEFORE the data arrives ---
PROMOTE_RULE = "promote when judged >= 100, seen on 7+ days and the market-adjusted lift is >= 1.5"
DROP_RULE = "drop when judged >= 200 and the market-adjusted lift is <= 1.1"


def hypotheses_report(st: dict) -> list:
    rows = {r["flag"]: r for r in flag_stats(st)[0]} if st else {}
    out = []
    for flag, claim in HYPOTHESES.items():
        row = rows.get(flag) or {}
        n, lift = int(row.get("n") or 0), row.get("lift")
        days = len({k.split(":")[2] for k in st if k.startswith(f"fd:{flag}:") and k.endswith(":n")}) if st else 0
        if n >= 200 and lift is not None and lift <= 1.1:
            status = "drop: no real signal"
        elif n >= 100 and days >= 7 and lift is not None and lift >= 1.5:
            status = "promote: measured signal"
        elif n < 100:
            status = "collecting"
        else:
            status = "keep watching"
        out.append({"flag": flag, "claim": claim, "status": status, "judged": n, "days_seen": days, "lift": lift})
    return out


_methodology_v25 = methodology_report


def methodology_report() -> dict:
    m = _methodology_v25()
    m["hypotheses"] = {"rules": [PROMOTE_RULE, DROP_RULE], "under_test": hypotheses_report(_raw_st["st"]),
                       "note": "Shown and measured, not scored. A status only says the numbers are strong enough to decide; weights change only when the operator promotes a flag or turns auto-tuning on."}
    m["adaptive_cache"] = {"base_ttl_s": BASE_CACHE_TTL, "current_ttl_s": CACHE_TTL,
                           "rule": "if the market data source is degraded or down, answers are reused 3x or 6x longer to lean on it less"}
    m["will_not_say"] = ["buy or sell", "price targets", "position sizes", "guarantees of any kind"]
    return m


# --- prediction error: show where the verdicts were wrong, not only where they were right ---
def render_surprises(recent: list) -> str:
    missed = [r for r in recent if r.get("verdict") in ("ok", "caution") and r.get("bad_outcome")][:5]
    false_alarms = [r for r in recent if r.get("verdict") == "avoid" and not r.get("bad_outcome")][:5]
    if not recent:
        return ""

    def rows(items):
        return "".join("<tr><td>" + _h(r.get("token")) + "</td><td>" + _h(r.get("verdict")) + "</td><td>" + _h(r.get("risk_score"))
                       + "</td><td>" + _pc(r.get("return_pct")) + "</td></tr>" for r in items)

    head = "<tr><th>Token</th><th>Verdict</th><th>Risk</th><th>Return</th></tr>"
    body = ('<h2>Biggest surprises</h2><p class="muted">A verdict is a prediction. The gap between it and what happened is what the '
            f"system learns from, so we show the misses too. Among the latest {len(recent)} judged verdicts:</p>")
    body += (f"<p><b>Called ok or caution, ended badly ({len(missed)})</b></p><table>{head}{rows(missed)}</table>" if missed
             else "<p class=\"muted\">No ok or caution verdict ended badly in this sample.</p>")
    body += (f"<p><b>Called avoid, did fine ({len(false_alarms)})</b></p><table>{head}{rows(false_alarms)}</table>" if false_alarms
             else "<p class=\"muted\">No avoid verdict turned out fine in this sample.</p>")
    return body


def render_hypotheses() -> str:
    rep = hypotheses_report(_raw_st["st"])
    rows = "".join("<tr><td>" + _h(h["flag"]) + "</td><td>" + _h(h["claim"]) + "</td><td>" + _h(h["status"]) + "</td><td>" + _h(h["judged"])
                   + "</td><td>" + _h(h["lift"]) + "</td></tr>" for h in rep)
    return ("<h2>Hypotheses under test</h2><p class=\"muted\">Written down before the data: " + _h(PROMOTE_RULE) + "; " + _h(DROP_RULE)
            + ". They are shown but not scored until a rule says promote.</p>"
            "<table><tr><th>Flag</th><th>Claim</th><th>Status</th><th>Judged</th><th>Lift</th></tr>" + rows + "</table>")


_render_accuracy_v25 = render_accuracy


def render_accuracy(rep: dict, head: Optional[str]) -> str:
    page = _render_accuracy_v25(rep, head)
    extra = render_surprises(rep.get("recent_outcomes") or []) + render_hypotheses()
    for marker in ("<h2>Methodology</h2>", "<h2>How we measure</h2>"):
        if marker in page:
            return page.replace(marker, extra + marker, 1)
    return page


LLMS_TXT = LLMS_TXT + """
## Decision object: validity and pre-mortem
decision.valid_for_s says how long the reading stays useful: 10s for fast-moving tokens (momentum, volume spikes, dumps, pairs under an hour),
30s normally, 120s for majors and established tokens, 0 when the data is stale. decision.would_change_if lists, for each reason against the
token, the condition that would make it go away. They are conditions, not predictions.

## Hypotheses
/methodology lists the hypotheses under test with the promotion and drop rules fixed in advance, and their current status.
The accuracy page also shows the biggest surprises: verdicts that were wrong.

## Data source
Market data normally comes from DexScreener. When DexScreener is rate-limiting, "source" is "geckoterminal" and
data_quality.data_confidence is capped at 0.7. Errors that mean "try again later" carry retry_after_s.
"""


# ---------- HTTP layer: shared cooldowns, DexScreener fetchers, fallback ----------
_DEX_COOLDOWN = {"until": 0.0}
_RUGCHECK_COOLDOWN = {"until": 0.0}
_HOST_COOLDOWNS = {"api.dexscreener.com": _DEX_COOLDOWN, "api.rugcheck.xyz": _RUGCHECK_COOLDOWN}


def _retry_after_seconds(resp, default: int = 30) -> int:
    try:
        return max(1, min(int(resp.headers.get("retry-after", default)), 120))
    except (TypeError, ValueError):
        return default


# Every call to these hosts, from any part (part4 and part15 call DexScreener straight through client.get),
# shares one cooldown: after a 429 we stop calling that host for Retry-After seconds. Callers already catch
# httpx.HTTPError, so they simply skip the round.
if not getattr(httpx.AsyncClient.get, "_host_guard", False):
    _client_get_original = httpx.AsyncClient.get

    async def _guarded_get(self, url, *args, **kwargs):
        cooldown = None
        if isinstance(url, str):
            for host, state in _HOST_COOLDOWNS.items():
                if host in url:
                    cooldown = state
                    break
        if cooldown is None:
            return await _client_get_original(self, url, *args, **kwargs)
        if cooldown["until"] > time.time():
            raise httpx.ConnectError("source is cooling down after a 429")
        resp = await _client_get_original(self, url, *args, **kwargs)
        if resp.status_code == 429:
            cooldown["until"] = time.time() + _retry_after_seconds(resp)
        return resp

    _guarded_get._host_guard = True
    httpx.AsyncClient.get = _guarded_get


async def _fetch_dex_direct(client: httpx.AsyncClient, mint: str) -> dict:
    wait = _DEX_COOLDOWN["until"] - time.time()
    if wait > 0:  # still cooling down: do not call upstream at all
        raise HTTPException(
            status_code=503,
            detail="DexScreener rate limit (429). Using longer cache window.",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=mint))
        if resp.status_code == 429:
            ra = _retry_after_seconds(resp)
            _DEX_COOLDOWN["until"] = time.time() + ra
            src_log("dexscreener", False, t0)
            raise HTTPException(
                status_code=503,
                detail="DexScreener rate limit (429). Using longer cache window.",
                headers={"Retry-After": str(ra)},
            )
        resp.raise_for_status()
        data = resp.json()
        src_log("dexscreener", True, t0)
        return data
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        src_log("dexscreener", False, t0)
        raise HTTPException(
            status_code=503,
            detail="Market data source (DexScreener) is unavailable, retry in a few seconds",
            headers={"Retry-After": "15"},
        )


async def _gt_fetch(client, mint: str):
    if _GT_COOLDOWN["until"] > time.time():
        return None
    async with _GT_SEM:
        try:
            resp = await client.get(_GT_URL.format(mint=mint), headers=_GT_HEADERS, timeout=8.0)
        except (httpx.HTTPError, asyncio.TimeoutError):
            print(f"[fallback] geckoterminal unreachable for {mint[:6]}")
            return None
        if resp.status_code == 429:
            _GT_COOLDOWN["until"] = time.time() + _retry_after_seconds(resp, 60)
            print("[fallback] geckoterminal is rate-limiting too, pausing it")
            return None
        if resp.status_code != 200:
            print(f"[fallback] geckoterminal answered {resp.status_code} for {mint[:6]}")
            return None
        try:
            return gt_to_dex_pairs(resp.json(), mint) or None
        except Exception:  # noqa: BLE001
            print(f"[fallback] geckoterminal answer for {mint[:6]} could not be read")
            return None


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
    """DexScreener first; if it refuses (503), GeckoTerminal in DexScreener's shape; else the original error."""
    try:
        data = await _fetch_dex_direct(client, mint)
        _FALLBACK_MINTS.pop(mint, None)  # DexScreener is back for this token
        return data
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        pairs = await _gt_fetch(client, mint)
        if not pairs:
            raise  # fallback failed too: report the original DexScreener problem
        _FALLBACK_MINTS[mint] = time.time()
        print(f"[fallback] geckoterminal served {mint[:6]} ({len(pairs)} pools)")
        return {"pairs": pairs}


async def fetch_dex_many(client: httpx.AsyncClient, mints: List[str]) -> Optional[Dict[str, dict]]:
    """ONE DexScreener request for up to 30 mints. Returns {mint: {"pairs": [...]}} or None if the call failed or
    the source is cooling down (callers then fall back to stale cache, the single fetch_dex, or a clear 503).
    - Majors (SOL, USDC, USDT) stay out: with their huge pair counts they crowd small tokens out of the shared answer.
    - A mint with no pair in the answer is left out, so the caller asks for it alone instead of reporting a false 404.
    """
    rest = [m for m in mints if m not in MAJOR_ASSETS]
    if not rest:
        return {}
    if _DEX_COOLDOWN["until"] > time.time():
        return None
    out: Dict[str, dict] = {m: {"pairs": []} for m in rest}
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=",".join(rest)))
        if resp.status_code == 429:
            _DEX_COOLDOWN["until"] = time.time() + _retry_after_seconds(resp)
            src_log("dexscreener", False, t0)
            return None
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        src_log("dexscreener", True, t0)
    except (httpx.HTTPError, ValueError):
        src_log("dexscreener", False, t0)
        return None
    # A token belongs to a pair as its BASE. Matching the quote side too would push every memecoin/SOL pair into
    # SOL's list, so quote is only a fallback for tokens with no base match.
    for p in pairs:
        base = (p.get("baseToken") or {}).get("address")
        if base in out:
            out[base]["pairs"].append(p)
    no_base = {m for m, v in out.items() if not v["pairs"]}
    for p in pairs:
        quote = (p.get("quoteToken") or {}).get("address")
        if quote in no_base:
            out[quote]["pairs"].append(p)
    return {m: v for m, v in out.items() if v["pairs"]}


# ---------- Homepage: retry button when 429/503 ----------
_old_get = """if(!res.ok){result.innerHTML='<div style="color:#ff4d6d">Error: '+esc(data.detail||'Failed')+'</div>'}"""

_new_get = """if(!res.ok){
  const retry=parseInt(res.headers.get('Retry-After')||'30',10);
  let left=retry;
  result.innerHTML='<div style="color:#ff4d6d;margin-bottom:10px">Error: '+esc(data.detail||'Failed')+'</div>'
    +'<button id="retryBtn" disabled style="margin-top:4px;padding:8px 16px;border-radius:8px;border:none;background:linear-gradient(90deg,#7c5cff,#00d4aa);color:#fff;font-weight:600;cursor:pointer">Retry in '+left+'s</button>';
  const rb=document.getElementById('retryBtn');
  const t=setInterval(()=>{
    left--;
    if(left<=0){clearInterval(t);rb.textContent='Retry now';rb.disabled=false;rb.onclick=()=>getSignal()}
    else{rb.textContent='Retry in '+left+'s';rb.disabled=true}
  },1000);
}"""

if _old_get in HOME_HTML:
    HOME_HTML = HOME_HTML.replace(_old_get, _new_get)
elif "retryBtn" not in HOME_HTML:
    print("[part19] warning: homepage retry button was NOT applied (the original JS line has changed)")