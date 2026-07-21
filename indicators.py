"""Pure indicator functions. No I/O, no state — pandas in, pandas out.

Conventions (every function follows all of them):

* Input is either a float Series or the canonical OHLCV DataFrame produced by
  kite_client.candles_to_dataframe (columns open/high/low/close/volume,
  tz-aware UTC index of candle START times, sorted ascending).
* Output is aligned to the input index. Warm-up rows (not enough history yet)
  are NaN — downstream code treats NaN as "no signal", NEVER as a value.
* RSI and ATR use Wilder's smoothing (alpha = 1/period), matching what
  TradingView and Kite's own charts display, so the user can eyeball-verify.
* Bollinger Bands use population standard deviation (ddof=0), the classic
  Bollinger definition.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import IST

# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average. NaN until `period` values exist."""
    _require_period(period)
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (span=period, the charting convention).

    adjust=False gives the recursive EMA traders expect. min_periods=period
    masks the earliest values, but note an EMA needs roughly 4-5x `period`
    candles of history before it converges to the "true" value — which is why
    signals.min_candles_required() asks for 5x the largest lookback.
    """
    _require_period(period)
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Oscillators
# ---------------------------------------------------------------------------


def rsi(series: pd.Series, period: int) -> pd.Series:
    """Relative Strength Index, Wilder-smoothed, in [0, 100]."""
    _require_period(period)
    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    # Wilder's smoothing is an EMA with alpha = 1/period.
    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    # Where avg_loss == 0 the market only rose: RSI is 100 by definition
    # (guards the division rather than emitting inf).
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)
    # Keep the warm-up masked even after the where() above.
    out[avg_gain.isna() | avg_loss.isna()] = np.nan
    return out


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """MACD: returns DataFrame with columns line, signal, histogram."""
    if fast >= slow:
        raise ValueError(f"macd fast ({fast}) must be < slow ({slow})")
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {"line": line, "signal": signal_line, "histogram": line - signal_line}
    )


# ---------------------------------------------------------------------------
# Volatility / bands
# ---------------------------------------------------------------------------


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range, Wilder-smoothed."""
    _require_period(period)
    _require_ohlc(df)
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(close: pd.Series, period: int, std: float) -> pd.DataFrame:
    """Bollinger Bands: columns upper, middle, lower. Population std (ddof=0)."""
    _require_period(period)
    if std <= 0:
        raise ValueError(f"bbands std must be > 0, got {std}")
    middle = sma(close, period)
    deviation = close.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {"upper": middle + std * deviation, "middle": middle, "lower": middle - std * deviation}
    )


def supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.DataFrame:
    """Supertrend: columns line (the stop level) and direction (+1 up / -1 down).

    Classic formulation: bands at hl2 +/- multiplier*ATR, tightened
    ratchet-style, flipping direction when close breaks the opposite band.
    Implemented as an explicit loop — the recurrence cannot be vectorized,
    and our data sizes (a few thousand candles) make the loop cost trivial.
    """
    _require_period(period)
    if multiplier <= 0:
        raise ValueError(f"supertrend multiplier must be > 0, got {multiplier}")
    _require_ohlc(df)

    atr_values = atr(df, period).to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr_values
    lower_basic = hl2 - multiplier * atr_values

    n = len(df)
    line = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)

    started = False
    for i in range(n):
        if np.isnan(atr_values[i]):
            continue  # still in ATR warm-up
        if not started:
            # First computable candle: seed with the basic bands, assume up.
            final_upper[i], final_lower[i] = upper_basic[i], lower_basic[i]
            direction[i], line[i] = 1.0, lower_basic[i]
            started = True
            continue

        # Bands only ratchet tighter unless price closed beyond them.
        final_upper[i] = (
            upper_basic[i]
            if upper_basic[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower_basic[i]
            if lower_basic[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]
            else final_lower[i - 1]
        )

        if close[i] > final_upper[i]:
            direction[i] = 1.0
        elif close[i] < final_lower[i]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]
        line[i] = final_lower[i] if direction[i] > 0 else final_upper[i]

    return pd.DataFrame({"line": line, "direction": direction}, index=df.index)


# ---------------------------------------------------------------------------
# Volume-anchored
# ---------------------------------------------------------------------------


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP: cumulative typical-price x volume, reset each IST day.

    Anchoring to the IST session (not a rolling window) is what intraday
    traders mean by VWAP. On the 'day' timeframe each session is one candle,
    so VWAP degenerates to that candle's typical price — valid, just trivial.
    """
    _require_ohlc(df)
    if df.empty:
        return pd.Series(dtype=float, index=df.index)
    if df.index.tz is None:
        raise ValueError("vwap requires a tz-aware UTC index (got naive timestamps)")

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    weighted = typical * df["volume"]
    session = df.index.tz_convert(IST).date  # groups candles by IST trading day
    cum_weighted = weighted.groupby(session).cumsum()
    cum_volume = df["volume"].groupby(session).cumsum()
    # A zero-volume session start would divide by zero; emit NaN (no signal).
    return cum_weighted / cum_volume.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Shared guards
# ---------------------------------------------------------------------------


def _require_period(period: int) -> None:
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        raise ValueError(f"period must be a positive integer, got {period!r}")


def _require_ohlc(df: pd.DataFrame) -> None:
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise ValueError(
            f"DataFrame is missing OHLCV column(s): {sorted(missing)}. "
            "Use kite_client.candles_to_dataframe to build candle frames."
        )
