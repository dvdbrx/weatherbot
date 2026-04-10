"""
test_balance.py — Quick balance check for USDC on Polygon.
"""
import requests
from config import POLYGON_RPC_URL, USDC_E_CONTRACT, USDC_NATIVE_CONTRACT


def get_erc20_balance(rpc_url: str, contract_address: str, wallet_address: str) -> float:
    data = "0x70a08231000000000000000000000000" + wallet_address[2:]
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": contract_address, "data": data}, "latest"],
        "id": 1
    }
    r = requests.post(rpc_url, json=payload).json()
    if 'result' in r and r['result'] != '0x':
        return int(r['result'], 16) / 1e6
    return 0


wallet = "0xBB30428Df2975E66c06c417129Dd4FD888Da152c"
bridged = get_erc20_balance(POLYGON_RPC_URL, USDC_E_CONTRACT, wallet)
native = get_erc20_balance(POLYGON_RPC_URL, USDC_NATIVE_CONTRACT, wallet)
print(f"Bridged USDC: {bridged}")
print(f"Native USDC: {native}")
