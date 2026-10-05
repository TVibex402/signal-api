"""Pure logic: no network. These guard the rules that decide every verdict."""
import hashlib
import time

import main

SOL = "So11111111111111111111111111111111111111112"
DAY = 1440


# ---- ground truth for "is this address a wallet?": a small ed25519 implementation (RFC 8032) ----
_P = 2 ** 255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    p, q = (y1 - x1) * (y2 - x2) % _P, (y1 + x1) * (y2 + x2) % _P
    c, d = 2 * t1 * t2 * _D % _P, 2 * z1 * z2 % _P
    e, f, g, h = q - p, d - c, d + c, q + p
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s, point):
    r = (0, 1, 1, 0)
    while s:
        if s & 1:
            r = _add(r, point)
        point = _add(point, point)
        s >>= 1
    return r


def _base_point():
    y = 4 * pow(5, _P - 2, _P) % _P
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    if x & 1:
        x = _P - x
    return x, y, 1, x * y % _P


def public_key(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    x, y, z, _ = _mul(a, _base_point())
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def test_real_wallet_addresses_are_on_the_curve():
    assert all(main.is_on_curve(public_key(bytes([i]) * 32)) for i in range(20))


def test_program_addresses_are_off_the_curve():
    assert main.is_pda("5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1") is True   # Raydium authority
    assert main.is_pda("GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL") is True   # Raydium CPMM authority
    assert main.is_pda("not-base58-0OIl") is None


# ---- Token-2022 ----
def _fee(new, old=None):
    return {"extension": "transferFeeConfig", "state": {
        "newerTransferFee": {"transferFeeBasisPoints": new, "maximumFee": 5},
        "olderTransferFee": {"transferFeeBasisPoints": new if old is None else old, "maximumFee": 5}}}


def test_token2022_fee_uses_the_worse_of_old_and_new():
    assert main.parse_t22([_fee(0, 600)])["transfer_fee_bps"] == 600
    assert main.parse_t22([_fee(0)])["transfer_fee_bps"] == 0


def test_token2022_details():
    t = main.parse_t22([
        {"extension": "permanentDelegate", "state": {"delegate": "Someone" + "x" * 30}},
        {"extension": "defaultAccountState", "state": {"accountState": "frozen"}},
        {"extension": "transferHook", "state": {"programId": "Hook" + "x" * 30}},
        {"extension": "metadataPointer", "state": {}},
    ])
    assert t["permanent_delegate"] and t["default_account_frozen"] and t["transfer_hook_program"]
    assert t["other"] == ["metadataPointer"]
    assert main.parse_t22([{"extension": "permanentDelegate", "state": {"delegate": "1" * 32}}])["permanent_delegate"] is None
    assert main.parse_t22("junk") is None and main.parse_t22(["junk"]) is None


# ---- verdicts ----
def judge(flags, liq, age, mint="Mint" + "x" * 40, dex="raydium"):
    r = {"token": "T", "mint": mint, "flags": list(flags), "liquidity_usd": liq, "pair_age_minutes": age,
         "dex": dex, "security": {}, "partial": False, "sources": {}}
    main.finalize_risk(r, True)
    main.add_verdict(r)
    return r


def test_sol_is_not_avoided_for_holder_and_lp_flags():
    r = judge(["extreme_holder_concentration", "lp_not_locked"], 50_000_000, 800 * DAY, mint=SOL)
    assert r["asset_class"] == "major" and r["verdict"] == "ok" and r["risk_score"] == 0


def test_established_token_with_open_authority_is_only_a_caution():
    r = judge(["mint_authority_active", "extreme_holder_concentration"], 2_000_000, 90 * DAY)
    assert r["asset_class"] == "established" and r["verdict"] == "caution"


def test_small_token_with_open_authority_is_avoided():
    r = judge(["mint_authority_active"], 300_000, 90 * DAY)
    assert r["verdict"] == "avoid" and r["decision"]["blockers"]


def test_pumpswap_lp_is_unverified_not_failed():
    flags = ["very_low_liquidity", "new_pair", "lp_not_locked"]
    pump = judge(flags, 5_511, 600, dex="pumpswap")
    other = judge(flags, 5_511, 600, dex="raydium")
    assert "lp_unverified" in pump["flags"] and "lp_not_locked" not in pump["flags"]
    assert pump["verdict"] == "caution" and other["verdict"] == "avoid"


def test_permanent_delegate_is_avoided():
    assert judge(["permanent_delegate_active"], 400_000, 30 * DAY)["verdict"] == "avoid"


def test_rank_key_does_not_reward_missing_data():
    complete = {"risk_score": 10, "liquidity_usd": 1, "data_quality": {"data_confidence": 1.0}}
    partial = {"risk_score": 10, "liquidity_usd": 1, "data_quality": {"data_confidence": 0.5}}
    assert main.rank_key(complete) < main.rank_key(partial)


# ---- statistics ----
def test_wilson_interval():
    lo, hi = main.wilson(50, 100)
    assert abs(lo - 0.404) < 0.005 and abs(hi - 0.596) < 0.005
    assert main.wilson(0, 0) == (0.0, 1.0)


def test_flag_lift_is_corrected_for_the_market_background():
    st = {"all:n": "200", "all:bad": "90", "d:20261001:n": "100", "d:20261001:bad": "80", "d:20261002:n": "100", "d:20261002:bad": "10",
          "f:low_liquidity:n": "40", "f:low_liquidity:bad": "32", "fd:low_liquidity:20261001:n": "40", "fd:low_liquidity:20261001:bad": "32"}
    row = {r["flag"]: r for r in main.flag_stats(st)[0]}["low_liquidity"]
    assert row["market_adjusted"] and row["lift_raw"] > 1.5 and row["lift"] == 1.0


def test_precision_recall_and_false_positives_against_the_baseline():
    day = time.strftime("%Y%m%d", time.gmtime())
    st = {f"vd:avoid:full:{day}:n": "100", f"vd:avoid:full:{day}:bad": "70", f"vd:caution:full:{day}:n": "100",
          f"vd:caution:full:{day}:bad": "30", f"vd:ok:full:{day}:n": "200", f"vd:ok:full:{day}:bad": "20",
          f"bd:avoid:{day}:n": "150", f"bd:avoid:{day}:bad": "60", f"bd:ok:{day}:n": "250", f"bd:ok:{day}:bad": "60"}
    w = main.performance_from(st)["last_7d"]
    ours, base = w["ours_avoid"], w["baseline_avoid"]
    assert (ours["precision_pct"], ours["recall_pct"], ours["false_positive_rate_pct"]) == (70.0, 58.3, 10.7)
    assert (base["precision_pct"], base["recall_pct"], base["false_positive_rate_pct"]) == (40.0, 50.0, 32.1)


def test_small_samples_stay_hidden():
    day = time.strftime("%Y%m%d", time.gmtime())
    w = main.performance_from({f"vd:avoid:full:{day}:n": "5", f"vd:avoid:full:{day}:bad": "4"})["last_7d"]
    assert w["ours_avoid"]["precision_pct"] is None and w["verdicts"]["avoid"]["bad_outcome_rate_pct"] is None


# ---- trust features ----
def test_ledger_detects_tampering():
    entries, prev = [], "GENESIS"
    for i in range(3):
        body = {"id": str(i), "verdict": "ok", "prev": prev}
        h = hashlib.sha256(main.canon(body).encode()).hexdigest()
        entries.append(dict(body, hash=h))
        prev = h
    assert main.verify_chain(entries)
    entries[1] = dict(entries[1], verdict="avoid")
    assert not main.verify_chain(entries)


def test_rpc_keys_never_leak_through_error_messages():
    msg = main.redact(f"error for url '{main.RPC_URL}?api-key=SECRET123' and https://x.example/?api-key=abc&foo=1")
    assert "SECRET123" not in msg and "abc" not in msg


def test_accuracy_page_escapes_hostile_token_names():
    rep = {"enabled": True, "available": True, "resolved": 300, "pending": 0, "horizon_hours": 24,
           "by_verdict": {v: {"full": {"n": 100, "bad_outcome_rate_pct": 10.0, "avg_return_pct": None, "avg_excess_return_pct": None}}
                          for v in ("ok", "caution", "avoid")},
           "by_risk_bucket": [], "flags": [], "daily_background": [], "caveats": [],
           "recent_outcomes": [{"token": "<script>alert(1)</script>", "verdict": "ok", "risk_score": 3, "return_pct": -60, "bad_outcome": True}]}
    html = main.render_accuracy(rep, "head")
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<h2>Methodology</h2>" in html


def test_calibration_context_needs_enough_samples():
    main._raw_st.update(ts=time.time(), st={"rb:3:n": "50", "rb:3:bad": "20", "rb:9:n": "5", "rb:9:bad": "5"})
    ctx = main.calibration_for({"risk_score": 35})
    assert ctx["n"] == 50 and ctx["bad_outcome_rate"] == 0.4 and "Not the chance" in ctx["meaning"]
    assert main.calibration_for({"risk_score": 95}) is None
    main._raw_st.update(ts=0.0, st={})
