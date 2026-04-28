"""
storage.py — Market data, state, and calibration persistence.
All file I/O for market records, bot state, and calibration data.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from config import (
    DATA_DIR, STATE_FILE, MARKETS_DIR, CALIBRATION_FILE,
    BALANCE, LOCATIONS, CALIBRATION_MIN,
    SIGMA_F, SIGMA_C,
    DAILY_SPEND_LIMIT, MAX_DRAWDOWN_PCT, MAX_DAILY_LOSSES,
)

# Maximum number of forecast/market snapshots to keep per market file
MAX_SNAPSHOTS = 120

# =============================================================================
# MARKET STORAGE
# =============================================================================

def market_path(city_slug: str, date_str: str) -> Path:
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"


def load_market(city_slug: str, date_str: str) -> dict | None:
    p = market_path(city_slug, date_str)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def save_market(market: dict) -> None:
    # Cap snapshot lists to prevent unbounded growth
    for key in ("forecast_snapshots", "market_snapshots"):
        if key in market and len(market[key]) > MAX_SNAPSHOTS:
            market[key] = market[key][-MAX_SNAPSHOTS:]

    p = market_path(market["city"], market["date"])
    p.write_text(json.dumps(market, indent=2, ensure_ascii=False), encoding="utf-8")


# --- Cache for load_all_markets ---
_MARKETS_CACHE: dict[str, dict] = {}
_LAST_DIR_MTIME: float = 0


def load_all_markets() -> list[dict]:
    """Load all market JSON files with mtime-based caching."""
    global _LAST_DIR_MTIME, _MARKETS_CACHE
    try:
        current_mtime = MARKETS_DIR.stat().st_mtime
    except Exception:
        current_mtime = 0

    if current_mtime <= _LAST_DIR_MTIME and _MARKETS_CACHE:
        return list(_MARKETS_CACHE.values())

    new_cache: dict[str, dict] = {}
    for f in MARKETS_DIR.glob("*.json"):
        try:
            fname = f.name
            if fname in _MARKETS_CACHE:
                fstat = f.stat()
                if fstat.st_mtime <= _MARKETS_CACHE[fname].get("_cached_mtime", 0):
                    new_cache[fname] = _MARKETS_CACHE[fname]
                    continue
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_cached_mtime"] = f.stat().st_mtime
            new_cache[fname] = data
        except Exception:
            pass

    _MARKETS_CACHE.clear()
    _MARKETS_CACHE.update(new_cache)
    _LAST_DIR_MTIME = current_mtime
    return list(_MARKETS_CACHE.values())


def new_market(city_slug: str, date_str: str, event: dict, hours: float) -> dict:
    """Create a fresh market record."""
    loc = LOCATIONS[city_slug]
    return {
        "city":               city_slug,
        "city_name":          loc["name"],
        "date":               date_str,
        "unit":               loc["unit"],
        "station":            loc["station"],
        "event_end_date":     event.get("endDate", ""),
        "hours_at_discovery": round(hours, 1),
        "status":             "open",
        "position":           None,
        "actual_temp":        None,
        "resolved_outcome":   None,
        "pnl":                None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# STATE STORAGE
# =============================================================================

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "balance":          BALANCE,
        "starting_balance": BALANCE,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     BALANCE,
        "equity":           BALANCE,
        "peak_equity":      BALANCE,
        "today_date":       "",
        "today_spent":      0.0,
        "today_losses":     0,
        "halted":           False,
        "halt_reason":      None,
        "state_desync":     False,
        "state_desync_reasons": [],
        "state_desync_warnings": [],
        "state_desync_since": None,
    }


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def reset_daily_if_new_day(state: dict) -> dict:
    """Reset daily counters if UTC date has changed. Returns updated state."""
    today = _today_utc()
    if state.get("today_date") != today:
        state["today_date"]   = today
        state["today_spent"]  = 0.0
        state["today_losses"] = 0
        # Only clear a daily-limit halt (not a drawdown halt)
        if state.get("halt_reason") in ("daily_spend", "daily_losses"):
            state["halted"]      = False
            state["halt_reason"] = None
    return state


def _position_mark_to_market(position: dict, outcomes: list[dict]) -> float | None:
    """Return best available mark for an open position from cached outcomes."""
    market_id = position.get("market_id")
    if not market_id:
        return None

    for outcome in outcomes or []:
        if outcome.get("market_id") != market_id:
            continue
        bid = outcome.get("bid")
        if bid is not None:
            return float(bid)
        price = outcome.get("price")
        if price is not None:
            return float(price)
    return None


def calculate_equity(state: dict, markets: list[dict]) -> float:
    """Compute equity = cash balance + unrealized PnL on all open positions."""
    balance = float(state.get("balance", 0.0))
    unrealized = 0.0

    for market in markets:
        position = market.get("position")
        if not position or position.get("status") != "open":
            continue

        mark = _position_mark_to_market(position, market.get("all_outcomes", []))
        if mark is None:
            continue

        entry = float(position.get("entry_price", 0.0))
        shares = float(position.get("shares", 0.0))
        unrealized += (mark - entry) * shares

    return round(balance + unrealized, 2)


def check_risk_guards(state: dict) -> tuple[bool, str | None]:
    """Evaluate all risk guards and return (halted, reason).

    Guards checked (in priority order):
      1. Manual halt flag already set
      2. Max drawdown kill switch  (permanent until manual reset)
      3. Daily spend limit         (resets next UTC day)
      4. Daily loss circuit breaker(resets next UTC day)
    """
    if state.get("halted"):
        return True, state.get("halt_reason", "manual_halt")

    equity = float(state.get("equity", state.get("balance", 0.0)))
    peak_equity = float(state.get("peak_equity", equity))

    # Guard 1 — max drawdown kill switch (equity-based)
    if peak_equity > 0 and (peak_equity - equity) / peak_equity >= MAX_DRAWDOWN_PCT:
        state["halted"]      = True
        state["halt_reason"] = "max_drawdown"
        return True, "max_drawdown"

    # Guard 2 — daily spend limit
    if state.get("today_spent", 0.0) >= DAILY_SPEND_LIMIT:
        state["halted"]      = True
        state["halt_reason"] = "daily_spend"
        return True, "daily_spend"

    # Guard 3 — daily loss circuit breaker
    if state.get("today_losses", 0) >= MAX_DAILY_LOSSES:
        state["halted"]      = True
        state["halt_reason"] = "daily_losses"
        return True, "daily_losses"

    return False, None



def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# =============================================================================
# CALIBRATION
# =============================================================================

def load_cal() -> dict:
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    return {}


def get_sigma(cal: dict, city_slug: str, source: str = "ecmwf") -> float:
    """Get calibrated sigma for a city/source, falling back to defaults."""
    key = f"{city_slug}_{source}"
    if key in cal:
        return cal[key]["sigma"]
    return SIGMA_F if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C


def run_calibration(markets: list[dict]) -> dict:
    """Recalculate sigma from resolved markets."""
    resolved = [
        m for m in markets
        if m.get("status") == "resolved" and m.get("actual_temp") is not None
    ]
    cal = load_cal()
    updated: list[str] = []
    errors_by_key: dict[str, list[float]] = {}

    for m in resolved:
        city = m["city"]
        actual_temp = m["actual_temp"]
        snapshots = m.get("forecast_snapshots", [])

        for source in ["ecmwf", "hrrr", "metar"]:
            snap = next(
                (s for s in reversed(snapshots) if s.get(source) is not None),
                None,
            )
            if snap is None:
                continue
            key = f"{city}_{source}"
            errors_by_key.setdefault(key, []).append(abs(snap[source] - actual_temp))

    for key, errors in errors_by_key.items():
        if len(errors) < CALIBRATION_MIN:
            continue

        city, source = key.rsplit("_", 1)
        mae = sum(errors) / len(errors)
        old = cal.get(key, {}).get(
            "sigma", SIGMA_F if LOCATIONS[city]["unit"] == "F" else SIGMA_C
        )
        new = round(mae, 3)
        cal[key] = {
            "sigma": new,
            "n": len(errors),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if abs(new - old) > 0.05:
            updated.append(f"{LOCATIONS[city]['name']} {source}: {old:.2f}->{new:.2f}")

    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    if updated:
        print(f"  [CAL] {', '.join(updated)}")
    return cal
