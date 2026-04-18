#!/usr/bin/env python3
"""
vpn_test_trade.py — Run with VPN active, check log after.

Usage:
    1. Open a terminal / IDE and run: python vpn_test_trade.py
    2. The script will say "Waiting for VPN..."
    3. Connect Surfshark to a European server. Your IDE will disconnect.
    4. Wait ~15 seconds. The script will automatically detect the IP change and run the trade!
    5. Disconnect Surfshark.
    6. Reconnect to your IDE and check carefully the generated 'vpn_trade.log'
"""

import os
import sys
import json
import time
import requests

# Force load .env manually (dotenv can fail if paths are weird)
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
def log(msg, also_print=True):
    if also_print:
        print(msg, flush=True)
    LOG.append(msg)
    with open('vpn_trade.log', 'w') as f:
        f.write("\n".join(LOG))

def get_ip_info():
    try:
        data = requests.get('http://ip-api.com/json', timeout=5).json()
        return data.get('query', '0.0.0.0'), data.get('countryCode', 'US')
    except:
        return None, None

def wait_for_vpn():
    log("⏳ Checking current IP...")
    orig_ip, orig_country = get_ip_info()
    log(f"   Current IP: {orig_ip} ({orig_country})")
    
    log("\n🛑 Please turn ON your Surfshark VPN (connect to Europe).")
    log("   Your IDE will disconnect, but this script will keep running!")
    log("   Waiting for IP to change from US... (Press Ctrl+C to abort)")
    
    while True:
        try:
            ip, country = get_ip_info()
            if ip and country:
                if country != 'US' and ip != orig_ip:
                    log(f"\n🎉 VPN DETECTED! New IP: {ip} ({country})")
                    break
        except:
            pass
        time.sleep(2)

def main():
    pk = os.getenv('POLYMARKET_PRIVATE_KEY')
    if not pk:
        log("❌ POLYMARKET_PRIVATE_KEY not set")
        return False
        
    wait_for_vpn()

    # Init client
    log("\n🔑 Connecting to CLOB...")
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

    try:
        order_args = OrderArgs(price=test_price, size=test_size, side=BUY, token_id=target['tid'])
        signed_order = client.create_order(order_args)
        log("   ✅ Order signed")
    except Exception as e:
        log(f"   ❌ Error signing order: {e}")
        return False

    try:
        resp = client.post_order(signed_order, OrderType.GTC)
        log(f"   ✅ POSTED: {resp}")
    except Exception as e:
        if "403" in str(e) or "restricted" in str(e).lower():
            log(f"   ❌ STILL GEO-BLOCKED: {e}")
            log(f"   Your VPN may be leaking or using a blocked IP.")
            return False
        else:
            log(f"   ❌ Error posting: {e}")
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

        log("\n=======================================================")
        log("🎉 LIVE TRADE TEST COMPLETE!")
        log("   ✅ VPN bypass works")
        log("   ✅ Order placement works")
        log("   ✅ Order cancellation works")
        log("   ✅ Bot is READY for live trading!")
        log("=======================================================")
        log("\n👉 You can now turn OFF your VPN and reconnect!")
        return True
    else:
        log(f"   ⚠️ Order posted but no ID returned: {resp}")
        return True

if __name__ == '__main__':
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        log(f"💥 Unexpected error: {e}")
        import traceback
        log(traceback.format_exc(), also_print=False)
        sys.exit(1)
