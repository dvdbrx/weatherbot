"""
polymarket_client.py — Live trading client for Polymarket CLOB.
Wraps py-clob-client for order execution, balance checks, and order management.
"""

import os
import requests
from datetime import datetime, timezone
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, BalanceAllowanceParams, AssetType

from config import POLYGON_RPC_URL, USDC_E_CONTRACT


class PolymarketLiveClient:
    def __init__(self, private_key: str | None = None, host: str = "https://clob.polymarket.com", chain_id: int = 137):
        self.private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("Private key missing for Polymarket live client")

        self.client = ClobClient(host, key=self.private_key, chain_id=chain_id)

        creds = self.client.create_or_derive_api_creds()
        if isinstance(creds, dict):
            creds = ApiCreds(**creds)
        self.client.set_api_creds(creds)

        self.trading_address = self.client.get_address()
        self.collateral_address = self.client.get_collateral_address()

    def get_usdc_balance(self) -> float:
        """Returns the USDC collateral balance available for trading.
        Uses the CLOB API authenticated balance endpoint (primary),
        with on-chain RPC fallback.
        """
        # Method 1: CLOB API balance/allowance endpoint (authenticated)
        try:
            res = self.client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            if "balance" in res:
                bal = float(res["balance"]) / 1e6
                return bal
        except Exception:
            pass

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
        except Exception:
            pass

        return 0.0

    def get_open_positions(self) -> list:
        """Returns a list of open and pending orders."""
        try:
            return self.client.get_orders()
        except Exception as e:
            print(f"Error fetching orders: {e}")
            return []

    def place_order(self, token_id: str, side: str, price: float, size: float) -> dict | None:
        """Places a Limit order for the specified token.
        side: 'BUY' or 'SELL'
        price: 0.0 to 1.0 (limit price)
        size: number of shares
        """
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side
            )
            order = self.client.create_order(order_args)
            resp = self.client.post_order(order, OrderType.GTC)
            return resp
        except Exception as e:
            print(f"Error placing {side} order: {e}")
            return None

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
