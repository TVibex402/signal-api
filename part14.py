# ---------- v2.3: live updates for one token (Server-Sent Events) ----------
# One polling loop per watched token, shared by every listener: upstream cost follows the number of
# tokens being watched, not the number of connections. Strict caps keep it from being abused.
from fastapi.responses import StreamingResponse

STREAM_MAX_WATCHED = int(os.getenv("STREAM_MAX_WATCHED", "30"))   # tokens watched at the same time
STREAM_MAX_PER_IP = int(os.getenv("STREAM_MAX_PER_IP", "2"))
STREAM_MAX_PER_KEY = int(os.getenv("STREAM_MAX_PER_KEY", "10"))
STREAM_MAX_SECONDS = int(os.getenv("STREAM_MAX_SECONDS", "600"))  # a connection ends after this; clients reconnect
STREAM_INTERVAL_S = max(10, int(os.getenv("STREAM_INTERVAL_S", "20")))
STREAM_KEEPALIVE_S = 15

_watchers: Dict[str, "_Watcher"] = {}
_stream_open: Counter = Counter()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _change_key(r: dict):
    d = r.get("decision") or {}
    return (r.get("verdict"), r.get("risk_score"), tuple(sorted(r.get("flags") or [])), tuple(d.get("blockers") or []))


class _Watcher:
    def __init__(self, mint: str):
        self.mint = mint
        self.subs: set = set()
        self.key = None
        self.last: Optional[dict] = None
        self.task = None

    def publish(self, message: str):
        for q in list(self.subs):
            if q.full():  # a slow listener loses its oldest message instead of blocking everyone
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)

    async def run(self, client: httpx.AsyncClient):
        try:
            while self.subs:
                try:
                    res, _ = await build_signal(client, self.mint, True)
                    snap = dict(compact_signal(res), checked_at=res.get("timestamp"))
                    key = _change_key(res)
                    self.last = snap
                    if key != self.key:
                        kind = "snapshot" if self.key is None else "change"
                        self.key = key
                        self.publish(_sse(kind, snap))
                    else:
                        self.publish(_sse("tick", {"price_usd": snap.get("price_usd"), "liquidity_usd": snap.get("liquidity_usd"),
                                                   "checked_at": snap.get("checked_at"),
                                                   "freshness": (snap.get("data_quality") or {}).get("freshness")}))
                except HTTPException as e:
                    self.publish(_sse("problem", {"status": e.status_code, "detail": e.detail}))
                await asyncio.sleep(STREAM_INTERVAL_S)
        finally:
            if _watchers.get(self.mint) is self:
                _watchers.pop(self.mint, None)


async def _stream_gen(request: Request, client: httpx.AsyncClient, mint: str, who: str):
    watcher = _watchers.get(mint)
    if watcher is None:
        watcher = _watchers[mint] = _Watcher(mint)
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    watcher.subs.add(q)
    _stream_open[who] += 1
    if watcher.task is None or watcher.task.done():
        watcher.task = asyncio.ensure_future(watcher.run(client))
        _bg.add(watcher.task)
        watcher.task.add_done_callback(_bg.discard)
    started = time.time()
    try:
        yield "retry: 5000\n\n"
        if watcher.last:
            yield _sse("snapshot", watcher.last)
        while time.time() - started < STREAM_MAX_SECONDS:
            if await request.is_disconnected():
                return
            try:
                message = await asyncio.wait_for(q.get(), timeout=STREAM_KEEPALIVE_S)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield message
        yield _sse("end", {"reason": "maximum duration reached, reconnect to continue"})
    finally:
        watcher.subs.discard(q)
        _stream_open[who] -= 1
        if _stream_open[who] <= 0:
            _stream_open.pop(who, None)
        if not watcher.subs and watcher.task is not None and not watcher.task.done():
            watcher.task.cancel()


@app.get("/stream", tags=["signals"], summary="Live updates for one token (Server-Sent Events)",
         description="Events: snapshot (first state), change (verdict, risk score, flags or blockers changed), tick (price and "
                     "liquidity, about every 20s), problem (a data source failed), end (max duration reached: reconnect). "
                     "Listeners of the same token share one polling loop. Limits: 2 open streams per IP (10 per API key), "
                     "30 tokens watched at once, 10 minutes per connection.")
async def stream_route(request: Request, mint: str = Query(..., min_length=1, max_length=64)):
    mint = mint.strip()
    if not MINT_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid Solana token address")
    who = client_ip(request)
    wait = rate_limited(who)
    if wait:
        raise too_many(wait)
    cap = STREAM_MAX_PER_KEY if str(who).startswith("key:") else STREAM_MAX_PER_IP
    if _stream_open.get(who, 0) >= cap:
        raise HTTPException(status_code=429, detail=f"Too many open streams for this client (max {cap})", headers={"Retry-After": "10"})
    if mint not in _watchers and len(_watchers) >= STREAM_MAX_WATCHED:
        raise HTTPException(status_code=503, detail="Too many tokens are being watched right now, retry in a minute",
                            headers={"Retry-After": "30"})
    return StreamingResponse(_stream_gen(request, request.app.state.client, mint, who), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


LLMS_TXT = LLMS_TXT + """
## Live updates (Server-Sent Events)
GET /stream?mint=<MINT> keeps the connection open and sends events: snapshot, change (verdict, risk score, flags or blockers
changed), tick (price and liquidity every ~20s), problem, end. Reconnect after `end` (max 10 minutes per connection).
Limits: 2 open streams per IP (10 per API key), 30 tokens watched at once. Listeners of one token share a single polling loop.
"""
