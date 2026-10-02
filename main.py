@app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TVibex402 | Solana Token Signal API</title>
  <meta name="description" content="Free Solana token data API for AI agents and bots">
  <style>
    :root {
      --bg: #070b12;
      --card: rgba(18, 25, 35, 0.75);
      --line: rgba(34, 48, 66, 0.8);
      --text: #e8eef6;
      --muted: #8b9bb4;
      --a: #14f195;
      --b: #9945ff;
      --danger: #ff4d6d;
      --warn: #ffb020;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      line-height: 1.55;
      min-height: 100vh;
      background-image: 
        radial-gradient(ellipse 80% 50% at 20% -10%, rgba(153, 69, 255, 0.18), transparent),
        radial-gradient(ellipse 60% 40% at 90% 10%, rgba(20, 241, 149, 0.12), transparent);
    }
    .wrap { max-width: 720px; margin: 0 auto; padding: 36px 18px 80px; }
    .brand {
      font-size: 2.6rem; font-weight: 800; letter-spacing: -1px;
      background: linear-gradient(120deg, var(--b), var(--a));
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .tag { color: var(--muted); margin: 6px 0 28px; font-size: 1.05rem; }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 22px;
      backdrop-filter: blur(12px);
      margin-bottom: 20px;
    }
    h2 { font-size: 1.15rem; margin: 28px 0 12px; font-weight: 600; }
    .grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
    }
    .grid .item {
      background: rgba(13, 17, 23, 0.6);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px 12px;
      font-size: 0.9rem;
      color: var(--muted);
    }
    input[type=text] {
      width: 100%; padding: 14px 16px; border-radius: 12px;
      border: 1px solid var(--line); background: rgba(13,17,23,0.8);
      color: var(--text); font-size: 0.95rem; outline: none;
      transition: border 0.2s;
    }
    input[type=text]:focus { border-color: var(--b); }
    button {
      background: linear-gradient(90deg, var(--b), var(--a));
      color: #06110b; border: 0; padding: 13px 22px; border-radius: 12px;
      font-weight: 700; cursor: pointer; font-size: 0.95rem;
      transition: transform 0.15s, opacity 0.15s;
    }
    button:hover { transform: translateY(-1px); }
    button:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
    .muted { color: var(--muted); font-size: 0.88rem; }
    pre {
      background: #0d1117; padding: 14px; border-radius: 12px;
      overflow-x: auto; font-size: 0.82rem; line-height: 1.45;
      border: 1px solid var(--line);
    }
    .badge {
      display: inline-block; padding: 3px 10px; border-radius: 999px;
      font-size: 0.75rem; font-weight: 600; margin: 3px 4px 3px 0;
    }
    .badge.green { background: rgba(20,241,149,0.15); color: var(--a); }
    .badge.purple { background: rgba(153,69,255,0.2); color: #c084fc; }
    .badge.red { background: rgba(255,77,109,0.15); color: var(--danger); }
    .badge.yellow { background: rgba(255,176,32,0.15); color: var(--warn); }
    .result-box { display: none; margin-top: 18px; }
    .result-box.show { display: block; }
    .stat-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 14px 0;
    }
    .stat {
      background: rgba(13,17,23,0.7); border-radius: 12px; padding: 12px;
      border: 1px solid var(--line);
    }
    .stat .label { font-size: 0.75rem; color: var(--muted); }
    .stat .value { font-size: 1.1rem; font-weight: 700; margin-top: 2px; }
    .loading { display: none; text-align: center; padding: 20px; color: var(--muted); }
    .loading.show { display: block; }
    .spinner {
      width: 28px; height: 28px; border: 3px solid var(--line);
      border-top-color: var(--a); border-radius: 50%;
      animation: spin 0.8s linear infinite; margin: 0 auto 10px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    a { color: var(--a); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .copy-btn {
      background: transparent; border: 1px solid var(--line);
      color: var(--muted); padding: 6px 12px; border-radius: 8px;
      font-size: 0.8rem; cursor: pointer;
    }
    footer { margin-top: 40px; text-align: center; color: var(--muted); font-size: 0.85rem; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1 class="brand">TVibex402</h1>
    <p class="tag">Free Solana token data API for AI agents & bots</p>

    <div class="card">
      <p style="font-weight:600; margin-bottom:12px;">Try it live</p>
      <input type="text" id="mint" placeholder="Paste Solana token mint address" 
             autocomplete="off" spellcheck="false">
      <div class="row">
        <button id="btn" onclick="getSignal()">Get Signal</button>
      </div>
      <p class="muted" style="margin-top:12px;">No API key • No signup • Instant JSON</p>

      <div class="loading" id="loading">
        <div class="spinner"></div>
        Fetching live data...
      </div>

      <div class="result-box" id="result"></div>
    </div>

    <h2>What you get</h2>
    <div class="grid">
      <div class="item">Price, FDV, Market Cap</div>
      <div class="item">Price change 5m / 1h / 6h / 24h</div>
      <div class="item">Liquidity + Pair age</div>
      <div class="item">Volume 5m / 1h / 6h</div>
      <div class="item">Buy / Sell + Buy pressure</div>
      <div class="item">Volume spike + Flags</div>
    </div>

    <h2>Endpoint for Agents</h2>
    <div class="card" style="padding:16px;">
      <pre style="margin:0;">GET /signal?mint=TOKEN_MINT_ADDRESS</pre>
      <p class="muted" style="margin-top:10px;">
        Example: 
        <a href="/signal?mint=So11111111111111111111111111111111111111112" target="_blank">
          /signal?mint=So111...112
        </a>
      </p>
    </div>

    <h2>Notes</h2>
    <ul class="muted" style="padding-left:18px; line-height:1.8;">
      <li>Data from DexScreener (may be slightly delayed)</li>
      <li>Flags are simple heuristics — not financial advice</li>
      <li>Cache 30 seconds • Free during beta</li>
    </ul>

    <footer>TVibex402 • Built for AI Agents</footer>
  </div>

  <script>
    async function getSignal() {
      const mint = document.getElementById('mint').value.trim();
      if (!mint || mint.length < 32) {
        alert('Please paste a valid Solana mint address');
        return;
      }

      const btn = document.getElementById('btn');
      const loading = document.getElementById('loading');
      const result = document.getElementById('result');

      btn.disabled = true;
      loading.classList.add('show');
      result.classList.remove('show');
      result.innerHTML = '';

      try {
        const res = await fetch('/signal?mint=' + encodeURIComponent(mint));
        const data = await res.json();

        if (!res.ok) {
          result.innerHTML = `<div class="card" style="border-color:var(--danger);">
            <p style="color:var(--danger);">Error: ${data.detail || 'Failed to fetch'}</p>
          </div>`;
        } else {
          const flags = (data.flags || []).map(f => {
            let cls = 'purple';
            if (f.includes('buying') || f.includes('momentum')) cls = 'green';
            if (f.includes('selling') || f.includes('dump') || f.includes('low')) cls = 'red';
            if (f.includes('new') || f.includes('spike')) cls = 'yellow';
            return `<span class="badge \( {cls}"> \){f}</span>`;
          }).join('');

          result.innerHTML = `
            <div style="margin-top:8px;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                  <div style="font-size:1.4rem; font-weight:700;">${data.token || '—'}</div>
                  <div class="muted" style="font-size:0.85rem;">${data.name || ''}</div>
                </div>
                <div style="text-align:right;">
                  <div style="font-size:1.3rem; font-weight:700;">\[ {data.price_usd || '—'}</div>
                  <div class="muted" style="font-size:0.8rem;">${data.dex || ''}</div>
                </div>
              </div>

              <div class="stat-grid">
                <div class="stat">
                  <div class="label">Liquidity</div>
                  <div class="value"> \]{(data.liquidity_usd || 0).toLocaleString()}</div>
                </div>
                <div class="stat">
                  <div class="label">Buy Pressure 5m</div>
                  <div class="value">${data.buy_pressure_5m != null ? (data.buy_pressure_5m * 100).toFixed(1) + '%' : '—'}</div>
                </div>
                <div class="stat">
                  <div class="label">Volume 5m</div>
                  <div class="value">$${(data.volume_5m || 0).toLocaleString()}</div>
                </div>
                <div class="stat">
                  <div class="label">Volume Spike</div>
                  <div class="value">${data.volume_spike_ratio_5m != null ? data.volume_spike_ratio_5m + 'x' : '—'}</div>
                </div>
              </div>

              <div style="margin:12px 0 6px; font-size:0.85rem; color:var(--muted);">Flags</div>
              <div>${flags || '<span class="muted">None</span>'}</div>
            </div>
          `;
        }
        result.classList.add('show');
      } catch (e) {
        result.innerHTML = `<p style="color:var(--danger);">Network error. Please try again.</p>`;
        result.classList.add('show');
      }

      loading.classList.remove('show');
      btn.disabled = false;
    }

    // Enter key support
    document.getElementById('mint').addEventListener('keypress', function(e) {
      if (e.key === 'Enter') getSignal();
    });
  </script>
</body>
</html>
"""