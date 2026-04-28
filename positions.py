"""
positions.py — Position lifecycle management.
Handles stop-loss, trailing stops, forecast-shift closes, and signal evaluation.
Consolidates the previously duplicated position management logic.
"""

from datetime import datetime, timezone

from config import (
    LOCATIONS, MIN_EV, MAX_PRICE, MIN_VOLUME,
    MAX_SLIPPAGE, SANDBAG_SLIPPAGE, MIN_HOURS,
    STOP_LOSS_PCT, TRAILING_ACTIVATION_PCT,
    MIN_BET_SIZE, MAX_SANDBAGGED_PRICE,
    LIVE_TRADING, DAILY_SPEND_LIMIT,
    QUOTE_MAX_AGE_SECONDS,
)
from math_utils import bucket_prob, calc_ev, calc_kelly, bet_size, in_bucket
from polymarket_api import get_current_price
from quote_utils import guard_quote_for_action, log_skip_trade_action
from storage import load_all_markets, get_sigma


def check_stop_loss(
    pos: dict,
    current_price: float,
    entry: float,
) -> dict | None:
    """Evaluate stop-loss and trailing stop conditions.

    Returns a dict with close details if triggered, or None if no action.
    This is the single source of truth for stop logic (was duplicated in
    scan_and_update and monitor_positions).
    """
    stop = pos.get("stop_price", entry * STOP_LOSS_PCT)

    # Trailing: if up enough, move stop to breakeven
    trailing_activated = False
    if current_price >= entry * TRAILING_ACTIVATION_PCT and stop < entry:
        stop = entry
        trailing_activated = True

    # Check stop
    if current_price <= stop:
        pnl = round((current_price - entry) * pos["shares"], 2)
        reason = "stop_loss" if current_price < entry else "trailing_stop"
        label = "STOP" if current_price < entry else "TRAILING BE"
        return {
            "pnl": pnl,
            "exit_price": current_price,
            "close_reason": reason,
            "label": label,
            "stop_price": stop,
            "trailing_activated": trailing_activated,
        }

    # No close, but maybe update trailing
    if trailing_activated:
        return {
            "trailing_only": True,
            "stop_price": stop,
            "trailing_activated": True,
        }

    return None


def check_forecast_shift(
    pos: dict,
    forecast_temp: float,
    loc: dict,
) -> bool:
    """Check if the forecast has shifted far enough to warrant closing.

    Returns True if position should be closed due to forecast change.
    """
    old_bucket_low = pos["bucket_low"]
    old_bucket_high = pos["bucket_high"]
    unit = loc["unit"]
    buffer = 2.0 if unit == "F" else 1.0

    mid_bucket = (
        (old_bucket_low + old_bucket_high) / 2
        if old_bucket_low != -999 and old_bucket_high != 999
        else forecast_temp
    )
    forecast_far = abs(forecast_temp - mid_bucket) > (abs(mid_bucket - old_bucket_low) + buffer)

    return not in_bucket(forecast_temp, old_bucket_low, old_bucket_high) and forecast_far


def evaluate_signal(
    outcomes: list[dict],
    forecast_temp: float,
    best_source: str | None,
    city_slug: str,
    cal: dict,
    balance: float,
    snap_ts: str | None,
    live_client=None,
    today_spent: float = 0.0,
) -> dict | None:
    """Evaluate all outcomes for a market and return the best trade signal.

    Returns a position dict if a valid signal is found, else None.
    Returns None immediately if any risk guard (daily spend, etc.) would be breached.
    """
    sigma = get_sigma(cal, city_slug, best_source or "ecmwf")

    for o in outcomes:
        t_low, t_high = o["range"]
        price = o["price"]
        volume = o["volume"]

        if not in_bucket(forecast_temp, t_low, t_high):
            continue

        bid = o.get("bid")
        ask = o.get("ask")
        spread = o.get("spread")
        tradable_price = o["price"]

        # Defensive quote handling:
        # - If only one side is available, use it as tradable price.
        # - If no side is available, fall back to "price" and treat spread as unknown.
        if ask is None:
            ask = bid if bid is not None else tradable_price
        if bid is None:
            bid = ask if ask is not None else tradable_price

        quote_ok, quote_reason, quote_meta = guard_quote_for_action(
            bid=o.get("bid"),
            ask=o.get("ask"),
            quote_ts=o.get("quote_ts"),
            snapshot_ts=snap_ts,
            max_age_seconds=QUOTE_MAX_AGE_SECONDS,
        )
        quote_meta["market_id"] = o.get("market_id")
        if not quote_ok:
            log_skip_trade_action(quote_reason, quote_meta)
            continue

        # Slippage filter
        if spread is not None and spread > MAX_SLIPPAGE:
            continue
        if ask >= MAX_PRICE or volume < MIN_VOLUME:
            continue

        # Sandbagging: worsen the ask price to be conservative
        spread_penalty = (spread * 0.5) if spread is not None else 0.0
        sandbagged_ask = min(ask + SANDBAG_SLIPPAGE + spread_penalty, MAX_SANDBAGGED_PRICE)

        p = bucket_prob(forecast_temp, t_low, t_high, sigma)
        ev = calc_ev(p, sandbagged_ask)
        if ev < MIN_EV:
            continue

        kelly = calc_kelly(p, sandbagged_ask)
        size = bet_size(kelly, balance)

        # Live trading sizing rules
        if LIVE_TRADING:
            active_count = len([
                m for m in load_all_markets()
                if m.get("position") and m["position"].get("status") == "open"
            ])
            if balance < 100:
                if active_count >= 1:
                    continue
                size = min(balance * 0.9, 10.0)
            else:
                if active_count >= 10:
                    continue
                size = 10.0

            # Enforce daily spend limit: cap size so we don't overshoot
            budget_left = DAILY_SPEND_LIMIT - today_spent
            if budget_left <= 0:
                return None  # daily budget exhausted
            size = min(size, budget_left)

        if size < MIN_BET_SIZE:
            continue

        return {
            "market_id":     o["market_id"],
            "token_id":      o.get("token_id"),
            "question":      o["question"],
            "bucket_low":    t_low,
            "bucket_high":   t_high,
            "entry_price":   round(sandbagged_ask, 4),
            "ask_at_entry":  round(ask, 4),          # actual market ask for FOK execution
            "bid_at_entry":  bid,
            "spread":        round(spread, 4) if spread is not None else None,
            "shares":        round(size / sandbagged_ask, 2),
            "cost":          size,
            "p":             round(p, 4),
            "ev":            round(ev, 4),
            "kelly":         round(kelly, 4),
            "forecast_temp": forecast_temp,
            "forecast_src":  best_source,
            "sigma":         sigma,
            "opened_at":     snap_ts,
            "status":        "open",
            "pnl":           None,
            "exit_price":    None,
            "close_reason":  None,
            "closed_at":     None,
            "live_order_id": None,
        }


    return None
