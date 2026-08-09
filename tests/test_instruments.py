"""Tests for the Dhan security-master mapping. Pure: no network, no database."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instruments import (  # noqa: E402
    SYMBOL_RE,
    Instrument,
    InstrumentError,
    parse_security_master,
    split_symbol,
)

CSV_SAMPLE = """SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_LOT_UNITS,SM_SYMBOL_NAME
2885,RELIANCE,NSE,E,EQUITY,1,RELIANCE INDUSTRIES
1333,HDFCBANK,NSE,E,EQUITY,1,HDFC BANK
11536,TCS,NSE,E,EQUITY,1,TATA CONSULTANCY
1,SENSEX,BSE,I,INDEX,1,SENSEX
"""


# --- symbol parsing ---------------------------------------------------------


def test_split_symbol() -> None:
    assert split_symbol("NSE:RELIANCE") == ("NSE", "RELIANCE")
    assert split_symbol("BSE:SENSEX") == ("BSE", "SENSEX")


@pytest.mark.parametrize("bad", ["RELIANCE", "NSE:", ":RELIANCE", "nse:reliance", ""])
def test_malformed_symbol_rejected(bad: str) -> None:
    with pytest.raises(InstrumentError):
        split_symbol(bad)


def test_symbols_with_punctuation_are_accepted() -> None:
    # Real NSE symbols: M&M, BAJAJ-AUTO.
    assert split_symbol("NSE:M&M") == ("NSE", "M&M")
    assert split_symbol("NSE:BAJAJ-AUTO") == ("NSE", "BAJAJ-AUTO")
    assert SYMBOL_RE.match("NSE:M&M")


# --- security master parsing ------------------------------------------------


def test_parse_security_master_builds_instruments() -> None:
    found = parse_security_master(CSV_SAMPLE, refreshed_on=date(2026, 8, 3))
    by_symbol = {i.symbol: i for i in found}
    assert "NSE:RELIANCE" in by_symbol
    reliance = by_symbol["NSE:RELIANCE"]
    assert reliance.dhan_security_id == "2885"
    assert reliance.exchange == "NSE"
    assert reliance.tradingsymbol == "RELIANCE"
    assert reliance.instrument_type == "EQUITY"
    assert reliance.dhan_segment == "NSE_EQ"
    assert reliance.name == "RELIANCE INDUSTRIES"
    assert reliance.lot_size == 1


def test_index_rows_get_the_index_segment() -> None:
    found = {i.symbol: i for i in parse_security_master(CSV_SAMPLE, refreshed_on=date(2026, 8, 3))}
    sensex = found["BSE:SENSEX"]
    assert sensex.instrument_type == "INDEX"
    assert sensex.dhan_segment == "IDX_I"


def test_wanted_filter_keeps_only_requested_symbols() -> None:
    found = parse_security_master(
        CSV_SAMPLE, refreshed_on=date(2026, 8, 3), wanted=["NSE:TCS"]
    )
    assert [i.symbol for i in found] == ["NSE:TCS"]


def test_rows_are_normalised_to_upper_case() -> None:
    csv = (
        "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
        "SEM_INSTRUMENT_NAME,SEM_LOT_UNITS,SM_SYMBOL_NAME\n"
        "2885,reliance,nse,E,equity,1,Reliance\n"
    )
    found = parse_security_master(csv, refreshed_on=date(2026, 8, 3))
    assert found[0].symbol == "NSE:RELIANCE"


def test_rows_with_blank_symbol_or_exchange_are_skipped() -> None:
    csv = (
        "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
        "SEM_INSTRUMENT_NAME,SEM_LOT_UNITS,SM_SYMBOL_NAME\n"
        "1,,NSE,E,EQUITY,1,No symbol\n"
        "2,GOOD,,E,EQUITY,1,No exchange\n"
        "3,VALID,NSE,E,EQUITY,1,Fine\n"
    )
    found = parse_security_master(csv, refreshed_on=date(2026, 8, 3))
    assert [i.symbol for i in found] == ["NSE:VALID"]


def test_unsupported_exchange_is_skipped_not_crashed() -> None:
    """F&O and other segments we do not support are simply dropped."""
    csv = (
        "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
        "SEM_INSTRUMENT_NAME,SEM_LOT_UNITS,SM_SYMBOL_NAME\n"
        "1,NIFTYFUT,NFO,D,FUTIDX,50,Nifty Future\n"
        "2,RELIANCE,NSE,E,EQUITY,1,Reliance\n"
    )
    found = parse_security_master(csv, refreshed_on=date(2026, 8, 3))
    assert [i.symbol for i in found] == ["NSE:RELIANCE"]


def test_unparseable_lot_size_becomes_none_not_a_crash() -> None:
    csv = (
        "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
        "SEM_INSTRUMENT_NAME,SEM_LOT_UNITS,SM_SYMBOL_NAME\n"
        "1,RELIANCE,NSE,E,EQUITY,notanumber,Reliance\n"
    )
    assert parse_security_master(csv, refreshed_on=date(2026, 8, 3))[0].lot_size is None


def test_missing_expected_column_fails_loudly() -> None:
    """A silent format change would produce wrong security IDs, which would
    fetch candles for the WRONG STOCK - the worst possible failure."""
    with pytest.raises(InstrumentError, match="expected column"):
        parse_security_master("wrong,header\n1,2\n", refreshed_on=date(2026, 8, 3))


def test_missing_column_names_every_absent_column() -> None:
    csv = "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL\n1,RELIANCE\n"
    with pytest.raises(InstrumentError) as exc:
        parse_security_master(csv, refreshed_on=date(2026, 8, 3))
    message = str(exc.value)
    assert "SEM_EXM_EXCH_ID" in message
    assert "SEM_INSTRUMENT_NAME" in message


# --- row shaping ------------------------------------------------------------


def test_instrument_to_row_matches_table_columns() -> None:
    inst = Instrument(
        symbol="NSE:RELIANCE", exchange="NSE", tradingsymbol="RELIANCE",
        dhan_security_id="2885", dhan_segment="NSE_EQ", name="RELIANCE INDUSTRIES",
        instrument_type="EQUITY", lot_size=1, refreshed_on=date(2026, 8, 3),
    )
    row = inst.to_row()
    assert row["symbol"] == "NSE:RELIANCE"
    assert row["dhan_security_id"] == "2885"
    assert row["refreshed_on"] == "2026-08-03"
    assert row["is_active"] is True
    assert set(row) == {
        "symbol", "exchange", "tradingsymbol", "dhan_security_id", "dhan_segment",
        "name", "instrument_type", "lot_size", "is_active", "refreshed_on",
    }
