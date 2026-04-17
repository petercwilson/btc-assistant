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
| 🔄 **Regime Change** | Market switches between trend_up / trend_down / range |
| 📈 **Resistance Break** | Price breaks above the recent swing high |
| 📉 **Support Break** | Price breaks below the recent swing low |
| 📊 **Volatility Expansion** | BB width increases by more than `BB_WIDTH_CHANGE_THRESHOLD`% |
| 📊 **Volatility Contraction** | BB width decreases by more than `BB_WIDTH_CHANGE_THRESHOLD`% |
| 📉 **Drawdown Alert** | Price drops a configured % from its rolling high (10% / 20% default) |
| ⚠️ **Risk-Off Signal** | Composite: price < SMA + BB expanding + bearish MACD + RSI > 55 |

BUY and SELL signals include a **strength score (1–5 ⭐)** combining RSI distance from its threshold and price distance from the SMA. A **🔥 volume spike** tag is added when volume exceeds `VOLUME_SPIKE_MULTIPLIER` × its rolling mean.

Every signal now includes a **💡 "Why it matters"** one-liner explanation, and a **📊 frequency stat** showing how many times the condition occurred in the last `SIGNAL_HISTORY_LOOKBACK` candles.

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

When enabled, the bot sends a structured "advisor-style" market brief — one message per monitored symbol — at the configured UTC hour. The brief covers:

- **Trend**: price vs SMA200 (distance %), SMA slope (rising / flat / falling)
- **Momentum**: RSI with label (oversold / neutral / overbought), MACD histogram direction
- **Volatility**: Bollinger Band width % vs 20-candle average (expanding / stable / contracting)
- **Key Levels**: recent swing high and swing low, drawdown from rolling high
- **Risk Posture** (deterministic label):
  - 🟢 **Aggressive** — price > SMA AND RSI < 50 AND regime is `trend_up`
  - 🔴 **Conservative** — price < SMA OR RSI > `RSI_SELL_THRESHOLD`
  - 🟡 **Neutral** — everything else
- **What would change my view**: context-sensitive bullets derived from the posture above
- **Disclaimer**: "Informational only. Not financial advice."

The daily brief and the weekly brief share the same `ADVISOR_BRIEF_HOUR_UTC`. Both are optional and independent.

**Multi-symbol**: one brief message is sent per configured symbol.

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `ADVISOR_BRIEF_ENABLED` | `false` | Enable daily advisor brief |
| `ADVISOR_BRIEF_HOUR_UTC` | `13` | UTC hour to send the daily brief (0–23) |
| `WEEKLY_BRIEF_ENABLED` | `false` | Enable weekly advisor brief |
| `WEEKLY_BRIEF_WEEKDAY` | `Mon` | Weekday for weekly brief (Mon–Sun) |

---

## Key Levels & Regime Detection

The bot continuously monitors market regime and key price levels.

**Regime classification** (per symbol) uses the SMA slope over the last `REGIME_SLOPE_PERIOD` candles:
- `trend_up` — slope > +0.5%
- `trend_down` — slope < −0.5%
- `range` — slope within ±0.5%

An alert is sent when the regime changes (with a cooldown).

**Key level breaks**: the bot tracks the recent swing high and swing low (highest high / lowest low over the first half of a `SWING_LOOKBACK × 2` candle window). A break above the swing high triggers a **Resistance Break** alert; a break below the swing low triggers a **Support Break** alert.

| Variable | Default | Description |
|---|---|---|
| `SWING_LOOKBACK` | `20` | Candles used for swing high/low detection |
| `REGIME_SLOPE_PERIOD` | `5` | Candles used to measure SMA slope for regime |
| `KEY_LEVEL_BREAK_COOLDOWN_HOURS` | `8` | Min hours between key-level break alerts |
| `REGIME_CHANGE_COOLDOWN_HOURS` | `12` | Min hours between regime-change alerts |

---

## Risk & Volatility Alerts

Three additional alert types help monitor market risk:

**Volatility Expansion/Contraction** — fires when the Bollinger Band width changes by more than `BB_WIDTH_CHANGE_THRESHOLD`% relative to its previous value. Helps identify periods of increasing or decreasing uncertainty.

**Drawdown Alerts** — monitors price drawdown from its rolling `DRAWDOWN_WINDOW`-candle high. Fires once per threshold (default: −10% and −20%) with a 48-hour cooldown per threshold.

**Risk-Off Composite** — fires when all four bearish conditions are met simultaneously: price < SMA, BB width expanding, MACD histogram negative, RSI > 55. Indicates a coordinated deterioration in trend, volatility, and momentum.

| Variable | Default | Description |
|---|---|---|
| `BB_WIDTH_CHANGE_THRESHOLD` | `20.0` | % BB width change to trigger vol alert |
| `DRAWDOWN_WINDOW` | `168` | Candles for rolling-high drawdown calculation |
| `DRAWDOWN_ALERT_THRESHOLDS` | `10,20` | Comma-separated drawdown % thresholds |
| `RISK_OFF_COOLDOWN_HOURS` | `24` | Min hours between risk-off alerts |
| `VOL_ALERT_COOLDOWN_HOURS` | `12` | Min hours between volatility alerts |

---

## Signal Frequency Context

Every signal alert includes a **📊 frequency stat** computed from the last `SIGNAL_HISTORY_LOOKBACK` candles of fetched history:

> *Occurred 3× in last 200 candles*

This helps distinguish rare signals from common noise — no external data required.

| Variable | Default | Description |
|---|---|---|
| `SIGNAL_HISTORY_LOOKBACK` | `200` | Candles used for frequency stat calculation |

---

## Macro Calendar Event Reminders

