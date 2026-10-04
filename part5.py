def rate(n: int, bad: int) -> Optional[float]:
    return round(bad / n * 100, 1) if n >= MIN_N_SHOW else None


def flag_stats(st: dict):
    """Per-flag outcome stats. The lift is ADJUSTED for the market background: each flagged token is compared
    with the bad-outcome rate of its own day (observed / expected), so a flag that merely shows up on crash
    days is not blamed for the crash. Falls back to the raw lift until 2+ days of cohort data exist."""
    n_all, bad_all = int(st.get("all:n", 0)), int(st.get("all:bad", 0))
    day_rate: Dict[str, float] = {}
    for k, v in st.items():
        if k.startswith("d:") and k.endswith(":n") and int(v) >= MIN_DAY_N:
            day = k[2:-2]
            day_rate[day] = int(st.get(f"d:{day}:bad", 0)) / int(v)
    fd: Dict[str, Dict[str, List[int]]] = {}  # flag -> day -> [n, bad]
    for k, v in st.items():
        if k.startswith("fd:") and (k.endswith(":n") or k.endswith(":bad")):
            parts = k.split(":")
            if len(parts) == 4:
                e = fd.setdefault(parts[1], {}).setdefault(parts[2], [0, 0])
                e[0 if parts[3] == "n" else 1] = int(v)
    base = bad_all / n_all if n_all else 0.0
    adjusted_ok = len(day_rate) >= 2
    rows = []
    for f, prior in RISK_WEIGHTS.items():
        n, bad = int(st.get(f"f:{f}:n", 0)), int(st.get(f"f:{f}:bad", 0))
        row: Dict[str, Any] = {"flag": f, "n": n, "bad_rate_pct": rate(n, bad), "lift_raw": None, "lift": None,
                               "market_adjusted": False, "prior_weight": prior, "suggested_weight": None, "blended_weight": prior}
        if n_all >= 100 and bad_all > 0 and n >= 30:
            raw = ((bad + base * 10) / (n + 10)) / base  # smoothed toward the base rate
            lift = raw
            if adjusted_ok:
                obs = exp = 0.0
                for day, (nf, bf) in fd.get(f, {}).items():
                    obs += bf
                    exp += nf * day_rate.get(day, base)
                lift = (obs + base * 10) / (exp + base * 10)
                row["market_adjusted"] = True
            sugg = max(0, min(40, round(25 * math.log(lift)))) if lift > 1 else 0
            alpha = min(0.5, n / 400)  # trust data more as n grows, never more than 50%
            row.update(lift_raw=round(raw, 2), lift=round(lift, 2), suggested_weight=sugg,
                       blended_weight=round((1 - alpha) * prior + alpha * sugg))
        rows.append(row)
    return rows, n_all


async def refresh_weights(client: httpx.AsyncClient):
    res = await redis_pipe(client, [["HGETALL", "st"]])
    if not res:
        return
    rows, n_all = flag_stats(to_dict(res[0]))
    new = dict(RISK_WEIGHTS)
    changed = False
    for r in rows:
        if r["suggested_weight"] is not None:
            new[r["flag"]] = r["blended_weight"]
            changed = True
    ACTIVE_WEIGHTS.clear()
    ACTIVE_WEIGHTS.update(new)
    _tune.update(on=changed, n=n_all)


