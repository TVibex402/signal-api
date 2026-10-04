# ---------- v2.1: resilience, API keys, tamper-evident ledger ----------
import hashlib
import hmac

# --- Solana RPC: fallbacks, one retry, circuit breaker ---
RPC_FALLBACKS = [u.strip() for u in (os.getenv("SOLANA_RPC_FALLBACKS") or "").split(",") if u.strip()]
_rpc_state: Dict[str, Any] = {"fails": 0, "open_until": 0.0}


async def rpc(client: httpx.AsyncClient, method: str, params: list):
    """Try the primary RPC, then each fallback, then the primary once more. After 5 failed calls in a row the
    breaker opens for 20s so a dead RPC cannot pile up requests (signals just come back marked partial)."""
    if time.time() < _rpc_state["open_until"]:
        raise RuntimeError("RPC circuit open: skipped for a few seconds")
    urls = [RPC_URL] + RPC_FALLBACKS + [RPC_URL]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last = "no attempt"
    for i, url in enumerate(urls):
        try:
            resp = await client.post(url, json=payload, timeout=4.0)
            resp.raise_for_status()
            body = resp.json()
            if body.get("error"):
                raise RuntimeError(str(body["error"])[:200])
            _rpc_state["fails"] = 0
            return body.get("result")
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:120]}"
            if i < len(urls) - 1:
                await asyncio.sleep(0.2)
    _rpc_state["fails"] += 1
    if _rpc_state["fails"] >= 5:
        _rpc_state.update(fails=0, open_until=time.time() + 20)
    raise RuntimeError(last)


# --- optional API keys: a key gets its own, higher rate limit (no more sharing an IP with strangers) ---
def _load_keys() -> Dict[str, str]:
    keys: Dict[str, str] = {}
    for item in (os.getenv("API_KEYS") or "").split(","):  # API_KEYS="secret1:partnerA,secret2:partnerB"
        key, _, label = item.strip().partition(":")
        if key:
            keys[key] = label or key[:4] + "..."
    return keys


API_KEYS = _load_keys()
KEY_RATE_LIMIT = int(os.getenv("KEY_RATE_LIMIT", "300"))
_client_ip_v20 = client_ip
_rate_limited_v20 = rate_limited


def client_ip(request: Request) -> str:
    given = request.headers.get("x-api-key")
    if given and API_KEYS:
        for key, label in API_KEYS.items():
            if hmac.compare_digest(given.encode(), key.encode()):
                return "key:" + label
    return _client_ip_v20(request)


def rate_limited(key: str, limit: Optional[int] = None, cost: int = 1) -> int:
    if limit is None:
        limit = KEY_RATE_LIMIT if str(key).startswith("key:") else RATE_LIMIT
    return _rate_limited_v20(key, limit, cost)


# --- ledger: every recorded verdict is chained to the previous one with SHA-256 ---
# Publishing the head hash now and then proves the history was not rewritten or back-filled.
LEDGER_KEEP = 20000
_ledger_lock: Optional[asyncio.Lock] = None


def canon(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def verify_chain(entries_oldest_first: list) -> bool:
    prev = None
    for e in entries_oldest_first:
        body = {k: v for k, v in e.items() if k != "hash"}
        if hashlib.sha256(canon(body).encode()).hexdigest() != e.get("hash"):
            return False
        if prev is not None and e.get("prev") != prev:
            return False
        prev = e["hash"]
    return True


async def ledger_append(client: httpx.AsyncClient, entry: dict):
    global _ledger_lock
    if not PRED_ENABLED:
        return
    if _ledger_lock is None:
        _ledger_lock = asyncio.Lock()
    async with _ledger_lock:
        got = await redis_pipe(client, [["GET", "ledger_head"]])
        if got is None:
            return  # store unavailable: skip, never block the API
        body = dict(entry, prev=got[0] or "GENESIS")
        h = hashlib.sha256(canon(body).encode()).hexdigest()
        await redis_pipe(client, [["LPUSH", "ledger", canon(dict(body, hash=h))],
                                  ["LTRIM", "ledger", 0, LEDGER_KEEP - 1], ["SET", "ledger_head", h]])


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
        await ledger_append(client, {
            "id": pid, "ts": now, "mint": mint, "token": r.get("token") or "", "verdict": r["verdict"],
            "risk_score": r["risk_score"], "price_usd": price, "liquidity_usd": r.get("liquidity_usd") or 0,
            "flags": sorted(r.get("flags") or []), "version": VERSION,
        })
    except Exception:
        pass


@app.get("/ledger", tags=["trust"], summary="Tamper-evident list of every recorded verdict (hash chain)")
async def ledger_route(request: Request, limit: int = Query(50, ge=1, le=500, description="Newest entries to return")):
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    if not PRED_ENABLED:
        return JSONResponse(content={"enabled": False, "detail": "The ledger is not switched on on this server yet."})
    res = await redis_pipe(request.app.state.client, [["LRANGE", "ledger", 0, limit - 1], ["GET", "ledger_head"], ["LLEN", "ledger"]])
    if res is None:
        return JSONResponse(content={"enabled": True, "available": False, "detail": "Ledger store unavailable, retry shortly"},
                            status_code=503, headers={"Retry-After": "10"})
    entries = []
    for x in res[0] or []:
        try:
            entries.append(json.loads(x))
        except ValueError:
            pass
    return JSONResponse(content={
        "enabled": True, "head": res[1], "total_kept": int(res[2] or 0), "returned": len(entries), "newest_first": True,
        "how_to_verify": "Go from oldest to newest. For each entry drop the 'hash' field, serialize with sorted keys and compact "
                         "separators (',' and ':'), SHA-256 it: it must equal 'hash', and the next entry's 'prev' must equal it. "
                         "The oldest kept entry's 'prev' is GENESIS or the hash of an older entry. Compare 'head' over time.",
        "entries": entries,
    }, headers={"Cache-Control": "public, max-age=30"})
