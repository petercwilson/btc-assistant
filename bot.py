"""
Signal Bot — multi-symbol, multi-indicator Telegram alert bot.

Monitors one or more trading pairs on a configurable exchange and sends
Telegram alerts for a range of technical signals.

Signals:
  BUY       🟢 — Price > SMA AND RSI < RSI_BUY_THRESHOLD
  SELL      🔴 — RSI > RSI_SELL_THRESHOLD (optionally AND price < SMA)
  MACD BUY  📈 — MACD histogram flips positive (bullish crossover)
  MACD SELL 📉 — MACD histogram flips negative (bearish crossover)
  BB UPPER  📊 — Price breaks above the upper Bollinger Band
  BB LOWER  📊 — Price breaks below the lower Bollinger Band
  DIVERGE   🔀 — Bullish or bearish RSI/price divergence detected
  MOVE      ⚡ — Price moves > PRICE_MOVE_PCT_THRESHOLD% in one candle
  ATH       🏔  — Price within ATH_PROXIMITY_PCT% of its rolling ATH
  PRICE ↑/↓ 🎯 — One-time alert when price crosses PRICE_ALERT_HIGH/LOW

All BUY/SELL signals include a strength score (1–5 ⭐).
Volume spikes (🔥) are flagged when volume exceeds
VOLUME_SPIKE_MULTIPLIER × its rolling mean.

Optional second-timeframe confirmation: set CONFIRM_TIMEFRAME to
require the same trend/RSI conditions on a higher timeframe before alerting.

A daily summary is sent every HEARTBEAT_INTERVAL_HOURS hours, including
price, RSI, SMA distance, Bollinger Band width, and signal counts.

Telegram commands (send to the bot chat):
  /status          — current price, RSI, and indicators for all symbols
  /config          — show active configuration
  /mute <hours>    — suppress all alerts for <hours> hours

Signal messages include "Snooze 1h" / "Snooze 4h" inline buttons.

Required environment variables:
  TELEGRAM_BOT_TOKEN — token from @BotFather
  TELEGRAM_CHAT_ID   — comma-separated chat ID(s)

Optional environment variables (all have sensible defaults):
  EXCHANGE                 — ccxt exchange ID (default: binance)
  SYMBOLS                  — comma-separated pairs (default: BTC/USDT)
  TIMEFRAME                — OHLCV candle size (default: 1h)
  CONFIRM_TIMEFRAME        — second timeframe for confirmation (default: disabled)
  SMA_PERIOD               — Simple Moving Average period (default: 200)
  RSI_PERIOD               — RSI period (default: 14)
  RSI_BUY_THRESHOLD        — RSI below this triggers BUY (default: 35)
  RSI_SELL_THRESHOLD       — RSI above this triggers SELL (default: 70)
  SELL_TREND_FILTER        — require price < SMA for SELL (default: false)
  MACD_FAST                — MACD fast EMA period (default: 12)
  MACD_SLOW                — MACD slow EMA period (default: 26)
  MACD_SIGNAL_PERIOD       — MACD signal line period (default: 9)
  BB_PERIOD                — Bollinger Band period (default: 20)
  BB_STD                   — Bollinger Band std multiplier (default: 2.0)
  VOLUME_SPIKE_PERIOD      — periods for volume moving average (default: 20)
  VOLUME_SPIKE_MULTIPLIER  — volume spike threshold multiplier (default: 2.0)
  DIVERGENCE_LOOKBACK      — candles to scan for divergence (default: 14)
  PRICE_ALERT_HIGH         — one-time alert when price crosses above this level
  PRICE_ALERT_LOW          — one-time alert when price crosses below this level
  ATH_LOOKBACK_CANDLES     — candles for rolling ATH (0 = disabled, default: 0)
  ATH_PROXIMITY_PCT        — alert within this % of ATH (default: 5.0)
  PRICE_MOVE_PCT_THRESHOLD — alert on candle move above this % (0 = disabled)
  POLL_INTERVAL_SECONDS    — seconds between data fetches (default: 60)
  HEARTBEAT_INTERVAL_HOURS — hours between daily summaries (default: 24)
  SIGNAL_COOLDOWN_HOURS    — minimum hours between same-type alerts (default: 4)
  CIRCUIT_BREAKER_ERRORS   — consecutive errors before alerting (default: 5)
  PROMETHEUS_PORT          — Prometheus /metrics port (0 = disabled)
  DB_PATH                  — SQLite history database path (default: signals.db)
"""

# Standard library
import json
import logging
import os
import signal as _signal_module
import sqlite3
import threading
import time
from datetime import datetime, timedelta, UTC
from typing import Any

# Third-party
import ccxt
import pandas as pd
import requests
from dotenv import load_dotenv

try:
    import prometheus_client as _prom_client  # type: ignore[import-untyped]
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults (overridden by _load_config)
# ---------------------------------------------------------------------------

EXCHANGE_ID: str = "binance"
SYMBOLS: list[str] = ["BTC/USDT"]
TIMEFRAME: str = "1h"
CONFIRM_TIMEFRAME: str = ""           # e.g. "4h"; empty = disabled
SMA_PERIOD: int = 200
RSI_PERIOD: int = 14
RSI_BUY_THRESHOLD: float = 35.0
RSI_SELL_THRESHOLD: float = 70.0
SELL_TREND_FILTER: bool = False        # require price < SMA for SELL signals
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL_PERIOD: int = 9
BB_PERIOD: int = 20
BB_STD: float = 2.0
VOLUME_SPIKE_PERIOD: int = 20
VOLUME_SPIKE_MULTIPLIER: float = 2.0
DIVERGENCE_LOOKBACK: int = 14
PRICE_ALERT_HIGH: float | None = None
PRICE_ALERT_LOW: float | None = None
ATH_LOOKBACK_CANDLES: int = 0         # 0 = disabled
ATH_PROXIMITY_PCT: float = 5.0
PRICE_MOVE_PCT_THRESHOLD: float = 0.0  # 0 = disabled
POLL_INTERVAL_SECONDS: int = 60
HEARTBEAT_INTERVAL_HOURS: int = 24
SIGNAL_COOLDOWN_HOURS: int = 4
CIRCUIT_BREAKER_ERRORS: int = 5
PROMETHEUS_PORT: int = 0              # 0 = disabled
DB_PATH: str = "signals.db"
TELEGRAM_MAX_RETRIES: int = 3
RSI_EPSILON: float = 1e-10

