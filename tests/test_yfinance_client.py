"""Tests for the free yfinance provider and the provider factory.

No network: yfinance responses are simulated with frames shaped exactly like
the real ones (verified against the live API on 2026-08-03), including the
MultiIndex columns and the tz-naive daily index that trip people up.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings  # noqa: E402
from kite_client import KiteClientError  # noqa: E402
from yfinance_client import (  # noqa: E402
    YF_MAX_HISTORY_DAYS,
    DataUnavailableError,
    YFinanceMarketDataClient,
    normalize_yf_frame,
    to_yahoo_symbol,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------


def test_symbol_mapping() -> None:
    assert to_yahoo_symbol("NSE:RELIANCE") == "RELIANCE.NS"
    assert to_yahoo_symbol("BSE:RELIANCE") == "RELIANCE.BO"
    # Verified live: these awkward real symbols resolve on Yahoo.
    assert to_yahoo_symbol("NSE:M&M") == "M&M.NS"
    assert to_yahoo_symbol("NSE:BAJAJ-AUTO") == "BAJAJ-AUTO.NS"


def test_derivatives_exchange_rejected_with_guidance() -> None:
    with pytest.raises(KiteClientError, match="DATA_PROVIDER=kite"):
        to_yahoo_symbol("NFO:NIFTY24AUGFUT")


def test_resolve_instrument_tokens_is_pure_and_deduped() -> None:
    client = YFinanceMarketDataClient(sleep_fn=lambda s: None)
    out = client.resolve_instrument_tokens(
        ["NSE:RELIANCE", "NSE:INFY", "NSE:RELIANCE"], today_ist=datetime.now().date()
    )
    assert out == {"NSE:RELIANCE": "RELIANCE.NS", "NSE:INFY": "INFY.NS"}


# ---------------------------------------------------------------------------
# Frame normalization — shapes copied from the real API
# ---------------------------------------------------------------------------


def yf_intraday_frame(n: int = 3) -> pd.DataFrame:
    """Mimics yf.download(...) for one ticker: MultiIndex cols, IST index."""
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex([start + timedelta(minutes=15 * i) for i in range(n)])
    cols = pd.MultiIndex.from_product(
        [["Close", "High", "Low", "Open", "Volume"], ["RELIANCE.NS"]]
    )
    data = np.array([[100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0] for i in range(n)])
    return pd.DataFrame(data, index=index, columns=cols)


def test_normalize_intraday_frame() -> None:
    df = normalize_yf_frame(yf_intraday_frame())
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    # 09:15 IST == 03:45 UTC
    assert df.index[0] == datetime(2026, 8, 3, 3, 45, tzinfo=UTC)
    assert df["open"].iloc[0] == 100.5
    assert df.index.is_monotonic_increasing


def test_normalize_daily_frame_localizes_naive_index_to_ist() -> None:
    """Daily candles come back tz-NAIVE at midnight; they are IST dates."""
    index = pd.DatetimeIndex([datetime(2026, 8, 3), datetime(2026, 8, 4)])
    cols = pd.MultiIndex.from_product(
        [["Close", "High", "Low", "Open", "Volume"], ["RELIANCE.NS"]]
    )
    raw = pd.DataFrame(
        [[100.0, 101.0, 99.0, 100.0, 5000.0], [102.0, 103.0, 101.0, 102.0, 6000.0]],
        index=index, columns=cols,
    )
    df = normalize_yf_frame(raw)
    # Midnight IST == 18:30 UTC the previous day.
    assert df.index[0] == datetime(2026, 8, 2, 18, 30, tzinfo=UTC)


def test_normalize_flat_columns_also_supported() -> None:
    """auto_adjust=True can return flat (non-MultiIndex) columns."""
    index = pd.DatetimeIndex([datetime(2026, 8, 3, 9, 15, tzinfo=IST)])
    raw = pd.DataFrame(
        {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [10.0]},
        index=index,
    )
    df = normalize_yf_frame(raw)
    assert df["close"].iloc[0] == 100.5


def test_nan_rows_are_dropped_not_filled() -> None:
    """Yahoo emits all-NaN bars for halts; a fabricated candle could fire a
    fake signal, so they must disappear entirely."""
    raw = yf_intraday_frame(3)
    raw.iloc[1] = np.nan
    df = normalize_yf_frame(raw)
    assert len(df) == 2
    assert not df.isna().any().any()


def test_empty_and_none_inputs_give_empty_canonical_frame() -> None:
    for value in (None, pd.DataFrame()):
        df = normalize_yf_frame(value)
        assert df.empty
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert str(df.index.tz) == "UTC"


def test_missing_columns_fail_loudly() -> None:
    raw = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex([datetime(2026, 8, 3)]))
    with pytest.raises(DataUnavailableError, match="missing column"):
        normalize_yf_frame(raw)


# ---------------------------------------------------------------------------
# History clamping — the key free-tier limitation
# ---------------------------------------------------------------------------


class FakeYF:
    """Records the window each download was asked for."""

    def __init__(self, frame: pd.DataFrame | None = None):
        self.calls: list[dict] = []
        self.frame = frame if frame is not None else yf_intraday_frame()

    def __call__(self, symbol, interval, start, end):
        self.calls.append(
            {"symbol": symbol, "interval": interval, "start": start, "end": end}
        )
        return self.frame


def client_with(fake: FakeYF) -> YFinanceMarketDataClient:
    client = YFinanceMarketDataClient(sleep_fn=lambda s: None)
    client._download = fake  # type: ignore[assignment]
    return client


NOW = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)


def test_15m_request_is_clamped_to_60_days() -> None:
    fake = FakeYF()
    client = client_with(fake)
    # Ask for 2 years of 15m data; Yahoo only serves ~60 days.
    client.fetch_historical_candles(
        "RELIANCE.NS", "15m", NOW - timedelta(days=730), NOW, now_utc=NOW
    )
    assert client.last_window_was_clamped is True
    span_days = (NOW - client.last_effective_from).days
    assert span_days == YF_MAX_HISTORY_DAYS["15m"]
    # Must stay strictly under Yahoo's exclusive 60-day boundary: a start of
    # exactly -60d returns ZERO rows (verified live), which would look like
    # "no signals" rather than "no data".
    assert span_days < 60


def test_daily_request_is_not_clamped() -> None:
    fake = FakeYF()
    client = client_with(fake)
    client.fetch_historical_candles(
        "RELIANCE.NS", "day", NOW - timedelta(days=1825), NOW, now_utc=NOW
    )
    assert client.last_window_was_clamped is False


def test_timeframe_maps_to_yahoo_interval() -> None:
    fake = FakeYF()
    client = client_with(fake)
    for tf, expected in [("15m", "15m"), ("30m", "30m"), ("60m", "1h"), ("day", "1d")]:
        client.fetch_historical_candles(
            "RELIANCE.NS", tf, NOW - timedelta(days=5), NOW, now_utc=NOW
        )
        assert fake.calls[-1]["interval"] == expected


def test_unsupported_timeframe_rejected() -> None:
    client = client_with(FakeYF())
    with pytest.raises(KiteClientError, match="Unsupported timeframe"):
        client.fetch_historical_candles(
            "RELIANCE.NS", "5m", NOW - timedelta(days=1), NOW, now_utc=NOW
        )


def test_forming_candle_dropped_by_default() -> None:
    """The no-look-ahead guarantee must hold on this provider too."""
    # Candles at 09:15/09:30/09:45 IST; "now" is 09:50 IST so 09:45 is forming.
    now = datetime(2026, 8, 3, 9, 50, tzinfo=IST).astimezone(UTC)
    client = client_with(FakeYF(yf_intraday_frame(3)))
    df = client.fetch_historical_candles(
        "RELIANCE.NS", "15m", now - timedelta(days=1), now, now_utc=now
    )
    assert len(df) == 2
    assert df.index[-1] == datetime(2026, 8, 3, 9, 30, tzinfo=IST).astimezone(UTC)


def test_retries_then_fails_with_actionable_message() -> None:
    calls = {"n": 0}

    def always_fail(*a, **kw):
        calls["n"] += 1
        raise ConnectionError("yahoo throttled")

    import yfinance_client as mod

    client = YFinanceMarketDataClient(sleep_fn=lambda s: None)
    original = mod.YFinanceMarketDataClient._download
    try:
        # Exercise the real _download retry loop with a failing yf.download.
        import types

        fake_yf = types.SimpleNamespace(download=always_fail)
        sys.modules["yfinance"] = fake_yf  # type: ignore[assignment]
        with pytest.raises(DataUnavailableError, match="after 4 attempts"):
            client._download("RELIANCE.NS", "15m", NOW - timedelta(days=1), NOW)
        assert calls["n"] == 4
    finally:
        sys.modules.pop("yfinance", None)
        mod.YFinanceMarketDataClient._download = original


def test_provider_declares_no_login_needed() -> None:
    assert YFinanceMarketDataClient.requires_daily_login is False


# ---------------------------------------------------------------------------
# Factory + settings wiring
# ---------------------------------------------------------------------------


def test_factory_returns_yfinance_without_touching_the_store() -> None:
    from data_provider import create_data_client, describe_provider

    settings = Settings(
        supabase_url="https://x.supabase.co", supabase_service_role_key="k",
        data_provider="yfinance",
    )
    # store=None proves the free provider needs no database/token at all.
    client = create_data_client(settings, None, datetime.now().date())
    assert isinstance(client, YFinanceMarketDataClient)
    assert "free" in describe_provider(settings)


def test_settings_default_provider_is_free_and_needs_no_login() -> None:
    settings = Settings(supabase_url="u", supabase_service_role_key="k")
    assert settings.data_provider == "yfinance"
    assert settings.requires_daily_login is False
    assert settings.kite_api_key == ""  # no broker account required


def test_kite_provider_flags_daily_login() -> None:
    settings = Settings(
        supabase_url="u", supabase_service_role_key="k", data_provider="kite",
        kite_api_key="a", kite_api_secret="b",
    )
    assert settings.requires_daily_login is True


def test_unknown_provider_rejected() -> None:
    from data_provider import create_data_client

    settings = Settings(
        supabase_url="u", supabase_service_role_key="k", data_provider="bogus",
    )
    with pytest.raises(RuntimeError, match="Unknown DATA_PROVIDER"):
        create_data_client(settings, None, datetime.now().date())
