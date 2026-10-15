"""FII / DII / Pro / Client open interest from NSE's daily file."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import participants  # noqa: E402

DAY = date(2026, 9, 30)
# The real file for 30 Sep 2026, verbatim (title row, trailing spaces in
# some header names, CRLF line ends).
FILE = (
    '""Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Sep 30, 2026"",,,,,,,,,,,,,,\r\n'
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short       ,"
    "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
    "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts      ,Total Short Contracts\r\n"
    "Client,301040,58614,3453975,154613,3334467,1980918,2900690,2823611,1368687,543310,770156,834210,10982397,7541894\r\n"
    "DII,49642,17592,245443,4554998,8483,46958,2932,300,2112,43447,177732,20228,396085,4773782\r\n"
    "FII,29394,312264,3360170,2870621,466513,1032048,932096,363923,92210,198506,179833,71258,5178842,4729996\r\n"
    "Pro,35278,26884,826666,306022,1052809,789280,1026555,661370,594084,752690,929372,612257,4050807,3562460\r\n"
    "TOTAL,415354,415354,7886254,7886254,4862273,3849205,4862273,3849205,2057093,1537953,2057093,1537953,20608131,20608131\r\n"
)


def test_every_participant_is_a_row_with_snake_case_columns():
    frame = participants.parse(FILE, DAY)
    assert list(frame["client_type"]) == ["Client", "DII", "FII", "Pro", "TOTAL"]
    fii = frame[frame["client_type"] == "FII"].iloc[0]
    assert fii["future_index_long"] == 29394
    assert fii["future_index_short"] == 312264
    assert fii["total_short_contracts"] == 4729996
    assert fii["trade_date"] == DAY


def test_a_file_missing_a_column_is_refused():
    broken = FILE.replace("Future Index Short", "Something Else")
    with pytest.raises(participants.ParticipantFileError, match="future_index_short"):
        participants.parse(broken, DAY)


def test_a_day_is_stored_and_replaced(tmp_path):
    frame = participants.parse(FILE, DAY)
    participants.write_day(tmp_path, frame, DAY)
    participants.write_day(tmp_path, frame, DAY)
    back = participants.read(tmp_path, DAY, DAY)
    assert len(back) == 5


def test_a_file_without_the_title_line_is_read_too():
    """13 Jun 2018: NSE published the header as the first line, no title."""
    no_title = FILE.split("\r\n", 1)[1]
    frame = participants.parse(no_title, DAY)
    assert list(frame["client_type"]) == ["Client", "DII", "FII", "Pro", "TOTAL"]
    assert frame[frame["client_type"] == "FII"].iloc[0]["future_index_long"] == 29394


def test_indian_comma_grouping_is_read_as_a_whole_number():
    """12 Dec 2019: the FII row came as "10,02,064.00" while the others were plain."""
    fii = "FII,29394,312264,3360170,2870621,466513,1032048,932096,363923,92210,198506,179833,71258,5178842,4729996"
    grouped = ('FII,"29,394.00","3,12,264.00","33,60,170.00",2870621,466513,1032048,932096,363923,'
               "92210,198506,179833,71258,5178842,4729996")
    frame = participants.parse(FILE.replace(fii, grouped), DAY)
    row = frame[frame["client_type"] == "FII"].iloc[0]
    assert row["future_index_long"] == 29394
    assert row["future_index_short"] == 312264
    assert row["future_stock_long"] == 3360170


def test_a_fractional_contract_count_is_refused():
    fii = "FII,29394,"
    with pytest.raises(participants.ParticipantFileError, match="whole"):
        participants.parse(FILE.replace(fii, 'FII,"29,394.50",'), DAY)
