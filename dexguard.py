"""dexguard.py - DexScreener access layer for TVibex402.

Fixes the 429 -> 503 problem:
  * fresh cache (default 15s) + stale-if-error (default 10 min)
  * request coalescing: same mint asked 20x at once = 1 upstream call
  * micro-batching: mints asked within 50ms share ONE call (max 30 per call)
  * 429 cooldown: honours Retry-After, stops hammering, serves stale meanwhile
Usage:
    guard = DexGuard(httpx_async_client)
    pairs, age_s = await guard.get_pairs(mint)   # pairs sorted by liquidity desc
"""
import asyncio
import time

BASE = "https://api.dexscreener.com/tokens/v1/solana/"
MAX_BATCH = 30
MAX_CACHE = 5000


class DexRateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"DexScreener rate limited, retry in {retry_after}s")
        self.retry_after = int(retry_after)


class DexGuard:
    def __init__(self, client, fresh_ttl=15.0, stale_ttl=600.0,
                 window=0.05, default_cooldown=30):
        self.client = client
        self.fresh_ttl, self.stale_ttl = fresh_ttl, stale_ttl
        self.window, self.default_cooldown = window, default_cooldown
        self._cache = {}        # mint -> (timestamp, pairs)
        self._waiting = {}      # mint -> Future (collected during the window)
        self._flusher = None
        self._cooldown_until = 0.0
        self.stats = {"hits": 0, "stale": 0, "upstream": 0, "r429": 0}

    def _cached(self, mint, max_age):
        item = self._cache.get(mint)
        if item:
            age = time.time() - item[0]
            if age <= max_age:
                return item[1], age
        return None

    def _stale_or_raise(self, mint, retry_after):
        hit = self._cached(mint, self.stale_ttl)
        if hit is None:
            raise DexRateLimited(retry_after)
        self.stats["stale"] += 1
        return hit

    async def get_pairs(self, mint):
        hit = self._cached(mint, self.fresh_ttl)
        if hit is not None:
            self.stats["hits"] += 1
            return hit
        wait = self._cooldown_until - time.time()
        if wait > 0:
            return self._stale_or_raise(mint, int(wait) + 1)

        fut = self._waiting.get(mint)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._waiting[mint] = fut
            if self._flusher is None:
                self._flusher = asyncio.create_task(self._flush())
        try:
            pairs = await asyncio.shield(fut)
            return pairs, 0.0
        except DexRateLimited as e:
            return self._stale_or_raise(mint, e.retry_after)
        except Exception:
            hit = self._cached(mint, self.stale_ttl)
            if hit is None:
                raise
            self.stats["stale"] += 1
            return hit

    async def _flush(self):
        await asyncio.sleep(self.window)
        batch, self._waiting = self._waiting, {}
        self._flusher = None
        if len(self._cache) > MAX_CACHE:
            cutoff = time.time() - self.stale_ttl
            self._cache = {m: v for m, v in self._cache.items() if v[0] >= cutoff}
        mints = list(batch)
        for i in range(0, len(mints), MAX_BATCH):
            await self._fetch_chunk(mints[i:i + MAX_BATCH], batch)

    async def _fetch_chunk(self, chunk, batch):
        try:
            r = await self.client.get(BASE + ",".join(chunk), timeout=6.0)
            self.stats["upstream"] += 1
            if r.status_code == 429:
                self.stats["r429"] += 1
                try:
                    ra = int(r.headers.get("retry-after", self.default_cooldown))
                except ValueError:
                    ra = self.default_cooldown
                self._cooldown_until = time.time() + ra
                raise DexRateLimited(ra)
            r.raise_for_status()
            by = {m: [] for m in chunk}
            for p in r.json() or []:
                addr = (p.get("baseToken") or {}).get("address")
                if addr in by:
                    by[addr].append(p)
            now = time.time()
            for m in chunk:
                by[m].sort(key=lambda p: (p.get("liquidity") or {}).get("usd") or 0,
                           reverse=True)
                self._cache[m] = (now, by[m])
                if not batch[m].done():
                    batch[m].set_result(by[m])
        except Exception as e:  # always resolve every waiting future
            for m in chunk:
                if not batch[m].done():
                    batch[m].set_exception(e)
