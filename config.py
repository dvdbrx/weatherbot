"""
config.py — Centralized configuration, constants, and location data.
All tunable parameters load from config.json and environment variables.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONFIG FILE
# =============================================================================

_CONFIG_PATH = Path(__file__).parent / "config.json"

with open(_CONFIG_PATH, encoding="utf-8") as f:
    _cfg = json.load(f)

# =============================================================================
# TRADING PARAMETERS
# =============================================================================

BALANCE: float           = _cfg.get("balance", 10000.0)
MAX_BET: float           = _cfg.get("max_bet", 20.0)
MIN_EV: float            = _cfg.get("min_ev", 0.10)
MAX_PRICE: float         = _cfg.get("max_price", 0.45)
MIN_VOLUME: int          = _cfg.get("min_volume", 500)
MIN_HOURS: float         = _cfg.get("min_hours", 2.0)
MAX_HOURS: float         = _cfg.get("max_hours", 72.0)
KELLY_FRACTION: float    = _cfg.get("kelly_fraction", 0.25)
MAX_SLIPPAGE: float      = _cfg.get("max_slippage", 0.03)
SANDBAG_SLIPPAGE: float  = _cfg.get("sandbag_slippage", 0.03)
SCAN_INTERVAL: int       = _cfg.get("scan_interval", 3600)
CALIBRATION_MIN: int     = _cfg.get("calibration_min", 30)
MONITOR_INTERVAL: int    = 600  # monitor positions every 10 minutes

# =============================================================================
# LIVE TRADING
# =============================================================================

LIVE_TRADING: bool        = _cfg.get("live_trading_enabled", False)
DAILY_SPEND_LIMIT: float  = _cfg.get("daily_spend_limit", 5.0)
MAX_DRAWDOWN_PCT: float   = _cfg.get("max_drawdown_pct", 0.30)   # kill switch: halt if balance drops 30% from peak
MAX_DAILY_LOSSES: int     = _cfg.get("max_daily_losses", 3)       # circuit breaker: halt after N losses in one day

# =============================================================================
# POSITION MANAGEMENT CONSTANTS (formerly magic numbers)
# =============================================================================

STOP_LOSS_PCT: float          = 0.80   # close if price drops to 80% of entry
TRAILING_ACTIVATION_PCT: float = 1.20  # activate trailing after 20% gain
MIN_BET_SIZE: float           = 0.50   # minimum bet in dollars
MAX_SANDBAGGED_PRICE: float   = 0.99   # cap sandbagged ask to avoid math issues
RESOLUTION_WIN_THRESHOLD: float  = 0.95  # YES price >= this means WIN
RESOLUTION_LOSS_THRESHOLD: float = 0.05  # YES price <= this means LOSS
EDGE_BUCKET_LOW: float  = -999.0  # sentinel for "X or below" buckets
EDGE_BUCKET_HIGH: float =  999.0  # sentinel for "X or higher" buckets

# =============================================================================
# FORECAST PARAMETERS
# =============================================================================

SIGMA_F: float = 2.0   # default sigma for Fahrenheit cities
SIGMA_C: float = 1.2   # default sigma for Celsius cities

# =============================================================================
# API KEYS
# =============================================================================

VC_KEY: str = os.getenv("VISUAL_CROSSING_KEY", "")

# Polymarket / blockchain configuration
POLYGON_RPC_URL: str     = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
USDC_E_CONTRACT: str     = os.getenv("USDC_E_CONTRACT", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
USDC_NATIVE_CONTRACT: str = os.getenv("USDC_NATIVE_CONTRACT", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")

# =============================================================================
# DATA PATHS
# =============================================================================

DATA_DIR         = Path(__file__).parent / "data"
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
CALIBRATION_FILE = DATA_DIR / "calibration.json"

# =============================================================================
# LOCATIONS — keyed by Polymarket slug component
# =============================================================================

LOCATIONS: dict[str, dict] = {
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

TIMEZONES: dict[str, str] = {
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

MONTHS: list[str] = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def validate_data_dir() -> None:
    """Validate and create the data directory structure.
    Raises SystemExit if 'data' exists as a file or broken symlink.
    """
    if not DATA_DIR.is_dir() and (DATA_DIR.is_symlink() or DATA_DIR.exists()):
        if DATA_DIR.is_symlink():
            import os as _os
            target = _os.readlink(DATA_DIR)
            raise SystemExit(
                f"ERROR: 'data' is a broken symlink pointing to '{target}'. "
                "Please mount the drive or remove the symlink: rm data"
            )
        else:
            raise SystemExit("ERROR: 'data' exists but is a file, not a directory.")

    DATA_DIR.mkdir(exist_ok=True)
    MARKETS_DIR.mkdir(exist_ok=True)
