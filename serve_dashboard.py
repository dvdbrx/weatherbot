import json
import http.server
import socketserver
from urllib.parse import urlparse
import logging
from bot_v2 import load_state, load_all_markets, CALIBRATION_FILE

def get_last_calibration():
    if not CALIBRATION_FILE.exists():
        return None
    try:
        cal = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        latest = None
        for k, v in cal.items():
            vt = v.get("updated_at")
            if vt:
                if not latest or vt > latest:
                    latest = vt
        return latest
    except:
        return None

PORT = 8000
HTML_FILE = "sim_dashboard_repost.html"

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        
        # Serve the HTML file when hitting the root or the explicit filename
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
            
        # Serve the dynamic simulation.json data by mocking the output
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
                    
                    # Compute current price from snapshot for open positions
                    current_price = pos.get("entry_price", 0.0)
                    for o in m.get("all_outcomes", []):
                        if o["market_id"] == pos["market_id"]:
                            current_price = o.get("price", current_price)
                            break
                            
                    cost = pos.get("shares", 0.0) * pos.get("entry_price", 0.0)
                    
                    # Calculate exact Unrealized PnL if open
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
                        "kelly_pct": 0.05, # Display stub
                        "ev": 0.10, # Display stub
                        "cost": cost
                    }
                    
                    if is_open:
                        sim["positions"][pos.get("market_id", m.get("condition_id", "unknown"))] = pos_data
                        
                    # Both open and closed have entries
                    sim["trades"].append({
                        "type": "entry",
                        "cost": cost,
                        "opened_at": f"2024-01-01 {m.get('date', '12:00:00')}", 
                        "question": question,
                        "entry_price": pos_data["entry_price"],
                        "location": pos_data["location"],
                        "kelly_pct": 0.05,
                        "ev": 0.10,
                        "our_prob": 0.50
                    })
                    
                    # Only closed positions have exits
                    if not is_open:
                        sim["trades"].append({
                            "type": "exit",
                            "pnl": pnl,
                            "cost": cost,
                            "closed_at": pos.get("closed_at", f"2024-01-01 23:59:59"),
                            "question": question,
                            "location": pos_data["location"]
                        })
                
                self.wfile.write(json.dumps(sim).encode())
            except Exception as e:
                logging.error(f"Error serving JSON: {e}")
                self.wfile.write(json.dumps({}).encode())
            return
            
        # Fallback to normal HTTP behavior for static files
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
