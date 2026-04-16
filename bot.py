"""
BTC/USDT Signal Bot
Monitors BTC/USDT price action and sends Telegram alerts for trading signals.

Signals:
  BUY  🟢 — Price > 200-period SMA AND RSI < 35
  SELL 🔴 — RSI > 70

A heartbeat "System Active" message is sent every 24 hours.

Required environment variables:
  TELEGRAM_BOT_TOKEN — token from @BotFather
  TELEGRAM_CHAT_ID   — your Telegram chat ID (see README for instructions)
"""

import os
import time
import logging
from datetime import datetime, timedelta

import ccxt
import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (loaded from environment variables)
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
SMA_PERIOD = 200
RSI_PERIOD = 14
POLL_INTERVAL_SECONDS = 60
HEARTBEAT_INTERVAL_HOURS = 24
SIGNAL_COOLDOWN_HOURS = 4  # minimum gap between repeated alerts for the same signal type

# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def send_telegram_message(text: str) -> None:
    """Send a message to the configured Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    logger.info("Telegram message sent: %s", text.splitlines()[0])


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


def fetch_ohlcv(exchange: ccxt.Exchange) -> pd.DataFrame:
    """Fetch recent OHLCV candles for BTC/USDT from Binance (read-only)."""
    limit = SMA_PERIOD + RSI_PERIOD + 10  # enough candles for both indicators
    raw = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["close"] = df["close"].astype(float)
    return df


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMA and RSI columns to the OHLCV DataFrame."""
    df = df.copy()

    # 200-period Simple Moving Average
    df["sma200"] = df["close"].rolling(window=SMA_PERIOD).mean()

    # 14-period RSI (Wilder / EMA smoothing via pandas ewm)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------


def check_and_notify(
    df: pd.DataFrame,
    last_signal: dict,
) -> None:
    """Evaluate the latest candle and send a Telegram alert if a signal fires.

    ``last_signal`` is a mutable dict with keys ``"buy"`` and ``"sell"`` mapping
    to the ``datetime`` of the most recent alert of each type (or ``None``).
    Alerts for the same signal type are suppressed within ``SIGNAL_COOLDOWN_HOURS``.
    """
    latest = df.iloc[-1]
    price: float = latest["close"]
    sma200: float = latest["sma200"]
    rsi: float = latest["rsi"]

    if pd.isna(sma200) or pd.isna(rsi):
        logger.warning("Not enough data to calculate indicators yet.")
        return

    logger.info("Price: %.2f | SMA200: %.2f | RSI: %.2f", price, sma200, rsi)
    now = datetime.utcnow()
    cooldown = timedelta(hours=SIGNAL_COOLDOWN_HOURS)

    if price > sma200 and rsi < 35:
        if last_signal["buy"] is None or now - last_signal["buy"] >= cooldown:
            message = (
                "🟢 <b>BUY SIGNAL</b>\n\n"
                f"Price:   <b>${price:,.2f}</b>\n"
                f"RSI:     <b>{rsi:.2f}</b>\n"
                f"200 SMA: <b>${sma200:,.2f}</b>"
            )
            send_telegram_message(message)
            last_signal["buy"] = now

    elif rsi > 70:
        if last_signal["sell"] is None or now - last_signal["sell"] >= cooldown:
            message = (
                "🔴 <b>SELL SIGNAL</b>\n\n"
                f"Price: <b>${price:,.2f}</b>\n"
                f"RSI:   <b>{rsi:.2f}</b>"
            )
            send_telegram_message(message)
            last_signal["sell"] = now


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    missing = [v for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Required environment variable(s) not set: {', '.join(missing)}\n"
            "See README.md for setup instructions."
        )

    logger.info("BTC/USDT Signal Bot starting up.")
    send_telegram_message("🤖 <b>BTC/USDT Signal Bot started</b>")

    exchange = ccxt.binance()
    last_heartbeat: datetime = datetime.utcnow()  # first heartbeat fires after 24 h; startup message serves as immediate confirmation
    last_signal: dict = {"buy": None, "sell": None}

    while True:
        try:
            # Heartbeat
            now = datetime.utcnow()
            if now - last_heartbeat >= timedelta(hours=HEARTBEAT_INTERVAL_HOURS):
                send_telegram_message("✅ <b>System Active</b>")
                last_heartbeat = now

            # Fetch data and check signals
            df = fetch_ohlcv(exchange)
            df = calculate_indicators(df)
            check_and_notify(df, last_signal)

        except requests.exceptions.RequestException as exc:
            logger.error("Telegram request failed: %s", exc)
        except ccxt.NetworkError as exc:
            logger.error("Exchange network error: %s", exc)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error: %s", exc)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
