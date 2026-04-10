"""
forecasts.py — Weather forecast data from multiple sources.
ECMWF (global), HRRR/GFS (US), METAR (real-time), Visual Crossing (actual).
"""

from datetime import datetime, timezone, timedelta

from config import LOCATIONS, TIMEZONES, VC_KEY
from http_utils import get_json


def get_ecmwf(city_slug: str, dates: list[str]) -> dict[str, float]:
    """ECMWF via Open-Meteo with bias correction. For all cities."""
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    result: dict[str, float] = {}
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={loc['lat']}&longitude={loc['lon']}"
            f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
            f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
            f"&models=ecmwf_ifs025&bias_correction=true"
        )
        data = get_json(url)
        if data and "error" not in data:
            for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                if date in dates and temp is not None:
                    result[date] = round(temp, 1) if unit == "C" else round(temp)
    except Exception as e:
        print(f"  [ECMWF] {city_slug}: {e}")
    return result


def get_hrrr(city_slug: str, dates: list[str]) -> dict[str, float]:
    """HRRR via Open-Meteo. US cities only, up to 48h horizon."""
    loc = LOCATIONS[city_slug]
    if loc["region"] != "us":
        return {}
    result: dict[str, float] = {}
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={loc['lat']}&longitude={loc['lon']}"
            f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
            f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
            f"&models=gfs_seamless"
        )
        data = get_json(url)
        if data and "error" not in data:
            for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                if date in dates and temp is not None:
                    result[date] = round(temp)
    except Exception as e:
        print(f"  [HRRR] {city_slug}: {e}")
    return result


def get_metar(city_slug: str) -> float | None:
    """Current observed temperature from METAR station. D+0 only."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
        data = get_json(url)
        if data and isinstance(data, list) and len(data) > 0:
            temp_c = data[0].get("temp")
            if temp_c is not None:
                if unit == "F":
                    return round(float(temp_c) * 9 / 5 + 32)
                return round(float(temp_c), 1)
    except Exception as e:
        print(f"  [METAR] {city_slug}: {e}")
    return None


def get_actual_temp(city_slug: str, date_str: str) -> float | None:
    """Actual temperature via Visual Crossing for closed markets."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    vc_unit = "us" if unit == "F" else "metric"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements=tempmax"
    )
    try:
        data = get_json(url)
        if data:
            days = data.get("days", [])
            if days and days[0].get("tempmax") is not None:
                return round(float(days[0]["tempmax"]), 1)
    except Exception as e:
        print(f"  [VC] {city_slug} {date_str}: {e}")
    return None


def take_forecast_snapshot(city_slug: str, dates: list[str]) -> dict[str, dict]:
    """Fetches forecasts from all sources and returns a snapshot per date."""
    now_str = datetime.now(timezone.utc).isoformat()
    ecmwf = get_ecmwf(city_slug, dates)
    hrrr = get_hrrr(city_slug, dates)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    two_days_out = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    snapshots: dict[str, dict] = {}
    for date in dates:
        snap = {
            "ts":    now_str,
            "ecmwf": ecmwf.get(date),
            "hrrr":  hrrr.get(date) if date <= two_days_out else None,
            "metar": get_metar(city_slug) if date == today else None,
        }
        # Best forecast: HRRR for US D+0/D+1, otherwise ECMWF
        loc = LOCATIONS[city_slug]
        if loc["region"] == "us" and snap["hrrr"] is not None:
            snap["best"] = snap["hrrr"]
            snap["best_source"] = "hrrr"
        elif snap["ecmwf"] is not None:
            snap["best"] = snap["ecmwf"]
            snap["best_source"] = "ecmwf"
        else:
            snap["best"] = None
            snap["best_source"] = None
        snapshots[date] = snap
    return snapshots
