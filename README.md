# BTC Assistant (Telegram)

A lightweight Python signal bot that monitors one or more crypto trading pairs on a configurable exchange and sends **Telegram alerts** when key technical conditions are met.

- **Read-only**: it never places orders.
- **No profiles**: it does not store user profiles.

---

## Signals

| Signal | Condition |
|---|---|
| 🟢 **BUY** | Price > SMA(`SMA_PERIOD`) **AND** RSI < `RSI_BUY_THRESHOLD` |
| 🔴 **SELL** | RSI > `RSI_SELL_THRESHOLD` (optionally AND price < SMA if `SELL_TREND_FILTER=true`) |
| 📈 **MACD BUY** | MACD histogram flips from negative to positive (bullish crossover) |
| 📉 **MACD SELL** | MACD histogram flips from positive to negative (bearish crossover) |
| 📊 **BB Upper Break** | Price closes above the upper Bollinger Band |
| 📊 **BB Lower Break** | Price closes below the lower Bollinger Band |
| 🔀 **Divergence** | Bullish or bearish RSI/price divergence detected |
| ⚡ **Large Move** | Single candle moves more than `PRICE_MOVE_PCT_THRESHOLD`% |
| 🏔 **ATH Proximity** | Price enters within `ATH_PROXIMITY_PCT`% of the rolling ATH |
| 🎯 **Price Alert** | Price crosses `PRICE_ALERT_HIGH` or `PRICE_ALERT_LOW` (one-time) |

BUY and SELL signals include a **strength score (1–5 ⭐)** combining RSI distance from its threshold and price distance from the SMA. A **🔥 volume spike** tag is added when volume exceeds `VOLUME_SPIKE_MULTIPLIER` × its rolling mean.

A ✅ **Daily Summary** is sent every `HEARTBEAT_INTERVAL_HOURS` hours with the current price, RSI, SMA distance, Bollinger Band width, and the signal counts for the period.

---

## Telegram Commands

Send these commands to your bot in the Telegram chat:

| Command | Description |
|---|---|
| `/status` | Current price, RSI, and SMA for all monitored symbols |
| `/config` | Show the active configuration |
| `/mute <hours>` | Suppress all alerts for the given number of hours |

Signal messages also include **Snooze 1h / Snooze 4h** inline buttons to temporarily suppress that specific signal type.

---

## Advisor Brief (Telegram-only, read-only)

You can optionally enable a daily/weekly "advisor-style" market brief message.

- It is still **read-only** (no orders).
- It does **not** store user profiles.

Environment variables:

- `ADVISOR_BRIEF_ENABLED` — `true`/`false` (default: `false`)
- `ADVISOR_BRIEF_HOUR_UTC` — hour of day in UTC (`0`-`23`, default: `13`)
- `WEEKLY_BRIEF_ENABLED` — `true`/`false` (default: `false`)
- `WEEKLY_BRIEF_WEEKDAY` — `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun` (default: `Mon`)

---

## Requirements

- Python 3.9+
- A Telegram Bot token (from @BotFather)
- Your Telegram Chat ID

---

## Setup

### 1 — Create a Telegram Bot and get your token

1. Open Telegram and search for **@BotFather**.
2. Start a chat and send `/newbot`.
3. Follow the prompts — choose a name and a username (must end in `bot`).
4. BotFather will reply with a token like:
   ````
   123456789:ABCdefGhIJKlmNoPQRsTUVwxYZ
   ````

### 2 — Find your Telegram Chat ID

1. Search for **@userinfobot** in Telegram.
2. Start a chat with it and send `/start`.
3. It will reply with your numeric **Chat ID**, e.g. `987654321`.

To broadcast signals to **multiple chats** (e.g. private chat + group), set `TELEGRAM_CHAT_ID` to a comma-separated list:

```
TELEGRAM_CHAT_ID=987654321,-100123456789
```

### 3 — Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### 4 — Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 5 — Run the bot

