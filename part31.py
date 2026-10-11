# ---------- part31: v2.18.0 - new landing page at / (the old home stays at /classic) ----------
# Loads before part7. Marketing copy that only says what the server can back up: the numbers (rate limit, batch size) come from
# the live settings, the proof strip and the example are fetched live from /stats and /signal and drawn with textContent,
# and a link is shown only if its page exists on this server.
VERSION = "2.18.0"
app.version = VERSION
app.openapi_schema = None

_CLASSIC_HOME_HTML = HOME_HTML


def _route_exists(path: str) -> bool:
    return any(getattr(r, "path", None) == path for r in app.routes)


def _links(items: list, tmpl: str) -> str:
    return "".join(tmpl.replace("%P%", p).replace("%L%", label) for p, label in items if _route_exists(p))


_LANDING_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVibex402 - Solana token signals for AI agents</title>
<meta name="description" content="A free Solana token API for AI agents and bots: a verdict, the reasons and the sell cost in one JSON response. No API key to start. Measured results are public.">
<meta property="og:type" content="website">
<meta property="og:title" content="TVibex402 - Solana token signals for AI agents">
<meta property="og:description" content="Verdict, reasons and sell cost for any Solana token in one request. Free to start, results measured in public.">
<meta name="twitter:card" content="summary">
<link rel="icon" href="/favicon.ico">
<style>
:root{--bg:#05080f;--card:#0f1420;--border:#1e2638;--text:#f1f5f9;--muted:#94a3b8;--accent:#6366f1;--green:#22c55e;--yellow:#eab308;--red:#ef4444;--grey:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
.container{max-width:1040px;margin:0 auto;padding:0 1.25rem}
nav{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem;padding:1rem 0;border-bottom:1px solid var(--border)}
.logo{font-weight:800;font-size:1.2rem;letter-spacing:-.03em}.logo span{color:var(--accent)}
nav .links a{color:var(--muted);text-decoration:none;margin-right:1.1rem;font-size:.92rem}nav .links a:hover{color:var(--text)}
.hero{text-align:center;padding:3.5rem 0 2.5rem}
.hero h1{font-size:clamp(2.1rem,6vw,3.4rem);font-weight:800;letter-spacing:-.04em;line-height:1.15;margin-bottom:1rem}
.hl{background:linear-gradient(135deg,#818cf8,#c084fc);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero p{font-size:1.1rem;color:var(--muted);max-width:620px;margin:0 auto 1.75rem}
.cta{display:flex;gap:.8rem;justify-content:center;flex-wrap:wrap}
.btn{display:inline-block;padding:.8rem 1.5rem;border-radius:.7rem;font-weight:600;text-decoration:none;font-size:.98rem}
.btn-primary{background:var(--accent);color:#fff}.btn-outline{border:1px solid var(--border);color:var(--text)}
.btn:hover{filter:brightness(1.12)}
#proof{display:none;text-align:center;margin:0 auto 2rem;max-width:720px;background:var(--card);border:1px solid var(--border);border-radius:.9rem;padding:.9rem 1.1rem;font-size:.95rem}
#proof .muted,.muted{color:var(--muted)}
.section{padding:2.5rem 0}
h2.t{text-align:center;font-size:1.7rem;font-weight:750;letter-spacing:-.03em;margin-bottom:.5rem}
.sub{text-align:center;color:var(--muted);max-width:560px;margin:0 auto 2rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:1rem;padding:1.4rem}
.card h3{font-size:1.05rem;margin-bottom:.4rem}.card p{color:var(--muted);font-size:.93rem}
.badge{display:inline-block;padding:.2rem .7rem;border-radius:9999px;font-size:.78rem;font-weight:700;text-transform:uppercase}
.ok{background:rgba(34,197,94,.15);color:var(--green);border:1px solid rgba(34,197,94,.3)}
.caution{background:rgba(234,179,8,.15);color:var(--yellow);border:1px solid rgba(234,179,8,.3)}
.avoid{background:rgba(239,68,68,.15);color:var(--red);border:1px solid rgba(239,68,68,.3)}
.unknown{background:rgba(100,116,139,.2);color:var(--muted);border:1px solid var(--border)}
#example{min-height:6rem}
#example ul{margin:.4rem 0 .4rem 1.1rem;font-size:.92rem}
pre{background:#0a0e17;border:1px solid var(--border);border-radius:.8rem;padding:1rem;overflow-x:auto;font-size:.85rem;margin:.8rem 0}
.honest li{margin:.35rem 0 .35rem 1.1rem;color:var(--muted);font-size:.95rem}
.final{text-align:center;padding:3rem 0}
footer{border-top:1px solid var(--border);padding:2rem 0;text-align:center;color:var(--muted);font-size:.85rem}
footer a{color:#60a5fa;text-decoration:none;margin:0 .6rem}
a{color:#60a5fa}
</style>
</head>
<body>
<div class="container">
<nav><div class="logo">TVibe<span>x402</span></div><div class="links">%%NAV%%</div></nav>

<section class="hero">
<h1>Solana token signals<br><span class="hl">built for AI agents</span></h1>
<p>A verdict, the reasons and the cost of selling, for any Solana token, in one JSON request. Free to start, no API key, and the results are measured in public.</p>
<div class="cta">%%CTA%%</div>
</section>

<div id="proof"></div>

<section class="section">
<h2 class="t">What one call gives you</h2>
<p class="sub">Four data sources read for you, combined into one decision-ready answer.</p>
<div class="grid">
<div class="card"><h3>Verdict with reasons</h3><p>ok, caution or avoid, plus blockers, cautions, positives and what is still unknown.</p></div>
<div class="card"><h3>Holder quality</h3><p>A 0-100 score and flags such as whale accumulating, whale dumping, holders churning and dip absorbed.</p></div>
<div class="card"><h3>Security checks</h3><p>Mint and freeze authority, LP lock status, Token-2022 risks and holder concentration.</p></div>
<div class="card"><h3>Sell cost</h3><p>Jupiter quotes for several sizes, so an agent can see what leaving would cost. A quote is not proof that a sell will succeed.</p></div>
<div class="card"><h3>Measured in public</h3><p>Every verdict is recorded and judged about 24 hours later. Results, rules and a hash-chained ledger are open to read.</p></div>
<div class="card"><h3>MCP ready</h3><p>Add the server URL to Claude, Cursor or any MCP client. Tools: get_token_signal, check_token_risk, check_tokens_batch, check_exit, get_track_record.</p></div>
</div>
</section>

<section class="section">
<h2 class="t">A live example</h2>
<p class="sub">Fetched from this server right now. SOL is a major asset; new tokens usually look very different.</p>
<div class="card" id="example"><p class="muted">Loading...</p></div>
<pre>curl "%%BASE%%/signal?mint=So11111111111111111111111111111111111111112"</pre>
</section>

<section class="section">
<h2 class="t">Use it from an agent</h2>
<p class="sub">Remote MCP server (Streamable HTTP). Paste this URL into your client:</p>
<pre>%%BASE%%/mcp</pre>
</section>

<section class="section">
<h2 class="t">Honest limits</h2>
<div class="card"><ul class="honest">
<li>Free tier: %%RATE%% requests per minute per IP, batches of up to %%BATCH%% tokens. An API key raises the limit to %%KEYRATE%% requests per minute; keys are issued by hand during the beta.</li>
<li>Verdicts are heuristics, not a security audit and not financial advice.</li>
<li>Only tokens that are checked get measured, so the track record is not a random sample of all tokens.</li>
<li>The free host can sleep. If you get a 503, retry after a few seconds.</li>
<li>Data comes from DexScreener, Solana RPC, RugCheck and Jupiter and can be late or wrong.</li>
</ul></div>
</section>

<section class="final">
<h2 class="t">Try a token now</h2>
<p class="sub">Paste an address and see the verdict, the reasons and the sell cost.</p>
<div class="cta">%%CTA%%</div>
</section>

<footer><div>%%FOOT%%</div><p style="margin-top:1rem">TVibex402 - Heuristic signals only - Not financial advice</p></footer>
</div>
<script>
(function () {
  var BASE = %%BASEJS%%;
  Array.prototype.forEach.call(document.querySelectorAll('pre'), function (p) { p.textContent = p.textContent.split(BASE).join(window.location.origin); });
  function el(tag, text, cls) { var n = document.createElement(tag); if (text !== undefined && text !== null) { n.textContent = String(text); } if (cls) { n.className = cls; } return n; }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }
  function getJson(url) { return fetch(url, { cache: 'no-store' }).then(function (r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.json(); }); }

  function proof() {
    getJson('/stats').then(function (d) {
      var a = ((d.performance || {}).all_tracked) || {};
      var judged = Number(a.judged), bad = Number(a.bad_outcomes);
      if (!isFinite(judged) || judged < 1 || !isFinite(bad)) { return; }
      var box = document.getElementById('proof');
      clear(box);
      box.appendChild(el('strong', judged + ' verdicts judged so far. '));
      box.appendChild(el('span', Math.round(bad / judged * 100) + '% of the tokens we tracked ended badly within ' + Math.round(d.horizon_hours || 24) + ' hours (price down 50%, liquidity down 70% or pair gone). ', 'muted'));
      var link = el('a', 'See every number'); link.href = '/accuracy'; box.appendChild(link);
      box.style.display = 'block';
    }).catch(function () {});
  }

  function usd(x) { var n = Number(x); return isFinite(n) ? '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 }) : '-'; }
  function example() {
    var box = document.getElementById('example');
    getJson('/signal?mint=So11111111111111111111111111111111111111112').then(function (d) {
      clear(box);
      var known = ['ok', 'caution', 'avoid'].indexOf(d.verdict) >= 0;
      var head = el('p'); head.appendChild(el('span', d.verdict || 'unknown', 'badge ' + (known ? d.verdict : 'unknown')));
      head.appendChild(el('span', '  ' + (d.token || 'SOL') + ' - risk ' + d.risk_score + '/100 - liquidity ' + usd(d.liquidity_usd) + ' - holder quality ' + ((d.holder_quality || {}).score) + '/100'));
      box.appendChild(head);
      if (d.summary) { box.appendChild(el('p', d.summary, 'muted')); }
      var dec = d.decision || {};
      [['Positives', dec.positives], ['Cautions', dec.cautions], ['Unknown', dec.unknowns]].forEach(function (g) {
        var items = Array.isArray(g[1]) ? g[1].slice(0, 4) : [];
        if (!items.length) { return; }
        box.appendChild(el('strong', g[0]));
        var ul = el('ul'); items.forEach(function (x) { ul.appendChild(el('li', x)); }); box.appendChild(ul);
      });
    }).catch(function () { clear(box); box.appendChild(el('p', 'The live example is not available right now. Try the playground instead.', 'muted')); });
  }

  setTimeout(function () { proof(); example(); }, 200);
})();
</script>
</body>
</html>"""


def render_landing(base: str) -> str:
    nav = _links([("/play", "Playground"), ("/docs", "Docs"), ("/pricing", "Pricing"), ("/accuracy", "Accuracy"), ("/start", "Get started")],
                 '<a href="%P%">%L%</a>')
    cta = ('<a href="/play" class="btn btn-primary">Open the playground</a>' if _route_exists("/play")
           else '<a href="/docs" class="btn btn-primary">Read the docs</a>')
    cta += ('<a href="/start" class="btn btn-outline">Get started</a>' if _route_exists("/start")
            else '<a href="/pricing" class="btn btn-outline">Pricing</a>')
    foot = _links([("/play", "Playground"), ("/docs", "Docs"), ("/pricing", "Pricing"), ("/accuracy", "Accuracy"), ("/methodology", "Methodology"),
                   ("/ledger", "Ledger"), ("/status", "Status"), ("/llms.txt", "llms.txt")], '<a href="%P%">%L%</a>')
    return (_LANDING_TEMPLATE.replace("%%NAV%%", nav).replace("%%CTA%%", cta).replace("%%FOOT%%", foot)
            .replace("%%BASEJS%%", _json_mod.dumps(base).replace("</", "<\\/")).replace("%%BASE%%", _html_escape(base)).replace("%%RATE%%", str(RATE_LIMIT))
            .replace("%%KEYRATE%%", str(KEY_RATE_LIMIT)).replace("%%BATCH%%", str(MAX_BATCH)))


import html as _html_mod  # noqa: E402
import json as _json_mod  # noqa: E402


def _html_escape(s: str) -> str:
    return _html_mod.escape(str(s), quote=True)


HOME_HTML = render_landing(os.getenv("PUBLIC_URL", "https://signal-api-24q4.onrender.com").rstrip("/"))  # "/" returns this variable


@app.get("/classic", response_class=HTMLResponse, include_in_schema=False)
async def classic_home():
    return _CLASSIC_HOME_HTML  # the previous home page, kept as a fallback


print(f"[part31] v{VERSION} new landing page at / (old home at /classic)")
