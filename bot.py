"""
BTC/USDT Signal Bot
Monitors BTC/USDT price action on Binance and sends Telegram alerts for trading signals.

Signals:
  BUY  🟢 — Price > SMA(SMA_PERIOD) AND RSI < RSI_BUY_THRESHOLD
  SELL 🔴 — RSI > RSI_SELL_THRESHOLD

A daily summary message is sent every HEARTBEAT_INTERVAL_HOURS hours with the current
price, RSI, distance to the SMA, and the number of signals fired during that period.

Required environment variables:
  TELEGRAM_BOT_TOKEN — token from @BotFather
  TELEGRAM_CHAT_ID   — your Telegram chat ID (see README for instructions)

Optional environment variables (all have sensible defaults):
  TIMEFRAME                — OHLCV candle size, e.g. "1h" (default: 1h)
  SMA_PERIOD               — periods for the Simple Moving Average (default: 200)
  RSI_PERIOD               — periods for RSI calculation (default: 14)
  RSI_BUY_THRESHOLD        — RSI level below which a BUY signal fires (default: 35)
  RSI_SELL_THRESHOLD       — RSI level above which a SELL signal fires (default: 70)
  POLL_INTERVAL_SECONDS    — seconds between each data fetch (default: 60)
  HEARTBEAT_INTERVAL_HOURS — hours between daily summary messages (default: 24)
  SIGNAL_COOLDOWN_HOURS    — minimum hours between repeated same-type alerts (default: 4)
"""

import os
import time
import logging
from datetime import datetime, timedelta, UTC

import ccxt
import pandas as pd
import requests
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (loaded from environment variables)
# ---------------------------------------------------------------------------

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
SMA_PERIOD = 200
RSI_PERIOD = 14
RSI_BUY_THRESHOLD: float = 35.0   # RSI below this triggers a BUY signal
RSI_SELL_THRESHOLD: float = 70.0  # RSI above this triggers a SELL signal
POLL_INTERVAL_SECONDS = 60
HEARTBEAT_INTERVAL_HOURS = 24
SIGNAL_COOLDOWN_HOURS = 4   # minimum gap between repeated alerts for the same signal type
TELEGRAM_MAX_RETRIES = 3    # number of attempts before giving up on a Telegram send
RSI_EPSILON = 1e-10         # minimum avg_loss to avoid division by zero in RSI calculation

# ---------------------------------------------------------------------------
# Runtime configuration loader
# ---------------------------------------------------------------------------


def _load_config() -> None:
    """Override module-level constants from environment variables.

    Called in ``main()`` after ``load_dotenv()`` so the ``.env`` file has
    already been applied.  All parameters have defaults matching the values
    defined above, so the bot runs out-of-the-box without any extra variables.
    """
    global SMA_PERIOD, RSI_PERIOD, TIMEFRAME, POLL_INTERVAL_SECONDS
    global HEARTBEAT_INTERVAL_HOURS, SIGNAL_COOLDOWN_HOURS
    global RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD

    TIMEFRAME = os.environ.get("TIMEFRAME", TIMEFRAME)
    SMA_PERIOD = int(os.environ.get("SMA_PERIOD", SMA_PERIOD))
    RSI_PERIOD = int(os.environ.get("RSI_PERIOD", RSI_PERIOD))
    RSI_BUY_THRESHOLD = float(os.environ.get("RSI_BUY_THRESHOLD", RSI_BUY_THRESHOLD))
    RSI_SELL_THRESHOLD = float(os.environ.get("RSI_SELL_THRESHOLD", RSI_SELL_THRESHOLD))
    POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", POLL_INTERVAL_SECONDS))
    HEARTBEAT_INTERVAL_HOURS = int(os.environ.get("HEARTBEAT_INTERVAL_HOURS", HEARTBEAT_INTERVAL_HOURS))
    SIGNAL_COOLDOWN_HOURS = int(os.environ.get("SIGNAL_COOLDOWN_HOURS", SIGNAL_COOLDOWN_HOURS))

    logger.info(
        "Config: TIMEFRAME=%s SMA_PERIOD=%d RSI_PERIOD=%d "
        "RSI_BUY=%.1f RSI_SELL=%.1f POLL=%ds HEARTBEAT=%dh COOLDOWN=%dh",
        TIMEFRAME, SMA_PERIOD, RSI_PERIOD,
        RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD,
        POLL_INTERVAL_SECONDS, HEARTBEAT_INTERVAL_HOURS, SIGNAL_COOLDOWN_HOURS,
    )

# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def send_telegram_message(text: str) -> None:
    """Send a message to the configured Telegram chat.

    Credentials are read from the environment at call time so that changes made
    after module import (e.g. in tests) are always picked up.

    Retries up to ``TELEGRAM_MAX_RETRIES`` times with exponential back-off on
    transient network errors.  Application-level errors returned by the
    Telegram API (HTTP 200 with ``"ok": false``) are raised immediately without
    retrying, as they indicate a configuration problem rather than a transient
    failure.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    for attempt in range(1, TELEGRAM_MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, timeout=10)
            # Raise on HTTP 4xx/5xx, but strip the request URL from the
            # exception so the bot token is never written to logs.
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError:
                raise requests.exceptions.HTTPError(
                    f"Telegram API HTTP error {response.status_code}: {response.text}"
                ) from None
            # The Telegram API can return HTTP 200 with {"ok": false} for
            # application-level errors (e.g. bad chat ID).
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data.get('description')}")
            logger.info("Telegram message sent: %s", text.splitlines()[0])
            return
        except requests.exceptions.RequestException as exc:
            if attempt < TELEGRAM_MAX_RETRIES:
                wait = min(2 ** (attempt - 1), 60)
                logger.warning(
                    "Telegram send attempt %d/%d failed (%s); retrying in %ds",
                    attempt, TELEGRAM_MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
            else:
                raise


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
    # Guard against division by zero: when avg_loss is 0 (all gains), replace it
    # with RSI_EPSILON so rs becomes very large and RSI converges to 100.
    rs = avg_gain / avg_loss.replace(0, RSI_EPSILON)
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------


def check_and_notify(
    df: pd.DataFrame,
    last_signal: dict[str, datetime | None],
    signals_counter: dict[str, int],
) -> None:
    """Evaluate the latest candle and send a Telegram alert if a signal fires.

    ``last_signal`` is a mutable dict with keys ``"buy"`` and ``"sell"`` mapping
    to the ``datetime`` of the most recent alert of each type (or ``None``).
    Alerts for the same signal type are suppressed within ``SIGNAL_COOLDOWN_HOURS``.

    ``signals_counter`` is a mutable dict with keys ``"buy"`` and ``"sell"``
    counting how many alerts of each type have fired since the last heartbeat.
    """
    latest = df.iloc[-1]
    price: float = latest["close"]
    sma200: float = latest["sma200"]
    rsi: float = latest["rsi"]

    if pd.isna(sma200) or pd.isna(rsi):
        logger.warning("Not enough data to calculate indicators yet.")
        return

    logger.info("Price: %.2f | SMA200: %.2f | RSI: %.2f", price, sma200, rsi)
    now = datetime.now(UTC)
    cooldown = timedelta(hours=SIGNAL_COOLDOWN_HOURS)

    if price > sma200 and rsi < RSI_BUY_THRESHOLD:
        if last_signal["buy"] is None or now - last_signal["buy"] >= cooldown:
            message = (
                "🟢 <b>BUY SIGNAL</b>\n\n"
                f"Price:   <b>${price:,.2f}</b>\n"
                f"RSI:     <b>{rsi:.2f}</b>\n"
                f"200 SMA: <b>${sma200:,.2f}</b>"
            )
            send_telegram_message(message)
            last_signal["buy"] = now
            signals_counter["buy"] += 1

    # The SELL signal deliberately omits a trend filter: RSI overbought
    # conditions are actionable regardless of whether price is above or below
    # the 200 SMA, since extreme readings in either trend direction carry risk.
    elif rsi > RSI_SELL_THRESHOLD:
        if last_signal["sell"] is None or now - last_signal["sell"] >= cooldown:
            message = (
                "🔴 <b>SELL SIGNAL</b>\n\n"
                f"Price: <b>${price:,.2f}</b>\n"
                f"RSI:   <b>{rsi:.2f}</b>"
            )
            send_telegram_message(message)
            last_signal["sell"] = now
            signals_counter["sell"] += 1


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------


def send_daily_summary(df: pd.DataFrame, signals_counter: dict[str, int]) -> None:
    """Send an enriched heartbeat with the latest market snapshot and signal counts.

    Replaces the plain "System Active" message with the current price, RSI,
    the percentage distance from the SMA, and how many buy/sell alerts fired
    during the preceding heartbeat period.
    """
    latest = df.iloc[-1]
    price: float = latest["close"]
    sma200: float = latest["sma200"]
    rsi: float = latest["rsi"]

    rsi_str = f"{rsi:.2f}" if not pd.isna(rsi) else "N/A"

    if not pd.isna(sma200):
        pct = (price - sma200) / sma200 * 100
        direction = "above" if pct >= 0 else "below"
        sma_str = f"<b>${sma200:,.2f}</b> ({direction} by {abs(pct):.1f}%)"
    else:
        sma_str = "N/A"

    message = (
        "✅ <b>Daily Summary</b>\n\n"
        f"Price:   <b>${price:,.2f}</b>\n"
        f"RSI:     <b>{rsi_str}</b>\n"
        f"SMA{SMA_PERIOD}:  {sma_str}\n\n"
        f"Signals (last {HEARTBEAT_INTERVAL_HOURS}h):\n"
        f"  🟢 Buy:  {signals_counter['buy']}\n"
        f"  🔴 Sell: {signals_counter['sell']}"
    )
    send_telegram_message(message)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    load_dotenv()  # load .env file if present (no-op when env vars are already set)
    _load_config()  # apply env-variable overrides for all tuneable parameters

    missing = [v for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Required environment variable(s) not set: {', '.join(missing)}\n"
            "See README.md for setup instructions."
        )

    logger.info("BTC/USDT Signal Bot starting up.")
    send_telegram_message("🤖 <b>BTC/USDT Signal Bot started</b>")

    exchange = ccxt.binance()
    last_heartbeat = datetime.now(UTC)  # first heartbeat fires after HEARTBEAT_INTERVAL_HOURS
    last_signal: dict[str, datetime | None] = {"buy": None, "sell": None}
    signals_counter: dict[str, int] = {"buy": 0, "sell": 0}
    last_df: pd.DataFrame | None = None

    while True:
        try:
            # Fetch data first so the heartbeat can include the latest snapshot
            df = fetch_ohlcv(exchange)
            df = calculate_indicators(df)
            last_df = df

            # Heartbeat / daily summary
            now = datetime.now(UTC)
            if now - last_heartbeat >= timedelta(hours=HEARTBEAT_INTERVAL_HOURS):
                if last_df is not None:
                    send_daily_summary(last_df, signals_counter)
                else:
                    send_telegram_message("✅ <b>System Active</b>")
                signals_counter = {"buy": 0, "sell": 0}
                last_heartbeat = now

            check_and_notify(df, last_signal, signals_counter)

        except requests.exceptions.RequestException as exc:
            logger.error("Telegram request failed: %s", exc)
        except ccxt.NetworkError as exc:
            logger.error("Exchange network error: %s", exc)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error: %s", exc)

        # Sleep outside the try/except so the bot always waits between
        # iterations regardless of errors, avoiding tight retry loops.
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
