#!/usr/bin/env python3
"""
verify_network.py — Pre-flight network verification for WeatherBot on Raspberry Pi
===================================================================================
Run this script ON the Raspberry Pi AFTER setting up Surfshark + routing bypasses.
It verifies that:
  1. The local LAN subnet route bypasses the VPN (SSH will survive)
  2. The Tailscale subnet route bypasses the VPN (Tailscale SSH will survive)
  3. The public IP is non-US (Polymarket won't 403)
  4. Polymarket CLOB API is actually reachable
  5. No broken proxy env vars are set (would confuse requests)

Usage:
  python verify_network.py

Ideal output: all checks GREEN ✅
"""

import os
import re
import socket
import subprocess
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────
# Config — adjust if your network is different
# ─────────────────────────────────────────────────────
LOCAL_GATEWAY = "192.168.1.1"       # Your router's IP (from `ip route | grep default`)
LOCAL_IFACE   = "eth0"              # Your Pi's main network interface
TAILSCALE_SUBNET = "100.64.0.0/10"  # Tailscale CGNAT range (never changes)
TAILSCALE_IFACE  = "tailscale0"

POLYMARKET_CLOB = "https://clob.polymarket.com"
IP_CHECK_URL    = "https://ifconfig.me/ip"
COUNTRY_CHECK_URL = "https://ipinfo.io/json"

BLOCKED_COUNTRIES = {"US"}

# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────
PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"

results: list[tuple[str, str, str]] = []   # (check_name, status, detail)

def check(name: str, passed: bool, detail: str, warn_only: bool = False) -> bool:
    status = PASS if passed else (WARN if warn_only else FAIL)
    results.append((name, status, detail))
    symbol = "✅" if passed else ("⚠️ " if warn_only else "❌")
    print(f"  {symbol}  {name}: {detail}")
    return passed


