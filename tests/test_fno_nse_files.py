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
