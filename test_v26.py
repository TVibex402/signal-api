"""v2.6: ideas from the encyclopedia that became code (feedback, pre-mortem, volatility, pre-registered hypotheses, surprises)."""
import time

import main

DAY = 1440


def judged(flags, liq=300_000, age=30 * DAY, status="MISS", mint="Mint" + "x" * 40, **kw):
    r = {"token": "T", "mint": mint, "flags": list(flags), "liquidity_usd": liq, "pair_age_minutes": age, "dex": "raydium",
         "security": {}, "partial": False, "sources": {}, "price_change_pct": {"1h": 5}, "buy_pressure_5m": 0.5, "buys_5m": 30, "sells_5m": 30}
    r.update(kw)
    main.finalize_risk(r, True)
    main.add_verdict(r)
    return main.with_quality(r, status, True)


def test_a_reading_stays_valid_for_less_time_when_the_token_moves_fast():
    assert judged(["dump_risk"])["decision"]["valid_for_s"] == 10
    assert judged([], age=30)["decision"]["valid_for_s"] == 10            # pair younger than an hour
    assert judged([])["decision"]["valid_for_s"] == 30
    assert judged([], liq=2_000_000, age=90 * DAY)["decision"]["valid_for_s"] == 120
    assert judged([], status="STALE")["decision"]["valid_for_s"] == 0     # stale data must be re-checked right away


def test_every_reason_against_a_token_says_what_would_change_it():
    d = judged(["mint_authority_active"])["decision"]
    assert d["verdict"] == "avoid"
    assert d["would_change_if"][0]["flag"] == "mint_authority_active" and "revoked" in d["would_change_if"][0]["if"]
    assert "would_change_if" not in judged([])["decision"]                # nothing to explain when the verdict is ok


def test_pre_mortem_only_lists_reasons_that_are_really_part_of_the_decision():
    d = judged(["mint_authority_active", "extreme_holder_concentration"], liq=2_000_000, age=90 * DAY)["decision"]
    flags = [x["flag"] for x in d["would_change_if"]]
    assert flags == ["mint_authority_active"]                              # holder flags are not scored for established tokens


def test_the_cache_lifetime_backs_off_when_the_market_source_struggles():
    original = main.src_status
    try:
        for state, ttl in (("degraded", 60), ("down", 120), ("ok", 30)):
            main.src_status = lambda s=state: {"dexscreener": {"status": s}}
            assert main.homeostat() == ttl and main.CACHE_TTL == ttl
    finally:
        main.src_status = original
        main.homeostat()


def _hypothesis_state():
    st = {"all:n": "240", "all:bad": "48", "f:late_entry_risk:n": "160", "f:late_entry_risk:bad": "112",
          "f:one_sided_flow:n": "240", "f:one_sided_flow:bad": "48"}
    for i in range(8):
        day = time.strftime("%Y%m%d", time.gmtime(time.time() - i * 86400))
        st.update({f"d:{day}:n": "30", f"d:{day}:bad": "6", f"fd:late_entry_risk:{day}:n": "20", f"fd:late_entry_risk:{day}:bad": "14",
                   f"fd:one_sided_flow:{day}:n": "30", f"fd:one_sided_flow:{day}:bad": "6"})
    return st


def test_hypotheses_are_promoted_or_dropped_by_rules_written_in_advance():
    status = {h["flag"]: h["status"] for h in main.hypotheses_report(_hypothesis_state())}
    assert status["late_entry_risk"].startswith("promote")
    assert status["one_sided_flow"].startswith("drop")
    assert status["no_recent_trades"] == "collecting"
    m = main.methodology_report()
    assert m["hypotheses"]["rules"] and m["will_not_say"] and m["adaptive_cache"]["base_ttl_s"] == 30


def test_the_accuracy_page_shows_where_the_verdicts_were_wrong():
    recent = [{"token": "<img src=x onerror=alert(1)>", "verdict": "ok", "risk_score": 4, "return_pct": -70, "bad_outcome": True},
              {"token": "SAFE", "verdict": "avoid", "risk_score": 62, "return_pct": 12, "bad_outcome": False}]
    html = main.render_accuracy({"enabled": False, "recent_outcomes": recent}, None)
    assert "Biggest surprises" in html and "Called ok or caution, ended badly (1)" in html and "Called avoid, did fine (1)" in html
    assert "<img src=x" not in html and "&lt;img" in html
    assert html.index("Biggest surprises") < html.index("Hypotheses under test") < html.index("<h2>Methodology</h2>")
