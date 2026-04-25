"""
tui.py — WeatherBot web dashboard server.
Run on the Pi, open http://<pi-tailscale-ip>:8080 from your laptop.

Usage:
    python3 tui.py              # serves on port 8080
    python3 tui.py 9090         # custom port

Tip: run the bot with logging so the activity feed works:
    python3 bot_v2.py 2>&1 | tee bot.log
"""

import sys
import json
import time
import threading
import http.server
import socketserver
from datetime import datetime, timezone
from pathlib import Path

from config import CALIBRATION_FILE, DATA_DIR, DAILY_SPEND_LIMIT, MAX_DAILY_LOSSES, MAX_DRAWDOWN_PCT
from storage import load_state, load_all_markets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
LOG_FILE = Path(__file__).parent / "bot.log"
LOG_LINES = 120   # tail this many lines from bot.log

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeatherBot Dashboard</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--dim:#8b949e;--text:#e6edf3}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'SF Mono',ui-monospace,monospace;font-size:13px;min-height:100vh}
  header{background:var(--card);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;justify-content:space-between}
  header h1{font-size:16px;font-weight:700;color:var(--accent);letter-spacing:.5px}
  #status-dot{width:8px;height:8px;border-radius:50%;background:var(--green);display:inline-block;margin-right:6px;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  #last-update{color:var(--dim);font-size:11px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;padding:20px 24px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
  .card h2{font-size:11px;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px}
  .big-num{font-size:28px;font-weight:700;color:var(--accent)}
  .sub{font-size:11px;color:var(--dim);margin-top:2px}
  .row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border)}
  .row:last-child{border-bottom:none}
  .label{color:var(--dim)}
  .green{color:var(--green)} .red{color:var(--red)} .yellow{color:var(--yellow)} .dim{color:var(--dim)}
  .badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
  .badge-green{background:#1a3a1e;color:var(--green)} .badge-red{background:#3a1a1a;color:var(--red)}
  .badge-yellow{background:#3a2a1a;color:var(--yellow)}
  .bar-wrap{height:6px;background:#21262d;border-radius:3px;margin-top:6px;overflow:hidden}
  .bar{height:100%;border-radius:3px;transition:width .5s ease}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th{color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:.5px;padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}
  td{padding:7px 8px;border-bottom:1px solid #1c2128}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#1c2128}
  .wide{grid-column:1/-1}
  #log{background:#0d1117;border:1px solid var(--border);border-radius:6px;padding:12px;font-size:11px;line-height:1.7;height:280px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}
  #log .buy{color:var(--green)} #log .stop{color:var(--red)} #log .live{color:var(--accent)}
  #log .warn{color:var(--yellow)} #log .scan{color:var(--dim)}
  #halted-banner{background:#3a1a1a;border:1px solid var(--red);border-radius:8px;padding:14px 20px;color:var(--red);font-weight:700;font-size:14px;margin:0 24px 0;display:none}
</style>
</head>
<body>
<header>
  <h1><span id="status-dot"></span>⛅ WeatherBot Dashboard</h1>
  <span id="last-update">connecting...</span>
</header>
<div id="halted-banner">⛔ TRADING HALTED — <span id="halt-reason"></span></div>
<div class="grid" id="grid">
  <!-- cards injected by JS -->
</div>
<script>
const fmt = (n, d=2) => n == null ? '—' : '$' + Number(n).toFixed(d);
const pct = n => n == null ? '—' : (n >= 0 ? '+' : '') + Number(n).toFixed(1) + '%';
const clr = n => n >= 0 ? 'green' : 'red';

function bar(val, max, color='var(--accent)') {
  const w = Math.min(100, (val / (max || 1)) * 100);
  const c = w >= 90 ? 'var(--red)' : w >= 60 ? 'var(--yellow)' : color;
  return `<div class="bar-wrap"><div class="bar" style="width:${w}%;background:${c}"></div></div>`;
}

function render(d) {
  const s = d.state;
  const bal = s.balance ?? 0;
  const start = s.starting_balance ?? bal;
  const ret = start ? (bal - start) / start * 100 : 0;
  const peak = s.peak_balance ?? bal;
  const drawdown = peak ? (peak - bal) / peak * 100 : 0;
  const spent = s.today_spent ?? 0;
  const todayL = s.today_losses ?? 0;

  // halted banner
  const banner = document.getElementById('halted-banner');
  if (s.halted) {
    banner.style.display = 'block';
    document.getElementById('halt-reason').textContent = s.halt_reason ?? '';
  } else { banner.style.display = 'none'; }

  const wins = s.wins ?? 0, losses = s.losses ?? 0, total = wins + losses;
  const wr = total ? (wins / total * 100).toFixed(0) + '%' : '—';

  let html = '';

  // ── Balance card
  html += `<div class="card">
    <h2>Wallet</h2>
    <div class="big-num">${fmt(bal)}</div>
    <div class="sub">Start ${fmt(start)} &nbsp;<span class="${clr(ret)}">${pct(ret)}</span></div>
    <div style="margin-top:12px">
      <div class="row"><span class="label">Peak</span><span>${fmt(peak)}</span></div>
      <div class="row"><span class="label">Drawdown</span><span class="${drawdown>=20?'red':drawdown>=10?'yellow':''}">${drawdown.toFixed(1)}% / ${(d.limits.max_drawdown*100).toFixed(0)}%</span></div>
      ${bar(drawdown, d.limits.max_drawdown * 100, 'var(--green)')}
    </div>
  </div>`;

  // ── Trades card
  html += `<div class="card">
    <h2>Performance</h2>
    <div class="big-num">${total}</div>
    <div class="sub">trades</div>
    <div style="margin-top:12px">
      <div class="row"><span class="label">Win Rate</span><span class="${wins>=losses?'green':'red'}">${wr}</span></div>
      <div class="row"><span class="label">Wins / Losses</span><span><span class="green">${wins}W</span> / <span class="red">${losses}L</span></span></div>
      <div class="row"><span class="label">Open Positions</span><span class="accent">${d.open_positions.length}</span></div>
    </div>
  </div>`;

  // ── Risk Guards card
  html += `<div class="card">
    <h2>Risk Guards</h2>
    <div class="row"><span class="label">Daily Spend</span><span>${fmt(spent)} / ${fmt(d.limits.daily_spend)}</span></div>
    ${bar(spent, d.limits.daily_spend)}
    <div class="row" style="margin-top:8px"><span class="label">Daily Losses</span><span>${todayL} / ${d.limits.max_daily_losses}</span></div>
    ${bar(todayL, d.limits.max_daily_losses)}
    <div class="row" style="margin-top:8px"><span class="label">Status</span>
      <span class="badge ${s.halted ? 'badge-red' : 'badge-green'}">${s.halted ? '⛔ HALTED' : '✅ Active'}</span>
    </div>
    <div class="row"><span class="label">Uptime</span><span class="dim">${d.uptime ?? '—'}</span></div>
    <div class="row"><span class="label">Last Scan</span><span class="dim">${d.last_scan ?? '—'}</span></div>
  </div>`;

  // ── Open Positions table
  html += `<div class="card wide"><h2>Open Positions</h2>`;
  if (d.open_positions.length === 0) {
    html += `<div class="dim" style="padding:12px 0">No open positions</div>`;
  } else {
    html += `<table><thead><tr><th>City</th><th>Date</th><th>Bucket</th><th>Entry</th><th>Current</th><th>Cost</th><th>EV</th><th>Source</th><th>PnL</th></tr></thead><tbody>`;
    for (const p of d.open_positions) {
      const pnl = p.pnl ?? 0;
      const c = clr(pnl);
      html += `<tr>
        <td>${p.city}</td><td>${p.date}</td><td>${p.bucket}</td>
        <td>${fmt(p.entry_price,3)}</td><td>${fmt(p.current_price,3)}</td>
        <td>${fmt(p.cost)}</td><td class="${p.ev>0?'green':'red'}">${(p.ev*100).toFixed(1)}%</td>
        <td class="dim">${(p.forecast_src??'').toUpperCase()}</td>
        <td class="${c}">${pnl>=0?'+':''}${fmt(pnl)}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }
  html += `</div>`;

  // ── Recent Closed table
  html += `<div class="card wide"><h2>Recent Closed Positions</h2>`;
  if (d.recent_closed.length === 0) {
    html += `<div class="dim" style="padding:12px 0">No closed positions yet</div>`;
  } else {
    html += `<table><thead><tr><th>City</th><th>Date</th><th>Bucket</th><th>Entry</th><th>Exit</th><th>Cost</th><th>PnL</th><th>Reason</th><th>Closed</th></tr></thead><tbody>`;
    for (const p of d.recent_closed) {
      const pnl = p.pnl ?? 0;
      const c = clr(pnl);
      html += `<tr>
        <td>${p.city}</td><td>${p.date}</td><td>${p.bucket}</td>
        <td>${fmt(p.entry_price,3)}</td><td>${fmt(p.exit_price,3)}</td>
        <td>${fmt(p.cost)}</td>
        <td class="${c}">${pnl>=0?'+':''}${fmt(pnl)}</td>
        <td class="dim">${p.close_reason??''}</td>
        <td class="dim">${p.closed_at?p.closed_at.slice(0,16).replace('T',' '):''}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }
  html += `</div>`;

  // ── Activity Log
  html += `<div class="card wide"><h2>Activity Log</h2><div id="log">`;
  if (d.log_lines && d.log_lines.length) {
    html += d.log_lines.map(l => {
      if (l.includes('[BUY]') || l.includes('POSTED')) return `<span class="buy">${l}</span>`;
      if (l.includes('[STOP]') || l.includes('[LIVE] SELL') || l.includes('LOSS')) return `<span class="stop">${l}</span>`;
      if (l.includes('[LIVE]')) return `<span class="live">${l}</span>`;
      if (l.includes('⚠') || l.includes('Error') || l.includes('error')) return `<span class="warn">${l}</span>`;
      if (l.includes('full scan') || l.includes('monitoring')) return `<span class="scan">${l}</span>`;
      return l;
    }).join('\n');
  } else {
    html += '<span class="dim">No log file found.\nTip: run bot with:  python3 bot_v2.py 2>&1 | tee bot.log</span>';
  }
  html += `</div></div>`;

  document.getElementById('grid').innerHTML = html;
  // scroll log to bottom
  const logEl = document.getElementById('log');
  if (logEl) logEl.scrollTop = logEl.scrollHeight;
}

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    render(d);
    document.getElementById('last-update').textContent =
      'Updated ' + new Date().toLocaleTimeString();
    document.getElementById('status-dot').style.background = 'var(--green)';
  } catch(e) {
    document.getElementById('status-dot').style.background = 'var(--red)';
    document.getElementById('last-update').textContent = 'Connection error';
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""

# ── API ───────────────────────────────────────────────────────────────────────

def tail_log(n=LOG_LINES):
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


def _uptime_str(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
        h, rem = divmod(int(secs), 3600)
        m = rem // 60
        return f"{h}h {m}m"
    except Exception:
        return None


def _scan_ago(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        return f"{secs}s ago" if secs < 60 else f"{secs // 60}m ago"
    except Exception:
        return None


def build_api_response() -> dict:
    state = load_state()
    markets = load_all_markets()

    open_pos = []
    closed_pos = []

    for m in markets:
        pos = m.get("position")
        if not pos:
            continue
        unit = "F" if m.get("unit") == "F" else "C"
        bucket = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit}"

        # best current price from cached outcomes
        current_price = pos.get("entry_price", 0.0)
        for o in m.get("all_outcomes", []):
            if o.get("market_id") == pos.get("market_id"):
                current_price = o.get("price", current_price)
                break

        base = {
            "city":        m.get("city_name", m.get("city")),
            "date":        m.get("date"),
            "bucket":      bucket,
            "entry_price": pos.get("entry_price"),
            "cost":        pos.get("cost"),
            "ev":          pos.get("ev"),
            "forecast_src": pos.get("forecast_src"),
            "close_reason": pos.get("close_reason"),
            "closed_at":   pos.get("closed_at"),
            "exit_price":  pos.get("exit_price"),
            "pnl":         pos.get("pnl"),
        }

        if pos.get("status") == "open":
            pnl = (current_price - (pos.get("entry_price") or 0)) * (pos.get("shares") or 0)
            open_pos.append({**base, "current_price": current_price, "pnl": round(pnl, 4)})
        else:
            closed_pos.append(base)

    closed_pos.sort(key=lambda x: x.get("closed_at") or "", reverse=True)

    return {
        "state":         state,
        "open_positions":  open_pos,
        "recent_closed":   closed_pos[:20],
        "log_lines":       tail_log(),
        "uptime":          _uptime_str(state.get("last_started")),
        "last_scan":       _scan_ago(state.get("last_updated")),
        "limits": {
            "daily_spend":      DAILY_SPEND_LIMIT,
            "max_daily_losses": MAX_DAILY_LOSSES,
            "max_drawdown":     MAX_DRAWDOWN_PCT,
        },
    }


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/status":
            try:
                data = build_api_response()
                body = json.dumps(data, default=str).encode()
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    print(f"╔══════════════════════════════════════════╗")
    print(f"║   WeatherBot Dashboard — port {PORT}        ║")
    print(f"╠══════════════════════════════════════════╣")
    print(f"║  Local:    http://localhost:{PORT}          ║")
    print(f"║  Pi:       http://<tailscale-ip>:{PORT}    ║")
    print(f"╚══════════════════════════════════════════╝")
    print(f"Tip: run bot with  python3 bot_v2.py 2>&1 | tee bot.log")
    print(f"Ctrl+C to stop\n")
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
