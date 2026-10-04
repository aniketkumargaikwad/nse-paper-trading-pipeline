"""The local F&O store: one Parquet per underlying per year, and a ledger."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import nse_files, store  # noqa: E402
from test_fno_nse_files import OLD, UDIFF  # noqa: E402 - pytest puts tests/ on the path, as for research_helpers

DAY = date(2016, 1, 4)


def test_a_day_is_written_per_underlying_and_read_back(tmp_path):
    frame = nse_files.parse(OLD, DAY)
    store.write_day(tmp_path, frame, DAY)
    assert store.daily_path(tmp_path, "NIFTY", 2016).exists()
    back = store.read_daily(tmp_path, "NIFTY", DAY, DAY)
    assert len(back) == 3
    assert back["trade_date"].iloc[0] == DAY
    assert list(back.columns) == list(nse_files.COLUMNS)


def test_writing_the_same_day_twice_replaces_it(tmp_path):
    frame = nse_files.parse(OLD, DAY)
    store.write_day(tmp_path, frame, DAY)
    store.write_day(tmp_path, frame, DAY)
    assert len(store.read_daily(tmp_path, "NIFTY", DAY, DAY)) == 3


def test_reading_a_range_spans_years_and_trims_the_edges(tmp_path):
    store.write_day(tmp_path, nse_files.parse(OLD, DAY), DAY)
    sep30 = date(2026, 9, 30)
    store.write_day(tmp_path, nse_files.parse(UDIFF, sep30), sep30)
    both = store.read_daily(tmp_path, "NIFTY", date(2016, 1, 1), date(2026, 12, 31))
    assert set(both["trade_date"]) == {DAY, sep30}
    assert store.read_daily(tmp_path, "NIFTY", date(2017, 1, 1), date(2025, 1, 1)).empty


def test_nothing_stored_reads_as_an_empty_frame_with_the_columns(tmp_path):
    empty = store.read_daily(tmp_path, "BANKNIFTY", DAY, DAY)
    assert empty.empty and list(empty.columns) == list(nse_files.COLUMNS)


def test_the_ledger_remembers_each_day_and_its_outcome(tmp_path):
    assert store.load_ledger(tmp_path) == {}
    store.record_session(tmp_path, date(2024, 1, 26), "no_session", 0)
    store.record_session(tmp_path, DAY, "ok", 3)
    assert store.load_ledger(tmp_path) == {date(2024, 1, 26): "no_session", DAY: "ok"}


def test_no_temporary_file_is_left_behind(tmp_path):
    store.write_day(tmp_path, nse_files.parse(OLD, DAY), DAY)
    assert not list(tmp_path.rglob("*.tmp"))


def test_writing_several_days_at_once_matches_writing_them_one_by_one(tmp_path):
    """The backfill saves in batches: one file rewrite per underlying-year,
    not one per day. The result must be identical."""
    sep30 = date(2026, 9, 30)
    days = [(DAY, nse_files.parse(OLD, DAY)), (sep30, nse_files.parse(UDIFF, sep30))]
    one_by_one, batched = tmp_path / "a", tmp_path / "b"
    for day, frame in days:
        store.write_day(one_by_one, frame, day)
    store.write_days(batched, days)
    for year in (2016, 2026):
        a = store.read_daily(one_by_one, "NIFTY", date(year, 1, 1), date(year, 12, 31))
        b = store.read_daily(batched, "NIFTY", date(year, 1, 1), date(year, 12, 31))
        assert a.equals(b)


def test_a_batch_replaces_days_already_stored(tmp_path):
    frame = nse_files.parse(OLD, DAY)
    store.write_day(tmp_path, frame, DAY)
    store.write_days(tmp_path, [(DAY, frame)])
    assert len(store.read_daily(tmp_path, "NIFTY", DAY, DAY)) == 3
