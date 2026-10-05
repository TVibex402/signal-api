# ---------- v2.2: honest PumpSwap LP status + richer home page ----------
# Adds or replaces things from earlier parts (same shared namespace). Loads before part7 (MCP).
VERSION = "2.2.0"
app.version = VERSION
app.openapi_schema = None  # rebuild /docs with the new version

# --- PumpSwap: RugCheck cannot tell a burned-at-graduation pool from a directly created one ---
PUMPSWAP_LP_NOTE = ("PumpSwap pools created by pump.fun graduation burn their LP automatically, but pools created directly "
                    "may not, and RugCheck cannot tell which this is. LP status is unverified, not confirmed bad.")
REASONS["lp_unverified"] = "Liquidity lock/burn could not be verified (PumpSwap pool)"
RISK_WEIGHTS.setdefault("lp_unverified", 10)
ACTIVE_WEIGHTS.setdefault("lp_unverified", 10)
if "lp_unverified" not in CAUTION_FLAGS:
    CAUTION_FLAGS.append("lp_unverified")
RUG_STYLE_FLAGS.add("lp_unverified")
_finalize_risk_v21 = finalize_risk


def finalize_risk(result: dict, onchain: bool):
    if result.get("dex") == "pumpswap" and {"lp_not_locked", "lp_partially_locked"} & set(result["flags"]):
        result["flags"] = [f for f in result["flags"] if f not in ("lp_not_locked", "lp_partially_locked")] + ["lp_unverified"]
        if isinstance(result.get("security"), dict):
            result["security"]["lp_note"] = PUMPSWAP_LP_NOTE
    _finalize_risk_v21(result, onchain)


_with_quality_v21 = with_quality


def with_quality(result: dict, status: str, security: bool) -> dict:
    out = _with_quality_v21(result, status, security)
    d = out.get("decision")
    if d and "lp_unverified" in out.get("flags", []):
        out["decision"] = dict(d, unknowns=list(d.get("unknowns") or []) + ["LP burn/lock is unverified for this PumpSwap pool"])
    return out


# --- home page: decision panels, data quality line, exit button (patched into the existing page) ---
_HOME_CSS = """
.why{border-left:3px solid var(--line);background:rgba(13,17,23,.6);border-radius:10px;padding:8px 12px;margin:10px 0;font-size:.88rem}
.why ul{margin:4px 0 0 18px}.why.bad{border-color:var(--danger)}.why.warn{border-color:var(--warn)}.why.good{border-color:var(--a)}.why.dim{color:var(--muted)}
button.ghost{background:transparent;color:var(--text);border:1px solid var(--line);margin-top:12px;padding:10px 16px}
table.mini{margin-top:6px;font-size:.85rem;border-collapse:collapse}table.mini td{padding:3px 12px 3px 0}
"""