# ---------------------------------------------------------------------------
# Shutdown event — set by SIGTERM or KeyboardInterrupt
# ---------------------------------------------------------------------------

_shutdown = threading.Event()

# ---------------------------------------------------------------------------
# Global mute state (shared between main loop and command handler thread)
# ---------------------------------------------------------------------------

_mute_lock = threading.Lock()
_global_mute_until: datetime | None = None

# ---------------------------------------------------------------------------
# Prometheus metric handles (populated in _init_prometheus)
# ---------------------------------------------------------------------------

_prom_signals: Any = None
_prom_price: Any = None
_prom_rsi: Any = None
_prom_fetch_hist: Any = None
_prom_errors: Any = None

# ---------------------------------------------------------------------------
# Runtime configuration loader
# ---------------------------------------------------------------------------


def _load_config() -> None:
    """Override module-level defaults from environment variables.

    Called in ``main()`` after ``load_dotenv()`` so the ``.env`` file has
    already been applied.  All parameters have defaults matching the values
    defined above, so the bot runs out-of-the-box without any extra variables.
    """
    global EXCHANGE_ID, SYMBOLS, TIMEFRAME, CONFIRM_TIMEFRAME
    global SMA_PERIOD, RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD
    global SELL_TREND_FILTER, MACD_FAST, MACD_SLOW, MACD_SIGNAL_PERIOD
    global BB_PERIOD, BB_STD, VOLUME_SPIKE_PERIOD, VOLUME_SPIKE_MULTIPLIER
    global DIVERGENCE_LOOKBACK, PRICE_ALERT_HIGH, PRICE_ALERT_LOW
    global ATH_LOOKBACK_CANDLES, ATH_PROXIMITY_PCT, PRICE_MOVE_PCT_THRESHOLD
    global POLL_INTERVAL_SECONDS, HEARTBEAT_INTERVAL_HOURS, SIGNAL_COOLDOWN_HOURS
    global CIRCUIT_BREAKER_ERRORS, PROMETHEUS_PORT, DB_PATH

    EXCHANGE_ID = os.environ.get("EXCHANGE", EXCHANGE_ID)
    raw_symbols = os.environ.get("SYMBOLS", ",".join(SYMBOLS))
    SYMBOLS = [s.strip() for s in raw_symbols.split(",") if s.strip()]
    TIMEFRAME = os.environ.get("TIMEFRAME", TIMEFRAME)
    CONFIRM_TIMEFRAME = os.environ.get("CONFIRM_TIMEFRAME", CONFIRM_TIMEFRAME)
    SMA_PERIOD = int(os.environ.get("SMA_PERIOD", SMA_PERIOD))
    RSI_PERIOD = int(os.environ.get("RSI_PERIOD", RSI_PERIOD))
    RSI_BUY_THRESHOLD = float(os.environ.get("RSI_BUY_THRESHOLD", RSI_BUY_THRESHOLD))
    RSI_SELL_THRESHOLD = float(os.environ.get("RSI_SELL_THRESHOLD", RSI_SELL_THRESHOLD))
    SELL_TREND_FILTER = os.environ.get("SELL_TREND_FILTER", "false").lower() in ("1", "true", "yes")
    MACD_FAST = int(os.environ.get("MACD_FAST", MACD_FAST))
    MACD_SLOW = int(os.environ.get("MACD_SLOW", MACD_SLOW))
    MACD_SIGNAL_PERIOD = int(os.environ.get("MACD_SIGNAL_PERIOD", MACD_SIGNAL_PERIOD))
    BB_PERIOD = int(os.environ.get("BB_PERIOD", BB_PERIOD))
    BB_STD = float(os.environ.get("BB_STD", BB_STD))
    VOLUME_SPIKE_PERIOD = int(os.environ.get("VOLUME_SPIKE_PERIOD", VOLUME_SPIKE_PERIOD))
    VOLUME_SPIKE_MULTIPLIER = float(os.environ.get("VOLUME_SPIKE_MULTIPLIER", VOLUME_SPIKE_MULTIPLIER))
    DIVERGENCE_LOOKBACK = int(os.environ.get("DIVERGENCE_LOOKBACK", DIVERGENCE_LOOKBACK))
    _ph = os.environ.get("PRICE_ALERT_HIGH")
    PRICE_ALERT_HIGH = float(_ph) if _ph else None
    _pl = os.environ.get("PRICE_ALERT_LOW")
    PRICE_ALERT_LOW = float(_pl) if _pl else None
    ATH_LOOKBACK_CANDLES = int(os.environ.get("ATH_LOOKBACK_CANDLES", ATH_LOOKBACK_CANDLES))
    ATH_PROXIMITY_PCT = float(os.environ.get("ATH_PROXIMITY_PCT", ATH_PROXIMITY_PCT))
    PRICE_MOVE_PCT_THRESHOLD = float(os.environ.get("PRICE_MOVE_PCT_THRESHOLD", PRICE_MOVE_PCT_THRESHOLD))
    POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", POLL_INTERVAL_SECONDS))
    HEARTBEAT_INTERVAL_HOURS = int(os.environ.get("HEARTBEAT_INTERVAL_HOURS", HEARTBEAT_INTERVAL_HOURS))
    SIGNAL_COOLDOWN_HOURS = int(os.environ.get("SIGNAL_COOLDOWN_HOURS", SIGNAL_COOLDOWN_HOURS))
    CIRCUIT_BREAKER_ERRORS = int(os.environ.get("CIRCUIT_BREAKER_ERRORS", CIRCUIT_BREAKER_ERRORS))
    PROMETHEUS_PORT = int(os.environ.get("PROMETHEUS_PORT", PROMETHEUS_PORT))
    DB_PATH = os.environ.get("DB_PATH", DB_PATH)

    logger.info(
        "Config: EXCHANGE=%s SYMBOLS=%s TIMEFRAME=%s CONFIRM=%s "
        "SMA=%d RSI=%d BUY=%.1f SELL=%.1f SELL_FILTER=%s "
        "MACD=%d/%d/%d BB=%d/%.1f VOL=%d/%.1fx DIV=%d "
        "POLL=%ds HB=%dh COOL=%dh CB=%d PROM=%d DB=%s",
        EXCHANGE_ID, SYMBOLS, TIMEFRAME, CONFIRM_TIMEFRAME or "disabled",
        SMA_PERIOD, RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD, SELL_TREND_FILTER,
        MACD_FAST, MACD_SLOW, MACD_SIGNAL_PERIOD, BB_PERIOD, BB_STD,
        VOLUME_SPIKE_PERIOD, VOLUME_SPIKE_MULTIPLIER, DIVERGENCE_LOOKBACK,
        POLL_INTERVAL_SECONDS, HEARTBEAT_INTERVAL_HOURS, SIGNAL_COOLDOWN_HOURS,
        CIRCUIT_BREAKER_ERRORS, PROMETHEUS_PORT, DB_PATH,
    )


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------


