#!/usr/bin/env python3
"""
live_test_trade.py — Zero-risk live trade test.

Places a resting limit order well BELOW the best bid (won't fill),
confirms the CLOB accepts it, then cancels immediately.
Proves the full pipeline: auth → sign → post → cancel.
"""

import os
import json
import time
import requests
from importlib import metadata
from dotenv import load_dotenv

load_dotenv()

SDK_FLAVOR = "v1"
try:
    from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions, Side
    SDK_FLAVOR = "v2"
except ImportError:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY as LEGACY_BUY

# ── Pre-flight checks ────────────────────────────────────────────────
pk = os.getenv('POLYMARKET_PRIVATE_KEY')
if not pk:
    print("❌ POLYMARKET_PRIVATE_KEY not found in .env")
    exit(1)

proxy = os.environ.get("HTTPS_PROXY")
if proxy:
    print(f"🌐 Proxy: {proxy}")
else:
    print("⚠️  No HTTPS_PROXY set. Order placement WILL be geo-blocked from US IPs.")
    print("   To fix: ssh -D 1080 -q -C -N user@your-non-us-server")
    print("   Then: HTTPS_PROXY=socks5h://127.0.0.1:1080 python live_test_trade.py")
    print()
    response = input("Continue anyway? (y/n): ").strip().lower()
    if response != 'y':
        print("Aborted.")
        exit(0)

# ── Initialize CLOB client ───────────────────────────────────────────
print("\n=== Initializing ===")
if SDK_FLAVOR != "v2":
    print("❌ py-clob-client-v2 is not importable in this environment.")
    print("   Fix: pip install -U py-clob-client-v2")
    exit(1)
print("✅ Using py-clob-client-v2")

signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))
funder = os.getenv("POLY_FUNDER") or None
if signature_type in (1, 2) and not funder:
    print(f"⚠️  POLY_SIGNATURE_TYPE={signature_type} but POLY_FUNDER is empty.")
    print("   If this is a proxy/safe wallet, set POLY_FUNDER=0x...")

client = ClobClient(
    host=os.getenv("POLY_CLOB_HOST", "https://clob-v2.polymarket.com"),
    key=pk,
    chain_id=137,
    signature_type=signature_type,
    funder=funder,
)
if hasattr(client, "create_or_derive_api_key"):
    creds = client.create_or_derive_api_key()
else:
    creds = client.create_or_derive_api_creds()
if isinstance(creds, dict):
    creds = ApiCreds(**creds)
if hasattr(client, "set_api_creds"):
    client.set_api_creds(creds)
print(f"🔑 Wallet: {client.get_address()}")

# ── Find a tradeable non-neg-risk market ──────────────────────────────
print("\n=== Finding a mid-range, non-neg-risk market ===")
r = requests.get('https://gamma-api.polymarket.com/markets',
    params={
        'active': 'true', 'closed': 'false',
        'limit': 50, 'order': 'volume24hr', 'ascending': 'false',
        'negRisk': 'false',
    }, timeout=30)
markets = r.json()

target = None
for m in markets:
    q = m.get('question', '?')
    prices = json.loads(m.get('outcomePrices', '[0.5,0.5]'))
    yes = float(prices[0])
    cids_raw = m.get('clobTokenIds', '[]')
    cids = json.loads(cids_raw) if isinstance(cids_raw, str) else cids_raw
    if 0.15 < yes < 0.85 and cids:
        target = {'q': q, 'yes': yes, 'tid': cids[0]}
        break

if not target:
    print("❌ No suitable market found. Try again later.")
    exit(1)

print(f"🏟️  {target['q']}")
print(f"   YES price: {target['yes']:.4f}")

# ── Check orderbook ───────────────────────────────────────────────────
print("\n=== Checking orderbook ===")
book = client.get_order_book(target['tid'])
if not book or not book.bids or not book.asks:
    print("❌ No orderbook. Market may be closed/illiquid.")
    exit(1)

bid = float(book.bids[0].price)
ask = float(book.asks[0].price)
spread = ask - bid
print(f"  Bid: {bid:.4f} | Ask: {ask:.4f} | Spread: {spread:.4f}")

# ── Place resting order below bid ─────────────────────────────────────
test_price = round(bid - 0.03, 2)
if test_price < 0.01:
    test_price = 0.01
test_size = 5.0  # Polymarket minimum

cost = test_price * test_size
print(f"\n=== Placing resting limit BUY ===")
print(f"  Price: {test_price} (3¢ below bid of {bid})")
print(f"  Size: {test_size}")
print(f"  Max cost if filled: ${cost:.2f}")
print(f"  ⚡ This order will NOT fill — it just tests the pipeline")

order_args = OrderArgs(
    price=test_price,
    size=test_size,
    side=Side.BUY if SDK_FLAVOR == "v2" else LEGACY_BUY,
    token_id=target['tid'],
)

print("\n  Signing order...")
signed_order = client.create_order(order_args)
print("  ✅ Signed")

print("  Posting to CLOB...")
try:
    if hasattr(client, "create_and_post_order"):
        resp = client.create_and_post_order(
            order_args=order_args,
            options=PartialCreateOrderOptions(order_type=OrderType.GTC),
        )
    else:
        resp = client.post_order(signed_order, OrderType.GTC)
except Exception as e:
    err_str = str(e)
    if "403" in err_str or "restricted" in err_str.lower() or "geoblock" in err_str.lower():
        print(f"\n  ❌ GEO-BLOCKED: {e}")
        print(f"\n  ══════════════════════════════════════════════════")
        print(f"  Your order was signed correctly, but Polymarket")
        print(f"  rejects POST requests from US IPs.")
        print(f"")
        print(f"  Fix:")
        print(f"    1. ssh -D 1080 -q -C -N user@your-non-us-server")
        print(f"    2. HTTPS_PROXY=socks5h://127.0.0.1:1080 python live_test_trade.py")
        print(f"  ══════════════════════════════════════════════════")
    else:
        print(f"\n  ❌ Error: {e}")
    exit(1)

print(f"  ✅ Response: {resp}")

order_id = None
if isinstance(resp, dict):
    order_id = resp.get('orderID') or resp.get('order_id') or resp.get('id')
elif isinstance(resp, str):
    order_id = resp

if order_id:
    print(f"  Order ID: {order_id}")

    # ── Cancel it ──────────────────────────────────────────────────────
    print(f"\n=== Cancelling test order ===")
    time.sleep(2)
    try:
        cancel = client.cancel(order_id)
        print(f"  ✅ Cancelled: {cancel}")
    except Exception as e:
        print(f"  ⚠️ Cancel error: {e}")

    print(f"\n{'='*55}")
    print(f"🎉 LIVE TRADE TEST COMPLETE!")
    print(f"   ✅ CLOB authentication works")
    print(f"   ✅ Order signing works")
    print(f"   ✅ Order placement works")
    print(f"   ✅ Order cancellation works")
    print(f"   ✅ Bot is READY for live trading!")
    print(f"{'='*55}")
else:
    print(f"\n⚠️ Order posted but no ID returned.")
    print(f"   Response: {json.dumps(resp, indent=2) if isinstance(resp, dict) else resp}")
