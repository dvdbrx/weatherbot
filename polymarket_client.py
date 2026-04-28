"""
polymarket_client.py — Live trading client for Polymarket CLOB.
Wraps py-clob-client for order execution, balance checks, and order management.
"""

import os
import logging
import requests
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

        self.client = ClobClient(host, key=self.private_key, chain_id=chain_id)

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
        """Returns a list of open and pending orders."""
        try:
            return self.client.get_orders()
        except Exception as e:
            print(f"Error fetching orders: {e}")
            return []

    @staticmethod
    def _sanitize(price: float, size: float) -> tuple[float, float]:
        """Enforce Polymarket CLOB precision rules before order creation.

        The CLOB enforces:
          - maker amount (shares): max 2 decimal places
          - taker amount (USDC):   max 4 decimal places
          - price:                 max 2 decimal places (cent grid)

        Rounding price first, then re-deriving size from the rounded price
        ensures the implied USDC cost (size * price) also stays within 4dp.
        We floor shares slightly (rather than round) to avoid overshooting the
        intended $ spend.
        """
        price = round(price, 2)
        size  = round(size,  2)
        # Verify implied USDC cost fits within 4dp; if not, trim size by 0.01
        implied_cost = round(size * price, 4)
        if round(implied_cost, 4) != implied_cost:
            size = round(size - 0.01, 2)
        return price, size

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
