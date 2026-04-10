import os
import re
import sys
import json
import math
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    CLOB_AVAILABLE = True
except ImportError as e:
    print(f"  ⚠️  Import error: {e}")
    CLOB_AVAILABLE = False

def get_clob_client():
    if not CLOB_AVAILABLE:
        print("  ⚠️  py-clob-client not installed. Cannot execute live trades.")
        return None
        
    key = os.getenv("POLYMARKET_KEY")
    funder = os.getenv("FUNDER_ADDRESS")
    if not key or not funder:
        print("  ⚠️  POLYMARKET_KEY or FUNDER_ADDRESS missing in .env")
        return None
        
    try:
        host = "https://clob.polymarket.com"
        chain_id = 137
        # EOA wallet uses signature_type=0
        temp_client = ClobClient(host, key=key, chain_id=chain_id)
        creds = temp_client.create_or_derive_api_creds()
        
        client = ClobClient(host, key=key, chain_id=chain_id, signature_type=0, funder=funder, creds=creds)
        return client
    except Exception as e:
        print(f"  ⚠️  Failed to initialize CLOB client: {e}")
        return None

# =============================================================================
# CONFIG & PATHS
# =============================================================================

with open("config.json") as f:
    _cfg = json.load(f)

# Trading Strategy
KELLY_FRACTION = _cfg.get("kelly_fraction", 0.20)
MIN_EV         = _cfg.get("min_ev", 0.05)
MAX_BET        = _cfg.get("max_bet", 20.0)
MAX_PRICE      = _cfg.get("max_price", 0.45)
MIN_VOLUME     = _cfg.get("min_volume", 2000)
MIN_HOURS      = _cfg.get("min_hours", 2.0)
MAX_HOURS      = _cfg.get("max_hours", 72.0)
SCAN_INTERVAL  = _cfg.get("scan_interval", 3600)
CALIBRATION_MIN = _cfg.get("calibration_min", 30)
MAX_SLIPPAGE   = _cfg.get("max_slippage", 0.03)

# Default Standard Deviation if no calibration exists
SIGMA_F = 2.0  # Fahrenheit
SIGMA_C = 1.2  # Celsius

# Data Storage structure (v2 compatible)
DATA_DIR         = Path("data")
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
MARKETS_DIR.mkdir(exist_ok=True)
CALIBRATION_FILE = DATA_DIR / "calibration.json"

# Detailed location mapping (Sync with v2)
LOCATIONS = {
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City", "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",       "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",         "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",        "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",       "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",       "station": "KATL", "unit": "F", "region": "us"},
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",        "station": "EGLC", "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9962,  "lon":    2.5979, "name": "Paris",         "station": "LFPG", "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",        "station": "EDDM", "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",        "station": "LTAC", "unit": "C", "region": "eu"},
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",         "station": "RKSI", "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",         "station": "RJTT", "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD", "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",     "station": "WSSS", "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",       "station": "VILK", "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG", "unit": "C", "region": "asia"},
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN", "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc": "America/New_York", "chicago": "America/Chicago",
    "miami": "America/New_York", "dallas": "America/Chicago",
    "seattle": "America/Los_Angeles", "atlanta": "America/New_York",
    "london": "Europe/London", "paris": "Europe/Paris",
    "munich": "Europe/Berlin", "ankara": "Europe/Istanbul",
    "seoul": "Asia/Seoul", "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai", "singapore": "Asia/Singapore",
    "lucknow": "Asia/Kolkata", "tel-aviv": "Asia/Jerusalem",
    "toronto": "America/Toronto", "sao-paulo": "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires", "wellington": "Pacific/Auckland",
}

MONTHS = ["january","february","march","april","may","june",
          "july","august","september","october","november","december"]

# =============================================================================

def get_forecast(city_slug, date_str):
    '''Fetch forecast from ECMWF and HRRR (simulated wrapper to match bot_v2).'''
    import requests
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    forecasts = []
    
    # ECMWF
    try:
        url = (f"https://api.open-meteo.com/v1/forecast"
               f"?latitude={loc['lat']}&longitude={loc['lon']}"
               f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
               f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
               f"&models=ecmwf_ifs025&bias_correction=true")
        data = requests.get(url, timeout=5).json()
        for d, t in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
            if d == date_str and t is not None:
                forecasts.append({"source": "ecmwf", "temp": round(t, 1) if unit == "C" else round(t)})
    except: pass
    
    # HRRR (US only)
    if loc["region"] == "us":
        try:
            url = (f"https://api.open-meteo.com/v1/forecast"
                   f"?latitude={loc['lat']}&longitude={loc['lon']}"
                   f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
                   f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
                   f"&models=gfs_seamless")
            data = requests.get(url, timeout=5).json()
            for d, t in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                if d == date_str and t is not None:
                    forecasts.append({"source": "hrrr", "temp": round(t, 1) if unit == "C" else round(t)})
        except: pass
        
    # METAR (Current day only)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if date_str == today:
        try:
            station = loc["station"]
            url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
            data = requests.get(url, timeout=5).json()
            if data and isinstance(data, list):
                temp_c = data[0].get("temp")
                if temp_c is not None:
                    val = float(temp_c) * 9/5 + 32 if unit == "F" else float(temp_c)
                    forecasts.append({"source": "metar", "temp": round(val, 1) if unit == "C" else round(val)})
        except: pass
        
    return forecasts

