# ---------- part28: v2.15.0 - watchlist with signed webhooks (OFF by default; needs an API key) ----------
# Loads before part7. Set WATCH_ENABLED=true to switch it on. Each watched token is re-checked about every WATCH_POLL_S seconds
# by the resolver loop that already runs (no extra task, no lifespan change). A webhook is called only when something that
# matters changed. Webhook URLs are checked (https, public address) before use because the server makes the request.
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import urllib.parse

VERSION = "2.15.0"
app.version = VERSION
app.openapi_schema = None

WATCH_ENABLED = (os.getenv("WATCH_ENABLED") or "false").lower() in ("1", "true", "yes")
WATCH_MAX_PER_KEY = max(1, int(os.getenv("WATCH_MAX_PER_KEY") or "10"))
WATCH_GLOBAL_MAX = max(1, int(os.getenv("WATCH_GLOBAL_MAX") or "20"))   # protects the shared upstream and Redis budget
WATCH_POLL_S = max(300, int(os.getenv("WATCH_POLL_S") or "900"))
WATCH_BATCH = 8                 # tokens re-checked per resolver cycle
WATCH_COOLDOWN_S = 600          # minimum gap between two notifications of the same token (a worse verdict skips it)
WATCH_RISK_DELTA = 15           # risk score move (vs the last notification) that counts as a change
WATCH_MAX_FAILS = 5             # failed deliveries in a row before the watch is paused
WATCH_TTL_S = 30 * 86400        # a watch expires after 30 days unless renewed with PATCH
_WATCH_RANK = {"ok": 0, "caution": 1, "avoid": 2}
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp", ".intranet")


# ---------- webhook URL safety (the server makes this request, so it must not reach private networks) ----------
def _ip_is_public(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


async def _resolve_host(host: str) -> list:
    infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 4.0)
    return [i[4][0] for i in infos]


async def _webhook_problem(url: str) -> Optional[str]:
    """None if the URL is fine, otherwise a short reason."""
    if not url or len(url) > 300:
        return "webhook URL is empty or too long"
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return "webhook URL is not valid"
    if parts.scheme != "https":
        return "webhook must use https"
    if parts.username or parts.password:
        return "do not put credentials in the webhook URL"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return "this host is not allowed"
    if port not in (None, 443):
        return "only port 443 is allowed"
    try:
        ipaddress.ip_address(host)
        return None if _ip_is_public(host) else "this address is not allowed"
    except ValueError:
        pass
    try:
        addrs = await _resolve_host(host)
    except Exception:  # noqa: BLE001
        return "could not resolve the webhook host"
    if not addrs or not all(_ip_is_public(a) for a in addrs):
        return "this host resolves to an address that is not allowed"
    return None


