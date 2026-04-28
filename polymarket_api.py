"""
polymarket_api.py — Polymarket Gamma API interactions.
Event discovery, price fetching, market resolution, and question parsing.
"""

import re
import json
from datetime import datetime, timezone

from config import RESOLUTION_WIN_THRESHOLD, RESOLUTION_LOSS_THRESHOLD
from http_utils import get_json


def _as_float(value) -> float | None:
    """Best-effort float parsing."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_json_array(raw_value) -> list:
    """Parse list-like Gamma fields that may arrive as JSON strings."""
    if isinstance(raw_value, list):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _get_yes_side_quotes(market: dict) -> tuple[float | None, float | None, float | None]:
    """Extract YES-side bid/ask/price from same-outcome fields (not YES/NO pair values)."""
    # First preference: explicit YES quote fields.
    bid = _as_float(market.get("yesBid"))
    ask = _as_float(market.get("yesAsk"))
    price = (
        _as_float(market.get("yesPrice"))
        or _as_float(market.get("lastTradePrice"))
        or _as_float(market.get("lastPrice"))
        or _as_float(market.get("price"))
    )

    # Fallbacks seen on Gamma payload variants.
    if bid is None:
        bid = _as_float(market.get("bestBid"))
    if ask is None:
        ask = _as_float(market.get("bestAsk"))

    # Outcome-indexed bid/ask arrays where index 0 is YES.
    bid_prices = _parse_json_array(market.get("outcomeBidPrices"))
    ask_prices = _parse_json_array(market.get("outcomeAskPrices"))
    if bid is None and bid_prices:
        bid = _as_float(bid_prices[0])
    if ask is None and ask_prices:
        ask = _as_float(ask_prices[0])

    # If no traded/last price exists, fall back to available side(s).
    if price is None:
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        else:
            price = bid if bid is not None else ask

    return bid, ask, price


def get_polymarket_event(city_slug: str, month: str, day: int, year: int) -> dict | None:
    """Fetch event data from Polymarket Gamma API by slug."""
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        data = get_json(f"https://gamma-api.polymarket.com/events?slug={slug}")
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except Exception:
        pass
    return None


def get_market_price(market_id: str) -> float | None:
    """Get the current YES price for a market."""
    try:
        data = get_json(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(3, 5))
        if data:
            prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
            return float(prices[0])
    except Exception:
        return None
    return None


def check_market_resolved(market_id: str) -> bool | None:
    """Check if a market has resolved on Polymarket.

    Returns:
        True  — YES won
        False — NO won
        None  — still open or indeterminate
    """
    try:
        data = get_json(f"https://gamma-api.polymarket.com/markets/{market_id}")
        if not data:
            return None
        closed = data.get("closed", False)
        if not closed:
            return None
        prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
        yes_price = float(prices[0])
        if yes_price >= RESOLUTION_WIN_THRESHOLD:
            return True
        elif yes_price <= RESOLUTION_LOSS_THRESHOLD:
            return False
        return None
    except Exception as e:
        print(f"  [RESOLVE] {market_id}: {e}")
    return None


def parse_temp_range(question: str) -> tuple[float, float] | None:
    """Extract temperature range from a Polymarket question string.

    Returns (low, high) or None if unparseable.
    Sentinel values: -999 for "or below", 999 for "or higher".
    """
    if not question:
        return None
    num = r'(-?\d+(?:\.\d+)?)'

    if re.search(r'or below', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or below', question, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))

    if re.search(r'or higher', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or higher', question, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)

    m = re.search(r'between ' + num + r'-' + num + r'[°]?[FC]', question, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    m = re.search(r'be ' + num + r'[°]?[FC] on', question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)

    return None


def hours_to_resolution(end_date_str: str) -> float:
    """Hours remaining until market resolution."""
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0


def parse_outcomes(event: dict) -> list[dict]:
    """Parse all outcomes from a Polymarket event into a sorted list.

    Each outcome dict has: question, market_id, token_id, range, bid, ask,
    price, spread, volume. bid/ask/spread may be None if only partial quote
    data exists on Gamma.
    """
    outcomes = []
    for market in event.get("markets", []):
        question = market.get("question", "")
        mid = str(market.get("id", ""))
        volume = float(market.get("volume", 0))
        rng = parse_temp_range(question)
        if not rng:
            continue
        bid, ask, price = _get_yes_side_quotes(market)
        if price is None:
            continue

        clob_ids = market.get("clobTokenIds")
        token_id = (
            json.loads(clob_ids)[0]
            if isinstance(clob_ids, str)
            else (clob_ids[0] if isinstance(clob_ids, list) and clob_ids else None)
        )

        outcomes.append({
            "question":  question,
            "market_id": mid,
            "token_id":  token_id,
            "range":     rng,
            "bid":       round(bid, 4) if bid is not None else None,
            "ask":       round(ask, 4) if ask is not None else None,
            "price":     round(price, 4),
            "spread":    round(ask - bid, 4) if (bid is not None and ask is not None) else None,
            "volume":    round(volume, 0),
        })

    outcomes.sort(key=lambda x: x["range"][0])
    return outcomes


def get_current_price(outcomes: list[dict], market_id: str) -> float | None:
    """Find the current price for a specific market_id in an outcomes list.

    This utility eliminates the 4x duplicated price-lookup pattern.
    """
    for o in outcomes:
        if o["market_id"] == market_id:
            return o.get("bid") if o.get("bid") is not None else o["price"]
    return None
