import re
import time

import requests
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# True  = free for everyone (beta)
# False = charge 0.01 USDC per call via x402 (Solana mainnet)
FREE_MODE = True

if not FREE_MODE:
    from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync, PaymentOption
    from x402.http.middleware.flask import payment_middleware
    from x402.http.types import RouteConfig
    from x402.mechanisms.svm.exact import ExactSvmServerScheme
    from x402.schemas import Network
    from x402.server import x402ResourceServerSync

    # Receiving wallet (public address)
    SVM_ADDRESS = "GyRZxTMEKS68r23jmTA4Mc8cmLQEFVQG2Ekh5AXrGtEH"
    SVM_NETWORK: Network = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"  # Mainnet

    facilitator = HTTPFacilitatorClientSync(
        FacilitatorConfig(url="https://facilitator.payai.network")
    )
    server = x402ResourceServerSync(facilitator)
    server.register(SVM_NETWORK, ExactSvmServerScheme())

    routes = {
        "GET /signal": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=SVM_ADDRESS,
                    price="$0.01",
                    network=SVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="TVibex402: Solana token data and signals",
        ),
    }
    payment_middleware(app, routes=routes, server=server)

MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
CACHE = {}
CACHE_TTL = 10  # seconds

# Heuristic thresholds for flags (starting values, tune them)
SPIKE_RATIO = 3.0          # 5m volume vs hourly average pace
MIN_SPIKE_VOLUME = 1000    # ignore tiny volumes (USD, 5m)
BUY_PRESSURE_HIGH = 0.65   # share of buys in 5m
MIN_TXNS = 20              # min 5m transactions for buy/sell flags
LOW_LIQ_USD = 10000        # liquidity below this = low_liquidity
NEW_PAIR_MIN = 60          # pair younger than this (minutes) = new_pair
HIGH_TURNOVER = 3.0        # 1h volume / liquidity


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def div(a, b):
    if a is None or not b:
        return None
    return a / b


def rnd(x, n=2):
    return None if x is None else round(x, n)


def build_payload(p):
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
    chg = p.get("priceChange") or {}

    liq = num((p.get("liquidity") or {}).get("usd"))
    v5, v1, v6 = num(vol.get("m5")), num(vol.get("h1")), num(vol.get("h6"))

    b5 = (tx.get("m5") or {}).get("buys") or 0
    s5 = (tx.get("m5") or {}).get("sells") or 0
    b1 = (tx.get("h1") or {}).get("buys") or 0
    s1 = (tx.get("h1") or {}).get("sells") or 0

    spike_5m = div(v5, (v1 / 12) if v1 else None)
    buy_pressure_5m = div(b5, b5 + s5)
    turnover_1h = div(v1, liq)

    created = num(p.get("pairCreatedAt"))
    age_min = (time.time() * 1000 - created) / 60000 if created else None

    flags = []
    if spike_5m is not None and spike_5m >= SPIKE_RATIO and (v5 or 0) >= MIN_SPIKE_VOLUME:
        flags.append("volume_spike")
    if buy_pressure_5m is not None and (b5 + s5) >= MIN_TXNS:
        if buy_pressure_5m >= BUY_PRESSURE_HIGH:
            flags.append("heavy_buying")
        elif buy_pressure_5m <= 1 - BUY_PRESSURE_HIGH:
            flags.append("heavy_selling")
    if liq is not None and liq < LOW_LIQ_USD:
        flags.append("low_liquidity")
    if age_min is not None and age_min < NEW_PAIR_MIN:
        flags.append("new_pair")
    if turnover_1h is not None and turnover_1h >= HIGH_TURNOVER:
        flags.append("high_turnover")

    return {
        "token": (p.get("baseToken") or {}).get("symbol"),
        "price_usd": p.get("priceUsd"),
        "price_change_pct": {
            "5m": chg.get("m5"),
            "1h": chg.get("h1"),
            "6h": chg.get("h6"),
            "24h": chg.get("h24"),
        },
        "liquidity_usd": liq,
        "market_cap_usd": p.get("marketCap"),
        "fdv_usd": p.get("fdv"),
        "pair_age_minutes": rnd(age_min, 1),
        "volume_5m": v5,
        "volume_1h": v1,
        "volume_6h": v6,
        "buys_5m": b5,
        "sells_5m": s5,
        "buys_1h": b1,
        "sells_1h": s1,
        "buy_pressure_5m": rnd(buy_pressure_5m, 3),
        "volume_spike_ratio_5m": rnd(spike_5m, 2),
        "turnover_1h": rnd(turnover_1h, 2),
        "flags": flags,
    }


