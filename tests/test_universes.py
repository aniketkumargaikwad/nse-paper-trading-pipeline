"""Tests for symbol universes. Pure: no network, no database."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universes import (  # noqa: E402
    UniverseError,
    load_constituents,
    newest_snapshot,
    parse_constituent_csv,
    project_storage,
    resolve_universe,
    snapshot_filename,
)

# The real header NSE publishes on its index constituent CSVs.
HEADER = '"Company Name","Industry","Symbol","Series","ISIN Code"'


def csv_text(*rows: str) -> str:
    return "\n".join((HEADER, *rows)) + "\n"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_symbols_into_our_notation():
    text = csv_text(
        '"Reliance Industries Ltd.","Oil Gas","RELIANCE","EQ","INE002A01018"',
        '"Tata Consultancy Services Ltd.","IT","TCS","EQ","INE467B01029"',
    )
    assert parse_constituent_csv(text) == ("NSE:RELIANCE", "NSE:TCS")


def test_symbols_are_deduplicated_and_ordered():
    text = csv_text(
        '"B Ltd.","X","BBB","EQ","INE2"',
        '"A Ltd.","X","AAA","EQ","INE1"',
        '"B Ltd.","X","BBB","EQ","INE2"',
    )
    assert parse_constituent_csv(text) == ("NSE:AAA", "NSE:BBB")


def test_non_eq_series_rows_are_skipped():
    """Mirrors instruments.py: a bond must not enter a universe as a stock."""
    text = csv_text(
        '"Good Ltd.","X","GOOD","EQ","INE1"',
        '"Bond 2033","X","757GS2033","GS","INE2"',
    )
    assert parse_constituent_csv(text) == ("NSE:GOOD",)


def test_a_missing_symbol_column_fails_loudly():
    text = '"Company Name","Industry","Series"\n"A","X","EQ"\n'
    with pytest.raises(UniverseError) as exc:
        parse_constituent_csv(text)
    assert "Symbol" in str(exc.value)


def test_an_empty_list_is_rejected_rather_than_returned():
    # An empty universe produces a zero-trade backtest that reads exactly like
    # "no signals found", so it must never be returned as a valid result.
    with pytest.raises(UniverseError) as exc:
        parse_constituent_csv(csv_text())
    assert "no constituents" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Resolution against the instruments table
# ---------------------------------------------------------------------------


def test_resolution_reports_symbols_missing_from_the_instruments_table():
    result = resolve_universe(
        name="NIFTY3",
        listed=("NSE:AAA", "NSE:BBB", "NSE:CCC"),
        known={"NSE:AAA", "NSE:CCC"},
        as_of=date(2026, 8, 10),
        source="nse",
    )
    assert result.symbols == ("NSE:AAA", "NSE:CCC")
    assert result.missing == ("NSE:BBB",)
    assert result.is_complete is False
    assert "2 of 3" in result.summary()
    assert "NSE:BBB" in result.summary()


def test_a_fully_resolved_universe_is_complete():
    result = resolve_universe(
        name="NIFTY2",
        listed=("NSE:AAA", "NSE:BBB"),
        known={"NSE:AAA", "NSE:BBB"},
        as_of=date(2026, 8, 10),
        source="nse",
    )
    assert result.is_complete is True
    assert result.missing == ()


def test_the_summary_always_states_the_list_date():
    """Membership is TODAY's applied to past data; the date is the caveat."""
    result = resolve_universe(
        name="NIFTY2", listed=("NSE:AAA",), known={"NSE:AAA"},
        as_of=date(2026, 8, 10), source="nse",
    )
    assert "2026-08-10" in result.summary()


def test_resolution_with_nothing_known_fails_rather_than_returning_empty():
    with pytest.raises(UniverseError) as exc:
        resolve_universe(
            name="NIFTY2",
            listed=("NSE:AAA", "NSE:BBB"),
            known=set(),
            as_of=date(2026, 8, 10),
            source="nse",
        )
    assert "NIFTY2" in str(exc.value)
    assert "refresh-instruments" in str(exc.value)


def test_snapshot_filename_is_dated_and_sorts_chronologically():
    assert snapshot_filename("NIFTY50", date(2026, 8, 10)) == "NIFTY50-2026-08-10.csv"


# ---------------------------------------------------------------------------
# Fetch with snapshot fallback
# ---------------------------------------------------------------------------


def test_newest_snapshot_picks_the_latest_date(tmp_path):
    (tmp_path / "NIFTY50-2026-01-01.csv").write_text("x", encoding="utf-8")
    (tmp_path / "NIFTY50-2026-08-10.csv").write_text("y", encoding="utf-8")
    (tmp_path / "NIFTY100-2026-12-01.csv").write_text("z", encoding="utf-8")
    path, as_of = newest_snapshot("NIFTY50", snapshot_dir=tmp_path)
    assert path.name == "NIFTY50-2026-08-10.csv"
    assert as_of == date(2026, 8, 10)


