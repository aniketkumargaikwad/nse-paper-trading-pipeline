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
    deduplicate_by_symbol,
    parse_security_master,
    split_symbol,
)

# The REAL 16-column header of Dhan's compact security master
# (api-scrip-master.csv), verified against the live file on 2026-08-08.
HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
    "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
    "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
)


def csv_rows(*rows: str) -> str:
    return HEADER + "\n" + "\n".join(rows) + "\n"


# Verified real values from the live master.
RELIANCE = "NSE,E,2885,EQUITY,0,RELIANCE,1.0,Reliance Industries,,,,10.0000,NA,ES,EQ,RELIANCE INDUSTRIES LTD"
TCS = "NSE,E,11536,EQUITY,0,TCS,1.0,Tata Consultancy,,,,5.0000,NA,ES,EQ,TATA CONSULTANCY LTD"
GOVT_BOND = "NSE,E,9999,EQUITY,0,757GS2033,1.0,Govt Stock,,,,1.0000,NA,ES,SG,7.57% GS 2033"
SME_SCRIP = "NSE,E,8888,EQUITY,0,SMALLCO,1.0,Small Co,,,,1.0000,NA,ES,SM,SMALL CO LTD"
BSE_STOCK = "BSE,E,500325,EQUITY,0,RELIANCE,1.0,Reliance,,,,5.0000,NA,ES,A,RELIANCE INDUSTRIES"
NSE_INDEX = "NSE,I,25,INDEX,0,BANKNIFTY,1.0,Nifty Bank,,,,0.0000,NA,IX,X,NIFTY BANK"
IDX_SPACED = "NSE,I,17,INDEX,0,NIFTY 100,1.0,Nifty 100,,,,0.0000,NA,IX,X,NIFTY 100"
CURRENCY_OPT = "NSE,C,2885,OPTCUR,0,EURINR-Aug2025-102.75-CE,1.0,EURINR CALL,2025-08-01,102.75,CE,0.25,W,CUR OP,,"
STOCK_OPTION = "NSE,D,106277,OPTSTK,0,RELIANCE-Sep2026-700-CE,500.0,RELIANCE CALL,2026-09-29,700,CE,5.0,M,OP,,"


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


def test_parses_the_real_nse_equity_row() -> None:
    found = {i.symbol: i for i in parse_security_master(
        csv_rows(RELIANCE), refreshed_on=date(2026, 8, 3))}
    r = found["NSE:RELIANCE"]
    assert r.dhan_security_id == "2885"
    assert r.dhan_segment == "NSE_EQ"
    assert r.instrument_type == "EQUITY"
    assert r.tradingsymbol == "RELIANCE"
    assert r.name == "RELIANCE INDUSTRIES LTD"
    assert r.lot_size == 1


def test_currency_option_sharing_the_same_security_id_is_excluded() -> None:
    """Security IDs are unique only WITHIN a segment. Id 2885 is RELIANCE in
    segment E and a EURINR option in segment C - fetching the wrong one would
    silently fill a symbol's cache with another instrument's candles."""
    found = parse_security_master(
        csv_rows(RELIANCE, CURRENCY_OPT), refreshed_on=date(2026, 8, 3))
    assert [i.symbol for i in found] == ["NSE:RELIANCE"]
    assert found[0].dhan_segment == "NSE_EQ"


def test_derivatives_are_excluded() -> None:
    found = parse_security_master(
        csv_rows(RELIANCE, STOCK_OPTION), refreshed_on=date(2026, 8, 3))
    assert [i.symbol for i in found] == ["NSE:RELIANCE"]


def test_government_securities_and_sme_are_excluded() -> None:
    """NSE segment E also carries govt securities (series SG) and SME scrips
    (SM). Only real equity series belong in the instruments table."""
    found = parse_security_master(
        csv_rows(RELIANCE, GOVT_BOND, SME_SCRIP), refreshed_on=date(2026, 8, 3))
    assert [i.symbol for i in found] == ["NSE:RELIANCE"]


def test_bse_equity_uses_its_own_series_codes() -> None:
    """BSE has NO 'EQ' series - it uses group codes. A shared EQ-only filter
    would silently exclude every BSE listing."""
    found = parse_security_master(
        csv_rows(BSE_STOCK), refreshed_on=date(2026, 8, 3))
    assert len(found) == 1
    assert found[0].symbol == "BSE:RELIANCE"
    assert found[0].dhan_segment == "BSE_EQ"


def test_index_rows_are_kept_with_the_index_segment() -> None:
    found = {i.symbol: i for i in parse_security_master(
        csv_rows(NSE_INDEX), refreshed_on=date(2026, 8, 3))}
    idx = found["NSE:BANKNIFTY"]
    assert idx.instrument_type == "INDEX"
    assert idx.dhan_segment == "IDX_I"
    assert idx.dhan_security_id == "25"


def test_index_names_with_spaces_are_normalised() -> None:
    """Real index names contain spaces ('NIFTY 100'); the symbol must be
    typeable and match SYMBOL_RE."""
    found = parse_security_master(csv_rows(IDX_SPACED), refreshed_on=date(2026, 8, 3))
    assert found[0].symbol == "NSE:NIFTY100"
    assert SYMBOL_RE.match(found[0].symbol)


