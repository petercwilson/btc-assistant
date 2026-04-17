# BTC/USDT Signal Bot

A lightweight Python signal bot that monitors BTC/USDT price action on Binance and sends **Telegram alerts** when key technical conditions are met. It is a **read-only** bot — it never places any orders.

---

## Signals

| Signal | Condition |
|---|---|
| 🟢 **BUY SIGNAL** | Price > SMA(`SMA_PERIOD`) **AND** RSI < `RSI_BUY_THRESHOLD` (default 35) |
| 🔴 **SELL SIGNAL** | RSI > `RSI_SELL_THRESHOLD` (default 70) |

Each alert includes the current price, RSI value, and (for buy signals) the SMA.  
A ✅ **Daily Summary** message is sent every `HEARTBEAT_INTERVAL_HOURS` hours (default 24h) with the current price, RSI, distance from the SMA, and the number of signals fired during that period.

---

## Requirements

- Python 3.9+
- A Telegram Bot token (free, from @BotFather)
- Your Telegram Chat ID

---

## Setup

### 1 — Create a Telegram Bot and get your token

1. Open Telegram and search for **@BotFather**.
2. Start a chat and send `/newbot`.
3. Follow the prompts — choose a name (e.g. `BTC Signal Bot`) and a username (must end in `bot`, e.g. `btc_signal_bot`).
4. BotFather will reply with a token that looks like:
   ```
   123456789:ABCdefGhIJKlmNoPQRsTUVwxYZ
   ```
   Keep this secret — treat it like a password.

### 2 — Find your Telegram Chat ID

1. Search for **@userinfobot** in Telegram.
2. Start a chat with it (send `/start` or any message).
3. It will reply with your numeric **Chat ID**, e.g. `987654321`.

> Alternatively, send any message to your new bot, then open this URL in a browser (replace `<TOKEN>` with your bot token):
> ```
> https://api.telegram.org/bot<TOKEN>/getUpdates
> ```
> Look for `"chat":{"id": ...}` in the JSON response.

### 3 — Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxYZ
TELEGRAM_CHAT_ID=987654321
```

> **Never commit your `.env` file.** It is already listed in `.gitignore`.

### 4 — Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 5 — Run the bot

The bot loads `.env` automatically at startup via `python-dotenv`, so no manual `export` step is needed:

```bash
python bot.py
```

The bot will:
1. Send a startup message to your Telegram chat.
2. Check the BTC/USDT 1-hour chart every 60 seconds.
3. Send a signal alert whenever conditions are met.
4. Send a "System Active" heartbeat every 24 hours.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(required)* | Your numeric Telegram chat ID |
| `TIMEFRAME` | `1h` | OHLCV candle size (e.g. `4h`, `1d`) |
| `SMA_PERIOD` | `200` | Simple Moving Average period |
| `RSI_PERIOD` | `14` | RSI calculation period |
| `RSI_BUY_THRESHOLD` | `35` | RSI below this value triggers a BUY signal |
| `RSI_SELL_THRESHOLD` | `70` | RSI above this value triggers a SELL signal |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between each data fetch |
| `HEARTBEAT_INTERVAL_HOURS` | `24` | Hours between daily summary messages |
| `SIGNAL_COOLDOWN_HOURS` | `4` | Minimum hours between repeated same-type alerts |

---

## Project structure

```
btc-assistant/
├── bot.py            # Signal bot (main entry point)
├── requirements.txt  # Python dependencies
├── .env.example      # Environment variable template
└── README.md
```

---

## Disclaimer

This bot is for **informational purposes only** and does **not** constitute financial advice. It never executes trades on your behalf. Always do your own research before making investment decisions.
