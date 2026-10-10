# ---------- part25: v2.12.0 - /status: a small live status page (reads /health in the browser) ----------
# Loads before part7. Adds one route and changes nothing else. Seed speed is set with env vars, not here:
# SEED_MAX_PER_DAY and SEED_INTERVAL_S (keep interval about 86400 / max_per_day so samples spread over the whole day).
VERSION = "2.12.0"
app.version = VERSION
app.openapi_schema = None

STATUS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVibex402 status</title>
<style>
  :root { --bg:#0f1117; --card:#1a1d27; --green:#22c55e; --yellow:#eab308; --red:#ef4444; --grey:#64748b; --text:#e2e8f0; --muted:#94a3b8; }
  body { font-family: system-ui, sans-serif; background:var(--bg); color:var(--text); margin:0; padding:1rem; }
  .card { background:var(--card); border-radius:12px; padding:1.25rem; margin:0 auto 1rem; max-width:760px; }
  h1 { margin:0 0 .25rem; font-size:1.4rem; }
  .badge { display:inline-block; padding:.15rem .6rem; border-radius:9999px; font-size:.8rem; font-weight:600; }
  .ok { background:var(--green); color:#052e16; }
  .degraded { background:var(--yellow); color:#422006; }
  .down { background:var(--red); color:#450a0a; }
  .unknown { background:var(--grey); color:#f1f5f9; }
  table { width:100%; border-collapse:collapse; margin-top:.75rem; }
  th, td { text-align:left; padding:.45rem .5rem; border-bottom:1px solid #2d3348; font-size:.9rem; }
  th { color:var(--muted); font-weight:500; }
  .muted { color:var(--muted); font-size:.85rem; }
  a { color:#60a5fa; }
</style>
</head>
<body>
<div class="card">
  <h1>TVibex402 status</h1>
  <p class="muted">Live from /health. Refreshes every minute while this tab is open.</p>
  <div id="status">Loading...</div>
  <noscript><p>This page needs JavaScript. The same data is at <a href="/health">/health</a>.</p></noscript>
</div>
<script>
(function () {
  var root = document.getElementById('status');
  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    if (cls) { n.className = cls; }
    return n;
  }
  function badge(s) {
    var known = ['ok', 'degraded', 'down'].indexOf(s) >= 0;
    return el('span', String(s).toUpperCase(), 'badge ' + (known ? s : 'unknown'));
  }
  function cell(content) {
    var td = document.createElement('td');
    if (typeof content === 'object' && content !== null) { td.appendChild(content); } else { td.textContent = String(content); }
    return td;
  }
  function render(d) {
    var src = d.sources || {};
    var names = Object.keys(src);
    var bad = false;
    names.forEach(function (k) { var s = src[k].status; if (s === 'down' || s === 'degraded') { bad = true; } });
    var overall = d.status !== 'ok' ? 'down' : (bad ? 'degraded' : 'ok');
    while (root.firstChild) { root.removeChild(root.firstChild); }
    var top = el('p');
    top.appendChild(badge(overall));
    top.appendChild(el('span', ' Version ' + d.version + ' - up ' + Math.floor((d.uptime_s || 0) / 60) + ' min'));
    root.appendChild(top);
    var table = el('table');
    var head = el('tr');
    ['Source', 'Status', 'Success', 'Avg', 'Samples'].forEach(function (h) { head.appendChild(el('th', h)); });
    table.appendChild(head);
    names.forEach(function (k) {
      var s = src[k] || {};
      var tr = el('tr');
      tr.appendChild(cell(k));
      tr.appendChild(cell(badge(s.status || 'unknown')));
      tr.appendChild(cell(s.success_rate_pct !== undefined ? s.success_rate_pct + '%' : '-'));
      tr.appendChild(cell(s.avg_ms !== undefined ? s.avg_ms + ' ms' : '-'));
      tr.appendChild(cell(s.samples !== undefined ? s.samples : '-'));
      table.appendChild(tr);
    });
    root.appendChild(table);
    var seed = d.seed || {};
    var up = d.upstream || {};
    var store = d.track_record_store || {};
    var lines = [
      'Track record: ' + (d.track_record ? 'on' : 'off') + (d.track_record ? (store.reachable ? ' (store reachable)' : ' (store NOT reachable)') : ''),
      'Price cross-check: ' + (up.status || 'unknown') + (up.price_diff_pct !== undefined && up.price_diff_pct !== null ? ' (gap ' + up.price_diff_pct + '%)' : ''),
      'Seeded today: ' + (seed.seeded_today !== undefined ? seed.seeded_today : 0) + ' of ' + (seed.max_per_day !== undefined ? seed.max_per_day : '?'),
      'MCP: ' + (d.mcp ? 'on' : 'off')
    ];
    lines.forEach(function (t) { var p = el('p', t, 'muted'); p.style.margin = '.25rem 0'; root.appendChild(p); });
    var links = el('p', null, 'muted');
    [['/', 'Home'], ['/accuracy', 'Accuracy'], ['/health', 'Health JSON'], ['/metrics', 'Metrics']].forEach(function (l, i) {
      if (i) { links.appendChild(document.createTextNode(' - ')); }
      var a = el('a', l[1]); a.href = l[0]; links.appendChild(a);
    });
    root.appendChild(links);
  }
  function load() {
    if (typeof document.hidden !== 'undefined' && document.hidden) { return; }
    fetch('/health', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.json(); })
      .then(render)
      .catch(function () {
        while (root.firstChild) { root.removeChild(root.firstChild); }
        var p = el('p'); p.appendChild(badge('down')); p.appendChild(el('span', ' Could not read /health right now.'));
        root.appendChild(p);
      });
  }
  load();
  setInterval(load, 60000);
  document.addEventListener('visibilitychange', load);
})();
</script>
</body>
</html>"""


@app.get("/status", include_in_schema=False)
async def status_page():
    return HTMLResponse(STATUS_HTML, headers={"Cache-Control": "no-cache"})


print(f"[part25] v{VERSION} /status page ready")