_HOME_JS = """
function wlist(title,items,cls){
if(!items||!items.length)return '';
return '<div class="why '+cls+'"><b>'+esc(title)+'</b><ul>'+items.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>';
}
function renderDecision(d){
if(!d)return '';
return wlist('Why not',d.blockers,'bad')+wlist('Watch out',d.cautions,'warn')+wlist('Good signs',d.positives,'good')+wlist('Not known or not checked',d.unknowns,'dim')+(d.note?'<p class="muted" style="margin-top:6px">'+esc(d.note)+'</p>':'');
}
function renderQuality(q){
if(!q)return '';
const c=q.data_confidence!=null?Math.round(q.data_confidence*100)+'%':'-';
const m=(q.missing&&q.missing.length)?' | missing: '+q.missing.map(esc).join(', '):'';
return '<p class="muted" style="margin-top:10px">Data: '+esc(q.freshness)+' | '+esc(q.age_s)+'s old | '+esc(q.completeness)+' | confidence '+esc(c)+m+'</p>';
}
async function checkExit(mint){
const box=document.getElementById('exitbox'),btn=document.getElementById('exitbtn');
btn.disabled=true;box.innerHTML='<p class="muted">Asking Jupiter...</p>';
try{
const res=await fetch('/exit?mint='+encodeURIComponent(mint));const d=await res.json();
if(!res.ok){box.innerHTML='<p style="color:#ff4d6d">'+esc(d.detail||'Failed')+'</p>';}
else{
const e=d.exit||{};
const rows=(e.levels||[]).map(l=>'<tr><td>$'+esc(l.size_usd)+'</td><td>'+(l.status==='ok'?'receive $'+esc(l.sell_receives_usd)+' ('+esc(l.loss_vs_market_pct)+'% below market)':esc(l.status))+'</td></tr>').join('');
const cls=d.combined_verdict==='avoid'?'bad':d.combined_verdict==='caution'?'warn':'good';
box.innerHTML='<div class="why '+cls+'"><b>Combined: '+esc((d.combined_verdict||'').toUpperCase())+'</b><p style="margin:4px 0">'+esc(d.summary||'')+'</p>'+(rows?'<table class="mini">'+rows+'</table>':'')+'<p class="muted" style="margin-top:6px">A quote is not proof that a real sell will succeed.</p></div>';
}
}catch(err){box.innerHTML='<p style="color:#ff4d6d">Network error</p>'}
btn.disabled=false;
}
"""

_HOME_RESULT_TAIL = """<div class="muted" style="margin-top:12px">${esc(data.summary||'')}</div>
${renderDecision(data.decision)}
<div style="margin:12px 0 6px;font-size:.85rem;color:var(--muted)">Flags</div>
<div>${flags||'<span class="muted">None</span>'}</div>
${data.decision?'':'<div class="muted" style="margin-top:8px">'+esc((data.verdict_reasons||[]).join(' | '))+'</div>'}
${renderQuality(data.data_quality)}
${data.partial?'<p class="muted" style="margin-top:6px">Some on-chain data was slow, so it is missing here.</p>':''}${data.stale?'<p class="muted" style="margin-top:6px">Showing cached data from '+esc(data.stale_age_s||'?')+'s ago because a data source was down.</p>':''}
<button class="ghost" id="exitbtn" data-mint="${esc(data.mint||'')}" onclick="checkExit(this.dataset.mint)">Check exit cost (can I sell?)</button>
<div id="exitbox"></div>
<p class="muted" style="margin-top:12px;font-size:.78rem">Heuristic output, not financial advice.</p></div>`;"""

_flags_marker = '<div style="margin:12px 0 6px;font-size:.85rem;color:var(--muted)">Flags</div>'
_a = HOME_HTML.find(_flags_marker)
_b = HOME_HTML.find("</div>`;", _a) if _a >= 0 else -1
if _a >= 0 and _b >= 0 and "renderDecision" not in HOME_HTML:
    HOME_HTML = HOME_HTML[:_a] + _HOME_RESULT_TAIL + HOME_HTML[_b + len("</div>`;"):]
    HOME_HTML = HOME_HTML.replace("</style>", _HOME_CSS + "</style>", 1)
    HOME_HTML = HOME_HTML.replace("</script>", _HOME_JS + "</script>", 1)
    HOME_HTML = HOME_HTML.replace("const lp=s.lp_locked_pct!=null?esc(s.lp_locked_pct)+'%':'-';",
                                  "const lp=s.lp_note?'unverified':(s.lp_locked_pct!=null?esc(s.lp_locked_pct)+'%':'-');")
    HOME_HTML = HOME_HTML.replace("Verdict + plain-English summary", "Verdict, reasons, watch-outs and what is unknown")

LLMS_TXT = LLMS_TXT + """
## PumpSwap liquidity
For pools on PumpSwap the flag lp_unverified is used instead of lp_not_locked: pump.fun graduations burn their LP automatically,
directly created pools may not, and the data source cannot tell which one it is. It is a caution, not a confirmed problem.
security.lp_note explains it. Check the pool's LP mint holders on a block explorer if it matters.
"""