def _init_prometheus() -> None:
    """Register Prometheus metrics and start the HTTP server on PROMETHEUS_PORT."""
    global _prom_signals, _prom_price, _prom_rsi, _prom_fetch_hist, _prom_errors
    if not _PROM_AVAILABLE:
        logger.warning(
            "prometheus_client is not installed; Prometheus metrics disabled. "
            "Install it with: pip install prometheus_client"
        )
        return
    _prom_signals = _prom_client.Counter(
        "signal_bot_signals_total", "Total signals fired", ["symbol", "type"]
    )
    _prom_price = _prom_client.Gauge(
        "signal_bot_price_usd", "Current price (USD)", ["symbol"]
    )
    _prom_rsi = _prom_client.Gauge("signal_bot_rsi", "Current RSI", ["symbol"])
    _prom_fetch_hist = _prom_client.Histogram(
        "signal_bot_fetch_duration_seconds", "OHLCV fetch duration", ["symbol"]
    )
    _prom_errors = _prom_client.Counter(
        "signal_bot_errors_total", "Consecutive polling errors", ["symbol"]
    )
    _prom_client.start_http_server(PROMETHEUS_PORT)
    logger.info("Prometheus /metrics available on port %d", PROMETHEUS_PORT)


# ---------------------------------------------------------------------------
# SQLite — persistent signal history
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create the signals history table if it does not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                signal_type TEXT    NOT NULL,
                price       REAL    NOT NULL,
                rsi         REAL,
                extra       TEXT
            )
            """
        )
        conn.commit()


def log_signal(
    symbol: str,
    signal_type: str,
    price: float,
    rsi: float | None = None,
    extra: dict | None = None,
) -> None:
    """Insert a fired-signal record into the history database."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO signals (timestamp, symbol, signal_type, price, rsi, extra) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    symbol,
                    signal_type,
                    price,
                    rsi,
                    json.dumps(extra) if extra else None,
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("DB write failed: %s", exc)


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def _telegram_chat_ids() -> list[str]:
    """Return all configured Telegram chat IDs (comma-separated env var)."""
    raw = os.environ.get("TELEGRAM_CHAT_ID", "")
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def send_telegram_message(
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Send *text* to all configured Telegram chats.

    Supports HTML formatting (``parse_mode="HTML"``).  Pass an
    ``InlineKeyboardMarkup`` dict as *reply_markup* to attach inline buttons.

    Retries up to ``TELEGRAM_MAX_RETRIES`` times with exponential back-off on
    transient network errors.  Application-level Telegram API errors are raised
    immediately without retrying.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in _telegram_chat_ids():
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        for attempt in range(1, TELEGRAM_MAX_RETRIES + 1):
            try:
                response = requests.post(url, json=payload, timeout=10)
                # Raise on HTTP 4xx/5xx, stripping the URL so the token is
                # never written to logs.
                try:
                    response.raise_for_status()
                except requests.exceptions.HTTPError:
                    raise requests.exceptions.HTTPError(
                        f"Telegram API HTTP error {response.status_code}: {response.text}"
                    ) from None
                data = response.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API error: {data.get('description')}")
                logger.info(
                    "Telegram message sent to %s: %s", chat_id, text.splitlines()[0]
                )
                break
            except requests.exceptions.RequestException as exc:
                if attempt < TELEGRAM_MAX_RETRIES:
                    wait = min(2 ** (attempt - 1), 60)
                    logger.warning(
                        "Telegram send attempt %d/%d to %s failed (%s); retrying in %ds",
                        attempt, TELEGRAM_MAX_RETRIES, chat_id, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    raise


def _telegram_get_updates(offset: int) -> list[dict]:
    """Long-poll the Telegram Bot API for new updates starting at *offset*."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(
            url, params={"offset": offset, "timeout": 10}, timeout=15
        )
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return data.get("result", [])
    except requests.exceptions.RequestException as exc:
        logger.debug("getUpdates failed: %s", exc)
    return []


def _telegram_answer_callback(callback_id: str) -> None:
    """Acknowledge an inline keyboard callback to clear the loading spinner."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_id}, timeout=5)
    except requests.exceptions.RequestException:
        pass  # best-effort — never block the main loop


def _symbol_to_safe(symbol: str) -> str:
    """Convert a trading pair to a safe string suitable for callback data.

    Replaces ``/`` with ``_`` so ``BTC/USDT`` becomes ``BTC_USDT``.
    The inverse is performed in the command handler when decoding callbacks.
    """
    return symbol.replace("/", "_")


def _make_snooze_keyboard(symbol: str, signal_type: str) -> dict:
    """Return an InlineKeyboardMarkup dict with "Snooze 1h" / "Snooze 4h" buttons.

    Callback data format: ``snooze:<symbol_safe>:<signal_type>:<hours>``
    where *symbol_safe* is produced by ``_symbol_to_safe`` (e.g. ``BTC_USDT``).
    """
    sym_safe = _symbol_to_safe(symbol)
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Snooze 1h",
                    "callback_data": f"snooze:{sym_safe}:{signal_type}:1",
                },
                {
                    "text": "Snooze 4h",
                    "callback_data": f"snooze:{sym_safe}:{signal_type}:4",
                },
            ]
        ]
    }


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