The bot can send reminders ahead of major macro events (FOMC, CPI, NFP, etc.) without any web calls. Events are stored in a local JSON file.

**Setup**: edit `data/macro_calendar.json` to add or update events:

```json
[
  {
    "id": "fomc-2026-06",
    "title": "FOMC Meeting",
    "utc": "2026-06-17T18:00:00Z",
    "note": "Federal Reserve interest rate decision."
  }
]
```

Each `id` must be unique. Reminders are sent at `MACRO_CALENDAR_LOOKAHEAD_HOURS` before the event and again 1 hour before. Sent reminders are tracked in SQLite (`event_reminders` table) so they are never re-sent, even if the bot restarts.

| Variable | Default | Description |
|---|---|---|
| `MACRO_CALENDAR_ENABLED` | `false` | Enable macro event reminders |
| `MACRO_CALENDAR_PATH` | `data/macro_calendar.json` | Path to the calendar file |
| `MACRO_CALENDAR_LOOKAHEAD_HOURS` | `24` | Hours-ahead window for first reminder |

---

## Portfolio-agnostic Guidance

Two optional weekly educational messages — no user profiles involved:

**DCA Reminder** — a simple weekly reminder to consider a scheduled purchase, regardless of current price. Sent on the configured weekday and hour.

**Risk Tip Rotation** — a weekly rotating tip from a static list of 7 risk-management principles (e.g. position sizing, dry powder, exit strategy). The tip index advances each week.

Both messages always include: *"Informational only. Not financial advice."*

| Variable | Default | Description |
|---|---|---|
| `DCA_REMINDER_ENABLED` | `false` | Enable weekly DCA reminder |
| `DCA_REMINDER_WEEKDAY` | `Mon` | Weekday for DCA reminder |
| `DCA_REMINDER_HOUR_UTC` | `9` | UTC hour for DCA reminder |
| `RISK_TIP_ENABLED` | `false` | Enable weekly risk tip rotation |
| `RISK_TIP_WEEKDAY` | `Sun` | Weekday for risk tip |
| `RISK_TIP_HOUR_UTC` | `10` | UTC hour for risk tip |

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

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(required)* | Comma-separated Telegram chat ID(s) |
| `EXCHANGE` | `coinbase` | ccxt exchange ID (e.g. `kraken`, `binance`) |
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

### Advisor Brief

| Variable | Default | Description |
|---|---|---|
| `ADVISOR_BRIEF_ENABLED` | `false` | Enable daily advisor brief |
| `ADVISOR_BRIEF_HOUR_UTC` | `13` | UTC hour to send the daily brief (0–23) |
| `WEEKLY_BRIEF_ENABLED` | `false` | Enable weekly advisor brief |
| `WEEKLY_BRIEF_WEEKDAY` | `Mon` | Weekday for weekly brief (Mon–Sun) |

### Key Levels & Regime

| Variable | Default | Description |
|---|---|---|
| `SWING_LOOKBACK` | `20` | Candles for swing high/low detection |
| `REGIME_SLOPE_PERIOD` | `5` | Candles used to measure SMA slope |
| `KEY_LEVEL_BREAK_COOLDOWN_HOURS` | `8` | Min hours between key-level break alerts |
| `REGIME_CHANGE_COOLDOWN_HOURS` | `12` | Min hours between regime-change alerts |

### Risk & Volatility

| Variable | Default | Description |
|---|---|---|
| `BB_WIDTH_CHANGE_THRESHOLD` | `20.0` | BB width % change to trigger vol alert |
| `DRAWDOWN_WINDOW` | `168` | Candles for rolling-high drawdown |
| `DRAWDOWN_ALERT_THRESHOLDS` | `10,20` | Drawdown % thresholds (comma-separated) |
| `RISK_OFF_COOLDOWN_HOURS` | `24` | Min hours between risk-off alerts |
| `VOL_ALERT_COOLDOWN_HOURS` | `12` | Min hours between volatility alerts |

### Signal History Context

| Variable | Default | Description |
|---|---|---|
| `SIGNAL_HISTORY_LOOKBACK` | `200` | Candles for signal frequency stat |

### Macro Calendar

| Variable | Default | Description |
|---|---|---|
| `MACRO_CALENDAR_ENABLED` | `false` | Enable macro event reminders |
| `MACRO_CALENDAR_PATH` | `data/macro_calendar.json` | Path to calendar file |
| `MACRO_CALENDAR_LOOKAHEAD_HOURS` | `24` | Hours-ahead window for first reminder |

### Portfolio Guidance

| Variable | Default | Description |
|---|---|---|
| `DCA_REMINDER_ENABLED` | `false` | Enable weekly DCA reminder |
| `DCA_REMINDER_WEEKDAY` | `Mon` | Weekday for DCA reminder |
| `DCA_REMINDER_HOUR_UTC` | `9` | UTC hour for DCA reminder |
| `RISK_TIP_ENABLED` | `false` | Enable weekly risk tip rotation |
| `RISK_TIP_WEEKDAY` | `Sun` | Weekday for risk tip |
| `RISK_TIP_HOUR_UTC` | `10` | UTC hour for risk tip |

---

## Project Structure

```
btc-assistant/
├── bot.py               # Signal bot (main entry point)
├── data/
│   └── macro_calendar.json  # Macro event calendar (edit to add events)
├── tests/
│   └── test_config.py   # Unit tests
├── requirements.txt     # Python dependencies
├── Dockerfile           # Docker image definition
├── docker-compose.yml   # Docker Compose service definition
├── .env.example         # Environment variable template
└── README.md
```

---

## Disclaimer

This bot is for **informational purposes only** and does **not** constitute financial advice. It never executes trades on your behalf. Always do your own research before making investment decisions.