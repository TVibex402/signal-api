# TVibex402

Free Solana token signal API for AI agents & bots.

Market data from DexScreener · On-chain from Solana RPC · LP lock from RugCheck · Exit quotes from Jupiter.

**Not financial advice.** Risk scores and verdicts are heuristics.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8080
Method	Path	Description
GET	/	Homepage (try live)
GET	/signal?mint=ADDRESS	Full signal + verdict for one token
GET	/signals?mints=A,B,C	Smart batch (max 10), ranked
GET	/exit?mint=ADDRESS&sizes=100,1000	Sell cost via Jupiter
GET	/stats	Measured accuracy (JSON)
GET	/accuracy	Human accuracy dashboard
GET	/ledger	Tamper-evident verdict ledger
GET	/metrics	Request counts, latency, source health
GET	/health	Health check
GET	/llms.txt	Agent-friendly docs
GET	/docs	OpenAPI
POST	/mcp	Remote MCP server (Claude & agents)
Example:

bash
curl "http://127.0.0.1:8080/signal?mint=So11111111111111111111111111111111111111112"
Environment variables (optional but recommended)
Variable	Purpose
SOLANA_RPC_URL	Primary RPC (Helius / QuickNode free tier strongly recommended)
SOLANA_RPC_FALLBACKS	Comma-separated fallback RPCs
UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN	Enable track record + ledger
JUPITER_API_KEY	Higher Jupiter rate limit
API_KEYS	secret1:label1,secret2:label2 — higher rate limit via X-API-Key
KEY_RATE_LIMIT	Rate limit for keyed clients (default 300/min)
AUTO_TUNE	true to auto-adjust risk weights from outcomes
TRUSTED_PROXY_HOPS	Proxy hops for real client IP (Render = 1)
Rate limits
Anonymous: 30 req/min per IP
With X-API-Key: higher (default 300/min)
Global upstream budget protects DexScreener quota
Deploy notes
Python 3.12 (runtime.txt)
curl "http://127.0.0.1:8080/signal?mint=So11111111111111111111111111111111111111112
Variable	Purpose
SOLANA_RPC_URL	Primary RPC (Helius / QuickNode free tier strongly recommended)
SOLANA_RPC_FALLBACKS	Comma-separated fallback RPCs
UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN	Enable track record + ledger
JUPITER_API_KEY	Higher Jupiter rate limit
API_KEYS	secret1:label1,secret2:label2 — higher rate limit via X-API-Key
KEY_RATE_LIMIT	Rate limit for keyed clients (default 300/min)
AUTO_TUNE	true to auto-adjust risk weights from outcomes
TRUSTED_PROXY_HOPS	Proxy hops for real client IP (Render = 1)
Rate limits
Anonymous: 30 req/min per IP
With X-API-Key: higher (default 300/min)
Global upstream budget protects DexScreener quota