def _required_limit() -> int:
    """Number of candles needed to compute every indicator with full warmup."""
    return (
        max(
            SMA_PERIOD + RSI_PERIOD,
            MACD_SLOW + MACD_SIGNAL_PERIOD,
            BB_PERIOD,
            VOLUME_SPIKE_PERIOD,
            ATH_LOOKBACK_CANDLES if ATH_LOOKBACK_CANDLES > 0 else 0,
            DIVERGENCE_LOOKBACK,
        )
        + 50
    )


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV candles for *symbol* from *exchange*.

    Returns a DataFrame with columns:
    ``timestamp``, ``open``, ``high``, ``low``, ``close``, ``volume``.
    """
    tf = timeframe or TIMEFRAME
    lim = limit or _required_limit()
    raw = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=lim)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators and return an enriched DataFrame.

    Added columns:
      sma         — Simple Moving Average (SMA_PERIOD)
      rsi         — RSI (RSI_PERIOD, Wilder EMA smoothing)
      macd        — MACD line (fast EMA − slow EMA)
      macd_signal — MACD signal line (EMA of macd)
      macd_hist   — MACD histogram (macd − macd_signal)
      bb_upper    — Bollinger Band upper
      bb_lower    — Bollinger Band lower
      bb_mid      — Bollinger Band middle (rolling mean, BB_PERIOD)
      vol_mean    — Rolling mean volume (VOLUME_SPIKE_PERIOD)
      ath         — Rolling maximum close (ATH_LOOKBACK_CANDLES; NaN if disabled)
    """
    df = df.copy()

    # Simple Moving Average
    df["sma"] = df["close"].rolling(window=SMA_PERIOD).mean()

    # RSI (Wilder / EMA smoothing via pandas ewm)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    # Guard against division by zero: when avg_loss is 0 (all gains), RSI → 100.
    rs = avg_gain / avg_loss.replace(0, RSI_EPSILON)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL_PERIOD, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Bollinger Bands
    bb_mid = df["close"].rolling(window=BB_PERIOD).mean()
    bb_std = df["close"].rolling(window=BB_PERIOD).std()
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_mid + BB_STD * bb_std
    df["bb_lower"] = bb_mid - BB_STD * bb_std

    # Volume rolling mean
    df["vol_mean"] = df["volume"].rolling(window=VOLUME_SPIKE_PERIOD).mean()

    # Rolling ATH
    if ATH_LOOKBACK_CANDLES > 0:
        df["ath"] = df["close"].rolling(window=ATH_LOOKBACK_CANDLES).max()
    else:
        df["ath"] = float("nan")

    return df


# ---------------------------------------------------------------------------
# Signal helper functions
# ---------------------------------------------------------------------------


def _signal_strength(rsi: float, sma: float, price: float, direction: str) -> int:
    """Return a signal strength score from 1 (weak) to 5 (strong).

    Combines RSI distance from its threshold and price distance from the SMA.
    *direction* must be ``"buy"`` or ``"sell"``.
    """
    if direction == "buy":
        denominator = RSI_BUY_THRESHOLD if RSI_BUY_THRESHOLD > 0 else 1.0
        rsi_score = max(0.0, (RSI_BUY_THRESHOLD - rsi) / denominator) * 2.5
    else:
        denominator = (100 - RSI_SELL_THRESHOLD) if RSI_SELL_THRESHOLD < 100 else 1.0
        rsi_score = max(0.0, (rsi - RSI_SELL_THRESHOLD) / denominator) * 2.5

    if not pd.isna(sma) and sma > 0:
        # Cap price/SMA contribution at 2.5 (5% move = max score)
        price_score = min(abs((price - sma) / sma) * 100 / 5, 1.0) * 2.5
    else:
        price_score = 0.0

    return max(1, min(5, round(rsi_score + price_score)))


def _is_volume_spike(latest: pd.Series) -> bool:
    """Return True if the latest candle's volume qualifies as a spike."""
    vol = float(latest.get("volume", float("nan")))
    vol_mean = float(latest.get("vol_mean", float("nan")))
    return (
        not pd.isna(vol)
        and not pd.isna(vol_mean)
        and vol_mean > 0
        and vol >= VOLUME_SPIKE_MULTIPLIER * vol_mean
    )


# Price tolerance for divergence detection: price is considered "near" an
# extreme when it is within this percentage of the first-half pivot price.
_DIVERGENCE_PRICE_TOLERANCE = 0.02  # 2%


def _detect_divergence(df: pd.DataFrame) -> str | None:
    """Scan the last DIVERGENCE_LOOKBACK candles for RSI/price divergence.

    Uses a split-window approach: compares an extreme in the first half of
    the window with the current (last) candle to identify hidden divergence.

    Returns:
      ``"bullish"``  — price near/at a window low but RSI higher than at that low
      ``"bearish"``  — price near/at a window high but RSI lower than at that high
      ``None``        — no divergence detected
    """
    window = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
    if len(window) < 4:
        return None
    closes = window["close"].values
    rsis = window["rsi"].values
    if any(pd.isna(r) for r in rsis):
        return None

    mid = len(window) // 2
    current_price = closes[-1]
    current_rsi = rsis[-1]
    tolerance = _DIVERGENCE_PRICE_TOLERANCE

    # Bullish divergence: current price near the first-half low, RSI relatively higher
    low_idx = int(closes[:mid].argmin())
    first_half_price_low = closes[low_idx]
    first_half_rsi_at_low = rsis[low_idx]
    if current_price <= first_half_price_low * (1 + tolerance) and current_rsi > first_half_rsi_at_low:
        return "bullish"

    # Bearish divergence: current price near the first-half high, RSI relatively lower
    high_idx = int(closes[:mid].argmax())
    first_half_price_high = closes[high_idx]
    first_half_rsi_at_high = rsis[high_idx]
    if current_price >= first_half_price_high * (1 - tolerance) and current_rsi < first_half_rsi_at_high:
        return "bearish"

    return None


