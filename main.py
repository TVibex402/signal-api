# TVibex402

Free Solana token signal API for AI agents & bots.

Market data from DexScreener · On-chain from Solana RPC · LP lock from RugCheck · Exit quotes from Jupiter.

**Not financial advice.** Verdicts and risk scores are heuristics, not a security audit. The project publishes how often
its verdicts were right (`/accuracy`), including when they were wrong.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Homepage (try it live) |
| GET | `/signal?mint=ADDRESS` | Full signal, verdict, decision object (`would_change_if`, `valid_for_s`) and holder quality (part18) for one token |
| GET | `/signals?mints=A,B,C` | Smart batch (max 10): ranked, filters `min_liq`, `max_age_minutes`, `min_volume_1h`, `sort`, `only`, `max_risk`, `top` |
| GET | `/exit?mint=ADDRESS&sizes=100,1000` | Sell cost via Jupiter (a quote is not proof a sell will succeed) |
| GET | `/stream?mint=ADDRESS` | Live updates for one token (Server-Sent Events) |
| GET | `/accuracy` | Public accuracy dashboard |
| GET | `/stats` | The same measurements as JSON (windows, precision / recall vs a simple baseline) |
| GET | `/methodology` | Exact rules, thresholds, weights in use |
| GET | `/ledger` | Tamper-evident hash chain of every recorded verdict |
| GET | `/pricing` | Plans, live limits, how to get an API key |
| GET | `/metrics` | Request counts, cache hit rate, latency, source health |
| GET | `/health` | Status and what is switched on (shows missing settings, never secrets) |
| GET | `/whoami` | The client IP the server sees (use it to check `TRUSTED_PROXY_HOPS`) |
| GET | `/llms.txt` | Agent-friendly docs |
| GET | `/docs` | OpenAPI |
| POST | `/mcp` | Remote MCP server for Claude and other agents |

```bash
curl "https://YOUR-SERVICE.onrender.com/signal?mint=So11111111111111111111111111111111111111112"
```

## Turn on the track record (5 minutes)

Without it `/accuracy` says "not switched on" and no verdict is judged. It needs a free Upstash Redis database:

1. Create a Redis database at upstash.com (free plan).
2. Copy the **REST URL** and **REST token** (not the `redis://` address).
3. On Render: your service, Environment, add `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`, save, redeploy.
4. Open `/health`: `track_record` must be `true` and `track_record_store.reachable` must be `true`.
5. Keep the free instance awake so it can judge verdicts about a day later: an uptime monitor calling `/health` every 5 minutes.

Rates on `/accuracy` stay hidden until a group has 20+ judged verdicts. Do not quote accuracy numbers before that.

## Environment variables

| Variable | Purpose |
|---|---|
| `SOLANA_RPC_URL` | Primary RPC (a free Helius or QuickNode key is strongly recommended; the public RPC is rate limited) |
| `SOLANA_RPC_FALLBACKS` | Comma-separated fallback RPCs |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Enable the track record and the ledger |
| `JUPITER_API_KEY` | Higher Jupiter rate limit for exit quotes |
| `API_KEYS` | `secret1:label1,secret2:label2`: keys that get a higher rate limit via the `X-API-Key` header |
| `KEY_RATE_LIMIT` | Requests/min for keyed clients (default 300) |
| `GLOBAL_KEY_RESERVE` | Upstream requests/min reserved for keyed clients (default 50) |
| `CONTACT_URL` | `https://` link shown on `/pricing` for requesting a key |
| `AUTO_TUNE` | `true` lets measured outcomes adjust risk weights (default off) |
| `TRUSTED_PROXY_HOPS` | Proxy hops for the real client IP (Render = 1). Check with `/whoami` |
| `STREAM_MAX_PER_IP`, `STREAM_MAX_WATCHED`, `STREAM_MAX_SECONDS`, `STREAM_INTERVAL_S` | Limits for `/stream` |
| `PYTHON_VERSION` | Optional on Render, e.g. `3.12.7` (see below) |

## What a signal contains

Price, FDV, market cap, liquidity, pair age · price change 5m / 1h / 6h / 24h · volume and buy/sell pressure · risk score, flags and a
verdict (ok / caution / avoid) · the **decision object**: blockers, cautions, positives, unknowns, `would_change_if` (what would make each
reason go away), `valid_for_s` (how long the reading stays useful: 10s for fast tokens, 30s normally, 120s for majors, 0 when stale) ·
security: mint and freeze authority, holder concentration, LP lock, Token-2022 details · holder quality score and flags (part18) ·
data quality: freshness, completeness, confidence · `track_record_context` once enough verdicts have been judged.

## Hypotheses

Some flags are shown and measured but not scored (`late_entry_risk`, `one_sided_flow`, `no_recent_trades`). The rules for promoting
or dropping them are written down before the data arrives (`/methodology`), and `/accuracy` also shows the biggest surprises:
verdicts that turned out wrong.

## Tests and CI

```bash
pip install -r requirements.txt pytest
pytest -q
```

`.github/workflows/ci.yml` runs the same on every push, so a bad copy shows up as a red check on GitHub instead of a failed
deploy. The tests use fake upstreams: no network and no real keys.

## How the code is organised

`main.py` is a small loader. The code lives in `part1.py` ... `part19.py` and runs in the order listed in `main.py`
(`part7.py`, the MCP server, always loads last). A checksum of `SKIP` in `main.py` runs a file without checking it: use it only while a file is being edited. All files must sit in the same folder. The loader checks every part and says
which file and which lines to re-check if a copy went wrong. Later parts replace or extend functions from earlier ones, so read
`main.py` for the order.

## Deploy notes

- Python version: Render reads `PYTHON_VERSION` or a `.python-version` file (this repo has one: `3.12`). `runtime.txt` is
  Heroku's format and is ignored by Render, so it can be deleted.
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Optional Docker: `docker build -t tvibex402 . && docker run -p 8080:8080 tvibex402`. The track record needs the Upstash REST
  API, so use a free Upstash database even locally.

## Limits

- Free: 30 requests/min per IP. With `X-API-Key`: higher. A global upstream budget protects the DexScreener quota.
- No uptime guarantee on the free host. Retry after a 503 (the response says when).
- Only tokens that people query are measured, not a random sample of the market.
