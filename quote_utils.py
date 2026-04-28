"""Shared quote validation utilities for trade open/close decisions."""

from __future__ import annotations

import json
from datetime import datetime


def parse_iso_ts(value: str | None) -> datetime | None:
    """Parse ISO timestamps with optional Z suffix."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def guard_quote_for_action(
    *,
    bid: float | None,
    ask: float | None,
    quote_ts: str | None,
    snapshot_ts: str | None,
    max_age_seconds: int,
) -> tuple[bool, str, dict]:
    """Return whether quote is actionable with reason + structured metadata."""
    meta = {
        "bid": bid,
        "ask": ask,
        "quote_ts": quote_ts,
        "snapshot_ts": snapshot_ts,
        "max_age_seconds": max_age_seconds,
    }

    if bid is None or ask is None:
        return False, "missing_bid_ask", meta
    if not (0 < bid <= ask < 1):
        return False, "invalid_bid_ask", meta

    if snapshot_ts and quote_ts:
        snap_dt = parse_iso_ts(snapshot_ts)
        quote_dt = parse_iso_ts(quote_ts)
        if snap_dt and quote_dt:
            age_seconds = (snap_dt - quote_dt).total_seconds()
            meta["quote_age_seconds"] = round(age_seconds, 2)
            if age_seconds > max_age_seconds:
                return False, "stale_quote", meta

    return True, "ok", meta


def log_skip_trade_action(reason: str, details: dict) -> None:
    """Emit standardized structured warning logs for skipped trade actions."""
    print(json.dumps({
        "level": "warning",
        "event": "skip_trade_action",
        "reason": reason,
        **details,
    }, sort_keys=True))