async def stats_report(client: httpx.AsyncClient) -> dict:
    if not PRED_ENABLED:
        return {"enabled": False, "detail": "Track record store is not configured on this server."}
    if _stats_cache["data"] and time.time() - _stats_cache["ts"] < STATS_TTL:
        return _stats_cache["data"]
    res = await redis_pipe(client, [["HGETALL", "st"], ["ZCARD", "due"], ["LRANGE", "recent", 0, 19]])
    if res is None:
        return {"enabled": True, "available": False, "detail": "Stats store unavailable, retry shortly"}
    st = to_dict(res[0])

    def grp(prefix: str) -> dict:
        n, bad = int(st.get(f"{prefix}:n", 0)), int(st.get(f"{prefix}:bad", 0))
        ret = float(st.get(f"{prefix}:ret", 0))
        xn, xr = int(st.get(f"{prefix}:xn", 0)), float(st.get(f"{prefix}:xret", 0))
        return {"n": n, "bad_outcome_rate_pct": rate(n, bad),
                "avg_return_pct": round(ret / n, 1) if n >= MIN_N_SHOW else None,
                "avg_excess_return_pct": round(xr / xn, 1) if xn >= MIN_N_SHOW else None}

    rows, n_all = flag_stats(st)
    by_verdict = {v: {c: grp(f"v:{v}:{c}") for c in ("full", "market_only")} for v in ("ok", "caution", "avoid")}
    ok_r, av_r = by_verdict["ok"]["full"]["bad_outcome_rate_pct"], by_verdict["avoid"]["full"]["bad_outcome_rate_pct"]
    recent = []
    for x in res[2] or []:
        try:
            recent.append(json.loads(x))
        except Exception:
            pass
    hours = round(PRED_HORIZON_S / 3600, 1)
    cohorts = []
    for k in sorted((k for k in st if k.startswith("d:") and k.endswith(":n")), reverse=True)[:14]:
        day, n = k[2:-2], int(st[k])
        cohorts.append({"day": day, "n": n,
                        "bad_outcome_rate_pct": round(int(st.get(f"d:{day}:bad", 0)) / n * 100, 1) if n >= MIN_DAY_N else None})
    data = {
        "enabled": True, "available": True, "horizon_hours": hours, "resolved": n_all, "pending": int(res[1] or 0),
        "bad_outcome_definition": f"price down {abs(BAD_RETURN_PCT):.0f}%+ or liquidity down {round((1 - BAD_LIQ_RATIO) * 100)}%+ "
                                  f"or pair gone, measured about {hours}h after the verdict",
        "by_verdict": by_verdict,
        "avoid_vs_ok_gap_pts": round(av_r - ok_r, 1) if ok_r is not None and av_r is not None else None,
        "by_risk_bucket": [{"risk_score_range": f"{b * 10}-{b * 10 + 9}", **grp(f"rb:{b}")} for b in range(10)],
        "flags": rows,
        "market_adjustment": {
            "method": "flag lift = observed bad outcomes / bad outcomes expected from each day's overall rate; "
                      "avg_excess_return_pct = token return minus SOL return over the same window",
            "active": any(r["market_adjusted"] for r in rows),
            "days_with_cohort_data": len([c for c in cohorts if c["bad_outcome_rate_pct"] is not None]),
        },
        "daily_background": cohorts,
        "scoring": "tuned" if _tune["on"] else "static",
        "recent_outcomes": recent,
        "caveats": [
            "Only tokens that people query are measured: this is not a random sample of all tokens.",
            "Bad outcomes can come from ordinary market moves, not only rugs. Flags overlap, so per-flag numbers are not causal.",
            f"Rates are hidden until there are at least {MIN_N_SHOW} samples.",
            "Flag lifts are corrected for each day's overall bad-outcome rate once 2+ days of data exist; before that they are raw.",
            "Heuristic risk scoring, not financial advice.",
        ],
    }
    _stats_cache.update(ts=time.time(), data=data)
    return data


async def resolver_loop(app: FastAPI):
    last_tune = 0.0
    await asyncio.sleep(20)
    while True:
        busy = False
        try:
            busy = (await resolve_due(app.state.client)) >= RESOLVE_BATCH
            if AUTO_TUNE and time.time() - last_tune > 1800:
                await refresh_weights(app.state.client)
                last_tune = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(5 if busy else 300)


# ---------- exit check (can you sell, and at what cost?) ----------
JUP_URL = "https://api.jup.ag/swap/v1/quote"
JUP_KEY = os.getenv("JUPITER_API_KEY")  # optional free key from portal.jup.ag (higher rate limit)
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUP_LIMIT = 50 if JUP_KEY else 25  # quotes per minute, shared by everyone (keyless tier is ~0.5 req/s)
EXIT_CACHE_TTL = 60
MAX_EXIT_USD = 50_000
DEFAULT_EXIT_SIZES = [100.0, 1000.0]
VERDICT_RANK = {"ok": 0, "caution": 1, "avoid": 2}
EXIT_NOTE = (
    "Based on a Jupiter quote vs the DexScreener market price, so the loss includes fees, price impact and "
    "price differences. A quote does NOT prove a real sell will succeed: it cannot detect every sell-blocking "
    "mechanism. Treat it as one signal, not a guarantee."
)


def clean_sizes(vals) -> List[float]:
    if not vals:
        return list(DEFAULT_EXIT_SIZES)
    out: List[float] = []
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="sizes must be numbers in USD, e.g. 100,1000")
        if not (1 <= f <= MAX_EXIT_USD):
            raise HTTPException(status_code=400, detail=f"each size must be between 1 and {MAX_EXIT_USD} USD")
        out.append(f)
    if len(out) > 3:
        raise HTTPException(status_code=400, detail="Use at most 3 sizes")
    return out


def parse_sizes(text: Optional[str]) -> List[float]:
    return clean_sizes([x for x in (text or "").split(",") if x.strip()])


async def _jup_quote_raw(client: httpx.AsyncClient, mint: str, raw_amount: int) -> dict:
    """status: ok | no_route | unavailable. Never raises."""
    headers = {"x-api-key": JUP_KEY} if JUP_KEY else {}
    params = {"inputMint": mint, "outputMint": USDC_MINT, "amount": str(raw_amount),
              "slippageBps": "100", "restrictIntermediateTokens": "true"}
    try:
        resp = await client.get(JUP_URL, params=params, headers=headers)
    except httpx.HTTPError:
        return {"status": "unavailable"}
    if resp.status_code == 200:
        try:
            j = resp.json()
            out = int(j["outAmount"])
        except (ValueError, KeyError, TypeError):
            return {"status": "unavailable"}
        if out <= 0:
            return {"status": "no_route"}
        labels = []
        for step in j.get("routePlan") or []:
            lab = (step.get("swapInfo") or {}).get("label")
            if lab and lab not in labels:
                labels.append(lab)
        return {"status": "ok", "out_usd": out / 1e6, "impact_raw": j.get("priceImpactPct"), "routes": labels[:3]}
    if resp.status_code in (400, 404):
        txt = resp.text.lower()
        if "route" in txt or "tradable" in txt or "tradeable" in txt:
            return {"status": "no_route"}
    return {"status": "unavailable", "http_status": resp.status_code}


