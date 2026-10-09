# ---------- part22: v2.9.0 - cleaner track record: no major assets, one verdict per mint per 24h, source labels ----------
# Loads before part7 (MCP). Replaces record_prediction and resolve_due, widens the seed candidates, adds by_source to /stats.
import contextvars

VERSION = "2.9.0"
app.version = VERSION
app.openapi_schema = None

PRED_LOCK_S = 86400  # at most one recorded verdict per mint per 24h: repeats are not independent samples
SEED_GECKO = (os.getenv("SEED_GECKO") or "true").lower() in ("1", "true", "yes")  # GeckoTerminal pools are not paid promotions
_GECKO_URLS = {
    "trending": "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools",
    "new": "https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
}
_PRED_SRC = contextvars.ContextVar("pred_src", default="user")
_SEED_ORIGIN: Dict[str, str] = {}  # mint -> boost | profile | trending | new (memory only)


def _pred_source(mint: str) -> str:
    if _PRED_SRC.get() == "seed":
        return "seed_" + _SEED_ORIGIN.get(mint, "other")
    return "user"


async def record_prediction(client: httpx.AsyncClient, r: dict):
    """Store one verdict to be judged later. Skips major assets, one per mint per 24h, MAX_PRED_PER_DAY per day."""
    try:
        price = float(r.get("price_usd") or 0)
        mint = r.get("mint")
        if price <= 0 or not mint or mint in MAJOR_ASSETS:
            return
        today = time.strftime("%Y%m%d", time.gmtime())
        if _pred_day["day"] != today:
            _pred_day.update(day=today, count=0)
        if _pred_day["count"] >= MAX_PRED_PER_DAY:
            return
        lock = await redis_pipe(client, [["SET", f"plock24:{mint}", "1", "EX", PRED_LOCK_S, "NX"]])
        if not lock or lock[0] != "OK":
            return
        _pred_day["count"] += 1
        now = int(time.time())
        pid = f"{mint}:{now}"
        sol = await sol_price(client)
        src = _pred_source(mint)
        await redis_pipe(client, [
            ["HSET", f"pred:{pid}", "mint", mint, "token", r.get("token") or "", "ts", now, "price", price,
             "liq", r.get("liquidity_usd") or 0, "verdict", r["verdict"], "conf", r["verdict_confidence"],
             "score", r["risk_score"], "flags", ",".join(r.get("flags") or []), "sol", sol or "", "src", src],
            ["EXPIRE", f"pred:{pid}", PRED_HORIZON_S * 4],
            ["ZADD", "due", now + PRED_HORIZON_S, pid],
        ])
        await ledger_append(client, {
            "id": pid, "ts": now, "mint": mint, "token": r.get("token") or "", "verdict": r["verdict"],
            "risk_score": r["risk_score"], "price_usd": price, "liquidity_usd": r.get("liquidity_usd") or 0,
            "flags": sorted(r.get("flags") or []), "version": VERSION, "src": src,
        })
    except Exception:
        pass


