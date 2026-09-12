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


def test_an_unsupported_timeframe_is_a_config_error_for_stock_and_index(tmp_path):
    from config import ConfigError

    reader = FrozenPriceReader(
        str(tmp_path), {"NSE:ABC": 7, "NSE:NIFTY": 9}, {}, frozenset({"NSE:NIFTY"})
    )
    with pytest.raises(ConfigError):
        reader.candles("NSE:ABC", "45m", FROM, TO)
    with pytest.raises(ConfigError):
        reader.candles("NSE:NIFTY", "45m", FROM, TO)


def test_1m_is_refused(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {}, frozenset())
    with pytest.raises(ValueError):
        reader.candles("NSE:ABC", "1m", FROM, TO)


def test_a_remote_candle_root_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        FrozenPriceReader("supabase://candles", {}, {}, frozenset())


def test_the_reader_matches_candle_store_get_candles(tmp_path):
    """The reader must never drift from the production read path.

    Runs candle_store.CandleStore.get_candles and FrozenPriceReader.candles
    over the SAME fixture parquet root and asserts identical frames - the
    behavioural proof that reading the files directly reproduces exactly what
    production would have returned, corrections and resampling included.
    """
    from candle_store import CandleStore
    from coverage_math import CoverageRange

    instrument_id = 7
    write(tmp_path, instrument_id, "5m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 6, 5))
    real_backend = ParquetCandleBackend(None, str(tmp_path))

    class StubProvider:
        name = "stub"

        def fetch(self, symbol, timeframe, from_utc, to_utc):
            raise AssertionError("research must never fetch")

        def max_history_days(self, timeframe):
            return 100_000

    class StubBackend:
        def instrument_id(self, symbol):
            return instrument_id

        def read_candles(self, iid, timeframe, from_utc, to_utc):
            return real_backend.read_candles(iid, timeframe, from_utc, to_utc)

        def write_candles(self, iid, timeframe, df):
            real_backend.write_candles(iid, timeframe, df)

        def read_coverage(self, iid, timeframe):
            # Already spans the requested window, so ensure_coverage has
            # nothing to fetch.
            return CoverageRange(FROM, TO)

        def write_coverage(self, *args, **kwargs):
            pass

        def write_quality_flags(self, rows):
            pass

        def read_price_adjustments(self, iid, timeframe):
            return [HALF]

    store = CandleStore(StubBackend(), StubProvider())
    reader = FrozenPriceReader(
        str(tmp_path), {"NSE:ABC": instrument_id}, {instrument_id: [HALF]}, frozenset()
    )

    from_store = store.get_candles("NSE:ABC", "15m", FROM, TO)
    from_reader = reader.candles("NSE:ABC", "15m", FROM, TO)

    pd.testing.assert_frame_equal(from_store, from_reader)


def test_a_truncated_price_adjustments_page_is_a_runtime_error(tmp_path):
    from research.prices import load_adjustments

    class StubExecuteResult:
        def __init__(self, data):
            self.data = data

    class StubQuery:
        def __init__(self, rows):
            self._rows = rows

        def select(self, *args, **kwargs):
            return self

        def in_(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def order(self, *args, **kwargs):
            return self

        def execute(self):
            return StubExecuteResult(self._rows)

    class StubClient:
        def __init__(self, rows):
            self._rows = rows

        def table(self, name):
            return StubQuery(self._rows)

    row = {
        "instrument_id": 7,
        "effective_from": "2026-08-01",
        "effective_to": "2026-08-31",
        "price_factor": 0.5,
        "volume_factor": 1.0,
        "sample_days": 10,
    }
    client = StubClient([row] * 1000)
    with pytest.raises(RuntimeError):
        load_adjustments(client, [7])


# ---------------------------------------------------------------------------
# The loaders: chunked Supabase reads, run once in the parent process.
#
# FakeClient below FILTERS, unlike the deliberately dumb StubClient above, so
# these tests can check which rows each query actually asked for.
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from research.prices import load_adjustments, load_instrument_ids  # noqa: E402


class FakeQuery:
    def __init__(self, rows, log):
        self._rows, self._log, self._filters = rows, log, []

    def select(self, columns):
        self._log.append(("select", columns))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, list(values)))
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def order(self, column):
        return self

    def execute(self):
        rows = self._rows
        for kind, column, value in self._filters:
            rows = [r for r in rows if (r[column] in value if kind == "in" else r[column] == value)]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, tables):
        self.tables, self.log = tables, []

    def table(self, name):
        return FakeQuery(self.tables[name], self.log)


def test_instrument_ids_are_looked_up_in_chunks():
    rows = [{"id": i, "symbol": f"NSE:S{i}"} for i in range(250)]
    client = FakeClient({"instruments": rows})
    ids = load_instrument_ids(client, [r["symbol"] for r in rows])
    assert ids["NSE:S249"] == 249
    assert sum(1 for entry in client.log if entry[0] == "select") == 3


def test_a_missing_instrument_is_named():
    client = FakeClient({"instruments": [{"id": 1, "symbol": "NSE:A"}]})
    with pytest.raises(LookupError, match="NSE:B"):
        load_instrument_ids(client, ["NSE:A", "NSE:B"])


def test_adjustments_are_grouped_by_instrument_and_only_5_minute():
    rows = [
        {"instrument_id": 1, "timeframe": "5m", "effective_from": "2020-01-01",
         "effective_to": "2020-06-30", "price_factor": 0.1, "volume_factor": None, "sample_days": 5},
        {"instrument_id": 1, "timeframe": "1m", "effective_from": "2020-01-01",
         "effective_to": "2020-06-30", "price_factor": 0.1, "volume_factor": 10.0, "sample_days": 5},
    ]
    got = load_adjustments(FakeClient({"price_adjustments": rows}), [1, 2])
    assert got[2] == []
    assert len(got[1]) == 1
    assert got[1][0].effective_from == date(2020, 1, 1)
    assert got[1][0].volume_factor == 1.0
