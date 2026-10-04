# ---------- self-calibration: track record, prediction error, source health ----------
# Every verdict is a prediction. About 24h later we measure what happened; the gap between
# prediction and outcome ("prediction error") feeds public stats and, optionally, the risk weights.
REDIS_URL = (os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
PRED_ENABLED = bool(REDIS_URL and REDIS_TOKEN)
PRED_HORIZON_S = int(float(os.getenv("PRED_HORIZON_HOURS", "24")) * 3600)
MAX_PRED_PER_DAY = int(os.getenv("MAX_PRED_PER_DAY", "300"))  # keeps Upstash usage well inside the free tier
AUTO_TUNE = os.getenv("AUTO_TUNE", "").lower() in ("1", "true", "yes")  # OFF by default
BAD_RETURN_PCT = -50.0   # "bad outcome": price down 50%+ ...
BAD_LIQ_RATIO = 0.3      # ... or liquidity down 70%+ (or the pair vanished)
MIN_N_SHOW = 20          # never show rates from fewer than 20 samples
STATS_TTL = 600
SOL_MINT = "So11111111111111111111111111111111111111112"
RESOLVE_BATCH = 25       # predictions judged per cycle (+1 slot for the SOL baseline price)
MIN_DAY_N = 5            # a day needs this many resolved predictions to count as a market-background cohort

ACTIVE_WEIGHTS: Dict[str, int] = dict(RISK_WEIGHTS)
_tune: Dict[str, Any] = {"on": False, "n": 0}
_pred_day: Dict[str, Any] = {"day": "", "count": 0}
_stats_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

# ----- source health (in memory) -----
_src: Dict[str, deque] = {}
SRC_WINDOW_S = 600


def src_log(name: str, ok: bool, t0: float):
    _src.setdefault(name, deque(maxlen=40)).append((time.time(), bool(ok), (time.time() - t0) * 1000))


def src_status() -> dict:
    now, out = time.time(), {}
    for name in ("dexscreener", "solana_rpc", "rugcheck", "jupiter"):
        recent = [x for x in _src.get(name, ()) if now - x[0] <= SRC_WINDOW_S]
        if not recent:
            out[name] = {"status": "unknown"}
            continue
        okr = sum(1 for x in recent if x[1]) / len(recent)
        out[name] = {"status": "ok" if okr >= 0.8 else "degraded" if okr >= 0.4 else "down",
                     "success_rate_pct": round(okr * 100), "avg_ms": round(sum(x[2] for x in recent) / len(recent)),
                     "samples": len(recent)}
    return out


# ----- Upstash Redis over REST (no extra dependency) -----
async def redis_pipe(client: httpx.AsyncClient, cmds: list) -> Optional[list]:
    if not PRED_ENABLED:
        return None
    try:
        resp = await client.post(
            REDIS_URL + "/pipeline",
            json=[[str(x) for x in c] for c in cmds],
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            timeout=5.0,
        )
        resp.raise_for_status()
        return [(x.get("result") if isinstance(x, dict) else None) for x in resp.json()]
    except Exception:
        return None


def to_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return dict(zip(v[0::2], v[1::2]))
    return {}


def schedule(coro):
    t = asyncio.ensure_future(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


_sol: Dict[str, Any] = {"ts": 0.0, "price": None}


async def sol_price(client: httpx.AsyncClient) -> Optional[float]:
    """SOL/USD, cached 60s. This is the 'background expansion' every token rides on."""
    if _sol["price"] and time.time() - _sol["ts"] < 60:
        return _sol["price"]
    price = None
    try:
        resp = await client.get(DEX_URL.format(mint=SOL_MINT))
        resp.raise_for_status()
        pair = pick_best_pair([p for p in (resp.json().get("pairs") or [])
                               if (p.get("baseToken") or {}).get("address") == SOL_MINT], SOL_MINT)
        price = float(pair.get("priceUsd")) if pair else None
    except (httpx.HTTPError, ValueError, TypeError):
        price = None
    if price and price > 0:
        _sol.update(ts=time.time(), price=price)
        return price
    return None


async def record_prediction(client: httpx.AsyncClient, r: dict):
    """Store one verdict to be judged later. At most one per mint per 6h and MAX_PRED_PER_DAY per day."""
    try:
        price = float(r.get("price_usd") or 0)
        mint = r.get("mint")
        if price <= 0 or not mint:
            return
        today = time.strftime("%Y%m%d", time.gmtime())
        if _pred_day["day"] != today:
            _pred_day.update(day=today, count=0)
        if _pred_day["count"] >= MAX_PRED_PER_DAY:
            return
        lock = await redis_pipe(client, [["SET", f"plock:{mint}", "1", "EX", 21600, "NX"]])
        if not lock or lock[0] != "OK":
            return
        _pred_day["count"] += 1
        now = int(time.time())
        pid = f"{mint}:{now}"
        sol = await sol_price(client)
        await redis_pipe(client, [
            ["HSET", f"pred:{pid}", "mint", mint, "token", r.get("token") or "", "ts", now, "price", price,
             "liq", r.get("liquidity_usd") or 0, "verdict", r["verdict"], "conf", r["verdict_confidence"],
             "score", r["risk_score"], "flags", ",".join(r.get("flags") or []), "sol", sol or ""],
            ["EXPIRE", f"pred:{pid}", PRED_HORIZON_S * 4],
            ["ZADD", "due", now + PRED_HORIZON_S, pid],
        ])
    except Exception:
        pass


async def resolve_due(client: httpx.AsyncClient) -> int:
    """Judge predictions whose time has come. Returns how many were processed."""
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
        groups = ["all", f"v:{v}:{c}", f"rb:{bucket}", f"d:{day}"]
        groups += [f"f:{f}" for f in flags_t] + [f"fd:{f}:{day}" for f in flags_t]
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
                                  "bad_outcome": bad, "age_h": round(age / 3600, 1)}))
    for item in recent:
        cmds.append(["LPUSH", "recent", item])
    if recent:
        cmds.append(["LTRIM", "recent", 0, 49])
    if cmds:
        await redis_pipe(client, cmds)
    return len(ids)
