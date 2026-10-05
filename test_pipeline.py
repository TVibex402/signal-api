"""The whole signal pipeline against a fake internet (no real network)."""
import asyncio

from fastapi import HTTPException

import main
from helpers import SOL, TOKEN_2022, World

TOK = "Tok" + "x" * 41


def run(coro):
    return asyncio.run(coro)


def setup(world):
    main._cache.clear()
    main._hits.clear()
    client = world.client()
    main.app.state.client = client
    return client


def test_a_normal_token_gets_a_full_signal():
    w = World()
    w.add(TOK)
    r, status = run(main.build_signal(setup(w), TOK, True))
    assert status == "MISS" and r["verdict"] == "ok" and r["asset_class"] == "standard"
    assert r["data_quality"]["completeness"] == "full" and "decision" in r and r["security"]["mint_authority_revoked"] is True


def test_sol_is_ok_even_with_concentrated_holders_and_no_lp_lock():
    w = World()
    w.add(SOL, liq=50_000_000, age_days=800)
    w.holders, w.lp = [100_000] * 9 + [0], 0.0
    r, _ = run(main.build_signal(setup(w), SOL, True))
    assert r["verdict"] == "ok" and r["risk_score"] == 0 and r["asset_class"] == "major"


def test_pumpswap_with_zero_lp_lock_is_a_caution_with_an_honest_note():
    w = World()
    w.add(TOK, liq=5_511, age_days=0.6, dex="pumpswap")
    w.lp = 0.0
    r, _ = run(main.build_signal(setup(w), TOK, True))
    assert "lp_unverified" in r["flags"] and r["verdict"] == "caution" and r["security"]["lp_note"]


def test_open_mint_authority_on_a_small_token_is_avoided():
    w = World()
    w.add(TOK)
    w.mint_authority = "Someone" + "x" * 30
    r, _ = run(main.build_signal(setup(w), TOK, True))
    assert r["verdict"] == "avoid" and "mint_authority_active" in r["flags"]


def test_token2022_zero_fee_is_fine_but_a_permanent_delegate_is_not():
    w = World()
    w.add(TOK)
    w.extensions = [{"extension": "transferFeeConfig", "state": {"newerTransferFee": {"transferFeeBasisPoints": 0, "maximumFee": 0},
                                                                  "olderTransferFee": {"transferFeeBasisPoints": 0, "maximumFee": 0}}}]
    r, _ = run(main.build_signal(setup(w), TOK, True))
    assert r["verdict"] == "ok" and "risky_token_extension" not in r["flags"]
    w.extensions.append({"extension": "permanentDelegate", "state": {"delegate": "Someone" + "x" * 30}})
    main._cache.clear()
    r, _ = run(main.build_signal(w.client(), TOK, True))
    assert r["verdict"] == "avoid" and "permanent_delegate_active" in r["flags"]
    assert TOKEN_2022  # the fake marks the mint as Token-2022 when extensions are present


def test_stale_copy_is_served_when_market_data_is_down():
    w = World()
    w.add(TOK)
    client = setup(w)
    run(main.build_signal(client, TOK, True))
    stamp, data = main._cache[TOK]
    main._cache[TOK] = (stamp - 100, data)           # older than the 30s cache, younger than the stale limit
    w.dex_down = True
    r, status = run(main.build_signal(client, TOK, True))
    assert status == "STALE" and r["stale"] is True and r["data_quality"]["freshness"] == "stale"


def test_outage_without_a_cached_copy_is_a_503_with_retry_after():
    w = World()
    w.add(TOK)
    w.dex_down = True
    try:
        run(main.build_signal(setup(w), TOK, True))
        raise AssertionError("expected an error")
    except HTTPException as e:
        assert e.status_code == 503 and e.headers["Retry-After"]


def test_batch_makes_one_market_request_and_filters_before_the_heavy_checks():
    w = World()
    big, small, mid = "Big" + "x" * 41, "Sml" + "x" * 41, "Mid" + "x" * 41
    w.add(big, liq=500_000)
    w.add(small, liq=20_000)
    w.add(mid, liq=300_000)
    b = run(main.build_batch(setup(w), [big, small, mid], True, min_liq=100_000, sort="liquidity"))
    assert w.dex_calls == 1
    assert [r["mint"] for r in b["results"]] == [big, mid]
    assert [s["mint"] for s in b["skipped"]] == [small]
    assert all(p != small for _, p in w.rpc_calls), "a skipped token must cost no RPC call"


def test_batch_ranks_safest_first_and_reports_bad_input():
    w = World()
    good, risky = "Gd" + "x" * 42, "Rk" + "x" * 42
    w.add(good, liq=900_000)
    w.add(risky, liq=8_000, age_days=0.2)
    b = run(main.build_batch(setup(w), [risky, "not-a-mint", good], True, sort="safest"))
    assert [r["mint"] for r in b["results"]] == [good, risky]
    assert b["errors"] and b["errors"][0]["status"] == 400
    assert b["summary"]["headline"]
