"""NSE's daily F&O files, both formats, normalised to one schema."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import nse_files  # noqa: E402


def test_days_before_the_switch_use_the_old_archive():
    assert nse_files.file_url(date(2016, 1, 4)) == (
        "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/2016/JAN/fo04JAN2016bhav.csv.zip"
    )
    assert "/2024/JUL/fo05JUL2024bhav" in nse_files.file_url(date(2024, 7, 5))


def test_days_from_8_july_2024_use_udiff():
    assert nse_files.file_url(date(2024, 7, 8)) == (
        "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip"
    )


# Real rows from fo04JAN2016bhav.csv, trimmed. NIFTYIT and the stock option
# are there to be filtered out; the trailing comma is NSE's.
OLD = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,
FUTIDX,NIFTY,28-Jan-2016,0,XX,7934.9,7947.75,7792.6,7809.8,7809.8,217442,1281367.25,20242200,804525,04-JAN-2016,
OPTIDX,NIFTY,28-Jan-2016,6350,CE,0,0,0,1533.9,1471.3,0,0,0,0,04-JAN-2016,
OPTIDX,NIFTY,28-Jan-2016,7800,PE,95.5,120,90.1,112.35,112.35,52000,302000.5,3100000,200000,04-JAN-2016,
FUTIDX,NIFTYIT,28-Jan-2016,0,XX,11300,11320,11200,11250,11250,10,28.1,500,0,04-JAN-2016,
OPTSTK,RELIANCE,28-Jan-2016,1000,CE,10,11,9,10.5,10.5,100,500,1000,0,04-JAN-2016,
"""


def test_old_format_keeps_index_futures_and_options_of_our_underlyings():
    frame = nse_files.parse(OLD, date(2016, 1, 4))
    assert list(frame.columns) == list(nse_files.COLUMNS)
    assert len(frame) == 3
    assert set(frame["underlying"]) == {"NIFTY"}


def test_old_format_future_row_is_normalised():
    frame = nse_files.parse(OLD, date(2016, 1, 4))
    fut = frame[frame["instrument"] == "FUT"].iloc[0]
    assert fut["trade_date"] == date(2016, 1, 4)
    assert fut["expiry"] == date(2016, 1, 28)
    assert fut["strike"] != fut["strike"]                 # NaN: a future has no strike
    assert fut["option_type"] != fut["option_type"]       # NaN, not "XX"
    assert fut["close"] == 7809.8
    assert fut["volume_contracts"] == 217442
    assert fut["oi_units"] == 20242200
    assert fut["turnover_inr"] == pytest.approx(1281367.25 * 100_000)
    assert fut["lot_size"] != fut["lot_size"]             # the old format has none


def test_old_format_untraded_option_keeps_its_published_close_and_zero_volume():
    frame = nse_files.parse(OLD, date(2016, 1, 4))
    idle = frame[(frame["instrument"] == "OPT") & (frame["strike"] == 6350)].iloc[0]
    assert idle["option_type"] == "CE"
    assert idle["volume_contracts"] == 0
    assert idle["close"] == 1533.9
    assert idle["settle"] == 1471.3


def test_a_file_for_another_day_is_refused():
    with pytest.raises(nse_files.NseFileError, match="2016-01-05"):
        nse_files.parse(OLD, date(2016, 1, 5))


UDIFF_HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
                "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
                "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
                "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")
# Real rows from BhavCopy_NSE_FO_0_0_0_20260930_F_0000.csv, plus a stock
# option (STO) that must be filtered out.
UDIFF = UDIFF_HEADER + """
2026-09-30,2026-09-30,FO,NSE,IDF,58875,,NIFTY,,2026-12-29,2026-12-29,,,NIFTY26DECFUT,23054.00,23185.00,22955.00,22973.40,22955.00,23054.00,22620.45,22973.40,89050,89050,1933,2898870338.00,1709,F1,65,,,,,
2026-09-30,2026-09-30,FO,NSE,IDO,53339,,NIFTY,,2026-11-03,2026-11-03,20550.00,PE,NIFTY26N0320550PE,0.00,0.00,0.00,2.35,0.00,2.35,22620.45,6.67,0,0,0,0.00,0,F1,65,,,,,
2026-09-30,2026-09-30,FO,NSE,IDO,67782,,NIFTY,,2028-12-26,2028-12-26,24000.00,CE,NIFTY28DEC24000CE,3064.95,3065.00,3064.95,3065.00,3065.00,3015.00,22620.45,3078.16,2665,-65,2,3518446.75,2,F1,65,,,,,
2026-09-30,2026-09-30,FO,NSE,STO,99999,,RELIANCE,,2026-10-27,2026-10-27,1400.00,CE,RELIANCE26OCT1400CE,10.00,11.00,9.00,10.50,10.50,10.00,1395.00,10.50,1000,0,5,100.00,5,F1,500,,,,,
"""


def test_udiff_keeps_index_futures_and_options_of_our_underlyings():
    frame = nse_files.parse(UDIFF, date(2026, 9, 30))
    assert list(frame.columns) == list(nse_files.COLUMNS)
    assert len(frame) == 3
    assert set(frame["instrument"]) == {"FUT", "OPT"}


def test_udiff_carries_lot_size_and_the_index_level():
    frame = nse_files.parse(UDIFF, date(2026, 9, 30))
    fut = frame[frame["instrument"] == "FUT"].iloc[0]
    assert fut["lot_size"] == 65
    assert fut["underlying_price"] == 22620.45
    assert fut["expiry"] == date(2026, 12, 29)
    assert fut["volume_contracts"] == 1933
    assert fut["oi_units"] == 89050
    assert fut["strike"] != fut["strike"]


def test_udiff_option_row_is_normalised():
    frame = nse_files.parse(UDIFF, date(2026, 9, 30))
    idle = frame[(frame["instrument"] == "OPT") & (frame["strike"] == 20550)].iloc[0]
    assert idle["option_type"] == "PE"
    assert idle["volume_contracts"] == 0
    assert idle["close"] == 2.35
    assert idle["settle"] == 6.67
    assert idle["expiry"] == date(2026, 11, 3)
