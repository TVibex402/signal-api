import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

# The tests must never talk to a real Redis, API key list or tuning switch.
for name in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "API_KEYS", "AUTO_TUNE", "SOLANA_RPC_URL", "SOLANA_RPC_FALLBACKS"):
    os.environ.pop(name, None)