def _confirm_signal(
    exchange: ccxt.Exchange,
    symbol: str,
    direction: str,
) -> bool:
    """Check CONFIRM_TIMEFRAME for the same trend/RSI conditions.

    Returns ``True`` if the signal is confirmed or if CONFIRM_TIMEFRAME is
    disabled.  Returns ``False`` only when the higher-timeframe check
    explicitly fails.  Falls back to ``True`` on fetch errors so a transient
    network issue never silently suppresses signals.
    """
    if not CONFIRM_TIMEFRAME:
        return True
    try:
        conf_df = fetch_ohlcv(exchange, symbol, timeframe=CONFIRM_TIMEFRAME)
        conf_df = calculate_indicators(conf_df)
        latest = conf_df.iloc[-1]
        price = latest["close"]
        sma = latest["sma"]
        rsi = latest["rsi"]
        if pd.isna(sma) or pd.isna(rsi):
            return True  # insufficient data — don't suppress
        if direction == "buy":
            return bool(price > sma and rsi < RSI_BUY_THRESHOLD)
        return bool(rsi > RSI_SELL_THRESHOLD)
    except (ccxt.NetworkError, ccxt.ExchangeError, requests.exceptions.RequestException,
            KeyError, IndexError, ValueError) as exc:
        logger.warning(
            "[%s] Confirmation fetch on %s failed (%s); proceeding without confirmation.",
            symbol, CONFIRM_TIMEFRAME, exc,
        )
        return True


def _can_alert(state: dict, signal_type: str) -> bool:
    """Return True if *signal_type* is not globally muted, snoozed, or on cooldown."""
    now = datetime.now(UTC)
    with _mute_lock:
        global _global_mute_until
        if _global_mute_until is not None and now < _global_mute_until:
            return False
    snooze = state["snooze_until"].get(signal_type)
    if snooze is not None and now < snooze:
        return False
    last = state["last_signal"].get(signal_type)
    if last is not None and (now - last) < timedelta(hours=SIGNAL_COOLDOWN_HOURS):
        return False
    return True


def _record_signal(
    state: dict,
    signal_type: str,
    symbol: str,
    price: float,
    rsi: float | None,
    extra: dict | None = None,
) -> None:
    """Update in-memory state and persist the fired signal to the history DB."""
    state["last_signal"][signal_type] = datetime.now(UTC)
    state["signals_counter"][signal_type] = state["signals_counter"].get(signal_type, 0) + 1
    log_signal(symbol, signal_type, price, rsi, extra)
    if _prom_signals is not None:
        try:
            _prom_signals.labels(symbol=symbol, type=signal_type).inc()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Main signal evaluation
# ---------------------------------------------------------------------------


