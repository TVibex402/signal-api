# ---------- part29: v2.16.0 - /me: your own rate limit and how much of it is used ----------
# Loads before part7. Reads the in-memory limiter that already exists (no Redis, no new middleware, no extra cost per request).
# Without a key it only says "free"; it never shows the IP. /whoami is the place for checking how the server sees an IP.
VERSION = "2.16.0"
app.version = VERSION
app.openapi_schema = None


@app.get("/me", tags=["account"], summary="Your rate limit and how much of it is used in the current minute")
async def me(request: Request):
    ident = str(client_ip(request))  # "key:<label>" for a valid X-API-Key, otherwise the client IP
    keyed = ident.startswith("key:")
    limit = KEY_RATE_LIMIT if keyed else RATE_LIMIT
    now = time.time()
    with _rl_lock:
        dq = _hits.get(ident)
        used = sum(1 for t in dq if now - t <= RATE_WINDOW) if dq else 0
    watch = None
    if "WATCH_ENABLED" in globals():  # only present when the watchlist part is installed
        watch = {"enabled": bool(WATCH_ENABLED), "max_per_key": WATCH_MAX_PER_KEY if WATCH_ENABLED else None}
    return JSONResponse(content={
        "plan": "key" if keyed else "free",
        "key_label": ident[4:] if keyed else None,
        "limit_per_min": limit, "used_in_window": used, "remaining": max(0, limit - used), "window_s": RATE_WINDOW,
        "batch_max": MAX_BATCH, "watchlist": watch,
        "note": ("Send your key in the X-API-Key header to see its limit. Keys are issued by hand during the beta, see /pricing."
                 if not keyed else "Limits are per key. Calls to this endpoint are not counted."),
    }, headers={"Cache-Control": "no-store"})


print(f"[part29] v{VERSION} /me ready")