def run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    """Run a shell command, return (returncode, stdout+stderr combined)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except FileNotFoundError:
        return 1, f"command not found: {cmd[0]}"


def get_routing_table() -> str:
    _, out = run(["ip", "route", "show"])
    return out


# ─────────────────────────────────────────────────────
# CHECK 1 — No broken proxy env vars
# ─────────────────────────────────────────────────────
def check_no_proxy_env() -> None:
    print("\n[1/5] Proxy environment variables")
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        val = os.environ.get(var)
        if val:
            check(f"Env var {var}", False,
                  f"is set to '{val}' — this will override VPN routing for requests!")
        else:
            check(f"Env var {var}", True, "not set (good)")


# ─────────────────────────────────────────────────────
# CHECK 2 — LAN subnet bypass route exists
# ─────────────────────────────────────────────────────
def check_lan_route() -> None:
    print("\n[2/5] Local LAN routing bypass")
    table = get_routing_table()

    # Find the line containing our gateway to verify the route exists via the physical iface
    # We look for the subnet route explicitly pointing to LOCAL_IFACE
    route_lines = [l for l in table.splitlines() if LOCAL_IFACE in l and "via" in l]
    has_lan_route = any(LOCAL_GATEWAY in l or "192.168" in l for l in route_lines)

    check("LAN route via physical interface", has_lan_route,
          f"found route through {LOCAL_IFACE}" if has_lan_route
          else f"NO static route for 192.168.0.0 via {LOCAL_IFACE} found — SSH may drop!")

    # Try pinging the gateway (proves the physical path is alive)
    rc, _ = run(["ping", "-c", "2", "-W", "2", LOCAL_GATEWAY])
    check("Ping local gateway", rc == 0,
          f"{LOCAL_GATEWAY} reachable" if rc == 0 else f"{LOCAL_GATEWAY} unreachable")


# ─────────────────────────────────────────────────────
# CHECK 3 — Tailscale bypass route exists
# ─────────────────────────────────────────────────────
def check_tailscale_route() -> None:
    print("\n[3/5] Tailscale routing bypass")
    table = get_routing_table()

    has_ts_route = TAILSCALE_IFACE in table and "100.64" in table
    check("Tailscale route exists", has_ts_route,
          f"100.64.0.0/10 → {TAILSCALE_IFACE} found" if has_ts_route
          else f"no 100.64.0.0/10 route via {TAILSCALE_IFACE} — Tailscale SSH will break!",
          warn_only=not has_ts_route)

    # Get Tailscale IP and ping it
    rc, ts_ip = run(["tailscale", "ip", "-4"])
    if rc != 0 or not ts_ip:
        check("Tailscale connected", False,
              "tailscale not installed or not connected — run: sudo tailscale up",
              warn_only=True)
        return

    ts_ip = ts_ip.strip()
    check("Tailscale IP obtained", True, ts_ip)

    rc_ping, _ = run(["ping", "-c", "2", "-W", "2", ts_ip])
    check("Ping own Tailscale IP", rc_ping == 0,
          f"{ts_ip} reachable via tailscale0" if rc_ping == 0
          else f"{ts_ip} unreachable — route may not be active",
          warn_only=rc_ping != 0)


# ─────────────────────────────────────────────────────
# CHECK 4 — Public IP is outside the US
# ─────────────────────────────────────────────────────
def check_public_ip() -> None:
    print("\n[4/5] Public IP geolocation (must NOT be US)")
    try:
        r = requests.get(COUNTRY_CHECK_URL, timeout=8)
        data = r.json()
        ip      = data.get("ip", "unknown")
        country = data.get("country", "unknown")
        city    = data.get("city", "")
        org     = data.get("org", "")
        is_non_us = country not in BLOCKED_COUNTRIES

        check("Public IP country", is_non_us,
              f"{ip} → {city}, {country}  ({org})" if is_non_us
              else f"{ip} → {country} (US!) — Polymarket will 403. Enable VPN first!")
    except requests.RequestException as e:
        check("Public IP lookup", False, f"request failed: {e}")


# ─────────────────────────────────────────────────────
# CHECK 5 — Polymarket CLOB API reachable
# ─────────────────────────────────────────────────────
def check_polymarket() -> None:
    print("\n[5/5] Polymarket CLOB API reachability")
    try:
        r = requests.get(f"{POLYMARKET_CLOB}/markets?limit=1", timeout=10)
        if r.status_code == 200:
            check("Polymarket CLOB /markets", True,
                  f"HTTP 200 — API reachable, {len(r.json().get('data', []))} market(s) returned")
        elif r.status_code == 403:
            check("Polymarket CLOB /markets", False,
                  "HTTP 403 — geo-blocked! VPN is not active or IP is still US-routed")
        else:
            check("Polymarket CLOB /markets", False,
                  f"Unexpected HTTP {r.status_code} — {r.text[:80]}")
    except requests.RequestException as e:
        check("Polymarket CLOB /markets", False, f"connection error: {e}")


# ─────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────
def print_summary() -> None:
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    passed  = [r for r in results if r[1] == PASS]
    warned  = [r for r in results if r[1] == WARN]
    failed  = [r for r in results if r[1] == FAIL]

    print(f"  Total checks : {len(results)}")
    print(f"  ✅ Passed    : {len(passed)}")
    print(f"  ⚠️  Warnings  : {len(warned)}")
    print(f"  ❌ Failed    : {len(failed)}")
    print()

    if failed:
        print("  ACTION REQUIRED — failing checks:")
        for name, _, detail in failed:
            print(f"    ❌  {name}")
            print(f"           {detail}")
        print()
        print("  See VPN_SETUP.md for fix instructions.")
    elif warned:
        print("  Warnings detected (non-blocking):")
        for name, _, detail in warned:
            print(f"    ⚠️   {name}")
            print(f"           {detail}")
        print()
        print("  ✅ Core routing is healthy — bot can run with warnings above noted.")
    else:
        print("  🎉 ALL CHECKS PASSED — routing is correctly configured!")
        print("     SSH is safe, Tailscale is safe, Polymarket is reachable.")
        print("     You can safely run: python bot_v2.py")

    print("=" * 60)
    sys.exit(0 if not failed else 1)


# ─────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  WeatherBot — Network Routing Verification")
    print("  Run this on your Raspberry Pi after VPN setup.")
    print("=" * 60)

    check_no_proxy_env()
    check_lan_route()
    check_tailscale_route()
    check_public_ip()
    check_polymarket()
    print_summary()