def check_and_notify(
    exchange: ccxt.Exchange,
    symbol: str,
    df: pd.DataFrame,
    state: dict,
) -> None:
    """Evaluate all signal types for *symbol* and send Telegram alerts as needed."""
    latest = df.iloc[-1]
    price: float = latest["close"]
    sma: float = latest["sma"]
    rsi: float = latest["rsi"]
    volume_spike = _is_volume_spike(latest)
    vol_tag = "🔥 <i>Volume spike</i>\n" if volume_spike else ""

    if pd.isna(sma) or pd.isna(rsi):
        logger.warning("[%s] Not enough data to calculate indicators yet.", symbol)
        return

    logger.info(
        "[%s] Price: %.2f | SMA%d: %.2f | RSI: %.2f",
        symbol, price, SMA_PERIOD, sma, rsi,
    )

    # Update Prometheus gauges
    if _prom_price is not None:
        try:
            _prom_price.labels(symbol=symbol).set(price)
            _prom_rsi.labels(symbol=symbol).set(rsi)
        except Exception:  # noqa: BLE001
            pass

    # ── BUY (RSI oversold + price above SMA) ────────────────────────────────
    if price > sma and rsi < RSI_BUY_THRESHOLD:
        if _can_alert(state, "buy") and _confirm_signal(exchange, symbol, "buy"):
            strength = _signal_strength(rsi, sma, price, "buy")
            stars = "⭐" * strength
            message = (
                f"🟢 <b>BUY SIGNAL — {symbol}</b>\n\n"
                f"Price:    <b>${price:,.2f}</b>\n"
                f"RSI:      <b>{rsi:.2f}</b>\n"
                f"SMA{SMA_PERIOD}: <b>${sma:,.2f}</b>\n"
                f"{vol_tag}"
                f"Strength: {stars} ({strength}/5)"
            )
            send_telegram_message(message, reply_markup=_make_snooze_keyboard(symbol, "buy"))
            _record_signal(state, "buy", symbol, price, rsi)

    # ── SELL (RSI overbought; optional price < SMA filter) ──────────────────
    sell_condition = rsi > RSI_SELL_THRESHOLD
    if SELL_TREND_FILTER:
        sell_condition = sell_condition and price < sma
    if sell_condition:
        if _can_alert(state, "sell") and _confirm_signal(exchange, symbol, "sell"):
            strength = _signal_strength(rsi, sma, price, "sell")
            stars = "⭐" * strength
            message = (
                f"🔴 <b>SELL SIGNAL — {symbol}</b>\n\n"
                f"Price: <b>${price:,.2f}</b>\n"
                f"RSI:   <b>{rsi:.2f}</b>\n"
                f"{vol_tag}"
                f"Strength: {stars} ({strength}/5)"
            )
            send_telegram_message(message, reply_markup=_make_snooze_keyboard(symbol, "sell"))
            _record_signal(state, "sell", symbol, price, rsi)

    # ── MACD crossover ───────────────────────────────────────────────────────
    if len(df) >= 2:
        prev_hist = df.iloc[-2]["macd_hist"]
        curr_hist = latest["macd_hist"]
        if not pd.isna(prev_hist) and not pd.isna(curr_hist):
            if prev_hist < 0 < curr_hist:
                # Histogram flipped from negative to positive → bullish crossover
                if _can_alert(state, "macd_buy"):
                    msg = (
                        f"📈 <b>MACD BUY — {symbol}</b>\n\n"
                        f"Price:     <b>${price:,.2f}</b>\n"
                        f"Histogram: <b>{curr_hist:+.4f}</b>\n"
                        f"{vol_tag}"
                        f"RSI: {rsi:.2f}"
                    )
                    send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, "macd_buy"))
                    _record_signal(state, "macd_buy", symbol, price, rsi, {"macd_hist": curr_hist})
            elif prev_hist > 0 > curr_hist:
                # Histogram flipped from positive to negative → bearish crossover
                if _can_alert(state, "macd_sell"):
                    msg = (
                        f"📉 <b>MACD SELL — {symbol}</b>\n\n"
                        f"Price:     <b>${price:,.2f}</b>\n"
                        f"Histogram: <b>{curr_hist:+.4f}</b>\n"
                        f"{vol_tag}"
                        f"RSI: {rsi:.2f}"
                    )
                    send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, "macd_sell"))
                    _record_signal(state, "macd_sell", symbol, price, rsi, {"macd_hist": curr_hist})

    # ── Bollinger Band breaks ────────────────────────────────────────────────
    bb_upper = latest["bb_upper"]
    bb_lower = latest["bb_lower"]
    if not pd.isna(bb_upper) and not pd.isna(bb_lower):
        if price > bb_upper:
            if _can_alert(state, "bb_upper"):
                msg = (
                    f"📊 <b>BB Upper Break — {symbol}</b>\n\n"
                    f"Price:    <b>${price:,.2f}</b>  (above <b>${bb_upper:,.2f}</b>)\n"
                    f"{vol_tag}"
                    f"RSI: {rsi:.2f}"
                )
                send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, "bb_upper"))
                _record_signal(state, "bb_upper", symbol, price, rsi, {"bb_upper": bb_upper})
        elif price < bb_lower:
            if _can_alert(state, "bb_lower"):
                msg = (
                    f"📊 <b>BB Lower Break — {symbol}</b>\n\n"
                    f"Price:    <b>${price:,.2f}</b>  (below <b>${bb_lower:,.2f}</b>)\n"
                    f"{vol_tag}"
                    f"RSI: {rsi:.2f}"
                )
                send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, "bb_lower"))
                _record_signal(state, "bb_lower", symbol, price, rsi, {"bb_lower": bb_lower})

    # ── RSI/price divergence ────────────────────────────────────────────────
    divergence = _detect_divergence(df)
    if divergence:
        div_key = f"divergence_{divergence}"
        if _can_alert(state, div_key):
            label = "Bullish" if divergence == "bullish" else "Bearish"
            msg = (
                f"🔀 <b>{label} Divergence — {symbol}</b>\n\n"
                f"Price: <b>${price:,.2f}</b>\n"
                f"RSI:   <b>{rsi:.2f}</b>\n"
                f"(RSI/price divergence over last {DIVERGENCE_LOOKBACK} candles)"
            )
            send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, div_key))
            _record_signal(state, div_key, symbol, price, rsi)

    # ── Large single-candle move ─────────────────────────────────────────────
    if PRICE_MOVE_PCT_THRESHOLD > 0:
        open_price: float = latest["open"]
        if open_price > 0:
            move_pct = abs((price - open_price) / open_price) * 100
            if move_pct >= PRICE_MOVE_PCT_THRESHOLD and _can_alert(state, "price_move"):
                arrow = "📈" if price >= open_price else "📉"
                msg = (
                    f"⚡ <b>Large Candle Move — {symbol}</b>\n\n"
                    f"{arrow} Move:  <b>{move_pct:.2f}%</b>\n"
                    f"Open:  <b>${open_price:,.2f}</b>\n"
                    f"Close: <b>${price:,.2f}</b>"
                )
                send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, "price_move"))
                _record_signal(state, "price_move", symbol, price, rsi, {"move_pct": move_pct})

    # ── ATH proximity ────────────────────────────────────────────────────────
    if ATH_LOOKBACK_CANDLES > 0:
        ath: float = latest["ath"]
        if not pd.isna(ath) and ath > 0:
            distance_pct = (ath - price) / ath * 100
            in_zone = distance_pct <= ATH_PROXIMITY_PCT
            was_in_zone = state.get("ath_proximity_in_zone", False)
            state["ath_proximity_in_zone"] = in_zone
            # Alert only on entry into the proximity zone
            if in_zone and not was_in_zone and _can_alert(state, "ath_proximity"):
                msg = (
                    f"🏔 <b>ATH Proximity — {symbol}</b>\n\n"
                    f"Price: <b>${price:,.2f}</b>\n"
                    f"Rolling ATH ({ATH_LOOKBACK_CANDLES} candles): <b>${ath:,.2f}</b>\n"
                    f"Distance: <b>{distance_pct:.2f}%</b> below ATH"
                )
                send_telegram_message(msg, reply_markup=_make_snooze_keyboard(symbol, "ath_proximity"))
                _record_signal(state, "ath_proximity", symbol, price, rsi, {"ath": ath})

    # ── One-time price level alerts ──────────────────────────────────────────
    last_price = state.get("last_price")
    if PRICE_ALERT_HIGH is not None and not state.get("price_alert_high_fired", False):
        if last_price is not None and last_price < PRICE_ALERT_HIGH <= price:
            msg = (
                f"🎯 <b>Price Alert ↑ — {symbol}</b>\n\n"
                f"Price crossed above <b>${PRICE_ALERT_HIGH:,.2f}</b>\n"
                f"Current: <b>${price:,.2f}</b>"
            )
            send_telegram_message(msg)
            log_signal(symbol, "price_alert_high", price, rsi)
            state["price_alert_high_fired"] = True

    if PRICE_ALERT_LOW is not None and not state.get("price_alert_low_fired", False):
        if last_price is not None and last_price > PRICE_ALERT_LOW >= price:
            msg = (
                f"🎯 <b>Price Alert ↓ — {symbol}</b>\n\n"
                f"Price crossed below <b>${PRICE_ALERT_LOW:,.2f}</b>\n"
                f"Current: <b>${price:,.2f}</b>"
            )
            send_telegram_message(msg)
            log_signal(symbol, "price_alert_low", price, rsi)
            state["price_alert_low_fired"] = True

    state["last_price"] = price


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------


