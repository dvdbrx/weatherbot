"""
serve_dashboard.py — HTTP server for the WeatherBot web dashboard.
Serves sim_dashboard_repost.html and a /simulation.json API endpoint.
"""

import json
import http.server
import socketserver
import logging
from urllib.parse import urlparse

from config import CALIBRATION_FILE
from storage import load_state, load_all_markets
from polymarket_api import get_current_price


def get_last_calibration() -> str | None:
    if not CALIBRATION_FILE.exists():
        return None
    try:
        cal = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        latest = None
        for k, v in cal.items():
            vt = v.get("updated_at")
            if vt and (not latest or vt > latest):
                latest = vt
        return latest
    except Exception:
        return None


PORT = 8000
HTML_FILE = "sim_dashboard_repost.html"


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == f'/{HTML_FILE}':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            try:
                with open(HTML_FILE, 'rb') as f:
                    self.wfile.write(f.read())
            except FileNotFoundError:
                self.wfile.write(b"HTML file not found.")
            return

        if parsed.path == '/simulation.json':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            try:
                state = load_state()
                markets = load_all_markets()

                wins = state.get("wins", 0)
                losses = state.get("losses", 0)

                sim = {
                    "balance": state.get("balance", 10000.0),
                    "starting_balance": state.get("starting_balance", 10000.0),
                    "wins": wins,
                    "losses": losses,
                    "total_trades": wins + losses,
                    "peak_balance": state.get("balance", 10000.0),
                    "last_started": state.get("last_started"),
                    "last_updated": state.get("last_updated"),
                    "last_calibrated": get_last_calibration(),
                    "positions": {},
                    "trades": []
                }

                for m in markets:
                    pos = m.get("position")
                    if not pos:
                        continue

                    is_open = pos.get("status") == "open"

                    current_price = get_current_price(m.get("all_outcomes", []), pos["market_id"])
                    if current_price is None:
                        current_price = pos.get("entry_price", 0.0)

                    cost = pos.get("shares", 0.0) * pos.get("entry_price", 0.0)

                    if is_open:
                        pnl = (current_price - pos["entry_price"]) * pos.get("shares", 0.0)
                    else:
                        pnl = pos.get("pnl", 0.0)

                    question = f"{m.get('city_name')} {pos.get('bucket_low')}-{pos.get('bucket_high')}"

                    pos_data = {
                        "question": question,
                        "pnl": pnl,
                        "entry_price": pos.get("entry_price", 0.0),
                        "current_price": current_price,
                        "location": m.get("city_name", "Unknown"),
                        "kelly_pct": pos.get("kelly", 0.05),
                        "ev": pos.get("ev", 0.10),
                        "cost": cost
                    }

                    if is_open:
                        sim["positions"][pos.get("market_id", "unknown")] = pos_data

                    sim["trades"].append({
                        "type": "entry",
                        "cost": cost,
                        "opened_at": pos.get("opened_at", ""),
                        "question": question,
                        "entry_price": pos_data["entry_price"],
                        "location": pos_data["location"],
                        "kelly_pct": pos.get("kelly", 0.05),
                        "ev": pos.get("ev", 0.10),
                        "our_prob": pos.get("p", 0.50),
                    })

                    if not is_open:
                        sim["trades"].append({
                            "type": "exit",
                            "pnl": pnl,
                            "cost": cost,
                            "closed_at": pos.get("closed_at", ""),
                            "question": question,
                            "location": pos_data["location"],
                        })

                self.wfile.write(json.dumps(sim).encode())
            except Exception as e:
                logging.error(f"Error serving JSON: {e}")
                self.wfile.write(json.dumps({}).encode())
            return

        super().do_GET()


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        print(f"=========================================")
        print(f" WeatherBot Web Dashboard Server Running ")
        print(f" -> Open http://localhost:{PORT}")
        print(f"=========================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
