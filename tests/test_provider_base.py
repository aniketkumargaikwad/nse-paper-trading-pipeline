"""Tests for the CandleProvider protocol and its canonical-frame helper."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.base import (  # noqa: E402
    OHLCV_COLUMNS,
    CandleProvider,
    ProviderError,
    canonical_frame,
    empty_frame,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def raw_frame(pairs: list[tuple[datetime, float]]) -> pd.DataFrame:
    """Frame with all OHLCV columns set from one value per row."""
    return pd.DataFrame(
        {
            "open": [v for _, v in pairs],
            "high": [v for _, v in pairs],
            "low": [v for _, v in pairs],
            "close": [v for _, v in pairs],
            "volume": [v * 10 for _, v in pairs],
        },
        index=pd.DatetimeIndex([t for t, _ in pairs]),
    )


def test_empty_frame_shape() -> None:
    df = empty_frame()
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert df.index.name == "ts"


def test_canonical_frame_sorts_dedupes_and_converts_to_utc() -> None:
    out = canonical_frame(raw_frame([
        (datetime(2026, 8, 3, 9, 20, tzinfo=IST), 2.0),
        (datetime(2026, 8, 3, 9, 15, tzinfo=IST), 1.0),
        (datetime(2026, 8, 3, 9, 20, tzinfo=IST), 2.0),  # duplicate
    ]))
    assert len(out) == 2                       # duplicate dropped
    assert out.index.is_monotonic_increasing   # sorted
    assert str(out.index.tz) == "UTC"
    assert out.index[0] == datetime(2026, 8, 3, 3, 45, tzinfo=UTC)  # 09:15 IST
    assert out.index.name == "ts"


def test_canonical_frame_localises_naive_index_to_ist() -> None:
    """Every supported source serves Indian data in local time, so a naive
    timestamp means IST - never UTC."""
    out = canonical_frame(raw_frame([(datetime(2026, 8, 3, 9, 15), 1.0)]))
    assert out.index[0] == datetime(2026, 8, 3, 3, 45, tzinfo=UTC)


def test_canonical_frame_keeps_only_ohlcv_columns_in_order() -> None:
    raw = raw_frame([(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 1.0)])
    raw["symbol"] = "RELIANCE"
    raw["extra"] = 42
    out = canonical_frame(raw)
    assert list(out.columns) == OHLCV_COLUMNS


def test_canonical_frame_coerces_to_float() -> None:
    raw = raw_frame([(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 1.0)])
    raw = raw.astype({"open": int, "volume": int})
    out = canonical_frame(raw)
    assert out["open"].dtype == float
    assert out["volume"].dtype == float


def test_canonical_frame_of_empty_or_none_is_canonical_empty() -> None:
    for value in (None, pd.DataFrame()):
        out = canonical_frame(value)
        assert out.empty
        assert list(out.columns) == OHLCV_COLUMNS
        assert str(out.index.tz) == "UTC"


def test_missing_column_fails_loudly() -> None:
    raw = pd.DataFrame({"open": [1.0]}, index=pd.DatetimeIndex([datetime(2026, 8, 3)]))
    with pytest.raises(ProviderError, match="missing column"):
        canonical_frame(raw)


def test_missing_column_names_every_absent_column() -> None:
    raw = pd.DataFrame(
        {"open": [1.0], "close": [1.0]},
        index=pd.DatetimeIndex([datetime(2026, 8, 3)]),
    )
    with pytest.raises(ProviderError) as exc:
        canonical_frame(raw)
    message = str(exc.value)
    for absent in ("high", "low", "volume"):
        assert absent in message


def test_already_utc_frame_is_unchanged_in_value() -> None:
    ts = datetime(2026, 8, 3, 3, 45, tzinfo=UTC)
    out = canonical_frame(raw_frame([(ts, 5.0)]))
    assert out.index[0] == ts
    assert out["close"].iloc[0] == 5.0


def test_protocol_accepts_a_conforming_object() -> None:
    """A duck-typed provider satisfies the protocol without inheriting it."""

    class Conforming:
        name = "fake"

        def fetch(self, symbol, timeframe, from_utc, to_utc):
            return empty_frame()

        def max_history_days(self, timeframe):
            return 365

    assert isinstance(Conforming(), CandleProvider)


def test_protocol_rejects_a_non_conforming_object() -> None:
    class Missing:
        name = "broken"
        # no fetch, no max_history_days

    assert not isinstance(Missing(), CandleProvider)
