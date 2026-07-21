"""Unit tests for indicators.py — values checked against hand computation."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from indicators import (  # noqa: E402
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
    supertrend,
    vwap,
)

IST = ZoneInfo("Asia/Kolkata")


def utc_index(n: int, start_ist: datetime | None = None, step_min: int = 15) -> pd.DatetimeIndex:
    """n consecutive intraday candle timestamps, IST session, tz UTC."""
    start = start_ist or datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    ts = [start + timedelta(minutes=step_min * i) for i in range(n)]
    return pd.DatetimeIndex([t.astimezone(ZoneInfo("UTC")) for t in ts], name="ts")


def ohlcv(closes, highs=None, lows=None, volumes=None, index=None) -> pd.DataFrame:
    closes = pd.Series(closes, dtype=float)
    n = len(closes)
    df = pd.DataFrame(
        {
            "open": closes,
            "high": highs if highs is not None else closes + 1,
            "low": lows if lows is not None else closes - 1,
            "close": closes,
            "volume": volumes if volumes is not None else [1000.0] * n,
        }
    )
    df.index = index if index is not None else utc_index(n)
    return df


# ---------------------------------------------------------------------------
# SMA / EMA
# ---------------------------------------------------------------------------


def test_sma_matches_hand_computation() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert out.iloc[:2].isna().all()  # warm-up
    assert out.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert out.iloc[4] == pytest.approx(4.0)   # (3+4+5)/3


def test_ema_recursive_formula() -> None:
    s = pd.Series([10.0, 11.0, 12.0, 13.0])
    out = ema(s, 3)  # alpha = 2/(3+1) = 0.5
    # Recursive from the first value: 10 -> 10.5 -> 11.25 -> 12.125,
    # with the first period-1 values masked as warm-up.
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == pytest.approx(11.25)
    assert out.iloc[3] == pytest.approx(12.125)


def test_period_validation() -> None:
    s = pd.Series([1.0, 2.0])
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ValueError):
            sma(s, bad)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def test_rsi_all_gains_is_100() -> None:
    s = pd.Series(np.arange(1.0, 40.0))  # strictly rising
    out = rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_approaches_0() -> None:
    s = pd.Series(np.arange(40.0, 1.0, -1.0))  # strictly falling
    out = rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_bounded_and_warmup_nan() -> None:
    rng = np.random.default_rng(42)
    s = pd.Series(100 + rng.normal(0, 1, 200).cumsum())
    out = rsi(s, 14)
    assert out.iloc[:14].isna().all()
    valid = out.dropna()
    assert ((valid >= 0) & (valid <= 100)).all()


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


def test_macd_line_is_fast_ema_minus_slow_ema() -> None:
    rng = np.random.default_rng(7)
    s = pd.Series(100 + rng.normal(0, 1, 100).cumsum())
    frame = macd(s, 12, 26, 9)
    expected = ema(s, 12) - ema(s, 26)
    pd.testing.assert_series_equal(frame["line"], expected, check_names=False)
    # histogram = line - signal wherever both exist
    both = frame.dropna()
    assert np.allclose(both["histogram"], both["line"] - both["signal"])


def test_macd_rejects_fast_ge_slow() -> None:
    with pytest.raises(ValueError):
        macd(pd.Series([1.0, 2.0]), 26, 12, 9)


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def test_atr_constant_range_converges_to_range() -> None:
    n = 300
    df = ohlcv(
        closes=[100.0] * n,
        highs=[101.0] * n,
        lows=[99.0] * n,
    )
    out = atr(df, 14)
    # TR is 2.0 every candle (h-l dominates), so ATR converges to 2.0.
    assert out.iloc[-1] == pytest.approx(2.0, rel=1e-6)
    # Wilder convention: TR exists from the FIRST candle (high-low needs no
    # previous close), so ATR(14) is first valid at index 13.
    assert out.iloc[:13].isna().all()
    assert not np.isnan(out.iloc[13])


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


def test_bollinger_bands_hand_computation() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    frame = bollinger_bands(s, 3, 2.0)
    # Window [3,4,5]: mean 4, population std = sqrt(2/3).
    expected_std = np.sqrt(2.0 / 3.0)
    assert frame["middle"].iloc[4] == pytest.approx(4.0)
    assert frame["upper"].iloc[4] == pytest.approx(4.0 + 2 * expected_std)
    assert frame["lower"].iloc[4] == pytest.approx(4.0 - 2 * expected_std)


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------


def test_supertrend_direction_in_strong_trends() -> None:
    n = 120
    up = ohlcv(closes=list(np.linspace(100, 220, n)))
    down = ohlcv(closes=list(np.linspace(220, 100, n)))

    st_up = supertrend(up, 10, 3.0)
    st_down = supertrend(down, 10, 3.0)

    assert st_up["direction"].iloc[-1] == 1.0
    assert st_up["line"].iloc[-1] < up["close"].iloc[-1]  # stop rides below

    assert st_down["direction"].iloc[-1] == -1.0
    assert st_down["line"].iloc[-1] > down["close"].iloc[-1]  # stop rides above


def test_supertrend_warmup_is_nan() -> None:
    df = ohlcv(closes=list(np.linspace(100, 120, 60)))
    st = supertrend(df, 10, 3.0)
    # Supertrend starts where ATR(10) starts: index 9 (see ATR warmup test).
    assert st["direction"].iloc[:9].isna().all()
    assert st["direction"].iloc[9] in (1.0, -1.0)


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------


def test_vwap_hand_computation_and_session_reset() -> None:
    # Two candles on day 1, one candle on day 2.
    day1_a = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    day1_b = datetime(2026, 7, 16, 9, 30, tzinfo=IST)
    day2_a = datetime(2026, 7, 17, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [t.astimezone(ZoneInfo("UTC")) for t in (day1_a, day1_b, day2_a)]
    )
    df = pd.DataFrame(
        {
            "open": [100.0, 102.0, 200.0],
            "high": [102.0, 104.0, 202.0],
            "low": [98.0, 100.0, 198.0],
            "close": [100.0, 102.0, 200.0],
            "volume": [1000.0, 3000.0, 500.0],
        },
        index=index,
    )
    out = vwap(df)
    tp1, tp2, tp3 = 100.0, 102.0, 200.0  # (h+l+c)/3 for each candle
    assert out.iloc[0] == pytest.approx(tp1)
    assert out.iloc[1] == pytest.approx((tp1 * 1000 + tp2 * 3000) / 4000)
    # Day 2 resets: VWAP == that candle's typical price, ignoring day 1.
    assert out.iloc[2] == pytest.approx(tp3)


def test_vwap_requires_tz_aware_index() -> None:
    df = ohlcv(closes=[100.0, 101.0])
    df.index = pd.DatetimeIndex([datetime(2026, 7, 16, 9, 15), datetime(2026, 7, 16, 9, 30)])
    with pytest.raises(ValueError, match="tz-aware"):
        vwap(df)
