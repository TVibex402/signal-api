# ---------- v2.6: ideas from the encyclopedia that survive contact with engineering ----------
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).
VERSION = "2.6.0"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version

# --- cybernetics (negative feedback): when the main market source struggles, answers live longer so we lean on it less ---
BASE_CACHE_TTL = 30


def homeostat() -> int:
    global CACHE_TTL
    status = (src_status().get("dexscreener") or {}).get("status")
    CACHE_TTL = BASE_CACHE_TTL * {"degraded": 2, "down": 4}.get(status, 1)
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
                           "rule": "if the market data source is degraded or down, answers are reused 2x or 4x longer to lean on it less"}
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
"""
# ---------- v2.6.1: stronger resilience against DexScreener 429 ----------
STALE_MAX = max(STALE_MAX, 900)          # cho phép stale tới 15 phút
PARTIAL_TTL = max(PARTIAL_TTL, 15)       # partial sống lâu hơn một chút

_original_homeostat = homeostat

def homeostat() -> int:
    """Khi DexScreener bị 429/degraded thì giữ cache lâu hơn nhiều."""
    global CACHE_TTL
    status = (src_status().get("dexscreener") or {}).get("status")
    if status == "down":
        CACHE_TTL = BASE_CACHE_TTL * 6      # 3 phút
    elif status == "degraded":
        CACHE_TTL = BASE_CACHE_TTL * 3      # 1.5 phút
    else:
        CACHE_TTL = BASE_CACHE_TTL
    return CACHE_TTL
# ---------- v2.6.2: smarter DexScreener 429 handling ----------
_fetch_dex_v26 = fetch_dex


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=mint))
        if resp.status_code == 429:
            src_log("dexscreener", False, t0)
            # báo client đợi lâu hơn, đồng thời đánh dấu degraded để homeostat kéo dài cache
            raise HTTPException(
                status_code=503,
                detail="DexScreener rate limit (429). Using longer cache window.",
                headers={"Retry-After": "30"},
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
# ---------- Homepage: retry button when 429/503 ----------
_old_get = """if(!res.ok){result.innerHTML='<div style="color:#ff4d6d">Error: '+esc(data.detail||'Failed')+'</div>'}"""

_new_get = """if(!res.ok){
  const retry=parseInt(res.headers.get('Retry-After')||'30',10);
  let left=retry;
  result.innerHTML='<div style="color:#ff4d6d;margin-bottom:10px">Error: '+esc(data.detail||'Failed')+'</div>'
    +'<button id="retryBtn" style="margin-top:4px;padding:8px 16px;border-radius:8px;border:none;background:linear-gradient(90deg,#7c5cff,#00d4aa);color:#fff;font-weight:600;cursor:pointer">Thử lại sau '+left+'s</button>';
  const rb=document.getElementById('retryBtn');
  const t=setInterval(()=>{
    left--;
    if(left<=0){clearInterval(t);rb.textContent='Thử lại ngay';rb.disabled=false;rb.onclick=()=>getSignal()}
    else{rb.textContent='Thử lại sau '+left+'s';rb.disabled=true}
  },1000);
}"""

if _old_get in HOME_HTML:
    HOME_HTML = HOME_HTML.replace(_old_get, _new_get)
# ---------- v2.6.3: 429 cooldown, cleaner flags for majors, honest version ----------
# Load after part19, before part7. Same shared namespace as the other parts.
VERSION = "2.6.3"
app.version = VERSION
app.openapi_schema = None

# --- 1) Stop hammering DexScreener while it is rate-limiting us ---
# The callers already fall back to stale cache on this HTTPException, so failing fast is safe.
_DEX_COOLDOWN = {"until": 0.0}


def _retry_after_seconds(resp, default: int = 30) -> int:
    try:
        return max(1, min(int(resp.headers.get("retry-after", default)), 120))
    except (TypeError, ValueError):
        return default


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
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


# --- 2) LP flags are noise for majors like SOL (they showed lp_not_locked next to verdict ok) ---
MAJOR_NOISE_FLAGS = {"lp_not_locked", "lp_partially_locked", "lp_unverified"}
_with_quality_v262 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v262(result, status, security)
    if out.get("asset_class") == "major" and out.get("flags"):
        out["flags"] = [f for f in out["flags"] if f not in MAJOR_NOISE_FLAGS]
    return out


# --- 3) Retry button text in English (part19 wrote it in Vietnamese) ---
HOME_HTML = HOME_HTML.replace("Thử lại sau ", "Retry in ").replace("Thử lại ngay", "Retry now")

# Do not fail silently if the homepage patch from part19 did not match
if "retryBtn" not in HOME_HTML:
    print("[part20] warning: homepage retry button was NOT applied (the original JS line has changed)")
# ---------- v2.6.5: majors no longer list LP flags under flags_not_scored (paste at the END of part19.py) ----------
# finalize_risk (part8) builds flags_not_scored before with_quality runs, so the v2.6.3 filter on `flags` missed it.
VERSION = "2.6.5"
app.version = VERSION
app.openapi_schema = None

_with_quality_v264 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v264(result, status, security)
    if out.get("asset_class") == "major" and out.get("flags_not_scored"):
        left = [f for f in out["flags_not_scored"] if f not in MAJOR_NOISE_FLAGS]
        if left:
            out["flags_not_scored"] = left
        else:
            out.pop("flags_not_scored", None)
    return out
