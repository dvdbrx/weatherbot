import re
with open("bot_v1.py", "r") as f:
    content = f.read()

missing_funcs = """
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
"""

content = content.replace("# MATH & UTILITIES", missing_funcs)
with open("bot_v1.py", "w") as f:
    f.write(content)
print("Patched bot_v1.py")
