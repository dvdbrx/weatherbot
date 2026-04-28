"""
polymarket_client.py — Live trading client for Polymarket CLOB.
Wraps py-clob-client for order execution, balance checks, and order management.
"""

import os
import logging
import requests
from importlib import metadata
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client.exceptions import PolyApiException

from config import POLYGON_RPC_URL, USDC_E_CONTRACT

log = logging.getLogger(__name__)

GEOBLOCK_MSG = (
    "Polymarket 403: Trading restricted in your region.\n"
    "  Fix: Start a SOCKS5 proxy and set HTTPS_PROXY in .env:\n"
    "    ssh -D 1080 -q -C -N user@your-non-us-server\n"
    "    HTTPS_PROXY=socks5h://127.0.0.1:1080"
)


class PolymarketLiveClient:
    def __init__(self, private_key: str | None = None, host: str = "https://clob.polymarket.com", chain_id: int = 137):
        self.private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("Private key missing for Polymarket live client")

        # Check proxy configuration
        self.proxy_configured = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"))
        if not self.proxy_configured and not os.environ.get("SKIP_PROXY_WARN"):
            log.warning("⚠️  No HTTPS_PROXY set — live order placement may be geo-blocked (403).")
            log.warning("   See .env for proxy setup instructions.")
        elif self.proxy_configured:
            log.info(f"✅ Proxy configured: {os.environ.get('HTTPS_PROXY', 'via HTTP_PROXY')}")

        sdk_v2 = None
        try:
            sdk_v2 = metadata.version("py-clob-client-v2")
        except metadata.PackageNotFoundError:
            log.warning("⚠️  py-clob-client-v2 not installed. CLOB V2 order posting may fail.")
            log.warning("   Fix: pip install -U py-clob-client-v2")

        signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))
        funder = os.getenv("POLY_FUNDER") or None
        if signature_type in (1, 2) and not funder:
            log.warning(
                "⚠️  POLY_SIGNATURE_TYPE=%s but POLY_FUNDER is empty. "
                "If this wallet is a proxy/safe account, set POLY_FUNDER=0x...",
                signature_type
            )
        if sdk_v2:
            log.info("✅ py-clob-client-v2 detected: %s", sdk_v2)

        self.client = ClobClient(
            host,
            key=self.private_key,
            chain_id=chain_id,
            signature_type=signature_type,
            funder=funder,
        )

        creds = self.client.create_or_derive_api_creds()
        if isinstance(creds, dict):
            creds = ApiCreds(**creds)
        self.client.set_api_creds(creds)

        self.trading_address = self.client.get_address()
        self.collateral_address = self.client.get_collateral_address()

    def get_usdc_balance(self) -> float | None:
        """Returns the USDC collateral balance available for trading.
        Uses the CLOB API authenticated balance endpoint (primary),
        with on-chain RPC fallback.
        Returns None if both methods fail so callers can skip the sync
        rather than stamping $0.00 into state.
        """
        # Method 1: CLOB API balance/allowance endpoint (authenticated)
        try:
            res = self.client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            if "balance" in res:
                bal = float(res["balance"]) / 1e6
                return bal
        except Exception as e:
            log.warning(f"Balance method 1 (CLOB API) failed: {e}")

        # Method 2: Direct RPC read of USDC.e on Polygon (EOA fallback)
        try:
            data = "0x70a08231000000000000000000000000" + self.trading_address[2:]
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": USDC_E_CONTRACT, "data": data}, "latest"],
                "id": 1
            }
            r = requests.post(POLYGON_RPC_URL, json=payload, timeout=5).json()
            if 'result' in r and r['result'] != '0x':
                return int(r['result'], 16) / 1e6
        except Exception as e:
            log.warning(f"Balance method 2 (RPC fallback) failed: {e}")

        return None

    def get_open_positions(self) -> list:
        """Back-compat alias: returns open and pending orders."""
        return self.get_open_orders()

    def get_open_orders(self) -> list:
        """Return open/pending orders for this wallet."""
        try:
            orders = self.client.get_orders()
            return orders if isinstance(orders, list) else []
        except Exception as e:
            print(f"Error fetching orders: {e}")
            return []

    def get_recent_fills(self) -> list:
        """Return recent user fills/trades from the CLOB client.

        The py-clob-client surface varies by version, so we try supported
        method names in order and gracefully fall back to [].
        """
        candidate_methods = ("get_trades", "get_fills", "get_user_trades")
        for method_name in candidate_methods:
            method = getattr(self.client, method_name, None)
            if not callable(method):
                continue
            try:
                fills = method()
                if isinstance(fills, list):
                    return fills
            except TypeError:
                # Method exists but needs args in this library version.
                continue
            except Exception as e:
                log.warning(f"Error fetching fills via {method_name}: {e}")
                continue
        return []

    @staticmethod
    def _extract_token_id(record: dict) -> str | None:
        # Only token/asset identifiers are safe here; market/condition IDs are broader
        # and can cause false mismatches during reconciliation.
        for key in ("token_id", "tokenID", "tokenId", "asset_id", "assetId", "outcomeTokenId"):
            value = record.get(key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _extract_side(record: dict) -> str | None:
        side = record.get("side")
        if side is None:
            return None
        return str(side).upper()

    @staticmethod
    def _side_sign(side: str | None) -> float | None:
        if side in {"BUY", "BID", "LONG"}:
            return 1.0
        if side in {"SELL", "ASK", "SHORT"}:
            return -1.0
        return None

    @staticmethod
    def _extract_size(record: dict) -> float:
        for key in ("size", "amount", "matched_amount", "filled_size"):
            value = record.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def get_wallet_exposure(self) -> dict:
        """Build token-level exposure summary from open orders + fills."""
        open_orders = self.get_open_orders()
        fills = self.get_recent_fills()

        pending_by_token: dict[str, float] = {}
        for order in open_orders:
            if not isinstance(order, dict):
                continue
            token_id = self._extract_token_id(order)
            if not token_id:
                continue
            side = self._extract_side(order)
            sign = self._side_sign(side)
            if sign is None:
                continue
            size = self._extract_size(order)
            if size <= 0:
                continue
            pending_by_token[token_id] = pending_by_token.get(token_id, 0.0) + sign * size

        filled_by_token: dict[str, float] = {}
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            token_id = self._extract_token_id(fill)
            if not token_id:
                continue
            side = self._extract_side(fill)
            sign = self._side_sign(side)
            if sign is None:
                continue
            size = self._extract_size(fill)
            if size <= 0:
                continue
            filled_by_token[token_id] = filled_by_token.get(token_id, 0.0) + sign * size

        return {
            "open_orders": open_orders,
            "fills": fills,
            "pending_by_token": pending_by_token,
            "filled_by_token": filled_by_token,
        }

    @staticmethod
    def _sanitize(price: float, size: float) -> tuple[float, float]:
        """Enforce CLOB precision with explicit quantization and validation.

        Quantization rules:
          - price:  2 decimal places
          - shares: 2 decimal places
          - taker amount / cost (price * shares): max 4 decimal places

        Validation checks are done on unrounded intermediate values. If the
        quantized values still imply a taker amount with >4 decimals, reduce
        shares in 0.01 steps (while > 0) until constraints are satisfied.
        """
        cent = Decimal("0.01")
        ten_thousandth = Decimal("0.0001")

        # Explicit quantization to CLOB-supported grids.
        q_price = Decimal(str(price)).quantize(cent, rounding=ROUND_DOWN)
        q_size = Decimal(str(size)).quantize(cent, rounding=ROUND_DOWN)

        # Ensure cost precision constraints hold using unrounded multiplication.
        while q_size > 0:
            raw_cost = q_price * q_size
            if raw_cost == raw_cost.quantize(ten_thousandth, rounding=ROUND_DOWN):
                return float(q_price), float(q_size)
            q_size = (q_size - cent).quantize(cent, rounding=ROUND_DOWN)

        return float(q_price), 0.0

    def place_order(self, token_id: str, side: str, price: float, size: float) -> dict | None:
        """Places a GTC limit order. Use place_order_fok() for live fills."""
        try:
            price, size = self._sanitize(price, size)
            order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            order = self.client.create_order(order_args)
            return self.client.post_order(order, OrderType.GTC)
        except PolyApiException as e:
            if e.status_code == 403:
                log.error(GEOBLOCK_MSG)
            else:
                log.error(f"CLOB API error placing {side} order: {e}")
            return None
        except Exception as e:
            log.error(f"Error placing {side} order: {e}")
            return None

    def place_order_fok(self, token_id: str, side: str, price: float, size: float) -> dict | None:
        """Places a Fill-Or-Kill order at the given price.
        Fills immediately against resting orders at or better than price, or cancels.
        No resting orders are left in the book. Ideal for live market execution.
        price: should be the actual ask (BUY) or bid (SELL) from the orderbook.
        """
        try:
            price, size = self._sanitize(price, size)
            order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            order = self.client.create_order(order_args)
            return self.client.post_order(order, OrderType.FOK)
        except PolyApiException as e:
            if e.status_code == 403:
                log.error(GEOBLOCK_MSG)
            else:
                log.error(f"CLOB API error placing FOK {side} order: {e}")
            return None
        except Exception as e:
            log.error(f"Error placing FOK {side} order: {e}")
            return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Fetch a single order's current status from the CLOB."""
        try:
            return self.client.get_order(order_id)
        except Exception as e:
            log.error(f"Error fetching order status {order_id}: {e}")
            return None

    def confirm_fill_by_balance(
        self, bal_before: float, retries: int = 3, delay: float = 2.0
    ) -> tuple[bool, float]:
        """Confirm a trade executed by checking wallet balance decreased.
        Returns (filled: bool, actual_cost: float).
        Polls up to retries times with delay seconds between each.
        """
        import time
        for _ in range(retries):
            time.sleep(delay)
            bal_after = self.get_usdc_balance()
            if bal_after is None:
                log.warning("Fill confirmation retry skipped: wallet balance unavailable.")
                continue
            spent = round(bal_before - bal_after, 6)
            if spent > 0.001:   # balance actually decreased
                return True, spent
        return False, 0.0

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.client.cancel(order_id)
            return True
        except Exception as e:
            print(f"Error canceling order {order_id}: {e}")
            return False

    def cancel_all(self) -> bool:
        try:
            self.client.cancel_all()
            return True
        except Exception as e:
            print(f"Error canceling all orders: {e}")
            return False