def test_newest_snapshot_raises_when_there_is_none(tmp_path):
    with pytest.raises(UniverseError) as exc:
        newest_snapshot("NIFTY50", snapshot_dir=tmp_path)
    assert "NIFTY50" in str(exc.value)


def test_load_constituents_prefers_the_live_fetch(tmp_path):
    (tmp_path / "NIFTY50-2020-01-01.csv").write_text(
        csv_text('"Old Ltd.","X","OLD","EQ","INE0"'), encoding="utf-8"
    )
    live = csv_text('"New Ltd.","X","NEW","EQ","INE1"')
    result = load_constituents(
        "NIFTY50", snapshot_dir=tmp_path, fetcher=lambda url: live
    )
    assert result.symbols == ("NSE:NEW",)
    assert result.source == "nse"
    assert result.warning is None


def test_load_constituents_falls_back_to_the_snapshot_and_warns(tmp_path):
    (tmp_path / "NIFTY50-2026-01-05.csv").write_text(
        csv_text('"Old Ltd.","X","OLD","EQ","INE0"'), encoding="utf-8"
    )

    def broken(url):
        raise OSError("connection reset")

    result = load_constituents("NIFTY50", snapshot_dir=tmp_path, fetcher=broken)
    assert result.symbols == ("NSE:OLD",)
    assert result.source == "snapshot"
    assert result.as_of == date(2026, 1, 5)
    assert "2026-01-05" in result.warning
    assert "connection reset" in result.warning


def test_a_malformed_live_response_also_falls_back(tmp_path):
    """NSE serving an HTML error page must not become an empty universe."""
    (tmp_path / "NIFTY50-2026-01-05.csv").write_text(
        csv_text('"Old Ltd.","X","OLD","EQ","INE0"'), encoding="utf-8"
    )
    result = load_constituents(
        "NIFTY50", snapshot_dir=tmp_path, fetcher=lambda url: "<html>blocked</html>"
    )
    assert result.source == "snapshot"
    assert result.symbols == ("NSE:OLD",)


def test_load_constituents_fails_when_the_fetch_breaks_and_no_snapshot_exists(tmp_path):
    def broken(url):
        raise OSError("connection reset")

    with pytest.raises(UniverseError) as exc:
        load_constituents("NIFTY50", snapshot_dir=tmp_path, fetcher=broken)
    assert "NIFTY50" in str(exc.value)


def test_load_constituents_rejects_an_unknown_index_name(tmp_path):
    with pytest.raises(UniverseError) as exc:
        load_constituents("NIFTY_MADE_UP", snapshot_dir=tmp_path, fetcher=lambda u: "")
    assert "NIFTY_MADE_UP" in str(exc.value)


# ---------------------------------------------------------------------------
# Storage projection
# ---------------------------------------------------------------------------


def test_projection_matches_the_spec_figures_for_nifty500_as_database_rows():
    projection = project_storage(symbol_count=500, years=2.0, store="supabase")
    assert 18_000_000 < projection.rows < 20_000_000
    assert 2.0 < projection.gigabytes < 2.5
    assert projection.exceeds_free_tier is True


def test_the_same_universe_fits_free_once_it_is_parquet():
    """The choice of backend is worth 5.6x, which decides whether to pay.

    Identical candle count, different answer: 393 MB of files against a 1 GB
    Storage quota rather than 2.2 GB of rows against a 500 MB database one.
    Projecting the wrong backend is how someone concludes they need the
    $25/mo plan for a universe that fits free.
    """
    rows_ = project_storage(symbol_count=500, years=2.0, store="supabase")
    files = project_storage(symbol_count=500, years=2.0, store="parquet")

    assert files.rows == rows_.rows
    assert files.megabytes < rows_.megabytes / 5
    assert files.exceeds_free_tier is False
    assert files.quota_mb == 1024 and rows_.quota_mb == 500


def test_parquet_is_the_default_because_it_is_what_runs():
    assert project_storage(symbol_count=10, years=1.0).store == "parquet"


def test_nifty200_at_full_depth_fits_the_storage_free_tier():
    """The measured case: 200 symbols x 9.4 years is ~740 MB of ~1024 MB.

    Pinned because it is close enough to the limit that a change in the
    per-candle figure should have to be noticed rather than discovered when
    a backfill stops halfway.
    """
    projection = project_storage(symbol_count=200, years=9.4)
    assert 700 < projection.megabytes < 800
    assert projection.exceeds_free_tier is False


def test_projection_for_nifty50_fits_the_free_tier():
    projection = project_storage(symbol_count=50, years=2.0)
    assert projection.exceeds_free_tier is False


def test_projection_summary_names_the_free_tier_when_exceeded():
    text = project_storage(symbol_count=500, years=2.0, store="supabase").summary()
    assert "500 MB" in text and "free tier" in text.lower()


def test_summary_says_how_much_room_is_left_when_it_fits():
    """"Fits" without a number invites a backfill that stops halfway."""
    text = project_storage(symbol_count=200, years=9.4).summary()
    assert "1024 MB free tier" in text and "spare" in text
