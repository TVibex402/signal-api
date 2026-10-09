# ---------- part23: v2.10.0 - upstream canary: is each market source alive, readable and in agreement? ----------
# Loads before part7 (MCP). part19 already falls back to GeckoTerminal when DexScreener refuses; this part makes that
# visible and checked: a small probe (SOL) every CANARY_EVERY_S compares both sources and the shape of their answers.
VERSION = "2.10.0"
app.version = VERSION
app.openapi_schema = None

CANARY_EVERY_S = max(300, int(os.getenv("CANARY_EVERY_S", "900")))
CANARY_MAX_DIFF_PCT = float(os.getenv("CANARY_MAX_DIFF_PCT", "3"))
# Off by default: when on, a token DexScreener has no pair for is also looked up on GeckoTerminal (lower confidence).
GT_FILL_MISSING = (os.getenv("GT_FILL_MISSING") or "false").lower() in ("1", "true", "yes")

_PAIR_KEYS = ("priceUsd", "liquidity", "baseToken", "pairAddress", "dexId")
_canary: Dict[str, Any] = {"ts": 0.0, "running": False, "status": "unknown", "price_diff_pct": None,
                           "dexscreener": None, "geckoterminal": None,
                           "streak": {"dexscreener": 0, "geckoterminal": 0}}


def _pair_problems(pair: Any) -> list:
    """What is wrong with a pair, judged by the fields the rest of the code needs. Empty list = readable."""
    if not isinstance(pair, dict):
        return ["no pair returned"]
    problems = [f"missing {k}" for k in _PAIR_KEYS if pair.get(k) in (None, "", {})]
    liq = pair.get("liquidity")
    if not isinstance(liq, dict) or not isinstance(liq.get("usd"), (int, float)):
        problems.append("missing liquidity.usd")
    try:
        if float(pair.get("priceUsd")) <= 0:
            problems.append("priceUsd is not positive")
    except (TypeError, ValueError):
        if "missing priceUsd" not in problems:
            problems.append("priceUsd is not a number")
    return problems


def _probe_result(pair: Any, t0: float, why: str = "") -> dict:
    problems = _pair_problems(pair) if not why else [why]
    price = None
    if not problems:
        price = float(pair["priceUsd"])
    return {"ok": not problems, "price": price, "problems": problems, "ms": round((time.time() - t0) * 1000)}


async def _probe_dex(client: httpx.AsyncClient) -> dict:
    t0 = time.time()
    try:
        data = await _fetch_dex_direct(client, SOL_MINT)
    except HTTPException as exc:
        return _probe_result(None, t0, f"unavailable ({exc.status_code})")
    except Exception as exc:  # noqa: BLE001
        return _probe_result(None, t0, f"error: {str(exc)[:80]}")
    pairs = data.get("pairs") if isinstance(data, dict) else None
    return _probe_result(pick_best_pair(pairs or [], SOL_MINT), t0)


async def _probe_gt(client: httpx.AsyncClient) -> dict:
    t0 = time.time()
    try:
        pairs = await _gt_fetch(client, SOL_MINT)
    except Exception as exc:  # noqa: BLE001
        return _probe_result(None, t0, f"error: {str(exc)[:80]}")
    if not pairs:
        return _probe_result(None, t0, "no data (cooling down, unreachable or unreadable)")
    return _probe_result(pick_best_pair(pairs, SOL_MINT), t0)


async def run_canary(client: httpx.AsyncClient) -> None:
    if _canary["running"]:
        return
    _canary["running"] = True
    try:
        dex, gt = await _probe_dex(client), await _probe_gt(client)
        for name, res in (("dexscreener", dex), ("geckoterminal", gt)):
            _canary["streak"][name] = 0 if res["ok"] else _canary["streak"][name] + 1
        diff = abs(dex["price"] - gt["price"]) / dex["price"] * 100 if dex["ok"] and gt["ok"] else None
        if dex["ok"] and gt["ok"]:
            status = "ok" if diff <= CANARY_MAX_DIFF_PCT else "price_mismatch"
        elif dex["ok"]:
            status = "fallback_unavailable"
        elif gt["ok"]:
            status = "primary_down_fallback_ok"
        else:
            status = "both_down"
        _canary.update(ts=time.time(), status=status, dexscreener=dex, geckoterminal=gt,
                       price_diff_pct=round(diff, 2) if diff is not None else None)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _canary["running"] = False


def _canary_view() -> dict:
    ts = _canary["ts"]
    if not ts:
        return {"status": "unknown", "detail": "no check has run yet since this server started"}

    def side(name: str) -> dict:
        r = _canary.get(name) or {}
        return {"ok": r.get("ok"), "problems": r.get("problems") or [], "failed_checks_in_a_row": _canary["streak"][name]}

    return {"status": _canary["status"], "checked_s_ago": int(time.time() - ts), "every_s": CANARY_EVERY_S,
            "price_diff_pct": _canary["price_diff_pct"], "max_diff_pct": CANARY_MAX_DIFF_PCT,
            "basis": "SOL priced by both sources, plus a shape check of each answer",
            "dexscreener": side("dexscreener"), "geckoterminal": side("geckoterminal")}


# GeckoTerminal shows up next to the other sources, judged by the canary (a normal 404 for an unknown token is not a failure)
_src_status_v4 = src_status


def src_status() -> dict:
    out = _src_status_v4()
    age = time.time() - _canary["ts"] if _canary["ts"] else None
    g = _canary.get("geckoterminal")
    if g and age is not None and age < CANARY_EVERY_S * 4:
        out["geckoterminal"] = {"status": "ok" if g["ok"] else "down", "basis": "canary", "checked_s_ago": int(age)}
    else:
        out["geckoterminal"] = {"status": "unknown"}
    return out


# Optional: also use GeckoTerminal for tokens DexScreener does not know (yet).
_fetch_dex_v19 = fetch_dex


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
    data = await _fetch_dex_v19(client, mint)
    if GT_FILL_MISSING and mint not in MAJOR_ASSETS and not (isinstance(data, dict) and data.get("pairs")):
        pairs = await _gt_fetch(client, mint)
        if pairs:
            _FALLBACK_MINTS[mint] = time.time()  # lowers the confidence and says so, exactly like the 429 fallback
            return {"pairs": pairs}
    return data


# /health: the canary and the fallback state
_drop_route("/health")
_health_v21 = health


@app.get("/health", tags=["ops"], summary="Service status and what is switched on")
async def health():
    body = await _health_v21()
    if isinstance(body, dict):
        now = time.time()
        body = dict(body, upstream=_canary_view(), fallback={
            "geckoterminal_serving_now": sum(1 for t in _FALLBACK_MINTS.values() if now - t < 600),
            "geckoterminal_cooldown_s": max(0, int(_GT_COOLDOWN["until"] - now)),
            "fill_missing_tokens": GT_FILL_MISSING,
        })
    return body


# /ping: start a check in the background when one is due (the cron you already have is enough)
_drop_route("/ping")
_ping_v22 = ping


@app.get("/ping", include_in_schema=False)
async def ping():
    body = await _ping_v22() if asyncio.iscoroutinefunction(_ping_v22) else _ping_v22()
    if isinstance(body, dict):
        if time.time() - _canary["ts"] >= CANARY_EVERY_S and not _canary["running"]:
            client = getattr(app.state, "client", None)
            if client is not None:
                schedule(run_canary(client))
        body["upstream"] = _canary["status"]
    return body


print(f"[part23] v{VERSION} upstream canary ready (every {CANARY_EVERY_S}s, max price gap {CANARY_MAX_DIFF_PCT}%, fill_missing={GT_FILL_MISSING})")
