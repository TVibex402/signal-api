# ---------- part24: v2.11.0 - shadow rules and early checks: learn faster, compare other rules on the same tokens ----------
# Loads before part7 (MCP). Every new verdict also stores what five alternative rules would have said, and is judged
# again after 1h and 6h (same definition of a bad outcome) besides the usual 24h. Live answers are not changed.
VERSION = "2.11.0"
app.version = VERSION
app.openapi_schema = None

SHADOW_ENABLED = (os.getenv("SHADOW_ENABLED") or "true").lower() in ("1", "true", "yes")


def _early_hours() -> list:
    out = []
    for part in (os.getenv("EARLY_HOURS") if os.getenv("EARLY_HOURS") is not None else "1,6").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 23 and int(part) not in out:
            out.append(int(part))
    return sorted(out)


_EARLY = [(h, f"due_h{h}") for h in _early_hours()]  # EARLY_HOURS="" switches the early checks off (saves Redis commands)
_VERDICTS = ("ok", "caution", "avoid")


def _rule_baseline(f: set, s: float, liq: float) -> str:
    return "avoid" if BASELINE_FLAGS & f else "ok"


def _rule_graded_liq_age(f: set, s: float, liq: float) -> str:
    if {"very_low_liquidity", "brand_new_pair"} & f:
        return "avoid"
    return "caution" if {"low_liquidity", "very_new_pair"} & f else "ok"


def _rule_score_35(f: set, s: float, liq: float) -> str:
    return "avoid" if s >= 35 else "caution" if s >= 15 else "ok"


def _rule_score_only(f: set, s: float, liq: float) -> str:
    return "avoid" if s >= 50 else "caution" if s >= 25 else "ok"  # the live thresholds without the hard flag overrides


def _rule_liq_5k(f: set, s: float, liq: float) -> str:
    return "avoid" if liq < 5000 else "caution" if liq < 20000 else "ok"


SHADOW_RULES = {"baseline": _rule_baseline, "graded_liq_age": _rule_graded_liq_age, "score_35": _rule_score_35,
                "score_only": _rule_score_only, "liq_5k": _rule_liq_5k}


def _shadow_str(r: dict) -> str:
    """'live=caution,baseline=ok,...' for one verdict. Never raises."""
    if not SHADOW_ENABLED:
        return ""
    try:
        f = set(r.get("flags") or [])
        s, liq = float(r.get("risk_score") or 0), float(r.get("liquidity_usd") or 0)
        pairs = [("live", r.get("verdict") if r.get("verdict") in _VERDICTS else "caution")]
        pairs += [(name, fn(f, s, liq)) for name, fn in SHADOW_RULES.items()]
        return ",".join(f"{a}={b}" for a, b in pairs)
    except Exception:  # noqa: BLE001
        return ""


def _parse_shadow(text: Any) -> list:
    out = []
    for item in (text or "").split(","):
        name, _, verdict = item.partition("=")
        if name and re.fullmatch(r"[a-z0-9_]+", name) and verdict in _VERDICTS:
            out.append((name, verdict))
    return out


def _early_zadds(pid: str, now: int) -> list:
    return [["ZADD", zset, now + hours * 3600, pid] for hours, zset in _EARLY]


async def record_prediction(client: httpx.AsyncClient, r: dict):
    """Store one verdict to be judged later. Skips major assets, one per mint per 24h, MAX_PRED_PER_DAY per day.
    (v2.11: also stores what the shadow rules would have said and schedules the 1h / 6h checks)"""
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
        shadow_str = _shadow_str(r)
        await redis_pipe(client, [
            ["HSET", f"pred:{pid}", "mint", mint, "token", r.get("token") or "", "ts", now, "price", price,
             "liq", r.get("liquidity_usd") or 0, "verdict", r["verdict"], "conf", r["verdict_confidence"],
             "score", r["risk_score"], "flags", ",".join(r.get("flags") or []), "sol", sol or "", "src", src, "shadow", shadow_str],
            ["EXPIRE", f"pred:{pid}", PRED_HORIZON_S * 4],
            ["ZADD", "due", now + PRED_HORIZON_S, pid],
        ] + _early_zadds(pid, now))
        await ledger_append(client, {
            "id": pid, "ts": now, "mint": mint, "token": r.get("token") or "", "verdict": r["verdict"],
            "risk_score": r["risk_score"], "price_usd": price, "liquidity_usd": r.get("liquidity_usd") or 0,
            "flags": sorted(r.get("flags") or []), "version": VERSION, "src": src,
        })
    except Exception:
        pass


async def resolve_due(client: httpx.AsyncClient) -> int:
    """Judge predictions whose time has come. Returns how many were processed. (v2.9: skips major assets, labels the source; v2.11: counts the shadow rules)"""
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
        groups += [f"sh:{rn}:{sv}" for rn, sv in _parse_shadow(d.get("shadow"))]  # v2.11: what the other rules would have said
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


_resolve_due_24h = resolve_due


