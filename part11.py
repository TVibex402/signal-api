# ---------- v2.1.1: holder accuracy, abuse resistance, hygiene ----------
# Adds or replaces functions from earlier parts (same shared namespace). Loads before part7 (MCP).
VERSION = "2.1.1"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version
import contextvars
import re as _re
from functools import lru_cache

SYSTEM_PROGRAM = "11111111111111111111111111111111"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def b58_to_32(s: str) -> Optional[bytes]:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = b"\x00" * (len(s) - len(s.lstrip("1"))) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return raw if len(raw) == 32 else None


def is_on_curve(b: bytes) -> bool:
    """True if these 32 bytes decode to an ed25519 point, i.e. the address can have a private key (a wallet).
    Program-derived addresses (pool vaults, bonding curves, multisig vaults) are OFF the curve by construction."""
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    y2 = y * y % _P
    x2 = (y2 - 1) * pow(_D * y2 + 1, _P - 2, _P) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


@lru_cache(maxsize=4096)
def is_pda(addr: str) -> Optional[bool]:
    raw = b58_to_32(addr)
    return None if raw is None else not is_on_curve(raw)


# A holder that is a program-derived address whose own account belongs to a program (an AMM pool, a bonding curve,
# a staking vault...) is not a person. Wallets and multisig vaults (system-owned) still count as holders.
_fetch_security_v20 = fetch_security


async def fetch_security(client: httpx.AsyncClient, mint: str) -> Optional[dict]:
    raw = await _fetch_security_v20(client, mint)
    try:
        holders = (raw or {}).get("holders") or []
        pdas = sorted({h["owner"] for h in holders if h.get("owner") and is_pda(h["owner"])})
        if pdas:
            res = await rpc(client, "getMultipleAccounts", [pdas, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])
            infos = dict(zip(pdas, (res or {}).get("value") or []))
            for h in holders:
                if h.get("owner") in infos:
                    acc = infos[h["owner"]]
                    program = (acc or {}).get("owner")
                    h["program_owned"] = bool(acc) and program != SYSTEM_PROGRAM
                    if h["program_owned"]:
                        h["program"] = program
    except Exception:  # noqa: BLE001  best effort: without it we simply fall back to the old behaviour
        pass
    return raw


def build_security(raw: Optional[dict], pool_ids: set):
    """Turn raw on-chain data into a public `security` block + extra flags."""
    if not raw:
        return {"available": False}, []
    flags = []
    out: Dict[str, Any] = {"available": True, "partial": False}

    if raw.get("info_ok"):
        ma, fa = raw.get("mint_authority"), raw.get("freeze_authority")
        out.update(
            mint_authority_revoked=ma is None,
            freeze_authority_revoked=fa is None,
            token_program=raw.get("program"),
            decimals=raw.get("decimals"),
        )
        risky = sorted(set(raw.get("extensions") or []) & RISKY_EXTENSIONS)
        out["risky_extensions"] = risky
        if ma is not None:
            flags.append("mint_authority_active")
        if fa is not None:
            flags.append("freeze_authority_active")
        if risky:
            flags.append("risky_token_extension")
    else:
        out["partial"] = True

    holders, supply = raw.get("holders"), raw.get("supply")
    if holders and supply:
        excluded_ids = pool_ids | KNOWN_NON_HOLDERS
        real, pool_pct, prog_pct = [], 0.0, 0.0
        for h in holders:
            pct = h["amount"] / supply * 100
            if h["owner"] in excluded_ids or h["account"] in excluded_ids or h.get("program_owned"):
                pool_pct += pct
                if h.get("program_owned"):
                    prog_pct += pct
            else:
                real.append({"owner": h["owner"] or h["account"], "pct": round(pct, 2)})
        top1 = real[0]["pct"] if real else 0.0
        top10 = round(sum(h["pct"] for h in real[:10]), 2)
        out.update(
            top1_holder_pct=top1,
            top10_holders_pct=top10,
            liquidity_pool_pct=round(pool_pct, 2),
            program_owned_pct=round(prog_pct, 2),
            top_holders=real[:5],
        )
        if top10 >= 80:
            flags.append("extreme_holder_concentration")
        elif top10 >= 50:
            flags.append("high_holder_concentration")
        if top1 >= 20:
            flags.append("dominant_holder")
    else:
        out["partial"] = True
    return out, flags


# --- a reserved lane in the shared upstream budget for keyed clients ---
# Anonymous traffic can no longer use up the whole DexScreener budget and lock out clients who hold an API key.
GLOBAL_KEY_RESERVE = int(os.getenv("GLOBAL_KEY_RESERVE", "50"))
_keyed_ctx: "contextvars.ContextVar[bool]" = contextvars.ContextVar("tvibex_keyed", default=False)
_rate_limited_v21 = rate_limited


def rate_limited(key: str, limit: Optional[int] = None, cost: int = 1) -> int:
    if key == "__global__" and _keyed_ctx.get():
        key, limit = "__global_key__", GLOBAL_KEY_RESERVE
    return _rate_limited_v21(key, limit, cost)


class KeyContextMiddleware:
    """Marks the request context as 'keyed' when a valid X-API-Key header is present."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and API_KEYS:
            given = next((v for k, v in scope.get("headers", []) if k == b"x-api-key"), None)
            if given and any(hmac.compare_digest(given, key.encode()) for key in API_KEYS):
                token = _keyed_ctx.set(True)
                try:
                    return await self.app(scope, receive, send)
                finally:
                    _keyed_ctx.reset(token)
        return await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                have = {k.lower() for k, _ in headers}
                for k, v in ((b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                             (b"x-frame-options", b"DENY")):
                    if k not in have:
                        headers.append((k, v))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrap)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(KeyContextMiddleware)

# --- never leak an RPC key (Helius puts it in the URL) through an error message ---
_SECRET_RE = _re.compile(r"(?i)(api[-_]?key|apikey|token|key)=[^&\s'\"]+")


def redact(msg: str) -> str:
    for u in [RPC_URL] + RPC_FALLBACKS:
        if u:
            msg = msg.replace(u, "[rpc]")
    return _SECRET_RE.sub(lambda m: m.group(1) + "=[redacted]", msg)


_rpc_v21 = rpc


async def rpc(client: httpx.AsyncClient, method: str, params: list):
    try:
        return await _rpc_v21(client, method, params)
    except RuntimeError as e:
        raise RuntimeError(redact(str(e))) from None


@app.get("/whoami", tags=["ops"], summary="Which client IP this server sees (use it to check TRUSTED_PROXY_HOPS)")
async def whoami(request: Request):
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    return JSONResponse(content={"seen_ip": client_ip(request), "x_forwarded_for": request.headers.get("x-forwarded-for"),
                                 "trusted_proxy_hops": TRUSTED_HOPS}, headers={"Cache-Control": "no-store"})


LLMS_TXT = LLMS_TXT + """
## Holders
top1_holder_pct and top10_holders_pct count real holders only. Liquidity pools, bonding curves and other accounts owned by
programs are excluded (liquidity_pool_pct, program_owned_pct). Wallets and multisig vaults still count as holders.
"""
