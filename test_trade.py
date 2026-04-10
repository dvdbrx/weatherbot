"""
Simple $1 test trade: BUY → wait 30s → SELL
Uses Gamma API for market discovery, MarketOrderArgs for FOK execution.
"""
import os
import json
import time
import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, MarketOrderArgs, OrderType,
    BalanceAllowanceParams, AssetType
)
from py_clob_client.order_builder.constants import BUY, SELL

load_dotenv()

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
TRADE_AMOUNT = 1.0  # $1 test

def find_liquid_market(client):
    """Find a liquid market using Gamma API (sorted by 24h volume)."""
    print("🔍 Finding a liquid market via Gamma API...")
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": "true", "closed": "false", "limit": 100,
                    "order": "volume24hr", "ascending": "false"},
            timeout=15
        )
        markets = r.json()
    except Exception as e:
        print(f"  Gamma API error: {e}")
        return None
    
    print(f"  Got {len(markets)} markets, scanning orderbooks...")
    
    for m in markets:
        question = m.get("question", "?")
        neg_risk = str(m.get("negRisk", "false")).lower() == "true"
        clob_ids_raw = m.get("clobTokenIds", "[]")
        
        try:
            token_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
        except json.JSONDecodeError:
            continue
        
        if not token_ids:
            continue
        
        # Try each outcome token (YES first)
        for i, tid in enumerate(token_ids[:2]):
            try:
                book = client.get_order_book(tid)
                if not book or not book.bids or not book.asks:
                    continue
                
                best_bid = float(book.bids[0].price)
                best_ask = float(book.asks[0].price)
                spread = best_ask - best_bid
                
                # Want mid-range price with reasonable spread
                if 0.05 < best_bid < 0.95 and spread < 0.15:
                    outcomes_raw = m.get("outcomes", '["Yes", "No"]')
                    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                    outcome = outcomes[i] if i < len(outcomes) else f"Token{i}"
                    
                    return {
                        "question": question,
                        "token_id": tid,
                        "outcome": outcome,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "spread": spread,
                        "neg_risk": neg_risk,
                        "condition_id": m.get("conditionId", ""),
                    }
            except Exception:
                continue
    
    return None

def main():
    pk = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not pk:
        print("❌ POLYMARKET_PRIVATE_KEY not set")
        return
    
    # Initialize client (EOA, signature_type=0 default)
    client = ClobClient(HOST, key=pk, chain_id=CHAIN_ID)
    creds = client.create_or_derive_api_creds()
    if isinstance(creds, dict):
        creds = ApiCreds(**creds)
    client.set_api_creds(creds)
    
    addr = client.get_address()
    print(f"✅ Wallet: {addr}")
    
    # Check balance
    bal_res = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    balance = float(bal_res.get("balance", 0)) / 1e6
    print(f"💰 Balance: ${balance:.2f}")
    
    # Check allowances
    allowances = bal_res.get("allowances", {})
    all_approved = all(int(v) > 0 for v in allowances.values()) if allowances else False
    print(f"🔑 Allowances: {'✅ All set' if all_approved else '❌ MISSING — run set_allowances.py first!'}")
    
    if not all_approved:
        print("  You need to set token allowances before trading with an EOA wallet.")
        print("  See: https://gist.github.com/poly-rodr/44313920481de58d5a3f6d1f8226bd5e")
        return
    
    if balance < TRADE_AMOUNT + 0.50:
        print(f"❌ Need at least ${TRADE_AMOUNT + 0.50:.2f} (${TRADE_AMOUNT} + buffer)")
        return
    
    # Find market
    market = find_liquid_market(client)
    if not market:
        print("❌ No suitable liquid market found")
        return
    
    print(f"\n📊 Selected Market:")
    print(f"   Question:  {market['question'][:80]}")
    print(f"   Outcome:   {market['outcome']}")
    print(f"   Best Bid:  {market['best_bid']:.2f}")
    print(f"   Best Ask:  {market['best_ask']:.2f}")
    print(f"   Spread:    {market['spread']:.3f}")
    print(f"   Neg Risk:  {market['neg_risk']}")
    
    # Calculate: use a limit order at the ask price to get filled
    buy_price = market['best_ask']
    buy_size = round(TRADE_AMOUNT / buy_price, 1)
    if buy_size < 1:
        buy_size = 1.0
    cost = buy_price * buy_size
    
    print(f"\n🛒 BUY ORDER (GTC limit at ask):")
    print(f"   Price: ${buy_price:.2f}/share")
    print(f"   Size:  {buy_size} shares")
    print(f"   Cost:  ~${cost:.2f}")
    
    # Confirm
    print(f"\n⚠️  This will spend ~${cost:.2f} of real USDC!")
    confirm = input("   Type 'yes' to proceed: ").strip().lower()
    if confirm != 'yes':
        print("❌ Cancelled.")
        return
    
    # === PLACE BUY ORDER ===
    print(f"\n📤 Placing BUY order...")
    try:
        order_args = OrderArgs(
            token_id=market['token_id'],
            price=buy_price,
            size=buy_size,
            side=BUY,
        )
        signed_order = client.create_order(order_args)
        buy_resp = client.post_order(signed_order, OrderType.GTC)
        print(f"   ✅ BUY response: {buy_resp}")
    except Exception as e:
        print(f"   ❌ BUY failed: {e}")
        return
    
    # === WAIT 30 SECONDS ===
    print(f"\n⏳ Waiting 30 seconds...")
    for i in range(30, 0, -1):
        print(f"   {i}s remaining...", end='\r')
        time.sleep(1)
    print(f"   Done waiting!       ")
    
    # === PLACE SELL ORDER ===
    print(f"\n📊 Re-fetching orderbook for sell price...")
    try:
        book = client.get_order_book(market['token_id'])
        if book and book.bids:
            sell_price = float(book.bids[0].price)
        else:
            sell_price = market['best_bid']
        print(f"   Current best bid: ${sell_price:.2f}")
    except Exception:
        sell_price = market['best_bid']
    
    print(f"\n📤 Placing SELL order...")
    print(f"   Price: ${sell_price:.2f}/share, Size: {buy_size} shares")
    try:
        sell_args = OrderArgs(
            token_id=market['token_id'],
            price=sell_price,
            size=buy_size,
            side=SELL,
        )
        signed_sell = client.create_order(sell_args)
        sell_resp = client.post_order(signed_sell, OrderType.GTC)
        print(f"   ✅ SELL response: {sell_resp}")
    except Exception as e:
        print(f"   ❌ SELL failed: {e}")
        print(f"   (You may have shares to sell manually on polymarket.com)")
    
    # === FINAL BALANCE ===
    print(f"\n💰 Final balance check...")
    bal_res = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    final_balance = float(bal_res.get("balance", 0)) / 1e6
    print(f"   Starting: ${balance:.2f}")
    print(f"   Final:    ${final_balance:.2f}")
    print(f"   P&L:      ${final_balance - balance:+.2f}")
    
    print(f"\n🏁 Test trade complete!")

if __name__ == "__main__":
    main()
