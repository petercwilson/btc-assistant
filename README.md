# BTC/USDT Signal Bot

A lightweight Python signal bot that monitors BTC/USDT price action on Binance and sends **Telegram alerts** when key technical conditions are met. It is a **read-only** bot — it never places any orders.

---

## Signals

| Signal | Condition |
|---|---|
| 🟢 **BUY SIGNAL** | Price > 200-period SMA **AND** RSI < 35 |
| 🔴 **SELL SIGNAL** | RSI > 70 |

Each alert includes the current price, RSI value, and (for buy signals) the 200 SMA.  
A ✅ **System Active** heartbeat message is sent every 24 hours so you know the bot is still running.

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

```bash
# Load env vars from .env (bash)
export $(grep -v '^#' .env | xargs)

python bot.py
```

Or on Windows (PowerShell):

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -notmatch '^#' -and $_ -ne '') {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
    }
}
python bot.py
```

The bot will:
1. Send a startup message to your Telegram chat.
2. Check the BTC/USDT 1-hour chart every 60 seconds.
3. Send a signal alert whenever conditions are met.
4. Send a "System Active" heartbeat every 24 hours.

---

## Configuration reference

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your numeric Telegram chat ID |

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
