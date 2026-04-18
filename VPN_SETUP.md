# 🛡️ Raspberry Pi — Surfshark VPN + SSH Access Guide

## How this works (understanding the problem first)

When Surfshark connects on the Pi, it **adds a new default route** through the VPN tunnel (`tun0` or `wg0`). This means **all** internet traffic — including SSH return packets and Tailscale mesh traffic — gets routed through the VPN and never makes it back correctly. **Your SSH session drops, and Tailscale becomes unreachable.**

The fix: before the VPN connects, we tell Linux to **pin specific subnets to specific interfaces**, so the VPN can never override them:
- **Local LAN subnet** (e.g., `192.168.1.0/24`) → always via `eth0`/`wlan0` → protects direct SSH
- **Tailscale subnet** (`100.64.0.0/10`) → always via `tailscale0` → protects Tailscale SSH

> [!IMPORTANT]
> **`verify_network.py` must be run ON the Raspberry Pi — not on your laptop.**
> The routing table and interfaces it checks are the Pi's, not your local machine's.

---

## ⚠️ What if I get locked out?

If you connect the VPN before the bypass routes are in place, your SSH drops and you cannot recover remotely. Your options:

| Recovery method | Requires |
|---|---|
| **Tailscale** (preferred) | Tailscale set up on Pi *before* Surfshark |
| **Scheduled reboot** | Run `sudo shutdown -r +5` before VPN connect |
| **Physical access** | Walk up to the Pi and power-cycle it |

**This is why Tailscale must be set up FIRST — it's your recovery parachute.**

---

## Correct order of operations

```
Step 0: Install & connect Tailscale (backup SSH path)
Step 1: Gather Pi network info
Step 2: Apply routing bypass rules (LAN + Tailscale)
Step 3: Install Surfshark
Step 4: Set the dead man's switch timer
Step 5: Connect Surfshark → verify SSH is still alive → cancel timer
Step 6: Run verify_network.py on the Pi
Step 7: Start the bot with tmux
Step 8: Set up autostart on crontab
```

---

## Step 0 — Install Tailscale FIRST (your recovery SSH path)

> [!IMPORTANT]
> Do this **before touching Surfshark at all**. If you get locked out, you can
> SSH back in via Tailscale's `100.x.y.z` IP from any network, anywhere.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Tailscale will print your Pi's Tailscale IP (e.g., `100.94.12.34`). **Write it down.**
Test it from your laptop now: `ssh pi@100.94.12.34`

---

## Step 1 — Gather your Pi's network info

SSH into your Pi and run:

```bash
ip route | grep default
```

Example output:
```
default via 192.168.1.1 dev eth0 proto dhcp src 192.168.1.147 metric 100
```

Write down:
- **Gateway IP** → e.g., `192.168.1.1` (your router)
- **Interface** → e.g., `eth0` (wired) or `wlan0` (wifi)
- **Your Pi's LAN IP** → e.g., `192.168.1.147`

Also find your **subnet**:
```bash
ip -o -f inet addr show eth0 | awk '{print $4}'
```
Example output: `192.168.1.147/24` → your subnet is `192.168.1.0/24`

---

## Step 2 — Apply bypass routes (LAN + Tailscale)

Run this **before** installing or connecting Surfshark.

```bash
# ⚠️ EDIT to match your network (from Step 1):
GATEWAY="192.168.1.1"        # Your router's IP
IFACE="eth0"                 # Your Pi's network interface (eth0 or wlan0)
SUBNET="192.168.1.0/24"      # Your local network subnet

# --- Local LAN bypass (protects direct SSH) ---
sudo ip route add "$SUBNET" via "$GATEWAY" dev "$IFACE"
echo "up ip route add $SUBNET via $GATEWAY dev $IFACE" | sudo tee -a /etc/network/interfaces.d/lan-bypass.conf

# --- Tailscale bypass (protects Tailscale SSH) ---
sudo ip route add 100.64.0.0/10 dev tailscale0
echo "up ip route add 100.64.0.0/10 dev tailscale0" | sudo tee -a /etc/network/interfaces.d/lan-bypass.conf
```

