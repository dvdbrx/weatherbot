import os
from dotenv import load_dotenv
from polymarket_client import PolymarketLiveClient

load_dotenv()

def main():
    if not os.getenv("POLYMARKET_PRIVATE_KEY"):
        print("Error: POLYMARKET_PRIVATE_KEY missing from .env")
        return

    print("Connecting to Polymarket...\n")
    client = PolymarketLiveClient()
    print(f"✅ Wallet Address: {client.trading_address}\n")

    print("--- Balance Check ---")
    balance = client.get_usdc_balance()
    print(f"  CLOB Exchange Balance: ${balance:.2f}")

    print(f"\n--- Summary ---")
    print(f"  Available for trading: ${balance:.2f}")

if __name__ == "__main__":
    main()
