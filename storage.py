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
    }


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
    resolved = [m for m in markets if m.get("resolved") and m.get("actual_temp") is not None]
    cal = load_cal()
    updated: list[str] = []

    for source in ["ecmwf", "hrrr", "metar"]:
        for city in set(m["city"] for m in resolved):
            group = [m for m in resolved if m["city"] == city]
            errors: list[float] = []
            for m in group:
                snap = next(
                    (s for s in reversed(m.get("forecast_snapshots", []))
                     if s["source"] == source), None
                )
                if snap and snap.get("temp") is not None:
                    errors.append(abs(snap["temp"] - m["actual_temp"]))
            if len(errors) < CALIBRATION_MIN:
                continue
            mae = sum(errors) / len(errors)
            key = f"{city}_{source}"
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
