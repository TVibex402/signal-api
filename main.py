import requests
from flask import Flask, Response, jsonify, request

from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync, PaymentOption
from x402.http.middleware.flask import payment_middleware
from x402.http.types import RouteConfig
from x402.mechanisms.svm.exact import ExactSvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServerSync

app = Flask(__name__)

# Receiving wallet (public address)
SVM_ADDRESS = "GyRZxTMEKS68r23jmTA4Mc8cmLQEFVQG2Ekh5AXrGtEH"
SVM_NETWORK: Network = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"  # Solana Mainnet

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
        description="Token signal data: price, liquidity, volume, buys/sells",
    ),
}

payment_middleware(app, routes=routes, server=server)

LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>signal-api: pay-per-call Solana token data</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;padding:24px;
       line-height:1.55;color:#1a1a1a;background:#fafafa}
  h1{font-size:1.6rem;margin-bottom:.2rem}
  h2{font-size:1.15rem;margin-top:1.8rem}
  code,pre{background:#eee;border-radius:6px;font-size:.85rem}
  code{padding:2px 5px}
  pre{padding:12px;overflow-x:auto}
  .price{display:inline-block;background:#1a1a1a;color:#fff;padding:4px 10px;
         border-radius:999px;font-size:.9rem}
  small{color:#555}
</style>
</head>
<body>
<h1>signal-api</h1>
<p>Pay-per-call market data for Solana tokens, built for AI agents and bots.</p>
<p><span class="price">$0.01 USDC per call</span></p>

<h2>What you get</h2>
<p>For any Solana token address, one call returns:</p>
<ul>
  <li>Token symbol and USD price</li>
  <li>Liquidity in USD (largest pool)</li>
  <li>Volume over 5 minutes and 1 hour</li>
  <li>Buy and sell transaction counts over 5 minutes</li>
</ul>

<h2>How to call it</h2>
<pre>GET https://signal-api-24q4.onrender.com/signal?mint=TOKEN_ADDRESS</pre>
<p>Example (wrapped SOL):</p>
<pre>https://signal-api-24q4.onrender.com/signal?mint=So11111111111111111111111111111111111111112</pre>

<h2>How payment works</h2>
<p>This API uses the <b>x402</b> protocol. An unpaid request gets an
HTTP <code>402 Payment Required</code> response with payment instructions.
An x402-compatible client pays 0.01 USDC on Solana mainnet and retries the
request automatically, then receives the data. No account or API key needed.</p>

<h2>Example response</h2>
<pre>{
  "token": "SOL",
  "price_usd": "119.54",
  "liquidity_usd": 37607261.15,
  "volume_5m": 31690.17,
  "volume_1h": 458039.23,
  "buys_5m": 468,
  "sells_5m": 365
}</pre>

<h2>Notes</h2>
<ul>
  <li>Data comes from DexScreener and may be delayed or inaccurate.</li>
  <li>For information only. Not financial advice.</li>
  <li>Errors (token not found, upstream failure) return an error status, so a failed call should not be charged.</li>
  <li>Hosted on a free tier: the first request after a quiet period can take up to about a minute.</li>
</ul>
<p><small>Experimental project. Payments are real USDC on Solana mainnet.</small></p>
</body>
</html>"""


@app.route("/")
def home():
    return Response(LANDING_HTML, mimetype="text/html")


@app.route("/signal")
def signal():
    mint = request.args.get("mint", "").strip()
    if not mint:
        return jsonify(error="Missing mint parameter"), 400

    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        data = requests.get(url, timeout=10).json()
    except Exception:
        return jsonify(error="Could not fetch data"), 502

    pairs = data.get("pairs") or []
    if not pairs:
        return jsonify(error="Token not found"), 404

    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    txns5 = (p.get("txns") or {}).get("m5") or {}

    return jsonify(
        token=p["baseToken"]["symbol"],
        price_usd=p.get("priceUsd"),
        liquidity_usd=(p.get("liquidity") or {}).get("usd"),
        volume_5m=(p.get("volume") or {}).get("m5"),
        volume_1h=(p.get("volume") or {}).get("h1"),
        buys_5m=txns5.get("buys"),
        sells_5m=txns5.get("sells"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
