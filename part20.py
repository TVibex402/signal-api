# ---------- v2.6.3: 429 cooldown, cleaner flags for majors, honest version ----------
# Load after part19, before part7. Same shared namespace as the other parts.
VERSION = "2.6.3"
app.version = VERSION
app.openapi_schema = None

# --- 1) Stop hammering DexScreener while it is rate-limiting us ---
# The callers already fall back to stale cache on this HTTPException, so failing fast is safe.
_DEX_COOLDOWN = {"until": 0.0}


def _retry_after_seconds(resp, default: int = 30) -> int:
    try:
        return max(1, min(int(resp.headers.get("retry-after", default)), 120))
    except (TypeError, ValueError):
        return default


async def fetch_dex(client: httpx.AsyncClient, mint: str) -> dict:
    wait = _DEX_COOLDOWN["until"] - time.time()
    if wait > 0:  # still cooling down: do not call upstream at all
        raise HTTPException(
            status_code=503,
            detail="DexScreener rate limit (429). Using longer cache window.",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    t0 = time.time()
    try:
        resp = await client.get(DEX_URL.format(mint=mint))
        if resp.status_code == 429:
            ra = _retry_after_seconds(resp)
            _DEX_COOLDOWN["until"] = time.time() + ra
            src_log("dexscreener", False, t0)
            raise HTTPException(
                status_code=503,
                detail="DexScreener rate limit (429). Using longer cache window.",
                headers={"Retry-After": str(ra)},
            )
        resp.raise_for_status()
        data = resp.json()
        src_log("dexscreener", True, t0)
        return data
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        src_log("dexscreener", False, t0)
        raise HTTPException(
            status_code=503,
            detail="Market data source (DexScreener) is unavailable, retry in a few seconds",
            headers={"Retry-After": "15"},
        )


# --- 2) LP flags are noise for majors like SOL (they showed lp_not_locked next to verdict ok) ---
MAJOR_NOISE_FLAGS = {"lp_not_locked", "lp_partially_locked", "lp_unverified"}
_with_quality_v262 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v262(result, status, security)
    if out.get("asset_class") == "major" and out.get("flags"):
        out["flags"] = [f for f in out["flags"] if f not in MAJOR_NOISE_FLAGS]
    return out


# --- 3) Retry button text in English (part19 wrote it in Vietnamese) ---
HOME_HTML = HOME_HTML.replace("Thử lại sau ", "Retry in ").replace("Thử lại ngay", "Retry now")

# Do not fail silently if the homepage patch from part19 did not match
if "retryBtn" not in HOME_HTML:
    print("[part20] warning: homepage retry button was NOT applied (the original JS line has changed)")
