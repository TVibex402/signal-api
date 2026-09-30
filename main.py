import requests
from flask import Flask, jsonify, request

from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync, PaymentOption
from x402.http.middleware.flask import payment_middleware
from x402.http.types import RouteConfig
from x402.mechanisms.svm.exact import ExactSvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServerSync

app = Flask(__name__)

# Ví nhận tiền (địa chỉ công khai)
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


@app.route("/")
def home():
    return jsonify(message="signal-api đang chạy")


@app.route("/signal")
def signal():
    mint = request.args.get("mint", "").strip()
    if not mint:
        return jsonify(error="Thiếu tham số mint"), 400

    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        data = requests.get(url, timeout=10).json()
    except Exception:
        return jsonify(error="Không lấy được dữ liệu"), 502

    pairs = data.get("pairs") or []
    if not pairs:
        return jsonify(error="Không tìm thấy token"), 404

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