Verify the routes are live:
```bash
ip route show | grep "192.168"    # → eth0/wlan0
ip route show | grep tailscale0   # → 100.64.0.0/10 dev tailscale0
ping -c 2 192.168.1.1             # router still reachable
ping -c 2 $(tailscale ip -4)      # Tailscale still reachable
```

---

## Step 3 — Install Surfshark

```bash
curl -f https://downloads.surfshark.com/linux/debian-install.sh --output surfshark-install.sh
sudo sh surfshark-install.sh
surfshark-vpn login
```

---

## Step 4 — Set the dead man's switch (safety net)

Before connecting the VPN, schedule an auto-reboot 5 minutes from now.
If anything goes wrong and you get locked out, the Pi will reboot on its own, drop the VPN, and both SSH paths will come back.

```bash
sudo shutdown -r +5 "VPN safety reboot: cancel with: sudo shutdown -c"
```

> [!CAUTION]
> Don't forget to cancel the timer after confirming everything works (Step 5)!

---

## Step 5 — Connect the VPN

```bash
surfshark-vpn connect "de"    # Germany — reliable, non-US
```

Your SSH session **should stay connected**. Verify immediately:

```bash
# 1. This terminal is still responding — that's proof #1

# 2. Public IP is now in Europe:
curl https://ifconfig.me/ip

# 3. Local LAN still reachable:
ping -c 2 192.168.1.1

# 4. Tailscale still reachable:
ping -c 2 $(tailscale ip -4)
```

All pass? **Cancel the safety reboot:**
```bash
sudo shutdown -c
echo "✅ VPN + routing working — safety timer cancelled."
```

If `403` from Polymarket, the VPN exit node's IP may be blocked. Try different servers:
```bash
surfshark-vpn connect "nl"    # Netherlands
surfshark-vpn connect "gb"    # UK
```

---

## Step 6 — Run verify_network.py on the Pi

Now that the code is on the Pi (via `git pull`), run the verification script:

```bash
cd ~/weatherbot
source .venv/bin/activate
python verify_network.py
```

All green? You're clear to run the bot.

---

## Step 7 — Run the bot 24/7 via tmux

```bash
sudo apt install -y tmux        # if not already installed

tmux new -s weatherbot
cd ~/weatherbot
source .venv/bin/activate
python bot_v2.py

# Detach (bot keeps running after you close your laptop):
# Press Ctrl+B then D
```

**To reattach later:**
```bash
tmux attach -t weatherbot
```

---

## Step 8 — Auto-start on reboot

```bash
crontab -e
```

Add at the bottom:
```cron
# Wait for network, then connect VPN, then start bot
@reboot sleep 20 && surfshark-vpn connect "de"
@reboot sleep 40 && ip route add 192.168.1.0/24 via 192.168.1.1 dev eth0
@reboot sleep 40 && ip route add 100.64.0.0/10 dev tailscale0
@reboot sleep 60 && tmux new-session -d -s weatherbot 'cd /home/pi/weatherbot && source .venv/bin/activate && python bot_v2.py'
```

> [!NOTE]
> The routing bypass lines are duplicated in crontab as a belt-and-suspenders fallback.
> `interfaces.d/lan-bypass.conf` should apply them first at boot, but crontab
> ensures they're set before the bot starts even if the timing is off.

---

## Troubleshooting: Locked out with no physical access

| Situation | Fix |
|---|---|
| Direct SSH dropped, Tailscale alive | `ssh pi@100.x.y.z` → run `surfshark-vpn disconnect` → re-apply routes → reconnect VPN |
| Both SSH and Tailscale dropped | Wait for the dead man's switch reboot (+5 min) → SSH back in → re-apply routes |
| Didn't set dead man's switch | Go physically reboot the Pi — lesson learned! |
| On reboot, VPN reconnects before routes | Fix the `@reboot` crontab ordering (routes before VPN connect) |

---

## Summary

| Traffic Type | Routes Through | Why |
|---|---|---|
| Direct SSH (same LAN) | `eth0` / `wlan0` | Static LAN subnet rule |
| Tailscale SSH (any network) | `tailscale0` | Static `100.64.0.0/10` rule |
| Polymarket API calls | VPN tunnel | Default VPN route |
| All other internet | VPN tunnel | Default VPN route |
