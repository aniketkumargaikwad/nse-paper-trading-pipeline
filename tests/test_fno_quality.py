"""Checks on one day's normalised F&O rows (design §2.6)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from fno import nse_files, quality  # noqa: E402
from test_fno_nse_files import OLD  # noqa: E402

DAY = date(2016, 1, 4)


def checks(issues):
    return {(i["check"], i["underlying"]) for i in issues}


def test_a_clean_day_has_only_the_missing_banknifty_of_this_trimmed_sample():
    issues = quality.check_day(nse_files.parse(OLD, DAY), DAY)
    assert checks(issues) == {("missing_underlying", "BANKNIFTY")}


def test_a_traded_row_whose_low_is_above_its_close_is_flagged():
    frame = nse_files.parse(OLD, DAY)
    frame.loc[frame["instrument"] == "FUT", "low"] = 9000.0
    assert ("ohlc_invalid", "NIFTY") in checks(quality.check_day(frame, DAY))


def test_untraded_rows_are_not_held_to_ohlc_rules():
    """An untraded option's open/high/low are 0 by NSE's convention."""
    frame = nse_files.parse(OLD, DAY)
    assert ("ohlc_invalid", "NIFTY") not in checks(quality.check_day(frame, DAY))


def test_open_interest_without_a_settlement_price_is_flagged():
    frame = nse_files.parse(OLD, DAY)
    frame.loc[frame["strike"] == 7800, "settle"] = 0.0
    assert ("settle_missing", "NIFTY") in checks(quality.check_day(frame, DAY))


def test_the_same_contract_twice_is_flagged():
    frame = nse_files.parse(OLD, DAY)
    doubled = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    assert ("duplicate_contract", "NIFTY") in checks(quality.check_day(doubled, DAY))


def test_issues_are_appended_to_a_yearly_report(tmp_path):
    issues = quality.check_day(nse_files.parse(OLD, DAY), DAY)
    quality.write_issues(tmp_path, issues)
    quality.write_issues(tmp_path, issues)
    text = (tmp_path / "quality" / "2016.csv").read_text(encoding="utf-8")
    assert text.count("missing_underlying") == 2
    assert text.splitlines()[0] == "trade_date,underlying,check,count,example"
