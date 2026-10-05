# ---------- v2.4: windowed accuracy, baseline comparison, evidence-based context ----------
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).
import calendar

VERSION = "2.4.0"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version

# The simplest honest baseline: "avoid anything with liquidity under $50k or a pair younger than 6 hours".
BASELINE_FLAGS = {"very_low_liquidity", "low_liquidity", "brand_new_pair", "very_new_pair"}
RAW_ST_TTL = 600
_raw_st: Dict[str, Any] = {"ts": 0.0, "st": {}}


def wilson(k: int, n: int, z: float = 1.96):
    """95% confidence interval for a share k/n (Wilson). Honest about small samples."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


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
                                  "bad_outcome": bad, "age_h": round(age / 3600, 1)}))
    for item in recent:
        cmds.append(["LPUSH", "recent", item])
    if recent:
        cmds.append(["LTRIM", "recent", 0, 49])
    if cmds:
        await redis_pipe(client, cmds)
    return len(ids)


async def refresh_raw_stats(client: httpx.AsyncClient, force: bool = True):
    if not PRED_ENABLED:
        return
    if not force and _raw_st["st"] and time.time() - _raw_st["ts"] < RAW_ST_TTL:
        return
    res = await redis_pipe(client, [["HGETALL", "st"]])
    if res:
        _raw_st.update(ts=time.time(), st=to_dict(res[0]))


def _day_in(day: str, span: Optional[int], now: float) -> bool:
    if span is None:
        return True
    try:
        return now - calendar.timegm(time.strptime(day, "%Y%m%d")) <= span * 86400
    except ValueError:
        return False


def _metrics(pos_n: int, pos_bad: int, total: int, total_bad: int) -> dict:
    """What happens if we treat the flagged group as the prediction 'this ends badly'."""
    tp, fp = pos_bad, pos_n - pos_bad
    fn, tn = total_bad - pos_bad, (total - total_bad) - (pos_n - pos_bad)

    def pct(a: int, b: int) -> Optional[float]:
        return round(a / b * 100, 1) if b >= MIN_N_SHOW else None

    ci = wilson(tp, pos_n) if pos_n >= MIN_N_SHOW else None
    return {"flagged": pos_n, "precision_pct": pct(tp, pos_n),
            "precision_ci95_pct": [round(x * 100, 1) for x in ci] if ci else None,
            "recall_pct": pct(tp, total_bad), "false_positive_rate_pct": pct(fp, fp + tn),
            "true_positive": tp, "false_positive": fp, "missed_bad": fn, "true_negative": tn}


def performance_from(st: dict, now: Optional[float] = None) -> dict:
    """Precision / recall / false-positive rate of our verdicts vs a simple baseline, per time window."""
    now = now or time.time()
    out: Dict[str, Any] = {}
    for name, span in (("all_tracked", None), ("last_30d", 30), ("last_7d", 7)):
        ours = {v: [0, 0] for v in ("ok", "caution", "avoid")}
        base = {v: [0, 0] for v in ("ok", "avoid")}
        days = set()
        for k, val in st.items():
            p = k.split(":")
            if len(p) == 5 and p[0] == "vd" and p[4] in ("n", "bad") and p[1] in ours and _day_in(p[3], span, now):
                ours[p[1]][0 if p[4] == "n" else 1] += int(val)
                days.add(p[3])
            elif len(p) == 4 and p[0] == "bd" and p[3] in ("n", "bad") and p[1] in base and _day_in(p[2], span, now):
                base[p[1]][0 if p[3] == "n" else 1] += int(val)
        total, bad = sum(x[0] for x in ours.values()), sum(x[1] for x in ours.values())
        b_total, b_bad = sum(x[0] for x in base.values()), sum(x[1] for x in base.values())
        out[name] = {
            "days_covered": len(days), "judged": total, "bad_outcomes": bad,
            "verdicts": {v: {"n": n, "bad_outcome_rate_pct": round(b / n * 100, 1) if n >= MIN_N_SHOW else None,
                             "ci95_pct": [round(x * 100, 1) for x in wilson(b, n)] if n >= MIN_N_SHOW else None}
                         for v, (n, b) in ours.items()},
            "ours_avoid": _metrics(ours["avoid"][0], ours["avoid"][1], total, bad),
            "ours_avoid_or_caution": _metrics(ours["avoid"][0] + ours["caution"][0], ours["avoid"][1] + ours["caution"][1], total, bad),
            "baseline_avoid": _metrics(base["avoid"][0], base["avoid"][1], b_total, b_bad),
        }
    return out


def calibration_for(r: dict) -> Optional[dict]:
    """What happened to similar past verdicts. NOT the chance that this particular token fails."""
    st = _raw_st["st"]
    if not st:
        return None
    b = min(9, int(r.get("risk_score") or 0) // 10)
    n, bad = int(st.get(f"rb:{b}:n", 0)), int(st.get(f"rb:{b}:bad", 0))
    if n < MIN_N_SHOW:
        return None
    lo, hi = wilson(bad, n)
    return {"basis": f"judged tokens with a risk score of {b * 10}-{b * 10 + 9}", "n": n,
            "bad_outcome_rate": round(bad / n, 3), "ci95": [round(lo, 3), round(hi, 3)],
            "horizon_hours": round(PRED_HORIZON_S / 3600, 1),
            "meaning": "Share of similar past verdicts that ended badly. Not the chance that this token will fail."}


_with_quality_v23 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v23(result, status, security)
    if PRED_ENABLED:
        if time.time() - _raw_st["ts"] > RAW_ST_TTL:
            _raw_st["ts"] = time.time()  # one refresh per interval, in the background
            schedule(refresh_raw_stats(app.state.client))
        ctx = calibration_for(out)
        if ctx:
            out["track_record_context"] = ctx
    return out


_compact_v23 = compact_signal


def compact_signal(r: dict) -> dict:
    out = _compact_v23(r)
    ctx = r.get("track_record_context")
    if ctx:
        out["track_record_context"] = {k: ctx[k] for k in ("n", "bad_outcome_rate", "ci95")}
    return out


_stats_report_v23 = stats_report


async def stats_report(client: httpx.AsyncClient) -> dict:
    rep = await _stats_report_v23(client)
    if rep.get("enabled") and rep.get("available") is not False:
        await refresh_raw_stats(client, force=False)
        rep = dict(rep, performance=performance_from(_raw_st["st"]))
    return rep
