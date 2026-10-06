# ---------- v2.6: Holder Quality + Dip Absorption ----------
# Lightweight holder conviction + dip absorption signals.
# Uses existing security holders + market data. Caches top holders in Redis when available.
# Loads before part7 (MCP).

VERSION = "2.6.0"
app.version = VERSION
app.openapi_schema = None

# --- new reasons & weights ---
REASONS["holders_stable"] = "Top holders largely unchanged over recent window (conviction signal)"
REASONS["holders_churning"] = "Significant turnover among top holders"
REASONS["whale_accumulating"] = "Largest holders appear to be adding"
REASONS["whale_dumping"] = "Largest holders appear to be reducing"
REASONS["dip_absorbed"] = "Price drop met with solid buy pressure (absorption)"
REASONS["weak_absorption"] = "Price drop with weak buy support"

RISK_WEIGHTS.setdefault("holders_churning", 14)
RISK_WEIGHTS.setdefault("whale_dumping", 18)
RISK_WEIGHTS.setdefault("weak_absorption", 11)
RISK_WEIGHTS.setdefault("holders_stable", 0)      # positive only
RISK_WEIGHTS.setdefault("whale_accumulating", 0)  # positive only
RISK_WEIGHTS.setdefault("dip_absorbed", 0)        # positive only

for _f, _w in (("holders_churning", 14), ("whale_dumping", 18), ("weak_absorption", 11),
               ("holders_stable", 0), ("whale_accumulating", 0), ("dip_absorbed", 0)):
    ACTIVE_WEIGHTS.setdefault(_f, _w)

if "holders_churning" not in CAUTION_FLAGS:
    CAUTION_FLAGS.append("holders_churning")
if "whale_dumping" not in CAUTION_FLAGS:
    CAUTION_FLAGS.append("whale_dumping")
if "weak_absorption" not in CAUTION_FLAGS:
    CAUTION_FLAGS.append("weak_absorption")

HOLDER_CACHE_TTL = 8 * 3600  # 8 hours
HOLDER_CACHE_KEY = "hq:{}"


def _top_owners(sec: dict, n: int = 5) -> list:
    """Return list of (owner, pct) for top n real holders."""
    holders = sec.get("top_holders") or []
    out = []
    for h in holders:
        owner = h.get("owner") or h.get("address")
        pct = h.get("pct") or h.get("percentage")
        if owner and pct is not None and owner not in KNOWN_NON_HOLDERS:
            try:
                out.append((owner, float(pct)))
            except (TypeError, ValueError):
                continue
        if len(out) >= n:
            break
    return out


def _concentration_score(top10: Optional[float], top1: Optional[float]) -> float:
    if top10 is None:
        return 50.0
    if top10 >= 80:
        return 12.0
    if top10 >= 60:
        return 35.0
    if top10 >= 45:
        return 55.0
    if top10 >= 30:
        return 75.0
    return 92.0


def _stability_score(curr: list, prev: list) -> float:
    """Overlap of supply still held by the same top owners."""
    if not curr or not prev:
        return 50.0
    prev_map = {o: p for o, p in prev}
    shared = 0.0
    for o, p in curr:
        if o in prev_map:
            shared += min(p, prev_map[o])
    # shared is % of supply still with same wallets
    return max(0.0, min(100.0, shared * 1.15))


def _absorption_score(result: dict) -> float:
    pc = result.get("price_change_pct") or {}
    h1 = pc.get("1h") or 0
    h6 = pc.get("6h") or 0
    bp = result.get("buy_pressure_5m")
    trades = (result.get("buys_5m") or 0) + (result.get("sells_5m") or 0)

    if bp is None or trades < 8:
        return 50.0

    # meaningful dip
    if h1 <= -12 or h6 <= -20:
        if bp >= 0.58:
            return 88.0
        if bp >= 0.45:
            return 68.0
        if bp <= 0.32:
            return 22.0
        return 40.0
    return 55.0


async def _load_prev_holders(client: httpx.AsyncClient, mint: str) -> Optional[list]:
    if not PRED_ENABLED:
        return None
    try:
        res = await redis_pipe(client, [["GET", HOLDER_CACHE_KEY.format(mint)]])
        raw = (res or [None])[0]
        if not raw:
            return None
        data = json.loads(raw)
        return data.get("tops")
    except Exception:
        return None


async def _save_holders(client: httpx.AsyncClient, mint: str, tops: list):
    if not PRED_ENABLED or not tops:
        return
    try:
        payload = json.dumps({"ts": int(time.time()), "tops": tops})
        await redis_pipe(client, [["SET", HOLDER_CACHE_KEY.format(mint), payload, "EX", HOLDER_CACHE_TTL]])
    except Exception:
        pass


def compute_holder_quality(result: dict, prev_tops: Optional[list] = None) -> dict:
    sec = result.get("security") or {}
    top10 = sec.get("top10_holders_pct")
    top1 = sec.get("top1_holder_pct")
    curr_tops = _top_owners(sec, 5)

    conc = _concentration_score(top10, top1)
    stab = _stability_score(curr_tops, prev_tops or [])
    abso = _absorption_score(result)

    hq = round(0.40 * conc + 0.35 * stab + 0.25 * abso)

    return {
        "holder_quality": hq,
        "concentration_score": round(conc, 1),
        "stability_score": round(stab, 1),
        "absorption_score": round(abso, 1),
        "curr_tops": curr_tops,
    }


def add_holder_flags(result: dict, hq_data: dict, prev_tops: Optional[list]):
    flags = result.setdefault("flags", [])
    hq = hq_data["holder_quality"]
    stab = hq_data["stability_score"]
    abso = hq_data["absorption_score"]
    curr = hq_data.get("curr_tops") or []

    # stability flags
    if prev_tops and stab >= 72:
        if "holders_stable" not in flags:
            flags.append("holders_stable")
    elif prev_tops and stab <= 35:
        if "holders_churning" not in flags:
            flags.append("holders_churning")

    # whale direction (very light heuristic)
    if prev_tops and curr:
        prev_map = {o: p for o, p in prev_tops}
        delta = 0.0
        for o, p in curr[:3]:
            delta += p - prev_map.get(o, 0)
        if delta >= 4.5:
            if "whale_accumulating" not in flags:
                flags.append("whale_accumulating")
        elif delta <= -4.5:
            if "whale_dumping" not in flags:
                flags.append("whale_dumping")

    # absorption
    if abso >= 75:
        if "dip_absorbed" not in flags:
            flags.append("dip_absorbed")
    elif abso <= 28:
        if "weak_absorption" not in flags:
            flags.append("weak_absorption")


_finalize_risk_v25 = finalize_risk


def finalize_risk(result: dict, onchain: bool):
    # run previous logic first
    _finalize_risk_v25(result, onchain)

    # holder quality is added later in with_quality (needs async cache)
    # we only prepare the synchronous part here if needed


_with_quality_v25 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v25(result, status, security)

    # We cannot await here. Schedule a background enrichment if possible,
    # but for the response we compute what we can synchronously.
    sec = out.get("security") or {}
    hq_data = compute_holder_quality(out, prev_tops=None)  # no prev on first pass
    out["holder_quality"] = hq_data["holder_quality"]
    out["holder_quality_detail"] = {
        "concentration": hq_data["concentration_score"],
        "stability": hq