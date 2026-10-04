# ---------- signal logic ----------
def pick_best_pair(pairs: list, mint: str) -> Optional[dict]:
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        return None
    # Prefer pairs where the requested mint is the BASE token (otherwise data is for the other token)
    base_match = [p for p in sol if (p.get("baseToken") or {}).get("address") == mint]
    pool = base_match or sol
    return max(pool, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


RISK_WEIGHTS = {
    "very_low_liquidity": 30,
    "low_liquidity": 15,
    "brand_new_pair": 20,
    "very_new_pair": 10,
    "new_pair": 5,
    "extreme_turnover": 15,
    "high_turnover": 5,
    "dump_risk": 25,
    "heavy_selling": 10,
    "extreme_volume_spike": 10,
    "mint_authority_active": 25,
    "freeze_authority_active": 20,
    "risky_token_extension": 20,
    "extreme_holder_concentration": 30,
    "high_holder_concentration": 15,
    "dominant_holder": 10,
    "lp_not_locked": 25,
    "lp_partially_locked": 10,
}


def compute_signals(pair: dict) -> Dict[str, Any]:
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}
    liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or 0)

    m5 = txns.get("m5") or {}
    buys_5m = int(m5.get("buys") or 0)
    sells_5m = int(m5.get("sells") or 0)
    total_5m = buys_5m + sells_5m
    buy_pressure = round(buys_5m / total_5m, 3) if total_5m > 0 else None

    vol_5m = float(volume.get("m5") or 0)
    vol_1h = float(volume.get("h1") or 0)
    vol_6h = float(volume.get("h6") or 0)
    avg_5m = vol_1h / 12 if vol_1h > 0 else 0
    volume_spike = round(vol_5m / avg_5m, 2) if avg_5m > 0 else None
    turnover = round(vol_1h / liquidity_usd, 3) if liquidity_usd > 0 else None

    flags = []
    if volume_spike is not None:
        if volume_spike >= 5.0:
            flags.append("extreme_volume_spike")
        elif volume_spike >= 3.0:
            flags.append("volume_spike")
    if buy_pressure is not None:
        if buy_pressure >= 0.75:
            flags.append("heavy_buying")
        elif buy_pressure <= 0.25:
            flags.append("heavy_selling")
    if liquidity_usd < 20000:
        flags.append("very_low_liquidity")
    elif liquidity_usd < 50000:
        flags.append("low_liquidity")
    if turnover is not None:
        if turnover >= 3.0:
            flags.append("extreme_turnover")
        elif turnover >= 1.5:
            flags.append("high_turnover")

    created = pair.get("pairCreatedAt")
    age_minutes = None
    if created:
        age_minutes = max(0, int((time.time() * 1000 - created) / 60000))
        if age_minutes < 60:
            flags.append("brand_new_pair")
        elif age_minutes < 360:
            flags.append("very_new_pair")
        elif age_minutes < 1440:
            flags.append("new_pair")

    pc = price_change
    if (pc.get("m5") or 0) > 10 and (pc.get("h1") or 0) > 20:
        flags.append("strong_momentum")
    if (pc.get("m5") or 0) < -15 and (pc.get("h1") or 0) < -25:
        flags.append("dump_risk")
    if total_5m >= 300:
        flags.append("high_activity")

    return {
        "token": base.get("symbol") or "UNKNOWN",
        "name": base.get("name"),
        "mint": base.get("address"),
        "price_usd": pair.get("priceUsd"),
        "price_change_pct": {
            "5m": price_change.get("m5"),
            "1h": price_change.get("h1"),
            "6h": price_change.get("h6"),
            "24h": price_change.get("h24"),
        },
        "liquidity_usd": round(liquidity_usd, 2),
        "fdv_usd": pair.get("fdv"),
        "market_cap_usd": pair.get("marketCap"),
        "volume_5m": round(vol_5m, 2),
        "volume_1h": round(vol_1h, 2),
        "volume_6h": round(vol_6h, 2) if vol_6h else None,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "buy_pressure_5m": buy_pressure,
        "volume_spike_ratio_5m": volume_spike,
        "turnover_1h": turnover,
        "pair_age_minutes": age_minutes,
        "dex": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "flags": flags,
        "timestamp": int(time.time()),
        "source": "dexscreener",
        "version": VERSION,
    }


