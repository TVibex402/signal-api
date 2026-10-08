# ---------- v2.6: Holder Quality + Dip Absorption ----------
# Không override with_quality nữa để tránh đệ quy với part19.
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
    hq = round(0.40 * conc + 0.35 * stab + 0.25 * abso)

    flags = []
    if stab >= 72:
        flags.append("holders_stable")
    elif stab <= 38 and prev_tops:
        flags.append("holders_churning")

    if prev_tops and curr_tops:
        prev_map = {o: p for o, p in prev_tops}
        gained = sum(max(0.0, p - prev_map.get(o, 0.0)) for o, p in curr_tops)
        lost = sum(max(0.0, prev_map.get(o, 0.0) - p) for o, p in curr_tops)
        if gained >= 8.0 and gained > lost * 1.4:
            flags.append("whale_accumulating")
        elif lost >= 8.0 and lost > gained * 1.4:
            flags.append("whale_dumping")

    if abso >= 78:
        flags.append("dip_absorbed")
    elif abso <= 28:
        flags.append("weak_absorption")

    return {
        "score": hq,
        "flags": flags,
        "concentration": round(conc, 1),
        "stability": round(stab, 1),
        "absorption": round(abso, 1),
        "top_owners": curr_tops,
    }


async def _load_prev_tops(client, mint: str):
    if not PRED_ENABLED or not client:
        return None
    try:
        raw = await redis_get(client, HOLDER_CACHE_KEY.format(mint))
        if raw:
            import json
            return json.loads(raw)
    except Exception:
        pass
    return None


async def _save_curr_tops(client, mint: str, tops):
    if not PRED_ENABLED or not client or not tops:
        return
    try:
        import json
        await redis_set(client, HOLDER_CACHE_KEY.format(mint), json.dumps(tops), HOLDER_CACHE_TTL)
    except Exception:
        pass


async def attach_holder_quality(client, mint: str, result: dict):
    """Gọi sau khi có security data. An toàn, không đệ quy."""
    prev = await _load_prev_tops(client, mint)
    hq = compute_holder_quality(result, prev)
    result["holder_quality"] = {
        "score": hq["score"],
        "flags": hq["flags"],
        "concentration": hq["concentration"],
        "stability": hq["stability"],
        "absorption": hq["absorption"],
    }
    for f in hq["flags"]:
        if f not in result.get("flags", []):
            result.setdefault("flags", []).append(f)
    await _save_curr_tops(client, mint, hq["top_owners"])
    return result
# ---------- gắn holder_quality vào pipeline chính ----------
_build_signal_v26 = build_signal


async def build_signal(client: httpx.AsyncClient, mint: str, security: bool, prefetched: Optional[dict] = None):
    result, status = await _build_signal_v26(client, mint, security, prefetched)
    if security and result.get("security"):
        try:
            result = await attach_holder_quality(client, mint, result)
        except Exception:
            pass  # không làm hỏng signal nếu Redis lỗi
    return result, status