def get_polymarket_event(city_slug, date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month = MONTHS[dt.month - 1]
        slug = f"highest-temperature-in-{city_slug}-on-{month}-{dt.day}-{dt.year}"
        r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=5)
        data = r.json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except: pass
    return None

def hours_until_resolution(event):
    try:
        end = datetime.fromisoformat(event.get("endDate", "").replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except: return 999.0

def parse_temp_range(question):
    if not question: return None
    num = r'(-?\d+(?:\.\d+)?)'
    if re.search(r'or below', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or below', question, re.IGNORECASE)
        if m: return (-999.0, float(m.group(1)))
    if re.search(r'or higher', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or higher', question, re.IGNORECASE)
        if m: return (float(m.group(1)), 999.0)
    m = re.search(r'between ' + num + r'-' + num + r'[°]?[FC]', question, re.IGNORECASE)
    if m: return (float(m.group(1)), float(m.group(2)))
    m = re.search(r'be ' + num + r'[°]?[FC] on', question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None

# =============================================================================
# MATH & UTILITIES

# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast, t_low, t_high, sigma=None):
    """Calculates probability of forecast landing in bucket using Normal Distribution."""
    s = sigma or 2.0
    # Edge cases - "or higher" / "or lower"
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / s)
    
    # Standard bucket: probability that X is between t_low and t_high
    return norm_cdf((t_high - float(forecast)) / s) - norm_cdf((t_low - float(forecast)) / s)

def calc_ev(p, price):
    if price <= 0 or price >= 1: return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price):
    if price <= 0 or price >= 1: return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

def bet_size(kelly, balance):
    raw = kelly * balance
    return round(min(raw, MAX_BET), 2)

def in_bucket(temp, t_low, t_high):
    return t_low <= temp <= t_high

# =============================================================================
# STORAGE & CALIBRATION
# =============================================================================

_cal: dict = {}

def load_cal():
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    return {}

def save_cal(cal):
    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")

def run_calibration(markets):
    """Recalculates sigma from resolved markets."""
    resolved = [m for m in markets if m.get("status") == "resolved" and m.get("actual_temp") is not None]
    if not resolved: return {}
    
    new_cal = {}
    sources = ["ecmwf", "hrrr", "metar"]
    for city in LOCATIONS:
        new_cal[city] = {}
        for source in sources:
            errors = []
            for m in [x for x in resolved if x["city"] == city]:
                snap = next((s for s in reversed(m.get("forecast_snapshots", [])) 
                             if s["source"] == source), None)
                if snap and snap.get("temp") is not None:
                    errors.append(abs(snap["temp"] - m["actual_temp"]))
            
            if len(errors) >= CALIBRATION_MIN:
                mae = sum(errors) / len(errors)
                new_cal[city][source] = round(mae * 1.25, 2)  # Conservative buffer
    
    save_cal(new_cal)
    return new_cal

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "balance": _cfg.get("balance", 1000.0),
        "last_started": None,
        "last_updated": None
    }

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def load_market(mkt_id):
    path = MARKETS_DIR / f"{mkt_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None

def save_market(mkt):
    path = MARKETS_DIR / f"{mkt['id']}.json"
    path.write_text(json.dumps(mkt, indent=2))

def load_all_markets():
    mkt_files = list(MARKETS_DIR.glob("*.json"))
    markets = []
    for f in mkt_files:
        try:
            markets.append(json.loads(f.read_text()))
        except: pass
    return markets

def reset_sim():
    import shutil
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(exist_ok=True)
    MARKETS_DIR.mkdir(exist_ok=True)
    print(f"{C.GREEN}  ✅ Storage reset — all data wiped.{C.RESET}")


ACTIVE_LOCATIONS = _cfg.get("locations", "nyc,chicago,miami,dallas,seattle,atlanta").split(",")
ACTIVE_LOCATIONS = [l.strip().lower() for l in ACTIVE_LOCATIONS]

MONTHS = ["january","february","march","april","may","june",
          "july","august","september","october","november","december"]

# =============================================================================
# COLORS
# =============================================================================

class C:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    RESET  = "\033[0m"
    BOLD   = "\033[1m"

def ok(msg):   print(f"{C.GREEN}  ✅ {msg}{C.RESET}")
def warn(msg): print(f"{C.YELLOW}  ⚠️  {msg}{C.RESET}")
def info(msg): print(f"{C.CYAN}  {msg}{C.RESET}")
def skip(msg): print(f"{C.GRAY}  ⏸️  {msg}{C.RESET}")

# =============================================================================
# TRADING CORE
# =============================================================================

def scan_and_update(paper: bool = False, live: bool = False):
    """Single pass through all active locations to update forecasts and check entries/exits."""
    state = load_state()
    markets = load_all_markets()
    cal = load_cal()
    
    # 1. Check Exits (Resolved & Stops)
    for m in [x for x in markets if x.get("status") == "active"]:
        # Skip if already checking recently (optional)
        event = get_polymarket_event(m["city"], m["date"])
        if not event: continue
        
        # Check if resolved
        if event.get("closed"):
            # Fetch actual temp (from METAR snapshot or final resolution)
            # For simplicity in v1, we assume the user might manually verify or we use the latest METAR
            actual = get_forecast(m["city"], m["date"])
            metar = next((s["temp"] for s in reversed(actual) if s["source"] == "metar"), None)
            
            if metar is not None:
                m["status"] = "resolved"
                m["actual_temp"] = metar
                won = in_bucket(metar, m["t_low"], m["t_high"])
                payout = m["shares"] if won else 0
                m["payout"] = payout
                if paper or live:
                    state["balance"] += payout
                ok(f"RESOLVED: {m['city']} {m['date']} | Temp: {metar} | {'WON' if won else 'LOST'} | Payout: ${payout:.2f}")
                save_market(m)
                continue

        # Check Price for Stops
        mkt_data = next((mk for mk in event.get("markets", []) if mk["id"] == m["id"]), None)
        if mkt_data:
            try:
                prices = json.loads(mkt_data.get("outcomePrices", "[0.5,0.5]"))
                price = float(prices[0])
                m["last_price"] = price
                
                # Trailing stop logic (v2 feature)
                if price > m.get("peak_price", 0):
                    m["peak_price"] = price
                
                # Stop Loss: 50% drop from entry
                if price < m["entry_price"] * 0.5:
                    warn(f"STOP LOSS: {m['city']} {m['date']} | Price ${price:.3f} < 50% of entry")
                    if live:
                        # In a real bot, we'd sell here. In this sim, we just mark it.
                        m["status"] = "stopped"
                
                save_market(m)
            except: pass

    # 2. Check Entries
    for city in ACTIVE_LOCATIONS:
        for i in range(1, 4): # Next 3 days
            date_str = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
            
            # Already in this city/date?
            if any(m["city"] == city and m["date"] == date_str and m["status"] == "active" for m in markets):
                continue
                
            forecasts = get_forecast(city, date_str)
            if not forecasts: continue
            
            event = get_polymarket_event(city, date_str)
            if not event or event.get("closed"): continue
            
            if hours_until_resolution(event) < MIN_HOURS: continue

            # Evaluate each source
            for snap in forecasts:
                source = snap["source"]
                sigma = cal.get(city, {}).get(source, SIGMA_F if LOCATIONS[city]["unit"] == "F" else SIGMA_C)
                
                for mkt in event.get("markets", []):
                    rng = parse_temp_range(mkt["question"])
                    if not rng: continue
                    
                    try:
                        prices = json.loads(mkt.get("outcomePrices", "[0.5,0.5]"))
                        price = float(prices[0])
                    except: continue

                    if price > MAX_PRICE: continue
                    
                    p = bucket_prob(snap["temp"], rng[0], rng[1], sigma)
                    ev = calc_ev(p, price)
                    
                    if ev >= MIN_EV:
                        kelly = calc_kelly(p, price)
                        size = bet_size(kelly, state["balance"])
                        
                        if size >= 1.0:
                            info(f"SIGNAL: {city} {date_str} [{source}] | {mkt['question'][:40]}...")
                            info(f"  P: {p:.1%} | Price: ${price:.3f} | EV: {ev:+.2f} | Kelly: {kelly:.1%} | Size: ${size:.2f}")
                            
                            if paper or live:
                                clob_success = False
                                actual_size = size
                                actual_shares = size / price
                                
                                if live:
                                    daily_limit = _cfg.get("daily_spend_limit", 50.0)
                                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                                    daily_spent = state.get("daily_spent", {}).get(today_str, 0.0)
                                    
                                    if (daily_spent + size) > daily_limit:
                                        warn(f"Daily spend limit reached (${daily_spent:.2f} + ${size:.2f} > ${daily_limit:.2f}). Skipping.")
                                        continue
                                        
                                    client = get_clob_client()
                                    if not client:
                                        continue
                                        
                                    try:
                                        # get_balance returns the balance as a string in wei
                                        wallet_balance_wei = int(client.get_balance())
                                        wallet_balance_usdc = wallet_balance_wei / 1e6
                                        if wallet_balance_usdc < size:
                                            warn(f"Insufficient wallet balance (${wallet_balance_usdc:.2f}) for trade size (${size:.2f}). Skipping.")
                                            continue
                                    except Exception as e:
                                        warn(f"Error checking wallet balance: {e}")
                                        continue
                                        
                                    token_id = mkt.get("clobTokenIds", [None])[0]
                                    if not token_id:
                                        warn("No token ID found for YES outcome in Gamma response.")
                                        continue
                                        
                                    # Ensure shares are whole numbers for safety in ordering (though Poly allows fractions, let's round down)
                                    actual_shares = max(int(actual_shares), 1)
                                    
                                    try:
                                        order_args = OrderArgs(price=price, size=actual_shares, side="BUY", token_id=token_id)
                                        resp = client.create_and_post_order(order_args)
                                        if resp and hasattr(resp, "success") and resp.success:
                                            ok(f"LIVE ORDER PLACED! ID: {resp.orderID}")
                                            clob_success = True
                                            actual_size = actual_shares * price
                                            
                                            if "daily_spent" not in state:
                                                state["daily_spent"] = {}
                                            state["daily_spent"][today_str] = daily_spent + actual_size
                                        else:
                                            warn(f"Live order failed: {resp}")
                                            continue
                                    except Exception as e:
                                        warn(f"Error placing live order: {e}")
                                        continue
                                
                                if paper or clob_success:
                                    actual_size = actual_shares * price # Finalize actual cost
                                    new_mkt = {
                                        "id": mkt["id"],
                                        "city": city,
                                        "date": date_str,
                                        "question": mkt["question"],
                                        "t_low": rng[0],
                                        "t_high": rng[1],
                                        "entry_price": price,
                                        "shares": actual_shares,
                                        "cost": actual_size,
                                        "status": "active",
                                        "peak_price": price,
                                        "forecast_snapshots": forecasts,
                                        "opened_at": datetime.now(timezone.utc).isoformat(),
                                        "mode": "live" if live else "paper"
                                    }
                                    state["balance"] -= actual_size
                                    save_market(new_mkt)
                                    mode_str = "LIVE" if live else "PAPER"
                                    ok(f"OPENED {mode_str} POSITION: ${actual_size:.2f} at ${price:.3f} for {actual_shares} shares")
                                    markets.append(new_mkt)
                                    break # One position per city/date
                else: continue
                break

    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    run_calibration(markets)

def show_report():
    state = load_state()
    markets = load_all_markets()
    active = [m for m in markets if m.get("status") == "active"]
    resolved = [m for m in markets if m.get("status") == "resolved"]
    
    print(f"\n{C.BOLD}{C.CYAN}📊 Weather Trading Report{C.RESET}")
    print(f"  Balance: {C.BOLD}${state['balance']:.2f}{C.RESET}")
    print(f"  Active Positions: {len(active)}")
    print(f"  Resolved Trades:  {len(resolved)}")
    
    if active:
        print(f"\n{C.BOLD}Current Positions:{C.RESET}")
        for m in active:
            print(f"  • {m['city'].upper()} {m['date']} | Cost: ${m['cost']:.1f} | Price: ${m.get('last_price', m['entry_price']):.3f}")
    
    if resolved:
        wins = sum(1 for m in resolved if m.get("payout", 0) > 0)
        pnl = sum(m.get("payout", 0) - m["cost"] for m in resolved)
        color = C.GREEN if pnl >= 0 else C.RED
        print(f"\n{C.BOLD}Performance:{C.RESET}")
        print(f"  Win Rate: {wins/len(resolved):.1%} ({wins}/{len(resolved)})")
        print(f"  Total PnL: {color}${pnl:+.2f}{C.RESET}")

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weather Trading Bot v1 (Global)")
    parser.add_argument("--paper", action="store_true", help="Execute paper trades in simulation")
    parser.add_argument("--live", action="store_true", help="Execute REAL live trades on Polymarket")
    parser.add_argument("--positions", action="store_true", help="Show current report")
    parser.add_argument("--reset", action="store_true", help="Wipe all data and reset balance")
    parser.add_argument("--loop", action="store_true", help="Run in a loop every hour")
    args = parser.parse_args()

    if args.reset:
        reset_sim()
    elif args.positions:
        show_report()
    elif args.loop:
        info("Entering 24/7 loop...")
        while True:
            scan_and_update(paper=args.paper, live=args.live)
            time.sleep(SCAN_INTERVAL)
    else:
        scan_and_update(paper=args.paper, live=args.live)
        show_report()
