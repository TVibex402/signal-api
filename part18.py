# ---------- v2.6: Holder Quality + Dip Absorption ----------
VERSION = "2.6.0"
app.version = VERSION
app.openapi_schema = None

REASONS["holders_stable"] = "Top holders largely unchanged (conviction)"
REASONS["holders_churning"] = "Significant turnover among top holders"
REASONS["whale_accumulating"] = "Largest holders appear to be adding"
REASONS["whale_dumping"] = "Largest holders appear to be reducing"
REASONS["dip_absorbed"] = "Price drop met with solid buy pressure"
REASONS["weak_absorption"] = "Price drop with weak buy support"

for flag, weight in (
    ("holders_churning", 14),
    ("whale_dumping", 18),
    ("weak_absorption", 11),
    ("holders_stable", 0),
    ("whale_accumulating", 0),
    ("dip_absorbed", 0),
):
    RISK_WEIGHTS.setdefault(flag, weight)
    ACTIVE_WEIGHTS.setdefault(flag, weight)

for flag in ("holders_churning", "whale_dumping", "weak_absorption"):
    if flag not in CAUTION_FLAGS:
        CAUTION_FLAGS.append(flag)

HOLDER_CACHE_TTL = 8 * 3600
HOLDER_CACHE_KEY = "hq:{}"


def _top_owners(sec, n=5):
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


def _concentration_score(top10):
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


def _stability_score(curr, prev):
    if not curr or not prev:
        return 50.0
    prev_map = {o: p for o, p in prev}
    shared = 0.0
    for o, p in curr:
        if o in prev_map:
            shared += min(p, prev_map[o])
    return max(0.0, min(100.0, shared * 1.15))


def _absorption_score(result):
    pc = result.get("price_change_pct") or {}
    h1 = pc.get("1h") or 0
    h6 = pc.get("6h") or 0
    bp = result.get("buy_pressure_5m")
    trades = (result.get("buys_5m") or 0) + (result.get("sells_5m") or 0)
    if bp is None or trades < 8:
        return 50.0
    if h1 <= -12 or h6 <= -20:
        if bp >= 0.58:
            return 88.0
        if bp >= 0.45:
            return 68.0
        if bp <= 0.32:
            return 22.0
        return 40.0
    return 55.0


def compute_holder_quality(result, prev_tops=None):
    sec = result.get("security") or {}
    top10 = sec.get("top10_holders_pct")
    curr_tops = _top_owners(sec, 5)
    conc = _concentration_score(top10)
    stab = _stability_score(curr_tops, prev_tops or [])
    abso = _absorption_score(result)
    hq = round