def send_daily_summary(symbol: str, df: pd.DataFrame, state: dict) -> None:
    """Send an enriched heartbeat with the latest market snapshot and signal counts."""
    latest = df.iloc[-1]
    price: float = latest["close"]
    sma: float = latest["sma"]
    rsi: float = latest["rsi"]
    bb_upper: float = latest["bb_upper"]
    bb_lower: float = latest["bb_lower"]
    bb_mid: float = latest["bb_mid"]

    rsi_str = f"{rsi:.2f}" if not pd.isna(rsi) else "N/A"

    if not pd.isna(sma):
        pct = (price - sma) / sma * 100
        direction = "above" if pct >= 0 else "below"
        sma_str = f"<b>${sma:,.2f}</b> ({direction} by {abs(pct):.1f}%)"
    else:
        sma_str = "N/A"

    if not pd.isna(bb_upper) and not pd.isna(bb_lower) and not pd.isna(bb_mid) and bb_mid > 0:
        bb_width = (bb_upper - bb_lower) / bb_mid * 100
        bb_str = f"<b>${bb_lower:,.0f}</b>–<b>${bb_upper:,.0f}</b>  (width {bb_width:.1f}%)"
    else:
        bb_str = "N/A"

    counter = state["signals_counter"]
    sig_lines = "\n".join(
        f"  {k}: {v}" for k, v in sorted(counter.items()) if v > 0
    ) or "  (none)"

    message = (
        f"✅ <b>Daily Summary — {symbol}</b>\n\n"
        f"Price:   <b>${price:,.2f}</b>\n"
        f"RSI:     <b>{rsi_str}</b>\n"
        f"SMA{SMA_PERIOD}:  {sma_str}\n"
        f"BB:      {bb_str}\n\n"
        f"Signals (last {HEARTBEAT_INTERVAL_HOURS}h):\n{sig_lines}"
    )
    send_telegram_message(message)


# ---------------------------------------------------------------------------
# Telegram command handler (background thread)
# ---------------------------------------------------------------------------


def _telegram_command_thread(symbol_states: dict) -> None:
    """Poll getUpdates and handle /status, /config, /mute, and snooze callbacks.

    Runs as a daemon thread.  Uses ``_shutdown`` to know when to exit.
    """
    global _global_mute_until
    offset = 0
    logger.info("Telegram command handler started.")
    while not _shutdown.is_set():
        updates = _telegram_get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1

            # ── Inline keyboard callback (snooze buttons) ────────────────────
            if "callback_query" in update:
                cq = update["callback_query"]
                _telegram_answer_callback(cq["id"])
                data = cq.get("data", "")
                if data.startswith("snooze:"):
                    parts = data.split(":")
                    if len(parts) == 4:
                        _, sym_safe, sig_type, hours_str = parts
                        try:
                            hours = int(hours_str)
                        except ValueError:
                            continue
                        until = datetime.now(UTC) + timedelta(hours=hours)
                        # Match symbol by safe name (BTC_USDT → BTC/USDT)
                        matched = next(
                            (s for s in symbol_states if _symbol_to_safe(s) == sym_safe),
                            None,
                        )
                        if matched:
                            symbol_states[matched]["snooze_until"][sig_type] = until
                            logger.info(
                                "Snoozed %s/%s for %dh (until %s UTC).",
                                matched, sig_type, hours,
                                until.strftime("%H:%M"),
                            )
                continue

            # ── Text commands ────────────────────────────────────────────────
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            if not text.startswith("/"):
                continue

            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/status":
                lines: list[str] = []
                for sym, st in symbol_states.items():
                    last_df = st.get("last_df")
                    if last_df is None:
                        lines.append(f"<b>{sym}</b>: no data yet")
                        continue
                    row = last_df.iloc[-1]
                    p = row["close"]
                    s = row["sma"]
                    r = row["rsi"]
                    rsi_s = f"{r:.2f}" if not pd.isna(r) else "N/A"
                    sma_s = f"${s:,.2f}" if not pd.isna(s) else "N/A"
                    lines.append(
                        f"<b>{sym}</b>: ${p:,.2f}  RSI {rsi_s}  SMA{SMA_PERIOD} {sma_s}"
                    )
                try:
                    send_telegram_message("📊 <b>Status</b>\n\n" + "\n".join(lines))
                except requests.exceptions.RequestException as exc:
                    logger.warning("Failed to send /status reply: %s", exc)

            elif cmd == "/config":
                cfg = (
                    "⚙️ <b>Configuration</b>\n\n"
                    f"Exchange:      {EXCHANGE_ID}\n"
                    f"Symbols:       {', '.join(SYMBOLS)}\n"
                    f"Timeframe:     {TIMEFRAME}\n"
                    f"Confirm TF:    {CONFIRM_TIMEFRAME or 'disabled'}\n"
                    f"SMA period:    {SMA_PERIOD}\n"
                    f"RSI period:    {RSI_PERIOD}\n"
                    f"RSI buy &lt;{RSI_BUY_THRESHOLD} / sell &gt;{RSI_SELL_THRESHOLD}\n"
                    f"Sell filter:   {SELL_TREND_FILTER}\n"
                    f"MACD:          {MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL_PERIOD}\n"
                    f"BB:            {BB_PERIOD} / {BB_STD}σ\n"
                    f"Vol spike:     {VOLUME_SPIKE_MULTIPLIER}× over {VOLUME_SPIKE_PERIOD} periods\n"
                    f"Divergence:    {DIVERGENCE_LOOKBACK} candles\n"
                    f"Price alert ↑: {PRICE_ALERT_HIGH or 'disabled'}\n"
                    f"Price alert ↓: {PRICE_ALERT_LOW or 'disabled'}\n"
                    f"ATH lookback:  {ATH_LOOKBACK_CANDLES or 'disabled'}\n"
                    f"ATH proximity: {ATH_PROXIMITY_PCT}%\n"
                    f"Move thresh:   {PRICE_MOVE_PCT_THRESHOLD or 'disabled'}%\n"
                    f"Poll:          {POLL_INTERVAL_SECONDS}s\n"
                    f"Heartbeat:     {HEARTBEAT_INTERVAL_HOURS}h\n"
                    f"Cooldown:      {SIGNAL_COOLDOWN_HOURS}h"
                )
                try:
                    send_telegram_message(cfg)
                except requests.exceptions.RequestException as exc:
                    logger.warning("Failed to send /config reply: %s", exc)

            elif cmd == "/mute":
                try:
                    hours = float(arg) if arg else 1.0
                    until = datetime.now(UTC) + timedelta(hours=hours)
                    with _mute_lock:
                        _global_mute_until = until
                    try:
                        send_telegram_message(
                            f"🔕 All alerts muted for <b>{hours:.1f}h</b> "
                            f"(until {until.strftime('%H:%M UTC')})"
                        )
                    except requests.exceptions.RequestException as exc:
                        logger.warning("Failed to send /mute confirmation: %s", exc)
                    logger.info("Global mute set for %.1fh.", hours)
                except ValueError:
                    try:
                        send_telegram_message("Usage: /mute &lt;hours&gt;  (e.g. /mute 2)")
                    except requests.exceptions.RequestException:
                        pass

        _shutdown.wait(timeout=5)


