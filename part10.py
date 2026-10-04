# ---------- v2.1: public accuracy dashboard + docs ----------
import html as _html

_head_cache: Dict[str, Any] = {"ts": 0.0, "head": None}


def _h(x: Any) -> str:
    return _html.escape(str(x if x is not None else "-"), quote=True)


def _num(v: Any, suffix: str = "") -> str:
    return "-" if v is None else f"{v}{suffix}"


def _rate_cell(rate_pct: Optional[float], n: int) -> str:
    if rate_pct is None:
        return f'<td class="muted">hidden (n={n}, needs {MIN_N_SHOW}+)</td>'
    w = max(0.0, min(100.0, float(rate_pct)))
    return f'<td><div class="bar"><span style="width:{w:.0f}%"></span></div> {w:.1f}% <span class="muted">(n={n})</span></td>'


def render_accuracy(rep: dict, head: Optional[str]) -> str:
    body = []
    if not rep.get("enabled"):
        body.append('<div class="card">The track record is not switched on yet on this server. When it is, every verdict is recorded '
                    "and judged about a day later, and the results appear here.</div>")
    elif rep.get("available") is False:
        body.append('<div class="card">The statistics store is temporarily unavailable. Please try again in a minute.</div>')
    else:
        resolved, pending = int(rep.get("resolved") or 0), int(rep.get("pending") or 0)
        if resolved < MIN_N_SHOW:
            body.append(f'<div class="card"><b>Collecting data.</b> {resolved} verdicts judged so far, {pending} waiting for their '
                        f"{_h(rep.get('horizon_hours'))}h mark. Rates appear once a group has {MIN_N_SHOW}+ samples. "
                        "Nothing is hidden or adjusted by hand.</div>")
        rows = "".join(
            f"<tr><td><b>{_h(v)}</b></td>{_rate_cell(g.get('bad_outcome_rate_pct'), g.get('n', 0))}"
            f"<td>{_num(g.get('avg_return_pct'), '%')}</td><td>{_num(g.get('avg_excess_return_pct'), '%')}</td></tr>"
            for v, d in (rep.get("by_verdict") or {}).items() for g in [d.get("full") or {}])
        body.append("<h2>Bad outcomes by verdict</h2><table><tr><th>Verdict</th><th>Share with a bad outcome</th>"
                    f"<th>Avg return</th><th>Avg vs SOL</th></tr>{rows}</table>")
        gap = rep.get("avoid_vs_ok_gap_pts")
        if gap is not None:
            word = "higher" if gap >= 0 else "LOWER (the verdicts are not working here)"
            body.append(f'<p class="note">Tokens marked <b>avoid</b> had a bad-outcome rate {abs(gap)} points {word} than tokens marked <b>ok</b>.</p>')
        brows = "".join(f"<tr><td>{_h(b['risk_score_range'])}</td>{_rate_cell(b.get('bad_outcome_rate_pct'), b.get('n', 0))}</tr>"
                        for b in rep.get("by_risk_bucket") or [] if b.get("n"))
        if brows:
            body.append(f"<h2>Does a higher risk score mean more bad outcomes?</h2><table><tr><th>Risk score</th><th>Share with a bad outcome</th></tr>{brows}</table>")
        drows = "".join(f"<tr><td>{_h(c['day'])}</td><td>{_h(c['n'])}</td><td>{_num(c.get('bad_outcome_rate_pct'), '%')}</td></tr>"
                        for c in rep.get("daily_background") or [])
        if drows:
            body.append("<h2>The market background, day by day</h2><p class=\"muted\">Share of all judged tokens with a bad outcome that day. "
                        f"Flag statistics are corrected for this.</p><table><tr><th>Day</th><th>Judged</th><th>Bad outcomes</th></tr>{drows}</table>")
        frows = "".join(f"<tr><td>{_h(f['flag'])}</td><td>{_h(f['n'])}</td><td>{_num(f.get('bad_rate_pct'), '%')}</td>"
                        f"<td>{_num(f.get('lift'))}{' (adjusted)' if f.get('market_adjusted') else ''}</td></tr>"
                        for f in rep.get("flags") or [] if f.get("n"))
        if frows:
            body.append(f"<h2>Flags</h2><table><tr><th>Flag</th><th>Judged</th><th>Bad rate</th><th>Lift</th></tr>{frows}</table>")
        rrows = "".join(f"<tr><td>{_h(r.get('token'))}</td><td>{_h(r.get('verdict'))}</td><td>{_h(r.get('risk_score'))}</td>"
                        f"<td>{_num(r.get('return_pct'), '%')}</td><td>{'bad' if r.get('bad_outcome') else 'ok'}</td></tr>"
                        for r in (rep.get("recent_outcomes") or [])[:10])
        if rrows:
            body.append(f"<h2>Latest judged verdicts</h2><table><tr><th>Token</th><th>Verdict</th><th>Risk</th><th>Return</th><th>Outcome</th></tr>{rrows}</table>")
    caveats = "".join(f"<li>{_h(c)}</li>" for c in rep.get("caveats") or [])
    ledger = (f'<p>Ledger head: <code>{_h(head)}</code></p>' if head else "")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVibex402 accuracy</title><style>