async def resolve_due(client: httpx.AsyncClient) -> int:
    """Judge predictions whose time has come. Returns how many were processed. (v2.9: skips major assets, labels the source)"""
    now = int(time.time())
    got = await redis_pipe(client, [["ZRANGEBYSCORE", "due", "-inf", now, "LIMIT", 0, RESOLVE_BATCH]])
    ids = (got or [[]])[0] or []
    if not ids:
        return 0
    hashes = await redis_pipe(client, [["HGETALL", f"pred:{i}"] for i in ids])
    if hashes is None:
        return 0
    cmds: list = []
    preds: Dict[str, dict] = {}
    for pid, h in zip(ids, hashes):
        d = to_dict(h)
        if d.get("mint"):
            preds[pid] = d
        else:
            cmds.append(["ZREM", "due", pid])  # hash expired: drop the orphan
    best: Dict[str, Optional[dict]] = {}
    sol1: Optional[float] = None
    if preds:
        mints = sorted({d["mint"] for d in preds.values()})
        ask = mints if SOL_MINT in mints else mints + [SOL_MINT]
        t0 = time.time()
        try:
            resp = await client.get(DEX_URL.format(mint=",".join(ask)))
            resp.raise_for_status()
            pairs = resp.json().get("pairs") or []
            src_log("dexscreener", True, t0)
        except (httpx.HTTPError, ValueError):
            src_log("dexscreener", False, t0)
            if cmds:
                await redis_pipe(client, cmds)
            return 0  # try again next cycle, nothing is lost
        for m in mints:
            best[m] = pick_best_pair([p for p in pairs if (p.get("baseToken") or {}).get("address") == m], m)
        sp = pick_best_pair([p for p in pairs if (p.get("baseToken") or {}).get("address") == SOL_MINT], SOL_MINT)
        try:
            sol1 = float(sp.get("priceUsd")) if sp else None
        except (TypeError, ValueError):
            sol1 = None

    recent = []
    for pid, d in preds.items():
        cmds += [["ZREM", "due", pid], ["DEL", f"pred:{pid}"]]
        if d["mint"] in MAJOR_ASSETS:
            continue  # v2.9: SOL and stablecoins are not judged (they would only pad the ok group)
        age = now - int(d.get("ts") or 0)
        if age > PRED_HORIZON_S * 3:
            continue  # judged far too late (server was asleep): would distort the stats
        p0, l0 = float(d.get("price") or 0), float(d.get("liq") or 0)
        pair = best.get(d["mint"])
        if pair is None:
            ret, liq_ratio = -100.0, 0.0
        else:
            p1 = float(pair.get("priceUsd") or 0)
            l1 = float((pair.get("liquidity") or {}).get("usd") or 0)
            ret = (p1 / p0 - 1) * 100 if p0 > 0 else 0.0
            liq_ratio = l1 / l0 if l0 > 0 else 1.0
        bad = ret <= BAD_RETURN_PCT or liq_ratio <= BAD_LIQ_RATIO
        # market background: how much did SOL itself move over the same window?
        sol0 = float(d.get("sol") or 0)
        sol_ret = (sol1 / sol0 - 1) * 100 if (sol1 and sol0 > 0) else None
        xret = ret - sol_ret if sol_ret is not None else None
        v, c = d.get("verdict", "?"), d.get("conf", "?")
        bucket = min(9, int(float(d.get("score") or 0)) // 10)
        day = time.strftime("%Y%m%d", time.gmtime(int(d.get("ts") or 0)))
        flags_t = [f for f in (d.get("flags") or "").split(",") if f in RISK_WEIGHTS]
        src = d.get("src") or "untagged"  # v2.9: where the request came from; older rows have no label
        groups = ["all", f"v:{v}:{c}", f"rb:{bucket}", f"d:{day}", f"src:{src}", f"srcv:{src}:{v}"]
        groups += [f"f:{f}" for f in flags_t] + [f"fd:{f}:{day}" for f in flags_t]
        base_pred = "avoid" if BASELINE_FLAGS & set((d.get("flags") or "").split(",")) else "ok"
        groups += [f"vd:{v}:{c}:{day}", f"bl:{base_pred}", f"bd:{base_pred}:{day}"]  # per-day verdicts + the simple baseline
        for g in groups:
            cmds.append(["HINCRBY", "st", f"{g}:n", 1])
            if bad:
                cmds.append(["HINCRBY", "st", f"{g}:bad", 1])
        cmds.append(["HINCRBYFLOAT", "st", f"v:{v}:{c}:ret", round(ret, 2)])
        if xret is not None:
            cmds += [["HINCRBYFLOAT", "st", f"v:{v}:{c}:xret", round(xret, 2)], ["HINCRBY", "st", f"v:{v}:{c}:xn", 1]]
        recent.append(json.dumps({"token": d.get("token"), "mint": d["mint"], "verdict": v, "risk_score": float(d.get("score") or 0),
                                  "return_pct": round(ret, 1), "sol_return_pct": round(sol_ret, 1) if sol_ret is not None else None,
                                  "excess_return_pct": round(xret, 1) if xret is not None else None,
                                  "bad_outcome": bad, "age_h": round(age / 3600, 1), "src": src}))
    for item in recent:
        cmds.append(["LPUSH", "recent", item])
    if recent:
        cmds.append(["LTRIM", "recent", 0, 49])
    if cmds:
        await redis_pipe(client, cmds)
    return len(ids)


# ---------- seed candidates: boosts + profiles (DexScreener, paid promotion) plus trending + new pools (GeckoTerminal) ----------
def _add_mints(pool: list, addrs) -> None:
    for addr in addrs:
        if addr and MINT_RE.match(addr) and addr not in MAJOR_ASSETS and addr not in pool:
            pool.append(addr)


async def _seed_candidate_mints(client: httpx.AsyncClient) -> list:
    """Solana mints from several sources, mixed so one seed call does not always draw from the same list (best-effort)."""
    pools: Dict[str, list] = {"boost": [], "profile": [], "trending": [], "new": []}
    for origin, url in (("boost", _SEED_BOOST), ("profile", _SEED_PROFILES)):
        try:
            resp = await client.get(url, timeout=6.0)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not isinstance(data, list):
                continue
            _add_mints(pools[origin], [row.get("tokenAddress") or row.get("token_address") for row in data
                                       if isinstance(row, dict) and (row.get("chainId") or "").lower() == "solana"])
        except Exception:
            continue
    if SEED_GECKO:
        for origin, url in _GECKO_URLS.items():
            try:
                resp = await client.get(url, timeout=6.0, headers={"Accept": "application/json"})
                if resp.status_code != 200:
                    continue
                body = resp.json()
                rows = body.get("data") if isinstance(body, dict) else None
                addrs = []
                for row in rows or []:
                    tid = ((((row.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id") or "")
                    addrs.append(tid.split("_", 1)[1] if tid.startswith("solana_") else "")
                _add_mints(pools[origin], addrs)
            except Exception:
                continue
    names = ["trending", "boost", "new", "profile"]
    k = (_seed_state["count"] + _seed_state["errors"]) % len(names)
    names = names[k:] + names[:k]  # the preferred origin changes from one seed call to the next
    bucket = int(time.time() // SEED_INTERVAL_S)
    for n in names:
        if pools[n]:
            o = bucket % len(pools[n])
            pools[n] = pools[n][o:] + pools[n][:o]
    pairs = [(names[0], m) for m in pools[names[0]][:2]]
    for i in range(max(len(p) for p in pools.values())):
        for n in names:
            if i < len(pools[n]):
                pairs.append((n, pools[n][i]))
    out: list = []
    for n, m in pairs:
        if m not in out:
            out.append(m)
            _SEED_ORIGIN[m] = n
    if len(_SEED_ORIGIN) > 400:
        keep = set(out)
        for m in [x for x in _SEED_ORIGIN if x not in keep]:
            _SEED_ORIGIN.pop(m, None)
    head_n = out[:40]
    if head_n:
        chk = await redis_pipe(client, [["EXISTS", f"plock24:{m}"] for m in head_n])
        if chk and len(chk) == len(head_n):
            out = [m for m, x in zip(head_n, chk) if not int(x or 0)] + out[40:]  # skip mints already measured in the last 24h
    return out


_run_one_seed_v21 = run_one_seed


async def run_one_seed(client: httpx.AsyncClient) -> dict:
    tok = _PRED_SRC.set("seed")  # record_prediction runs in a task created inside this context, so it sees the label
    try:
        return await _run_one_seed_v21(client)
    finally:
        _PRED_SRC.reset(tok)


# ---------- /stats and the MCP track record: accuracy split by where the verdict came from ----------
def _by_source(st: dict) -> dict:
    out: Dict[str, Any] = {}
    for key in sorted(st):
        p = key.split(":")
        if len(p) != 3 or p[0] != "src" or p[2] != "n":
            continue
        s = p[1]
        n, bad = int(st.get(f"src:{s}:n", 0)), int(st.get(f"src:{s}:bad", 0))
        verdicts = {}
        for v in ("ok", "caution", "avoid"):
            vn, vb = int(st.get(f"srcv:{s}:{v}:n", 0)), int(st.get(f"srcv:{s}:{v}:bad", 0))
            verdicts[v] = {"n": vn, "bad_outcome_rate_pct": round(vb / vn * 100, 1) if vn >= MIN_N_SHOW else None}
        out[s] = {"n": n, "bad_outcome_rate_pct": round(bad / n * 100, 1) if n >= MIN_N_SHOW else None,
                  "ci95_pct": [round(x * 100, 1) for x in wilson(bad, n)] if n >= MIN_N_SHOW else None,
                  "verdicts": verdicts}
    return out


_stats_report_v21 = stats_report


async def stats_report(client: httpx.AsyncClient) -> dict:
    rep = await _stats_report_v21(client)
    if rep.get("enabled") and rep.get("available") is not False:
        await refresh_raw_stats(client, force=False)
        note = ("From v2.9: major assets (SOL, USDC, USDT) are no longer judged and each mint is recorded at most once "
                "per 24h. Rows judged earlier are labelled 'untagged' and may include repeats. by_source separates "
                "user requests from seeded samples (seed_boost and seed_profile are paid promotions on DexScreener; "
                "seed_trending and seed_new come from GeckoTerminal).")
        rep = dict(rep, by_source=_by_source(_raw_st["st"]), caveats=list(rep.get("caveats") or []) + [note])
    return rep


print(f"[part22] v{VERSION} track record: majors skipped, 1 verdict/mint/{PRED_LOCK_S // 3600}h, sources labelled (gecko={SEED_GECKO})")