async def jup_quote(client: httpx.AsyncClient, mint: str, raw_amount: int) -> dict:
    t0 = time.time()
    q = await _jup_quote_raw(client, mint, raw_amount)
    src_log("jupiter", q["status"] != "unavailable", t0)
    return q


async def exit_check(client: httpx.AsyncClient, mint: str, sizes: List[float], sig: dict) -> dict:
    key = f"exit:{mint}:{','.join(str(x) for x in sizes)}"
    cached = cache_get(key, EXIT_CACHE_TTL)
    if cached:
        return cached
    price = float(sig.get("price_usd") or 0)
    dec = (sig.get("security") or {}).get("decimals")
    if dec is None:
        raw = await soft(get_security(client, mint), SECONDARY_TIMEOUT)
        dec = (raw or {}).get("decimals")
    if price <= 0 or dec is None:
        return {"available": False, "reason": "price or token decimals unavailable, retry shortly", "note": EXIT_NOTE}

    levels = []
    for usd in sizes:
        if rate_limited("__jup__", JUP_LIMIT):
            levels.append({"size_usd": usd, "status": "rate_limited"})
            continue
        q = await jup_quote(client, mint, int(usd / price * 10 ** int(dec)))
        lvl: Dict[str, Any] = {"size_usd": usd, "status": q["status"]}
        if q["status"] == "ok":
            loss = max(0.0, (1 - q["out_usd"] / usd) * 100)
            lvl.update(sell_receives_usd=round(q["out_usd"], 2), loss_vs_market_pct=round(loss, 2),
                       jupiter_price_impact_raw=q.get("impact_raw"), routes=q.get("routes"))
        levels.append(lvl)

    ok = [l for l in levels if l["status"] == "ok"]
    no_route = any(l["status"] == "no_route" for l in levels)
    if not ok and not no_route:
        return {"available": False, "levels": levels, "note": EXIT_NOTE,
                "reason": "Jupiter quote unavailable or rate limited, retry in a minute"}

    reasons: List[str] = []
    if no_route:
        grade, verdict = "no_route", "avoid"
        reasons.append("No sell route found on Jupiter")
    else:
        worst = max(l["loss_vs_market_pct"] for l in ok)
        grade = "good" if worst < 3 else "fair" if worst < 10 else "poor" if worst < 25 else "very_poor"
        verdict = {"good": "ok", "fair": "ok", "poor": "caution", "very_poor": "avoid"}[grade]
        if grade in ("poor", "very_poor"):
            reasons.append(f"Selling would lose about {worst:.0f}% vs market price (fees + price impact)")
    result = {"available": True, "exit_route_found": not no_route, "grade": grade, "exit_verdict": verdict,
              "levels": levels, "reasons": reasons, "note": EXIT_NOTE}
    cache_set(key, result)
    return result


async def build_exit_report(client: httpx.AsyncClient, mint: str, sizes: List[float]) -> dict:
    sig, _ = await build_signal(client, mint, True)
    ex = await exit_check(client, mint, sizes, sig)
    base = sig["verdict"]
    combined = base
    if ex.get("available") and VERDICT_RANK[ex["exit_verdict"]] > VERDICT_RANK[base]:
        combined = ex["exit_verdict"]
    reasons = list(sig.get("verdict_reasons") or []) + list(ex.get("reasons") or [])
    if ex.get("available"):
        ok = [l for l in ex["levels"] if l["status"] == "ok"]
        big = max(ok, key=lambda l: l["size_usd"]) if ok else None
        sell = (f"Selling ${big['size_usd']:,.0f} returns about ${big['sell_receives_usd']:,.2f} "
                f"({big['loss_vs_market_pct']}% below market)." if big else "No sell route found.")
    else:
        sell = "Exit check unavailable right now."
    mkt_reasons = "; ".join((sig.get("verdict_reasons") or [])[:2])
    mkt = f" ({mkt_reasons})" if mkt_reasons else ""
    return {
        "mint": mint, "token": sig.get("token"), "combined_verdict": combined, "market_verdict": base,
        "verdict_confidence": sig.get("verdict_confidence"), "reasons": reasons, "exit": ex,
        "summary": f"{sig.get('token') or '?'}: {combined.upper()}. {sell} Market verdict: {base}{mkt}.",
        "timestamp": int(time.time()),
    }