:root{{--bg:#070b12;--card:rgba(18,25,35,.8);--line:rgba(34,48,66,.8);--text:#e8eef6;--muted:#8b9bb4;--a:#14f195;--b:#9945ff}}
*{{box-sizing:border-box;margin:0;padding:0}}body{{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;line-height:1.55}}
.wrap{{max-width:760px;margin:0 auto;padding:32px 16px 70px}}h1{{font-size:2rem;background:linear-gradient(120deg,var(--b),var(--a));-webkit-background-clip:text;background-clip:text;color:transparent}}
h2{{font-size:1.1rem;margin:26px 0 10px}}.muted{{color:var(--muted);font-size:.88rem}}.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:16px 0}}
table{{width:100%;border-collapse:collapse;font-size:.9rem;display:block;overflow-x:auto}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
.bar{{display:inline-block;width:90px;height:8px;background:var(--line);border-radius:6px;vertical-align:middle;overflow:hidden}}.bar span{{display:block;height:100%;background:var(--b)}}
.note{{margin:10px 0;padding:10px 12px;border-left:3px solid var(--a);background:var(--card)}}a{{color:var(--a)}}code{{word-break:break-all;font-size:.78rem}}li{{margin:4px 0 4px 18px}}
</style></head><body><div class="wrap"><h1>Measured accuracy</h1>
<p class="muted">Every verdict this API gives is recorded, then judged about {_h(rep.get('horizon_hours', 24))} hours later. This page shows what actually happened, including when the verdicts are wrong.</p>
{''.join(body)}
<h2>How we measure</h2><p class="muted">{_h(rep.get('bad_outcome_definition', 'A bad outcome means price down 50%+, liquidity down 70%+, or the pair disappeared.'))}</p>
<ul class="muted">{caveats}</ul>{ledger}
<p class="muted" style="margin-top:14px"><a href="/ledger">Tamper-evident ledger</a> &bull; <a href="/stats">Raw JSON</a> &bull; <a href="/">Home</a></p>
<p class="muted" style="margin-top:18px">Heuristic data, not financial advice. Past measurements do not predict future results.</p>
</div></body></html>"""


@app.get("/accuracy", response_class=HTMLResponse, tags=["trust"], summary="Public accuracy dashboard")
async def accuracy_page(request: Request):
    wait = rate_limited(client_ip(request))
    if wait:
        raise too_many(wait)
    client = request.app.state.client
    rep = await stats_report(client)
    if PRED_ENABLED and time.time() - _head_cache["ts"] > 60:
        got = await redis_pipe(client, [["GET", "ledger_head"]])
        _head_cache.update(ts=time.time(), head=got[0] if got else None)
    return render_accuracy(rep, _head_cache["head"] if PRED_ENABLED else None)


# --- docs: links on the home page and in llms.txt ---
if '<a href="/stats">/stats</a>' in HOME_HTML:
    HOME_HTML = HOME_HTML.replace('<a href="/stats">/stats</a>',
                                  '<a href="/accuracy">/accuracy</a> &bull; <a href="/ledger">/ledger</a> &bull; <a href="/stats">/stats</a>')
LLMS_TXT = LLMS_TXT + """
## Decision object
Every signal has `decision`: verdict, blockers (hard reasons to avoid), cautions, positives, unknowns (what is missing or was NOT
checked), next_checks, valid_for_s (how long this stays valid), confidence (0-1: completeness and freshness of the data, NOT the
chance that the verdict is right) and asset_class (major | established | standard).
Majors (wrapped SOL, USDC, USDT) skip rug-style checks. Established tokens (liquidity >= $1M, pair older than 30 days) show
holder and LP flags but do not score them (flags_not_scored); active authorities still give at most a caution.

## Trust
GET /accuracy  human dashboard of measured outcomes.  GET /stats  same data as JSON.
GET /ledger  hash-chained list of every recorded verdict (tamper-evident: each entry contains the hash of the previous one).
Optional header X-API-Key gives a higher rate limit (keys are issued by the operator).
"""