```bash
python bot.py
```

The bot will:
1. Send a startup message to your Telegram chat.
2. Check all configured symbols every `POLL_INTERVAL_SECONDS` seconds.
3. Send signal alerts whenever conditions are met.
4. Send a daily summary every `HEARTBEAT_INTERVAL_HOURS` hours.
5. Send a "Bot stopped" message on clean shutdown (SIGTERM or Ctrl-C).

---

## Docker

Build and run with Docker Compose (recommended for production):

```bash
cp .env.example .env   # fill in your credentials
docker compose up -d
```

The SQLite history database is persisted in a named Docker volume (`bot_data`).

To expose the Prometheus `/metrics` endpoint, uncomment the `ports` section in `docker-compose.yml` and set `PROMETHEUS_PORT=8000` in `.env`.

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(required)* | Comma-separated Telegram chat ID(s) |
| `EXCHANGE` | `binance` | ccxt exchange ID (e.g. `kraken`, `coinbase`) |
| `SYMBOLS` | `BTC/USDT,SOL/USDT` | Comma-separated trading pairs |
| `TIMEFRAME` | `1h` | OHLCV candle size (e.g. `4h`, `1d`) |
| `CONFIRM_TIMEFRAME` | *(disabled)* | Second timeframe for signal confirmation |
| `SMA_PERIOD` | `200` | Simple Moving Average period |
| `RSI_PERIOD` | `14` | RSI calculation period |
| `RSI_BUY_THRESHOLD` | `35` | RSI below this triggers a BUY signal |
| `RSI_SELL_THRESHOLD` | `70` | RSI above this triggers a SELL signal |
| `SELL_TREND_FILTER` | `false` | Require price < SMA for SELL signals |
| `MACD_FAST` | `12` | MACD fast EMA period |
| `MACD_SLOW` | `26` | MACD slow EMA period |
| `MACD_SIGNAL_PERIOD` | `9` | MACD signal line period |
| `BB_PERIOD` | `20` | Bollinger Bands rolling period |
| `BB_STD` | `2.0` | Bollinger Bands standard deviation multiplier |
| `VOLUME_SPIKE_PERIOD` | `20` | Periods for the volume rolling average |
| `VOLUME_SPIKE_MULTIPLIER` | `2.0` | Volume spike threshold (× rolling average) |
| `DIVERGENCE_LOOKBACK` | `14` | Candles to scan for RSI/price divergence |
| `PRICE_ALERT_HIGH` | *(disabled)* | One-time alert when price crosses above this |
| `PRICE_ALERT_LOW` | *(disabled)* | One-time alert when price crosses below this |
| `ATH_LOOKBACK_CANDLES` | `0` (disabled) | Candles for the rolling ATH calculation |
| `ATH_PROXIMITY_PCT` | `5.0` | Alert when price is within this % of ATH |
| `PRICE_MOVE_PCT_THRESHOLD` | `0` (disabled) | Alert on candle move above this % |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between each data fetch |
| `HEARTBEAT_INTERVAL_HOURS` | `24` | Hours between daily summary messages |
| `SIGNAL_COOLDOWN_HOURS` | `4` | Minimum hours between repeated same-type alerts |
| `CIRCUIT_BREAKER_ERRORS` | `5` | Consecutive errors before circuit-breaker alert |
| `PROMETHEUS_PORT` | `0` (disabled) | Port for the Prometheus `/metrics` endpoint |
| `DB_PATH` | `signals.db` | Path for the SQLite signal history database |

---

## Project Structure

```
btc-assistant/
├── bot.py             # Signal bot (main entry point)
├── requirements.txt   # Python dependencies
├── Dockerfile         # Docker image definition
├── docker-compose.yml # Docker Compose service definition
├── .env.example       # Environment variable template
└── README.md
```

---

## Disclaimer

This bot is for **informational purposes only** and does **not** constitute financial advice. It never executes trades on your behalf. Always do your own research before making investment decisions.