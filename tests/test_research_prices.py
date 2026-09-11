"""Reading frozen price history without ever fetching."""

from __future__ import annotations

import pickle
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parquet_candle_backend import ParquetCandleBackend  # noqa: E402
from price_adjust import Adjustment  # noqa: E402
from research.prices import FrozenPriceReader  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
FROM = datetime(2026, 1, 1, tzinfo=UTC)
TO = datetime(2026, 12, 31, tzinfo=UTC)


def bars(start_ist: datetime, n: int, step_min: int, base: float = 100.0) -> pd.DataFrame:
    idx = pd.DatetimeIndex([(start_ist + timedelta(minutes=step_min * i)).astimezone(UTC) for i in range(n)])
    return pd.DataFrame(
        {
            "open": [base + i for i in range(n)],
            "high": [base + i + 1 for i in range(n)],
            "low": [base + i - 1 for i in range(n)],
            "close": [base + i + 0.5 for i in range(n)],
            "volume": [1000.0] * n,
        },
        index=idx,
    )


def write(root: Path, instrument_id: int, timeframe: str, frame: pd.DataFrame) -> None:
    ParquetCandleBackend(None, str(root)).write_candles(instrument_id, timeframe, frame)


HALF = Adjustment(
    effective_from=date(2026, 8, 1), effective_to=date(2026, 8, 31),
    price_factor=0.5, volume_factor=1.0, sample_days=10,
)


def test_stock_intraday_is_resampled_from_the_5_minute_base(tmp_path):
    write(tmp_path, 7, "5m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 6, 5))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {}, frozenset())
    got = reader.candles("NSE:ABC", "15m", FROM, TO)
    assert len(got) == 2
    assert got["open"].iloc[0] == 100.0
    assert got["close"].iloc[0] == 102.5


def test_stock_intraday_applies_stored_corrections(tmp_path):
    write(tmp_path, 7, "5m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 3, 5))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {7: [HALF]}, frozenset())
    got = reader.candles("NSE:ABC", "5m", FROM, TO)
    assert got["open"].iloc[0] == pytest.approx(50.0)


def test_daily_candles_are_read_directly_without_corrections(tmp_path):
    write(tmp_path, 7, "day", bars(datetime(2026, 8, 3, 0, 0, tzinfo=IST), 3, 1440))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {7: [HALF]}, frozenset())
    got = reader.candles("NSE:ABC", "day", FROM, TO)
    assert list(got["open"]) == [100.0, 101.0, 102.0]


def test_index_timeframes_are_read_exactly_as_stored(tmp_path):
    write(tmp_path, 9, "60m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 7, 60))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:NIFTY": 9}, {}, frozenset({"NSE:NIFTY"}))
    assert len(reader.candles("NSE:NIFTY", "60m", FROM, TO)) == 7


def test_nothing_stored_is_an_empty_frame_not_a_fetch(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {}, frozenset())
    assert reader.candles("NSE:ABC", "15m", FROM, TO).empty


def test_an_unknown_symbol_is_a_key_error(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {}, {}, frozenset())
    with pytest.raises(KeyError):
        reader.candles("NSE:NOPE", "day", FROM, TO)


def test_the_reader_can_be_sent_to_a_worker_process(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {7: [HALF]}, frozenset({"NSE:NIFTY"}))
    assert pickle.loads(pickle.dumps(reader)) == reader
