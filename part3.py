# ---------- verdict ----------
REASONS = {
    "mint_authority_active": "Mint authority still active: supply can be inflated",
    "freeze_authority_active": "Freeze authority active: wallets can be frozen",
    "risky_token_extension": "Token-2022 extension that can restrict or tax transfers",
    "lp_not_locked": "Liquidity is not locked or burned",
    "dump_risk": "Price is dumping fast",
    "extreme_holder_concentration": "Top 10 holders own 80%+ of supply",
    "very_low_liquidity": "Liquidity under $20k",
    "low_liquidity": "Liquidity under $50k",
    "high_holder_concentration": "Top 10 holders own 50%+ of supply",
    "dominant_holder": "One wallet holds 20%+ of supply",
    "lp_partially_locked": "Liquidity only partly locked or burned",
    "brand_new_pair": "Pair is under 1 hour old",
    "extreme_turnover": "Volume is extreme vs liquidity (wash-trading risk)",
}
AVOID_FLAGS = ["mint_authority_active", "freeze_authority_active", "risky_token_extension",
               "lp_not_locked", "dump_risk", "extreme_holder_concentration"]
CAUTION_FLAGS = ["very_low_liquidity", "low_liquidity", "high_holder_concentration", "dominant_holder",
                 "lp_partially_locked", "brand_new_pair", "extreme_turnover"]
AUTHORITY_FLAGS = {"mint_authority_active", "freeze_authority_active"}
LP_FLAGS = {"lp_not_locked", "lp_partially_locked"}  # established tokens được miễn trừ LP flags