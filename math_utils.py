"""
math_utils.py — Pure mathematical functions for trading calculations.
Stateless, easily testable. No I/O, no side effects.
"""

import math
from config import (
    KELLY_FRACTION, MAX_BET,
    EDGE_BUCKET_LOW, EDGE_BUCKET_HIGH,
)


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bucket_prob(forecast: float, t_low: float, t_high: float, sigma: float = 2.0) -> float:
    """Probability that the actual temp lands in [t_low, t_high] given forecast and sigma.

    For edge buckets ("X or below" / "X or higher"), uses normal CDF.
    For standard buckets, uses the interval mass of a normal distribution.
    """
    sigma = max(float(sigma), 1e-6)
    forecast = float(forecast)

    if t_low == EDGE_BUCKET_LOW:
        p = norm_cdf((t_high - forecast) / sigma)
        return min(max(p, 0.0), 1.0)
    if t_high == EDGE_BUCKET_HIGH:
        p = 1.0 - norm_cdf((t_low - forecast) / sigma)
        return min(max(p, 0.0), 1.0)

    p = norm_cdf((t_high - forecast) / sigma) - norm_cdf((t_low - forecast) / sigma)
    return min(max(p, 0.0), 1.0)


def calc_ev(p: float, price: float) -> float:
    """Expected value per dollar wagered."""
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)


def calc_kelly(p: float, price: float) -> float:
    """Fractional Kelly criterion bet fraction."""
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)


def bet_size(kelly: float, balance: float) -> float:
    """Dollar bet size, capped by MAX_BET."""
    raw = kelly * balance
    return round(min(raw, MAX_BET), 2)


def in_bucket(forecast: float, t_low: float, t_high: float) -> bool:
    """Check whether forecast belongs to a canonical bucket interval.

    Canonical rule:
    - Interior buckets use lower-inclusive / upper-exclusive: [low, high)
    - Singleton buckets (low == high) match that exact rounded degree
    - Lower edge bucket (-inf, high) is exclusive at high
    - Upper edge bucket [low, +inf) is inclusive at low
    """
    f = float(forecast)

    # Singleton market (e.g., "Will it be exactly 72F?")
    if t_low == t_high:
        return round(f) == round(t_low)

    # Defensive guard for malformed market text/ranges.
    if t_low > t_high:
        return False

    # Explicit edge-bucket handling to avoid adjacent-boundary double matches.
    if t_low == EDGE_BUCKET_LOW:
        return f < t_high
    if t_high == EDGE_BUCKET_HIGH:
        return f >= t_low

    # Interior canonical interval.
    return t_low <= f < t_high