# ---------- on-chain security (Solana RPC) ----------
async def rpc(client: httpx.AsyncClient, method: str, params: list):
    resp = await client.post(
        RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"])[:200])
    return body.get("result")


async def fetch_security(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    """Raw on-chain data. Never raises; returns None if nothing could be read."""
    t_rpc = time.time()
    info_r, big_r = await asyncio.gather(
        rpc(client, "getAccountInfo", [mint, {"encoding": "jsonParsed"}]),
        rpc(client, "getTokenLargestAccounts", [mint, {"commitment": "confirmed"}]),
        return_exceptions=True,
    )
    src_log("solana_rpc", not isinstance(info_r, Exception) and not isinstance(big_r, Exception), t_rpc)
    raw: Dict[str, Any] = {"info_ok": False, "holders": None, "supply": None}

    try:
        val = (info_r or {}).get("value") if not isinstance(info_r, Exception) else None
        data = (val or {}).get("data")
        parsed = data.get("parsed") if isinstance(data, dict) else None
        if parsed and parsed.get("type") == "mint":
            info = parsed.get("info") or {}
            raw.update(
                info_ok=True,
                mint_authority=info.get("mintAuthority"),
                freeze_authority=info.get("freezeAuthority"),
                decimals=info.get("decimals"),
                supply=int(info.get("supply") or 0) or None,
                program="token-2022" if val.get("owner") == TOKEN_2022 else "spl-token",
                extensions=[e.get("extension") for e in (info.get("extensions") or []) if isinstance(e, dict)],
            )
    except Exception:
        pass

    try:
        if not isinstance(big_r, Exception) and big_r:
            accts = big_r.get("value") or []
            addrs = [a["address"] for a in accts]
            owners: Dict[str, str] = {}
            if addrs:
                try:
                    multi = await rpc(client, "getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
                    for addr, acc in zip(addrs, (multi or {}).get("value") or []):
                        try:
                            owners[addr] = acc["data"]["parsed"]["info"]["owner"]
                        except (TypeError, KeyError):
                            pass
                except Exception:
                    pass
            raw["holders"] = [
                {"account": a["address"], "owner": owners.get(a["address"]), "amount": int(a.get("amount") or 0)}
                for a in accts
            ]
    except Exception:
        pass

    if not raw["info_ok"] and raw["holders"] is None:
        return None
    return raw


async def get_security(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    key = "sec:" + mint
    cached = cache_get(key, SEC_CACHE_TTL)
    if cached:
        return cached
    try:
        raw = await fetch_security(client, mint)
    except Exception:
        return None
    if raw:
        cache_set(key, raw)
    return raw


def build_security(raw: Optional[dict], pool_ids: set):
    """Turn raw on-chain data into a public `security` block + extra flags."""
    if not raw:
        return {"available": False}, []
    flags = []
    out: Dict[str, Any] = {"available": True, "partial": False}

    if raw.get("info_ok"):
        ma, fa = raw.get("mint_authority"), raw.get("freeze_authority")
        out.update(
            mint_authority_revoked=ma is None,
            freeze_authority_revoked=fa is None,
            token_program=raw.get("program"),
            decimals=raw.get("decimals"),
        )
        risky = sorted(set(raw.get("extensions") or []) & RISKY_EXTENSIONS)
        out["risky_extensions"] = risky
        if ma is not None:
            flags.append("mint_authority_active")
        if fa is not None:
            flags.append("freeze_authority_active")
        if risky:
            flags.append("risky_token_extension")
    else:
        out["partial"] = True

    holders, supply = raw.get("holders"), raw.get("supply")
    if holders and supply:
        excluded_ids = pool_ids | KNOWN_NON_HOLDERS
        real, pool_pct = [], 0.0
        for h in holders:
            pct = h["amount"] / supply * 100
            if h["owner"] in excluded_ids or h["account"] in excluded_ids:
                pool_pct += pct
            else:
                real.append({"owner": h["owner"] or h["account"], "pct": round(pct, 2)})
        top1 = real[0]["pct"] if real else 0.0
        top10 = round(sum(h["pct"] for h in real[:10]), 2)
        out.update(
            top1_holder_pct=top1,
            top10_holders_pct=top10,
            liquidity_pool_pct=round(pool_pct, 2),
            top_holders=real[:5],
        )
        if top10 >= 80:
            flags.append("extreme_holder_concentration")
        elif top10 >= 50:
            flags.append("high_holder_concentration")
        if top1 >= 20:
            flags.append("dominant_holder")
    else:
        out["partial"] = True
    return out, flags


def finalize_risk(result: dict, onchain: bool):
    """Heuristic 0-100 score from market flags (+ on-chain flags when available).
    It is a quick filter, NOT a guarantee."""
    score = min(100, sum(ACTIVE_WEIGHTS.get(f, 0) for f in result["flags"]))
    result["scoring"] = "tuned" if _tune["on"] else "static"
    result["risk_score"] = score
    result["risk_level"] = "low" if score < 25 else "medium" if score < 50 else "high"
    result["risk_basis"] = "market+onchain" if onchain else "market"


async def get_lp_lock(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    """LP lock/burn % + RugCheck score from RugCheck's public summary. Never raises."""
    key = "lp:" + mint
    cached = cache_get(key, SEC_CACHE_TTL)
    if cached:
        return cached
    t0 = time.time()
    try:
        resp = await client.get(RUGCHECK_URL.format(mint=mint))
        resp.raise_for_status()
        j = resp.json()
        pct = j.get("lpLockedPct")
        out = {
            "lp_locked_pct": round(float(pct), 2) if pct is not None else None,
            "rugcheck_score": j.get("score_normalised"),  # 0-100, higher = riskier
            "rugcheck_risks": [
                {"name": r.get("name"), "level": r.get("level")}
                for r in (j.get("risks") or [])[:6]
                if isinstance(r, dict)
            ],
        }
        src_log("rugcheck", True, t0)
    except Exception:
        src_log("rugcheck", False, t0)
        return None
    cache_set(key, out)
    return out


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=mint))
        resp.raise_for_status()
        data = resp.json()
        src_log("dexscreener", True, t0)
        return data
    except (httpx.HTTPError, ValueError):
        src_log("dexscreener", False, t0)
        raise HTTPException(status_code=503, detail="Market data source (DexScreener) is unavailable, retry in a few seconds",
                            headers={"Retry-After": "10"})