async def _deliver(client: httpx.AsyncClient, url: str, secret: str, event: str, payload: dict) -> tuple:
    problem = await _webhook_problem(url)  # checked again at delivery time, DNS can change
    if problem:
        return False, problem
    body = json.dumps(dict(payload, event=event, version=VERSION), sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        resp = await client.post(url, content=body, timeout=8.0, follow_redirects=False, headers={
            "Content-Type": "application/json", "User-Agent": f"TVibex402-Watch/{VERSION}",
            "X-TVibe-Event": event, "X-TVibe-Signature": f"sha256={sig}"})
    except Exception as exc:  # noqa: BLE001
        return False, f"request failed ({type(exc).__name__})"
    return 200 <= resp.status_code < 300, f"HTTP {resp.status_code}"


# ---------- what counts as a change ----------
def _watch_decide(ref: dict, cur: dict, last_notified: int, now: int) -> Optional[str]:
    """Reason to notify, or None. `ref` is what the last notification (or the start) said."""
    ref_v, cur_v = ref.get("verdict"), cur.get("verdict")
    worse = _WATCH_RANK.get(cur_v, 0) > _WATCH_RANK.get(ref_v, 0)
    if cur_v != ref_v:
        reason = "verdict_changed"
    elif set(cur.get("blockers") or []) - set(ref.get("blockers") or []):
        reason = "new_blocker"
    elif abs(float(cur.get("risk") or 0) - float(ref.get("risk") or 0)) >= WATCH_RISK_DELTA:
        reason = "risk_moved"
    else:
        return None
    if not worse and now - last_notified < WATCH_COOLDOWN_S:
        return None
    return reason


def _watch_view(sig: dict) -> dict:
    dec = sig.get("decision") or {}
    return {"verdict": sig.get("verdict"), "risk": float(sig.get("risk_score") or 0),
            "blockers": [str(x)[:120] for x in (dec.get("blockers") or [])][:10]}


# ---------- storage layout ----------
def _w_owner(ident: str) -> str:
    return hashlib.sha256(ident.encode()).hexdigest()[:16]  # never put key material in Redis key names


def _w_keys(owner: str, mint: str) -> tuple:
    return f"watch:{owner}:{mint}", f"watch:idx:{owner}", f"{owner}|{mint}"  # hash, per-owner set, queue/all member


def _w_guard(request: Request) -> str:
    if not WATCH_ENABLED:
        raise HTTPException(status_code=404, detail="Watchlist is not enabled on this server")
    ident = client_ip(request)
    if not str(ident).startswith("key:"):
        raise HTTPException(status_code=401, detail="Watchlist needs an API key (x-api-key header)")
    wait = rate_limited(ident, cost=2)
    if wait:
        raise HTTPException(status_code=429, detail="rate limited", headers={"Retry-After": str(wait)})
    return _w_owner(ident)


async def _w_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Send a JSON object")
    return body


def _w_mint(mint: str) -> str:
    mint = (mint or "").strip()
    if not MINT_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid Solana token address")
    return mint


def _w_label(text: Any) -> str:
    return re.sub(r"[^\w .\-]", "", str(text or ""))[:40].strip()


@app.post("/watch", tags=["watchlist"], summary="Watch a token; optional signed webhook when it changes")
async def watch_add(request: Request):
    owner = _w_guard(request)
    body = await _w_body(request)
    mint = _w_mint(str(body.get("mint") or ""))
    webhook = str(body.get("webhook") or "").strip()
    if webhook:
        problem = await _webhook_problem(webhook)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    client = request.app.state.client
    hkey, idx, member = _w_keys(owner, mint)
    got = await redis_pipe(client, [["SCARD", idx], ["SCARD", "watch:all"], ["EXISTS", hkey]])
    if not got:
        raise HTTPException(status_code=503, detail="The store is not available right now")
    count, total, exists = int(got[0] or 0), int(got[1] or 0), int(got[2] or 0)
    if exists:
        raise HTTPException(status_code=409, detail="Already watching this token (use PATCH to change it)")
    if count >= WATCH_MAX_PER_KEY:
        raise HTTPException(status_code=429, detail=f"Watchlist full for this key (max {WATCH_MAX_PER_KEY})")
    if total >= WATCH_GLOBAL_MAX:
        raise HTTPException(status_code=503, detail="The server watchlist is full right now")
    try:
        sig, _status = await asyncio.wait_for(build_signal(client, mint, True), 25.0)
    except HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc.detail)[:120])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Could not read this token right now")
    now, view = int(time.time()), _watch_view(sig)
    secret = secrets.token_hex(16) if webhook else ""
    await redis_pipe(client, [
        ["HSET", hkey, "mint", mint, "label", _w_label(body.get("label")) or str(sig.get("token") or mint[:8])[:40],
         "webhook", webhook, "secret", secret, "paused", "0", "fails", "0", "created", now, "expires", now + WATCH_TTL_S,
         "last_check", now, "last_verdict", view["verdict"] or "", "last_risk", view["risk"], "last_notified", 0,
         "last_error", "", "ref_verdict", view["verdict"] or "", "ref_risk", view["risk"],
         "ref_blockers", json.dumps(view["blockers"])],
        ["EXPIRE", hkey, WATCH_TTL_S], ["SADD", idx, mint], ["SADD", "watch:all", member],
        ["ZADD", "watch:q", now + WATCH_POLL_S, member]])
    out = {"status": "watching", "mint": mint, "verdict": view["verdict"], "risk_score": view["risk"],
           "check_every_s": WATCH_POLL_S, "expires_in_days": WATCH_TTL_S // 86400}
    if webhook:
        out["webhook_secret"] = secret
        out["note"] = "Keep this secret: it is shown once. Verify X-TVibe-Signature = sha256 HMAC of the raw body."
    return JSONResponse(content=out, headers={"Cache-Control": "no-store"})


@app.get("/watch", tags=["watchlist"], summary="Your watched tokens and their last known state")
async def watch_list(request: Request):
    owner = _w_guard(request)
    client = request.app.state.client
    _h, idx, _m = _w_keys(owner, "x")
    got = await redis_pipe(client, [["SMEMBERS", idx]])
    mints = [m for m in ((got or [[]])[0] or []) if MINT_RE.match(str(m))]
    hashes = await redis_pipe(client, [["HGETALL", _w_keys(owner, m)[0]] for m in mints]) if mints else []
    items = []
    for mint, h in zip(mints, hashes or []):
        d = to_dict(h)
        if not d.get("mint"):
            continue
        host = urllib.parse.urlsplit(d.get("webhook") or "").hostname if d.get("webhook") else None
        items.append({"mint": mint, "label": d.get("label"), "paused": d.get("paused") == "1", "webhook_host": host,
                      "last_verdict": d.get("last_verdict"), "last_risk": float(d.get("last_risk") or 0),
                      "last_check": int(d.get("last_check") or 0), "last_notified": int(d.get("last_notified") or 0),
                      "failed_deliveries": int(d.get("fails") or 0), "last_error": d.get("last_error") or None,
                      "expires": int(d.get("expires") or 0)})
    return JSONResponse(content={"count": len(items), "limit": WATCH_MAX_PER_KEY,
                                 "items": sorted(items, key=lambda x: x["mint"])}, headers={"Cache-Control": "no-store"})


@app.delete("/watch/{mint}", tags=["watchlist"], summary="Stop watching a token")
async def watch_remove(mint: str, request: Request):
    owner = _w_guard(request)
    hkey, idx, member = _w_keys(owner, _w_mint(mint))
    await redis_pipe(request.app.state.client, [["DEL", hkey], ["SREM", idx, mint], ["SREM", "watch:all", member],
                                                ["ZREM", "watch:q", member]])
    return {"status": "removed", "mint": mint}


@app.patch("/watch/{mint}", tags=["watchlist"], summary="Change webhook, label or pause; any PATCH also renews the 30 days")
async def watch_update(mint: str, request: Request):
    owner = _w_guard(request)
    body = await _w_body(request)
    mint = _w_mint(mint)
    client = request.app.state.client
    hkey, _i, _m = _w_keys(owner, mint)
    got = await redis_pipe(client, [["EXISTS", hkey]])
    if not got or not int(got[0] or 0):
        raise HTTPException(status_code=404, detail="Not in your watchlist")
    sets: list = ["expires", int(time.time()) + WATCH_TTL_S]
    out: Dict[str, Any] = {"status": "updated", "mint": mint}
    if "webhook" in body:
        url = str(body.get("webhook") or "").strip()
        if url:
            problem = await _webhook_problem(url)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
            secret = secrets.token_hex(16)
            sets += ["webhook", url, "secret", secret, "fails", "0", "last_error", ""]
            out["webhook_secret"] = secret
        else:
            sets += ["webhook", "", "secret", ""]
    if "label" in body:
        sets += ["label", _w_label(body.get("label"))]
    if "paused" in body:
        sets += ["paused", "1" if body.get("paused") else "0"]
        if not body.get("paused"):
            sets += ["fails", "0", "last_error", ""]
    await redis_pipe(client, [["HSET", hkey, *sets], ["EXPIRE", hkey, WATCH_TTL_S]])
    return JSONResponse(content=out, headers={"Cache-Control": "no-store"})


@app.post("/watch/{mint}/test", tags=["watchlist"], summary="Send a test event to the webhook")
async def watch_test(mint: str, request: Request):
    owner = _w_guard(request)
    mint = _w_mint(mint)
    client = request.app.state.client
    got = await redis_pipe(client, [["HGETALL", _w_keys(owner, mint)[0]]])
    d = to_dict((got or [None])[0])
    if not d.get("mint"):
        raise HTTPException(status_code=404, detail="Not in your watchlist")
    if not d.get("webhook"):
        raise HTTPException(status_code=400, detail="This watch has no webhook")
    ok, info = await _deliver(client, d["webhook"], d.get("secret") or "", "test", {"mint": mint, "message": "test event"})
    return {"delivered": ok, "detail": info}


# ---------- the worker: a tick inside the resolver cycle that already exists ----------
async def _watch_tick(client: httpx.AsyncClient) -> int:
    if not WATCH_ENABLED:
        return 0
    now = int(time.time())
    got = await redis_pipe(client, [["ZRANGEBYSCORE", "watch:q", "-inf", now, "LIMIT", 0, WATCH_BATCH]])
    members = (got or [[]])[0] or []
    done = 0
    for member in members:
        owner, _, mint = str(member).partition("|")
        hkey, idx, _m = _w_keys(owner, mint)
        try:
            h = await redis_pipe(client, [["HGETALL", hkey]])
            d = to_dict((h or [None])[0])
            if not d.get("mint") or int(d.get("expires") or 0) < now:  # gone or expired: forget it everywhere
                await redis_pipe(client, [["DEL", hkey], ["SREM", idx, mint], ["SREM", "watch:all", member], ["ZREM", "watch:q", member]])
                continue
            if d.get("paused") == "1":
                await redis_pipe(client, [["ZADD", "watch:q", now + WATCH_POLL_S * 3, member]])
                continue
            try:
                sig, _status = await asyncio.wait_for(build_signal(client, mint, True), 20.0)
            except Exception:  # noqa: BLE001
                await redis_pipe(client, [["ZADD", "watch:q", now + WATCH_POLL_S, member]])
                continue
            cur = _watch_view(sig)
            ref = {"verdict": d.get("ref_verdict"), "risk": float(d.get("ref_risk") or 0),
                   "blockers": json.loads(d.get("ref_blockers") or "[]")}
            reason = _watch_decide(ref, cur, int(d.get("last_notified") or 0), now) if d.get("webhook") else None
            sets: list = ["last_check", now, "last_verdict", cur["verdict"] or "", "last_risk", cur["risk"]]
            if reason:
                dec = sig.get("decision") or {}
                ok, info = await _deliver(client, d["webhook"], d.get("secret") or "", "signal_changed", {
                    "mint": mint, "label": d.get("label"), "reason": reason, "timestamp": now,
                    "previous": {"verdict": ref["verdict"], "risk_score": ref["risk"]},
                    "current": {"verdict": cur["verdict"], "risk_score": cur["risk"], "summary": str(sig.get("summary") or "")[:300],
                                "blockers": cur["blockers"], "cautions": [str(x)[:120] for x in (dec.get("cautions") or [])][:10]},
                    "holder_quality": (sig.get("holder_quality") or {}).get("score")})
                if ok:
                    sets += ["fails", "0", "last_error", "", "last_notified", now, "ref_verdict", cur["verdict"] or "",
                             "ref_risk", cur["risk"], "ref_blockers", json.dumps(cur["blockers"])]
                else:
                    fails = int(d.get("fails") or 0) + 1
                    sets += ["fails", str(fails), "last_error", info[:120]]
                    if fails >= WATCH_MAX_FAILS:
                        sets += ["paused", "1"]  # stop hammering a dead endpoint; PATCH {"paused": false} resumes
            await redis_pipe(client, [["HSET", hkey, *sets], ["ZADD", "watch:q", now + WATCH_POLL_S, member]])
            done += 1
        except Exception:  # noqa: BLE001
            try:
                await redis_pipe(client, [["ZADD", "watch:q", now + WATCH_POLL_S * 2, member]])  # one broken item never blocks the rest
            except Exception:  # noqa: BLE001
                pass
    return done


_resolve_due_v24 = resolve_due


async def resolve_due(client: httpx.AsyncClient) -> int:
    n = await _resolve_due_v24(client)
    try:
        await _watch_tick(client)  # not added to n: n decides how soon the resolver runs again
    except Exception:  # noqa: BLE001
        pass
    return n


print(f"[part28] v{VERSION} watchlist: {'ON' if WATCH_ENABLED else 'off (set WATCH_ENABLED=true)'}, "
      f"per key {WATCH_MAX_PER_KEY}, total {WATCH_GLOBAL_MAX}, every {WATCH_POLL_S}s")