def test_wanted_filter_still_works() -> None:
    found = parse_security_master(
        csv_rows(RELIANCE, TCS), refreshed_on=date(2026, 8, 3), wanted=["NSE:TCS"])
    assert [i.symbol for i in found] == ["NSE:TCS"]


def test_rows_are_normalised_to_upper_case() -> None:
    row = "nse,E,2885,EQUITY,0,reliance,1.0,Reliance,,,,10.0000,NA,ES,EQ,Reliance Industries"
    found = parse_security_master(csv_rows(row), refreshed_on=date(2026, 8, 3))
    assert found[0].symbol == "NSE:RELIANCE"


def test_rows_with_blank_symbol_or_exchange_are_skipped() -> None:
    no_symbol = "NSE,E,1,EQUITY,0,,1.0,No symbol,,,,10.0000,NA,ES,EQ,No symbol"
    no_exchange = ",E,2,EQUITY,0,GOOD,1.0,No exchange,,,,10.0000,NA,ES,EQ,No exchange"
    found = parse_security_master(
        csv_rows(no_symbol, no_exchange, RELIANCE), refreshed_on=date(2026, 8, 3))
    assert [i.symbol for i in found] == ["NSE:RELIANCE"]


def test_unparseable_lot_size_becomes_none_not_a_crash() -> None:
    row = "NSE,E,2885,EQUITY,0,RELIANCE,notanumber,Reliance,,,,10.0000,NA,ES,EQ,Reliance"
    found = parse_security_master(csv_rows(row), refreshed_on=date(2026, 8, 3))
    assert found[0].lot_size is None


def test_missing_expected_column_fails_loudly() -> None:
    """A silent format change would produce wrong security IDs, which would
    fetch candles for the WRONG INSTRUMENT - the worst possible failure."""
    with pytest.raises(InstrumentError, match="expected column"):
        parse_security_master("wrong,header\n1,2\n", refreshed_on=date(2026, 8, 3))


def test_missing_column_names_every_absent_column() -> None:
    with pytest.raises(InstrumentError) as exc:
        parse_security_master(
            "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL\n1,RELIANCE\n",
            refreshed_on=date(2026, 8, 3))
    message = str(exc.value)
    assert "SEM_EXM_EXCH_ID" in message
    assert "SEM_SERIES" in message


# --- symbol collisions -------------------------------------------------------


def test_equity_wins_when_an_index_shares_its_ticker() -> None:
    """Real case: BSE:METAL is both a Mirae ETF and a BSE index. An index
    must never shadow a tradable ticker."""
    rows = csv_rows(
        "BSE,E,544268,EQUITY,0,METAL,1.0,Mirae ETF,,,,5.0000,NA,ES,A,Mirae Asset Mutual Fund",
        "BSE,I,75,INDEX,0,METAL,1.0,Metal Index,,,,0.0000,NA,IX,X,METAL",
    )
    found = parse_security_master(rows, refreshed_on=date(2026, 8, 3))
    assert len(found) == 1
    assert found[0].symbol == "BSE:METAL"
    assert found[0].instrument_type == "EQUITY"
    assert found[0].dhan_security_id == "544268"


def test_lower_security_id_wins_between_same_type_rows() -> None:
    """Real case: BSE:CAPINS appears twice as an INDEX (ids 99 and 846).
    The tie-break must be deterministic across runs."""
    rows = csv_rows(
        "BSE,I,846,INDEX,0,CAPINS,1.0,Cap Ins,,,,0.0000,NA,IX,X,CAPINS",
        "BSE,I,99,INDEX,0,CAPINS,1.0,Cap Ins,,,,0.0000,NA,IX,X,CAPINS",
    )
    found = parse_security_master(rows, refreshed_on=date(2026, 8, 3))
    assert len(found) == 1
    assert found[0].dhan_security_id == "99"


def test_dedup_is_order_independent() -> None:
    """Same input in either order must give the same winner."""
    a = "BSE,E,544268,EQUITY,0,METAL,1.0,Mirae ETF,,,,5.0000,NA,ES,A,Mirae Asset Mutual Fund"
    b = "BSE,I,75,INDEX,0,METAL,1.0,Metal Index,,,,0.0000,NA,IX,X,METAL"
    first = parse_security_master(csv_rows(a, b), refreshed_on=date(2026, 8, 3))
    second = parse_security_master(csv_rows(b, a), refreshed_on=date(2026, 8, 3))
    assert first[0].dhan_security_id == second[0].dhan_security_id == "544268"


def test_no_duplicate_symbols_survive_parsing() -> None:
    rows = csv_rows(
        RELIANCE,
        "BSE,E,544268,EQUITY,0,METAL,1.0,Mirae ETF,,,,5.0000,NA,ES,A,Mirae Asset Mutual Fund",
        "BSE,I,75,INDEX,0,METAL,1.0,Metal Index,,,,0.0000,NA,IX,X,METAL",
    )
    found = parse_security_master(rows, refreshed_on=date(2026, 8, 3))
    symbols = [i.symbol for i in found]
    assert len(symbols) == len(set(symbols))


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
