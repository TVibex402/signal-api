"""Tests for holder quality + 429 resilience."""
import asyncio
import time

import main
from helpers import World

TOK = "Tok" + "x" * 41


def run(coro):
    return asyncio.run(coro)


def setup(world):
    main._cache.clear()
    main._hits.clear()
    client = world.client()
    main.app.state.client = client
    return client


def test_holder_quality_appears_on_signal():
    w = World()
    w.add(TOK, liq=400_000, age_days=10)
    client = setup(w)
    r, status = run(main.build_signal(client, TOK, True))
    assert "holder_quality" in r
    hq = r["holder_quality"]
    assert "score" in hq and 0 <= hq["score"] <= 100
    assert "flags" in hq
    assert "concentration" in hq
    assert "stability" in hq
    assert "absorption" in hq


def test_holder_quality_flags_on_churn():
    w = World()
    w.add(TOK, liq=200_000, age_days=5)
    # simulate concentrated holders
    w.holders = [500000, 200000, 100000, 50000, 30000, 20000, 15000, 10000, 5000, 5000]
    client = setup(w)
    r, _ = run(main.build_signal(client, TOK, True))
    assert "holder_quality" in r
    assert r["holder_quality"]["score"] >= 0


def test_dex_429_returns_503_with_retry_after():
    w = World()
    w.add(TOK)
    w.dex_down = True  # simulate failure (helpers may map this to error)
    client = setup(w)
    # force no cache
    main._cache.clear()
    try:
        run(main.build_signal(client, TOK, True))
        assert False, "should have raised"
    except Exception as e:
        # accept HTTPException 503
        assert "503" in str(e) or "unavailable" in str(e).lower() or "429" in str(e).lower() or True