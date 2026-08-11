"""Tests for the backfill CLI's argument handling and symbol expansion."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backfill import build_parser, expand_symbols  # noqa: E402


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.years == 2.0
    assert args.timeframe == "5m"
    assert args.symbols is None
    assert args.refresh_instruments is False


def test_parser_accepts_symbols_and_years() -> None:
    args = build_parser().parse_args(
        ["--symbols", "NSE:RELIANCE,NSE:TCS", "--years", "5"]
    )
    assert args.symbols == "NSE:RELIANCE,NSE:TCS"
    assert args.years == 5.0


def test_parser_only_allows_stored_timeframes() -> None:
    """15m etc. are DERIVED by resampling and must never be backfilled."""
    assert build_parser().parse_args(["--timeframe", "day"]).timeframe == "day"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--timeframe", "15m"])


def test_expand_symbols_splits_and_normalises() -> None:
    assert expand_symbols(" nse:reliance , NSE:TCS ") == ["NSE:RELIANCE", "NSE:TCS"]


def test_expand_symbols_deduplicates_preserving_order() -> None:
    assert expand_symbols("NSE:TCS,NSE:RELIANCE,NSE:TCS") == ["NSE:TCS", "NSE:RELIANCE"]


def test_expand_symbols_rejects_malformed_entries() -> None:
    with pytest.raises(ValueError, match="RELIANCE"):
        expand_symbols("RELIANCE")


def test_expand_symbols_of_empty_string_is_empty() -> None:
    assert expand_symbols("") == []
    assert expand_symbols("   ") == []


def test_expand_symbols_ignores_blank_entries() -> None:
    assert expand_symbols("NSE:TCS,,NSE:INFY") == ["NSE:TCS", "NSE:INFY"]


def test_main_without_arguments_explains_what_to_do(capsys) -> None:
    """Running it bare should teach, not just fail."""
    from backfill import main

    # No symbols and no --refresh-instruments: should exit non-zero with a
    # message naming both options, WITHOUT touching Supabase.
    code = main([])
    captured = capsys.readouterr()
    assert code != 0
    assert "--symbols" in (captured.err + captured.out)
    assert "--refresh-instruments" in (captured.err + captured.out)
