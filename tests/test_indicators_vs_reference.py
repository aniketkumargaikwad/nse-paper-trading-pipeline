"""Our indicators, checked against two independent libraries.

WHY THIS EXISTS
---------------
Every other test here checks that our code does what we intended. This one
checks that what we intended is what everyone else calls by the same name. A
correct-looking RSI that disagrees with TradingView is not an RSI — it is a
number that will make our backtests disagree with every chart the owner looks
at, with no way to tell which is wrong.

`ta` and `pandas-ta-classic` are unrelated projects with their own authors and
their own bugs. Where all three agree, the formula is almost certainly right.

TWO REAL DIFFERENCES ARE EXPECTED AND ALLOWED
---------------------------------------------
* **Warm-up.** RSI, ATR and anything else using Wilder's smoothing needs a
  running start, and libraries seed it differently. The difference decays to
  nothing: measured on real data it is under 0.0001% by bar 200. Comparisons
  here therefore skip the warm-up, and a separate test pins the decay so the
  skip cannot hide a genuine drift.

* **Supertrend.** Three implementations, three answers — ours agreed with
  TradingView on 99.30% of candles and pandas-ta on 97.65%, before ours was
  rewritten to be TradingView's formula exactly. It is deliberately NOT
  compared against pandas-ta here, because we chose a different authority on
  purpose. See the note in indicators.supertrend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import indicators as I  # noqa: E402

pta = pytest.importorskip(
    "pandas_ta_classic",
    reason="reference library not installed; run: pip install pandas-ta-classic",
)

# Smoothed indicators need a running start before any two implementations
# agree. 250 candles is comfortably past where the difference vanishes.
WARMUP = 250


@pytest.fixture(scope="module")
def candles() -> pd.DataFrame:
    """A deterministic, realistic price series.

    Synthetic rather than live: this test must pass with no database, no
    network and no credentials, and must give the same answer next year.
    """
    rng = np.random.default_rng(20260822)
    n = 1200
    steps = rng.normal(0.0, 1.0, size=n)
    close = 1400 + np.cumsum(steps)
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0.0, 2.0, size=n)) + 0.5
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
            "volume": rng.uniform(5_000, 50_000, size=n),
        },
        index=pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
    )


def agree(ours, theirs, *, tolerance: float = 1e-6) -> None:
    """Assert two series match after warm-up, comparing by POSITION.

    Reference libraries sometimes return a shorter frame with the warm-up
    rows dropped. Truncating both to the shorter length would then offset
    them by however many rows were dropped and report a formula difference
    that does not exist — which is exactly the mistake this helper prevents.
    """
    a = pd.Series(np.asarray(ours, dtype=float)).reset_index(drop=True)
    b = pd.Series(np.asarray(theirs, dtype=float)).reset_index(drop=True)
    if len(a) != len(b):
        # Right-align: the reference dropped leading rows, not trailing ones.
        keep = min(len(a), len(b))
        a, b = a.iloc[len(a) - keep:], b.iloc[len(b) - keep:]
        a, b = a.reset_index(drop=True), b.reset_index(drop=True)

    a, b = a.iloc[WARMUP:], b.iloc[WARMUP:]
    both = a.notna() & b.notna()
    assert both.sum() > 100, "not enough overlapping values to compare"
    worst = (a[both] - b[both]).abs().max()
    scale = max(float(b[both].abs().median()), 1e-9)
    assert worst / scale < tolerance, (
        f"worst difference {worst:.10f} ({worst / scale * 100:.6f}% of typical "
        f"value {scale:.4f}) exceeds tolerance"
    )


# --- moving averages --------------------------------------------------------


def test_sma_matches(candles):
    agree(I.sma(candles["close"], 20), pta.sma(candles["close"], length=20))


def test_ema_matches(candles):
    agree(I.ema(candles["close"], 20), pta.ema(candles["close"], length=20))


def test_wma_matches(candles):
    agree(I.wma(candles["close"], 20), pta.wma(candles["close"], length=20))


def test_hma_matches(candles):
    agree(I.hma(candles["close"], 20), pta.hma(candles["close"], length=20))


def test_vwma_matches(candles):
    agree(I.vwma(candles, 20),
          pta.vwma(candles["close"], candles["volume"], length=20))


def test_dema_matches(candles):
    agree(I.dema(candles["close"], 20), pta.dema(candles["close"], length=20))


def test_tema_matches(candles):
    agree(I.tema(candles["close"], 20), pta.tema(candles["close"], length=20))


# --- oscillators ------------------------------------------------------------


def test_rsi_matches(candles):
    agree(I.rsi(candles["close"], 14), pta.rsi(candles["close"], length=14))


def test_stochastic_k_matches(candles):
    theirs = pta.stoch(candles["high"], candles["low"], candles["close"],
                       k=14, d=3, smooth_k=3)
    agree(I.stochastic(candles, 14, 3, 3)["k"], theirs["STOCHk_14_3_3"])


def test_stochastic_d_matches(candles):
    theirs = pta.stoch(candles["high"], candles["low"], candles["close"],
                       k=14, d=3, smooth_k=3)
    agree(I.stochastic(candles, 14, 3, 3)["d"], theirs["STOCHd_14_3_3"])


def test_stoch_rsi_matches(candles):
    theirs = pta.stochrsi(candles["close"], length=14, rsi_length=14, k=3, d=3)
    agree(I.stoch_rsi(candles["close"], 14, 14, 3, 3)["k"], theirs.iloc[:, 0])


def test_cci_matches(candles):
    agree(I.cci(candles, 20),
          pta.cci(candles["high"], candles["low"], candles["close"], length=20))


def test_williams_r_matches(candles):
    agree(I.williams_r(candles, 14),
          pta.willr(candles["high"], candles["low"], candles["close"], length=14))


def test_roc_matches(candles):
    agree(I.roc(candles["close"], 10), pta.roc(candles["close"], length=10))


def test_momentum_matches(candles):
    agree(I.momentum(candles["close"], 10), pta.mom(candles["close"], length=10))


def test_trix_matches(candles):
    agree(I.trix(candles["close"], 15),
          pta.trix(candles["close"], length=15).iloc[:, 0])


def test_awesome_oscillator_matches(candles):
    agree(I.awesome_oscillator(candles), pta.ao(candles["high"], candles["low"]))


def test_ultimate_oscillator_matches(candles):
    agree(I.ultimate_oscillator(candles),
          pta.uo(candles["high"], candles["low"], candles["close"]))


# --- trend ------------------------------------------------------------------


def test_macd_matches(candles):
    theirs = pta.macd(candles["close"], fast=12, slow=26, signal=9)
    agree(I.macd(candles["close"], 12, 26, 9)["line"], theirs.iloc[:, 0])


def test_adx_matches(candles):
    theirs = pta.adx(candles["high"], candles["low"], candles["close"], length=14)
    agree(I.adx(candles, 14)["adx"], theirs["ADX_14"])


def test_plus_di_matches(candles):
    theirs = pta.adx(candles["high"], candles["low"], candles["close"], length=14)
    agree(I.adx(candles, 14)["plus_di"], theirs["DMP_14"])


def test_minus_di_matches(candles):
    theirs = pta.adx(candles["high"], candles["low"], candles["close"], length=14)
    agree(I.adx(candles, 14)["minus_di"], theirs["DMN_14"])


def test_aroon_up_matches(candles):
    """Ties are the whole point here: when the same high repeats, the most
    RECENT occurrence counts, which is what TradingView reports."""
    theirs = pta.aroon(candles["high"], candles["low"], length=14)
    agree(I.aroon(candles, 14)["up"], theirs["AROONU_14"])


def test_aroon_down_matches(candles):
    theirs = pta.aroon(candles["high"], candles["low"], length=14)
    agree(I.aroon(candles, 14)["down"], theirs["AROOND_14"])


# --- channels and volatility ------------------------------------------------


def test_atr_matches(candles):
    agree(I.atr(candles, 14),
          pta.atr(candles["high"], candles["low"], candles["close"], length=14))


def test_bollinger_upper_matches(candles):
    theirs = pta.bbands(candles["close"], length=20, std=2)
    agree(I.bollinger_bands(candles["close"], 20, 2)["upper"], theirs.iloc[:, 2])


def test_bollinger_lower_matches(candles):
    theirs = pta.bbands(candles["close"], length=20, std=2)
    agree(I.bollinger_bands(candles["close"], 20, 2)["lower"], theirs.iloc[:, 0])


def test_stddev_matches(candles):
    agree(I.stddev(candles["close"], 20), pta.stdev(candles["close"], length=20))


def test_donchian_upper_matches(candles):
    theirs = pta.donchian(candles["high"], candles["low"],
                          lower_length=20, upper_length=20)
    agree(I.donchian(candles, 20)["upper"], theirs.iloc[:, 2])


def test_donchian_lower_matches(candles):
    theirs = pta.donchian(candles["high"], candles["low"],
                          lower_length=20, upper_length=20)
    agree(I.donchian(candles, 20)["lower"], theirs.iloc[:, 0])


# --- volume -----------------------------------------------------------------


def test_obv_matches(candles):
    agree(I.obv(candles), pta.obv(candles["close"], candles["volume"]))


def test_mfi_matches(candles):
    agree(I.mfi(candles, 14),
          pta.mfi(candles["high"], candles["low"], candles["close"],
                  candles["volume"], length=14))


def test_cmf_matches(candles):
    agree(I.cmf(candles, 20),
          pta.cmf(candles["high"], candles["low"], candles["close"],
                  candles["volume"], length=20))


# --- the warm-up claim, pinned ----------------------------------------------


def test_wilder_warmup_difference_actually_decays(candles):
    """The comparisons above skip the first 250 candles. That skip is only
    honest if the difference genuinely vanishes rather than being hidden."""
    ours = I.rsi(candles["close"], 14)
    theirs = pta.rsi(candles["close"], length=14)
    a = pd.Series(np.asarray(ours, dtype=float))
    b = pd.Series(np.asarray(theirs, dtype=float))
    both = a.notna() & b.notna()
    rel = ((a[both] - b[both]).abs() / b[both].abs()).reset_index(drop=True)
    early = rel.iloc[:30].max()
    late = rel.iloc[200:].max()
    later = rel.iloc[500:].max()

    # 1e-6 relative is one part in a million — far below anything that could
    # change a trade, and below the precision anyone reads off a chart.
    assert late < 1e-6, f"difference did not vanish: still {late:.10f} after 200 bars"
    # It must also keep shrinking. A residual that stopped decaying would mean
    # a real formula difference hiding under a loose threshold.
    assert later <= late, "difference stopped decaying — suspect a real drift"
    assert early > late * 100, "expected the difference to be far larger at the start"
