"""A fake internet for the tests: DexScreener, RugCheck, Solana RPC and Jupiter, all in memory."""
import json
import time

import httpx

SOL = "So11111111111111111111111111111111111111112"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


class World:
    def __init__(self):
        self.pairs = {}            # mint -> DexScreener pair
        self.dex_down = False
        self.dex_429 = False       # NEW: simulate rate limit
        self.dex_calls = 0
        self.rpc_calls = []        # (method, first param)
        self.lp = 100.0            # what RugCheck says
        self.mint_authority = None
        self.holders = [10000] * 10   # token amounts of the 10 largest accounts (supply is 1,000,000)
        self.extensions = None     # Token-2022 extension list, or None for a plain SPL token
        self.jup = None            # "no_route" or None
        self.jup_loss = 0.0

    def add(self, mint, liq=300_000, age_days=30, vol_1h=1000.0, dex="raydium"):
        self.pairs[mint] = {
            "chainId": "solana", "dexId": dex, "pairAddress": "Pair" + mint[:6],
            "baseToken": {"address": mint, "symbol": mint[:4], "name": mint[:4]},
            "quoteToken": {"address": "USDCquote"}, "priceUsd": "1.0",
            "liquidity": {"usd": liq}, "txns": {"m5": {"buys": 5, "sells": 5}},
            "volume": {"m5": 10, "h1": vol_1h, "h6": vol_1h * 5},
            "priceChange": {"m5": 0, "h1": 0, "h6": 0, "h24": 0},
            "pairCreatedAt": int((time.time() - age_days * 86400) * 1000),
        }

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def handler(self, request):
        host, path = request.url.host, request.url.path
        if host == "api.dexscreener.com":
            self.dex_calls += 1
            if self.dex_429:
                return httpx.Response(429, json={"error": "Too Many Requests"})
            if self.dex_down:
                return httpx.Response(503, json={"error": "down"})
            wanted = path.rsplit("/", 1)[-1].split(",")
            return httpx.Response(200, json={"pairs": [self.pairs[m] for m in wanted if m in self.pairs]})
        if host == "api.rugcheck.xyz":
            return httpx.Response(200, json={"lpLockedPct": self.lp, "score_normalised": 5, "risks": []})
        if host == "api.mainnet-beta.solana.com":
            body = json.loads(request.content)
            method, params = body["method"], body["params"]
            self.rpc_calls.append((method, params[0] if isinstance(params[0], str) else None))
            if method == "getAccountInfo":
                owner = TOKEN_2022 if self.extensions is not None else "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                info = {"mintAuthority": self.mint_authority, "freezeAuthority": None, "supply": "1000000", "decimals": 6}
                if self.extensions is not None:
                    info["extensions"] = self.extensions
                return httpx.Response(200, json={"result": {"value": {"owner": owner, "data": {"parsed": {"type": "mint", "info": info}}}}})
            if method == "getTokenLargestAccounts":
                return httpx.Response(200, json={"result": {"value": [{"address": f"Acct{i}", "amount": str(a)} for i, a in enumerate(self.holders)]}})
            if method == "getMultipleAccounts":
                return httpx.Response(200, json={"result": {"value": [{"data": {"parsed": {"info": {"owner": "Wallet" + a}}}} for a in params[0]]}})
        # Jupiter quote (simple)
        if "jupiter" in host or "quote-api" in host:
            if self.jup == "no_route":
                return httpx.Response(404, json={"error": "no route"})
            # return a fake quote with optional loss
            return httpx.Response(200, json={"outAmount": str(int(1000000 * (1 - self.jup_loss))), "inAmount": "1000000"})
        return httpx.Response(404, json={"error": "not faked"})