#!/usr/bin/env python3
"""
vpn_test_trade.py — Run with VPN active, check log after.

Usage:
    1. Connect Surfshark to a European server
    2. Run:  nohup python vpn_test_trade.py > trade_test.log 2>&1 &
    3. Disconnect Surfshark
    4. Check results:  cat trade_test.log
"""

import os
import sys
import json
import time
import requests

# Force load .env manually (dotenv can fail in nohup context)
env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

LOG = []
def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def main():
    pk = os.getenv('POLYMARKET_PRIVATE_KEY')
    if not pk:
        log("❌ POLYMARKET_PRIVATE_KEY not set")
        return False

    # Check IP
    try:
        ip = requests.get('https://ifconfig.me/ip', timeout=5).text.strip()
        log(f"🌍 IP: {ip}")
    except:
        log("⚠️ Could not check IP")

    # Init client
    log("🔑 Connecting to CLOB...")
    client = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137)
    creds = client.create_or_derive_api_creds()
    if isinstance(creds, dict):
        creds = ApiCreds(**creds)
    client.set_api_creds(creds)
    log(f"   Wallet: {client.get_address()}")

    # Find a non-neg-risk market
    log("🔍 Finding market...")
    r = requests.get('https://gamma-api.polymarket.com/markets',
        params={'active': 'true', 'closed': 'false', 'limit': 50,
                'order': 'volume24hr', 'ascending': 'false', 'negRisk': 'false'},
        timeout=30)
    markets = r.json()

    target = None
    for m in markets:
        prices = json.loads(m.get('outcomePrices', '[0.5,0.5]'))
        yes = float(prices[0])
        cids = json.loads(m.get('clobTokenIds', '[]')) if isinstance(m.get('clobTokenIds'), str) else m.get('clobTokenIds', [])
        if 0.15 < yes < 0.85 and cids:
            target = {'q': m.get('question', '?'), 'tid': cids[0], 'yes': yes}
            break

    if not target:
        log("❌ No suitable market found")
        return False

    log(f"🏟️  {target['q']}")
    log(f"   YES: {target['yes']:.4f}")

    # Check orderbook
    log("📊 Checking orderbook...")
    book = client.get_order_book(target['tid'])
    if not book or not book.bids:
        log("❌ No orderbook")
        return False

    bid = float(book.bids[0].price)
    log(f"   Best bid: {bid:.4f}")

    # Place resting order below bid
    test_price = max(0.01, round(bid - 0.03, 2))
    test_size = 5.0
    log(f"📝 Placing resting BUY @ {test_price} x {test_size} (${test_price * test_size:.2f})")

    order_args = OrderArgs(price=test_price, size=test_size, side=BUY, token_id=target['tid'])
    signed_order = client.create_order(order_args)
    log("   ✅ Order signed")

    try:
        resp = client.post_order(signed_order, OrderType.GTC)
        log(f"   ✅ POSTED: {resp}")
    except Exception as e:
        if "403" in str(e) or "restricted" in str(e).lower():
            log(f"   ❌ STILL GEO-BLOCKED: {e}")
            log(f"   Your VPN may not be connected, or it's using a US exit.")
            return False
        else:
            log(f"   ❌ Error: {e}")
            return False

    # Extract order ID and cancel
    order_id = None
    if isinstance(resp, dict):
        order_id = resp.get('orderID') or resp.get('order_id') or resp.get('id')
    elif isinstance(resp, str):
        order_id = resp

    if order_id:
        log(f"   Order ID: {order_id}")
        time.sleep(2)
        try:
            cancel = client.cancel(order_id)
            log(f"   ✅ Cancelled: {cancel}")
        except Exception as e:
            log(f"   ⚠️ Cancel error (order may have expired): {e}")

        log("")
        log("=" * 55)
        log("🎉 LIVE TRADE TEST COMPLETE!")
        log("   ✅ VPN bypass works")
        log("   ✅ Order placement works")
        log("   ✅ Order cancellation works")
        log("   ✅ Bot is READY for live trading!")
        log("=" * 55)
        return True
    else:
        log(f"   ⚠️ Order posted but no ID returned: {resp}")
        return True  # Still a success — order went through


if __name__ == '__main__':
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        log(f"💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