LANDING_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__DESC__">
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__DESC__">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="__TITLE__">
<meta name="twitter:description" content="__DESC__">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22%3E%3Ctext y=%22.9em%22 font-size=%2290%22%3E%E2%9A%A1%3C/text%3E%3C/svg%3E">
<style>
:root{--bg:#0b0f14;--card:#121923;--line:#223042;--text:#e6edf3;--muted:#8b9bb0;--a:#14f195;--b:#9945ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.55}
.wrap{max-width:720px;margin:0 auto;padding:28px 18px 60px}
.brand{font-size:2.3rem;font-weight:800;margin:0;letter-spacing:-.5px;background:linear-gradient(90deg,var(--b),var(--a));-webkit-background-clip:text;background-clip:text;color:transparent}
.tag{color:var(--muted);margin:.4rem 0 1rem;font-size:1.02rem}
.pills{display:flex;gap:8px;flex-wrap:wrap}
.pill{border:1px solid var(--line);background:var(--card);padding:5px 12px;border-radius:999px;font-size:.82rem}
.pill b{color:var(--a)}
h2{font-size:1.1rem;margin:2.2rem 0 .7rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid .card{padding:12px;font-size:.92rem}
.btn{display:inline-block;padding:11px 16px;border-radius:10px;font-weight:600;text-decoration:none;border:0;cursor:pointer;font-size:.95rem}
.btn.primary{background:linear-gradient(90deg,var(--b),var(--a));color:#06110b}
.btn.ghost{border:1px solid var(--line);color:var(--text);background:transparent}
.row{display:flex;gap:10px;flex-wrap:wrap;margin:1.1rem 0 0}
form{display:flex;gap:8px;flex-wrap:wrap;margin:.6rem 0}
input[type=text]{flex:1 1 220px;min-width:0;padding:11px 12px;border-radius:10px;border:1px solid var(--line);background:#0e141c;color:var(--text);font-size:.9rem}
pre{background:#0e141c;border:1px solid var(--line);border-radius:10px;padding:12px;overflow-x:auto;font-size:.8rem;margin:.5rem 0}
code{font-family:ui-monospace,Menlo,Consolas,monospace}
ol{padding-left:1.2rem;margin:.4rem 0}
li{margin:.4rem 0}
.muted{color:var(--muted);font-size:.85rem}
a{color:var(--a)}
</style>
</head>
<body>
<div class="wrap">

<h1 class="brand">TVibex402</h1>
<p class="tag">__TAG__</p>
<div class="pills">__PILLS__</div>
<div class="row">
  <a class="btn primary" href="/demo">View free sample</a>
  <a class="btn ghost" href="#how">How it works</a>
</div>

<h2>Try it</h2>
<div class="card">
  <p style="margin:0 0 .4rem">Paste any Solana token address:</p>
  <form action="/signal" method="get">
    <input type="text" name="mint" placeholder="Token address (mint)" required minlength="32" maxlength="44" autocomplete="off" autocapitalize="off" spellcheck="false">
    <button class="btn primary" type="submit">__BTN__</button>
  </form>
  <p class="muted" style="margin:.4rem 0 0">__TRYNOTE__</p>
</div>

<h2>What you get</h2>
<div class="grid">
  <div class="card">Price, market cap and FDV</div>
  <div class="card">Price change: 5m, 1h, 6h, 24h</div>
  <div class="card">Liquidity (largest pool) and pair age</div>
  <div class="card">Volume: 5m, 1h, 6h</div>
  <div class="card">Buy/sell counts and buy pressure</div>
  <div class="card">Volume spike ratio and turnover</div>
</div>
<p class="muted">Plus simple flags: volume_spike, heavy_buying, heavy_selling, low_liquidity, new_pair, high_turnover.</p>

<h2 id="how">How it works</h2>
<div class="card">__HOW__</div>

<h2>Call it</h2>
<pre><code>GET https://signal-api-24q4.onrender.com/signal?mint=TOKEN_ADDRESS</code></pre>
<p class="muted">Example (wrapped SOL):</p>
<pre><code>https://signal-api-24q4.onrender.com/signal?mint=So11111111111111111111111111111111111111112</code></pre>

<h2>Sample response</h2>
<pre><code>{
  "token": "SOL",
  "price_usd": "119.54",
  "price_change_pct": {"5m": 0.1, "1h": -0.4, "6h": 1.2, "24h": 2.8},
  "liquidity_usd": 37607261.15,
  "volume_5m": 31690.17,
  "volume_1h": 458039.23,
  "buys_5m": 468,
  "sells_5m": 365,
  "buy_pressure_5m": 0.562,
  "volume_spike_ratio_5m": 0.83,
  "turnover_1h": 0.01,
  "flags": []
}</code></pre>

<h2>Notes</h2>
<ul class="muted">
  <li>Data comes from DexScreener and may be delayed or inaccurate.</li>
  <li>Flags are simple heuristics that describe current activity. They are not buy or sell recommendations.</li>
  <li>For information only. Not financial advice.</li>
  <li>Hosted on a free tier: the first request after a quiet period can take up to about a minute.</li>
  __EXTRA__
</ul>
<p class="muted">__FOOT__</p>

</div>
</body>
</html>"""

if FREE_MODE:
    COPY = {
        "__TITLE__": "TVibex402 | Solana token data API",
        "__DESC__": "Free Solana token data API for AI agents and bots: price, liquidity, volume, buy pressure and simple signals.",
        "__TAG__": "Solana token data and signals, built for AI agents and bots.",
        "__PILLS__": '<span class="pill"><b>Free</b> during beta</span><span class="pill">Solana tokens</span><span class="pill">No signup, no API key</span>',
        "__BTN__": "Get data (free)",
        "__TRYNOTE__": "Free during beta. No wallet or signup needed. Results come back as JSON.",
        "__HOW__": "<ol><li>Call <code>/signal?mint=TOKEN_ADDRESS</code>.</li><li>Receive the data as JSON. No account, no API key.</li><li>Paid access via the x402 protocol (USDC on Solana) may be added later.</li></ol>",
        "__EXTRA__": "",
        "__FOOT__": "TVibex402 is an experimental project. Free during beta.",
    }
else:
    COPY = {
        "__TITLE__": "TVibex402 | Pay-per-call Solana token data",
        "__DESC__": "Pay-per-call Solana token data and signals for AI agents and bots. 0.01 USDC per call via x402. No signup, no API key.",
        "__TAG__": "Pay-per-call Solana token data and signals, built for AI agents and bots.",
        "__PILLS__": '<span class="pill"><b>$0.01</b> USDC per call</span><span class="pill">Solana mainnet</span><span class="pill">x402 protocol</span><span class="pill">No signup, no API key</span>',
        "__BTN__": "Get data ($0.01)",
        "__TRYNOTE__": "You will be asked to connect a Solana wallet and pay 0.01 USDC (real money, mainnet). Want to see the format first? Open the free sample above.",
        "__HOW__": "<ol><li>Call <code>/signal?mint=TOKEN_ADDRESS</code>.</li><li>An unpaid request gets <code>402 Payment Required</code> with payment instructions.</li><li>An x402-compatible client pays 0.01 USDC on Solana and retries automatically, then receives the data.</li></ol>",
        "__EXTRA__": "<li>Errors (invalid address, token not found, upstream failure) return an error status, so a failed call should not be charged.</li>",
        "__FOOT__": "TVibex402 is an experimental project. Payments are real USDC on Solana mainnet.",
    }

LANDING_HTML = LANDING_TEMPLATE
for _key, _value in COPY.items():
    LANDING_HTML = LANDING_HTML.replace(_key, _value)


@app.route("/")
def home():
    return Response(LANDING_HTML, mimetype="text/html")


@app.route("/demo")
def demo():
    # Free, static sample. Does not call DexScreener.
    return jsonify(
        sample=True,
        note="Static sample data. /signal returns live data.",
        token="SOL",
        price_usd="119.54",
        price_change_pct={"5m": 0.1, "1h": -0.4, "6h": 1.2, "24h": 2.8},
        liquidity_usd=37607261.15,
        market_cap_usd=None,
        fdv_usd=None,
        pair_age_minutes=None,
        volume_5m=31690.17,
        volume_1h=458039.23,
        volume_6h=None,
        buys_5m=468,
        sells_5m=365,
        buys_1h=None,
        sells_1h=None,
        buy_pressure_5m=0.562,
        volume_spike_ratio_5m=0.83,
        turnover_1h=0.01,
        flags=[],
    )


@app.route("/signal")
def signal():
    mint = request.args.get("mint", "").strip()
    if not mint:
        return jsonify(error="Missing mint parameter"), 400
    if not MINT_RE.match(mint):
        return jsonify(error="Invalid token address"), 400

    now = time.time()
    hit = CACHE.get(mint)
    if hit and now - hit[0] < CACHE_TTL:
        return jsonify(hit[1])

    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        data = requests.get(url, timeout=10).json()
    except Exception:
        return jsonify(error="Could not fetch data"), 502

    pairs = data.get("pairs") or []
    if not pairs:
        return jsonify(error="Token not found"), 404

    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    payload = build_payload(p)

    if len(CACHE) > 500:
        CACHE.clear()
    CACHE[mint] = (now, payload)
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)