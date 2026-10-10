# ---------- part30: v2.17.0 - /start onboarding page and llms.txt additions (numbers come from the live settings) ----------
# Loads before part7. Replaces nothing: /pricing, /llms.txt and the MCP tools stay as they are. This part only adds a short
# onboarding page and appends accurate sections to llms.txt. A link is shown only when its page exists on this server.
import html as _html

VERSION = "2.17.0"
app.version = VERSION
app.openapi_schema = None


def _has_route(path: str) -> bool:
    return any(getattr(r, "path", None) == path for r in app.routes)


def render_start(base: str) -> str:
    e = lambda s: _html.escape(str(s), quote=True)  # noqa: E731
    watch_on = "WATCH_ENABLED" in globals() and bool(WATCH_ENABLED)
    play_link = ' at <a href="/play">/play</a>' if _has_route("/play") else ""
    me_link = ' <a href="/me">/me</a> shows your current limit and use.' if _has_route("/me") else ""
    curl = "curl &quot;" + e(base) + "/signal?mint=So11111111111111111111111111111111111111112&quot;"
    steps = [
        ("Try it", "<p>Check a token in the browser" + play_link + ", or call the API directly:</p><pre>" + curl + "</pre>"
                   "<p class=\"muted\">The free tier needs no signup: " + str(RATE_LIMIT) + " requests per minute per IP, batches of up to "
                   + str(MAX_BATCH) + " tokens.</p>"),
        ("Use it from an AI agent", "<p>Add this as a remote MCP server (Streamable HTTP):</p><pre>" + e(base) + "/mcp</pre>"
                                    "<p>Machine-readable docs: <a href=\"/llms.txt\">/llms.txt</a> and <a href=\"/docs\">/docs</a>.</p>"),
        ("Higher limits", "<p>An <code>X-API-Key</code> header raises the limit to " + str(KEY_RATE_LIMIT) + " requests per minute. "
                          "Keys are issued by hand during the beta: see <a href=\"/pricing\">/pricing</a>." + me_link + "</p>"),
    ]
    if watch_on:
        steps.append(("Get notified", "<p>With a key you can watch up to " + str(WATCH_MAX_PER_KEY) + " tokens: <code>POST /watch</code> with "
                                      "<code>{&quot;mint&quot;: &quot;...&quot;, &quot;webhook&quot;: &quot;https://...&quot;}</code>. The webhook is called when the verdict "
                                      "changes, a new blocker appears or the risk score moves by " + str(WATCH_RISK_DELTA) + "+, checked about every "
                                      + str(max(1, WATCH_POLL_S // 60)) + " minutes, and each call is signed (<code>X-TVibe-Signature</code>).</p>"))
    check_links = " - ".join('<a href="' + p + '">' + p + "</a>" for p in ("/accuracy", "/methodology", "/ledger", "/status") if _has_route(p))
    steps.append(("Check our work", "<p>" + check_links + "</p><p class=\"muted\">Verdicts are heuristics, not a security audit and not "
                  "financial advice. The free host can sleep, so retry after a 503.</p>"))
    body = "".join(f'<div class="step"><h2><span class="n">{i}</span>{t}</h2>{c}</div>' for i, (t, c) in enumerate(steps, 1))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Get started - TVibex402</title><style>
:root{{--bg:#070b14;--card:#111827;--border:#1f2937;--text:#f3f4f6;--muted:#9ca3af;--accent:#6366f1}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;margin:0;padding:1rem}}
.wrap{{max-width:760px;margin:0 auto}} h1{{font-size:1.8rem;margin:.5rem 0}} .muted{{color:var(--muted);font-size:.9rem}}
.step{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.2rem;margin:1rem 0}}
h2{{font-size:1.05rem;margin:0 0 .6rem;display:flex;align-items:center}}
.n{{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;background:var(--accent);border-radius:50%;margin-right:.6rem;font-size:.85rem}}
pre,code{{background:#0b1220;border-radius:6px;font-size:.85rem}} pre{{padding:.8rem;overflow-x:auto;border:1px solid var(--border)}} code{{padding:.1rem .35rem}}
a{{color:#60a5fa}}</style></head><body><div class="wrap"><h1>Get started with TVibex402</h1>
<p class="muted">A free Solana token signal API for people, bots and AI agents.</p>{body}</div></body></html>"""


@app.get("/start", response_class=HTMLResponse, include_in_schema=False)
async def onboarding(request: Request):
    base = os.getenv("PUBLIC_URL") or ("https://" + request.url.netloc)
    return HTMLResponse(render_start(base.rstrip("/")), headers={"Cache-Control": "public, max-age=300"})


if "GET /play" not in LLMS_TXT:
    LLMS_TXT = LLMS_TXT + """
## Try it, check it, see your limit
GET /play is a web page to check a token by hand (/play?mint=ADDRESS pre-fills it). GET /status shows live source health.
GET /me shows your own rate limit and how much of the current minute is used (send X-API-Key to see the key limit).
GET /start is a short onboarding page.
"""
if "WATCH_ENABLED" in globals() and WATCH_ENABLED and "POST /watch" not in LLMS_TXT:
    LLMS_TXT = LLMS_TXT + f"""
## Watchlist and webhooks (needs an X-API-Key)
POST /watch {{mint, webhook?, label?}} starts watching a token; GET /watch lists yours; PATCH /watch/MINT changes webhook, label or pause;
DELETE /watch/MINT stops it; POST /watch/MINT/test sends a test event. Up to {WATCH_MAX_PER_KEY} tokens per key.
The webhook must be https on a public address. It is called when the verdict changes, a new blocker appears or the risk score moves
by {WATCH_RISK_DELTA}+ since the last notification (at most one call per {WATCH_COOLDOWN_S // 60} minutes unless the verdict gets worse).
Each call is signed: X-TVibe-Signature is sha256= plus the HMAC-SHA256 of the raw body with the secret shown once when you add the webhook.
Tokens are re-checked about every {max(1, WATCH_POLL_S // 60)} minutes, so this is not a real-time alert.
"""

if '<a href="/accuracy">/accuracy</a>' in HOME_HTML and 'href="/play"' not in HOME_HTML and _has_route("/play"):
    HOME_HTML = HOME_HTML.replace('<a href="/accuracy">/accuracy</a>', '<a href="/accuracy">/accuracy</a> &bull; <a href="/play">/play</a>', 1)

print(f"[part30] v{VERSION} /start onboarding page and llms.txt additions ready")
