# ---------- part27: v2.14.0 - optional Telegram bot: send a token address, get the verdict ----------
# Loads before part7. Does nothing unless BOTH env vars are set: TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET.
# Telegram sends updates to /telegram/webhook; the secret header must match, so strangers cannot make the bot send messages.
# Work runs in the background so the webhook answers at once (Telegram retries slow webhooks and would double the replies).
import hmac
import html as _html

VERSION = "2.14.0"
app.version = VERSION
app.openapi_schema = None

TG_RATE_PER_MIN = 8   # per chat; over that the bot stays silent
TG_FETCH_TIMEOUT_S = 25.0
_TG_CHECK_RE = re.compile(r"^/check(?:@\w+)?\s+(\S+)", re.I)
_TG_HELP = ("<b>TVibex402 bot</b>\n\nSend a Solana token address, or <code>/check &lt;address&gt;</code>.\n"
            "You get the verdict, the reasons and holder quality.\n\n"
            "Heuristic output, not a security audit and not financial advice.")


def _tg_cfg() -> tuple:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(), (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()


def _e(x: Any, limit: int = 120) -> str:
    """Escape text that came from the network before it goes into an HTML message."""
    return _html.escape(str(x if x is not None else "-")[:limit], quote=False)


def _tg_format(d: dict) -> str:
    v = str(d.get("verdict") or "unknown")
    icon = {"ok": "\u2705", "caution": "\u26a0\ufe0f", "avoid": "\U0001f6ab"}.get(v, "\u2139\ufe0f")
    dec, hq = d.get("decision") or {}, d.get("holder_quality") or {}
    try:
        liq = f"${float(d.get('liquidity_usd')):,.0f}"
    except (TypeError, ValueError):
        liq = "-"
    lines = [f"{icon} <b>{_e(d.get('token') or d.get('name') or 'token', 32)}</b> - <b>{_e(v.upper(), 12)}</b>",
             f"Risk {_e(d.get('risk_score'), 6)}/100 | Holder quality {_e(hq.get('score'), 6)}/100 | Liquidity {_e(liq, 20)}"]
    if d.get("summary"):
        lines += ["", _e(d["summary"], 300)]
    for title, key in (("Blockers", "blockers"), ("Cautions", "cautions")):
        items = [x for x in (dec.get(key) or []) if x][:5]
        if items:
            lines += ["", f"<b>{title}:</b>"] + [f"- {_e(x, 160)}" for x in items]
    mint = str(d.get("mint") or "")
    if MINT_RE.match(mint):  # validated base58, safe inside a link
        lines += ["", f'<a href="https://solscan.io/token/{mint}">Solscan</a>']
    lines += ["", "<i>Heuristic output, not financial advice.</i>"]
    return "\n".join(lines)[:4000]


async def _tg_send(client: httpx.AsyncClient, token: str, chat_id: int, text: str) -> None:
    try:
        await client.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10.0,
                          json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
    except Exception:  # noqa: BLE001
        pass  # never raise out of a background task, and never log the token


async def _tg_handle(client: httpx.AsyncClient, token: str, chat_id: int, text: str, private: bool) -> None:
    low = text.lower()
    if low.startswith("/start") or low.startswith("/help"):
        await _tg_send(client, token, chat_id, _TG_HELP)
        return
    m = _TG_CHECK_RE.match(text)
    mint = m.group(1) if m else (text if private else "")  # in groups only /check reacts, so the bot does not answer every message
    if not mint:
        return
    if not MINT_RE.match(mint):
        await _tg_send(client, token, chat_id, "That does not look like a Solana token address.\n\n" + _TG_HELP)
        return
    try:
        result, _status = await asyncio.wait_for(build_signal(client, mint, True), TG_FETCH_TIMEOUT_S)
    except HTTPException as exc:
        await _tg_send(client, token, chat_id, f"No data for this token: {_e(exc.detail, 100)}")
        return
    except Exception:  # noqa: BLE001
        await _tg_send(client, token, chat_id, "Could not get data right now. Please try again in a minute.")
        return
    await _tg_send(client, token, chat_id, _tg_format(result))


@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request):
    token, secret = _tg_cfg()
    if not token or not secret:
        raise HTTPException(status_code=404, detail="Not found")  # feature is off
    given = request.headers.get("x-telegram-bot-api-secret-token") or ""
    if not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": True}
    msg = (update.get("message") or update.get("edited_message") or {}) if isinstance(update, dict) else {}
    chat = msg.get("chat") or {}
    chat_id, text = chat.get("id"), str(msg.get("text") or "").strip()[:200]
    if not isinstance(chat_id, int) or not text:
        return {"ok": True}
    if rate_limited(f"tg:{chat_id}", limit=TG_RATE_PER_MIN):
        return {"ok": True}
    client = getattr(request.app.state, "client", None)
    if client is not None:
        schedule(_tg_handle(client, token, chat_id, text, chat.get("type") == "private"))
    return {"ok": True}


print(f"[part27] v{VERSION} Telegram bot: {'ON' if all(_tg_cfg()) else 'off (needs TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET)'}")
