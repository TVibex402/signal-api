import requests
from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify(message="Chạy được rồi")


@app.route("/signal/<mint>")
def signal(mint):
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    data = requests.get(url, timeout=10).json()
    pairs = data.get("pairs") or []
    if not pairs:
        return jsonify(error="Không tìm thấy token")

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
