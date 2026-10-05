# ---------- v2.4: methodology page and the new accuracy-page sections ----------
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).


def methodology_report() -> dict:
    """Everything that decides a verdict, in the open."""
    return {
        "version": VERSION,
        "verdict_rules": {
            "summary": ("avoid if any avoid-flag is present or the risk score is 50 or more; caution if any caution-flag is present "
                        "or the score is 25 or more; otherwise ok. Majors (wrapped SOL, USDC, USDT) skip rug-style checks; "
                        "established tokens (liquidity $1M+, older than 30 days) show them without scoring them."),
            "avoid_flags": list(AVOID_FLAGS), "caution_flags": list(CAUTION_FLAGS),
            "rug_style_flags": sorted(RUG_STYLE_FLAGS), "avoid_score_threshold": 50, "caution_score_threshold": 25,
        },
        "risk_score": {
            "how": "sum of the weights of the flags present, capped at 100",
            "scoring": "tuned" if _tune["on"] else "static",
            "weights_in_use": dict(sorted(ACTIVE_WEIGHTS.items())),
            "weights_default": dict(sorted(RISK_WEIGHTS.items())),
        },
        "auto_tune": {
            "enabled": AUTO_TUNE, "currently_tuned": bool(_tune["on"]), "judged_samples": _tune["n"],
            "how": ("Each flag's weight is blended (at most 50%) toward a value suggested by how much that flag raised the "
                    "bad-outcome rate versus the same day's overall rate. It only starts once a flag has 30+ judged samples and "
                    "100+ judged tokens exist. Default: off."),
        },
        "outcomes": {
            "horizon_hours": round(PRED_HORIZON_S / 3600, 1),
            "bad_outcome": f"price down {abs(BAD_RETURN_PCT):.0f}%+, or liquidity down {round((1 - BAD_LIQ_RATIO) * 100)}%+, or the pair disappeared",
            "judged": "every verdict is stored, then judged once after the horizon; a tamper-evident hash chain is at /ledger",
            "minimum_samples_to_show_a_rate": MIN_N_SHOW,
        },
        "baseline": {
            "definition": "avoid anything with liquidity under $50k or a pair younger than 6 hours",
            "why": "A signal that cannot beat two simple rules is not worth the complexity. The accuracy page shows both side by side.",
        },
        "data_sources": ["DexScreener (market)", "Solana RPC (authorities, holders)", "RugCheck (LP lock)", "Jupiter (exit quotes)"],
        "limits": [
            "Verdicts are heuristics, not advice and not a security audit.",
            "Only tokens that people query are measured, not a random sample of the market.",
            "A bad outcome can come from ordinary market moves, not only from rugs.",
            "A Jupiter quote does not prove a real sell will succeed.",
        ],
    }


@app.get("/methodology", tags=["trust"], summary="Exactly how verdicts and risk scores are computed")
async def methodology_route(request: Request):
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    return JSONResponse(content=methodology_report(), headers={"Cache-Control": "public, max-age=300"})


def _pc(v: Any) -> str:
    return "-" if v is None else _h(f"{v}%")


def render_performance(perf: dict) -> str:
    rows = ""
    for key, label in (("all_tracked", "Since tracking started"), ("last_30d", "Last 30 days"), ("last_7d", "Last 7 days")):
        w = perf.get(key) or {}
        a, b = w.get("ours_avoid") or {}, w.get("baseline_avoid") or {}
        rows += ("<tr><td>" + _h(label) + "</td><td>" + _h(w.get("judged", 0)) + "</td><td>" + _h(a.get("flagged", 0)) + "</td>"
                 "<td>" + _pc(a.get("precision_pct")) + "</td><td>" + _pc(a.get("recall_pct")) + "</td><td>" + _pc(a.get("false_positive_rate_pct")) + "</td>"
                 "<td>" + _pc(b.get("precision_pct")) + "</td><td>" + _pc(b.get("recall_pct")) + "</td><td>" + _pc(b.get("false_positive_rate_pct")) + "</td></tr>")
    return ('<h2>Our "avoid" against a simple baseline</h2><p class="muted">Precision: of the tokens flagged, how many ended badly. '
            "Recall: of all bad outcomes, how many we flagged. False positives: of the tokens that did fine, how many we flagged anyway. "
            "Baseline: avoid anything with liquidity under $50k or a pair younger than 6 hours. A number stays hidden until it rests on "
            "20+ samples. If we do not beat the baseline, this table will show it.</p>"
            "<table><tr><th>Window</th><th>Judged</th><th>Flagged</th><th>Precision</th><th>Recall</th><th>False pos.</th>"
            "<th>Base precision</th><th>Base recall</th><th>Base false pos.</th></tr>" + rows + "</table>")


def render_methodology() -> str:
    m = methodology_report()
    weights = sorted(m["risk_score"]["weights_in_use"].items(), key=lambda kv: -kv[1])
    rows = "".join("<tr><td>" + _h(k) + "</td><td>" + _h(v) + "</td></tr>" for k, v in weights if v)
    tune = m["auto_tune"]
    return ('<h2>Methodology</h2><p class="muted">' + _h(m["verdict_rules"]["summary"]) + "</p>"
            '<p class="muted">Weights in use are ' + _h(m["risk_score"]["scoring"]) + ". Auto-tuning is "
            + ("on" if tune["enabled"] else "off") + ". " + _h(tune["how"]) + "</p>"
            "<table><tr><th>Flag</th><th>Points</th></tr>" + rows + "</table>"
            '<p class="muted">Machine-readable version: <a href="/methodology">/methodology</a></p>')


_render_accuracy_v23 = render_accuracy


def render_accuracy(rep: dict, head: Optional[str]) -> str:
    page = _render_accuracy_v23(rep, head)
    extra = (render_performance(rep["performance"]) if rep.get("performance") else "") + render_methodology()
    marker = "<h2>How we measure</h2>"
    return page.replace(marker, extra + marker, 1) if marker in page else page


LLMS_TXT = LLMS_TXT + """
## Methodology and performance
GET /methodology: the exact rules, thresholds, weights in use, auto-tuning rules, baseline and limits.
GET /stats now includes `performance`: precision, recall and false-positive rate of our "avoid" per window (since tracking started,
30 days, 7 days) next to a simple baseline (liquidity under $50k or pair younger than 6 hours). Rates stay null below 20 samples.
Signals can carry `track_record_context`: how similar past verdicts (same risk-score band) ended, with n and a 95% interval.
It is a historical rate, not the chance that this token will fail.
"""
