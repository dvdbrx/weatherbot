# 🌤 WeatherBet — Polymarket Weather Trading Bot

Automated weather market trading bot for Polymarket. Finds mispriced temperature outcomes using real forecast data from multiple sources across 20 cities worldwide.

No SDK. No black box. Pure Python.

---

## Project Structure

```
weatherbot/
├── bot_v2.py              # Main orchestrator — scan loop, trade execution
├── config.py              # All configuration, constants, locations
├── math_utils.py          # Pure math: Kelly, EV, bucket probability
├── http_utils.py          # HTTP client with retry + exponential backoff
├── forecasts.py           # Weather APIs: ECMWF, HRRR, METAR, Visual Crossing
├── polymarket_api.py      # Polymarket Gamma API: events, prices, resolution
├── storage.py             # Market/state/calibration persistence
├── positions.py           # Position lifecycle: stops, signals, forecast shifts
├── polymarket_client.py   # Live trading client (py-clob-client wrapper)
├── tui.py                 # Rich terminal dashboard
├── serve_dashboard.py     # HTTP server for web dashboard
├── sim_dashboard_repost.html  # Web dashboard UI
├── config.json            # Runtime parameters
├── requirements.txt       # Python dependencies
├── test_trade.py          # Manual $1 test trade script
├── test_balance.py        # Wallet balance checker
├── wallettest.py          # Client connection test
└── legacy/                # Archived: bot_v1.py, patch.py
```

---

## How It Works

Polymarket runs markets like "Will the highest temperature in Chicago be between 46–47°F on March 7?" These markets are often mispriced — the forecast says 78% likely but the market is trading at 8 cents.

The bot:
1. Fetches forecasts from ECMWF and HRRR via Open-Meteo (free, no key required)
2. Gets real-time observations from METAR airport stations
3. Finds the matching temperature bucket on Polymarket
4. Calculates Expected Value — only enters if the math is positive
5. Sizes the position using fractional Kelly Criterion
6. Monitors stops every 10 minutes, full scan every hour
7. Auto-resolves markets by querying Polymarket API directly

### Key Features

- **20 cities** across 4 continents (US, Europe, Asia, South America, Oceania)
- **3 forecast sources** — ECMWF (global), HRRR/GFS (US, hourly), METAR (real-time observations)
- **Expected Value** — skips trades where the math doesn't work
- **Kelly Criterion** — sizes positions based on edge strength
- **Stop-loss + trailing stop** — configurable stop, moves to breakeven after gain threshold
- **Slippage filter** — skips markets with wide spreads
- **Self-calibration** — learns forecast accuracy per city over time
- **Retry logic** — HTTP requests use exponential backoff
- **Full data storage** — every forecast snapshot, trade, and resolution saved to JSON

---

## Why Airport Coordinates Matter

Most bots use city center coordinates. That's wrong.

Every Polymarket weather market resolves on a specific airport station. NYC resolves on LaGuardia (KLGA), Dallas on Love Field (KDAL) — not DFW. The difference between city center and airport can be 3–8°F. On markets with 1–2°F buckets, that's the difference between the right trade and a guaranteed loss.

| City | Station | Airport |
|------|---------|---------|
| NYC | KLGA | LaGuardia |
| Chicago | KORD | O'Hare |
| Miami | KMIA | Miami Intl |
| Dallas | KDAL | Love Field |
| Seattle | KSEA | Sea-Tac |
| Atlanta | KATL | Hartsfield |
| London | EGLC | London City |
| Tokyo | RJTT | Haneda |
| ... | ... | ... |

---

## Installation
```bash
git clone https://github.com/alteregoeth-ai/weatherbot
cd weatherbot
pip install -r requirements.txt
```

Create a `.env` file:
```
VISUAL_CROSSING_KEY=your_key_here
# For live trading:
# POLYMARKET_PRIVATE_KEY=0x...
```

Configure trading parameters in `config.json`:
```json
{
  "balance": 10000.0,
  "max_bet": 5.0,
  "min_ev": 0.15,
  "max_price": 0.50,
  "min_volume": 1000,
  "kelly_fraction": 0.15,
  "live_trading_enabled": false
}
```

Get a free Visual Crossing API key at visualcrossing.com — used to fetch actual temperatures after market resolution.

---

## 🏗 Architecture & Monitoring

This system is incredibly lightweight. It is designed to be run completely headless 24/7 on a **Raspberry Pi**, while you securely monitor its performance from your **Laptop** (or phone) anywhere in the world.

### 1. The Core Engine (Raspberry Pi)
The Pi runs the trading engine 24/7. It writes all live trading data and balances securely to a local `data/` folder. Using `rclone`, this folder is continuously synced to a private Google Drive folder (e.g., `WeatherBotData`).

**To start the bot on the Pi (Background Mode):**
```bash
ssh bob@<your-rpi-ip>
tmux new -s weatherbot
cd weatherbot
source .venv/bin/activate
python bot_v2.py
```

**How to safely leave the bot running:**
Once the bot is running inside `tmux`, you can safely detach and close your laptop:
1. Press and hold `Ctrl`
2. Tap the `B` key, then release both keys
3. Tap the `D` key
*(You will see `[detached (from session weatherbot)]`. The bot is now running forever in the background).*

**How to check on the bot later:**
To see the live scrolling terminal again, just SSH back in and re-attach:
```bash
ssh bob@<your-rpi-ip>
tmux attach
```

### 2. The Command Center (Your Laptop)
Your laptop does no trading. By mounting the same Google Drive folder via `rclone`, your local dashboard scripts instantly read the Pi's live pipeline.

**To view the glowing Terminal Dashboard:**
```bash
rclone mount gdrive: ~/google_drive --daemon
cd weatherbot
python tui.py
```

**To serve the Web Dashboard (Historical Charts):**
```bash
python serve_dashboard.py
# Open http://localhost:8000 in your browser
```

---

## Data Storage

All data is saved to `data/markets/` — one JSON file per market. Each file contains:
- Hourly forecast snapshots (ECMWF, HRRR, METAR)
- Market price history
- Position details (entry, stop, PnL)
- Final resolution outcome

This data is used for self-calibration — the bot learns forecast accuracy per city over time and adjusts position sizing accordingly.

---

## APIs Used

| API | Auth | Purpose |
|-----|------|---------|
| Open-Meteo | None | ECMWF + HRRR forecasts |
| Aviation Weather (METAR) | None | Real-time station observations |
| Polymarket Gamma | None | Market data |
| Visual Crossing | Free key | Historical temps for resolution |

---

## Disclaimer

This is not financial advice. Prediction markets carry real risk. Run the simulation thoroughly before committing real capital.
