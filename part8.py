# ---------- v2.1: honest verdicts, decision object, smarter ranking ----------
# This file only ADDS or REPLACES functions defined in part1-part6 (same shared namespace).
VERSION = "2.1.0"
app.version = VERSION
app.openapi_schema = None  # force /docs to rebuild with the new version

# Assets where "rug" checks make no sense (deep, regulated or native markets). Matched by exact mint address.
MAJOR_ASSETS = {
    "So11111111111111111111111111111111111111112": "SOL (wrapped)",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
}
# Flags about authorities, holders and LP lock: right for memecoins, noise for majors/established tokens
RUG_STYLE_FLAGS = {"mint_authority_active", "freeze_authority_active", "extreme_holder_concentration",
                   "high_holder_concentration", "dominant_holder", "lp_not_locked", "lp_partially_locked"}


def asset_class(r: dict) -> str:
    """major | established | standard"""
    if r.get("mint") in MAJOR_ASSETS:
        return "major"
    if (r.get("liquidity_usd") or 0) >= 1_000_000 and (r.get("pair_age_minutes") or 0) >= 43200:
        return "established"
    return "standard"


def rank_key(r: dict):
    """Lower = safer. Unknown data is not rewarded: missing sources add up to 20 points."""
    conf = (r.get("data_quality") or {}).get("data_confidence")
    penalty = round((1 - (conf if conf is not None else 1.0)) * 20)
    return (r["risk_score"] + penalty, -(r.get("liquidity_usd") or 0))


def finalize_risk(result: dict, onchain: bool):
    """Heuristic 0-100 score. Rug-style flags are not scored for majors and established tokens."""
    cls = asset_class(result)
    result["asset_class"] = cls
    skip = RUG_STYLE_FLAGS if cls in ("major", "established") else set()
    score = min(100, sum(ACTIVE_WEIGHTS.get(f, 0) for f in result["flags"] if f not in skip))
    result["scoring"] = "tuned" if _tune["on"] else "static"
    result["risk_score"] = score
    result["risk_level"] = "low" if score < 25 else "medium" if score < 50 else "high"
    result["risk_basis"] = "market+onchain" if onchain else "market"
    shown = sorted(skip & set(result["flags"]))
    if shown:
        result["flags_not_scored"] = shown


def _positives(r: dict) -> list:
    out, sec = [], r.get("security") or {}
    if sec.get("mint_authority_revoked") is True:
        out.append("Mint authority revoked")
    if sec.get("freeze_authority_revoked") is True:
        out.append("Freeze authority revoked")
    lp = sec.get("lp_locked_pct")
    if lp is not None and lp >= 80:
        out.append(f"{lp:.0f}% of liquidity locked or burned")
    t10 = sec.get("top10_holders_pct")
    if t10 is not None and t10 < 30:
        out.append(f"Top 10 holders own only {t10:.0f}%")
    if (r.get("liquidity_usd") or 0) >= 250_000:
        out.append(f"Liquidity ${r['liquidity_usd']:,.0f}")
    if (r.get("pair_age_minutes") or 0) >= 10080:
        out.append("Pair older than 7 days")
    return out


def add_verdict(result: dict):
    """ok | caution | avoid plus a decision object. Heuristic, not advice."""
    flags = set(result["flags"])
    cls = result.get("asset_class") or asset_class(result)
    ignore = RUG_STYLE_FLAGS if cls == "major" else (RUG_STYLE_FLAGS - AUTHORITY_FLAGS) if cls == "established" else set()
    avoid = [f for f in AVOID_FLAGS if f in flags and f not in ignore and not (cls == "established" and f in AUTHORITY_FLAGS)]
    caution = [f for f in CAUTION_FLAGS if f in flags and f not in ignore]
    if cls == "established":
        caution = [f for f in sorted(AUTHORITY_FLAGS) if f in flags] + caution
    score = result["risk_score"]
    verdict = "avoid" if avoid or score >= 50 else "caution" if caution or score >= 25 else "ok"
    result["verdict"] = verdict
    result["verdict_reasons"] = [REASONS[f] for f in avoid + caution]
    result["verdict_confidence"] = "full" if result.get("security") and not result.get("partial") else "market_only"
    top = "; ".join(result["verdict_reasons"][:3]) or ("major asset, rug-style checks do not apply" if cls == "major" else "no major red flags found")
    result["summary"] = (
        f"{result.get('token') or '?'}: {verdict.upper()} ({result['verdict_confidence']} data). "
        f"{top}. Liquidity ${result['liquidity_usd']:,.0f}, risk {score}/100."
    )
    nxt = []
    if result.get("partial"):
        miss = [k for k, v in (result.get("sources") or {}).items() if v != "ok"]
        nxt.append("retry in a few seconds for full on-chain data" + (f" (missing: {', '.join(miss)})" if miss else ""))
    if verdict != "avoid":
        nxt.append("run the exit check (GET /exit or MCP check_exit) before sizing a trade")
    result["suggested_next"] = nxt
    blockers = [REASONS[f] for f in avoid]
    if not avoid and score >= 50:
        blockers.append(f"Risk score {score}/100 is at or above the avoid threshold (50)")
    note = ("Major asset: rug-style checks (authorities, holders, LP lock) do not apply." if cls == "major" else
            "Established token: holder and LP flags are shown but not scored." if cls == "established" else None)
    decision = {
        "verdict": verdict,
        "blockers": blockers,
        "cautions": [REASONS[f] for f in caution] + ([f"Risk score {score}/100"] if verdict == "caution" and not caution else []),
        "positives": _positives(result),
        "unknowns": [],          # filled in with_quality, which knows about missing data
        "next_checks": nxt,
        "valid_for_s": 30,
        "asset_class": cls,
        "disclaimer": "Heuristic output, not financial advice.",
    }
    if note:
        decision["note"] = note
    result["decision"] = decision


_with_quality_v20 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v20(result, status, security)
    dq, d = out["data_quality"], dict(out.get("decision") or {})
    if d:
        unknown = [f"{m} did not answer" for m in dq["missing"]]
        if not security:
            unknown.append("On-chain checks were skipped (security=false)")
        if status == "STALE":
            unknown.append(f"Data is {dq['age_s']}s old because the live source was down")
        if d.get("verdict") != "avoid":
            unknown.append("Exit cost not checked yet (use check_exit)")
        d.update(unknowns=unknown, confidence=dq["data_confidence"], freshness=dq["freshness"])
        out["decision"] = d
    return out


_compact_v20 = compact_signal


def compact_signal(r: dict) -> dict:
    out = _compact_v20(r)
    if "asset_class" in r:
        out["asset_class"] = r["asset_class"]
    d = r.get("decision")
    if d:
        out["decision"] = {k: d[k] for k in ("blockers", "unknowns", "confidence", "valid_for_s") if k in d}
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
        "rank_basis": "risk_score + data-quality penalty (up to 20 points), ties broken by liquidity",
    }
