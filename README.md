# TVibex402

Free Solana token signal API for AI agents & bots.

Market data from DexScreener · On-chain from Solana RPC · LP lock from RugCheck · Exit quotes from Jupiter.

**Not financial advice.** Risk scores and verdicts are heuristics.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

```bash
curl "http://127.0.0.1:8080/signal?mint=So11111111111111111111111111111111111111112"
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Homepage (try live) |
| GET | `/signal?mint=ADDRESS` | Full signal, verdict and decision object for one token |
| GET | `/signals?mints=A,B,C` | Smart batch (max 10), ranked |
| GET | `/exit?mint=ADDRESS&sizes=100,1000` | Sell cost via Jupiter |
| GET | `/stats` | Measured accuracy (JSON) |
| GET | `/accuracy` | Human accuracy dashboard |
| GET | `/ledger` | Tamper-evident verdict ledger |
| GET | `/metrics` | Request counts, latency, source health |
| GET | `/health` | Health check |
| GET | `/whoami` | The client IP the server sees (use it to check `TRUSTED_PROXY_HOPS`) |
| GET | `/llms.txt` | Agent-friendly docs |
| GET | `/docs` | OpenAPI |
| POST | `/mcp` | Remote MCP server (Claude & agents) |

## Environment variables (optional but recommended)

| Variable | Purpose |
|---|---|
| `SOLANA_RPC_URL` | Primary RPC (Helius / QuickNode free tier strongly recommended) |
| `SOLANA_RPC_FALLBACKS` | Comma-separated fallback RPCs |
| `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` | Enable track record + ledger |
| `JUPITER_API_KEY` | Higher Jupiter rate limit |
| `API_KEYS` | `secret1:label1,secret2:label2`, higher rate limit via `X-API-Key` |
| `KEY_RATE_LIMIT` | Rate limit for keyed clients (default 300/min) |
| `AUTO_TUNE` | `true` to auto-adjust risk weights from measured outcomes |
| `TRUSTED_PROXY_HOPS` | Proxy hops for real client IP (Render = 1). Check with `/whoami` |
| `GLOBAL_KEY_RESERVE` | Upstream requests/min reserved for keyed clients (default 50) |
| `PYTHON_VERSION` | Fully qualified Python version for Render, e.g. `3.12.7` (see below) |

## Rate limits

- Anonymous: 30 requests/min per IP
- With `X-API-Key`: higher (default 300/min)
- A global upstream budget protects the DexScreener quota; clients with an API key have their own reserved lane in it

## How the code is organised

`main.py` is a small loader. The code lives in `part1.py` ... `part10.py` and runs in the order listed in `main.py`
(`part7.py`, the MCP server, always loads last). All files must sit in the same folder. The loader checks every part
and says which file and which lines to re-check if a copy went wrong.

## Deploy notes

Render reads the Python version from the `PYTHON_VERSION` environment variable or from a `.python-version` file
(it does not document `runtime.txt`). Check the build log line that says which Python was used, and set one of the two if
you want 3.12. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
