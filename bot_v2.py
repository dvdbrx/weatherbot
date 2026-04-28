#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_v2.py — Weather Trading Bot for Polymarket (Orchestrator)
=============================================================
Tracks weather forecasts from 3 sources (ECMWF, HRRR, METAR),
compares with Polymarket markets, paper trades using Kelly criterion.

Usage:
    python bot_v2.py          # main loop
    python bot_v2.py report   # full report
    python bot_v2.py status   # balance and open positions
"""

import sys
import os
import time
import requests
from datetime import datetime, timezone, timedelta

from config import (
    LOCATIONS, MONTHS, TIMEZONES,
    LIVE_TRADING, SCAN_INTERVAL, MONITOR_INTERVAL,
    MAX_BET, MIN_HOURS, MAX_HOURS, CALIBRATION_MIN,
    STOP_LOSS_PCT, TRAILING_ACTIVATION_PCT,
    DAILY_SPEND_LIMIT, MAX_DRAWDOWN_PCT, MAX_DAILY_LOSSES,
    QUOTE_MAX_AGE_SECONDS,
    validate_data_dir, DATA_DIR, CALIBRATION_FILE,
)
from math_utils import in_bucket
from forecasts import take_forecast_snapshot
from polymarket_api import (
    get_polymarket_event, check_market_resolved,
    hours_to_resolution, parse_outcomes, get_current_price,
)
from quote_utils import guard_quote_for_action, log_skip_trade_action
from storage import (
    load_state, save_state,
    load_market, save_market, load_all_markets,
    new_market, load_cal, run_calibration, get_sigma,
    reset_daily_if_new_day, check_risk_guards, calculate_equity,
)
from positions import check_stop_loss, check_forecast_shift, evaluate_signal

# =============================================================================
# INIT
# =============================================================================

validate_data_dir()

live_client = None
if LIVE_TRADING:
    # Warn about proxy only if not on a system-level VPN
    if (not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY")
            and not os.environ.get("SKIP_PROXY_WARN")):
        print("⚠️  LIVE_TRADING enabled but no HTTPS_PROXY set.")
        print("   Order placement will fail from US IPs (403 geoblock).")
        print("   Fix: ssh -D 1080 user@non-us-server, then set HTTPS_PROXY in .env")
        print("   (Or set SKIP_PROXY_WARN=1 in .env if using a system-level VPN)")
        print()
    try:
        from polymarket_client import PolymarketLiveClient
        live_client = PolymarketLiveClient()
        print(f"Live Trading ENABLED. Address: {live_client.trading_address}")

        # Seed state with real wallet balance on first run
        _init_state = load_state()
        if _init_state.get("starting_balance", 0) == 10000.0 or _init_state["balance"] == 10000.0:
            _real_bal = live_client.get_usdc_balance()
            if _real_bal is not None and _real_bal > 0:
                _init_state["balance"]         = _real_bal
                _init_state["starting_balance"] = _real_bal
                _init_state["peak_balance"]     = _real_bal
                save_state(_init_state)
                print(f"  Seeded balance from wallet: ${_real_bal:.2f}")
    except Exception as e:
        print(f"Failed to initialize Polymarket Live Client: {e}")
        sys.exit(1)


# Module-level calibration cache
_cal: dict = {}

# =============================================================================
# POSITION CLOSE HELPERS
# =============================================================================

def _close_position(pos: dict, current_price: float, reason: str, ts: str | None) -> float:
    """Close a position and return the PnL."""
    pnl = round((current_price - pos["entry_price"]) * pos["shares"], 2)
    pos["closed_at"] = ts
    pos["close_reason"] = reason
    pos["exit_price"] = current_price
    pos["pnl"] = pnl
    pos["status"] = "closed"
    return pnl


def _execute_sell(pos: dict, current_price: float, reason_label: str) -> dict:
    """Execute a sell and return execution status metadata."""
    if not (LIVE_TRADING and live_client):
        return {"success": True, "order_id": None, "error": None}

    token_id = pos.get("token_id")
    if not token_id:
        return {"success": False, "order_id": None, "error": "missing_token_id"}

    print(f"  [LIVE] Placing SELL order ({reason_label}) for {pos['shares']} shares @ ${current_price:.3f}")
    resp = live_client.place_order(token_id, "SELL", current_price, pos["shares"])
    order_id = resp.get("orderID") if isinstance(resp, dict) else None
    if resp and resp.get("success"):
        return {"success": True, "order_id": order_id, "error": None}

    return {
        "success": False,
        "order_id": order_id,
        "error": str(resp) if resp is not None else "empty_response",
    }


def _mark_pending_exit_retry(mkt: dict, pos: dict, reason: str, current_price: float, sell_status: dict, ts: str | None) -> None:
    """Keep position open after failed live sell and persist retry context."""
    order_id = sell_status.get("order_id") if isinstance(sell_status, dict) else None
    error = sell_status.get("error") if isinstance(sell_status, dict) else str(sell_status)
    pos["status"] = "open"
    pos["pending_exit_retry"] = True
    pos["pending_exit_reason"] = reason
    pos["pending_exit_price"] = current_price
    pos["pending_exit_order_id"] = order_id
    pos["pending_exit_error"] = error
    pos["pending_exit_updated_at"] = ts

    print(
        "  [LIVE] SELL failed; keeping position open for retry | "
        f"market={mkt.get('id') or mkt.get('market_id') or pos.get('market_id')} "
        f"token={pos.get('token_id')} order_id={order_id} "
        f"reason={reason} error={error}"
    )



def _get_actionable_market_quote(
    outcomes: list[dict],
    market_id: str,
    snap_ts: str | None,
    action: str,
) -> tuple[float | None, dict | None]:
    outcome = next((o for o in outcomes if o.get("market_id") == market_id), None)
    if not outcome:
        log_skip_trade_action("missing_market_outcome", {
            "market_id": market_id,
            "snapshot_ts": snap_ts,
            "action": action,
        })
        return None, None

    bid = outcome.get("bid")
    ask = outcome.get("ask")
    meta = {
        "market_id": market_id,
        "snapshot_ts": snap_ts,
        "quote_ts": outcome.get("quote_ts"),
        "bid": bid,
        "ask": ask,
        "max_age_seconds": QUOTE_MAX_AGE_SECONDS,
        "action": action,
    }

    quote_ok, quote_reason, quote_meta = guard_quote_for_action(
        bid=bid,
        ask=ask,
        quote_ts=outcome.get("quote_ts"),
        snapshot_ts=snap_ts,
        max_age_seconds=QUOTE_MAX_AGE_SECONDS,
    )
    quote_meta.update({"market_id": market_id, "action": action})
    if not quote_ok:
        log_skip_trade_action(quote_reason, quote_meta)
        return None, outcome

    return bid, outcome


def _set_state_desync(state: dict, active: bool, reasons: list[str]) -> None:
    """Set/clear desync flags used to halt entries during reconciliation issues."""
    state["state_desync"] = active
    state["state_desync_reasons"] = reasons
    if active:
        state["halted"] = True
        state["halt_reason"] = "state_desync"
        state["state_desync_since"] = state.get("state_desync_since") or datetime.now(timezone.utc).isoformat()
    else:
        state["state_desync_since"] = None
        # Only clear halt if it is specifically caused by desync.
        if state.get("halt_reason") == "state_desync":
            state["halted"] = False
            state["halt_reason"] = None


def reconcile_live_state(state: dict) -> tuple[bool, list[str]]:
    """Compare local open positions vs. CLOB wallet exposure and flag desync."""
    was_desynced = bool(state.get("state_desync"))
    if not (LIVE_TRADING and live_client):
        if state.get("state_desync"):
            _set_state_desync(state, False, [])
        return False, []

    def _fmt_tokens(tokens: list[str], limit: int = 5) -> str:
        if len(tokens) <= limit:
            return str(tokens)
        head = tokens[:limit]
        return f"{head} ... (+{len(tokens) - limit} more)"

    local_open_tokens: set[str] = set()
    known_local_tokens: set[str] = set()
    for mkt in load_all_markets():
        pos = mkt.get("position")
        if not (pos and pos.get("token_id")):
            continue
        token_id = str(pos["token_id"])
        known_local_tokens.add(token_id)
        if pos.get("status") == "open":
            local_open_tokens.add(token_id)

    exposure = live_client.get_wallet_exposure()
    filled_by_token = exposure.get("filled_by_token", {})
    pending_by_token = exposure.get("pending_by_token", {})

    wallet_exposed_tokens = {token for token, qty in filled_by_token.items() if qty > 0.01}
    wallet_pending_tokens = {
        token for token, qty in pending_by_token.items()
        if abs(qty) > 0.01
    }

    reasons: list[str] = []
    warnings: list[str] = []

    # Only treat exposures for locally-known tokens as desync blockers.
    # Wallet may contain unrelated legacy/manual positions that this bot does not own.
    relevant_wallet_exposure = wallet_exposed_tokens & known_local_tokens
    wallet_only = sorted(relevant_wallet_exposure - local_open_tokens)
    if wallet_only:
        warnings.append(
            "wallet exposure exists for non-open local positions (non-blocking): "
            f"{_fmt_tokens(wallet_only)}"
        )

    local_only = sorted(local_open_tokens - wallet_exposed_tokens)
    if local_only:
        reasons.append(
            "local position open but wallet exposure missing: "
            f"{_fmt_tokens(local_only)}"
        )

    relevant_pending = sorted(wallet_pending_tokens & known_local_tokens)
    if relevant_pending:
        warnings.append(
            "wallet has open/pending orders for known tokens (non-blocking): "
            f"{_fmt_tokens(relevant_pending)}"
        )

    desync = len(reasons) > 0
    _set_state_desync(state, desync, reasons)
    state["state_desync_warnings"] = warnings

    if desync:
        print("🚨 [DESYNC] Local state does not match CLOB wallet exposure.")
        for reason in reasons:
            print(f"🚨 [DESYNC] {reason}")
        print("🚨 [DESYNC] New entries are HALTED until reconciliation succeeds.")
    elif warnings:
        print("⚠️ [DESYNC] Non-blocking wallet/local reconciliation warnings:")
        for warning in warnings:
            print(f"⚠️ [DESYNC] {warning}")
    elif was_desynced:
        print("✅ [DESYNC] Local state reconciled with CLOB wallet; entry halt cleared.")

    return desync, reasons

# =============================================================================
# CORE: SCAN AND UPDATE
# =============================================================================

def scan_and_update() -> tuple[int, int, int]:
    """Main scan cycle: update forecasts, manage positions, resolve markets.

    Returns (new_positions, closed_positions, resolved_markets).
    """
    global _cal
    now = datetime.now(timezone.utc)
    state = load_state()

    # Roll over daily counters if UTC date changed
    state = reset_daily_if_new_day(state)

    # Equity-based drawdown checks (mark open positions to latest bid/price).
    markets = load_all_markets()
    state["equity"] = calculate_equity(state, markets)
    state["peak_equity"] = max(state.get("peak_equity", state["equity"]), state["equity"])

    desync, _ = reconcile_live_state(state)
    if desync:
        save_state(state)
        return 0, 0, 0

    # Check risk guards before doing anything else.
    halted, halt_reason = check_risk_guards(state)
    if halted:
        save_state(state)
        _HALT_REASONS = {
            "max_drawdown": f"⛔ MAX DRAWDOWN exceeded ({MAX_DRAWDOWN_PCT:.0%} from equity peak) — bot HALTED.",
            "daily_spend":  f"🛑 Daily spend limit ${DAILY_SPEND_LIMIT:.2f} reached — no new trades today.",
            "daily_losses": f"🛑 Daily loss limit ({MAX_DAILY_LOSSES} losses) reached — no new trades today.",
            "state_desync": "🚨 State desync detected — new entries halted until local/CLOB reconciliation succeeds.",
        }
        print(_HALT_REASONS.get(halt_reason, f"⛔ Trading halted: {halt_reason}"))
        return 0, 0, 0

    if LIVE_TRADING and live_client:
        live_balance = live_client.get_usdc_balance()
        if live_balance:
            state["balance"] = live_balance
            print(f"  [LIVE TRADING] Synced balance: ${live_balance:.2f}")
        else:
            print(f"  [LIVE TRADING] Balance read failed — using cached ${state['balance']:.2f}")

    balance = state["balance"]
    new_pos = 0
    closed = 0
    resolved = 0

    for city_slug, loc in LOCATIONS.items():
        unit = loc["unit"]
        unit_sym = "F" if unit == "F" else "C"
        print(f"  -> {loc['name']}...", end=" ", flush=True)

        try:
            dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
            snapshots = take_forecast_snapshot(city_slug, dates)
            time.sleep(0.3)
        except Exception as e:
            print(f"skipped ({e})")
            continue

        for i, date in enumerate(dates):
            dt = datetime.strptime(date, "%Y-%m-%d")
            event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
            if not event:
                continue

            end_date = event.get("endDate", "")
            hours = hours_to_resolution(end_date) if end_date else 0
            horizon = f"D+{i}"

            mkt = load_market(city_slug, date)
            if mkt is None:
                if hours < MIN_HOURS or hours > MAX_HOURS:
                    continue
                mkt = new_market(city_slug, date, event, hours)

            if mkt["status"] == "resolved":
                continue

            # Parse outcomes
            outcomes = parse_outcomes(event)
            mkt["all_outcomes"] = outcomes

            # Forecast snapshot
            snap = snapshots.get(date, {})
            forecast_snap = {
                "ts":          snap.get("ts"),
                "horizon":     horizon,
                "hours_left":  round(hours, 1),
                "ecmwf":       snap.get("ecmwf"),
                "hrrr":        snap.get("hrrr"),
                "metar":       snap.get("metar"),
                "best":        snap.get("best"),
                "best_source": snap.get("best_source"),
            }
            mkt["forecast_snapshots"].append(forecast_snap)

            # Market price snapshot
            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            market_snap = {
                "ts":         snap.get("ts"),
                "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":  top["price"] if top else None,
            }
            mkt["market_snapshots"].append(market_snap)

            forecast_temp = snap.get("best")
            best_source = snap.get("best_source")

            # --- STOP-LOSS AND TRAILING STOP ---
            if mkt.get("position") and mkt["position"].get("status") == "open":
                pos = mkt["position"]
                current_price = get_current_price(outcomes, pos["market_id"])

                if current_price is not None:
                    actionable_price, _ = _get_actionable_market_quote(
                        outcomes, pos["market_id"], snap.get("ts"), "close_stop_loss"
                    )
                    if actionable_price is not None:
                        current_price = actionable_price
                        result = check_stop_loss(pos, current_price, pos["entry_price"])

                        if result and not result.get("trailing_only"):
                            _execute_sell(pos, current_price, result["close_reason"])
                    result = check_stop_loss(pos, current_price, pos["entry_price"])

                    if result and not result.get("trailing_only"):
                        sell_status = _execute_sell(pos, current_price, result["close_reason"])
                        if sell_status["success"]:
                            pos.pop("pending_exit_retry", None)
                            pos.pop("pending_exit_reason", None)
                            pos.pop("pending_exit_price", None)
                            pos.pop("pending_exit_order_id", None)
                            pos.pop("pending_exit_error", None)
                            pos.pop("pending_exit_updated_at", None)
                            pnl = _close_position(pos, current_price, result["close_reason"], snap.get("ts"))
                            balance += pos["cost"] + pnl
                            closed += 1
                            print(f"  [{result['label']}] {loc['name']} {date} | "
                                  f"entry ${pos['entry_price']:.3f} exit ${current_price:.3f} | "
                                  f"PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
                        elif result and result.get("trailing_only"):
                            pos["stop_price"] = result["stop_price"]
                            pos["trailing_activated"] = True
                        else:
                            _mark_pending_exit_retry(mkt, pos, result["close_reason"], current_price, sell_status, snap.get("ts"))
                    elif result and result.get("trailing_only"):
                        pos["stop_price"] = result["stop_price"]
                        pos["trailing_activated"] = True

            # --- CLOSE POSITION if forecast shifted ---
            if (mkt.get("position") and mkt["position"].get("status") == "open"
                    and forecast_temp is not None):
                pos = mkt["position"]
                if check_forecast_shift(pos, forecast_temp, loc):
                    current_price = get_current_price(outcomes, pos["market_id"])
                    if current_price is not None:
                        actionable_price, _ = _get_actionable_market_quote(
                            outcomes, pos["market_id"], snap.get("ts"), "close_forecast_shift"
                        )
                        if actionable_price is not None:
                            current_price = actionable_price
                            _execute_sell(pos, current_price, "Forecast Shift")
                        sell_status = _execute_sell(pos, current_price, "Forecast Shift")
                        if sell_status["success"]:
                            pos.pop("pending_exit_retry", None)
                            pos.pop("pending_exit_reason", None)
                            pos.pop("pending_exit_price", None)
                            pos.pop("pending_exit_order_id", None)
                            pos.pop("pending_exit_error", None)
                            pos.pop("pending_exit_updated_at", None)
                            pnl = _close_position(pos, current_price, "forecast_changed", snap.get("ts"))
                            balance += pos["cost"] + pnl
                            closed += 1
                            print(f"  [CLOSE] {loc['name']} {date} — forecast changed | "
                                  f"PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
                        else:
                            _mark_pending_exit_retry(mkt, pos, "forecast_changed", current_price, sell_status, snap.get("ts"))

            # --- OPEN POSITION ---
            if (
                not state.get("state_desync")
                and not mkt.get("position")
                and forecast_temp is not None
                and hours >= MIN_HOURS
            ):
                signal = evaluate_signal(
                    outcomes, forecast_temp, best_source,
                    city_slug, date, _cal, balance, snap.get("ts"), live_client,
                    today_spent=state.get("today_spent", 0.0),
                )
                if signal:
                    if LIVE_TRADING and live_client:
                        if signal.get("token_id"):
                            # FOK at actual ask price (not sandbagged) — fills immediately or cancels
                            fok_price  = signal.get("ask_at_entry", signal["entry_price"])
                            fok_shares = round(signal["cost"] / fok_price, 2)
                            bal_before = live_client.get_usdc_balance()

                            if bal_before is None:
                                print("  [LIVE] ⚠️ Unable to confirm fill: wallet balance unavailable before order. "
                                      "Skipping trade attempt.")
                                signal = None
                                continue

                            print(f"  [LIVE] FOK BUY {fok_shares} shares @ ${fok_price:.3f} "
                                  f"| expected cost ${signal['cost']:.2f} "
                                  f"| wallet before ${bal_before:.2f}")
                            resp = live_client.place_order_fok(
                                signal["token_id"], "BUY", fok_price, fok_shares
                            )

                            if resp and resp.get("success"):
                                order_id = resp.get("orderID", "")
                                signal["live_order_id"] = order_id
                                filled, actual_cost = live_client.confirm_fill_by_balance(
                                    bal_before, retries=3, delay=2.0
                                )
                                if filled:
                                    signal["entry_price"] = round(fok_price, 4)
                                    signal["shares"]      = fok_shares
                                    signal["cost"]        = actual_cost
                                    print(f"  [LIVE] ✅ FILL CONFIRMED | {order_id} "
                                          f"| actual cost ${actual_cost:.4f} "
                                          f"| wallet ${bal_before:.2f} → ${bal_before - actual_cost:.2f}")
                                else:
                                    print(f"  [LIVE] ❌ FOK not filled — no liquidity at ${fok_price:.3f} "
                                          f"(order {order_id} cancelled)")
                                    signal = None
                            else:
                                print(f"  [LIVE] Order placement failed: {resp}")
                                signal = None
                        else:
                            print(f"  [LIVE] Skipping: no token_id for market {signal['market_id']}")
                            signal = None


                    if signal:
                        balance -= signal["cost"]
                        state["today_spent"] = round(state.get("today_spent", 0.0) + signal["cost"], 4)
                        mkt["position"] = signal
                        state["total_trades"] += 1
                        new_pos += 1
                        bucket_label = f"{signal['bucket_low']}-{signal['bucket_high']}{unit_sym}"
                        print(f"  [BUY]  {loc['name']} {horizon} {date} | {bucket_label} | "
                              f"${signal['entry_price']:.3f} | EV {signal['ev']:+.2f} | "
                              f"${signal['cost']:.2f} ({signal['forecast_src'].upper()}) "
                              f"[spent today: ${state['today_spent']:.2f}/{DAILY_SPEND_LIMIT:.2f}]")

            # Market closed by time
            if hours < 0.5 and mkt["status"] == "open":
                mkt["status"] = "closed"

            save_market(mkt)
            time.sleep(0.1)

        print("ok")

    # --- AUTO-RESOLUTION ---
    for mkt in load_all_markets():
        if mkt["status"] == "resolved":
            continue
        pos = mkt.get("position")
        if not pos or pos.get("status") != "open":
            continue
        market_id = pos.get("market_id")
        if not market_id:
            continue

        won = check_market_resolved(market_id)
        if won is None:
            continue

        price = pos["entry_price"]
        size = pos["cost"]
        shares = pos["shares"]
        pnl = round(shares * (1 - price), 2) if won else round(-size, 2)

        balance += size + pnl
        pos["exit_price"] = 1.0 if won else 0.0
        pos["pnl"] = pnl
        pos["close_reason"] = "resolved"
        pos["closed_at"] = now.isoformat()
        pos["status"] = "closed"
        mkt["pnl"] = pnl
        mkt["status"] = "resolved"
        mkt["resolved_outcome"] = "win" if won else "loss"

        if won:
            state["wins"] += 1
        else:
            state["losses"] += 1
            state["today_losses"] = state.get("today_losses", 0) + 1

        result = "WIN" if won else "LOSS"
        print(f"  [{result}] {mkt['city_name']} {mkt['date']} | "
              f"PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
        resolved += 1
        save_market(mkt)
        time.sleep(0.3)

    state["balance"] = round(balance, 2)
    state["peak_balance"] = max(state.get("peak_balance", balance), balance)

    refreshed_markets = load_all_markets()
    state["equity"] = calculate_equity(state, refreshed_markets)
    state["peak_equity"] = max(state.get("peak_equity", state["equity"]), state["equity"])
    halted, halt_reason = check_risk_guards(state)
    if halted:
        _HALT_REASONS = {
            "max_drawdown": f"⛔ MAX DRAWDOWN exceeded ({MAX_DRAWDOWN_PCT:.0%} from equity peak) — bot HALTED.",
            "daily_spend":  f"🛑 Daily spend limit ${DAILY_SPEND_LIMIT:.2f} reached — no new trades today.",
            "daily_losses": f"🛑 Daily loss limit ({MAX_DAILY_LOSSES} losses) reached — no new trades today.",
        }
        print(_HALT_REASONS.get(halt_reason, f"⛔ Trading halted: {halt_reason}"))

    save_state(state)

    # Run calibration if enough data
    all_mkts = load_all_markets()
    resolved_count = len([m for m in all_mkts if m["status"] == "resolved"])
    if resolved_count >= CALIBRATION_MIN:
        _cal = run_calibration(all_mkts)

    return new_pos, closed, resolved


# =============================================================================
# MONITOR (quick stop-check without full scan)
# =============================================================================

def monitor_positions() -> int:
    """Quick stop check on open positions without full scan."""
    state = load_state()
    reconcile_live_state(state)
    save_state(state)

    markets = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if not open_pos:
        return 0

    balance = state["balance"]
    closed = 0

    for mkt in open_pos:
        pos = mkt["position"]
        mid = pos["market_id"]
        ts = datetime.now(timezone.utc).isoformat()

        # Refresh outcomes from Gamma so stop checks use live prices.
        current_price = None
        date_obj = datetime.strptime(mkt["date"], "%Y-%m-%d")
        event = get_polymarket_event(
            mkt["city"],
            MONTHS[date_obj.month - 1],
            date_obj.day,
            date_obj.year,
        )
        if event:
            outcomes = parse_outcomes(event)
            if outcomes:
                mkt["all_outcomes"] = outcomes
                top = max(outcomes, key=lambda x: x["price"])
                mkt.setdefault("market_snapshots", [])
                mkt["market_snapshots"].append({
                    "ts": ts,
                    "top_bucket": f"{top['range'][0]}-{top['range'][1]}{mkt['unit']}",
                    "top_price": top["price"],
                })
                current_price = get_current_price(outcomes, mid)

        # Fallback: fetch direct market price if event refresh failed.
        if current_price is None:
            current_price = get_current_price(mkt.get("all_outcomes", []), mid)
        if current_price is None:
            continue

        entry = pos["entry_price"]
        result = check_stop_loss(pos, current_price, entry)

        if result and not result.get("trailing_only"):
            sell_status = _execute_sell(pos, current_price, result["close_reason"])
            if sell_status["success"]:
                pos.pop("pending_exit_retry", None)
                pos.pop("pending_exit_reason", None)
                pos.pop("pending_exit_price", None)
                pos.pop("pending_exit_order_id", None)
                pos.pop("pending_exit_error", None)
                pos.pop("pending_exit_updated_at", None)
                pnl = _close_position(pos, current_price, result["close_reason"],
                                      ts)
                balance += pos["cost"] + pnl
                closed += 1
                city_name = LOCATIONS.get(mkt["city"], {}).get("name", mkt["city"])
                print(f"  [{result['label']}] {city_name} {mkt['date']} | "
                      f"entry ${entry:.3f} exit ${current_price:.3f} | "
                      f"PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
                save_market(mkt)
            else:
                _mark_pending_exit_retry(mkt, pos, result["close_reason"], current_price, sell_status, ts)
                save_market(mkt)
        elif result and result.get("trailing_only"):
            pos["stop_price"] = result["stop_price"]
            pos["trailing_activated"] = True
            city_name = LOCATIONS.get(mkt["city"], {}).get("name", mkt["city"])
            print(f"  [TRAILING] {city_name} {mkt['date']} — stop moved to breakeven ${entry:.3f}")
            save_market(mkt)

    if closed:
        state["balance"] = round(balance, 2)
    save_state(state)

    return closed


# =============================================================================
# REPORT
# =============================================================================

def print_status() -> None:
    state = load_state()
    markets = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal = state["balance"]
    eq = state.get("equity", bal)
    start = state["starting_balance"]
    wins = state["wins"]
    losses = state["losses"]
    total = wins + losses

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — STATUS")
    print(f"{'='*55}")

    # Live wallet balance (fetched fresh from chain/CLOB)
    if LIVE_TRADING and live_client:
        try:
            wallet_bal = live_client.get_usdc_balance()
            if wallet_bal:
                print(f"  Wallet USDC: ${wallet_bal:,.2f}  (address: {live_client.trading_address[:10]}...)")
                state["balance"] = wallet_bal
                save_state(state)
                bal = wallet_bal
            else:
                print(f"  Wallet USDC: ⚠️  could not fetch (both methods failed or returned $0)")
        except Exception as e:
            print(f"  Wallet USDC: ⚠️  could not fetch ({e})")

    ret_pct = (bal - start) / start * 100 if start else 0
    print(f"  Bot balance: ${bal:,.2f}  (start ${start:,.2f}, {'+'if ret_pct>=0 else ''}{ret_pct:.1f}%)")
    print(f"  Trades:      {total} | W: {wins} | L: {losses} | WR: {wins/total:.0%}" if total else "  No trades yet")
    print(f"  Open:        {len(open_pos)}")
    print(f"  Resolved:    {len(resolved)}")


    # Risk guard summary
    peak = state.get("peak_equity", eq)
    drawdown = (peak - eq) / peak * 100 if peak > 0 else 0
    today_spent  = state.get("today_spent", 0.0)
    today_losses = state.get("today_losses", 0)
    halted       = state.get("halted", False)
    halt_reason  = state.get("halt_reason")
    print(f"\n  Risk Guards:")
    print(f"    Daily spend:  ${today_spent:.2f} / ${DAILY_SPEND_LIMIT:.2f}")
    print(f"    Daily losses: {today_losses} / {MAX_DAILY_LOSSES}")
    print(f"    Equity:       ${eq:,.2f} (peak ${peak:,.2f})")
    print(f"    Peak drawdown:{drawdown:.1f}% (limit {MAX_DRAWDOWN_PCT*100:.0f}%)")
    if halted:
        print(f"    ⛔ HALTED — reason: {halt_reason}")
        if state.get("state_desync"):
            since = state.get("state_desync_since")
            if since:
                print(f"    🚨 Desync since: {since}")
            for reason in state.get("state_desync_reasons", []):
                print(f"    🚨 {reason}")
    elif state.get("state_desync_warnings"):
        for warning in state.get("state_desync_warnings", []):
            print(f"    ⚠️ {warning}")
    else:
        print(f"    ✅ Trading active")


    if open_pos:
        print(f"\n  Open positions:")
        total_unrealized = 0.0
        for m in open_pos:
            pos = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"

            current_price = get_current_price(m.get("all_outcomes", []), pos["market_id"])
            if current_price is None:
                current_price = pos["entry_price"]

            unrealized = round((current_price - pos["entry_price"]) * pos["shares"], 2)
            total_unrealized += unrealized
            pnl_str = f"{'+'if unrealized>=0 else ''}{unrealized:.2f}"

            print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
                  f"entry ${pos['entry_price']:.3f} -> ${current_price:.3f} | "
                  f"PnL: {pnl_str} | {pos['forecast_src'].upper()}")

        sign = "+" if total_unrealized >= 0 else ""
        print(f"\n  Unrealized PnL: {sign}{total_unrealized:.2f}")

    print(f"{'='*55}\n")


def print_report() -> None:
    markets = load_all_markets()
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — FULL REPORT")
    print(f"{'='*55}")

    if not resolved:
        print("  No resolved markets yet.")
        return

    total_pnl = sum(m["pnl"] for m in resolved)
    wins = [m for m in resolved if m["resolved_outcome"] == "win"]
    losses = [m for m in resolved if m["resolved_outcome"] == "loss"]

    print(f"\n  Total resolved: {len(resolved)}")
    print(f"  Wins:           {len(wins)} | Losses: {len(losses)}")
    print(f"  Win rate:       {len(wins)/len(resolved):.0%}")
    print(f"  Total PnL:      {'+'if total_pnl>=0 else ''}{total_pnl:.2f}")

    print(f"\n  By city:")
    for city in sorted(set(m["city"] for m in resolved)):
        group = [m for m in resolved if m["city"] == city]
        w = len([m for m in group if m["resolved_outcome"] == "win"])
        pnl = sum(m["pnl"] for m in group)
        name = LOCATIONS[city]["name"]
        print(f"    {name:<16} {w}/{len(group)} ({w/len(group):.0%})  "
              f"PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

    print(f"\n  Market details:")
    for m in sorted(resolved, key=lambda x: x["date"]):
        pos = m.get("position", {})
        unit_sym = "F" if m["unit"] == "F" else "C"
        snaps = m.get("forecast_snapshots", [])
        first_fc = snaps[0]["best"] if snaps else None
        last_fc = snaps[-1]["best"] if snaps else None
        label = (f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit_sym}"
                 if pos else "no position")
        result = m["resolved_outcome"].upper()
        pnl_str = (f"{'+'if m['pnl']>=0 else ''}{m['pnl']:.2f}"
                   if m["pnl"] is not None else "-")
        fc_str = f"forecast {first_fc}->{last_fc}{unit_sym}" if first_fc else "no forecast"
        actual = f"actual {m['actual_temp']}{unit_sym}" if m["actual_temp"] else ""
        print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
              f"{fc_str} | {actual} | {result} {pnl_str}")

    print(f"{'='*55}\n")


# =============================================================================
# MAIN LOOP
# =============================================================================

def run_loop() -> None:
    global _cal
    _cal = load_cal()

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — STARTED")
    print(f"{'='*55}")

    st = load_state()
    print(f"  Cities:     {len(LOCATIONS)}")
    print(f"  Balance:    ${st['balance']:,.2f} | Max bet: ${MAX_BET}")
    print(f"  Scan:       {SCAN_INTERVAL//60} min | Monitor: {MONITOR_INTERVAL//60} min")
    print(f"  Sources:    ECMWF + HRRR(US) + METAR(D+0)")
    print(f"  Data:       {DATA_DIR.resolve()}")
    print(f"  Ctrl+C to stop\n")

    last_full_scan = 0

    state = load_state()
    state["last_started"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    while True:
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if now_ts - last_full_scan >= SCAN_INTERVAL:
            print(f"[{now_str}] full scan...")
            try:
                new_pos, closed, resolved = scan_and_update()
                state = load_state()
                print(f"  balance: ${state['balance']:,.2f} | "
                      f"new: {new_pos} | closed: {closed} | resolved: {resolved}")
                last_full_scan = time.time()
            except KeyboardInterrupt:
                print(f"\n  Stopping — saving state...")
                save_state(load_state())
                print(f"  Done. Bye!")
                break
            except requests.exceptions.ConnectionError:
                print(f"  Connection lost — waiting 60 sec")
                time.sleep(60)
                continue
            except Exception as e:
                print(f"  Error: {e} — waiting 60 sec")
                time.sleep(60)
                continue
        else:
            print(f"[{now_str}] monitoring positions...")
            try:
                stopped = monitor_positions()
                if stopped:
                    state = load_state()
                    print(f"  balance: ${state['balance']:,.2f}")
            except Exception as e:
                print(f"  Monitor error: {e}")

        state = load_state()
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

        try:
            time.sleep(MONITOR_INTERVAL)
        except KeyboardInterrupt:
            print(f"\n  Stopping — saving state...")
            save_state(load_state())
            print(f"  Done. Bye!")
            break


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_loop()
    elif cmd == "status":
        _cal = load_cal()
        print_status()
    elif cmd == "report":
        _cal = load_cal()
        print_report()
    else:
        print("Usage: python bot_v2.py [run|status|report]")