# ---------------------------------------------------------------------------
# SIGTERM handler
# ---------------------------------------------------------------------------


def _handle_sigterm(signum: int, frame: Any) -> None:  # noqa: ARG001
    """Set the shutdown event when SIGTERM is received."""
    logger.info("SIGTERM received; initiating graceful shutdown.")
    _shutdown.set()


# ---------------------------------------------------------------------------
# Per-symbol state factory
# ---------------------------------------------------------------------------

_SIGNAL_TYPES = (
    "buy", "sell",
    "macd_buy", "macd_sell",
    "bb_upper", "bb_lower",
    "divergence_bullish", "divergence_bearish",
    "price_move", "ath_proximity",
)


def _make_symbol_state() -> dict:
    """Return a fresh per-symbol mutable state dictionary."""
    return {
        "last_signal":   {t: None for t in _SIGNAL_TYPES},
        "snooze_until":  {t: None for t in _SIGNAL_TYPES},
        "signals_counter": {t: 0  for t in _SIGNAL_TYPES},
        "last_df": None,
        "last_price": None,
        "consecutive_errors": 0,
        "circuit_breaker_alerted": False,
        "circuit_breaker_until": None,
        "ath_proximity_in_zone": False,
        "price_alert_high_fired": False,
        "price_alert_low_fired": False,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    load_dotenv()   # load .env file if present (no-op when vars are already set)
    _load_config()  # apply environment-variable overrides

    missing = [v for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Required environment variable(s) not set: {', '.join(missing)}\n"
            "See README.md for setup instructions."
        )

    init_db()

    if PROMETHEUS_PORT > 0:
        _init_prometheus()

    # Register SIGTERM handler for graceful container shutdown
    _signal_module.signal(_signal_module.SIGTERM, _handle_sigterm)

    if EXCHANGE_ID not in ccxt.exchanges:
        raise ValueError(
            f"Unknown exchange '{EXCHANGE_ID}'. "
            f"Valid options include: {', '.join(sorted(ccxt.exchanges)[:10])}, ..."
        )
    exchange = getattr(ccxt, EXCHANGE_ID)()
    symbol_states = {sym: _make_symbol_state() for sym in SYMBOLS}

    # Start command handler in a daemon thread
    cmd_thread = threading.Thread(
        target=_telegram_command_thread,
        args=(symbol_states,),
        daemon=True,
        name="telegram-cmd",
    )
    cmd_thread.start()

    logger.info("Signal bot starting (exchange=%s, symbols=%s).", EXCHANGE_ID, SYMBOLS)
    send_telegram_message(
        f"🤖 <b>Signal Bot started</b>\n"
        f"Exchange: {EXCHANGE_ID}  |  Symbols: {', '.join(SYMBOLS)}"
    )

    last_heartbeat = datetime.now(UTC)

    while not _shutdown.is_set():
        for symbol in SYMBOLS:
            if _shutdown.is_set():
                break

            state = symbol_states[symbol]

            # Skip symbol while circuit breaker cooldown is active
            cb_until = state.get("circuit_breaker_until")
            if cb_until is not None and datetime.now(UTC) < cb_until:
                continue

            try:
                t0 = time.monotonic()
                df = fetch_ohlcv(exchange, symbol)
                fetch_duration = time.monotonic() - t0
                if _prom_fetch_hist is not None:
                    try:
                        _prom_fetch_hist.labels(symbol=symbol).observe(fetch_duration)
                    except Exception:  # noqa: BLE001
                        pass

                df = calculate_indicators(df)
                state["last_df"] = df

                # Reset circuit breaker counters on a successful fetch
                state["consecutive_errors"] = 0
                state["circuit_breaker_alerted"] = False
                state["circuit_breaker_until"] = None

                check_and_notify(exchange, symbol, df, state)

            except requests.exceptions.RequestException as exc:
                logger.error("[%s] Telegram request failed: %s", symbol, exc)
            except ccxt.NetworkError as exc:
                logger.error("[%s] Exchange network error: %s", symbol, exc)
                state["consecutive_errors"] += 1
                if _prom_errors is not None:
                    try:
                        _prom_errors.labels(symbol=symbol).inc()
                    except Exception:  # noqa: BLE001
                        pass
            except (KeyboardInterrupt, SystemExit):
                _shutdown.set()
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] Unexpected error: %s", symbol, exc)
                state["consecutive_errors"] += 1
                if _prom_errors is not None:
                    try:
                        _prom_errors.labels(symbol=symbol).inc()
                    except Exception:  # noqa: BLE001
                        pass

            # Circuit breaker: alert and pause this symbol after too many errors
            if (
                state["consecutive_errors"] >= CIRCUIT_BREAKER_ERRORS
                and not state["circuit_breaker_alerted"]
            ):
                pause = POLL_INTERVAL_SECONDS * 5
                state["circuit_breaker_alerted"] = True
                state["circuit_breaker_until"] = (
                    datetime.now(UTC) + timedelta(seconds=pause)
                )
                try:
                    send_telegram_message(
                        f"🚨 <b>Circuit Breaker — {symbol}</b>\n\n"
                        f"{state['consecutive_errors']} consecutive errors detected.\n"
                        f"Pausing {symbol} for {pause}s before retrying."
                    )
                except Exception:  # noqa: BLE001
                    pass

        # Heartbeat / daily summary (fires once per cycle, covers all symbols)
        now = datetime.now(UTC)
        if now - last_heartbeat >= timedelta(hours=HEARTBEAT_INTERVAL_HOURS):
            for symbol in SYMBOLS:
                state = symbol_states[symbol]
                if state["last_df"] is not None:
                    try:
                        send_daily_summary(symbol, state["last_df"], state)
                    except requests.exceptions.RequestException as exc:
                        logger.error("[%s] Failed to send daily summary: %s", symbol, exc)
                state["signals_counter"] = {t: 0 for t in _SIGNAL_TYPES}
            last_heartbeat = now

        _shutdown.wait(timeout=POLL_INTERVAL_SECONDS)

    # Graceful shutdown — send a final Telegram message
    logger.info("Shutting down.")
    try:
        send_telegram_message("🛑 <b>Signal Bot stopped</b>")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