async def _resolve_early_one(client: httpx.AsyncClient, hours: int, zset: str) -> int:
    """Judge predictions that reached the `hours` mark. Same bad-outcome rule as at 24h; the prediction is kept for 24h."""
    now = int(time.time())
    got = await redis_pipe(client, [["ZRANGEBYSCORE", zset, "-inf", now, "LIMIT", 0, RESOLVE_BATCH]])
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
            cmds.append(["ZREM", zset, pid])  # hash gone (judged at 24h already, or expired): drop the entry
    best: Dict[str, Optional[dict]] = {}
    if preds:
        mints = sorted({d["mint"] for d in preds.values()})
        t0 = time.time()
        try:
            resp = await client.get(DEX_URL.format(mint=",".join(mints)))
            resp.raise_for_status()
            pairs = resp.json().get("pairs") or []
            src_log("dexscreener", True, t0)
        except (httpx.HTTPError, ValueError):
            src_log("dexscreener", False, t0)
            if cmds:
                await redis_pipe(client, cmds)
            return 0  # try again next cycle
        for m in mints:
            best[m] = pick_best_pair([p for p in pairs if (p.get("baseToken") or {}).get("address") == m], m)
    pre = f"h{hours}"
    for pid, d in preds.items():
        cmds.append(["ZREM", zset, pid])
        if d["mint"] in MAJOR_ASSETS:
            continue
        age = now - int(d.get("ts") or 0)
        if age > hours * 3600 * 3:
            continue  # judged far too late: would distort the numbers
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
        v = d.get("verdict", "?")
        groups = ["all", f"v:{v}", f"src:{d.get('src') or 'untagged'}"]
        groups += [f"sh:{rn}:{sv}" for rn, sv in _parse_shadow(d.get("shadow"))]
        for g in groups:
            cmds.append(["HINCRBY", "sth", f"{pre}:{g}:n", 1])
            if bad:
                cmds.append(["HINCRBY", "sth", f"{pre}:{g}:bad", 1])
        cmds.append(["HINCRBYFLOAT", "sth", f"{pre}:v:{v}:ret", round(ret, 2)])
    if cmds:
        await redis_pipe(client, cmds)
    return len(ids)


async def resolve_due(client: httpx.AsyncClient) -> int:
    """24h judgments as before, then the early (1h / 6h) ones. Returns how many predictions were processed."""
    n = await _resolve_due_24h(client)
    for hours, zset in _EARLY:
        try:
            n += await _resolve_early_one(client, hours, zset)
        except Exception:  # noqa: BLE001
            pass  # the early checks must never break the 24h track record
    return n


# ---------- /stats and the MCP track record: shadow rules and early horizons ----------
def _cell(n: int, bad: int) -> dict:
    return {"n": n, "bad_outcome_rate_pct": round(bad / n * 100, 1) if n >= MIN_N_SHOW else None,
            "ci95_pct": [round(x * 100, 1) for x in wilson(bad, n)] if n >= MIN_N_SHOW else None}


def _shadow_table(st: dict, pre: str) -> dict:
    """Per rule: how its avoid / caution / ok groups did, on the same judged tokens. pre is '' (24h) or 'h1:' ..."""
    names = set()
    for key in st:
        if key.startswith(f"{pre}sh:") and key.endswith(":n"):
            names.add(key[len(pre) + 3:-2].rpartition(":")[0])
    rules: Dict[str, Any] = {}
    for name in sorted(names, key=lambda x: (x != "live", x)):
        cells = {v: (int(st.get(f"{pre}sh:{name}:{v}:n", 0)), int(st.get(f"{pre}sh:{name}:{v}:bad", 0))) for v in _VERDICTS}
        judged, bad = sum(c[0] for c in cells.values()), sum(c[1] for c in cells.values())
        an, ab = cells["avoid"]
        rules[name] = {"judged": judged, "bad": bad, **{v: _cell(*cells[v]) for v in _VERDICTS},
                       "avoid_precision_pct": round(ab / an * 100, 1) if an >= MIN_N_SHOW else None,
                       "avoid_recall_pct": round(ab / bad * 100, 1) if judged >= MIN_N_SHOW and bad > 0 else None}
    return rules


_sth_cache: Dict[str, Any] = {"ts": 0.0, "v": {}}


async def _get_sth(client: httpx.AsyncClient) -> dict:
    if time.time() - _sth_cache["ts"] > 120:
        got = await redis_pipe(client, [["HGETALL", "sth"]])
        if got is not None:
            _sth_cache.update(ts=time.time(), v=to_dict(got[0]))
    return _sth_cache["v"]


_stats_report_v22 = stats_report


async def stats_report(client: httpx.AsyncClient) -> dict:
    rep = await _stats_report_v22(client)
    if not (rep.get("enabled") and rep.get("available") is not False):
        return rep
    note = ("Shadow rules (v2.11): each new verdict also records what 5 alternative rules would have said, so every rule is "
            "judged on the same tokens (only verdicts made since v2.11 are included). 'live' is the real verdict. "
            "Early horizons judge the same verdicts after 1h and 6h with the same definition of a bad outcome. "
            "Rates stay hidden under 20 samples per group; do not tune anything from small numbers.")
    extra: Dict[str, Any] = {}
    if SHADOW_ENABLED:
        extra["shadow_rules"] = {"horizon_hours": round(PRED_HORIZON_S / 3600, 1), "rules": _shadow_table(_raw_st["st"], "")}
    if _EARLY:
        sth = await _get_sth(client)
        early: Dict[str, Any] = {}
        for hours, _ in _EARLY:
            pre = f"h{hours}:"
            n, bad = int(sth.get(f"{pre}all:n", 0)), int(sth.get(f"{pre}all:bad", 0))
            early[f"{hours}h"] = {**_cell(n, bad), "by_verdict": {v: _cell(int(sth.get(f"{pre}v:{v}:n", 0)), int(sth.get(f"{pre}v:{v}:bad", 0))) for v in _VERDICTS},
                                  "shadow_rules": _shadow_table(sth, pre) if SHADOW_ENABLED else {}}
        extra["early_horizons"] = early
    return dict(rep, **extra, caveats=list(rep.get("caveats") or []) + [note])


print(f"[part24] v{VERSION} shadow rules ({'on' if SHADOW_ENABLED else 'off'}: {', '.join(SHADOW_RULES)}), early checks at {[h for h, _ in _EARLY] or 'none'}h")
